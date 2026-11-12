import re

from sazon.engine.service_areas import get_location
from sazon.engine.spec import agent
from sazon.engine.state import ConversationContext, ConversationState, CountrySource
from sazon.engine.text import fold
from sazon.engine.validators import Country, phone_country

MEXICAN_TIMEZONES = frozenset(
    {
        "America/Mexico_City",
        "America/Monterrey",
        "America/Merida",
        "America/Cancun",
        "America/Tijuana",
        "America/Hermosillo",
        "America/Chihuahua",
        "America/Ciudad_Juarez",
        "America/Mazatlan",
        "America/Matamoros",
        "America/Bahia_Banderas",
        "America/Ojinaga",
    }
)
SPANISH_TIMEZONES = frozenset({"Europe/Madrid", "Atlantic/Canary", "Africa/Ceuta"})

PROVISIONAL_SOURCES = frozenset(
    {CountrySource.TIMEZONE, CountrySource.LOCALE, CountrySource.DEFAULT}
)
PICKER_OVERRIDES = PROVISIONAL_SOURCES | {CountrySource.PHONE}


def initial_country(context: ConversationContext) -> tuple[Country, CountrySource]:
    """Provisional country for the greeting: posting, then timezone, then locale, then the
    agent's first. Only a country the agent hires in counts."""
    posting = posting_country(context)
    if posting:
        return posting, CountrySource.POSTING
    hires = agent().countries
    locale = (context.locale or "").replace("_", "-").upper()
    hints: tuple[tuple[bool, Country, CountrySource], ...] = (
        (context.timezone in MEXICAN_TIMEZONES, "MX", CountrySource.TIMEZONE),
        (context.timezone in SPANISH_TIMEZONES, "ES", CountrySource.TIMEZONE),
        (locale.endswith("-MX"), "MX", CountrySource.LOCALE),
        (locale.endswith("-ES"), "ES", CountrySource.LOCALE),
    )
    for hinted, country, source in hints:
        if hinted and country in hires:
            return country, source
    return hires[0], CountrySource.DEFAULT


def posting_country(context: ConversationContext) -> Country | None:
    if context.country:
        return context.country if context.country in agent().countries else None
    location = get_location(context.location_id) if context.location_id else None
    if location is None:
        return None
    return "MX" if location.country == "MX" else "ES"


def after_phone(state: ConversationState, phone: str, *, picked: bool) -> ConversationState:
    """A phone number settles the country only when nothing stronger has.

    A prefix picked in the phone widget outranks a number typed as free text.
    """
    country = phone_country(phone)
    if country is None or country not in agent().countries:
        return state
    if picked and state.country_source in PICKER_OVERRIDES:
        return state.model_copy(
            update={"country": country, "country_source": CountrySource.PHONE_PICKER}
        )
    if not picked and state.country_source in PROVISIONAL_SOURCES:
        return state.model_copy(update={"country": country, "country_source": CountrySource.PHONE})
    return state


def after_location(state: ConversationState, location_id: str) -> ConversationState:
    """The matched location's country is final."""
    location = get_location(location_id)
    if location is None:
        return state
    country: Country = "MX" if location.country == "MX" else "ES"
    return state.model_copy(update={"country": country, "country_source": CountrySource.LOCATION})


# A country a question is about, said with a preposition ("¿y en México?", "in Spain", "para
# España"): never where the candidate comes from ("soy de México"). Matched on folded text.
_NAMED_COUNTRY: dict[Country, re.Pattern[str]] = {
    "MX": re.compile(r"\b(?:en|in|para|for|a|to)\s+(?:el\s+)?mexico\b"),
    "ES": re.compile(r"\b(?:en|in|para|for|a|to)\s+(?:espana|spain)\b"),
}


def named_country(question: str, own: Country) -> Country | None:
    """The other country a question explicitly asks about ("¿Y en México?" in a Spanish
    conversation), whose documents answer it; None when it names only its own, or both."""
    folded = fold(question)
    named: list[Country] = [
        country for country, pattern in _NAMED_COUNTRY.items() if pattern.search(folded)
    ]
    if len(named) == 1 and named[0] != own:
        return named[0]
    return None
