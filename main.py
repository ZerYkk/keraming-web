import hashlib
import sqlite3
import re
from pathlib import Path
from typing import List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

from scraper import sync_from_stolnytenis

DB_FILE = "keraming.db"

scheduler = BackgroundScheduler()


def auto_sync_task():
    print("[AUTOSYNC] Spúšťam synchronizáciu so stolnytenis.info...")
    try:
        players, matches = sync_from_stolnytenis(DB_FILE)
        print(f"[AUTOSYNC] Hotovo: {matches} zápasov, {players} hráčov.")
    except Exception as e:
        print(f"[AUTOSYNC CHYBA]: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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

    conn.commit()
    conn.close()


def get_asset_version():
    static_dir = Path(__file__).resolve().parent / "static"
    digest = hashlib.sha256()
    for asset_path in (static_dir / "css/style.css", static_dir / "js/app.js"):
        digest.update(asset_path.read_bytes())
    return digest.hexdigest()[:12]


@app.get("/", response_class=HTMLResponse)
@app.get("/zapasy", response_class=HTMLResponse)
@app.get("/prihlasovanie", response_class=HTMLResponse)
@app.get("/rebricek", response_class=HTMLResponse)
@app.get("/profil", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_version": get_asset_version()}
    )


@app.post("/api/sync")
def run_sync():
    count_players, count_matches = sync_from_stolnytenis(DB_FILE)
    return {
        "ok": True,
        "synced_players": count_players,
        "synced_matches": count_matches
    }


@app.get("/api/players")
def get_players():
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

        result.append({
            "id": r["id"],
            "name": pname,
            "teamId": r["primary_team"],
            "roster": rosters,
            "won": r["won"],
            "lost": r["lost"],
            "doubles": r["doubles"],
            "leagueStats": league_stats_list,
            "canMorning": bool(r["can_morning"]),
            "canAfternoon": bool(r["can_afternoon"]),
            "cannotPlay": bool(r["cannot_play"]),
            "selectedLeagues": [x.strip() for x in r["selected_leagues"].split(",") if x.strip()],
            "takesCar": bool(r["takes_car"]),
            "stolnytenisUrl": (
                f"https://www.stolnytenis.info/hrac/{r['stolnytenis_player_id']}"
                if r["stolnytenis_player_id"] and str(r["stolnytenis_player_id"]).isdigit()
                else None
            ),
            "note": r["note"]
        })

    return result


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
    playerId: int
    canMorning: bool
    canAfternoon: bool
    cannotPlay: bool
    selectedLeagues: List[str]
    takesCar: bool
    note: str = ""


@app.post("/api/signup")
def update_signup(data: SignupPayload):
    conn = get_db()
    player = conn.execute("SELECT * FROM players WHERE id = ?", (data.playerId,)).fetchone()
    if not player:
        conn.close()
        raise HTTPException(status_code=404, detail="Hráč neexistuje")

    allowed_roster = [x.strip() for x in player["roster"].split(",") if x.strip()]
    valid_leagues = [lg for lg in data.selectedLeagues if lg in allowed_roster]

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
        data.playerId
    ))
    conn.commit()
    conn.close()
    return {"ok": True}