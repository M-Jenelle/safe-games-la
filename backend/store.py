"""Public data access for the API and the chatbot.

The sections are ``datasets`` (file cache), ``venues`` (one briefing),
``heatmap`` (citywide crime), and ``layers`` (map pins).
"""

from backend.datasets import DatasetNotFound, get_crime_points, load_summary
from backend.heatmap import crime_heat_points
from backend.layers import map_layers, map_payload, meta
from backend.venues import get_venue, home_game_comparison, list_venues, permit_comparison, weather_comparison

__all__ = [
    "DatasetNotFound",
    "crime_heat_points",
    "get_crime_points",
    "get_venue",
    "home_game_comparison",
    "list_venues",
    "load_summary",
    "map_layers",
    "map_payload",
    "meta",
    "permit_comparison",
    "weather_comparison",
]
