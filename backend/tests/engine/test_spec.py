"""The agent spec: what a valid one is, the rider spec as the first, and the engine reading
any spec by its field types, here a small school agent stored in the riders' profile."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from riders import SPEC
from sazon.agents.riders import Close, FieldName, Step
from sazon.agents.riders.readers import validate_availability
from sazon.engine import (
    AskField,
    Clarify,
    ConfirmSummary,
    ConfirmValue,
    ConversationState,
    Disqualify,
    DisqualifyReason,
    awaits_talent_pool_answer,
    start,
    step,
)
from sazon.engine.models import (
    Availability,
    CandidateProfile,
    Intent,
    Language,
    MatrixLanguage,
    Platform,
    QualityFlag,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.spec import (
    AgentSpec,
    Channel,
    Column,
    Condition,
    DashboardSpec,
    FieldSpec,
    FieldType,
    Gate,
    GateOutcome,
    Hooks,
    ReadBack,
    agent,
    use,
)
from sazon.engine.texts import SpecError, check_texts
from sazon.engine.ui import (
    MultiSelect,
    PhoneEntry,
    QuickReplies,
    field_options,
    field_ui,
    summary_value,
)
from sazon.engine.validators import Country

TODAY = date(2026, 10, 19)
TYPO = ReadBack(domain_typo=True)


# --- the rider spec ---


def test_the_rider_spec_is_the_default_and_asks_in_the_field_order():
    assert agent() is SPEC
    assert SPEC.ids == tuple(FieldName)


def test_the_rider_gates_are_applied_first():
    applied = [field.id for field in SPEC.applied]
    assert applied[:2] == [FieldName.HAS_LICENSE, FieldName.CITY]
    assert sorted(applied) == sorted(FieldName)


def test_the_rider_gates_close_with_their_reasons():
    closing = SPEC.closing(Close.LICENSE_NOT_VALID)
    assert closing is not None
    assert closing.id == FieldName.HAS_LICENSE
    gate = SPEC.gate(Close.LICENSE_NOT_VALID)
    assert gate is not None
    assert gate.reapply
    assert gate.reopenable
    assert SPEC.talent_pool(Close.OUT_OF_AREA)
    assert not SPEC.talent_pool(Close.NO_LICENSE)
    assert SPEC.closing(DisqualifyReason.POLICY_VIOLATION) is None


def test_required_fields_and_no_contact_may_start_again():
    assert SPEC.unresolved(Close.UNRESOLVED_CITY)
    assert SPEC.unresolved(DisqualifyReason.NO_CONTACT)
    assert not SPEC.unresolved(Close.NO_LICENSE)


def test_platforms_are_asked_only_with_experience():
    platforms = SPEC.field(FieldName.PLATFORMS)
    assert not SPEC.asked(platforms, CandidateProfile())
    assert not SPEC.asked(platforms, CandidateProfile(experience_years=0))
    assert SPEC.asked(platforms, CandidateProfile(experience_years=1.5))


# --- what the model rejects ---


def _field(id: str, kind: FieldType, **rules: Any) -> FieldSpec:
    return FieldSpec(id=id, type=kind, **{"funnel_step": Step.NAME, **rules})


def _levels(**rules: Any) -> FieldSpec:
    return _field("levels", FieldType.MULTI_ENUM, options=("eso", "fp"), **rules)


def test_a_condition_has_one_known_operator():
    with pytest.raises(ValidationError, match="exactly one"):
        Condition(gt=0, eq=1)
    with pytest.raises(ValidationError):
        Condition.model_validate({"lt": 3})


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        # a field not asked before
        ((_field("master", FieldType.CREDENTIAL, ask_if={"levels": Condition(any_of=("eso",))}),
          _levels()), "not asked before"),
        ((_field("master", FieldType.CREDENTIAL, ask_if={"age": Condition(gt=18)}),), "not asked"),
        # an operator the field's type has not
        ((_levels(), _field("master", FieldType.CREDENTIAL, ask_if={"levels": Condition(gt=0)})),
         "`gt` on levels"),
        ((_field("years", FieldType.YEARS),
          _field("x", FieldType.FREE_TEXT, ask_if={"years": Condition(any_of=("1",))})),
         "`any_of` on years"),
        # a value that is no option
        ((_levels(),
          _field("master", FieldType.CREDENTIAL, ask_if={"levels": Condition(none_of=("bach",))})),
         "no options"),
    ],
)  # fmt: skip
def test_a_bad_ask_if_is_rejected(fields: tuple[FieldSpec, ...], error: str):
    with pytest.raises(ValidationError, match=error):
        AgentSpec(agent="school", fields=fields)


def test_an_unknown_hook_is_rejected():
    years = _field("years", FieldType.YEARS, checks=("seniority",))
    with pytest.raises(ValidationError, match="no check hook `seniority`"):
        AgentSpec(agent="school", fields=(years,))
    with pytest.raises(ValidationError, match="no reader hook `levels`"):
        AgentSpec(agent="school", fields=(_levels(reader="levels"),))


@pytest.mark.parametrize(
    ("kind", "reading"),
    [
        (FieldType.LOCATION, "no"),
        (FieldType.CREDENTIAL, "out_of_area"),
        (FieldType.YES_NO, "no"),
        (FieldType.MULTI_ENUM, "eso"),
    ],
)
def test_a_gate_on_a_reading_the_type_never_has_is_rejected(kind: FieldType, reading: str):
    gate = Gate(readings={reading: GateOutcome.CLOSE}, reasons={reading: Close.NO_LICENSE})
    options = {"options": ("eso",)} if kind == FieldType.MULTI_ENUM else {}
    with pytest.raises(ValidationError, match="is never read as"):
        _field("f", kind, gate=gate, **options)


def test_a_gate_gives_a_reason_for_each_close_and_only_for_those():
    with pytest.raises(ValidationError, match="reasons for"):
        Gate(readings={"no": GateOutcome.CLOSE})
    with pytest.raises(ValidationError, match="reasons for"):
        Gate(readings={"no": GateOutcome.FLAG}, reasons={"no": Close.NO_LICENSE})


@pytest.mark.parametrize(
    "rules",
    [
        {"type": FieldType.YEARS, "exclusive": ("x",)},
        {"type": FieldType.LOCATION, "read_back": ReadBack(over=10)},
        {"type": FieldType.CREDENTIAL, "zone_question_from": 3},
        {"type": FieldType.CONTACT},
        {"type": FieldType.FREE_TEXT, "channel": Channel.PHONE},
        {
            "type": FieldType.CONTACT,
            "channel": Channel.PHONE,
            "read_back": ReadBack(domain_typo=True),
        },
        {"type": FieldType.ENUM},
        {"type": FieldType.MULTI_ENUM, "options": ("a",), "exclusive": ("b",)},
    ],
)
def test_a_rule_for_another_type_is_rejected(rules: dict[str, Any]):
    with pytest.raises(ValidationError):
        FieldSpec(id="f", funnel_step=Step.NAME, **rules)


def test_ids_are_unique_and_every_reference_names_a_field():
    with pytest.raises(ValidationError, match="repeat"):
        AgentSpec(agent="school", fields=(_levels(), _levels()))
    with pytest.raises(ValidationError, match="apply_order: no field"):
        AgentSpec(agent="school", fields=(_levels(),), apply_order=("campus",))
    with pytest.raises(ValidationError, match="fix_words: no field"):
        AgentSpec(agent="school", fields=(_levels(),), fix_words={"campus": "campus"})


def test_funnel_steps_and_closes_are_the_agents_own_names():
    with pytest.raises(ValidationError, match="confirm is the engine's step"):
        AgentSpec(agent="school", fields=(_levels(funnel_step="confirm"),))
    no_contact = _field("name", FieldType.PERSON_NAME, required="no_contact")
    with pytest.raises(ValidationError, match="no_contact is the engine's close"):
        AgentSpec(agent="school", fields=(no_contact,))
    gate = Gate(readings={"no": GateOutcome.CLOSE}, reasons={"no": "policy_violation"})
    with pytest.raises(ValidationError, match="policy_violation is the engine's close"):
        AgentSpec(agent="school", fields=(_field("dbs", FieldType.CREDENTIAL, gate=gate),))
    AgentSpec(agent="school", fields=(_levels(funnel_step="levels", required="no_levels"),))


def test_the_dashboard_names_fields_and_filters_by_options():
    name = _field("full_name", FieldType.PERSON_NAME)
    with pytest.raises(ValidationError, match="dashboard: no field"):
        AgentSpec(
            agent="school",
            fields=(_levels(),),
            dashboard=DashboardSpec(columns=(Column(field="campus"),)),
        )
    with pytest.raises(ValidationError, match="no options to filter by"):
        AgentSpec(agent="school", fields=(name,), dashboard=DashboardSpec(filters=("full_name",)))
    AgentSpec(agent="school", fields=(_levels(),), dashboard=DashboardSpec(filters=("levels",)))


def test_the_teacher_example_is_a_valid_spec():
    """The design's second agent: an ask_if on a list, a gate that flags, enum and yes/no."""
    credential = Gate(
        readings={"no": GateOutcome.CLOSE, "in_progress": GateOutcome.FLAG},
        reasons={"no": Close.NO_LICENSE},
        reopenable=True,
        reapply=True,
    )
    teachers = AgentSpec(
        agent="teachers",
        fields=(
            _field("full_name", FieldType.PERSON_NAME, required=Close.UNRESOLVED_NAME),
            _field("email", FieldType.CONTACT, channel=Channel.EMAIL, max_tries=2),
            _field("levels", FieldType.MULTI_ENUM, options=("infantil", "primaria", "eso", "fp")),
            _field(
                "master_profesorado",
                FieldType.CREDENTIAL,
                ask_if={"levels": Condition(any_of=("eso", "fp"))},
                gate=credential,
            ),
            _field("english_c1", FieldType.YES_NO),
            _field("workload", FieldType.ENUM, options=("completa", "media", "sustituciones")),
            _field("experience_years", FieldType.YEARS, most=40, read_back=ReadBack(over=25)),
        ),
    )
    master = teachers.field("master_profesorado")
    assert master.ask_if["levels"].holds(["primaria", "eso"])
    assert not master.ask_if["levels"].holds(["infantil"])


