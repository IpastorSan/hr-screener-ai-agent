import asyncio
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import col, select

from sazon.api.analytics import Filters, compute_analytics
from sazon.api.app import create_app
from sazon.api.seed import seed
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.service_areas import load_locations
from sazon.llm import FakeLLM
from sazon.service import ScreeningService
from sazon.storage.models import Booking, BookingStatus, Candidate, Recruiter
from sazon.storage.repo import Repo, init_db, make_engine

NAME = "Lucía García"
EMAIL = "lucia@example.com"
BRIEF_FIELDS = (
    "full_name",
    "phone",
    "email",
    "has_license",
    "city",
    "location_id",
    "availability",
    "schedule",
    "experience_years",
    "platforms",
    "start_date",
)
JSON = {"accept": "application/json"}
CONTEXT = {"country": "ES", "timezone": "Europe/Madrid", "locale": "es-ES"}


def ex(intent: Intent = Intent.ANSWER, **fields: Any) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={
            name: FieldCandidate(value=value, confidence=0.95) for name, value in fields.items()
        },
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    parsed = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def start(client: TestClient) -> str:
    response = client.post("/api/conversations", json={"channel": "web", "context": CONTEXT})
    assert response.status_code == 200
    return response.json()["conversation_id"]


def send(client: TestClient, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json=body, headers=JSON
    )
    assert response.status_code == 200, response.text
    return response.json()


def tap(kind: str, value: Any) -> dict[str, Any]:
    return {"action": {"kind": kind, "value": value}}


# --- candidate chat ---


def test_start_returns_greeting_with_consent_buttons(client: TestClient):
    response = client.post("/api/conversations", json={"context": CONTEXT})

    body = response.json()
    assert body["conversation_id"]
    assert body["reply"]["text"]
    assert body["reply"]["ui"]["kind"] == "quick_replies"
    assert [o["value"] for o in body["reply"]["ui"]["options"]] == ["yes", "no"]
    assert body["reply"]["ui"]["link"]["href"] == "/privacidad"
    assert body["state"] == {
        "stage": "consent",
        "outcome": None,
        "language": "es",
        "country": "ES",
        "ended": False,
        "restartable": False,
        "reopenable": False,
        "paused": False,
    }


def test_message_streams_reply_then_done(client: TestClient, llm: FakeLLM):
    conversation_id = start(client)
    send(client, conversation_id, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME))

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"text": NAME},
        headers={"accept": "text/event-stream"},
    )

    assert response.headers["content-type"].startswith("text/event-stream")
    (first, reply), (last, done) = events(response.text)
    assert (first, last) == ("reply", "done")
    assert reply["ui"]["kind"] == "phone"
    assert done["reply"] == reply
    assert done["conversation_id"] == conversation_id
    assert done["resumed"] is False
    assert done["state"]["stage"] == "contact"


def test_sse_is_the_default_without_accept_header(client: TestClient):
    conversation_id = start(client)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json=tap("quick_replies", "yes"),
        headers={"accept": "*/*"},
    )
    assert [name for name, _ in events(response.text)] == ["reply", "done"]


