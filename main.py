import hashlib
import hmac
import os
import secrets
import sqlite3
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from getpass import getpass
from typing import List
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from scraper import sync_from_stolnytenis

APP_DIR = Path(__file__).resolve().parent
DB_FILE = os.getenv("DB_FILE", str(APP_DIR / "keraming.db"))
SESSION_COOKIE = "keraming_session"
SESSION_DURATION = timedelta(days=14)
COOKIE_SECURE_OVERRIDE = os.getenv("COOKIE_SECURE")
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
PASSWORD_SCRYPT_N = 1 << 14
PASSWORD_MIN_LENGTH = 8
TEMP_PASSWORD_LENGTH = 8
TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"

scheduler = BackgroundScheduler()
LOCAL_TIMEZONE = ZoneInfo("Europe/Bratislava")


def auto_sync_task():
    print("[AUTOSYNC] Spúšťam synchronizáciu so stolnytenis.info...")
    try:
        players, matches = sync_from_stolnytenis(DB_FILE)
        print(f"[AUTOSYNC] Hotovo: {matches} zápasov, {players} hráčov.")
    except Exception as e:
        print(f"[AUTOSYNC CHYBA]: {e}")


def completed_signup_saturday(now=None):
    now = now or datetime.now(LOCAL_TIMEZONE)
    if now.weekday() == 5:
        return None
    saturday = now.date() - timedelta(days=(now.weekday() - 5) % 7)
    return saturday.isoformat()


def reset_weekly_signups(now=None):
    saturday = completed_signup_saturday(now)
    if saturday is None:
        return False
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signup_reset_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_reset_date TEXT
        )
    """)
    state = conn.execute(
        "SELECT last_reset_date FROM signup_reset_state WHERE id = 1"
    ).fetchone()
    if state and state["last_reset_date"] == saturday:
        conn.close()
        return False

    clear_signup_choices(conn)
    conn.execute("""
        INSERT INTO signup_reset_state (id, last_reset_date)
        VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET last_reset_date = excluded.last_reset_date
    """, (saturday,))
    conn.commit()
    conn.close()
    print(f"[SIGNUP RESET] Prihlásenia resetované po sobote {saturday}.")
    return True


def clear_signup_choices(conn):
    conn.execute("""
        UPDATE players
        SET can_morning = 0,
            can_afternoon = 0,
            cannot_play = 0,
            selected_leagues = '',
            takes_car = 0,
            note = ''
    """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    reset_weekly_signups()
    scheduler.add_job(
        reset_weekly_signups,
        CronTrigger(day_of_week="sun", hour=0, minute=0, timezone=LOCAL_TIMEZONE),
        id="weekly_signup_reset",
        replace_existing=True
    )
    # Spustíme synchronizáciu hneď pri štarte, aby boli dáta okamžite čerstvé a opravené
    auto_sync_task()
    # Pravidelná synchronizácia každých 6 hodín
    scheduler.add_job(auto_sync_task, "interval", hours=6, id="interval_sync", replace_existing=True)
    scheduler.start()
    print("[LIFESPAN] Plánovač spustený (interval: 6 hodín).")
    yield
    scheduler.shutdown()
    print("[LIFESPAN] Plánovač vypnutý.")


app = FastAPI(title="Keraming Trenčín", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            primary_team TEXT NOT NULL,
            roster TEXT NOT NULL,
            won INTEGER DEFAULT 0,
            lost INTEGER DEFAULT 0,
            doubles TEXT DEFAULT '0:0',
            can_morning INTEGER DEFAULT 0,
            can_afternoon INTEGER DEFAULT 0,
            cannot_play INTEGER DEFAULT 0,
            selected_leagues TEXT DEFAULT '',
            takes_car INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            stolnytenis_player_id TEXT
        )
    """)
    player_columns = {row[1] for row in c.execute("PRAGMA table_info(players)")}
    if "stolnytenis_player_id" not in player_columns:
        c.execute("ALTER TABLE players ADD COLUMN stolnytenis_player_id TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            team_id TEXT NOT NULL,
            round TEXT,
            match_date TEXT,
            slot TEXT,
            home_team TEXT,
            away_team TEXT,
            is_home INTEGER,
            status TEXT,
            score TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS league_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id TEXT NOT NULL,
            club_name TEXT NOT NULL,
            player_name TEXT NOT NULL,
            encounters INTEGER DEFAULT 0,
            won INTEGER DEFAULT 0,
            lost INTEGER DEFAULT 0,
            UNIQUE(league_id, club_name, player_name)
        )
    """)

    try:
        c.execute("ALTER TABLE league_players ADD COLUMN encounters INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'player')),
            player_id INTEGER UNIQUE REFERENCES players(id),
            must_change_password INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS login_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            csrf_token TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username_hash TEXT NOT NULL,
            remote_address TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        )
    """)
    login_attempt_columns = {row[1] for row in c.execute("PRAGMA table_info(login_attempts)")}
    if "remote_address" not in login_attempt_columns:
        c.execute(
            "ALTER TABLE login_attempts ADD COLUMN remote_address TEXT NOT NULL DEFAULT ''"
        )

    c.execute("""
        CREATE TABLE IF NOT EXISTS signup_reset_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_reset_date TEXT
        )
    """)

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=8,
        p=1,
        dklen=64,
        maxmem=64 * 1024 * 1024
    )
    return f"scrypt${PASSWORD_SCRYPT_N}$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored_hash.split("$")
        if (
            scheme != "scrypt"
            or int(n) != PASSWORD_SCRYPT_N
            or int(r) != 8
            or int(p) != 1
            or len(bytes.fromhex(digest_hex)) != 64
        ):
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=PASSWORD_SCRYPT_N,
            r=8,
            p=1,
            dklen=64,
            maxmem=64 * 1024 * 1024
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def normalize_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Prihlasovacie meno musí mať 3–32 znakov: písmená bez diakritiky, čísla, bodku, pomlčku alebo podčiarkovník."
        )
    return username