@pytest.mark.parametrize(
    ("condition", "value", "holds"),
    [
        (Condition(gt=0), 2, True),
        (Condition(gt=0), 0, False),
        (Condition(gt=0), None, False),
        (Condition(eq="media"), "media", True),
        (Condition(eq=True), False, False),
        (Condition(any_of=("eso", "fp")), ["primaria", "fp"], True),
        (Condition(any_of=("eso", "fp")), "eso", True),
        (Condition(any_of=("eso",)), [], False),
        (Condition(none_of=("eso",)), ["primaria"], True),
        (Condition(none_of=("eso",)), ["eso"], False),
        (Condition(none_of=("eso",)), None, False),
    ],
)
def test_a_condition_reads_a_value_or_a_list(condition: Condition, value: object, holds: bool):
    assert condition.holds(value) is holds


# --- the engine on another spec ---


def _school(*more: FieldSpec, gate: Gate | None = None) -> AgentSpec:
    """Teachers, in the riders' profile: their levels where the availability is kept, a
    certificate where the license is."""
    certificate = gate or Gate(
        readings={"no": GateOutcome.CLOSE, "in_progress": GateOutcome.CLOSE_TALENT_POOL},
        reasons={
            "no": Close.NO_LICENSE,
            "in_progress": Close.LICENSE_IN_PROGRESS,
        },
    )
    levels = {Language.ES: {"full_time": "ESO", "part_time": "FP", "weekends": "Primaria"}}
    return AgentSpec(
        agent="school",
        fields=(
            FieldSpec(
                id="name",
                type=FieldType.PERSON_NAME,
                funnel_step=Step.NAME,
                stored_as=("full_name",),
                required=Close.UNRESOLVED_NAME,
                label={Language.ES: "Nombre"},
            ),
            FieldSpec(
                id="levels",
                type=FieldType.MULTI_ENUM,
                funnel_step=Step.AVAILABILITY,
                stored_as=("availability",),
                options=tuple(Availability),
                exclusive=(Availability.WEEKENDS,),
                reader="levels",
                label={Language.ES: "Niveles"},
                option_labels=levels,
            ),
            FieldSpec(
                id="certificate",
                type=FieldType.CREDENTIAL,
                funnel_step=Step.LICENSE,
                stored_as=("has_license",),
                ask_if={"levels": Condition(any_of=(Availability.WEEKENDS,))},
                gate=certificate,
                label={Language.ES: "Certificado"},
            ),
            *more,
        ),
        hooks=Hooks(
            readers={"levels": validate_availability},
            checks={"seniority": lambda _profile, years, _country, _today: (
                Platform.GLOVO if years > 1 else None
            )},
        ),
    )  # fmt: skip