def test_full_screening_with_widget_actions(client: TestClient, llm: FakeLLM, now: datetime):
    conversation_id = start(client)
    assert send(client, conversation_id, tap("quick_replies", "yes"))["state"]["stage"] == "name"
    llm.queue(ex(full_name=NAME))
    send(client, conversation_id, {"text": NAME})

    after_phone = send(
        client, conversation_id, tap("phone", {"country": "ES", "number": "612345678"})
    )
    assert after_phone["state"]["stage"] == "contact"

    llm.queue(ex(email=EMAIL))
    send(client, conversation_id, {"text": EMAIL})
    send(client, conversation_id, tap("quick_replies", "yes"))
    llm.queue(ex(city="Malasaña"))
    after_city = send(client, conversation_id, {"text": "Malasaña"})
    assert after_city["state"]["stage"] == "availability"

    send(client, conversation_id, tap("multi_select", ["full_time", "weekends"]))
    send(client, conversation_id, tap("multi_select", ["morning", "afternoon"]))
    llm.queue(ex(experience_years=2, platforms=["glovo"]))
    send(client, conversation_id, {"text": "2 años en Glovo"})
    start_date = (now + timedelta(days=7)).date().isoformat()
    llm.queue(ex(start_date=start_date))
    summary = send(client, conversation_id, {"text": "el lunes que viene"})
    assert summary["reply"]["ui"]["kind"] == "confirm"
    assert summary["state"]["stage"] == "confirm"

    rows = {field["label"]: field["value"] for field in summary["reply"]["ui"]["fields"]}
    assert rows["Experiencia"] == "2 años"
    assert rows["Fecha de inicio"] == "lunes 26 de octubre"

    done = send(client, conversation_id, tap("confirm", True))
    assert done["state"]["outcome"] == "qualified"
    assert done["candidate_text"] == "Sí, es correcto"
    turns = client.get(f"/api/conversations/{conversation_id}").json()["turns"]
    assert turns[-2] == {**turns[-2], "role": "candidate", "text": "Sí, es correcto"}
    assert all(turn["ui"] is None for turn in turns[:-1])
    # No recruiter: in person with Madrid Centro's manager, the only mode, so no question.
    assert turns[-1]["ui"]["kind"] == "slots"

    candidates = client.get("/api/candidates").json()["items"]
    assert len(candidates) == 1
    profile = client.get(f"/api/candidates/{candidates[0]['id']}").json()["profile"]
    assert profile["phone"] == "+34612345678"
    assert profile["availability"] == ["full_time", "weekends"]
    assert profile["schedule"] == ["morning", "afternoon"]
    assert profile["location_id"] == "es-mad-centro"


def test_a_license_close_keeps_the_composer_open_for_one_message(client: TestClient, llm: FakeLLM):
    """The message right after a license close may reopen it (QA batch 3): the chat has ended,
    but `reopenable` keeps the composer open for that message; after it, it locks."""
    conversation_id = start(client)
    send(client, conversation_id, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME, phone="612345678", email=EMAIL))
    send(client, conversation_id, {"text": f"{NAME}, 612345678, {EMAIL}"})

    closed = send(client, conversation_id, tap("quick_replies", "no"))["state"]
    assert closed["outcome"] == "disqualified"
    assert closed["ended"] is True
    assert closed["reopenable"] is True
    assert client.get(f"/api/conversations/{conversation_id}").json()["state"]["reopenable"]

    llm.queue(ex(Intent.SMALL_TALK))
    after = send(client, conversation_id, {"text": "vale, gracias"})["state"]
    assert after["ended"] is True
    assert after["reopenable"] is False


def test_transcript_restores_the_chat(client: TestClient, llm: FakeLLM):
    conversation_id = start(client)
    [greeting] = client.get(f"/api/conversations/{conversation_id}").json()["turns"]
    assert greeting["ui"]["kind"] == "quick_replies"
    send(client, conversation_id, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME))
    send(client, conversation_id, {"text": NAME})
    send(client, conversation_id, tap("phone", {"country": "MX", "number": "5512345678"}))

    body = client.get(f"/api/conversations/{conversation_id}").json()

    roles = [turn["role"] for turn in body["turns"]]
    assert roles == ["agent", "candidate", "agent", "candidate", "agent", "candidate", "agent"]
    assert all(turn["ui"] is None for turn in body["turns"])
    assert body["turns"][1]["text"] == "Sí"
    assert body["turns"][3]["text"] == NAME
    assert body["turns"][5]["text"] == "+52 5512345678"
    assert all(turn["created_at"] for turn in body["turns"])
    assert body["state"]["stage"] == "contact"
    assert "extraction" not in body["turns"][3]


def test_resumed_conversation_id_is_returned(client: TestClient, llm: FakeLLM):
    first = start(client)
    send(client, first, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME, phone="612345678", email=EMAIL))
    send(client, first, {"text": f"{NAME}, 612345678, {EMAIL}"})

    second = start(client)
    send(client, second, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME, phone="612345678", email=EMAIL))
    asked = send(client, second, {"text": f"{NAME}, 612345678, {EMAIL}"})
    assert asked["resumed"] is False
    assert asked["reply"]["text"] == (
        "Ya tenemos este teléfono registrado. ¿Quieres continuar como Lucía?"
    )
    assert [o["value"] for o in asked["reply"]["ui"]["options"]] == ["yes", "no"]

    body = send(client, second, tap("quick_replies", "yes"))

    assert body["resumed"] is True
    assert body["conversation_id"] == first
    assert body["candidate_text"] == "Sí"
    assert body["reply"]["text"] == (
        "Hola de nuevo, Lucía, seguimos donde lo dejaste. ¿Tienes carnet de conducir en vigor?"
    )
    turns = client.get(f"/api/conversations/{first}").json()["turns"]
    assert turns[-1]["text"] == body["reply"]["text"]


