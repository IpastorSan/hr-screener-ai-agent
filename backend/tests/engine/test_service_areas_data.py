import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import sazon.agents

DATA_PATH = Path(sazon.agents.__file__).parent / "riders" / "data" / "service_areas.json"


def load_locations() -> list[dict]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))["locations"]


def test_has_enough_locations_in_both_countries():
    locations = load_locations()

    assert len(locations) >= 40
    assert {loc["country"] for loc in locations} == {"ES", "MX"}


def test_location_ids_are_unique():
    ids = [loc["location_id"] for loc in load_locations()]

    assert len(ids) == len(set(ids))


def test_timezones_are_valid():
    for loc in load_locations():
        ZoneInfo(loc["timezone"])


def test_every_location_has_an_address():
    for loc in load_locations():
        assert loc["address"].strip(), loc["location_id"]


WEB_LOCATIONS = Path(__file__).parents[3] / "web/src/components/careers/locations.ts"


def test_job_postings_list_the_same_locations():
    """The posting pages keep a static copy of the locations for their location picker."""
    web = re.findall(r'id: "([^"]+)", name: "([^"]+)"', WEB_LOCATIONS.read_text(encoding="utf-8"))

    assert web == [(loc["location_id"], loc["name"]) for loc in load_locations()]


WEB_POSTINGS = WEB_LOCATIONS.parent / "postings.ts"


def test_posting_city_counts_match_the_service_areas():
    """'Madrid, Barcelona, Valencia y 11 ciudades más' counts the cities the posting names
    plus the rest of the country's cities."""
    text = WEB_POSTINGS.read_text(encoding="utf-8")
    claims = re.findall(
        r"en ((?:[\w ]+, )+[\w ]+) y (\d+) ciudades más"
        r"|in ((?:[\w ]+, )+[\w ]+) and (\d+) more cities",
        text,
    )
    spain = {loc["city"] for loc in load_locations() if loc["country"] == "ES"}

    assert len(claims) == 2
    for named_es, more_es, named_en, more_en in claims:
        named = (named_es or named_en).split(", ")
        assert set(named) <= spain
        assert len(named) + int(more_es or more_en) == len(spain)


def test_every_mexico_city_zone_names_one_location():
    """A colonia of one alcaldía never matches another location (2026-10-22: Roma Sur, Doctores
    and other Cuauhtémoc colonias were missing and read back as outside the areas)."""
    from sazon.engine.service_areas import match_city

    cdmx = [loc for loc in load_locations() if loc["city"] == "Ciudad de México"]
    ids = {loc["location_id"] for loc in cdmx}
    for loc in cdmx:
        for zone in loc["zones"]:
            matched = {found.location_id for found, score in match_city(zone) if score >= 85}
            assert matched & ids == {loc["location_id"]}, (zone, matched)


@pytest.mark.parametrize(
    ("place", "location_id"),
    [
        ("Roma Sur", "mx-cdmx-cuauh"),
        ("Roma Norte", "mx-cdmx-cuauh"),
        ("Hipódromo", "mx-cdmx-cuauh"),
        ("Doctores", "mx-cdmx-cuauh"),
        ("Obrera", "mx-cdmx-cuauh"),
        ("Santa María la Ribera", "mx-cdmx-cuauh"),
        ("San Rafael", "mx-cdmx-cuauh"),
        ("Tabacalera", "mx-cdmx-cuauh"),
        ("Colonia Guerrero", "mx-cdmx-cuauh"),
        ("Álamos", "mx-cdmx-bju"),
        ("Lomas de Chapultepec", "mx-cdmx-mh"),
        ("Country Club", "mx-cdmx-coy"),
        ("Guadalupe Inn", "mx-cdmx-ao"),
        ("Cerro de la Estrella", "mx-cdmx-izt"),
        ("San Juan de Aragón", "mx-cdmx-gam"),
        ("Héroes de Padierna", "mx-cdmx-tlp"),
    ],
)
def test_main_colonias_of_mexico_city_match_their_alcaldia(place: str, location_id: str):
    from sazon.engine.validators import Valid, validate_location

    assert validate_location(place) == Valid(location_id)