def turn(intent: Intent = Intent.ANSWER, /, **fields: object) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={name: {"value": value, "confidence": 0.9} for name, value in fields.items()},  # pyright: ignore[reportArgumentType]
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def say(state: ConversationState, **fields: object) -> tuple[ConversationState, Any]:
    return step(state, turn(**fields), today=TODAY)


def at_levels() -> ConversationState:
    state, _ = start()
    state, action = step(state, turn(Intent.CONFIRM), today=TODAY)
    assert isinstance(action, AskField)
    assert action.field == "name"
    state, action = say(state, name="Lucía Fernández")
    assert isinstance(action, AskField)
    assert action.field == "levels"
    assert state.stage == Step.AVAILABILITY
    return state


def test_another_spec_asks_its_own_fields_in_order():
    with use(_school()):
        state, action = say(at_levels(), levels=["weekends"])
        assert isinstance(action, AskField)
        assert action.field == "certificate"
        assert state.stage == Step.LICENSE


def test_a_field_whose_condition_fails_is_not_asked():
    with use(_school()):
        state, action = say(at_levels(), levels=["full time"])
        assert isinstance(action, ConfirmSummary)
        assert state.profile.availability == [Availability.FULL_TIME]
        assert state.profile.has_license is None


def test_a_credential_gate_closes_with_the_reason_of_the_reading():
    with use(_school()):
        state, _ = say(at_levels(), levels=["weekends"])
        state, action = say(state, certificate=False)
        assert isinstance(action, Disqualify)
        assert action.reason == Close.NO_LICENSE
        assert not action.invite_reapply
        assert not action.talent_pool_offer
        assert state.profile.has_license is False
        assert not state.reopenable


