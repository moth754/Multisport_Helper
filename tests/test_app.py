import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multisport.app import create_app
from tests import helpers as H

HDR = {"X-Requested-With": "multisport"}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.app = create_app(self.data, start_poller=False)
        self.client = self.app.test_client()
        racers, taps = H.field()
        true = H.legs_to_cum([560, 85, 2300, 57, 1400])
        racers.append(H.racer(900, "Sam Example", H.TRI, true[:3] + [true[4], true[4] + 23]))
        self.race = H.race({"Sprint": racers})
        self.taps = {"FastTaps": taps}

    def tearDown(self):
        self.tmp.cleanup()

    def put_settings(self, **values):
        return self.client.put("/api/settings", json={"values": values}, headers=HDR)

    def test_status_starts_with_live_off(self):
        st = self.client.get("/api/status").get_json()
        self.assertFalse(st["live"])
        self.assertFalse(st["configured"])

    def test_token_is_saved_but_never_returned(self):
        r = self.put_settings(webscorer_api_id="12345", webscorer_token="secret99")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertNotIn("secret99", body)
        self.assertTrue(r.get_json()["webscorer_token_set"])
        self.assertNotIn("secret99", self.client.get("/api/settings").get_data(as_text=True))
        saved = json.loads((self.data / "config.json").read_text())
        self.assertEqual(saved["webscorer_token"], "secret99")
        self.assertEqual((self.data / "config.json").stat().st_mode & 0o777, 0o600)
        # a blank token keeps the saved one
        self.put_settings(webscorer_token="")
        self.assertEqual(json.loads((self.data / "config.json").read_text())["webscorer_token"], "secret99")

    def test_bad_settings_are_refused(self):
        self.assertEqual(self.put_settings(refresh_seconds="1").status_code, 400)
        self.assertEqual(self.put_settings(transition_multiple="abc").status_code, 400)

    def test_writes_need_the_header(self):
        r = self.client.post("/api/live", json={"on": True})
        self.assertEqual(r.status_code, 403)

    def test_live_needs_a_race(self):
        self.put_settings(webscorer_api_id="12345")
        r = self.client.post("/api/live", json={"on": True}, headers=HDR)
        self.assertEqual(r.status_code, 400)

    def test_load_race_and_live_toggle(self):
        self.put_settings(webscorer_api_id="12345")
        with mock.patch("multisport.webscorer.fetch_race", return_value=self.race), \
                mock.patch("multisport.webscorer.fetch_taps", return_value=self.taps):
            r = self.client.post("/api/race", json={"race_id": "4242"}, headers=HDR)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        st = r.get_json()
        self.assertEqual(st["race"]["name"], "Test Triathlon")
        self.assertEqual(st["problems"], 1)
        a = self.client.get("/api/analysis").get_json()["analysis"]
        self.assertEqual(a["problems"][0]["bib"], "900")
        r = self.client.post("/api/live", json={"on": True}, headers=HDR)
        self.assertTrue(r.get_json()["live"])
        r = self.client.post("/api/live", json={"on": False}, headers=HDR)
        self.assertFalse(r.get_json()["live"])
        # the race ID is remembered, the results are not
        saved = json.loads((self.data / "config.json").read_text())
        self.assertEqual(saved["race_id"], "4242")
        self.assertNotIn("Sam Example", (self.data / "config.json").read_text())

    def test_same_data_does_not_bump_version(self):
        self.put_settings(webscorer_api_id="12345")
        with mock.patch("multisport.webscorer.fetch_race", return_value=self.race), \
                mock.patch("multisport.webscorer.fetch_taps", return_value=self.taps):
            v1 = self.client.post("/api/race", json={"race_id": "4242"}, headers=HDR).get_json()["version"]
            v2 = self.client.post("/api/refresh", headers=HDR).get_json()["version"]
        self.assertEqual(v1, v2)

    def test_webscorer_error_is_shown(self):
        from multisport.webscorer import WebscorerError
        self.put_settings(webscorer_api_id="12345")
        with mock.patch("multisport.webscorer.fetch_race", side_effect=WebscorerError("Webscorer: Invalid race")):
            r = self.client.post("/api/race", json={"race_id": "1"}, headers=HDR)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Invalid race", r.get_json()["error"])
        self.assertEqual(self.client.get("/api/status").get_json()["race_id"], "")

    def test_mistyped_race_keeps_the_current_one(self):
        from multisport.webscorer import WebscorerError
        self.put_settings(webscorer_api_id="12345")
        with mock.patch("multisport.webscorer.fetch_race", return_value=self.race), \
                mock.patch("multisport.webscorer.fetch_taps", return_value=self.taps):
            self.client.post("/api/race", json={"race_id": "4242"}, headers=HDR)
        with mock.patch("multisport.webscorer.fetch_race", side_effect=WebscorerError("Webscorer: Invalid race")):
            r = self.client.post("/api/race", json={"race_id": "4243"}, headers=HDR)
        self.assertEqual(r.status_code, 400)
        st = self.client.get("/api/status").get_json()
        self.assertEqual(st["race_id"], "4242")
        self.assertEqual(st["problems"], 1)
        self.assertIsNone(st["fetch"]["error"])
        self.assertEqual(json.loads((self.data / "config.json").read_text())["race_id"], "4242")

    def test_failed_live_fetch_is_shown(self):
        from multisport.webscorer import WebscorerError
        self.put_settings(webscorer_api_id="12345")
        with mock.patch("multisport.webscorer.fetch_race", return_value=self.race), \
                mock.patch("multisport.webscorer.fetch_taps", return_value=self.taps):
            self.client.post("/api/race", json={"race_id": "4242"}, headers=HDR)
        with mock.patch("multisport.webscorer.fetch_race", side_effect=WebscorerError("Can't reach Webscorer")):
            self.client.post("/api/refresh", headers=HDR)
        st = self.client.get("/api/status").get_json()
        self.assertIn("Can't reach", st["fetch"]["error"])
        self.assertEqual(st["problems"], 1)  # last good results stay on screen

    def test_race_id_must_be_a_number(self):
        self.put_settings(webscorer_api_id="12345")
        r = self.client.post("/api/race", json={"race_id": "abc"}, headers=HDR)
        self.assertEqual(r.status_code, 400)

    def test_password_login(self):
        r = self.client.post("/api/settings/password", json={"password": "letmein"}, headers=HDR)
        self.assertTrue(r.get_json()["password_set"])
        other = self.app.test_client()
        self.assertEqual(other.get("/api/status").status_code, 401)
        self.assertEqual(other.get("/").status_code, 302)
        other.post("/login", data={"password": "letmein"})
        self.assertEqual(other.get("/api/status").status_code, 200)
        self.assertNotIn("letmein", (self.data / "config.json").read_text())

    def test_page_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"LIVE UPDATE OFF", r.data)


if __name__ == "__main__":
    unittest.main()
