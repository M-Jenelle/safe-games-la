"""The map grid built in the second DuckDB, without the city extracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.heat_warehouse import build_grids


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class HeatWarehouseTests(unittest.TestCase):
    def test_lapd_grid_keeps_the_series_break_and_the_buffer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lapd = root / "crime.csv"
            _write(
                lapd,
                "\n".join(
                    [
                        "LAT,LON,DATE OCC,Crm Cd Desc",
                        '34.0500,-118.2500,01/15/2024 12:00:00 AM,THEFT OF IDENTITY',
                        '34.0500,-118.2500,02/01/2024 01:00:00 PM,THEFT OF IDENTITY',
                        '34.0500,-118.2500,03/07/2024 12:00:00 AM,THEFT OF IDENTITY',
                        '34.0600,-118.2500,01/15/2024 12:00:00 AM,BIKE - STOLEN',
                        '10.0000,10.0000,01/15/2024 12:00:00 AM,THEFT OF IDENTITY',
                        '34.0500,-118.2500,,BURGLARY',
                        '34.0550,-118.2500,01/20/2024 12:00:00 AM,VANDALISM',
                    ]
                )
                + "\n",
            )
            venues = [
                {"latitude": 34.05, "longitude": -118.25},
                {"latitude": 34.05, "longitude": -118.25},
            ]
            report, nibrs = build_grids(lapd, None, venues, 800)
        self.assertEqual(int(report["all"].sum()), 5)
        self.assertEqual(int(report["venues"].sum()), 4)
        self.assertEqual(int(report["types"]["theft"].sum()), 2)
        self.assertEqual(int(report["types"]["vehicle"].sum()), 1)
        self.assertEqual(int(report["types"]["burglary"].sum()), 1)
        self.assertEqual(int(report["types"]["vandalism"].sum()), 1)
        self.assertNotIn("other", report["types"])
        self.assertEqual(report["months"], ["2024-01", "2024-02"])
        self.assertEqual(int(report["monthly"]["all"].sum()), 4)
        self.assertEqual(int(report["monthly"]["venues"].sum()), 3)
        self.assertEqual(nibrs["months"], [])

    def test_nibrs_grid_uses_keywords_and_rounded_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lapd = root / "crime.csv"
            nibrs_path = root / "nibrs.csv"
            _write(
                lapd,
                "LAT,LON,DATE OCC,Crm Cd Desc\n"
                "34.0500,-118.2500,01/01/2020 12:00:00 AM,THEFT\n",
            )
            _write(
                nibrs_path,
                "\n".join(
                    [
                        "date_occ,nibr_description,hndrdth_lat,hndrdth_lon",
                        "2024-03-07T00:00:00.000,331  - THEFT FROM MOTOR VEHICLE - GRAND,34.0500,-118.2500",
                        "2024-03-06T00:00:00.000,THEFT,34.0500,-118.2500",
                        "2024-04-01T12:00:00.000,310C - BURGLARY - COMMERCIAL,34.2000,-118.5000",
                        "2024-05-01T00:00:00.000,,34.05001,-118.2500",
                    ]
                )
                + "\n",
            )
            report, nibrs = build_grids(
                lapd,
                nibrs_path,
                [{"latitude": 34.05, "longitude": -118.25}],
                800,
            )
        self.assertEqual(int(report["all"].sum()), 1)
        self.assertEqual(nibrs["months"], ["2024-03", "2024-04", "2024-05"])
        self.assertEqual(int(nibrs["counts"].sum()), 3)
        self.assertEqual(int(nibrs["venues"].sum()), 2)
        self.assertEqual(int(nibrs["types"]["vehicle"].sum()), 1)
        self.assertEqual(int(nibrs["types"]["burglary"].sum()), 1)
        self.assertNotIn("theft", nibrs["types"])