def create_session(conn, user_id: int):
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + SESSION_DURATION).isoformat()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO login_sessions (token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash, user_id, csrf_token, expires_at)
    )
    return token, csrf_token


def generate_temporary_password() -> str:
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))


def session_cookie_is_secure(request: Request) -> bool:
    if COOKIE_SECURE_OVERRIDE is not None:
        return COOKIE_SECURE_OVERRIDE.lower() != "false"
    if request.url.scheme == "https":
        return True
    return urlsplit(str(request.url)).hostname not in {"localhost", "127.0.0.1", "::1"}


def set_session_cookie(request: Request, response: Response, token: str):
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_DURATION.total_seconds()),
        httponly=True,
        secure=session_cookie_is_secure(request),
        samesite="lax",
        path="/"
    )


def get_optional_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_db()
    user = conn.execute("""
        SELECT u.*, s.csrf_token
        FROM login_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1
    """, (token_hash, datetime.now(timezone.utc).isoformat())).fetchone()
    conn.close()
    return user


def require_user(request: Request, *, allow_password_change=False, check_csrf=False):
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Prihláste sa.")
    if user["must_change_password"] and not allow_password_change:
        raise HTTPException(status_code=403, detail="Pred pokračovaním si zmeňte dočasné heslo.")
    if check_csrf and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""),
        user["csrf_token"]
    ):
        raise HTTPException(status_code=403, detail="Neplatný bezpečnostný token.")
    return user


def require_admin(request: Request):
    user = require_user(request, check_csrf=request.method not in {"GET", "HEAD", "OPTIONS"})
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Táto operácia je dostupná iba administrátorovi.")
    return user


def create_admin_account(username: str, password: str):
    try:
        username = normalize_username(username)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Heslo musí mať aspoň {PASSWORD_MIN_LENGTH} znakov.")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, hash_password(password))
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("Toto prihlasovacie meno už existuje.") from exc
    finally:
        conn.close()


