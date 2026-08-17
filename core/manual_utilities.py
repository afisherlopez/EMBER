"""Small manually mapped utility records used until authoritative geometry is available."""

from __future__ import annotations

DENVER_WATER_ID = "denver-water"
DENVER_WATER_NAME = "Denver Water"
DENVER_WATER_STATE = "CO"
DENVER_WATER_LONGITUDE = -104.9903
DENVER_WATER_LATITUDE = 39.7392
DENVER_WATER_SERVICE_POINT = {
    "type": "Feature",
    "geometry": {
        "type": "Point",
        "coordinates": [DENVER_WATER_LONGITUDE, DENVER_WATER_LATITUDE],
    },
    "properties": {
        "utility_id": DENVER_WATER_ID,
        "name": DENVER_WATER_NAME,
        "state": DENVER_WATER_STATE,
        "area_type": "service",
        "geometry_basis": "approximate service location",
    },
}
