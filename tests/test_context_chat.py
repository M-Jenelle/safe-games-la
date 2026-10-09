"""Check supporting answers against raw CSVs, not model-generated facts."""

from __future__ import annotations

from copy import deepcopy
import csv
import json
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.chat import answer_question
from backend.main import app


def distance(venue, row, lat="latitude", lon="longitude"):
    phi1, phi2 = math.radians(venue["latitude"]), math.radians(float(row[lat]))
    delta_phi = phi2 - phi1
    delta_lon = math.radians(float(row[lon]) - venue["longitude"])
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


class ContextChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(__file__).resolve().parents[1] / "data"
        cls.summary = json.loads((cls.data / "processed/venue_summary.json").read_text())
        cls.venues = cls.summary["venues"]
        cls.client = TestClient(app)
        cls.raw = {}
        for source in ("rail_stations", "bus_stops", "fire_stations", "police_stations", "hospitals"):
            with (cls.data / cls.summary["meta"]["sources"][source]).open(encoding="utf-8-sig", newline="") as handle:
                cls.raw[source] = list(csv.DictReader(handle))

    def setUp(self):
        environment = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GOOGLE_CLOUD_PROJECT": ""})
        environment.start()
        self.addCleanup(environment.stop)
        env_file = patch("backend.claude.ENV_PATH", Path("/nonexistent/safe-games-la.env"))
        env_file.start()
        self.addCleanup(env_file.stop)

    def post(self, message, **context):
        body = answer_question(message, context.get("venue_id"), context.get("prior_message"))
        for label in ("LAPD", "2020–2024", "800 m radius"):
            self.assertIn(label, body["answer"])
        return body

    def assert_supporting_source(self, body, source_keys, radius):
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["source"], body["sources"][0])
        self.assertEqual(
            {source["file"] for source in body["sources"]},
            {self.summary["meta"]["sources"][key] for key in source_keys},
        )
        for source in body["sources"]:
            self.assertIn(source["file"], body["answer"])
            self.assertIsNone(source["period"])
            self.assertIsNone(source["coverage_date"])
            self.assertEqual(source["radius_m"], radius)
            self.assertEqual(source["processed_at"], self.summary["meta"]["generated_at"])
        for label in ("Crime context only", "static snapshot", "coverage/as-of date not recorded", "processing time, not a source update date"):
            self.assertIn(label, body["answer"])

    def test_rail_and_bus_counts_and_lines_match_raw_csvs_for_all_venues(self):
        for venue in self.venues:
            with self.subTest(venue=venue["venue_name"]):
                body = self.post(f"What transit is near {venue['venue_name']}?")
                self.assert_supporting_source(body, ["rail_stations", "bus_stops"], 800)
                rail, bus = body["results"]
                raw_rail = [row for row in self.raw["rail_stations"] if distance(venue, row) <= 800]
                raw_bus = [row for row in self.raw["bus_stops"] if distance(venue, row) <= 800]
                self.assertEqual(rail["count"], len(raw_rail))
                self.assertEqual(bus["count"], len(raw_bus))
                self.assertEqual({row["station_name"] for row in rail["stations"]}, {row["station_name"] for row in raw_rail})
                by_name = {row["station_name"]: row for row in raw_rail}
                for row in rail["stations"]:
                    expected = by_name[row["station_name"]]
                    self.assertEqual(row["lines"], expected["lines"])
                    self.assertAlmostEqual(row["distance_m"], distance(venue, expected), delta=0.051)
                    self.assertIn(row["station_name"], body["answer"])
                routes = {line.strip() for row in raw_bus for line in row["bus_line"].split(";") if line.strip()}
                self.assertEqual(set(bus["lines"]), routes)
                for line in routes:
                    self.assertIn(line, body["answer"])

    def test_nearest_facilities_match_raw_csvs_for_all_venues(self):
        specs = [
            ("fire", "fire_stations", "latitude", "longitude", "station_name"),
            ("police", "police_stations", "latitude", "longitude", "station_name"),
            ("hospital", "hospitals", "LATITUDE", "LONGITUDE", "FACNAME"),
        ]
        for venue in self.venues:
            with self.subTest(venue=venue["venue_name"]):
                body = self.post(f"What emergency services are near {venue['venue_name']}?")
                self.assert_supporting_source(body, [spec[1] for spec in specs], None)
                for result, (topic, source, lat, lon, name) in zip(body["results"], specs):
                    rows = self.raw[source]
                    if topic == "fire":
                        rows = [row for row in rows if row["department"].strip().upper() == "FIRE"]
                    elif topic == "police":
                        rows = [row for row in rows if row["station_type"] in {"municipal_police", "sheriff", "law_enforcement"}]
                    else:
                        rows = [row for row in rows if row["COUNTY_NAME"].strip().upper() == "LOS ANGELES"]
                    nearest = min(rows, key=lambda row: distance(venue, row, lat, lon))
                    self.assertEqual(result["topic"], topic)
                    self.assertEqual(result["facility"]["station_name"], nearest[name])
                    self.assertAlmostEqual(result["facility"]["distance_m"], distance(venue, nearest, lat, lon), delta=0.051)
                    self.assertIn(nearest[name], body["answer"])
                    if topic == "hospital":
                        self.assertEqual(result["emergency_room"], nearest["Emergency Room?"])
                self.assertIn("not restricted to 800 m", body["answer"])
                self.assertIn("straight-line", body["answer"])

    def test_sports_match_the_processed_roster_for_all_venues(self):
        for venue in self.venues:
            with self.subTest(venue=venue["venue_name"]):
                body = self.post(f"Which sports are listed at {venue['venue_name']}?")
                self.assert_supporting_source(body, ["venues"], None)
                expected = [sport.strip() for sport in venue["sports"].split(";") if sport.strip()]
                self.assertEqual(body["results"][0]["sports"], expected)
                self.assertIn("not a confirmed event schedule", body["answer"])

    def test_individual_topics_and_scope(self):
        for question, topic, source, radius in (
            ("What rail stations are near LA Convention Center?", "rail", "rail_stations", 800),
            ("How many bus stops are near Dodger Stadium?", "bus", "bus_stops", 800),
            ("What Metro bus routes are near Dodger Stadium?", "bus", "bus_stops", 800),
            ("What is the nearest fire station to Dodger Stadium?", "fire", "fire_stations", None),
            ("Where's the closest police station to Dodger Stadium?", "police", "police_stations", None),
            ("What police station is nearest to Dodger Stadium?", "police", "police_stations", None),
            ("Does the nearest hospital to Dodger Stadium have an emergency room?", "hospital", "hospitals", None),
        ):
            with self.subTest(question=question):
                body = self.post(question)
                self.assertEqual(body["question_type"], topic)
                self.assert_supporting_source(body, [source], radius)

    def test_zero_transit_is_distinguished_from_missing_data(self):
        body = self.post("What transit is near Griffith Observatory?")
        self.assertEqual([row["count"] for row in body["results"]], [0, 0])
        self.assertIn("No rail station is recorded", body["answer"])
        self.assertIn("No bus stop is recorded", body["answer"])

    def test_hospital_er_flag_no_yes_unknown(self):
        no = self.post("What is the nearest hospital to Dodger Stadium?")
        self.assertEqual(no["results"][0]["emergency_room"], "No")
        self.assertIn("nearest hospital overall", no["answer"])
        yes = self.post("What is the nearest hospital to LA Convention Center?")
        self.assertEqual(yes["results"][0]["emergency_room"], "Yes")
        summary = deepcopy(self.summary)
        del summary["venues"][0]["nearest_hospital"]["emergency_room"]
        with patch("backend.chat.load_summary", return_value=summary):
            body = answer_question("What is the nearest hospital to Dodger Stadium?")
        self.assertEqual(body["results"][0]["emergency_room"], "Unknown / not recorded")

    def test_facility_outside_buffer_is_still_reported_with_true_distance(self):
        body = self.post("What is the nearest fire station to Dodger Stadium?")
        self.assertEqual(body["results"][0]["facility"]["distance_m"], 2003.1)
        self.assertIn("2,003.1 m", body["answer"])
        self.assertIn("not restricted to 800 m", body["answer"])
        self.assertIsNone(body["source"]["radius_m"])

    def test_ambiguous_supporting_question_preserves_topic_and_choices(self):
        body = self.post("What transit is near Venice?", venue_id=self.venues[0]["venue_id"])
        self.assertEqual(body["status"], "clarification")
        self.assertEqual(len(body["choices"]), 2)
        for choice in body["choices"]:
            resolved = self.post(choice["message"])
            self.assertEqual(resolved["question_type"], "transit")
            self.assertEqual(resolved["results"][0]["venue_id"], choice["venue_id"])

    def test_missing_venue_and_followup_context(self):
        missing = self.post("What is the nearest hospital to this venue?")
        self.assertEqual(missing["status"], "clarification")
        chosen = self.post(missing["choices"][0]["message"])
        self.assertEqual(chosen["status"], "answered")
        followup = self.post("Which sports are listed at this venue?", venue_id=self.venues[0]["venue_id"])
        self.assertEqual(followup["results"][0]["sports"], ["Baseball"])

    def test_unsupported_supporting_questions_never_return_facts(self):
        for question in (
            "Compare transit near Dodger Stadium and LA Zoo",
            "Which venue has more bus stops, Dodger Stadium or LA Zoo?",
            "What is the most common bus route near Dodger Stadium?",
            "List bus stops near Dodger Stadium",
            "What are the names of bus stops near Dodger Stadium?",
            "What is the nearest emergency room to Dodger Stadium?",
            "What is the nearest hospital with an emergency room to Dodger Stadium?",
            "Find the nearest hospital that has an ER to Dodger Stadium",
            "What is the nearest hospital without an emergency room to Dodger Stadium?",
            "How many hospitals are near Dodger Stadium?",
            "What bus routes are available near Dodger Stadium today?",
            "What is the bus schedule near Dodger Stadium?",
            "What is the bus fare near Dodger Stadium?",
            "What is the fire response time near Dodger Stadium?",
            "Is the nearest hospital to Dodger Stadium open?",
            "How many hospital beds are near Dodger Stadium?",
            "Which police jurisdiction covers Dodger Stadium?",
            "What is the fastest transit to Dodger Stadium?",
            "Is transit wheelchair accessible near Dodger Stadium?",
            "What is traffic like near Dodger Stadium?",
            "What sports will be held at Dodger Stadium in 2028?",
            "What transit is near Dodger Stadium during 2020–2024?",
            "What is the nearest fire station within 800 m of Dodger Stadium?",
            "Show crime counts and transit near Dodger Stadium",
        ):
            with self.subTest(question=question):
                body = self.post(question)
                self.assertEqual(body["status"], "unsupported")
                self.assertEqual(body["results"], [])
                self.assertIn("cannot answer", body["answer"])

    def test_missing_or_invalid_context_cannot_turn_into_zero_or_an_answer(self):
        cases = [
            ("rail_stations_nearby", None, "rail stations"),
            ("rail_stations_nearby", {"count": 1, "stations": []}, "rail stations"),
            ("rail_stations_nearby", {"count": -1, "stations": []}, "rail stations"),
            ("bus_stops_nearby", {"count": 2}, "bus stops"),
            ("bus_stops_nearby", {"count": 0, "lines": ["33"]}, "bus stops"),
            ("nearest_fire_station", {"station_name": "test", "distance_m": -1}, "nearest fire station"),
        ]
        for field, value, topic in cases:
            with self.subTest(field=field, value=value):
                summary = deepcopy(self.summary)
                summary["venues"][0][field] = value
                with patch("backend.chat.load_summary", return_value=summary):
                    body = answer_question(f"What {topic} is near Dodger Stadium?")
                self.assertEqual(body["status"], "unavailable")
                self.assertEqual(body["results"], [])

    def test_missing_source_is_not_fabricated(self):
        summary = deepcopy(self.summary)
        del summary["meta"]["sources"]["hospitals"]
        with patch("backend.chat.load_summary", return_value=summary):
            body = answer_question("What is the nearest hospital to Dodger Stadium?")
        self.assertEqual(body["status"], "unavailable")
        self.assertEqual(body["results"], [])

    def test_no_facility_record_is_not_zero_distance(self):
        summary = deepcopy(self.summary)
        summary["venues"][0]["nearest_fire_station"] = None
        with patch("backend.chat.load_summary", return_value=summary):
            body = answer_question("What is the nearest fire station to Dodger Stadium?")
        self.assertEqual(body["status"], "answered")
        self.assertIsNone(body["results"][0]["facility"])
        self.assertIn("No nearest fire station is recorded", body["answer"])

    def test_nearest_metro_station_is_the_closest_recorded_stop(self):
        body = self.post("Where is the nearest Metro station to Peacock Theater?")
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["question_type"], "rail")
        stations = body["results"][0]["stations"]
        self.assertEqual(len(stations), 1)
        peacock = next(venue for venue in self.venues if venue["venue_id"] == "V04")
        recorded = peacock["rail_stations_nearby"]["stations"]
        nearest = min(recorded, key=lambda station: station["distance_m"])
        self.assertEqual(stations[0]["station_name"], nearest["station_name"])
        self.assertIn("within 800", body["answer"])
        none_nearby = self.post("Where is the nearest Metro station to Dodger Stadium?")
        self.assertIn("No rail station is recorded within 800", none_nearby["answer"])
        police = self.post("What is the nearest police station to Dodger Stadium?")
        self.assertEqual(police["question_type"], "police")
        self.assertIn(police["results"][0]["facility"]["station_name"], police["answer"])

    def test_supporting_answers_do_not_depend_on_crime_category_counts(self):
        summary = deepcopy(self.summary)
        summary["venues"][0]["crime_by_category"] = {}
        with patch("backend.chat.load_summary", return_value=summary):
            body = answer_question("What transit is near Dodger Stadium?")
        self.assertEqual(body["status"], "answered")


if __name__ == "__main__":
    unittest.main()