def reset_admin_password(username: str, password: str):
    try:
        username = normalize_username(username)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Heslo musí mať aspoň {PASSWORD_MIN_LENGTH} znakov.")

    conn = get_db()
    try:
        account = conn.execute(
            "SELECT id FROM users WHERE username = ? AND role = 'admin'",
            (username,)
        ).fetchone()
        if not account:
            raise ValueError("Administrátorský účet sa nenašiel.")
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(password), account["id"])
        )
        conn.execute("DELETE FROM login_sessions WHERE user_id = ?", (account["id"],))
        conn.commit()
    finally:
        conn.close()


DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


def get_asset_version():
    static_dir = Path(__file__).resolve().parent / "static"
    digest = hashlib.sha256()
    for asset_path in (static_dir / "css/style.css", static_dir / "js/app.js"):
        digest.update(asset_path.read_bytes())
    return digest.hexdigest()[:12]


class LoginPayload(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangePayload(BaseModel):
    currentPassword: str = Field(default="", max_length=128)
    newPassword: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)


class AccountPayload(BaseModel):
    playerId: int = Field(gt=0)
    username: str = Field(min_length=3, max_length=32)


class UsernamePayload(BaseModel):
    username: str = Field(min_length=3, max_length=32)


def public_user_data(user, csrf_token: str):
    return {
        "userId": user["id"],
        "playerId": user["player_id"],
        "username": user["username"],
        "role": user["role"],
        "mustChangePassword": bool(user["must_change_password"]),
        "csrfToken": csrf_token
    }


@app.post("/api/auth/login")
def login(data: LoginPayload, request: Request, response: Response):
    username = data.username.strip().lower()
    username_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=15)).isoformat()
    conn = get_db()
    conn.execute("DELETE FROM login_attempts WHERE attempted_at <= ?", (cutoff,))
    attempts = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username_hash = ? AND attempted_at > ?",
        (username_hash, cutoff)
    ).fetchone()[0]
    if attempts >= 10:
        conn.close()
        raise HTTPException(status_code=429, detail="Priveľa neúspešných pokusov. Skúste to o 15 minút.")

    user = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1",
        (username,)
    ).fetchone()
    valid_password = verify_password(
        data.password,
        user["password_hash"] if user else DUMMY_PASSWORD_HASH
    )
    if not user or not valid_password:
        conn.execute(
            "INSERT INTO login_attempts (username_hash, remote_address, attempted_at) VALUES (?, ?, ?)",
            (username_hash, request.client.host if request.client else "", now.isoformat())
        )
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Nesprávne prihlasovacie meno alebo heslo.")

    conn.execute("DELETE FROM login_attempts WHERE username_hash = ?", (username_hash,))
    conn.execute("DELETE FROM login_sessions WHERE expires_at <= ?", (now.isoformat(),))
    token, csrf_token = create_session(conn, user["id"])
    conn.commit()
    conn.close()
    set_session_cookie(request, response, token)
    return public_user_data(user, csrf_token)


@app.get("/api/auth/me")
def get_current_session(request: Request):
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Nie ste prihlásený.")
    return public_user_data(user, user["csrf_token"])


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    user = require_user(request, allow_password_change=True, check_csrf=True)
    token = request.cookies.get(SESSION_COOKIE, "")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_db()
    conn.execute("DELETE FROM login_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=session_cookie_is_secure(request),
        httponly=True,
        samesite="lax"
    )
    return {"ok": True}


