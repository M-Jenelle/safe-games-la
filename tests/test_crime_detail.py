"""Raw-report slices: hour, premise, weapon, wording, drugs, area, and lag."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.crime_detail import crime_detail
from backend.heat_warehouse import build_grids


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class CrimeDetailTests(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        root = Path(self._folder.name)
        lapd = root / "crime.csv"
        nibrs = root / "nibrs.csv"
        _write(
            lapd,
            "\n".join([
                "DR_NO,Date Rptd,DATE OCC,TIME OCC,AREA NAME,Rpt Dist No,Crm Cd Desc,Premis Desc,Weapon Desc,LAT,LON",
                "1,01/03/2024 12:00:00 AM,01/01/2024 12:00:00 AM,2300,CENTRAL,100,THEFT,STREET,HANDGUN,34.0500,-118.2500",
                "2,01/04/2024 12:00:00 AM,01/02/2024 12:00:00 AM,1200,CENTRAL,100,BATTERY POLICE (SIMPLE),STREET,,34.0500,-118.2500",
                "3,01/04/2024 12:00:00 AM,01/02/2024 12:00:00 AM,100,HOLLYWOOD,200,\"BUNCO, GRAND THEFT\",PARKING LOT,,34.2000,-118.5000",
                "4,01/04/2024 12:00:00 AM,01/02/2024 12:00:00 AM,900,CENTRAL,100,PICKPOCKET,MTA BUS,,34.0500,-118.2500",
                "5,01/04/2024 12:00:00 AM,01/02/2024 12:00:00 AM,1400,CENTRAL,100,ASSAULT WITH DEADLY WEAPON ON POLICE OFFICER,FIRE STATION,,34.0500,-118.2500",
            ]) + "\n",
        )
        _write(
            nibrs,
            "\n".join([
                "date_occ,nibr_description,hndrdth_lat,hndrdth_lon",
                "2024-04-01T02:15:00.000,POSSESS NARCOTIC - 35A,34.0500,-118.2500",
                "2024-04-01T14:00:00.000,POSSESS PARAPHERNALIA - 35B,34.2000,-118.5000",
                "2024-04-02T00:00:00.000,THEFT,34.2000,-118.5000",
                "2024-03-06T02:00:00.000,POSSESS NARCOTIC - 35A,34.0500,-118.2500",
            ]) + "\n",
        )
        _report, _nibrs, self.details = build_grids(lapd, nibrs, [], 800)

    def tearDown(self):
        self._folder.cleanup()

    def _ask(self, **kwargs):
        return crime_detail(details=self.details, **kwargs)

    def test_hours_stay_in_two_series(self):
        city = self._ask(topic="hours")
        lapd = {row["period"]: row["records"] for row in city["lapd"]["periods"]}
        nibrs = {row["period"]: row["records"] for row in city["nibrs"]["periods"]}
        self.assertEqual(lapd, {"night": 1, "morning": 1, "afternoon": 2, "evening": 1})
        self.assertEqual(city["lapd"]["stamped_1200"], 1)
        self.assertEqual(nibrs["night"], 2)
        self.assertEqual(nibrs["afternoon"], 1)
        self.assertEqual(city["nibrs"]["stamped_midnight"], 1)
        near = self._ask(topic="hours", latitude=34.05, longitude=-118.25)
        near_lapd = {row["period"]: row["records"] for row in near["lapd"]["periods"]}
        self.assertEqual(near_lapd["night"], 0)
        self.assertEqual(near_lapd["evening"], 1)
        self.assertEqual(near["nibrs"]["records"], 1)

    def test_premise_weapon_wording_area_and_lag(self):
        premise = self._ask(topic="premise", latitude=34.05, longitude=-118.25)
        buckets = {row["bucket"]: row["records"] for row in premise["buckets"]}
        self.assertEqual(buckets["street"], 2)
        self.assertEqual(buckets["bus"], 1)
        self.assertEqual(buckets["fire_station"], 1)
        weapon = self._ask(topic="weapon")
        self.assertEqual(weapon["weapon_recorded"], 1)
        self.assertEqual(weapon["reports"], 5)
        officer = self._ask(topic="officer")
        counts = {row["id"]: row["records"] for row in officer["rows"]}
        self.assertEqual(counts["officer_battery"], 1)
        self.assertEqual(counts["officer_adw"], 1)
        self.assertEqual(self._ask(topic="bunco")["rows"][0]["records"], 1)
        self.assertEqual(self._ask(topic="pickpocket")["rows"][0]["records"], 1)
        self.assertEqual(self._ask(topic="area")["areas"][0]["name"], "CENTRAL")
        lag = self._ask(topic="lag")
        self.assertEqual(lag["median_days"], 2.0)
        self.assertEqual(lag["buckets"][0]["name"], "1_to_7_days")
        self.assertEqual(lag["buckets"][0]["records"], 5)

    def test_drug_codes_are_separate_from_the_nine_groups(self):
        drugs = self._ask(topic="drugs")
        codes = {row["name"]: row["records"] for row in drugs["codes"]}
        self.assertEqual(codes, {"35A": 1, "35B": 1})
        near = self._ask(topic="drugs", latitude=34.05, longitude=-118.25)
        self.assertEqual(near["records"], 1)
        night = self._ask(topic="drugs", period="night")
        self.assertEqual(night["records"], 1)
