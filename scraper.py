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
    },
    "H": {
        "match_name": "Keraming Trenčín",
        "url": "https://www.stolnytenis.info/liga/sezona-2026-27-extraliga-zeny-sstz"
    }
}


def ensure_tables(conn):
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


def extract_stat_col(card, col_class: str, label_prefix: str = "") -> int:
    """
    Vytiahne číselnú hodnotu priamo zo stĺpca (napr. gridc__c2 = Stret., gridc__c4 = Výhry, gridc__c5 = Prehry).
    Ak sa trieda nenájde, hľadá podľa prefixu v grd__label.
    """
    col = card.find("div", class_=col_class)
    if col:
        val_span = col.find("span", class_="grd__value")
        if val_span:
            txt = val_span.get_text(strip=True).replace("%", "").strip()
            try:
                return int(float(txt))
            except ValueError:
                pass

    if label_prefix:
        for c in card.find_all("div", class_=re.compile(r"gridc__c\d")):
            lbl = c.find("span", class_="grd__label")
            if lbl and label_prefix.lower() in lbl.get_text(strip=True).lower():
                val_span = c.find("span", class_="grd__value")
                if val_span:
                    txt = val_span.get_text(strip=True).replace("%", "").strip()
                    try:
                        return int(float(txt))
                    except ValueError:
                        return 0
    return 0


def match_team_belongs(team_id: str, target_name: str, team_str: str) -> bool:
    """Presne overí, či názov tímu v zápise patrí danému družstvu (A až G)."""
    t_low = team_str.lower().strip()
    target_low = target_name.lower().strip()
    if team_id == "A":
        # Tím A nemá na konci písmeno B..G
        return "keraming" in t_low and not re.search(r"\b[b-g]\s*$", t_low)
    return target_low in t_low or bool(re.search(rf"keraming.*\b{team_id.lower()}\s*$", t_low))


def extract_stolnytenis_player_id(card):
    profile_link = card.select_one('a[href*="/hrac/"]')
    profile_href = profile_link.get("href", "") if profile_link else ""
    profile_id_match = re.search(r"/hrac/(\d+)/?$", profile_href)
    return profile_id_match.group(1) if profile_id_match else None


def extract_roster_players(soup, team_ids):
    roster_players = []
    for club_card in soup.select("div.card__supisky"):
        club_heading = club_card.select_one(".sticky-header h3")
        if not club_heading:
            continue

        club_name = club_heading.get_text(" ", strip=True)
        if "keraming" not in club_name.lower():
            continue

        matched_tids = [
            team_id for team_id in team_ids
            if match_team_belongs(team_id, LEAGUES[team_id]["match_name"], club_name)
        ]
        if not matched_tids and len(team_ids) == 1:
            matched_tids = team_ids
        if not matched_tids:
            continue

        for name_element in club_card.select(".trn__avatar__name"):
            badge = name_element.select_one(".badge")
            if badge:
                badge.decompose()
            player_name = name_element.get_text(" ", strip=True)
            if not player_name:
                continue

            player_card = name_element.find_parent("div", class_="card")
            onclick = player_card.get("onclick", "") if player_card else ""
            player_id_match = re.search(r"/hrac/(\d+)", onclick)
            roster_players.append({
                "name": player_name,
                "teamIds": matched_tids,
                "playerId": player_id_match.group(1) if player_id_match else None
            })

    return roster_players


def extract_match_schedule(match_div):
    match_group = match_div.find_parent("div", class_="mb-3")
    round_header = match_group.select_one(".mtchrnd__num") if match_group else None
    round_str = round_header.get_text(" ", strip=True) if round_header else "Kolo neurčené"

    date_span = match_div.select_one(".mtchr__round__date, .mtchr__more__round__date")
    source_datetime = date_span.get_text(" ", strip=True) if date_span else ""
    time_match = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", source_datetime)
    if time_match:
        match_date = " ".join(
            (source_datetime[:time_match.start()] + source_datetime[time_match.end():]).split()
        )
        match_time = time_match.group(0)
    else:
        match_date = source_datetime
        match_time = "Čas neurčený"

    return round_str, match_date, match_time


