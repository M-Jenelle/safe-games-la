"""Shared offense groups for the 2020–2024 reports and the NIBRS extract.

The first matching rule wins. Vehicle is before theft so "theft from a motor
vehicle" stays with vehicle crime. Sexual offenses are before assault.
"""

from __future__ import annotations

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
