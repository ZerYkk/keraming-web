import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import httpx

import main


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = main.DB_FILE
        main.DB_FILE = str(Path(self.temp_dir.name) / "test.db")
        main.init_db()

        conn = main.get_db()
        conn.executemany(
            "INSERT INTO players (name, primary_team, roster) VALUES (?, ?, ?)",
            [("Hráč Jeden", "A", "A"), ("Hráč Dva", "B", "B")]
        )
        conn.commit()
        conn.close()
        main.create_admin_account("admin", "AdminHesloTest123")

        transport = httpx.ASGITransport(app=main.app)
        self.client = httpx.AsyncClient(transport=transport, base_url="https://testserver")
        self.admin_client = httpx.AsyncClient(transport=transport, base_url="https://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.admin_client.aclose()
        main.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    async def login(self, client, username, password):
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_admin_page_route_is_available_for_refreshes(self):
        response = await self.client.get("/sprava-uctov")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("login-form", response.text)

    async def test_site_uses_keraming_logo_for_header_and_favicon(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('rel="icon" type="image/png" href="/static/img/keraming-logo.png"', response.text)
        self.assertIn('src="/static/img/keraming-logo.png"', response.text)

        logo = await self.client.get("/static/img/keraming-logo.png")
        self.assertEqual(logo.status_code, 200, logo.text)
        self.assertEqual(logo.headers["content-type"], "image/png")

    async def test_admin_can_read_signup_fields(self):
        await self.login(self.admin_client, "admin", "AdminHesloTest123")
        response = await self.admin_client.get("/api/players")

        self.assertEqual(response.status_code, 200, response.text)
        player = next(item for item in response.json() if item["name"] == "Hráč Jeden")
        self.assertIn("canMorning", player)
        self.assertIn("selectedLeagues", player)
        self.assertIn("note", player)

    async def test_reset_admin_password_changes_password_and_revokes_sessions(self):
        await self.login(self.admin_client, "admin", "AdminHesloTest123")
        main.reset_admin_password("admin", "NoveAdminHeslo123")

        self.assertEqual((await self.admin_client.get("/api/auth/me")).status_code, 401)
        login = await self.login(self.client, "admin", "NoveAdminHeslo123")
        self.assertEqual(login["role"], "admin")

    async def test_session_cookie_is_secure_on_public_hosts_but_not_local_http(self):
        previous_override = main.COOKIE_SECURE_OVERRIDE
        main.COOKIE_SECURE_OVERRIDE = None
        try:
            self.assertFalse(main.session_cookie_is_secure(httpx.Request("GET", "http://127.0.0.1:8000")))
            self.assertFalse(main.session_cookie_is_secure(httpx.Request("GET", "http://localhost:8000")))
            self.assertTrue(main.session_cookie_is_secure(httpx.Request("GET", "https://example.com")))
            self.assertTrue(main.session_cookie_is_secure(httpx.Request("GET", "https://localhost")))
            self.assertTrue(main.session_cookie_is_secure(httpx.Request("GET", "http://example.com")))
        finally:
            main.COOKIE_SECURE_OVERRIDE = previous_override

    async def test_login_persists_session_over_local_http(self):
        previous_override = main.COOKIE_SECURE_OVERRIDE
        main.COOKIE_SECURE_OVERRIDE = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="http://127.0.0.1:8000"
            ) as local_client:
                login_response = await local_client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "AdminHesloTest123"}
                )
                self.assertEqual(login_response.status_code, 200, login_response.text)
                self.assertNotIn("; secure", login_response.headers["set-cookie"].lower())
                response = await local_client.get("/api/auth/me")
                self.assertEqual(response.status_code, 200, response.text)
        finally:
            main.COOKIE_SECURE_OVERRIDE = previous_override

    async def test_failed_login_records_attempt_without_server_error(self):
        response = await self.client.post(
            "/api/auth/login",
            json={"username": "neexistujuci.ucet", "password": "nespravne"}
        )
        self.assertEqual(response.status_code, 401, response.text)

        conn = main.get_db()
        attempt = conn.execute(
            "SELECT remote_address FROM login_attempts WHERE username_hash = ?",
            (hashlib.sha256(b"neexistujuci.ucet").hexdigest(),)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(attempt)
        self.assertTrue(attempt["remote_address"])

    async def test_init_migrates_login_attempts_without_remote_address(self):
        conn = main.get_db()
        conn.execute("DROP TABLE login_attempts")
        conn.execute("""
            CREATE TABLE login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username_hash TEXT NOT NULL,
                attempted_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        main.init_db()
        conn = main.get_db()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(login_attempts)")}
        conn.close()
        self.assertIn("remote_address", columns)

    async def test_password_change_accepts_eight_characters(self):
        admin = await self.login(self.admin_client, "admin", "AdminHesloTest123")
        created = await self.admin_client.post(
            "/api/admin/accounts",
            headers={"X-CSRF-Token": admin["csrfToken"]},
            json={"playerId": 1, "username": "hrac.jeden"}
        )
        self.assertEqual(created.status_code, 200, created.text)
        player = await self.login(self.client, "hrac.jeden", created.json()["temporaryPassword"])
        csrf = {"X-CSRF-Token": player["csrfToken"]}

        too_short = await self.client.post(
            "/api/auth/change-password",
            headers=csrf,
            json={"newPassword": "Heslo12"}
        )
        self.assertEqual(too_short.status_code, 422)

        changed = await self.client.post(
            "/api/auth/change-password",
            headers=csrf,
            json={"newPassword": "Heslo123"}
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(changed.json()["mustChangePassword"])
        await self.login(self.admin_client, "hrac.jeden", "Heslo123")

    async def test_weekly_signup_reset_runs_after_saturday_only_once(self):
        conn = main.get_db()
        conn.execute("""
            UPDATE players
            SET can_morning = 1, can_afternoon = 1, cannot_play = 0,
                selected_leagues = 'A', takes_car = 1, note = 'Sobota'
            WHERE id = 1
        """)
        conn.commit()
        conn.close()

        saturday = datetime(2026, 11, 21, 18, 0)
        self.assertFalse(main.reset_weekly_signups(saturday))
        conn = main.get_db()
        morning = conn.execute("SELECT can_morning FROM players WHERE id = 1").fetchone()[0]
        conn.close()
        self.assertEqual(morning, 1)

        sunday = datetime(2026, 11, 22, 0, 1)
        self.assertTrue(main.reset_weekly_signups(sunday))
        self.assertFalse(main.reset_weekly_signups(sunday))

        conn = main.get_db()
        reset = conn.execute("""
            SELECT can_morning, can_afternoon, cannot_play,
                   selected_leagues, takes_car, note
            FROM players WHERE id = 1
        """).fetchone()
        conn.close()
        self.assertEqual(
            (reset["can_morning"], reset["can_afternoon"], reset["cannot_play"],
             reset["selected_leagues"], reset["takes_car"], reset["note"]),
            (0, 0, 0, "", 0, "")
        )

    async def test_admin_can_reset_all_weekly_signups_immediately(self):
        conn = main.get_db()
        conn.execute("""
            UPDATE players
            SET can_morning = 1, can_afternoon = 1, cannot_play = 0,
                selected_leagues = 'A', takes_car = 1, note = 'Administratívny reset'
        """)
        conn.commit()
        conn.close()

        admin = await self.login(self.admin_client, "admin", "AdminHesloTest123")
        response = await self.admin_client.post(
            "/api/admin/signup/reset",
            headers={"X-CSRF-Token": admin["csrfToken"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])

        conn = main.get_db()
        rows = conn.execute("""
            SELECT can_morning, can_afternoon, cannot_play,
                   selected_leagues, takes_car, note
            FROM players
        """).fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(
                (row["can_morning"], row["can_afternoon"], row["cannot_play"],
                 row["selected_leagues"], row["takes_car"], row["note"]),
                (0, 0, 0, "", 0, "")
            )

    async def test_player_accounts_are_owned_and_admin_reset_revokes_sessions(self):
        signup_payload = {
            "canMorning": True,
            "canAfternoon": False,
            "cannotPlay": False,
            "selectedLeagues": [],
            "takesCar": False,
            "note": ""
        }
        self.assertEqual(
            (await self.client.post("/api/signup", json=signup_payload)).status_code,
            401
        )

        admin = await self.login(self.admin_client, "admin", "AdminHesloTest123")
        csrf = {"X-CSRF-Token": admin["csrfToken"]}
        missing_csrf = await self.admin_client.post(
            "/api/admin/accounts",
            json={"playerId": 1, "username": "hrac.jeden"}
        )
        self.assertEqual(missing_csrf.status_code, 403)
        created = await self.admin_client.post(
            "/api/admin/accounts",
            headers=csrf,
            json={"playerId": 1, "username": "hrac.jeden"}
        )
        self.assertEqual(created.status_code, 200, created.text)
        temporary_password = created.json()["temporaryPassword"]
        self.assertEqual(len(temporary_password), 8)
        self.assertTrue(set(temporary_password) <= set(main.TEMP_PASSWORD_ALPHABET))
        conn = main.get_db()
        stored_hash = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'hrac.jeden'"
        ).fetchone()[0]
        conn.close()
        self.assertNotEqual(stored_hash, temporary_password)
        self.assertTrue(main.verify_password(temporary_password, stored_hash))

        player = await self.login(self.client, "hrac.jeden", temporary_password)
        self.assertTrue(player["mustChangePassword"])
        denied = await self.client.post(
            "/api/signup",
            headers={"X-CSRF-Token": player["csrfToken"]},
            json=signup_payload
        )
        self.assertEqual(denied.status_code, 403)

        changed = await self.client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": player["csrfToken"]},
            json={"newPassword": "HracoveNoveHeslo123"}
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(changed.json()["mustChangePassword"])

        saved = await self.client.post(
            "/api/signup",
            headers={"X-CSRF-Token": changed.json()["csrfToken"]},
            json={
                **signup_payload,
                "playerId": 2,
                "canMorning": True,
                "selectedLeagues": ["A", "B"],
                "takesCar": False,
                "note": "Vlastné údaje"
            }
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        conn = main.get_db()
        one = conn.execute("SELECT can_morning, selected_leagues, note FROM players WHERE id = 1").fetchone()
        two = conn.execute("SELECT can_morning, note FROM players WHERE id = 2").fetchone()
        conn.close()
        self.assertEqual((one["can_morning"], one["selected_leagues"], one["note"]), (1, "A", "Vlastné údaje"))
        self.assertEqual((two["can_morning"], two["note"]), (0, ""))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="https://testserver"
        ) as public_client:
            public_players = (await public_client.get("/api/players")).json()
        self.assertNotIn("note", public_players[0])
        self.assertNotIn("canMorning", public_players[0])

        reset = await self.admin_client.post(
            "/api/admin/accounts/1/reset-password",
            headers=csrf
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(len(reset.json()["temporaryPassword"]), 8)
        self.assertEqual((await self.client.get("/api/auth/me")).status_code, 401)

        reset_login = await self.login(self.client, "hrac.jeden", reset.json()["temporaryPassword"])
        self.assertTrue(reset_login["mustChangePassword"])

    async def test_admin_routes_reject_player_accounts(self):
        admin = await self.login(self.admin_client, "admin", "AdminHesloTest123")
        created = await self.admin_client.post(
            "/api/admin/accounts",
            headers={"X-CSRF-Token": admin["csrfToken"]},
            json={"playerId": 1, "username": "hrac.jeden"}
        )
        self.assertEqual(created.status_code, 200, created.text)
        player = await self.login(self.client, "hrac.jeden", created.json()["temporaryPassword"])
        changed = await self.client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": player["csrfToken"]},
            json={"newPassword": "HracoveNoveHeslo123"}
        )
        self.assertEqual(changed.status_code, 200, changed.text)

        response = await self.client.get("/api/admin/accounts")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
