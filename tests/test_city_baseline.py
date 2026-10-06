"""Citywide rate math. The incident count itself comes from the LAPD extract."""

from __future__ import annotations

import unittest

from pipeline.city_baseline import (
    CITY_LAND_SQ_MI,
    city_land_area_km2,
    city_rate,
    compared_with_city,
)


class CityBaselineTests(unittest.TestCase):
    def test_land_area_uses_the_census_square_miles(self):
        self.assertEqual(CITY_LAND_SQ_MI, 469.49)
        self.assertAlmostEqual(city_land_area_km2(), 1215.97, places=2)

    def test_rate_is_reports_over_city_land(self):
        count = 1_002_654
        expected = round(count / city_land_area_km2(), 1)
        self.assertEqual(city_rate(count), expected)

    def test_dense_circle_is_above_the_city(self):
        result = compared_with_city(7047.6, 824.6)
        self.assertEqual(result["relation"], "above")
        self.assertGreater(result["ratio"], 1)
        self.assertIn("Above the city", result["summary"])
        self.assertIn("824.6", result["summary"])
        self.assertEqual(result["value"], "8.5×")
        self.assertIn("2020–2024", result["caption"])

    def test_quiet_circle_is_below_the_city(self):
        result = compared_with_city(40.3, 824.6)
        self.assertEqual(result["relation"], "below")
        self.assertLess(result["ratio"], 1)
        self.assertIn("Below the city", result["summary"])
        self.assertIn("% of the citywide rate", result["summary"])

    def test_near_match_is_not_called_above_or_below(self):
        result = compared_with_city(824.6, 824.6)
        self.assertEqual(result["relation"], "near")
        self.assertIn("About the citywide rate", result["summary"])


if __name__ == "__main__":
    unittest.main()