@app.post("/api/auth/change-password")
def change_password(data: PasswordChangePayload, request: Request, response: Response):
    user = require_user(request, allow_password_change=True, check_csrf=True)
    conn = get_db()
    stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not user["must_change_password"] and not verify_password(data.currentPassword, stored["password_hash"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Súčasné heslo nie je správne.")

    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (hash_password(data.newPassword), user["id"])
    )
    conn.execute("DELETE FROM login_sessions WHERE user_id = ?", (user["id"],))
    token, csrf_token = create_session(conn, user["id"])
    conn.commit()
    conn.close()
    set_session_cookie(request, response, token)
    user = dict(user)
    user["must_change_password"] = 0
    return public_user_data(user, csrf_token)


@app.get("/", response_class=HTMLResponse)
@app.get("/zapasy", response_class=HTMLResponse)
@app.get("/prihlasovanie", response_class=HTMLResponse)
@app.get("/rebricek", response_class=HTMLResponse)
@app.get("/profil", response_class=HTMLResponse)
@app.get("/sprava-uctov", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_version": get_asset_version()}
    )


@app.post("/api/sync")
def run_sync(request: Request):
    require_admin(request)
    count_players, count_matches = sync_from_stolnytenis(DB_FILE)
    return {
        "ok": True,
        "synced_players": count_players,
        "synced_matches": count_matches
    }


@app.get("/api/players")
def get_players(request: Request):
    reset_weekly_signups()
    user = get_optional_user(request)
    can_view_signup = user is not None and not user["must_change_password"]
    conn = get_db()
    rows = conn.execute("SELECT * FROM players ORDER BY id").fetchall()

    # 1. Počet odohraných zápasov družstva podľa výsledkov v rozpise
    played_rows = conn.execute("""
        SELECT team_id, COUNT(*) as played_count
        FROM matches
        WHERE status = 'played'
        GROUP BY team_id
    """).fetchall()
    team_played_map = {r["team_id"]: r["played_count"] for r in played_rows}

    # 2. Maximálny počet stretnutí hráča v danom družstve (záložná kontrola)
    max_enc_rows = conn.execute("""
        SELECT league_id, MAX(encounters) as max_enc
        FROM league_players
        WHERE LOWER(club_name) LIKE '%keraming%'
        GROUP BY league_id
    """).fetchall()
    max_enc_map = {r["league_id"]: (r["max_enc"] or 0) for r in max_enc_rows}

    # 3. Štatistiky našich hráčov po jednotlivých súťažiach
    lp_rows = conn.execute("""
        SELECT league_id, player_name, encounters, won, lost
        FROM league_players
        WHERE LOWER(club_name) LIKE '%keraming%'
        ORDER BY league_id ASC
    """).fetchall()
    conn.close()

    stats_by_player = {}
    for lp in lp_rows:
        pname = lp["player_name"]
        lid = lp["league_id"]
        enc = lp["encounters"] or 0
        won = lp["won"] or 0
        lost = lp["lost"] or 0
        total_singles = won + lost

        # Celkový počet stretnutí družstva v sezóne doteraz
        denom = max(team_played_map.get(lid, 0), max_enc_map.get(lid, 0), enc, 1)
        attendance_ratio = enc / denom
        attendance_pct = round(attendance_ratio * 100)
        win_rate = round((won / total_singles) * 100, 1) if total_singles > 0 else 0.0
        qualifies_40 = (attendance_ratio >= 0.40) and (total_singles > 0)

        if pname not in stats_by_player:
            stats_by_player[pname] = {}

        stats_by_player[pname][lid] = {
            "leagueId": lid,
            "encounters": enc,
            "teamPlayed": denom,
            "attendancePct": attendance_pct,
            "qualifies": qualifies_40,
            "won": won,
            "lost": lost,
            "totalMatches": total_singles,
            "winRate": win_rate
        }

    result = []
    for r in rows:
        pname = r["name"]
        rosters = [x.strip() for x in r["roster"].split(",") if x.strip()]
        p_stats_dict = stats_by_player.get(pname, {})

        # Zabezpečíme, aby mal hráč záznam pre každú súťaž zo svojej súpisky
        league_stats_list = []
        for lid in sorted(set(rosters + list(p_stats_dict.keys()))):
            if lid in p_stats_dict:
                league_stats_list.append(p_stats_dict[lid])
            else:
                denom = max(team_played_map.get(lid, 0), max_enc_map.get(lid, 0), 1)
                league_stats_list.append({
                    "leagueId": lid,
                    "encounters": 0,
                    "teamPlayed": denom,
                    "attendancePct": 0,
                    "qualifies": False,
                    "won": 0,
                    "lost": 0,
                    "totalMatches": 0,
                    "winRate": 0.0
                })

        player_data = {
            "id": r["id"],
            "name": pname,
            "teamId": r["primary_team"],
            "roster": rosters,
            "won": r["won"],
            "lost": r["lost"],
            "doubles": r["doubles"],
            "leagueStats": league_stats_list,
            "stolnytenisUrl": (
                f"https://www.stolnytenis.info/hrac/{r['stolnytenis_player_id']}"
                if r["stolnytenis_player_id"] and str(r["stolnytenis_player_id"]).isdigit()
                else None
            )
        }
        if can_view_signup:
            player_data.update({
                "canMorning": bool(r["can_morning"]),
                "canAfternoon": bool(r["can_afternoon"]),
                "cannotPlay": bool(r["cannot_play"]),
                "selectedLeagues": [x.strip() for x in r["selected_leagues"].split(",") if x.strip()],
                "takesCar": bool(r["takes_car"]),
                "note": r["note"]
            })
        result.append(player_data)

    return result


@app.get("/api/admin/accounts")
def get_accounts(request: Request):
    require_admin(request)
    conn = get_db()
    rows = conn.execute("""
        SELECT p.id, p.name, p.roster, u.username
        FROM players p
        LEFT JOIN users u ON u.player_id = p.id AND u.role = 'player'
        ORDER BY p.name COLLATE NOCASE
    """).fetchall()
    conn.close()
    return [
        {
            "playerId": row["id"],
            "name": row["name"],
            "roster": [team.strip() for team in row["roster"].split(",") if team.strip()],
            "username": row["username"]
        }
        for row in rows
    ]


@app.post("/api/admin/signup/reset")
def reset_signups_now(request: Request):
    require_admin(request)
    conn = get_db()
    clear_signup_choices(conn)
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/admin/accounts")
def create_player_account(data: AccountPayload, request: Request):
    require_admin(request)
    username = normalize_username(data.username)
    temporary_password = generate_temporary_password()
    conn = get_db()
    player = conn.execute("SELECT id FROM players WHERE id = ?", (data.playerId,)).fetchone()
    if not player:
        conn.close()
        raise HTTPException(status_code=404, detail="Hráč sa nenašiel.")
    try:
        conn.execute("""
            INSERT INTO users (username, password_hash, role, player_id, must_change_password)
            VALUES (?, ?, 'player', ?, 1)
        """, (username, hash_password(temporary_password), data.playerId))
        username_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
        conn.execute("DELETE FROM login_attempts WHERE username_hash = ?", (username_hash,))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise HTTPException(status_code=409, detail="Hráč už má účet alebo prihlasovacie meno už existuje.") from exc
    conn.close()
    return {"username": username, "temporaryPassword": temporary_password}


@app.put("/api/admin/accounts/{player_id}")
def update_player_username(player_id: int, data: UsernamePayload, request: Request):
    require_admin(request)
    username = normalize_username(data.username)
    conn = get_db()
    account = conn.execute(
        "SELECT id FROM users WHERE player_id = ? AND role = 'player'",
        (player_id,)
    ).fetchone()
    if not account:
        conn.close()
        raise HTTPException(status_code=404, detail="Hráč ešte nemá prihlasovací účet.")
    try:
        conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, account["id"]))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise HTTPException(status_code=409, detail="Toto prihlasovacie meno už existuje.") from exc
    conn.close()
    return {"ok": True, "username": username}


