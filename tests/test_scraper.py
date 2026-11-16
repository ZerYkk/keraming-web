import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import main
import scraper


class FakeResponse:
    status_code = 200

    def __init__(self, text=""):
        self.text = text


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def get(self, url):
        if url.endswith("/supisky"):
            return FakeResponse("""
                <div class="card__supisky">
                  <div class="sticky-header"><h3>Keraming Trenčín E</h3></div>
                  <div class="card" onclick="location.href = '/hrac/123'">
                    <p class="trn__avatar__name"><span class="badge">A</span>Hráč Bez Zápasu</p>
                  </div>
                </div>
                <div class="card__supisky">
                  <div class="sticky-header"><h3>Keraming Trenčín F</h3></div>
                  <div class="card" onclick="location.href = '/hrac/123'">
                    <p class="trn__avatar__name"><span class="badge">B</span>Hráč Bez Zápasu</p>
                  </div>
                </div>
            """)
        return FakeResponse()


class ScraperRosterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = str(Path(self.temp_dir.name) / "rosters.db")
        self.original_db_file = main.DB_FILE
        self.original_leagues = scraper.LEAGUES
        main.DB_FILE = self.db_file
        scraper.LEAGUES = {
            "E": {
                "match_name": "Keraming Trenčín E",
                "url": "https://example.test/league"
            },
            "F": {
                "match_name": "Keraming Trenčín F",
                "url": "https://example.test/league"
            }
        }

    async def asyncTearDown(self):
        main.DB_FILE = self.original_db_file
        scraper.LEAGUES = self.original_leagues
        self.temp_dir.cleanup()

    async def test_profile_includes_rosters_without_played_matches(self):
        with patch.object(scraper.httpx, "Client", FakeClient):
            player_count, match_count = scraper.sync_from_stolnytenis(self.db_file)

        self.assertEqual((player_count, match_count), (1, 0))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://testserver"
        ) as client:
            response = await client.get("/api/players")

        self.assertEqual(response.status_code, 200, response.text)
        player = next(item for item in response.json() if item["name"] == "Hráč Bez Zápasu")
        self.assertEqual(player["roster"], ["E", "F"])
        self.assertEqual(
            [(stat["leagueId"], stat["encounters"], stat["totalMatches"]) for stat in player["leagueStats"]],
            [("E", 0, 0), ("F", 0, 0)]
        )


if __name__ == "__main__":
    unittest.main()
