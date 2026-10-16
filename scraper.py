import sqlite3
import httpx
from bs4 import BeautifulSoup
import re

LEAGUES = {
    "A": {
        "match_name": "Keraming Trenčín",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-joola-extraliga-muzi-sstz"
    },
    "B": {
        "match_name": "Keraming Trenčín B",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-2-liga-muzi-kstz-trencin"
    },
    "C": {
        "match_name": "Keraming Trenčín C",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-3-liga-muzi-kstz-trencin"
    },
    "D": {
        "match_name": "Keraming Trenčín D",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-4-liga-muzi-kstz-trencin"
    },
    "E": {
        "match_name": "Keraming Trenčín E",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-5-liga-muzi-ostz-trencin"
    },
    "F": {
        "match_name": "Keraming Trenčín F",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-5-liga-muzi-ostz-trencin"
    },
    "G": {
        "match_name": "Keraming Trenčín G",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-6-liga-muzi-ostz-trencin"
    }
}


def ensure_tables(conn):
    c = conn.cursor()
    # Hráči Keramingu (pre prihlasovanie a rebríček)
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

    # Zápasy
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

    # Všetci hráči v ligách (pre zobrazenie úspešnosti súperov)
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


def sync_from_stolnytenis(db_file: str):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    cursor = conn.cursor()

    synced_matches = 0
    synced_club_players = 0
    synced_opponents = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
        for team_id, info in LEAGUES.items():
            base_url = info["url"].rstrip("/")
            target_team = info["match_name"]

            # 1. ROZPIS ZÁPASOV
            try:
                resp = client.get(f"{base_url}/rozpis")
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for m_div in soup.find_all("div", class_=re.compile("mtchr row")):
                        match_id = m_div.get("data-match_id")
                        date_span = m_div.find("span", class_="mtchr__round__date")
                        match_date_str = date_span.get_text(strip=True) if date_span else ""

                        num_div = m_div.find("div", class_="mtchr__num")
                        round_str = f"{num_div.get_text(strip=True)} kolo" if num_div else "1. kolo"

                        home_a = m_div.find("a", class_="mtchr__home")
                        away_a = m_div.find("a", class_="mtchr__away")
                        home_name = home_a.get_text(strip=True) if home_a else ""
                        away_name = away_a.get_text(strip=True) if away_a else ""

                        if target_team.lower() not in home_name.lower() and target_team.lower() not in away_name.lower():
                            continue

                        is_home = target_team.lower() in home_name.lower()
                        home_score = m_div.find("a", class_="mtchr__home__score")
                        away_score = m_div.find("a", class_="mtchr__away__score")

                        status = "upcoming"
                        score_str = None
                        if home_score and away_score:
                            score_str = f"{home_score.get_text(strip=True)} : {away_score.get_text(strip=True)}"
                            status = "played"

                        slot = "Doobeda (10:00)"
                        if any(h in match_date_str for h in ["13:", "14:", "15:", "16:", "17:", "18:"]):
                            slot = "Poobede (14:00+)"

                        cursor.execute("""
                            INSERT INTO matches (external_id, team_id, round, match_date, slot, home_team, away_team, is_home, status, score)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(external_id) DO UPDATE SET
                                status=excluded.status,
                                score=excluded.score
                        """, (
                            match_id, team_id, round_str, match_date_str, slot,
                            home_name, away_name, 1 if is_home else 0, status, score_str
                        ))
                        synced_matches += 1
            except Exception as e:
                print(f"Chyba rozpisu {team_id}: {e}")

            # 2. ÚSPEŠNOSŤ VŠETKÝCH HRÁČOV V LIGE (Keraming + Súperi)
            try:
                resp_usp = client.get(f"{base_url}/uspesnost")
                if resp_usp.status_code == 200:
                    soup_usp = BeautifulSoup(resp_usp.text, "html.parser")
                    player_cards = soup_usp.find_all("div", class_=re.compile("gridc_usp"))

                    for card in player_cards:
                        club_p = card.find("p", class_="trn__avatar__club")
                        name_p = card.find("p", class_="trn__avatar__name")
                        if not club_p or not name_p:
                            continue

                        club_name = club_p.get_text(strip=True)
                        player_name = name_p.get_text(strip=True)

                        c4 = card.find("div", class_="gridc__c4")
                        c5 = card.find("div", class_="gridc__c5")

                        won_val = int(c4.find("span", class_="grd__value").get_text(strip=True)) if c4 and c4.find("span", class_="grd__value") else 0
                        lost_val = int(c5.find("span", class_="grd__value").get_text(strip=True)) if c5 and c5.find("span", class_="grd__value") else 0

                        # A) Uložíme do zoznamu hráčov celej ligy (pre súperov)
                        cursor.execute("""
                            INSERT INTO league_players (league_id, club_name, player_name, won, lost)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(league_id, club_name, player_name) DO UPDATE SET
                                won=excluded.won,
                                lost=excluded.lost
                        """, (team_id, club_name, player_name, won_val, lost_val))
                        synced_opponents += 1

                        # B) Ak je to náš hráč Keramingu, aktualizujeme aj internú tabuľku players
                        if "keraming" in club_name.lower():
                            existing = cursor.execute("SELECT roster, won, lost FROM players WHERE name = ?", (player_name,)).fetchone()
                            if not existing:
                                cursor.execute("""
                                    INSERT INTO players (name, primary_team, roster, won, lost)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (player_name, team_id, team_id, won_val, lost_val))
                            else:
                                rosters = [x.strip() for x in existing["roster"].split(",") if x.strip()]
                                if team_id not in rosters:
                                    rosters.append(team_id)
                                total_won = existing["won"] + won_val
                                total_lost = existing["lost"] + lost_val
                                cursor.execute("""
                                    UPDATE players SET roster = ?, won = ?, lost = ? WHERE name = ?
                                """, (",".join(rosters), total_won, total_lost, player_name))
                            synced_club_players += 1

            except Exception as e:
                print(f"Chyba úspešnosti {team_id}: {e}")

    conn.commit()
    conn.close()
    print(f"Úspešne synchronizované: {synced_matches} zápasov, {synced_club_players} našich hráčov a {synced_opponents} hráčov súperov.")
    return synced_club_players, synced_matches


if __name__ == "__main__":
    sync_from_stolnytenis("keraming.db")