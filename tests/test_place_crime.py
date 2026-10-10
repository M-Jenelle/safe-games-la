"""Crime around a pin, without treating a station as a venue."""

from __future__ import annotations

import unittest

import numpy as np

from backend.agent_tools import explain_page
from backend.place_crime import count_near, crime_around


class PlaceCrimeTests(unittest.TestCase):
    def test_count_near_keeps_the_cell_inside_the_circle(self):
        arrays = (
            np.array([34.05, 34.20], dtype=np.float64),
            np.array([-118.25, -118.50], dtype=np.float64),
            np.array([10, 7], dtype=np.int64),
        )
        self.assertEqual(count_near(34.05, -118.25, arrays), 10)

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