def test_a_chat_started_with_restart_of_does_not_resume_the_one_left(
    client: TestClient, llm: FakeLLM
):
    first = start(client)
    send(client, first, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME, phone="612345678", email=EMAIL))
    send(client, first, {"text": f"{NAME}, 612345678, {EMAIL}"})

    response = client.post("/api/conversations", json={"context": CONTEXT, "restart_of": first})
    second = response.json()["conversation_id"]
    send(client, second, tap("quick_replies", "yes"))
    llm.queue(ex(full_name=NAME, phone="612345678", email=EMAIL))
    body = send(client, second, {"text": f"{NAME}, 612345678, {EMAIL}"})

    assert body["resumed"] is False
    assert body["conversation_id"] == second
    assert body["state"]["stage"] == "license"
    assert client.get(f"/api/conversations/{first}").json()["state"]["outcome"] == "abandoned"


def test_unknown_conversation_is_404_json(client: TestClient):
    response = client.post("/api/conversations/nope/messages", json={"text": "hola"})
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "detail": "Conversation not found."}
    assert client.get("/api/conversations/nope").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"text": EMAIL, "action": {"kind": "confirm", "value": True}},
        {},
        {"action": {"kind": "phone", "value": {"country": "FR", "number": EMAIL}}},
        {"action": {"kind": "teleport", "value": EMAIL}},
    ],
)
def test_invalid_message_is_422_without_echoing_input(client: TestClient, body: dict[str, Any]):
    conversation_id = start(client)
    response = client.post(f"/api/conversations/{conversation_id}/messages", json=body)

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "validation_error"
    assert payload["fields"]
    assert EMAIL not in response.text


def test_unexpected_error_is_json_without_trace_or_pii(service: ScreeningService):
    app = create_app(service)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"failed for {EMAIL}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": "error",
        "detail": "Something went wrong. Please try again.",
    }
    assert EMAIL not in response.text


