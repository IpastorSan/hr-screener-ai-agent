"""The rider agent's Python, by the names `agent.yaml` gives it: the `years` check and the
`multi_enum` readers."""

from datetime import date

from sazon.agents.riders.platforms import implausible_platform
from sazon.agents.riders.readers import validate_availability, validate_platforms, validate_schedule
from sazon.engine.models import CandidateProfile, Platform
from sazon.engine.validators import Country


def platform_years(
    profile: CandidateProfile, years: float, country: Country, today: date
) -> Platform | None:
    """More years than a platform the candidate named has existed in the country (`platforms`)."""
    return implausible_platform(years, profile.platforms, country, today)


# The `multi_enum` readers, by the field each reads.
availability = validate_availability
schedule = validate_schedule
platforms = validate_platforms
