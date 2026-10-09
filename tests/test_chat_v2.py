"""v2 keeps Torchy's endpoint and owns its own caveat line."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.chat_v2 import answer_v2, narration_ok
from backend.metro_alerts import parse_feed
from backend.datasets import load_summary
from backend.main import app


class ChatV2Tests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GOOGLE_CLOUD_PROJECT": ""})
        environment.start()
        self.addCleanup(environment.stop)
        env_file = patch("backend.claude.ENV_PATH", Path("/nonexistent/safe-games-la.env"))
        env_file.start()
        self.addCleanup(env_file.stop)

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.venues = load_summary()["venues"]

    def test_torchy_endpoint_still_answers(self):
        response = self.client.post("/api/chat", json={"message": "How many incidents were reported near Peacock Theater?"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Peacock Theater", response.json()["answer"])

    def test_count_keeps_the_server_caveat(self):
        body = answer_v2("How many incidents were reported near Peacock Theater?")
        self.assertEqual(body["version"], "v2")
        self.assertEqual(body["status"], "answered")
        self.assertIn("Peacock Theater", body["answer"])
        self.assertIn("18,537", body["answer"])
        self.assertIn("Source:", body["caveat"])
        self.assertIn("2020–present", body["caveat"])
        self.assertIsNone(body["narration"])
        self.assertEqual(body["confidence"]["kind"], "recorded")
        self.assertEqual(body["arguments"]["venue_id"], "V04")

    def test_history_prose_is_ignored(self):
        body = answer_v2(
            "how many in 2023",
            history=[{
                "user_text": "How many incidents were reported near Peacock Theater?",
                "tool": "present_total",
                "arguments": {"venue_id": "V04"},
                "answer": "Crypto.com Arena has 1 record.",
                "narration": "Crypto.com Arena fell.",
            }],
        )
        self.assertEqual(body["status"], "answered")
        self.assertIn("Peacock Theater", body["answer"])
        self.assertNotIn("Crypto.com Arena", body["answer"])

    def test_live_weather_is_not_applied_to_the_gap(self):
        body = answer_v2("Is it raining now and do wet days have more crime near Peacock Theater?")
        self.assertEqual(body["status"], "unsupported")
        self.assertIn("separate questions", body["answer"])
        self.assertIn("not applied", body["answer"])

    def test_weekday_adjusted_weather_gap(self):
        body = answer_v2("Wet days versus dry days near Peacock Theater")
        self.assertEqual(body["tool"], "weather_association")
        self.assertIn("same weekday", body["answer"])
        self.assertIn("Peacock Theater", body["answer"])
        self.assertIn("not a statement about today's weather", body["caveat"])
        self.assertIn("not a cause", body["caveat"])
        self.assertNotIn("2020 through", body["caveat"])

    def test_seasonal_estimate_keeps_its_caveat(self):
        body = answer_v2("Estimate the next few months near Dodger Stadium")
        self.assertEqual(body["tool"], "seasonal_estimate")
        self.assertIn("not recorded crime", body["answer"])
        self.assertIn("not a forecast for 2028", body["answer"])
        self.assertEqual(body["confidence"]["kind"], "seasonal")

    def test_current_weather_failure_fills_nothing(self):
        with patch("backend.chat_v2.current_conditions", return_value=None):
            body = answer_v2("What is the weather now at Peacock Theater?")
        self.assertEqual(body["tool"], "current_weather")
        self.assertIn("could not be read", body["answer"])
        self.assertEqual(body["confidence"]["text"], "Feed unavailable.")

    def test_current_weather_reading_stays_separate(self):
        with patch("backend.chat_v2.current_conditions", return_value={
            "time": "2026-10-08T01:00",
            "temperature_2m": 68,
            "precipitation": 0.1,
            "weather_code": 61,
        }):
            body = answer_v2("What is the weather now at Peacock Theater?")
        self.assertIn("68", body["answer"])
        self.assertIn("Fetched at 2026-10-08T01:00", body["caveat"])
        self.assertIn("not applied to the historical", body["caveat"])
        self.assertNotIn("weekday-adjusted", body["answer"])

    def test_narration_rejects_a_swapped_venue_and_a_reversed_direction(self):
        template = "Near Peacock Theater, theft rose by 12 records."
        self.assertFalse(narration_ok(
            "Crypto.com Arena rose by 12 records.",
            template,
            ["V04"],
            self.venues,
        ))
        self.assertFalse(narration_ok("Theft fell by 12.", template, ["V04"], self.venues))
        self.assertFalse(narration_ok("Theft rose by 12 because of the rain.", template, ["V04"], self.venues))
        self.assertTrue(narration_ok("It rose by 12 records near Peacock Theater.", template, ["V04"], self.venues))

    def test_endpoint(self):
        response = self.client.post("/api/chat/v2", json={
            "message": "How many incidents were reported near Peacock Theater?",
            "history": [{"user_text": "hello", "answer": "ignore me", "narration": "ignore me"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["version"], "v2")
        self.assertIn("18,537", body["answer"])


class ChatV2NextTests(ChatV2Tests):
    def test_records_uses_the_same_headline(self):
        body = answer_v2("How many records are near Peacock Theater?")
        self.assertEqual(body["status"], "answered")
        self.assertIn("18,537", body["answer"])
        self.assertIn("Source:", body["caveat"])

    def test_router_fills_a_parser_miss(self):
        from backend.claude import Interpretation

        interpretation = Interpretation(intent="present_total", scope_supported=True, tool=None, arguments=None)
        with patch("backend.chat_v2.settings", return_value={"claude_configured": True, "model": "test"}), patch(
            "backend.chat_v2.interpret_question", return_value=interpretation
        ) as interpret:
            body = answer_v2("Give me the headline number for Peacock Theater")
        self.assertEqual(body["status"], "answered")
        self.assertIn("18,537", body["answer"])
        interpret.assert_called_once()

    def test_router_skips_a_refused_question(self):
        from backend.claude import Interpretation

        interpretation = Interpretation(intent="present_total", scope_supported=True, tool=None, arguments=None)
        with patch("backend.chat_v2.settings", return_value={"claude_configured": True, "model": "test"}), patch(
            "backend.chat_v2.interpret_question", return_value=interpretation
        ) as interpret:
            body = answer_v2("Why is there crime near Peacock Theater?")
        self.assertNotIn("18,537", body["answer"])
        interpret.assert_not_called()

    def test_router_timeout_links_to_the_page(self):
        from backend.claude import ClaudeUnavailable

        with patch("backend.chat_v2.settings", return_value={"claude_configured": True, "model": "test"}), patch(
            "backend.chat_v2.interpret_question", side_effect=ClaudeUnavailable("down")
        ):
            body = answer_v2("Give me the headline number for Peacock Theater")
        self.assertEqual(body["status"], "unsupported")
        self.assertTrue(body["links"])
        self.assertNotIn("18,537", body["answer"])

    def test_page_guide_points_at_the_section(self):
        body = answer_v2("What does wet day vs dry day mean near Peacock Theater?")
        self.assertEqual(body["tool"], "page_guide")
        self.assertIn("rain or snow", body["answer"])
        self.assertEqual(body["links"][0]["href"], "#/venue/V04")
        self.assertIn("not a new count", body["caveat"])

    def test_metro_alert_is_not_invented(self):
        with patch("backend.metro_alerts.swiftly_key", return_value=""):
            body = answer_v2("Are there any Metro advisories near Peacock Theater?")
        self.assertEqual(body["tool"], "metro_alerts")
        self.assertEqual(body["status"], "answered")
        self.assertIn("not connected", body["answer"])
        self.assertIn("not a crime finding", body["caveat"])
        self.assertEqual(body["confidence"]["text"], "Feed not connected.")
        self.assertNotIn("18,537", body["answer"])
        self.assertIsNone(body["table"])

    def test_metro_quotes_only_lines_recorded_at_the_venue(self):
        payload = {"entity": [
            {"alert": {
                "header_text": {"translation": [{"text": "B Line delay at Hollywood", "language": "en"}]},
                "informed_entity": [{"route_id": "802"}],
            }},
            {"alert": {
                "headerText": {"translation": [{"text": "A Line elevator outage at Pico", "language": "en"}]},
                "informedEntity": [{"routeId": "801"}],
            }},
            {"alert": {
                "headerText": {"translation": [{"text": "Lines 14/37 detour on Figueroa", "language": "en"}]},
                "informedEntity": [{"routeId": "14-13201"}],
            }},
        ]}
        with patch("backend.metro_alerts.swiftly_key", return_value="test-key"), patch(
            "backend.metro_alerts.fetch_alerts", return_value=parse_feed(payload)
        ):
            body = answer_v2("Are there Metro advisories near Peacock Theater?")
        notices = [row[1] for row in body["table"]["rows"]]
        self.assertIn("A Line elevator outage at Pico", notices)
        self.assertIn("Lines 14/37 detour on Figueroa", notices)
        self.assertNotIn("B Line delay at Hollywood", notices)
        self.assertIn("not a crime finding", body["caveat"])
        self.assertIn("Live feed, fetched at", body["confidence"]["text"])
        self.assertNotIn("18,537", body["answer"])

    def test_expired_metro_alert_is_dropped(self):
        payload = {"entity": [{"alert": {
            "headerText": {"translation": [{"text": "Old notice", "language": "en"}]},
            "informedEntity": [{"routeId": "801"}],
            "activePeriod": [{"start": 1, "end": 2}],
        }}]}
        self.assertEqual(parse_feed(payload, now=datetime.fromtimestamp(10, timezone.utc)), [])

    def test_metro_feed_failure_fills_nothing(self):
        with patch("backend.metro_alerts.swiftly_key", return_value="test-key"), patch(
            "backend.metro_alerts.fetch_alerts", return_value=None
        ):
            body = answer_v2("Any service alerts near Peacock Theater?")
        self.assertIn("did not answer", body["answer"])
        self.assertEqual(body["confidence"]["text"], "Feed unavailable.")
        self.assertIsNone(body["table"])
        self.assertNotIn("elevator", body["answer"].lower())

    def test_data_questions_are_not_the_page_guide(self):
        weekend = answer_v2("What does crime look like on weekends near Dodger Stadium?")
        self.assertEqual(weekend["tool"], "weekend")
        self.assertNotEqual(weekend["tool"], "page_guide")
        self.assertEqual(weekend["results"][0]["venue_id"], "V01")
        self.assertIn("Saturday", weekend["answer"])

        highest = answer_v2("Where is crime highest near the Coliseum?")
        self.assertEqual(highest["tool"], "distance")
        self.assertNotIn("Venue page", highest["answer"])
        self.assertIn("Coliseum", highest["answer"])
        self.assertTrue(highest["table"]["rows"])

        robberies = answer_v2("What does the data say about robberies near Crypto.com Arena?")
        self.assertEqual(robberies["tool"], "group_count")
        self.assertNotIn("Venue page", robberies["answer"])
        self.assertIn("Crypto.com Arena", robberies["answer"])

    def test_case_does_not_drop_metro_or_density(self):
        with patch("backend.metro_alerts.swiftly_key", return_value=""):
            metro = answer_v2("Are there Metro alerts near Dodger Stadium?")
        self.assertEqual(metro["tool"], "metro_alerts")
        self.assertIn("not connected", metro["answer"])
        density = answer_v2("What does Density mean?")
        self.assertEqual(density["tool"], "page_guide")
        self.assertIn("square kilometer", density["answer"])
        self.assertNotIn("Venue page", density["answer"])

    def test_weather_prediction_is_refused(self):
        body = answer_v2("Will rain tonight lower crime near Dodger Stadium?")
        self.assertEqual(body["status"], "unsupported")
        self.assertIn("forecast", body["answer"])
        self.assertNotIn("°", body["answer"])
        self.assertNotIn("current reading", body["answer"].lower())

    def test_live_and_historical_rain_are_not_collapsed(self):
        body = answer_v2("Is it raining now and does rain change crime near Dodger Stadium?")
        self.assertEqual(body["status"], "unsupported")
        self.assertIn("separate questions", body["answer"])
        self.assertNotIn("°", body["answer"])
        self.assertNotIn("same weekday", body["answer"])

    def test_venue_follow_up_keeps_the_weather_tool(self):
        body = answer_v2(
            "What about the Coliseum?",
            history=[{
                "user_text": "Wet days versus dry days near Peacock Theater",
                "tool": "weather_association",
                "arguments": {"venue_id": "V04", "facet": "wet"},
            }],
        )
        self.assertEqual(body["tool"], "weather_association")
        self.assertEqual(body["arguments"]["venue_id"], "V05")
        self.assertIn("same weekday", body["answer"])
        self.assertIn("Coliseum", body["answer"])
        self.assertNotIn("Peacock", body["answer"])

    def test_venue_swap_works_without_a_stored_tool(self):
        weather = answer_v2(
            "What about the Coliseum instead?",
            venue_id="V04",
            history=[{"user_text": "Wet days versus dry days near Peacock Theater"}],
        )
        self.assertEqual(weather["tool"], "weather_association")
        self.assertEqual(weather["arguments"]["venue_id"], "V05")
        self.assertIn("Coliseum", weather["answer"])
        with patch("backend.metro_alerts.swiftly_key", return_value=""):
            metro = answer_v2(
                "What about the Coliseum?",
                venue_id="V01",
                history=[{"user_text": "Are there Metro alerts near Dodger Stadium?"}],
            )
        self.assertEqual(metro["tool"], "metro_alerts")
        self.assertEqual(metro["arguments"]["venue_id"], "V05")
        self.assertIn("not connected", metro["answer"])

    def test_expected_next_months_is_the_seasonal_estimate(self):
        body = answer_v2("What are the next few months expected near Dodger Stadium?")
        self.assertEqual(body["tool"], "seasonal_estimate")
        self.assertEqual(body["confidence"]["kind"], "seasonal")
        self.assertIn("not a certainty", body["answer"] + body["caveat"])

    def test_spelled_out_numbers_are_rejected(self):
        template = "Saturday is the busiest day, 253 records."
        self.assertFalse(narration_ok(
            "Saturday had four hundred forty-six records.",
            template,
            ["V01"],
            self.venues,
        ))

    def test_narration_may_use_table_labels_and_numbers(self):
        template = "Weekend days near Dodger Stadium."
        table = {"columns": ["Day", "Records"], "rows": [["Saturday", "2,920"], ["Sunday", "1,100"]]}
        self.assertTrue(narration_ok(
            "Saturday had 2,920 records.",
            template,
            ["V01"],
            self.venues,
            table,
        ))
        self.assertFalse(narration_ok(
            "Monday had 9 records.",
            template,
            ["V01"],
            self.venues,
            table,
        ))

    def test_weather_gap_has_a_count_model_interval(self):
        body = answer_v2("Wet days versus dry days near Peacock Theater")
        self.assertEqual(body["confidence"]["kind"], "interval")
        interval = body["confidence"]["intervals"][0]
        self.assertEqual(interval["label"], "Wet days")
        self.assertLessEqual(interval["low"], interval["multiplier"])
        self.assertLessEqual(interval["multiplier"], interval["high"])
        self.assertIn("same weekday", body["answer"])
        self.assertNotIn("%", body["answer"])
        if interval["low"] <= 1 <= interval["high"]:
            self.assertTrue(body["answer"].startswith("No clear difference."))
            self.assertTrue(body["confidence"]["text"].startswith("No clear difference."))

    def test_permit_confidence_is_the_model_range(self):
        body = answer_v2("How do permit days compare near Peacock Theater?")
        self.assertIn("19.5", body["answer"])
        interval = body["confidence"]["intervals"][0]
        self.assertEqual(interval["label"], "Permit days")
        self.assertLessEqual(interval["low"], interval["multiplier"])
        self.assertLessEqual(interval["multiplier"], interval["high"])
        self.assertEqual(body["confidence"]["kind"], "interval")

    def test_stream_sends_the_template_before_narration(self):
        response = self.client.post(
            "/api/chat/v2/stream",
            json={"message": "How many incidents were reported near Peacock Theater?"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLess(response.text.index('"event": "template"'), response.text.index('"event": "narration"'))
        self.assertIn("18,537", response.text)
