"""The kitchens candidates are hired for, for the dashboard's location filter."""

from fastapi import APIRouter, Depends

from sazon.api.deps import require_recruiter
from sazon.api.schemas import LocationOut
from sazon.engine.service_areas import load_locations
from sazon.engine.text import fold

router = APIRouter(
    prefix="/api/locations", tags=["recruiter"], dependencies=[Depends(require_recruiter)]
)


@router.get("", response_model=list[LocationOut])
async def list_locations() -> list[LocationOut]:
    """Every location, by country and then name."""
    locations = sorted(load_locations().values(), key=lambda loc: (loc.country, fold(loc.name)))
    return [
        LocationOut(
            location_id=loc.location_id,
            name=loc.name,
            city=loc.city,
            country="MX" if loc.country == "MX" else "ES",
        )
        for loc in locations
    ]
