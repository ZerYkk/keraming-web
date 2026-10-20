import sqlite3
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

# Plánovač na pozadí
scheduler = BackgroundScheduler()

def auto_sync_task():
    print("[AUTOSYNC] Spúšťam pravidelnú synchronizáciu so stolnytenis.info...")
    try:
        players, matches = sync_from_stolnytenis(DB_FILE)
        print(f"[AUTOSYNC] Úspešne synchronizované: {matches} zápasov, {players} hráčov.")
    except Exception as e:
        print(f"[AUTOSYNC CHYBA]: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Inicializácia databázy
    init_db()
    
    # 2. Prvá synchronizácia hneď po štarte (ak je databáza prázdna alebo nová)
    # Spustíme ju v plánovači, aby neblokovala rýchly štart servera
    scheduler.add_job(auto_sync_task, id="startup_sync")
    
    # 3. Pravidelná synchronizácia každých 6 hodín
    scheduler.add_job(auto_sync_task, 'interval', hours=6, id="interval_sync")
    scheduler.start()
    print("[LIFESPAN] Plánovač spustený: synchronizácia každých 6 hodín.")
    
    yield
    
    # Vypnutie plánovača pri zastavení aplikácie
    scheduler.shutdown()
    print("[LIFESPAN] Plánovač bezpečne vypnutý.")

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
            note TEXT DEFAULT ''
        )
    """)

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
            won INTEGER DEFAULT 0,
            lost INTEGER DEFAULT 0,
            UNIQUE(league_id, club_name, player_name)
        )
    """)

    conn.commit()
    conn.close()


# Routy pre frontend
@app.get("/", response_class=HTMLResponse)
@app.get("/zapasy", response_class=HTMLResponse)
@app.get("/prihlasovanie", response_class=HTMLResponse)
@app.get("/rebricek", response_class=HTMLResponse)
@app.get("/profil", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


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
    conn.close()

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "teamId": r["primary_team"],
            "roster": [x.strip() for x in r["roster"].split(",") if x.strip()],
            "won": r["won"],
            "lost": r["lost"],
            "doubles": r["doubles"],
            "canMorning": bool(r["can_morning"]),
            "canAfternoon": bool(r["can_afternoon"]),
            "cannotPlay": bool(r["cannot_play"]),
            "selectedLeagues": [x.strip() for x in r["selected_leagues"].split(",") if x.strip()],
            "takesCar": bool(r["takes_car"]),
            "note": r["note"]
        }
        for r in rows
    ]


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

    opp_team_name = match["away_team"] if match["is_home"] else match["home_team"]

    rows = conn.execute("""
        SELECT player_name, won, lost, club_name
        FROM league_players
        WHERE league_id = ? AND (
            LOWER(club_name) = LOWER(?) OR
            INSTR(LOWER(?), LOWER(club_name)) > 0 OR
            INSTR(LOWER(club_name), LOWER(?)) > 0
        )
        ORDER BY won DESC, lost ASC
    """, (match["team_id"], opp_team_name, opp_team_name, opp_team_name)).fetchall()

    conn.close()

    return [
        {
            "name": r["player_name"],
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