def test_a_gate_may_offer_the_talent_pool_on_any_reading():
    with use(_school()):
        state, _ = say(at_levels(), levels=["weekends"])
        state, action = say(state, certificate="in_progress")
        assert isinstance(action, Disqualify)
        assert action.talent_pool_offer
        assert awaits_talent_pool_answer(state)


def test_a_reading_the_gate_lets_through_is_kept():
    passes = Gate(
        readings={"in_progress": GateOutcome.CLOSE},
        reasons={"in_progress": Close.LICENSE_IN_PROGRESS},
    )
    with use(_school(gate=passes)):
        state, _ = say(at_levels(), levels=["weekends"])
        state, action = say(state, certificate=False)
        assert isinstance(action, ConfirmSummary)
        assert state.profile.has_license is False


def _own(id: str, kind: FieldType, **rules: Any) -> FieldSpec:
    """A field of the school's own, kept by its id beside the riders' profile attributes."""
    return _field(id, kind, label={Language.ES: id, Language.EN: id}, **rules)


def at_own() -> ConversationState:
    """Past the levels and the certificate (not asked for full time): the fields after them."""
    state, _ = say(at_levels(), levels=["full time"])
    return state


def test_a_flagging_gate_keeps_the_reading_and_flags_it_for_the_recruiter():
    flags = Gate(readings={"in_progress": GateOutcome.FLAG})
    named = {Language.ES: {"in_progress": "En trámite"}, Language.EN: {"in_progress": "Pending"}}
    dbs = _own("dbs", FieldType.CREDENTIAL, gate=flags, option_labels=named)
    with use(_school(dbs)):
        state, action = say(at_own(), dbs="in_progress")
        assert isinstance(action, ConfirmSummary)
        assert state.outcome is None
        assert QualityFlag.GATE_FLAG in state.quality_flags
        assert state.profile.dbs == "in_progress"  # pyright: ignore[reportAttributeAccessIssue]
        assert summary_value(state.profile, "dbs", Language.ES) == "En trámite"
        state, _ = say(at_own(), dbs="sí, lo tengo")
        assert state.profile.dbs is True  # pyright: ignore[reportAttributeAccessIssue]
        assert QualityFlag.GATE_FLAG not in state.quality_flags


