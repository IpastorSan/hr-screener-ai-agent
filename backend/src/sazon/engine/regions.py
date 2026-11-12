"""States, autonomous communities and provinces, to tell "Guadalupe, Zacatecas" from the
Guadalupe location in Nuevo León (QA sweep 2026-10-21).

A place's parts are matched to locations on their own; a part that names a region (and no
location) must agree with the region of the location the rest matched, or the answer is a
place outside the service areas, read back to the candidate.
"""

from functools import cache

from sazon.engine.service_areas import get_location
from sazon.engine.text import fold

# Each region by the names a candidate writes, abbreviations included, unaccented and
# lowercase as `fold` leaves them. "México" alone is left out: it is the country, and Mexico
# City to many.
REGIONS: dict[str, tuple[str, ...]] = {
    # Mexico: the 32 states.
    "aguascalientes": ("aguascalientes", "ags"),
    "baja california": ("baja california", "bc"),
    "baja california sur": ("baja california sur", "bcs"),
    "campeche": ("campeche", "camp"),
    "chiapas": ("chiapas", "chis"),
    "chihuahua": ("chihuahua", "chih"),
    "ciudad de mexico": ("ciudad de mexico", "cdmx", "df", "distrito federal"),
    "coahuila": ("coahuila", "coah", "coahuila de zaragoza"),
    "colima": ("colima",),
    "durango": ("durango", "dgo"),
    "estado de mexico": ("estado de mexico", "edomex", "edo mex", "edo de mexico"),
    "guanajuato": ("guanajuato", "gto"),
    "guerrero": ("guerrero", "gro"),
    "hidalgo": ("hidalgo", "hgo"),
    "jalisco": ("jalisco", "jal"),
    "michoacan": ("michoacan", "mich"),
    "morelos": ("morelos",),
    "nayarit": ("nayarit", "nay"),
    "nuevo leon": ("nuevo leon", "nl", "n l"),
    "oaxaca": ("oaxaca", "oax"),
    "puebla": ("puebla", "pue"),
    "queretaro": ("queretaro", "qro"),
    "quintana roo": ("quintana roo", "q roo", "qroo"),
    "san luis potosi": ("san luis potosi", "slp"),
    "sinaloa": ("sinaloa",),
    "sonora": ("sonora",),
    "tabasco": ("tabasco",),
    "tamaulipas": ("tamaulipas", "tamps"),
    "tlaxcala": ("tlaxcala", "tlax"),
    "veracruz": ("veracruz",),
    "yucatan": ("yucatan", "yuc"),
    "zacatecas": ("zacatecas", "zac"),
    # Spain: the autonomous communities and the provinces.
    "andalucia": (
        "andalucia",
        "almeria",
        "cadiz",
        "cordoba",
        "granada",
        "huelva",
        "jaen",
        "malaga",
        "sevilla",
    ),
    "aragon": ("aragon", "huesca", "teruel", "zaragoza"),
    "asturias": ("asturias", "principado de asturias"),
    "baleares": ("baleares", "illes balears", "islas baleares", "mallorca", "menorca", "ibiza"),
    "canarias": ("canarias", "islas canarias", "las palmas", "santa cruz de tenerife", "tenerife"),
    "cantabria": ("cantabria",),
    "castilla la mancha": (
        "castilla la mancha",
        "albacete",
        "ciudad real",
        "cuenca",
        "guadalajara",
        "toledo",
    ),
    "castilla y leon": (
        "castilla y leon",
        "avila",
        "burgos",
        "leon",
        "palencia",
        "salamanca",
        "segovia",
        "soria",
        "valladolid",
        "zamora",
    ),
    "cataluna": ("cataluna", "catalunya", "barcelona", "girona", "gerona", "lleida", "tarragona"),
    "comunidad valenciana": (
        "comunidad valenciana",
        "comunitat valenciana",
        "alicante",
        "alacant",
        "castellon",
        "valencia",
    ),
    "extremadura": ("extremadura", "badajoz", "caceres"),
    "galicia": ("galicia", "a coruna", "la coruna", "lugo", "ourense", "orense", "pontevedra"),
    "la rioja": ("la rioja", "rioja"),
    "madrid": ("madrid", "comunidad de madrid"),
    "murcia": ("murcia", "region de murcia"),
    "navarra": ("navarra", "nafarroa"),
    "pais vasco": ("pais vasco", "euskadi", "alava", "araba", "bizkaia", "vizcaya", "gipuzkoa"),
}


@cache
def _names() -> dict[str, frozenset[str]]:
    """Each name a region goes by, to every region it may stand for."""
    names: dict[str, set[str]] = {}
    for region, aliases in REGIONS.items():
        for alias in aliases:
            names.setdefault(alias, set()).add(region)
    return {name: frozenset(regions) for name, regions in names.items()}


def regions_named(part: str) -> frozenset[str]:
    """The regions a part of an answer names ("Zacatecas", "NL", "Jalisco"), if any."""
    return _names().get(fold(part), frozenset())


def region_of(location_id: str) -> str | None:
    location = get_location(location_id)
    return location.region if location is not None else None
