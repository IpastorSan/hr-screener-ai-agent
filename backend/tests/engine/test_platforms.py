from datetime import date

import pytest

from sazon.agents.riders.platforms import implausible_platform, max_years
from sazon.engine.models import Platform

TODAY = date(2026, 10, 20)


@pytest.mark.parametrize(
    ("platform", "country", "years"),
    [
        (Platform.GLOVO, "ES", 12),
        (Platform.JUST_EAT, "ES", 17),
        (Platform.DELIVEROO, "ES", 7),
        (Platform.AMAZON_FLEX, "ES", 5),
        (Platform.RAPPI, "MX", 12),
        (Platform.UBER_EATS, "MX", 11),
        (Platform.DIDI_FOOD, "MX", 8),
        (Platform.AMAZON_FLEX, "MX", 4),
        (Platform.RAPPI, "ES", None),
        (Platform.OTHER, "MX", None),
    ],
)
def test_most_years_on_a_platform(platform, country, years):
    assert max_years(platform, country, TODAY) == years


@pytest.mark.parametrize(
    ("years", "platforms", "country", "expected"),
    [
        (20, [Platform.RAPPI], "MX", Platform.RAPPI),
        (12, [Platform.RAPPI], "MX", None),
        (12.5, [Platform.RAPPI], "MX", Platform.RAPPI),
        (12, [Platform.JUST_EAT], "ES", None),
        (8, [Platform.DELIVEROO], "ES", Platform.DELIVEROO),
        (13, [Platform.GLOVO, Platform.JUST_EAT], "ES", None),
        (18, [Platform.GLOVO, Platform.JUST_EAT], "ES", Platform.JUST_EAT),
        (20, [Platform.RAPPI], "ES", None),
        (20, [Platform.GLOVO, Platform.RAPPI], "ES", None),
        (20, [Platform.OTHER], "ES", None),
        (20, [], "ES", None),
        (20, None, "ES", None),
    ],
)
def test_implausible_platform(years, platforms, country, expected):
    assert implausible_platform(years, platforms, country, TODAY) == expected
