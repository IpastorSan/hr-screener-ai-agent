from enum import StrEnum


class FieldName(StrEnum):
    """The riders' fields, in the order they are asked. `city` is stored as `location_id`."""

    FULL_NAME = "full_name"
    PHONE = "phone"
    EMAIL = "email"
    HAS_LICENSE = "has_license"
    CITY = "city"
    AVAILABILITY = "availability"
    SCHEDULE = "schedule"
    EXPERIENCE_YEARS = "experience_years"
    PLATFORMS = "platforms"
    START_DATE = "start_date"


class Step(StrEnum):
    """The riders' funnel steps, the `funnel_step` of their fields in `agent.yaml`."""

    NAME = "name"
    CONTACT = "contact"
    LICENSE = "license"
    CITY = "city"
    AVAILABILITY = "availability"
    SCHEDULE = "schedule"
    EXPERIENCE = "experience"
    START_DATE = "start_date"


class Close(StrEnum):
    """The riders' closes, by the reasons `agent.yaml` gives its gates and required fields."""

    NO_LICENSE = "no_license"
    LICENSE_IN_PROGRESS = "license_in_progress"
    """Still getting it, or a learner's permit."""
    LICENSE_NOT_VALID = "license_not_valid"
    """Expired, being renewed, suspended or withdrawn: not valid when applying."""
    OUT_OF_AREA = "out_of_area"
    UNRESOLVED_NAME = "unresolved_name"
    """No first name and surname after the last try."""
    UNRESOLVED_LICENSE = "unresolved_license"
    UNRESOLVED_CITY = "unresolved_city"