def test_a_reading_a_gate_flags_has_a_name_in_every_language():
    flags = Gate(readings={"in_progress": GateOutcome.FLAG})
    dbs = AgentSpec(agent="school", fields=(_own("dbs", FieldType.CREDENTIAL, gate=flags),))
    with pytest.raises(SpecError, match="no es label for \\['in_progress'\\]"):
        check_texts(dbs, {})


def test_a_credential_reads_its_own_words():
    readings = {
        "in_progress": ("lo estoy cursando", "me falta el tfm"),
        "not_valid": ("caducad[oa]",),
    }
    gate = Gate(
        readings={"in_progress": GateOutcome.CLOSE_TALENT_POOL, "not_valid": GateOutcome.CLOSE},
        reasons={"in_progress": "master_in_progress", "not_valid": "master_not_valid"},
    )
    master = _own("master", FieldType.CREDENTIAL, readings=readings, gate=gate)
    with use(_school(master)):
        _, action = say(at_own(), master="Me falta el TFM")
        assert isinstance(action, Disqualify)
        assert action.reason == "master_in_progress"
        _, action = say(at_own(), master="estoy sacándolo")  # a licence's words, not its
        assert isinstance(action, AskField)
        assert action.field == "master"
    with pytest.raises(ValidationError, match="no words for a reading"):
        _own("master", FieldType.CREDENTIAL, readings={"yes": ("sí",)})


def test_a_yes_no_an_enum_and_free_text_are_read_by_type():
    workload = _own(
        "workload",
        FieldType.ENUM,
        options=("full", "half"),
        option_labels={Language.ES: {"full": "Jornada completa", "half": "Media jornada"}},
        aliases={"half": ("media",)},
    )
    fields = (_own("english", FieldType.YES_NO), workload, _own("note", FieldType.FREE_TEXT))
    with use(_school(*fields)):
        state, action = say(at_own(), english="ni idea")
        assert isinstance(action, AskField)
        assert action.field == "english"
        state, action = say(state, english="Sí, el C1")
        assert isinstance(action, AskField)
        assert action.field == "workload"
        state, action = say(state, workload="jornada completa o media")
        assert isinstance(action, Clarify)
        assert action.options == ("full", "half")
        state, action = say(state, workload="half")
        assert isinstance(action, AskField)
        assert action.field == "note"
        state, action = say(state, note="  Llevo   el club de ajedrez  ")
        assert isinstance(action, ConfirmSummary)
        profile = state.profile
        assert (profile.english, profile.workload, profile.note) == (  # pyright: ignore[reportAttributeAccessIssue]
            True,
            "half",
            "Llevo el club de ajedrez",
        )


