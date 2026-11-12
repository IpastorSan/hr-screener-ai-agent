from datetime import date

import pytest

from sazon.agents.riders.readers import (
    Platforms,
    validate_availability,
    validate_platforms,
    validate_schedule,
)
from sazon.engine.models import Availability, Platform, Schedule
from sazon.engine.regions import REGIONS
from sazon.engine.service_areas import load_locations
from sazon.engine.validators import (
    Ambiguous,
    Invalid,
    ReasonCode,
    StartDate,
    Valid,
    license_affirmed,
    phone_country,
    validate_credential,
    validate_date,
    validate_email,
    validate_location,
    validate_person_name,
    validate_phone,
    validate_years,
    years_need_words,
)

TODAY = date(2026, 10, 19)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Lucía Fernández", Valid("Lucía Fernández")),
        ("  jorge   ramírez  ", Valid("jorge ramírez")),
        ("María-José O'Neill", Valid("María-José O'Neill")),
        ("Jo Li", Valid("Jo Li")),
        ("José M. García", Valid("José M. García")),
        ("Carlos", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        ("Carlos S.", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        ("María-José", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        # A compound first name alone is not a full name; with a surname or two it is.
        ("Juan Carlos", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        ("  maria   jose ", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        ("JOSÉ LUIS", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        ("María del Carmen", Invalid(ReasonCode.NAME_NEEDS_SURNAME)),
        ("Juan Carlos Sánchez", Valid("Juan Carlos Sánchez")),
        ("María José Sánchez Pérez", Valid("María José Sánchez Pérez")),
        ("Carlos Juan", Valid("Carlos Juan")),
        ("J", Invalid(ReasonCode.NAME_TOO_SHORT)),
        ("Lucia 2", Invalid(ReasonCode.NAME_HAS_DIGITS)),
        ("</table><script>", Invalid(ReasonCode.NAME_INVALID_CHARS)),
        ("", Invalid(ReasonCode.NAME_TOO_SHORT)),
    ],
)
def test_full_name(raw, expected):
    assert validate_person_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("juan carlos hdz", "juan carlos Hernández"),
        ("Luis Hdez.", "Luis Hernández"),
        ("Ana Fdz Fdez", "Ana Fernández Fernández"),
        ("Rosa Glz", "Rosa González"),
        ("Rosa Glez.", "Rosa González"),
        ("Rosa Gzlz", "Rosa González"),
        ("Rosa Gonz", "Rosa González"),
        ("JOSÉ RDZ", "JOSÉ Rodríguez"),
        ("José Rguez", "José Rodríguez"),
        ("Ana Mtz. Mtnez", "Ana Martínez Martínez"),
        ("Iván Gtz Gtrrz", "Iván Gutiérrez Gutiérrez"),
        ("Luz Rmz", "Luz Ramírez"),
        ("Luz Lpz", "Luz López"),
        ("Eva Pz Sz", "Eva Pérez Sánchez"),
        ("Eva Dz", "Eva Díaz"),
        ("Eva Gcía", "Eva García"),
        ("Eva Gca.", "Eva García"),
        # Surnames of their own, and abbreviations inside a word, stay as written.
        ("Raúl Pérez Sanz", "Raúl Pérez Sanz"),
        ("María Gil Garza", "María Gil Garza"),
        ("Pedro Mtzz", "Pedro Mtzz"),
        ("Hdzson Smith", "Hdzson Smith"),
    ],
)
def test_surname_abbreviations_are_expanded(raw, expected):
    assert validate_person_name(raw) == Valid(expected)


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [
        ("612 345 678", "ES", Valid("+34612345678")),
        ("+34 612-345-678", "MX", Valid("+34612345678")),
        ("0034 712 345 678", "ES", Valid("+34712345678")),
        ("912 345 678", "ES", Invalid(ReasonCode.PHONE_NOT_MOBILE)),
        ("902 123 456", "ES", Invalid(ReasonCode.PHONE_NOT_MOBILE)),
        ("+52 55 1234 5678", "ES", Valid("+525512345678")),
        ("55 1234 5678", "MX", Valid("+525512345678")),
        ("(33) 1234-5678", "MX", Valid("+523312345678")),
        ("+52 1 55 1234 5678", "MX", Valid("+525512345678")),
        ("55 1234 5678", "ES", Valid("+525512345678")),
        ("612 345 678", "MX", Valid("+34612345678")),
        ("912 345 678", "MX", Invalid(ReasonCode.PHONE_INVALID)),
        ("12345", "ES", Invalid(ReasonCode.PHONE_INVALID)),
        ("no tengo", "ES", Invalid(ReasonCode.PHONE_INVALID)),
    ],
)
def test_phone(raw, country, expected):
    assert validate_phone(raw, country=country) == expected


def test_phone_without_fallback_reads_only_the_given_country():
    assert validate_phone("55 1234 5678", country="ES", fallback=False) == Invalid(
        ReasonCode.PHONE_INVALID
    )
    assert validate_phone("55 1234 5678", country="MX", fallback=False) == Valid("+525512345678")


@pytest.mark.parametrize(
    ("e164", "expected"),
    [("+34612345678", "ES"), ("+525512345678", "MX"), ("+447700900123", None)],
)
def test_phone_country(e164, expected):
    assert phone_country(e164) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Lucia.Fernandez@Gmail.com", Valid("lucia.fernandez@gmail.com")),
        (" jorge+jobs@empresa.com.mx ", Valid("jorge+jobs@empresa.com.mx")),
        ("lucia@gmail", Invalid(ReasonCode.EMAIL_INVALID)),
        ("lucia gmail.com", Invalid(ReasonCode.EMAIL_INVALID)),
        ("lucia@@gmail.com", Invalid(ReasonCode.EMAIL_INVALID)),
        ("lucia..f@gmail.com", Invalid(ReasonCode.EMAIL_INVALID)),
        ("lucía@gmail.com", Invalid(ReasonCode.EMAIL_INVALID)),
    ],
)
def test_email(raw, expected):
    assert validate_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, Valid(True)),
        (False, Valid(False)),
        ("in_progress", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("Sí", Valid(True)),
        ("sí, B y A2", Valid(True)),
        ("licencia tipo A", Valid(True)),
        ("tengo el B desde hace años", Valid(True)),
        ("yes", Valid(True)),
        ("yes I do", Valid(True)),
        ("no", Valid(False)),
        ("no tengo", Valid(False)),
        ("todavía no", Valid(False)),
        ("I don't have one", Valid(False)),
        ("lo estoy sacando", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("No, me lo estoy sacando", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("en trámite", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("in progress", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("not_valid", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        # Strict: only a license valid when applying counts (SAZ-41).
        ("tengo permiso de aprendiz", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("sí, permiso de aprendiz", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("I have a learner's permit", Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)),
        ("la tengo vencida", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("sí, pero está caducado", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("la estoy renovando", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("me la quitaron", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("tengo carnet pero me lo suspendieron", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("me quitaron los puntos", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("yes, but it expired in June", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("it's suspended", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("sí, y no está vencida", Valid(True)),
        ("No está vencida", Valid(True)),  # screening-rules: "no está vencida" stays a yes
        ("no está caducado", Valid(True)),
        ("it's not expired", Valid(True)),
        ("No, no tengo", Valid(False)),
        # A refusal's "no" does not deny the problem in the next clause.
        ("No, está vencida", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("No, la tengo caducada", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("no, caducado", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("No. Está suspendida", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("estoy sin carnet, vencido", Valid(False, note=ReasonCode.LICENSE_NOT_VALID)),
        ("tengo licencia vigente", Valid(True)),
        ("maybe", Invalid(ReasonCode.LICENSE_INVALID)),
        ("no sé", Invalid(ReasonCode.LICENSE_INVALID)),
        ("I don't know", Invalid(ReasonCode.LICENSE_INVALID)),
        ("creo que sí", Invalid(ReasonCode.LICENSE_INVALID)),
        (3, Invalid(ReasonCode.LICENSE_INVALID)),
    ],
)
def test_has_license(raw, expected):
    assert validate_credential(raw) == expected


SPANGLISH_YES = [
    "tengo driver license vigente",
    "Tengo license",
    "driver license",
    "driver's license",
    "licencia de driver",
    "I have a license",
    "yes license",
    "sí, tengo driver license",
    "mi driver license está vigente",
    "tengo la license de driver",
]
SPANGLISH_NO = [
    "no license",
    "no tengo license",
    "No, ya no tengo license",
    "nel, no tengo license",
]


@pytest.mark.parametrize("raw", SPANGLISH_YES)
def test_spanglish_license_words_read_as_yes(raw):
    assert validate_credential(raw) == Valid(True)


@pytest.mark.parametrize("raw", SPANGLISH_NO)
def test_spanglish_no_license_words_read_as_no(raw):
    assert validate_credential(raw) == Valid(False)


@pytest.mark.parametrize(
    "raw",
    [
        "tengo driver license vigente",
        "I have a license",
        "yes license",
        "sí, tengo driver license",
        "mi driver license está vigente",
        "tengo licencia de manejo",
        "Sí",
    ],
)
def test_words_that_claim_a_valid_license_are_affirmed(raw):
    assert license_affirmed(raw)


@pytest.mark.parametrize(
    "raw",
    [
        *SPANGLISH_NO,
        "No está vencida",  # owner decision: read as no, it stays a no
        "sí, y no está vencida",
        "tengo driver license pero está expired",
        "tengo permiso de aprendiz",
        "creo que sí tengo license",
        "my license is not valid",
        "licencia",  # a bare noun claims nothing
        "driver",
    ],
)
def test_words_that_do_not_clearly_claim_a_license_are_not_affirmed(raw):
    assert not license_affirmed(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Málaga", Valid("es-mlg-centro")),
        ("malaga", Valid("es-mlg-centro")),
        ("MALAGA", Valid("es-mlg-centro")),
        ("Gràcia", Valid("es-bcn-gracia")),
        ("Zapopan", Valid("mx-gdl-zapopan")),
        ("Zapopna", Valid("mx-gdl-zapopan")),
        ("Monterey", Valid("mx-mty-centro")),
        ("Sevilla", Valid("es-sev-centro")),
        ("Seville", Valid("es-sev-centro")),
        ("Polanco", Valid("mx-cdmx-mh")),
        ("Madrid Centro", Valid("es-mad-centro")),
        ("Bilbo", Valid("es-bio-centro")),
        ("Malagaa", Valid("es-mlg-centro")),
        ("Sazón Madrid Norte", Valid("es-mad-norte")),
        ("Madird Centro", Valid("es-mad-centro")),
        ("Roma, CDMX", Valid("mx-cdmx-cuauh")),
        ("Ciudad de México Roma", Valid("mx-cdmx-cuauh")),
        ("Condesa DF", Valid("mx-cdmx-cuauh")),
        ("Gràcia, Barcelona", Valid("es-bcn-gracia")),
        ("Toledo", Invalid(ReasonCode.OUT_OF_AREA)),
        ("Ourense", Invalid(ReasonCode.OUT_OF_AREA)),
        # Sharing "de Juárez" with Naucalpan de Juárez is not a match.
        ("Oaxaca de Juárez", Invalid(ReasonCode.OUT_OF_AREA)),
        ("Naucalpn de Juárez", Valid("mx-mex-naucalpan")),
        ("", Invalid(ReasonCode.OUT_OF_AREA)),
    ],
)
def test_city(raw, expected):
    assert validate_location(raw) == expected


@pytest.mark.parametrize(
    ("raw", "options"),
    [
        ("GDL", {"mx-gdl-centro", "mx-gdl-zapopan", "mx-gdl-tlaquepaque"}),
        ("MTY", {"mx-mty-centro", "mx-mty-sanpedro", "mx-mty-guadalupe"}),
        ("Barcelona", {"es-bcn-eixample", "es-bcn-gracia", "es-bcn-sants"}),
        ("Triana", {"es-sev-centro", "es-lpa-centro"}),
        # An exact match and a near spelling ("Linda Vista") within the margin: ask which.
        ("Lindavista", {"mx-cdmx-gam", "mx-mty-guadalupe"}),
    ],
)
def test_city_ambiguous(raw, options):
    result = validate_location(raw)

    assert isinstance(result, Ambiguous)
    assert result.reason is ReasonCode.CITY_AMBIGUOUS
    assert set(result.options) == options


@pytest.mark.parametrize("raw", ["DF", "CDMX", "Mexico City", "Ciudad de México"])
def test_cdmx_asks_for_alcaldia(raw):
    result = validate_location(raw)

    assert isinstance(result, Ambiguous)
    assert len(result.options) == 8
    assert all(option.startswith("mx-cdmx-") for option in result.options)


@pytest.mark.parametrize(
    ("raw", "location_id"),
    [("Sapopa", "mx-gdl-zapopan"), ("Cancum", "mx-cun-centro"), ("Santander", "mx-cdmx-ao")],
)
def test_city_fuzzy_middle_band_needs_confirmation(raw, location_id):
    """One loose match is asked about once, never as a one-option clarification (SAZ-42)."""
    assert validate_location(raw) == Valid(location_id, needs_confirmation=True)


@pytest.mark.parametrize(
    "raw", ["México, DF", "En México, DF.", "México DF", "Ciudad de México, DF", "DF, México"]
)
def test_the_city_with_the_country_is_every_location_in_it(raw):
    result = validate_location(raw, country="MX")
    assert isinstance(result, Ambiguous)
    assert len(result.options) == 8
    assert all(option.startswith("mx-cdmx-") for option in result.options)


@pytest.mark.parametrize(
    "raw",
    [
        "Benito Juárez",
        "Mmm, Benito Juárez.",
        "México, DF, Benito Juárez",
        "CDMX, Benito Juárez",
        "Benito Juárez, México",
        "alcaldía Benito Juárez",
        "colonia Narvarte",
        "Narvarte (CDMX)",
        "DF - Benito Juárez",
    ],
)
def test_a_zone_inside_a_longer_answer_is_valid(raw):
    assert validate_location(raw) == Valid("mx-cdmx-bju")


@pytest.mark.parametrize(
    "raw",
    [
        "Narvarte, Polanco",
        "Narvarte; Polanco",
        "Narvarte / Polanco",
        "Narvarte - Polanco",
        "Narvarte y Polanco",
        "entre Narvarte y Polanco",
        "Narvarte o Polanco",
    ],
)
def test_two_zones_named_are_ambiguous_between_them(raw):
    assert validate_location(raw) == Ambiguous(
        ("mx-cdmx-bju", "mx-cdmx-mh"), ReasonCode.CITY_AMBIGUOUS
    )


def test_a_region_named_with_a_conjunction_stays_one_part():
    """ "Castilla y León" is one region, not "Castilla" and "León"."""
    assert validate_location("Valladolid, Castilla y León", country="ES") == Valid("es-vll-centro")
    assert validate_location("Madrid, Castilla y León", country="ES") == Invalid(
        ReasonCode.OUT_OF_AREA
    )


@pytest.mark.parametrize("raw", ["México DF - Narvarte", "Mmm (alcaldía Benito Juárez) México"])
def test_a_zone_set_off_by_a_dash_or_brackets_is_read(raw):
    assert validate_location(raw) == Valid("mx-cdmx-bju")


def test_a_zone_outside_the_city_named_is_not_picked():
    """ "DF, Gràcia": the zone is in another city, so the candidate is asked."""
    result = validate_location("DF, Gràcia")
    assert isinstance(result, Ambiguous)
    assert "es-bcn-gracia" in result.options
    assert "mx-cdmx-bju" in result.options


@pytest.mark.parametrize("raw", ["México", "España", "mmm, en"])
def test_parts_with_no_place_are_not_read(raw):
    result = validate_location(raw)
    assert not isinstance(result, Valid)


def test_a_weak_match_far_behind_the_top_is_dropped():
    # Its best match (Tepeyac, next door in Gustavo A. Madero) is loose: read back, never taken.
    assert validate_location("Ecatepec") == Valid("mx-cdmx-gam", needs_confirmation=True)


@pytest.mark.parametrize("country", ["ES", "MX"])
def test_city_respects_country(country):
    result = validate_location("Centro", country=country)

    assert isinstance(result, Ambiguous)
    assert len(result.options) > 1
    assert all(option.startswith(country.lower() + "-") for option in result.options)


def test_city_in_the_other_country_is_out_of_area():
    assert validate_location("Malaga", country="MX") == Invalid(ReasonCode.OUT_OF_AREA)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("full_time", [Availability.FULL_TIME]),
        (["part_time", "weekends"], [Availability.PART_TIME, Availability.WEEKENDS]),
        ("tiempo completo", [Availability.FULL_TIME]),
        ("Full-time", [Availability.FULL_TIME]),
        ("medio tiempo", [Availability.PART_TIME]),
        ("media jornada", [Availability.PART_TIME]),
        ("fines de semana", [Availability.WEEKENDS]),
        ("weekends", [Availability.WEEKENDS]),
        ("sábados y domingos", [Availability.WEEKENDS]),
        (
            "puedo full time pero solo weekends",
            [Availability.FULL_TIME, Availability.WEEKENDS],
        ),
        ("part time y fines de semana", [Availability.PART_TIME, Availability.WEEKENDS]),
    ],
)
def test_availability(raw, expected):
    assert validate_availability(raw) == Valid(expected)


@pytest.mark.parametrize("raw", ["cuando quieras", "", 5])
def test_availability_invalid(raw):
    assert validate_availability(raw) == Invalid(ReasonCode.AVAILABILITY_INVALID)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("morning", Valid([Schedule.MORNING])),
        ("en la mañana", Valid([Schedule.MORNING])),
        ("por las mañanas", Valid([Schedule.MORNING])),
        ("por la tarde", Valid([Schedule.AFTERNOON])),
        ("afternoons", Valid([Schedule.AFTERNOON])),
        ("noches", Valid([Schedule.EVENING])),
        ("at night", Valid([Schedule.EVENING])),
        ("cualquiera", Valid([Schedule.FLEXIBLE])),
        ("me da igual", Valid([Schedule.FLEXIBLE])),
        ("any time is fine", Valid([Schedule.FLEXIBLE])),
        ("mañana o tarde", Valid([Schedule.MORNING, Schedule.AFTERNOON])),
        ("mañanas y tardes, noches no", Valid([Schedule.MORNING, Schedule.AFTERNOON])),
        ("tardes y mañanas", Valid([Schedule.MORNING, Schedule.AFTERNOON])),
        ("no puedo por las noches, solo mañanas", Valid([Schedule.MORNING])),
        ("las mañanas no, mejor tardes", Valid([Schedule.AFTERNOON])),
        ("morning or afternoon, not evenings", Valid([Schedule.MORNING, Schedule.AFTERNOON])),
        ("cualquiera menos noches", Valid([Schedule.MORNING, Schedule.AFTERNOON])),
        ("flexible, mañanas también", Valid([Schedule.FLEXIBLE])),
        (["morning", "evening"], Valid([Schedule.MORNING, Schedule.EVENING])),
        (["flexible", "morning"], Valid([Schedule.FLEXIBLE])),
        ("noches no", Invalid(ReasonCode.SCHEDULE_INVALID)),
        ("los martes", Invalid(ReasonCode.SCHEDULE_INVALID)),
        (3, Invalid(ReasonCode.SCHEDULE_INVALID)),
    ],
)
def test_schedule(raw, expected):
    assert validate_schedule(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hice Glovo", Platforms([Platform.GLOVO], None)),
        (["Uber Eats", "Rappi"], Platforms([Platform.UBER_EATS, Platform.RAPPI], None)),
        ("ubereats y just eat", Platforms([Platform.UBER_EATS, Platform.JUST_EAT], None)),
        ("DiDi Food, Deliveroo", Platforms([Platform.DIDI_FOOD, Platform.DELIVEROO], None)),
        ("Amazon Flex", Platforms([Platform.AMAZON_FLEX], None)),
        ("Glovo y Stuart", Platforms([Platform.GLOVO, Platform.OTHER], "Stuart")),
        ("Cornershop", Platforms([Platform.OTHER], "Cornershop")),
        ("ninguna", Platforms([], None)),
        ([], Platforms([], None)),
        # E2E 2026-10-22: the kind of work is not a platform name
        (["delivery", "Uber Eats", "Rappi"], Platforms([Platform.UBER_EATS, Platform.RAPPI], None)),
        (
            "hice delivery con Uber Eats y Rappi",
            Platforms([Platform.UBER_EATS, Platform.RAPPI], None),
        ),
        ("hice delivery", Platforms([], None)),
        (["reparto", "repartos", "apps"], Platforms([], None)),
        ("apps de delivery", Platforms([], None)),
        ("delivery con Stuart", Platforms([Platform.OTHER], "delivery con Stuart")),
        ("Stuart y otras apps", Platforms([Platform.OTHER], "Stuart, otras apps")),
    ],
)
def test_platforms(raw, expected):
    assert validate_platforms(raw) == Valid(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2, 2.0),
        (1.333333, 1.33),
        ("2", 2.0),
        ("unos 2", 2.0),
        ("2 años", 2.0),
        ("two years", 2.0),
        ("medio año", 0.5),
        ("half a year", 0.5),
        ("3 meses", 0.25),
        ("six months", 0.5),
        ("18 meses", 1.5),
        ("un año y medio", 1.5),
        ("1 año y 6 meses", 1.5),
        ("1,5 años", 1.5),
        ("un par de años en Glovo", 2.0),
        ("ninguna", 0.0),
        ("no experience", 0.0),
        ("año y medio", 1.5),
        ("a year and a half", 1.5),
        ("3 años y 6 meses", 3.5),
        ("2 años en Glovo y 3 en Uber", 5.0),
        ("2 semanas", 0.04),
        ("3 weeks", 0.06),
        ("15 días", 0.04),
        ("once años", 11.0),
    ],
)
def test_experience(raw, expected):
    assert validate_years(raw) == Valid(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "unos meses",
        "a few months",
        "algunos años",
        # A range or a calendar year is not one amount.
        "entre 2 y 3 años",
        "between 2 and 3 years",
        "4-5 años",
        "2 o 3 años",
        "de 2 a 3 años",
        "desde 2019",
    ],
)
def test_experience_vague_asks_for_a_number(raw):
    assert validate_years(raw) == Ambiguous((), ReasonCode.EXPERIENCE_NEEDS_NUMBER)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (21, Invalid(ReasonCode.EXPERIENCE_OUT_OF_RANGE)),
        (45, Invalid(ReasonCode.EXPERIENCE_OUT_OF_RANGE)),
        (-1, Invalid(ReasonCode.EXPERIENCE_OUT_OF_RANGE)),
        ("50 años", Invalid(ReasonCode.EXPERIENCE_OUT_OF_RANGE)),
        ("bastante", Invalid(ReasonCode.EXPERIENCE_INVALID)),
        ("I did it once", Invalid(ReasonCode.EXPERIENCE_INVALID)),
        (True, Invalid(ReasonCode.EXPERIENCE_INVALID)),
    ],
)
def test_experience_invalid(raw, expected):
    assert validate_years(raw) == expected


def test_twenty_years_is_the_longest_experience_accepted():
    assert validate_years(20) == Valid(20.0)


@pytest.mark.parametrize(
    ("raw", "confirmed", "expected"),
    [
        ("2026-10-19", False, Valid(StartDate(date(2026, 10, 19), None))),
        (date(2026, 10, 26), False, Valid(StartDate(date(2026, 10, 26), None))),
        ("2026-12-18", False, Valid(StartDate(date(2026, 12, 18), None))),
        (
            "2026-12-19",
            False,
            Valid(StartDate(date(2026, 12, 19), 61), note=ReasonCode.LATE_START),
        ),
        (
            "2027-01-17",
            False,
            Valid(StartDate(date(2027, 1, 17), 90), note=ReasonCode.LATE_START),
        ),
        (
            "2027-01-18",
            False,
            Valid(
                StartDate(date(2027, 1, 18), 91),
                note=ReasonCode.LATE_START,
                needs_confirmation=True,
            ),
        ),
        (
            "2027-01-18",
            True,
            Valid(StartDate(date(2027, 1, 18), 91), note=ReasonCode.LATE_START),
        ),
        ("2026-10-18", False, Invalid(ReasonCode.PAST_DATE)),
        ("next monday", False, Invalid(ReasonCode.DATE_INVALID)),
        ("2026-02-30", False, Invalid(ReasonCode.DATE_INVALID)),
    ],
)
def test_start_date(raw, confirmed, expected):
    assert validate_date(raw, today=TODAY, confirmed=confirmed) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # QA sweep 2026-10-21: a state the matched location is not in.
        "Guadalupe, Zacatecas",
        "Guadalupe, Zac.",
        "Zapopan, Nuevo León",
        "Triana, Galicia",
    ],
)
def test_a_part_naming_another_region_is_out_of_area(raw):
    assert validate_location(raw) == Invalid(ReasonCode.OUT_OF_AREA)


@pytest.mark.parametrize(
    ("raw", "location_id"),
    [
        ("Guadalupe, Nuevo León", "mx-mty-guadalupe"),
        ("Guadalupe, NL", "mx-mty-guadalupe"),
        ("Zapopan, Jalisco", "mx-gdl-zapopan"),
        ("León, Guanajuato", "mx-leo-centro"),
        ("Triana, Sevilla", "es-sev-centro"),
        ("Gràcia, Catalunya", "es-bcn-gracia"),
        ("Bilbao, Bizkaia", "es-bio-centro"),
        ("Triana, Canarias", "es-lpa-centro"),
    ],
)
def test_a_part_naming_the_location_s_own_region_is_kept(raw, location_id):
    assert validate_location(raw) == Valid(location_id)


def test_a_town_weakly_like_a_city_with_several_locations_is_out_of_area():
    """QA sweep 2026-10-21: "Badalona" tied the Barcelona locations at 71 and got the barrio
    question; it is a town we don't serve, read back as outside the areas."""
    assert validate_location("Badalona") == Invalid(ReasonCode.OUT_OF_AREA)


@pytest.mark.parametrize(
    ("raw", "city"), [("Madriz", "es-mad"), ("Mexico", "mx-cdmx"), ("Barna", "es-bcn")]
)
def test_the_city_itself_written_loosely_is_still_asked_its_zone(raw, city):
    result = validate_location(raw)
    assert isinstance(result, Ambiguous)
    assert all(option.startswith(city) for option in result.options)


def test_every_location_has_a_region():
    for location in load_locations().values():
        assert location.region in REGIONS, location.location_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # QA sweep 2026-10-21: not knowing or not saying is never zero years.
        ("No sé, la verdad", Invalid(ReasonCode.EXPERIENCE_INVALID)),
        ("no me acuerdo", Invalid(ReasonCode.EXPERIENCE_INVALID)),
        ("prefiero no decirlo", Invalid(ReasonCode.EXPERIENCE_INVALID)),
        ("I don't know", Invalid(ReasonCode.EXPERIENCE_INVALID)),
        ("no sé, unos 2 años", Valid(2.0)),
        # An amount taken back is left out.
        ("dos años no, tres", Valid(3.0)),
        ("tengo 2 años, no 3", Valid(2.0)),
        ("No, ninguna", Valid(0.0)),
        ("No", Valid(0.0)),
        ("1,5 años", Valid(1.5)),
        ("Desde 2019 más o menos", Ambiguous((), ReasonCode.EXPERIENCE_NEEDS_NUMBER)),
    ],
)
def test_experience_words(raw, expected):
    assert validate_years(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Desde 2019 más o menos", True),
        ("since 2020", True),
        ("Entre 2 y 3 años", True),
        ("No sé, la verdad", True),
        ("dos años no, tres", True),
        ("2 años", False),
        ("Llevo 2 años, puedo empezar en enero de 2027", False),
        ("ninguna", False),
    ],
)
def test_experience_needs_words(text, expected):
    assert years_need_words(text) is expected


@pytest.mark.parametrize(
    ("raw", "corrected"),
    [
        ("LUCIA.FERNANDEZ@GMAIL.CON", "lucia.fernandez@gmail.com"),
        ("lucia@gmial.com", "lucia@gmail.com"),
        ("lucia@hotmial.com", "lucia@hotmail.com"),
        ("lucia@hotmail.con", "lucia@hotmail.com"),
        ("lucia@hotmial.es", "lucia@hotmail.es"),
        ("lucia@hotmail.con.mx", "lucia@hotmail.com.mx"),
        ("lucia@outlok.com", "lucia@outlook.com"),
        ("lucia@yahoo.con", "lucia@yahoo.com"),
        ("lucia@gmail.co", "lucia@gmail.com"),
    ],
)
def test_a_mistyped_email_domain_is_read_back_corrected(raw, corrected):
    """D2: "¿Quisiste decir ...@gmail.com?" before it is kept."""
    assert validate_email(raw) == Valid(corrected, needs_confirmation=True)


@pytest.mark.parametrize(
    "raw", ["lucia@gmail.com", "lucia@empresa.co", "lucia@hotmail.co.uk", "lucia@con.es"]
)
def test_a_well_spelled_email_is_kept_as_given(raw):
    assert validate_email(raw) == Valid(raw)