def test_cors_allows_the_web_origin(client: TestClient):
    response = client.options(
        "/api/conversations",
        headers={"origin": "http://localhost:3000", "access-control-request-method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_openapi_documents_sse_events(client: TestClient):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/conversations/{conversation_id}/messages"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    assert "`done`" in operation["description"]
    assert "MessageOut" in schema["components"]["schemas"]


# --- recruiter ---


@pytest.fixture(scope="module")
def seed_template() -> dict[str, Engine]:
    """Holds the module's seeded database once the first test has built it."""
    return {}


@pytest.fixture
def seeded(
    request: pytest.FixtureRequest,
    service: ScreeningService,
    now: datetime,
    seed_template: dict[str, Engine],
) -> TestClient:
    """A signed-in client over 30 seeded conversations.

    Seeding takes about a second, so the module seeds once (inside a test, after the
    environment fixtures ran) and every test gets its own copy of that database: tests that
    write never see each other's changes.
    """
    if "engine" not in seed_template:
        engine = make_engine("sqlite://")
        init_db(engine)
        asyncio.run(seed(Repo(engine), 30, now=now))
        seed_template["engine"] = engine
    source = seed_template["engine"].raw_connection()
    target = service.repo.engine.raw_connection()
    try:
        copy_from = cast(sqlite3.Connection, source.driver_connection)
        copy_from.backup(cast(sqlite3.Connection, target.driver_connection))
    finally:
        source.close()
        target.close()
    return request.getfixturevalue("client")


def items(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/candidates", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_list_filters(seeded: TestClient):
    everyone = items(seeded)
    assert len(everyone) > 20

    for status in ("qualified", "abandoned", "disqualified", "withdrawn"):
        rows = items(seeded, status=status)
        assert rows
        assert {row["status"] for row in rows} == {status}
    mexico = items(seeded, country="MX")
    assert mexico
    assert {row["country"] for row in mexico} == {"MX"}
    location = everyone[0]["location_id"] or "es-mad-centro"
    assert {row["location_id"] for row in items(seeded, location_id=location)} <= {location}
    for row in items(seeded, availability="weekends"):
        assert "weekends" in row["availability"]
    for row in items(seeded, schedule="morning"):
        assert "morning" in row["schedule"] or "flexible" in row["schedule"]
    flagged = items(seeded, quality_flag="unknown_faq")
    assert flagged
    assert all("unknown_faq" in row["quality_flags"] for row in flagged)
    for row in items(seeded, sentiment="positive"):
        assert row["sentiment"] == "positive"

    named = everyone[0]
    by_name = items(seeded, q=named["name"].split()[0].lower())
    assert named["id"] in {row["id"] for row in by_name}
    by_phone = items(seeded, q=named["phone"][-6:])
    assert [row["id"] for row in by_phone] == [named["id"]]


def test_the_talent_pool_filter(seeded: TestClient, service: ScreeningService):
    """SAZ-42: "Bolsa de candidatos", the out-of-area candidates who agreed to keep their data."""
    everyone = items(seeded)
    pooled = everyone[-1]
    candidate = service.repo.get_candidate(pooled["id"])
    assert candidate is not None
    candidate.talent_pool = True
    service.repo.save(candidate)

    pool = items(seeded, talent_pool="true")
    assert pooled["id"] in {row["id"] for row in pool}
    assert all(row["talent_pool"] for row in pool)
    others = items(seeded, talent_pool="false")
    assert pooled["id"] not in {row["id"] for row in others}
    assert len(pool) + len(others) == len(everyone)
    mexico_pool = items(seeded, talent_pool="true", country=pooled["country"])
    assert pooled["id"] in {row["id"] for row in mexico_pool}


def test_list_sorts_and_paginates(seeded: TestClient):
    newest = items(seeded, sort="-created_at")
    created = [row["created_at"] for row in newest]
    assert created == sorted(created, reverse=True)
    names = [row["name"] for row in items(seeded, sort="name")]
    named = [name for name in names if name]
    assert None in names  # the seed has nameless candidates: they go last
    assert names[: len(named)] == named
    assert named[0] <= named[-1]
    years = [row["experience_years"] for row in items(seeded, sort="-experience_years")]
    present = [y for y in years if y is not None]
    assert present == sorted(present, reverse=True)
    assert years[: len(present)] == present

    params = {"page_size": 5, "sort": "-created_at"}
    first = seeded.get("/api/candidates", params=params).json()
    second = seeded.get("/api/candidates", params={**params, "page": 2}).json()
    assert first["total"] == second["total"] == len(newest)
    assert len(first["items"]) == 5
    assert [r["id"] for r in first["items"] + second["items"]] == [r["id"] for r in newest[:10]]


def test_default_sort_is_latest_activity(
    seeded: TestClient, service: ScreeningService, now: datetime
):
    oldest = items(seeded, sort="created_at")[0]
    conversation = service.repo.latest_conversation(oldest["id"])
    assert conversation is not None
    conversation.last_activity_at = now - timedelta(minutes=1)
    service.repo.save(conversation)

    rows = items(seeded)
    assert rows[0]["id"] == oldest["id"]
    activity = [row["last_activity_at"] for row in rows]
    assert activity == sorted(activity, reverse=True)
    oldest_first = [row["last_activity_at"] for row in items(seeded, sort="last_activity")]
    assert oldest_first == sorted(activity)
    assert rows != items(seeded, sort="-created_at")  # activity order is not creation order


def test_list_counts_every_status_for_the_other_filters(seeded: TestClient):
    page = seeded.get("/api/candidates", params={"country": "ES", "page_size": 1}).json()
    counts = page["counts"]
    assert counts["all"] == page["total"]
    for status in ("in_progress", "qualified", "disqualified", "abandoned", "withdrawn"):
        assert counts[status] == len(items(seeded, country="ES", status=status))
    assert sum(counts.values()) == 2 * counts["all"]

    qualified = seeded.get("/api/candidates", params={"country": "ES", "status": "qualified"})
    assert qualified.json()["counts"] == counts
    assert qualified.json()["total"] == counts["qualified"]


def test_locations(client: TestClient):
    response = client.get("/api/locations")
    assert response.status_code == 200
    locations = response.json()
    assert len(locations) == len(load_locations())
    madrid = next(loc for loc in locations if loc["location_id"] == "es-mad-centro")
    assert madrid == {
        "location_id": "es-mad-centro",
        "name": "Sazón Madrid Centro",
        "city": "Madrid",
        "country": "ES",
    }
    countries = [loc["country"] for loc in locations]
    assert countries == sorted(countries)


@pytest.mark.parametrize(
    "params", [{"status": "hired_maybe"}, {"page_size": 1000}, {"sort": "salary"}, {"page": 0}]
)
def test_list_rejects_bad_params(seeded: TestClient, params: dict[str, Any]):
    assert seeded.get("/api/candidates", params=params).status_code == 422


def test_detail_has_conversations_turns_and_summary(seeded: TestClient):
    qualified = items(seeded, status="qualified")[0]
    detail = seeded.get(f"/api/candidates/{qualified['id']}").json()

    assert detail["profile"]["full_name"] == qualified["name"]
    assert detail["summary"]["lines"]
    conversation = detail["conversations"][0]
    assert conversation["outcome"] == "qualified"
    candidate_turns = [t for t in conversation["turns"] if t["role"] == "candidate"]
    agent_turns = [t for t in conversation["turns"] if t["role"] == "agent"]
    assert all(t["extraction"]["intent"] for t in candidate_turns)
    assert all(t["action"]["kind"] for t in agent_turns)


def test_booked_interviews_show_in_the_list_and_detail(seeded: TestClient):
    qualified = items(seeded, status="qualified")
    booked = [item for item in qualified if item["interview_at"]]
    assert booked
    assert all(item["interview_at"] is None for item in items(seeded, status="disqualified"))

    detail = seeded.get(f"/api/candidates/{booked[0]['id']}").json()

    booking = detail["booking"]
    assert booking["starts_at"] == detail["interview_at"]
    assert booking["location_id"] == detail["location_id"]
    assert booking["location_name"] == detail["location_name"]
    assert booking["address"]
    assert booking["local_start"][-6] in "+-"  # local time with its UTC offset
    agent_kinds = [t["action"]["kind"] for t in detail["conversations"][0]["turns"] if t["action"]]
    assert "confirm_booking" in agent_kinds


def test_every_booked_video_interview_has_a_recruiter_and_mis_entrevistas_shows_theirs(
    seeded: TestClient,
):
    every = [item for item in items(seeded) if item["interview_at"]]
    # Seeded candidates choose: some interviews are video calls, the rest in person.
    assert {item["interview_mode"] for item in every} == {"onsite", "video"}
    booked = [item for item in every if item["interview_mode"] == "video"]
    assert all(item["recruiter_id"] and item["recruiter_name"] for item in booked)
    detail = seeded.get(f"/api/candidates/{booked[0]['id']}").json()
    assert detail["booking"]["recruiter_id"] == booked[0]["recruiter_id"]
    assert detail["booking"]["recruiter_name"] == booked[0]["recruiter_name"]
    assert detail["booking"]["mode"] == booked[0]["interview_mode"]

    owner = booked[0]["recruiter_id"]
    team = {r["id"]: r for r in seeded.get("/api/recruiters").json()}
    login = seeded.post("/api/auth/dev-login", json={"email": team[owner]["email"]})
    assert login.status_code == 200

    mine = items(seeded, recruiter_id="me")
    assert mine
    assert {item["recruiter_id"] for item in mine} == {owner}
    assert {item["id"] for item in mine} == {i["id"] for i in booked if i["recruiter_id"] == owner}
    assert items(seeded, recruiter_id=owner) == mine

    agenda = seeded.get("/api/interviews").json()
    video = [i for i in agenda if i["mode"] == "video"]
    assert {i["candidate_id"] for i in video} <= {item["id"] for item in mine}
    assert video, "the seeded bookings are upcoming"
    assert [i["starts_at"] for i in agenda] == sorted(i["starts_at"] for i in agenda)
    assert all(i["recruiter_id"] == owner and i["local_start"] for i in video)
    everything = seeded.get("/api/interviews", params={"include_past": True}).json()
    assert len([i for i in everything if i["mode"] == "video"]) == len(mine)


def test_the_agenda_shows_the_in_person_interviews_at_the_recruiters_locations(
    seeded: TestClient,
):
    """In person (SAZ-66): the location's manager runs it; a recruiter sees those at their
    locations, an admin all of them, and none is in their candidate filter."""
    onsite = [i for i in items(seeded) if i["interview_mode"] == "onsite"]
    assert onsite
    assert all(i["recruiter_id"] is None and i["interviewer_name"] for i in onsite)
    first = onsite[0]
    assert first["interviewer_email"] == f"gerencia.{first['location_id']}@gruposazon.example"
    detail = seeded.get(f"/api/candidates/{first['id']}").json()["booking"]
    assert detail["interviewer_name"] == first["interviewer_name"]
    assert detail["recruiter_id"] is None

    team = seeded.get("/api/recruiters").json()
    admin = next(r for r in team if r["is_admin"])
    seeded.post("/api/auth/dev-login", json={"email": admin["email"]})
    every = seeded.get("/api/interviews", params={"include_past": True}).json()
    assert {i["candidate_id"] for i in every if i["mode"] == "onsite"} == {i["id"] for i in onsite}
    covering = next(
        r for r in team if not r["is_admin"] and first["location_id"] in r["location_ids"]
    )
    theirs = seeded.get(
        "/api/interviews", params={"include_past": True, "recruiter_id": covering["id"]}
    ).json()
    in_person = [i for i in theirs if i["mode"] == "onsite"]
    assert first["id"] in {i["candidate_id"] for i in in_person}
    assert {i["location_id"] for i in in_person} <= set(covering["location_ids"])
    assert all(i["recruiter_id"] is None and i["interviewer_name"] for i in in_person)
    seeded.post("/api/auth/dev-login", json={"email": covering["email"]})
    assert seeded.get("/api/interviews", params={"include_past": True}).json() == theirs


def test_only_an_admin_reads_another_recruiters_agenda(
    client: TestClient, sign_in: Callable[..., Recruiter]
):
    admin = sign_in(client)
    recruiter = sign_in(client, admin=False)  # now signed in as them
    assert client.get("/api/interviews").status_code == 200
    assert client.get("/api/interviews", params={"recruiter_id": recruiter.id}).status_code == 200
    assert client.get("/api/interviews", params={"recruiter_id": admin.id}).status_code == 403

    sign_in(client)  # another admin
    assert client.get("/api/interviews", params={"recruiter_id": recruiter.id}).status_code == 200


def test_patch_sets_review_status_and_appends_notes(seeded: TestClient):
    candidate = items(seeded, status="qualified", review_status="new")[0]
    url = f"/api/candidates/{candidate['id']}"

    first = seeded.patch(url, json={"status": "contacted", "note": "Llamada el martes"}).json()
    second = seeded.patch(url, json={"note": "Confirma entrevista"}).json()

    assert first["review_status"] == second["review_status"] == "contacted"
    assert [n["text"] for n in second["notes"]] == ["Llamada el martes", "Confirma entrevista"]
    assert candidate["id"] in {r["id"] for r in items(seeded, review_status="contacted")}
    hired = seeded.patch(url, json={"status": "hired"}).json()
    assert hired["review_status"] == "hired"
    assert hired["status"] == "qualified"


@pytest.mark.parametrize("body", [{}, {"status": "promoted"}, {"note": ""}])
def test_patch_rejects_bad_bodies(seeded: TestClient, body: dict[str, Any]):
    candidate = items(seeded)[0]
    response = seeded.patch(f"/api/candidates/{candidate['id']}", json=body)
    assert response.status_code == 422


def test_unknown_candidate_is_404(seeded: TestClient):
    assert seeded.get("/api/candidates/nope").status_code == 404
    assert seeded.patch("/api/candidates/nope", json={"status": "hired"}).status_code == 404
    assert seeded.get("/api/candidates/nope/export").status_code == 404


def test_the_list_gives_the_interview_in_the_locations_time(seeded: TestClient):
    """The mobile card shows the interview at the location's time, not the viewer's."""
    booked = [item for item in items(seeded) if item["interview_at"]]
    assert booked
    for item in booked:
        local = datetime.fromisoformat(item["interview_local_start"])
        assert local.utcoffset() is not None
        assert local == datetime.fromisoformat(item["interview_at"])
    without = next(item for item in items(seeded) if not item["interview_at"])
    assert without["interview_local_start"] is None


def test_export_contains_every_brief_field(seeded: TestClient):
    complete = [c for c in items(seeded, status="qualified") if not c["quality_flags"]]
    candidate = complete[0]
    export = seeded.get(f"/api/candidates/{candidate['id']}/export").json()

    profile = export["profile"]
    for name in BRIEF_FIELDS:
        assert name in profile
        assert profile[name] is not None, name
    assert profile["phone"].startswith("+")
    assert "@" in profile["email"]
    assert export["status"] == "qualified"
    assert export["summary"] is not None
    assert export["conversations"][0]["turns"]


# --- scope: a recruiter's locations (SAZ-70) ---


def candidate_urls(candidate_id: str) -> list[str]:
    return [f"/api/candidates/{candidate_id}", f"/api/candidates/{candidate_id}/export"]


def place(
    service: ScreeningService,
    candidate_id: str,
    *,
    location_id: str | None,
    posting_location_id: str | None = None,
    country: str | None = None,
) -> None:
    with service.repo.session() as session:
        candidate = session.get(Candidate, candidate_id)
        assert candidate is not None
        candidate.location_id = location_id
        candidate.posting_location_id = posting_location_id
        if country is not None:
            candidate.country = country
        session.add(candidate)
        session.commit()


def test_a_recruiter_sees_and_acts_only_on_the_candidates_at_their_locations(
    seeded: TestClient, service: ScreeningService, sign_in: Callable[..., Recruiter]
):
    everyone = items(seeded)  # as an admin: every candidate, those without a location too
    mine = Counter(item["location_id"] for item in everyone if item["location_id"]).most_common(1)
    location = mine[0][0]
    locations = load_locations()
    country = locations[location].country
    elsewhere = next(
        lid for lid, loc in locations.items() if loc.country == country and lid != location
    )
    abroad = "MX" if country == "ES" else "ES"
    for item in everyone:  # the seed's own candidates without a location: out of scope here
        if item["location_id"] is None:
            place(service, item["id"], location_id=None, posting_location_id=elsewhere)
    others = [item["id"] for item in everyone if item["location_id"] not in (location, None)]
    # Closed before the city question, or out of area: placed by their posting, else country.
    from_posting, from_other_posting, in_country, out_of_country, outside = others[:5]
    place(service, from_posting, location_id=None, posting_location_id=location)
    place(service, from_other_posting, location_id=None, posting_location_id=elsewhere)
    place(service, in_country, location_id=None, country=country)
    place(service, out_of_country, location_id=None, country=abroad)
    located = {item["id"] for item in everyone if item["location_id"] == location}
    inside = located | {from_posting, in_country}
    with service.repo.session() as session:
        for candidate_id in (next(iter(located)), outside, from_other_posting):
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            candidate.talent_pool = True
            session.add(candidate)
        session.commit()

    sign_in(seeded, admin=False, location_ids=[location])
    page = seeded.get("/api/candidates", params={"page_size": 100}).json()
    assert {item["id"] for item in page["items"]} == inside
    assert page["total"] == page["counts"]["all"] == len(inside)
    assert sum(page["counts"][status] for status in page["counts"] if status != "all") == len(
        inside
    )
    assert {item["id"] for item in items(seeded, talent_pool=True)} <= inside
    assert len(items(seeded, talent_pool=True)) == 1
    outside_location = next(item["location_id"] for item in everyone if item["id"] == outside)
    assert items(seeded, location_id=outside_location) == []

    for shown in (next(iter(located)), from_posting, in_country):
        for url in candidate_urls(shown):
            assert seeded.get(url).status_code == 200, url
        patched = seeded.patch(f"/api/candidates/{shown}", json={"note": "Llamar"})
        assert patched.status_code == 200
    for hidden in (outside, from_other_posting, out_of_country):
        for url in candidate_urls(hidden):
            response = seeded.get(url)
            assert response.status_code == 404, url
            assert response.json() == seeded.get("/api/candidates/nope").json()
        patch = seeded.patch(f"/api/candidates/{hidden}", json={"status": "hired", "note": "x"})
        assert patch.status_code == 404
    sign_in(seeded)
    assert seeded.get(f"/api/candidates/{outside}").json()["notes"] == []
    assert seeded.get(f"/api/candidates/{outside}").json()["review_status"] == "new"


def test_a_candidate_keeps_the_postings_location_for_the_scope(
    client: TestClient, service: ScreeningService
):
    """A candidate closed before the city has no location of their own; the posting's is kept
    for the recruiters' scope, and a hint naming no location is not."""
    location = next(iter(load_locations()))
    for hint, kept in ((location, location), ("nowhere", None)):
        response = client.post(
            "/api/conversations", json={"context": {**CONTEXT, "location_id": hint}}
        )
        conversation_id = response.json()["conversation_id"]
        send(client, conversation_id, tap("quick_replies", "yes"))
        conversation = service.repo.get_conversation(conversation_id)
        assert conversation is not None
        assert conversation.candidate_id is not None
        candidate = service.repo.get_candidate(conversation.candidate_id)
        assert candidate is not None
        assert candidate.location_id is None
        assert candidate.posting_location_id == kept


def test_a_recruiter_without_locations_sees_no_one(
    seeded: TestClient, sign_in: Callable[..., Recruiter]
):
    anyone = items(seeded)[0]["id"]
    sign_in(seeded, admin=False, location_ids=[])
    page = seeded.get("/api/candidates").json()
    assert page["items"] == []
    assert page["counts"]["all"] == page["total"] == 0
    assert seeded.get(f"/api/candidates/{anyone}").status_code == 404
    assert seeded.get("/api/analytics").json()["totals"]["started"] == 0


def test_a_recruiter_still_sees_their_video_interviews_outside_their_locations(
    seeded: TestClient, service: ScreeningService, sign_in: Callable[..., Recruiter]
):
    """Video interviews go to a recruiter covering the slot's location; an admin may change
    their locations afterwards, and the interview stays theirs."""
    video = next(item for item in items(seeded) if item["interview_mode"] == "video")
    recruiter = sign_in(seeded, admin=False, location_ids=[], interview_modes=["video"])
    assert seeded.get(f"/api/candidates/{video['id']}").status_code == 404
    with service.repo.session() as session:
        booking = session.exec(
            select(Booking)
            .where(col(Booking.candidate_id) == video["id"])
            .where(col(Booking.status) == BookingStatus.BOOKED)
        ).one()
        booking.recruiter_id = recruiter.id
        session.add(booking)
        session.commit()

    assert [item["id"] for item in items(seeded)] == [video["id"]]
    assert [item["id"] for item in items(seeded, recruiter_id="me")] == [video["id"]]
    for url in candidate_urls(video["id"]):
        assert seeded.get(url).status_code == 200, url


def test_analytics_count_only_the_recruiters_locations(
    seeded: TestClient,
    service: ScreeningService,
    sign_in: Callable[..., Recruiter],
    now: datetime,
):
    every = seeded.get("/api/analytics").json()["totals"]
    located = sorted({item["location_id"] for item in items(seeded) if item["location_id"]})
    first, rest = located[:1], located[1:]
    sign_in(seeded, admin=False, location_ids=first)
    mine = seeded.get("/api/analytics").json()["totals"]
    sign_in(seeded, admin=False, location_ids=rest)
    others = seeded.get("/api/analytics").json()["totals"]

    assert 0 < mine["started"] < every["started"]
    assert (
        mine
        == compute_analytics(
            service.repo, Filters(location_ids=frozenset(first)), now
        ).totals.model_dump()
    )
    # Declined consents have no candidate: the admins' only.
    assert every["declined_consent"] > 0
    assert mine["declined_consent"] == others["declined_consent"] == 0
    assert mine["started"] + others["started"] <= every["started"]
    sign_in(seeded)
    assert seeded.get("/api/analytics").json()["totals"] == every


def test_the_schedule_filter_counts_flexible_candidates_and_any_block():
    from sazon.api.recruiter import _fits_schedule  # pyright: ignore[reportPrivateUsage]
    from sazon.engine.models import Schedule

    assert _fits_schedule([Schedule.MORNING, Schedule.EVENING], Schedule.EVENING)
    assert _fits_schedule([Schedule.FLEXIBLE], Schedule.MORNING)
    assert not _fits_schedule([Schedule.AFTERNOON], Schedule.MORNING)
    assert not _fits_schedule(None, Schedule.MORNING)
