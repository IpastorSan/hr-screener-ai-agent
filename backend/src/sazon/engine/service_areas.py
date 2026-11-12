"""The places an agent hires for (its `locations` file: the riders' kitchens, the teachers'
campuses), and the fuzzy match of a place a candidate names to them."""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache, cached_property
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from sazon.engine.text import fold

MIN_SCORE = 70.0


@dataclass(frozen=True)
class Manager:
    """The location's manager, who holds its in-person interviews (SAZ-64)."""

    name: str
    email: str


@dataclass(frozen=True)
class Location:
    """A kitchen. In-person interviews there are with its `manager`, inside `interview_hours`
    (weekday key to `[["10:00", "14:00"], ...]`, local time), at most `interviews_per_slot` at
    once and `max_interviews_per_day` a day; the country's defaults unless the location sets
    its own."""

    location_id: str
    name: str
    city: str
    country: str
    timezone: str
    zones: tuple[str, ...]
    aliases: tuple[str, ...]
    address: str = ""
    region: str | None = None
    """The state, community or province it is in (`regions.REGIONS`)."""
    manager: Manager | None = None
    interview_hours: Mapping[str, Sequence[Sequence[str]]] = field(
        default_factory=dict, hash=False, compare=False
    )
    interviews_per_slot: int = 1
    max_interviews_per_day: int = 15


def parse_location(raw: Mapping[str, Any], defaults: Mapping[str, Any]) -> Location:
    """One entry of `service_areas.json`, its country's interview defaults filled in."""
    own = {**defaults.get(raw["country"], {}), **raw}
    manager = raw.get("manager")
    return Location(
        location_id=raw["location_id"],
        name=raw["name"],
        city=raw["city"],
        country=raw["country"],
        timezone=raw["timezone"],
        zones=tuple(raw["zones"]),
        aliases=tuple(raw["aliases"]),
        address=raw.get("address", ""),
        region=raw.get("region"),
        manager=Manager(manager["name"], manager["email"].strip().lower()) if manager else None,
        interview_hours={
            day: [list(block) for block in blocks]
            for day, blocks in (own.get("interview_hours") or {}).items()
        },
        interviews_per_slot=int(own.get("interviews_per_slot", 1)),
        max_interviews_per_day=int(own.get("max_interviews_per_day", 15)),
    )


@dataclass(frozen=True)
class _Terms:
    own: frozenset[str]  # name, city, zones and aliases
    areas: frozenset[str]  # city and aliases, which candidates write around a zone: "Roma, CDMX"
    compound: frozenset[str]  # "<city> <zone>", which still matches a misspelled city


@dataclass(frozen=True)
class Places:
    """An agent's locations file: its locations by id, and the brand their names start with
    ("Sazón Madrid Centro"), which is never how Ana names one."""

    locations: Mapping[str, Location]
    brand: str = ""

    @cached_property
    def prefix(self) -> str:
        return fold(self.brand) + " " if self.brand else ""

    @cached_property
    def terms(self) -> dict[str, _Terms]:
        """What each location is matched on, by its id."""
        terms: dict[str, _Terms] = {}
        for loc in self.locations.values():
            city = fold(loc.city)
            zones = {fold(zone) for zone in loc.zones}
            areas = {city, *map(fold, loc.aliases)} - {""}
            own = {fold(loc.name).removeprefix(self.prefix), *zones, *areas} - {""}
            compound = {f"{city} {zone}" for zone in zones if zone}
            terms[loc.location_id] = _Terms(frozenset(own), frozenset(areas), frozenset(compound))
        return terms


@cache
def _read(path: str | None) -> Places:
    if path is None:
        return Places({})
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    defaults = data.get("interview_defaults", {})
    locations = {loc["location_id"]: parse_location(loc, defaults) for loc in data["locations"]}
    return Places(locations, data.get("brand", ""))


def places() -> Places:
    """The locations of the agent screened for (`spec.AgentSpec.locations`)."""
    from sazon.engine.spec import agent  # the spec's validators match places here

    return _read(agent().locations)


def load_locations() -> Mapping[str, Location]:
    return places().locations


def _place_around(query: str, areas: frozenset[str]) -> str | None:
    """What is left of "<area> <place>" or "<place> <area>", if the query has that shape."""
    for area in sorted(areas, key=len, reverse=True):
        if query.startswith(area + " "):
            return query.removeprefix(area + " ")
        if query.endswith(" " + area):
            return query.removesuffix(" " + area)
    return None


def _ratio(query: str, term: str) -> float:
    """Fuzzy similarity, where shared words prove nothing on their own: "Oaxaca de Juárez" is
    not "Naucalpan de Juárez". When both have words the other lacks, those must still match
    each other. A query that is part of the term ("México", "Mexico City") is left alone."""
    query_words, term_words = query.split(), term.split()
    shared = set(query_words) & set(term_words)
    rest_query = " ".join(w for w in query_words if w not in shared)
    rest_term = " ".join(w for w in term_words if w not in shared)
    if shared and rest_query and rest_term and fuzz.ratio(rest_query, rest_term) < MIN_SCORE:
        return 0.0
    return fuzz.ratio(query, term)


def get_location(location_id: str) -> Location | None:
    return load_locations().get(location_id)


def branch_name(location_id: str) -> str:
    """How Ana names a location to a candidate: its name without the brand, and without a
    leading city abbreviation that is one of its aliases ("Sazón CDMX Benito Juárez" ->
    "Benito Juárez", "Sazón Madrid Centro" -> "Madrid Centro"). Replies say "la sucursal de
    Benito Juárez"; the internal name is for recruiters."""
    location = get_location(location_id)
    if location is None:
        return location_id
    brand = places().brand
    name = location.name.removeprefix(f"{brand} ") if brand else location.name
    first, _, rest = name.partition(" ")
    return rest if rest and first in location.aliases else name


def shared_city(location_ids: Iterable[str]) -> str | None:
    """The city all these locations are in, or None when they span several (or none)."""
    cities = {location.city for location in map(get_location, location_ids) if location}
    return cities.pop() if len(cities) == 1 else None


def match_city(text: str, country: str | None = None) -> list[tuple[Location, float]]:
    """Best fuzzy score per location, highest first, for scores of at least MIN_SCORE."""
    known = places()
    query = fold(text).removeprefix(known.prefix)
    if not query:
        return []
    locations = known.locations
    best: dict[str, float] = {}
    for location_id, terms in known.terms.items():
        if country and locations[location_id].country != country:
            continue
        place = _place_around(query, terms.areas)
        pool = terms.own if place else terms.own | terms.compound
        score = max(_ratio(query, term) for term in pool)
        if place:
            score = max(score, *(_ratio(place, term) for term in terms.own))
        if score >= MIN_SCORE:
            best[location_id] = score
    ranked = sorted(best.items(), key=lambda item: (-item[1], item[0]))
    return [(locations[location_id], score) for location_id, score in ranked]