def sync_from_stolnytenis(db_file: str):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    cursor = conn.cursor()

    # Vyčistíme tabuľku ligových štatistík a staré zápasy pred čerstvým sťahovaním,
    # aby nevznikali duplicity z predchádzajúcich behov.
    cursor.execute("DELETE FROM league_players")
    cursor.execute("DELETE FROM matches")
    cursor.execute("UPDATE players SET won = 0, lost = 0")
    conn.commit()

    synced_matches = 0
    synced_club_players = 0
    synced_opponents = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
        # 1. ROZPIS ZÁPASOV
        for team_id, info in LEAGUES.items():
            base_url = info["url"].rstrip("/")
            target_team = info["match_name"]

            try:
                resp = client.get(f"{base_url}/rozpis")
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for m_div in soup.find_all("div", class_=re.compile("mtchr row")):
                        raw_match_id = m_div.get("data-match_id") or ""
                        ext_id = f"{team_id}_{raw_match_id}"

                        round_str, match_date_str, slot = extract_match_schedule(m_div)

                        home_a = m_div.find("a", class_="mtchr__home")
                        away_a = m_div.find("a", class_="mtchr__away")
                        home_name = home_a.get_text(strip=True) if home_a else ""
                        away_name = away_a.get_text(strip=True) if away_a else ""

                        is_home = match_team_belongs(team_id, target_team, home_name)
                        is_away = match_team_belongs(team_id, target_team, away_name)
                        if not is_home and not is_away:
                            continue

                        home_score = m_div.find("a", class_="mtchr__home__score")
                        away_score = m_div.find("a", class_="mtchr__away__score")

                        status = "upcoming"
                        score_str = None
                        if home_score and away_score:
                            h_txt = home_score.get_text(strip=True)
                            a_txt = away_score.get_text(strip=True)
                            # Zápas je odohraný iba vtedy, ak obsahuje reálne číselné skóre a nejde o neodohraný zápas (0:0 bez výsledku)
                            if h_txt.isdigit() and a_txt.isdigit():
                                div_classes = m_div.get("class", [])
                                has_result_class = any(c in div_classes for c in ["mtchr__win", "mtchr__lose", "mtchr__draw"])
                                if (h_txt != "0" or a_txt != "0") or has_result_class:
                                    score_str = f"{h_txt} : {a_txt}"
                                    status = "played"

                        cursor.execute("""
                            INSERT INTO matches (external_id, team_id, round, match_date, slot, home_team, away_team, is_home, status, score)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(external_id) DO UPDATE SET
                                status=excluded.status,
                                score=excluded.score
                        """, (
                            ext_id, team_id, round_str, match_date_str, slot,
                            home_name, away_name, 1 if is_home else 0, status, score_str
                        ))
                        synced_matches += 1
            except Exception as e:
                print(f"Chyba rozpisu {team_id}: {e}")

        # 2. SÚPISKY (každú unikátnu URL stiahneme iba raz)
        unique_urls = {}
        for tid, l_data in LEAGUES.items():
            u = l_data["url"].rstrip("/")
            if u not in unique_urls:
                unique_urls[u] = []
            unique_urls[u].append(tid)

        player_totals = {}

        for u, team_ids in unique_urls.items():
            try:
                roster_response = client.get(f"{u}/supisky")
                if roster_response.status_code != 200:
                    continue

                roster_soup = BeautifulSoup(roster_response.text, "html.parser")
                for roster_player in extract_roster_players(roster_soup, team_ids):
                    player_name = roster_player["name"]
                    matched_tids = roster_player["teamIds"]
                    if player_name not in player_totals:
                        player_totals[player_name] = {
                            "primary_team": matched_tids[0],
                            "rosters": set(),
                            "won": 0,
                            "lost": 0,
                            "stolnytenis_player_id": roster_player["playerId"]
                        }
                    player_totals[player_name]["rosters"].update(matched_tids)
                    if roster_player["playerId"]:
                        player_totals[player_name]["stolnytenis_player_id"] = roster_player["playerId"]
            except Exception as e:
                print(f"Chyba súpisky {u}: {e}")

        # 3. ÚSPEŠNOSŤ PODĽA SÚŤAŽÍ
        for u, team_ids in unique_urls.items():
            try:
                resp_usp = client.get(f"{u}/uspesnost")
                if resp_usp.status_code != 200:
                    continue

                soup_usp = BeautifulSoup(resp_usp.text, "html.parser")
                player_cards = soup_usp.select("div.gridc.gridc_usp")

                for card in player_cards:
                    club_p = card.find("p", class_="trn__avatar__club")
                    name_p = card.find("p", class_="trn__avatar__name")
                    if not club_p or not name_p:
                        continue

                    club_name = club_p.get_text(strip=True)
                    player_name = name_p.get_text(strip=True)
                    stolnytenis_player_id = extract_stolnytenis_player_id(card)

                    # gridc__c2 = Stretnutia, gridc__c4 = Výhry, gridc__c5 = Prehry
                    encounters_val = extract_stat_col(card, "gridc__c2", "stret")
                    won_val = extract_stat_col(card, "gridc__c4", "výh")
                    lost_val = extract_stat_col(card, "gridc__c5", "preh")

                    matched_tids = []
                    if "keraming" in club_name.lower():
                        for tid in team_ids:
                            if match_team_belongs(tid, LEAGUES[tid]["match_name"], club_name):
                                matched_tids.append(tid)
                        if not matched_tids:
                            matched_tids = [team_ids[0]]
                    else:
                        matched_tids = team_ids

                    for tid in matched_tids:
                        cursor.execute("""
                            INSERT INTO league_players (league_id, club_name, player_name, encounters, won, lost)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(league_id, club_name, player_name) DO UPDATE SET
                                encounters=excluded.encounters,
                                won=excluded.won,
                                lost=excluded.lost
                        """, (tid, club_name, player_name, encounters_val, won_val, lost_val))
                        synced_opponents += 1

                    if "keraming" in club_name.lower():
                        if player_name not in player_totals:
                            player_totals[player_name] = {
                                "primary_team": matched_tids[0],
                                "rosters": set(matched_tids),
                                "won": won_val,
                                "lost": lost_val,
                                "stolnytenis_player_id": stolnytenis_player_id
                            }
                        else:
                            player_totals[player_name]["rosters"].update(matched_tids)
                            player_totals[player_name]["won"] += won_val
                            player_totals[player_name]["lost"] += lost_val
                            if stolnytenis_player_id:
                                player_totals[player_name]["stolnytenis_player_id"] = stolnytenis_player_id

            except Exception as e:
                print(f"Chyba úspešnosti {u}: {e}")

        for p_name, p_data in player_totals.items():
            roster_str = ",".join(sorted(list(p_data["rosters"])))
            existing = cursor.execute("SELECT id FROM players WHERE name = ?", (p_name,)).fetchone()
            if not existing:
                cursor.execute("""
                    INSERT INTO players (name, primary_team, roster, won, lost, stolnytenis_player_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    p_name, p_data["primary_team"], roster_str, p_data["won"], p_data["lost"],
                    p_data["stolnytenis_player_id"]
                ))
            else:
                cursor.execute("""
                    UPDATE players
                    SET primary_team = ?, roster = ?, won = ?, lost = ?,
                        stolnytenis_player_id = COALESCE(?, stolnytenis_player_id)
                    WHERE name = ?
                """, (
                    p_data["primary_team"], roster_str, p_data["won"], p_data["lost"],
                    p_data["stolnytenis_player_id"], p_name
                ))
            synced_club_players += 1

    conn.commit()
    conn.close()
    print(f"Úspešne synchronizované: {synced_matches} zápasov, {synced_club_players} našich hráčov a {synced_opponents} záznamov v ligách.")
    return synced_club_players, synced_matches


if __name__ == "__main__":
    sync_from_stolnytenis("keraming.db")