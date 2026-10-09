"""Shared offense groups for the 2020–2024 reports and the NIBRS extract.

Report descriptions use the first matching words. Vehicle is before theft so
"theft from a motor vehicle" stays with vehicle crime. Sexual offenses are
before assault.

NIBRS descriptions end in an offense code. Those columns use the code, not
the older wording. Larceny stays theft, including theft from a motor vehicle.
Motor-vehicle theft is code 240.
"""

from __future__ import annotations

import re

CRIME_GROUPS = (
    ("sexual", "Sexual offenses", ("RAPE", "SEXUAL", "LEWD", "INDECENT", "FONDLING")),
    ("homicide", "Homicide", ("HOMICIDE", "MANSLAUGHTER", "MURDER")),
    ("robbery", "Robbery", ("ROBBERY",)),
    ("assault", "Assault", ("ASSAULT", "BATTERY")),
    ("weapons", "Weapons", ("WEAPON", "FIREARM", "SHOTS FIRED")),
    ("vehicle", "Vehicle", ("VEHICLE", "MOTOR VEHICLE", "BIKE - STOLEN")),
    ("burglary", "Burglary", ("BURGLARY",)),
    ("theft", "Theft", ("THEFT", "SHOPLIFT", "LARCENY", "PICKPOCKET", "BUNCO", "STOLEN")),
    ("vandalism", "Vandalism", ("VANDALISM", "DESTRUCTION")),
)

GROUP_LABELS = {group_id: label for group_id, label, _needles in CRIME_GROUPS}
GROUP_LABELS["other"] = "Other"


def crime_group(description: str | None) -> str:
    text = (description or "").upper()
    for group_id, _label, needles in CRIME_GROUPS:
        if any(needle in text for needle in needles):
            return group_id
    return "other"


_NIBRS_CODE = re.compile(r"([0-9]{2,3}[A-Z]?)\s*$")
_NIBRS_GROUPS = {
    "09A": "homicide",
    "09B": "homicide",
    "11A": "sexual",
    "11B": "sexual",
    "11C": "sexual",
    "11D": "sexual",
    "36A": "sexual",
    "36B": "sexual",
    "120": "robbery",
    "13A": "assault",
    "13B": "assault",
    "520": "weapons",
    "240": "vehicle",
    "220": "burglary",
    "23A": "theft",
    "23B": "theft",
    "23C": "theft",
    "23D": "theft",
    "23E": "theft",
    "23F": "theft",
    "23G": "theft",
    "23H": "theft",
    "290": "vandalism",
}


def nibrs_group(description: str | None) -> str:
    """Group one NIBRS offense by its code. Wording is used only when no code is present."""
    match = _NIBRS_CODE.search((description or "").strip().upper())
    if match is None:
        return crime_group(description)
    return _NIBRS_GROUPS.get(match.group(1), "other")
