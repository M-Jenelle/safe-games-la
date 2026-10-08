"""Weather rows join onto the daily crime table without dropping later days."""

from __future__ import annotations

import unittest

from datetime import date

from pipeline.weather import blend_crime_days, join_weather, wet_day, weather_rows
from pipeline.weather_compare import comparison_payload


class WeatherTests(unittest.TestCase):
    def test_wet_day_follows_precipitation_or_a_rain_code(self):
        self.assertEqual(wet_day("0.200", "0"), "1")
        self.assertEqual(wet_day("0.000", "61"), "1")
        self.assertEqual(wet_day("0.000", "0"), "0")
        self.assertEqual(wet_day("", ""), "")

    def test_archive_days_become_one_row_per_date(self):
        rows = weather_rows(
            {"venue_id": "V01", "venue_name": "Dodger Stadium", "latitude": 34.0739, "longitude": -118.24},
            {
                "daily": {
                    "time": ["2020-01-01", "2020-01-02"],
                    "temperature_2m_mean": [55.14, None],
                    "temperature_2m_max": [62.0, 63.2],
                    "temperature_2m_min": [48.0, 49.1],
                    "precipitation_sum": [0.0, 0.24],
                    "rain_sum": [0.0, 0.24],
                    "snowfall_sum": [0.0, 0.0],
                    "wind_speed_10m_max": [8.0, 12.5],
                    "weather_code": [1, 61],
                }
            },
        )
        self.assertEqual(rows[0]["date"], "2020-01-01")
        self.assertEqual(rows[0]["temp_f_mean"], "55.1")
        self.assertEqual(rows[0]["wet_day"], "0")
        self.assertEqual(rows[1]["temp_f_mean"], "")
        self.assertEqual(rows[1]["wet_day"], "1")

    def test_join_keeps_weather_after_the_crime_table_ends(self):
        weather = [
            {
                "venue_id": "V01",
                "venue_name": "Dodger Stadium",
                "latitude": "34.0739",
                "longitude": "-118.2400",
                "date": "2024-12-31",
                "temp_f_mean": "60.0",
                "temp_f_max": "68.0",
                "temp_f_min": "52.0",
                "precip_in": "0.000",
                "rain_in": "0.000",
                "snow_in": "0.000",
                "wind_mph_max": "7.0",
                "weather_code": "1",
                "wet_day": "0",
            },
            {
                "venue_id": "V01",
                "venue_name": "Dodger Stadium",
                "latitude": "34.0739",
                "longitude": "-118.2400",
                "date": "2026-01-01",
                "temp_f_mean": "58.0",
                "temp_f_max": "66.0",
                "temp_f_min": "50.0",
                "precip_in": "0.100",
                "rain_in": "0.100",
                "snow_in": "0.000",
                "wind_mph_max": "9.0",
                "weather_code": "61",
                "wet_day": "1",
            },
        ]
        crime = {
            ("V01", "2024-12-31"): {
                "venue_id": "V01",
                "venue_name": "Dodger Stadium",
                "incident_count": "4",
                "is_permit_event_day": "0",
                "permit_count": "0",
            }
        }
        joined = join_weather(weather, crime)
        self.assertEqual([row["date"] for row in joined], ["2024-12-31", "2026-01-01"])
        self.assertEqual(joined[0]["incident_count"], "4")
        self.assertEqual(joined[1]["incident_count"], "")
        self.assertEqual(joined[1]["wet_day"], "1")

    def test_blend_keeps_reports_before_the_nibrs_start(self):
        reports = {
            ("V01", "2024-03-06"): {
                "venue_id": "V01",
                "venue_name": "Dodger Stadium",
                "incident_count": "2",
                "is_permit_event_day": "0",
                "permit_count": "0",
            },
            ("V01", "2024-03-07"): {
                "venue_id": "V01",
                "venue_name": "Dodger Stadium",
                "incident_count": "9",
                "is_permit_event_day": "1",
                "permit_count": "2",
            },
        }
        venues = [{"venue_id": "V01", "venue_name": "Dodger Stadium"}]
        blended = blend_crime_days(reports, {("V01", "2024-03-07"): 4}, venues, date(2024, 3, 8))
        self.assertEqual(blended[("V01", "2024-03-06")]["incident_count"], "2")
        self.assertEqual(blended[("V01", "2024-03-06")]["count_source"], "report")
        self.assertEqual(blended[("V01", "2024-03-07")]["incident_count"], "4")
        self.assertEqual(blended[("V01", "2024-03-07")]["count_source"], "nibrs")
        self.assertEqual(blended[("V01", "2024-03-07")]["permit_count"], "2")
        self.assertEqual(blended[("V01", "2024-03-08")]["incident_count"], "0")
        self.assertEqual(blended[("V01", "2024-03-08")]["permit_count"], "")
        self.assertNotIn(("V01", "2024-03-09"), blended)

    def test_same_month_comparison_leaves_out_a_one_sided_month(self):
        rows = [
            {"date": "2020-01-02", "count_source": "report", "incident_count": 10, "wet_day": True, "temp_f_mean": 55.0},
            {"date": "2020-01-03", "count_source": "report", "incident_count": 10, "wet_day": True, "temp_f_mean": 54.0},
            {"date": "2020-01-04", "count_source": "report", "incident_count": 1, "wet_day": False, "temp_f_mean": 60.0},
            {"date": "2020-01-05", "count_source": "report", "incident_count": 1, "wet_day": False, "temp_f_mean": 61.0},
            {"date": "2020-07-01", "count_source": "report", "incident_count": 100, "wet_day": False, "temp_f_mean": 80.0},
            {"date": "2020-07-02", "count_source": "report", "incident_count": 100, "wet_day": False, "temp_f_mean": 81.0},
        ]
        wet = comparison_payload(rows)["comparisons"][0]["rows"][0]
        self.assertTrue(wet["available"])
        self.assertEqual(wet["month_count"], 1)
        self.assertEqual(wet["event_day_mean"], 10)
        self.assertEqual(wet["other_day_mean"], 1)
        self.assertFalse(wet["percent_shown"])
        hot = comparison_payload(rows)["comparisons"][1]["rows"][0]
        self.assertFalse(hot["available"])
        self.assertIn("both kinds", hot["note"])


if __name__ == "__main__":
    unittest.main()