@app.post("/api/admin/accounts/{player_id}/reset-password")
def reset_player_password(player_id: int, request: Request):
    require_admin(request)
    temporary_password = generate_temporary_password()
    conn = get_db()
    account = conn.execute(
        "SELECT id, username FROM users WHERE player_id = ? AND role = 'player'",
        (player_id,)
    ).fetchone()
    if not account:
        conn.close()
        raise HTTPException(status_code=404, detail="Hráč ešte nemá prihlasovací účet.")
    conn.execute("""
        UPDATE users SET password_hash = ?, must_change_password = 1
        WHERE id = ?
    """, (hash_password(temporary_password), account["id"]))
    conn.execute("DELETE FROM login_sessions WHERE user_id = ?", (account["id"],))
    username_hash = hashlib.sha256(account["username"].encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM login_attempts WHERE username_hash = ?", (username_hash,))
    conn.commit()
    conn.close()
    return {"username": account["username"], "temporaryPassword": temporary_password}


@app.get("/api/matches")
def get_matches():
    conn = get_db()
    rows = conn.execute("SELECT * FROM matches ORDER BY id").fetchall()
    conn.close()

    return [
        {
            "id": r["id"],
            "externalId": r["external_id"],
            "teamId": r["team_id"],
            "round": r["round"],
            "date": r["match_date"],
            "slot": r["slot"],
            "home": r["home_team"],
            "away": r["away_team"],
            "isHome": bool(r["is_home"]),
            "status": r["status"],
            "score": r["score"],
            "opponents": []
        }
        for r in rows
    ]


@app.get("/api/matches/{match_id}/opponents")
def get_match_opponents(match_id: int):
    conn = get_db()
    match = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match:
        conn.close()
        raise HTTPException(status_code=404, detail="Zápas sa nenašiel")

    opp_team_name = (match["away_team"] if match["is_home"] else match["home_team"]).strip()
    base_opp_name = re.sub(r"\s+[B-G]$", "", opp_team_name, flags=re.IGNORECASE).strip()

    rows = conn.execute("""
        SELECT player_name, encounters, won, lost, club_name
        FROM league_players
        WHERE league_id = ? AND (
            LOWER(club_name) = LOWER(?) OR
            INSTR(LOWER(?), LOWER(club_name)) > 0 OR
            INSTR(LOWER(club_name), LOWER(?)) > 0 OR
            INSTR(LOWER(club_name), LOWER(?)) > 0
        )
        ORDER BY won DESC, lost ASC
    """, (match["team_id"], opp_team_name, opp_team_name, opp_team_name, base_opp_name)).fetchall()

    conn.close()

    return [
        {
            "name": r["player_name"],
            "encounters": r["encounters"],
            "won": r["won"],
            "lost": r["lost"],
            "club": r["club_name"]
        }
        for r in rows
    ]


class SignupPayload(BaseModel):
    canMorning: bool
    canAfternoon: bool
    cannotPlay: bool
    selectedLeagues: List[str]
    takesCar: bool
    note: str = Field(default="", max_length=500)


@app.post("/api/signup")
def update_signup(data: SignupPayload, request: Request):
    user = require_user(request, check_csrf=True)
    if user["role"] != "player" or user["player_id"] is None:
        raise HTTPException(status_code=403, detail="Prihlasovať sa môže iba hráčsky účet.")

    reset_weekly_signups()
    conn = get_db()
    player = conn.execute("SELECT * FROM players WHERE id = ?", (user["player_id"],)).fetchone()
    if not player:
        conn.close()
        raise HTTPException(status_code=404, detail="Hráč neexistuje")

    allowed_roster = [x.strip() for x in player["roster"].split(",") if x.strip()]
    saturday_team_ids = {"A", "B", "C", "D", "H"}
    valid_leagues = [
        lg for lg in data.selectedLeagues
        if lg in allowed_roster and lg in saturday_team_ids
    ]

    conn.execute("""
        UPDATE players
        SET can_morning = ?, can_afternoon = ?, cannot_play = ?,
            selected_leagues = ?, takes_car = ?, note = ?
        WHERE id = ?
    """, (
        0 if data.cannotPlay else int(data.canMorning),
        0 if data.cannotPlay else int(data.canAfternoon),
        int(data.cannotPlay),
        "" if data.cannotPlay else ",".join(valid_leagues),
        0 if data.cannotPlay else int(data.takesCar),
        data.note.strip(),
        user["player_id"]
    ))
    conn.commit()
    conn.close()
    return {"ok": True}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"create-admin", "reset-admin"}:
        raise SystemExit(
            "Použitie: python main.py create-admin|reset-admin <používateľské_meno>"
        )

    init_db()
    first_password = getpass(f"Heslo administrátora (min. {PASSWORD_MIN_LENGTH} znakov): ")
    if first_password != getpass("Zopakujte heslo: "):
        raise SystemExit("Heslá sa nezhodujú.")
    try:
        if sys.argv[1] == "create-admin":
            create_admin_account(sys.argv[2], first_password)
            action = "vytvorený"
        else:
            reset_admin_password(sys.argv[2], first_password)
            action = "resetovaný"
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Administrátorský účet {sys.argv[2].strip().lower()} bol {action}.")