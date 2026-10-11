"""Crime around a pin, without treating a station as a venue."""

from __future__ import annotations

import unittest

import numpy as np

from backend.agent_tools import explain_page
from backend.place_crime import count_near, count_near_any, crime_around, station_lines


class PlaceCrimeTests(unittest.TestCase):
    def test_count_near_keeps_the_cell_inside_the_circle(self):
        arrays = (
            np.array([34.05, 34.20], dtype=np.float64),
            np.array([-118.25, -118.50], dtype=np.float64),
            np.array([10, 7], dtype=np.int64),
        )
        self.assertEqual(count_near(34.05, -118.25, arrays), 10)

    def test_a_cell_near_two_stations_on_one_line_is_counted_once(self):
        arrays = (
            np.array([34.05], dtype=np.float64),
            np.array([-118.25], dtype=np.float64),
            np.array([10], dtype=np.int64),
        )
        stations = [
            {"latitude": 34.050, "longitude": -118.250},
            {"latitude": 34.051, "longitude": -118.250},
        ]
        self.assertEqual(count_near_any(stations, arrays), 10)
        self.assertEqual(station_lines("A;E"), ["A", "E"])

    def test_narcotics_is_not_a_group(self):
        result = crime_around(kind="rail", offense="narcotics")
        self.assertIn("Narcotics", result["error"])

    def test_hours_for_a_venue_list_the_four_parts_of_day(self):
        result = explain_page("Dodger Stadium", "hours")
        labels = [entry["label"] for entry in result["entries"]]
        self.assertEqual(labels, ["12am–6am", "6am–12pm", "12pm–6pm", "6pm–12am"])
        self.assertTrue(any(entry["value"] for entry in result["entries"]))

    def test_rail_rank_is_a_station_not_a_venue_total(self):
        result = crime_around(kind="rail")
        highest = result["highest"]
        self.assertGreater(result["place_count"], 10)
        self.assertNotIn("Theater", highest["name"])
        self.assertGreater(highest["records"], 1000)
        self.assertIn("citywide", highest["city_comparison"])

    def test_a_metro_letter_counts_the_stations_on_that_line(self):
        station = crime_around(kind="rail", name="Willowbrook")
        line_a = crime_around(line="A")
        ranked = crime_around(line="all")
        self.assertGreaterEqual(line_a["place"]["records"], station["highest"]["records"])
        self.assertGreater(line_a["place"]["station_count"], 5)
        self.assertIn("counted once", line_a["what_it_counts"])
        self.assertIn("citywide", line_a["place"]["city_comparison"])
        letters = [row["line"] for row in ranked["ranked"]]
        records = [row["records"] for row in ranked["ranked"]]
        self.assertIn("A", letters)
        self.assertIn("E", letters)
        self.assertEqual(records, sorted(records, reverse=True))
        missing = crime_around(line="Z")
        self.assertIn("No Metro line Z", missing["error"])
