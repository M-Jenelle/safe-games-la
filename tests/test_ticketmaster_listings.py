"""Ticketmaster calendar: future listings, with empty months kept."""

from __future__ import annotations

import unittest

import pandas as pd

from pipeline.ticketmaster_listings import (
    busiest_sentence,
    clock_sentence,
    clock_value,
    listings_from_frame,
    shares_name,
)


VENUES = [
    {
        "venue_id": "V01",
        "venue_name": "Dodger Stadium",
        "latitude": 34.0739,
        "longitude": -118.24,
        "buffer_radius_m": 800,
    },
    {
        "venue_id": "V03",
        "venue_name": "LA Convention Center (Halls 1-3)",
        "latitude": 34.0397,
        "longitude": -118.2703,
        "buffer_radius_m": 800,
    },
    {
        "venue_id": "V04",
        "venue_name": "Peacock Theater",
        "latitude": 34.0445,
        "longitude": -118.2669,
        "buffer_radius_m": 800,
    },
]


class TicketmasterListingTests(unittest.TestCase):
    def test_clock_and_name_match(self):
        self.assertEqual(clock_value("20:00:00"), "20:00")
        self.assertEqual(clock_value("19:30:00"), "19:30")
        self.assertIsNone(clock_value(None))
        self.assertIsNone(clock_value(float("nan")))
        self.assertTrue(shares_name("Dodger Stadium", "UNIQLO Field at Dodger Stadium"))
        self.assertFalse(shares_name("LA Convention Center (Halls 1-3)", "Peacock Theater - LA"))

    def test_empty_months_and_peacock_note(self):
        frame = pd.DataFrame([
            {
                "event_id": "a",
                "event_name": "Los Horoscopos de Durango",
                "event_test": False,
                "venue_id": "V03",
                "ticketmaster_venue_name": "Peacock Theater - LA",
                "latitude": 34.0397,
                "longitude": -118.266403,
                "start_local_date": "2026-10-09",
                "start_local_time": "20:00:00",
                "status_code": "onsale",
            },
            {
                "event_id": "b",
                "event_name": "Cece Winans",
                "event_test": False,
                "venue_id": "V03",
                "ticketmaster_venue_name": "Peacock Theater - LA",
                "latitude": 34.0397,
                "longitude": -118.266403,
                "start_local_date": "2026-12-17",
                "start_local_time": None,
                "status_code": "offsale",
            },
            {
                "event_id": "skip",
                "event_name": "Test show",
                "event_test": True,
                "venue_id": "V03",
                "ticketmaster_venue_name": "Peacock Theater - LA",
                "latitude": 34.0397,
                "longitude": -118.266403,
                "start_local_date": "2026-11-01",
                "start_local_time": "20:00:00",
                "status_code": "onsale",
            },
        ])
        block = listings_from_frame(frame, VENUES)["venues"]["V03"]
        self.assertEqual(block["event_count"], 2)
        self.assertEqual([month["count"] for month in block["months"]], [1, 0, 1])
        self.assertEqual(block["months"][1]["label"], "November 2026")
        self.assertIn("inside the 800 m circle", block["note"])
        self.assertIn("Peacock Theater is", block["note"])
        self.assertTrue(block["events"][0]["on_sale"])
        self.assertFalse(block["events"][1]["on_sale"])
        self.assertIsNone(block["events"][1]["start_time"])
        self.assertNotIn("V04", listings_from_frame(frame, VENUES)["venues"])
        self.assertIn("no report count", block["disclaimer"])

    def test_sentences(self):
        months = [
            {"label": "May 2027", "count": 15},
            {"label": "June 2027", "count": 12},
            {"label": "August 2027", "count": 15},
        ]
        self.assertEqual(
            busiest_sentence(months),
            "May 2027 and August 2027 have the most listings, 15 each.",
        )
        self.assertEqual(clock_sentence(83, 1), "1 listing has a start time. The other 82 do not.")
        self.assertEqual(clock_sentence(13, 13), "All 13 listings have a start time.")