def test_a_multi_enum_without_a_reader_reads_its_options_and_other():
    subjects = _own(
        "subjects",
        FieldType.MULTI_ENUM,
        stored_as=("subjects", "subjects_other"),
        options=("maths", "physics_chemistry"),
        option_labels={
            Language.ES: {"maths": "Matemáticas", "physics_chemistry": "Física y Química"}
        },
        aliases={"maths": ("mates",), "physics_chemistry": ("fyq", "fisica", "quimica")},
        other=True,
    )
    with use(_school(subjects)):
        state, _ = say(at_own(), subjects="Mates y física, sobre todo")
        assert state.profile.subjects == ["maths", "physics_chemistry"]  # pyright: ignore[reportAttributeAccessIssue]
        assert state.profile.subjects_other is None  # pyright: ignore[reportAttributeAccessIssue]
        state, _ = say(at_own(), subjects=["maths", "Robótica"])
        assert state.profile.subjects == ["maths", "other"]  # pyright: ignore[reportAttributeAccessIssue]
        assert state.profile.subjects_other == "Robótica"  # pyright: ignore[reportAttributeAccessIssue]
        state, action = say(at_own(), subjects="nada de eso")
        assert isinstance(action, ConfirmSummary)
        assert state.profile.subjects == ["other"]  # pyright: ignore[reportAttributeAccessIssue]


def test_an_agents_own_fields_are_stored_and_read_back_by_their_ids():
    start_date = _own("joins", FieldType.DATE)
    with use(_school(start_date, _own("english", FieldType.YES_NO))):
        state, _ = say(at_own(), joins="2026-11-02", english=True)
        stored = ConversationState.model_validate(state.model_dump(mode="json"))
        assert stored == state
        assert stored.profile.joins == date(2026, 11, 2)  # pyright: ignore[reportAttributeAccessIssue]


def test_an_optional_field_is_left_out_after_its_tries():
    with use(_school()):
        state, _ = say(at_levels(), levels=["weekends"])
        state, _ = say(state, certificate="ni idea")
        state, _ = say(state, certificate="ni idea")
        state, action = say(state, certificate="ni idea")
        assert isinstance(action, ConfirmSummary)
        assert "certificate" in state.skipped


def test_a_years_check_hook_reads_the_years_back_with_its_doubt():
    years = FieldSpec(
        id="years",
        type=FieldType.YEARS,
        funnel_step=Step.EXPERIENCE,
        stored_as=("experience_years", "experience_unverified"),
        checks=("seniority",),
        label={Language.ES: "Experiencia"},
    )
    with use(_school(years)):
        state, action = say(at_levels(), levels=["full time"], years=3)
        assert isinstance(action, ConfirmValue)
        assert action.field == "years"
        assert action.platform == Platform.GLOVO
        assert state.profile.experience_years is None
        state, action = step(state, turn(Intent.CONFIRM), today=TODAY)
        assert state.profile.experience_years == 3
        assert state.profile.experience_unverified is True


@pytest.mark.parametrize("country", ["ES", "MX"])
def test_widgets_follow_the_field_type(country: Country):
    with use(_school()):
        options = field_options("levels", country)
        assert options == tuple(Availability)
        ui = field_ui("levels", options, Language.ES, country)
        assert isinstance(ui, MultiSelect)
        assert ui.exclusive == ("weekends",)
        assert [option.label for option in ui.options] == ["ESO", "FP", "Primaria"]
        assert field_options("certificate", country) == ("yes", "no")
        yes_no = field_ui("certificate", ("yes", "no"), Language.ES, country)
        assert isinstance(yes_no, QuickReplies)
        assert field_options("name", country) == ()
        assert field_ui("name", (), Language.ES, country) is None


@pytest.mark.parametrize("country", ["ES", "MX"])
def test_a_phone_is_typed_into_the_phone_widget(country: Country):
    phone = SPEC.channel(Channel.PHONE)
    assert phone is not None
    ui = field_ui(phone.id, field_options(phone.id, country), Language.ES, country)
    assert isinstance(ui, PhoneEntry)
    assert ui.selected == country


def test_the_summary_shows_values_by_type():
    profile = CandidateProfile(full_name="Lucía Fernández", availability=[Availability.WEEKENDS])
    with use(_school()):
        assert summary_value(profile, "levels", Language.ES) == "Primaria"
        assert summary_value(profile, "name", Language.ES) == "Lucía Fernández"
        assert summary_value(profile, "certificate", Language.ES) is None
