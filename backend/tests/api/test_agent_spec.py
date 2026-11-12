"""`GET /api/agent-spec`: what the dashboard renders of the agent, by field type. The rider
values here are the names the dashboard showed when it named every field itself."""

from fastapi.testclient import TestClient

from riders import SPEC
from sazon.agents.riders import Step
from sazon.api.agent_spec import agent_spec_out
from sazon.engine.models import Language
from sazon.engine.spec import AgentSpec, Column, DashboardSpec, FieldSpec, FieldType


def test_the_dashboard_gets_the_fields_in_order_with_their_names(client: TestClient):
    response = client.get("/api/agent-spec")
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "riders"
    assert body["language"] == "es"
    fields = {field["id"]: field for field in body["fields"]}
    assert list(fields) == list(SPEC.ids)
    assert {name: field["label"] for name, field in fields.items()} == {
        "full_name": "Nombre",
        "phone": "Teléfono",
        "email": "Email",
        "has_license": "Carnet",
        "city": "Ubicación",
        "availability": "Disponibilidad",
        "schedule": "Horario",
        "experience_years": "Experiencia",
        "platforms": "Plataformas",
        "start_date": "Fecha de inicio",
    }
    assert fields["has_license"]["country_labels"] == {"MX": "Licencia"}
    assert fields["phone"]["channel"] == "phone"
    assert fields["experience_years"]["companion"] == "experience_unverified"
    assert fields["start_date"]["companion"] == "late_start_days"
    assert fields["city"]["attr"] == "location_id"
    platforms = fields["platforms"]
    assert platforms["none_label"] == "Ninguna"
    assert platforms["options"][-1] == {"value": "other", "label": "Otra"}


def test_the_list_columns_and_filters():
    out = agent_spec_out(SPEC)
    assert [(c.field, c.label, c.sort, c.wrap) for c in out.columns] == [
        ("availability", "Disponibilidad", None, True),
        ("schedule", "Horario", None, False),
        ("experience_years", "Experiencia", "experience_years", False),
        ("start_date", "Inicio", "start_date", False),
    ]
    assert [(f.param, f.label) for f in out.filters] == [
        ("availability", "Disponibilidad"),
        ("schedule", "Horario"),
    ]
    assert [o.label for o in out.filters[1].options] == ["Mañana", "Tarde", "Noche", "Flexible"]


def test_the_funnel_steps_are_the_analytics_steps_with_their_names():
    out = agent_spec_out(SPEC)
    assert [step.step for step in out.funnel] == list(SPEC.funnel.steps)
    assert [step.label for step in out.funnel] == [
        "Consentimiento",
        "Nombre",
        "Contacto",
        "Carnet",
        "Ciudad",
        "Disponibilidad",
        "Horario",
        "Experiencia",
        "Fecha de inicio",
        "Confirmación",
        "Apto",
    ]


def test_another_spec_names_its_own_fields_and_steps():
    school = AgentSpec(
        agent="school",
        fields=(
            FieldSpec(
                id="workload",
                type=FieldType.ENUM,
                funnel_step=Step.AVAILABILITY,
                options=("completa", "media"),
                label={Language.ES: "Jornada"},
                option_labels={Language.ES: {"completa": "Completa", "media": "Media"}},
            ),
        ),
        dashboard=DashboardSpec(columns=(Column(field="workload"),), filters=("workload",)),
    )
    out = agent_spec_out(school)
    assert [(c.field, c.label) for c in out.columns] == [("workload", "Jornada")]
    assert [o.label for o in out.filters[0].options] == ["Completa", "Media"]
    assert [s.label for s in out.funnel] == ["Consentimiento", "Jornada", "Confirmación", "Apto"]


def test_the_list_serves_every_filter_and_sort_the_spec_offers(client: TestClient):
    """The dashboard builds its filters and sortable columns from the spec; the candidate list
    still names its own, so each must be one it takes."""
    out = agent_spec_out(SPEC)
    for spec_filter in out.filters:
        for option in spec_filter.options:
            response = client.get("/api/candidates", params={spec_filter.param: option.value})
            assert response.status_code == 200, (spec_filter.param, option.value)
    for column in out.columns:
        if column.sort:
            for sort in (column.sort, f"-{column.sort}"):
                assert client.get("/api/candidates", params={"sort": sort}).status_code == 200
