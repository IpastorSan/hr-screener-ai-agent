"""The agent spec as the recruiter dashboard reads it: the fields with their types, names and
options, the candidate list's columns and filters, and the funnel's steps. The dashboard renders
each by its field type, so it names no field itself."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sazon.api.deps import require_recruiter
from sazon.engine.models import Language, Stage, StepId
from sazon.engine.spec import (
    QUALIFIED_STEP,
    YES_NO_TYPES,
    AgentSpec,
    Channel,
    FieldSpec,
    FieldType,
    agent,
)
from sazon.engine.ui import YES_NO
from sazon.engine.validators import Country

router = APIRouter(
    prefix="/api/agent-spec", tags=["recruiter"], dependencies=[Depends(require_recruiter)]
)

STEP_LABELS = {
    Language.ES: {
        Stage.CONSENT: "Consentimiento",
        Stage.CONFIRM: "Confirmación",
        QUALIFIED_STEP: "Apto",
    },
    Language.EN: {
        Stage.CONSENT: "Consent",
        Stage.CONFIRM: "Read-back",
        QUALIFIED_STEP: "Qualified",
    },
}
"""The funnel's steps that are not a field's: the engine's own."""


class OptionOut(BaseModel):
    value: str
    label: str


class FieldOut(BaseModel):
    id: str
    type: FieldType
    channel: Channel | None = Field(description="`contact`: phone or email")
    attr: str = Field(description="The profile attribute (and candidate column) with the value")
    companion: str | None = Field(
        description="The attribute kept with the value: the words of an `other`, whether "
        "`years` failed a check, the days to a late start"
    )
    label: str
    country_labels: dict[Country, str] = Field(
        description="`label` where a country names it otherwise"
    )
    options: list[OptionOut] = Field(description="`enum`, `multi_enum`, `credential`, `yes_no`")
    none_label: str | None = Field(description="`multi_enum`: an empty choice")
    funnel_step: StepId


class ColumnOut(BaseModel):
    field: str
    label: str
    sort: str | None = Field(description="The list's sort key for the column, if it sorts")
    wrap: bool = Field(description="Long values wrap in a narrow column")


class FilterOut(BaseModel):
    field: str
    param: str = Field(description="The list's query parameter")
    label: str
    options: list[OptionOut]


class StepOut(BaseModel):
    step: str = Field(description="A funnel step, as `AnalyticsOut.funnel` names it")
    label: str


class AgentSpecOut(BaseModel):
    agent: str
    language: Language = Field(description="The language of every label")
    fields: list[FieldOut] = Field(description="In the order they are asked")
    columns: list[ColumnOut]
    filters: list[FilterOut]
    funnel: list[StepOut]
    interview_scope: list[str] = Field(
        description="The fields a recruiter may be scoped on beyond the locations "
        "(`RecruiterOut.scope`): a teacher's subjects"
    )


@router.get("", response_model=AgentSpecOut)
async def get_agent_spec() -> AgentSpecOut:
    """What the dashboard shows of the agent it screens for."""
    return agent_spec_out(agent())


def agent_spec_out(spec: AgentSpec) -> AgentSpecOut:
    dashboard = spec.dashboard
    language = dashboard.language
    labels = {f.id: dashboard.labels.get(f.id) or f.label[language] for f in spec.fields}
    own = STEP_LABELS[language]
    firsts = spec.funnel.firsts
    return AgentSpecOut(
        agent=spec.agent,
        language=language,
        fields=[
            FieldOut(
                id=field.id,
                type=field.type,
                channel=field.channel,
                attr=field.attr,
                companion=field.companion,
                label=labels[field.id],
                country_labels=dict(dashboard.country_labels.get(field.id, {})),
                options=_options(field, language),
                none_label=field.none_label.get(language),
                funnel_step=field.funnel_step,
            )
            for field in spec.fields
        ],
        columns=[
            ColumnOut(
                field=column.field,
                label=column.label or labels[column.field],
                sort=spec.field(column.field).attr if column.sortable else None,
                wrap=column.wrap,
            )
            for column in dashboard.columns
        ],
        filters=[
            FilterOut(
                field=name,
                param=spec.field(name).attr,
                label=labels[name],
                options=_options(spec.field(name), language),
            )
            for name in dashboard.filters
        ],
        funnel=[
            StepOut(
                step=step,
                label=own.get(step) or dashboard.step_labels.get(step) or labels[firsts[step].id],
            )
            for step in spec.funnel.steps
        ],
        interview_scope=[field.id for field in spec.scoped],
    )


def _options(field: FieldSpec, language: Language) -> list[OptionOut]:
    """The options by their names; a credential's yes and no, and the readings its gate keeps
    as the value ("En trámite")."""
    names = field.option_labels.get(language, {})
    if field.type in YES_NO_TYPES:
        yes_no = [OptionOut(value=option.value, label=option.label) for option in YES_NO[language]]
        return yes_no + [OptionOut(value=value, label=label) for value, label in names.items()]
    return [OptionOut(value=choice, label=names.get(choice, choice)) for choice in field.choices]
