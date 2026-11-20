import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from app import config
from app.calls import agent_claude, agent_openai, contacts, form, routes, session, telephony
from app.deps import load_courses
from conftest import ADMIN

CSV = """name,email,course,phoneNum
Asha Rao,asha@example.com,ai-agents-devops,+919876543210
Ben Ode,BEN@example.com,AI Agents for Data Engineers,+1 (415) 555-0100
"""


def sign(url, params=None):
    return RequestValidator("twilio-test-token").compute_signature(url, params or {})


def add_contact(mongo, **overrides):
    doc = {
        "name": "Asha Rao",
        "email": "asha@example.com",
        "course_id": "ai-agents-devops",
        "phone": "+919876543210",
        "status": "calling",
        "call_token": "tok123",
        "answers": {},
        "transcript": [],
        "attempts": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    return mongo.call_contacts.insert_one(doc).inserted_id


class FakeStream:
    """Stands in for the Anthropic streaming call: replays one scripted model response."""

    def __init__(self, text, tools, stop_reason):
        self.text, self.tools, self.stop_reason = text, tools, stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for word in self.text.split(" ") if self.text else []:
            yield SimpleNamespace(type="text", text=word + " ")

    async def get_final_message(self):
        content = [SimpleNamespace(type="text", text=self.text)] if self.text else []
        for i, (name, tool_input) in enumerate(self.tools):
            content.append(SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=tool_input))
        return SimpleNamespace(content=content, stop_reason=self.stop_reason or ("tool_use" if self.tools else "end_turn"))


@pytest.fixture()
def script(monkeypatch):
    """Queue of model responses: (text, [(tool_name, input), ...]) or (text, tools, stop_reason)."""
    responses = []
    seen = []

    def fake_stream(messages):
        seen.append(list(messages))
        text, tools, *rest = responses.pop(0)
        return FakeStream(text, tools, rest[0] if rest else None)

    monkeypatch.setattr(agent_claude, "_stream", fake_stream)
    monkeypatch.setattr(session, "speech_seconds", lambda text: 0.0)
    responses_and_seen = SimpleNamespace(responses=responses, seen=seen)
    return responses_and_seen


def connect(client, token="tok123"):
    url = f"/voice/ws/{token}"
    return client.websocket_connect(url, headers={"X-Twilio-Signature": sign("wss://feedback.test" + url)})


def hear_reply(ws):
    """Collect the spoken tokens of one agent turn."""
    tokens = []
    while True:
        message = ws.receive_json()
        assert message["type"] == "text"
        tokens.append(message["token"])
        if message["last"]:
            return "".join(tokens).strip()


# --- CSV import --------------------------------------------------------------


def test_parse_csv_accepts_ids_titles_and_formatted_phones():
    rows, errors = contacts.parse_csv(CSV.encode(), load_courses())
    assert errors == []
    assert rows == [
        {"name": "Asha Rao", "email": "asha@example.com", "course_id": "ai-agents-devops", "phone": "+919876543210"},
        {"name": "Ben Ode", "email": "ben@example.com", "course_id": "ai-agents-data-engineering", "phone": "+14155550100"},
    ]


def test_parse_csv_reports_bad_rows_by_line():
    bad = (
        "﻿Name, Email ,Course,Phone Num\n"
        "Asha,asha@example.com,ai-agents-devops,+919876543210\n"
        ",not-an-email,ai-agents-devops,+919876543210\n"
        "Cy,cy@example.com,basket weaving,9876543210\n"
        "\n"
        "Asha again,ASHA@example.com,ai-agents-devops,+919876543210\n"
    )
    rows, errors = contacts.parse_csv(bad.encode(), load_courses())
    assert [r["email"] for r in rows] == ["asha@example.com"]
    assert [e["row"] for e in errors] == [3, 4, 6]
    assert "name is empty" in errors[0]["error"] and "email is not valid" in errors[0]["error"]
    assert "unknown course" in errors[1]["error"] and "country code" in errors[1]["error"]
    assert "duplicate" in errors[2]["error"]


def test_parse_csv_missing_column():
    rows, errors = contacts.parse_csv(b"name,email,course\nA,a@example.com,x\n", load_courses())
    assert rows == [] and "phone" in errors[0]["error"]


def test_default_country_code(monkeypatch):
    assert contacts.normalize_phone("98765 43210") is None
    monkeypatch.setenv("CALL_DEFAULT_COUNTRY_CODE", "+91")
    assert contacts.normalize_phone("098765 43210") == "+919876543210"


def test_upload_adds_updates_and_skips(client, mongo):
    upload = lambda body: client.post(
        "/admin/calls/contacts/upload", headers=ADMIN, files={"file": ("people.csv", body, "text/csv")}
    )
    assert client.post("/admin/calls/contacts/upload", files={"file": ("p.csv", CSV, "text/csv")}).status_code == 401

    assert upload(CSV).json() == {"added": 2, "updated": 0, "skipped": 0, "errors": []}
    # Ben already submitted the web form, so he should not be called.
    mongo.call_contacts.delete_one({"email": "ben@example.com"})
    mongo.feedbacks.insert_one({"email": "ben@example.com", "course_id": "ai-agents-data-engineering"})
    assert upload(CSV).json() == {"added": 0, "updated": 1, "skipped": 1, "errors": []}

    listing = client.get("/admin/calls/contacts", headers=ADMIN).json()
    assert [c["email"] for c in listing["contacts"]] == ["asha@example.com"]
    assert listing["contacts"][0]["status"] == "pending"
    assert listing["contacts"][0]["total_questions"] == len(form.FIELDS)
    assert "TWILIO_ACCOUNT_SID" in listing["missing_settings"]


# --- the form ----------------------------------------------------------------


def test_next_question_follows_form_order():
    answers = {}
    asked = []
    while (q := form.next_question(answers)) is not None:
        asked.append(q["field"])
        answers[q["field"]] = None
    assert asked == form.FIELDS
    assert form.next_question({"overall_rating": 5, "pace": "too_fast"})["field"] == "relevance_rating"


def test_validate_answers_checks_each_field_alone():
    accepted, rejected = form.validate_answers(
        {"overall_rating": 5, "relevance_rating": 9, "pace": "warp", "improvement": None, "key_learning": " ", "nope": 1}
    )
    assert accepted == {"overall_rating": 5, "improvement": None}
    assert set(rejected) == {"relevance_rating", "pace", "key_learning", "nope"}


# --- a whole call ------------------------------------------------------------


def test_call_fills_and_submits_the_form(client, mongo, script):
    contact_id = add_contact(mongo)
    script.responses.extend(
        [
            ("Great. Overall, how would you rate the course from 1 to 5?", []),
            ("", [("save_answers", {"overall_rating": 5, "relevance_rating": 4, "confidence_rating": 44})]),
            ("Sorry, confidence is on a scale of 1 to 5. Which number?", []),
            (
                "",
                [
                    (
                        "save_answers",
                        {
                            "confidence_rating": 4,
                            "pace": "just_right",
                            "recommend_score": 9,
                            "key_learning": "Scoping tasks for agents",
                            "improvement": None,
                            "job_role": "SRE",
                            "testimonial_consent": True,
                        },
                    )
                ],
            ),
            ("That's everything, thank you so much. Goodbye!", [("end_call", {"outcome": "completed"})]),
        ]
    )
    with connect(client) as ws:
        ws.send_json({"type": "setup", "callSid": "CA1"})
        ws.send_json({"type": "prompt", "voicePrompt": "Sure, go ahead", "last": True})
        assert hear_reply(ws).startswith("Great. Overall")

        ws.send_json({"type": "prompt", "voicePrompt": "five, four, and forty four", "last": True})
        assert "scale of 1 to 5" in hear_reply(ws)
        # The tool result told the model what was saved, what was rejected, and what to ask next.
        tool_result = script.seen[2][-1]["content"][0]["content"]
        assert '"saved": ["overall_rating", "relevance_rating"]' in tool_result
        assert '"confidence_rating"' in tool_result and '"field": "confidence_rating"' in tool_result

        ws.send_json({"type": "prompt", "voicePrompt": "four. just right. nine. scoping tasks. nothing. SRE. yes", "last": True})
        assert hear_reply(ws) == "That's everything, thank you so much. Goodbye!"
        assert ws.receive_json() == {"type": "end"}

    feedback = mongo.feedbacks.find_one({"email": "asha@example.com"})
    assert feedback["source"] == "call" and feedback["name"] == "Asha Rao"
    assert feedback["confidence_rating"] == 4 and feedback["improvement"] is None
    assert feedback["testimonial_consent"] is True and feedback["job_role"] == "SRE"

    contact = mongo.call_contacts.find_one({"_id": contact_id})
    assert contact["status"] == "completed" and contact["outcome"] == "completed"
    assert contact["call_sid"] == "CA1" and contact["call_token"] is None
    assert [t["role"] for t in contact["transcript"]] == ["agent", "caller", "agent", "caller", "agent", "caller", "agent"]
    assert {t["attempt"] for t in contact["transcript"]} == {1}

    detail = client.get(f"/admin/calls/contacts/{contact_id}", headers=ADMIN).json()
    assert detail["answered"] == len(form.FIELDS) and detail["transcript"][1]["text"] == "Sure, go ahead"
    assert client.get("/admin/feedback", headers=ADMIN).json()[0]["source"] == "call"


def test_resumed_call_starts_at_next_unanswered_question(client, mongo, script):
    earlier = {"role": "agent", "text": "Hi Asha...", "at": datetime.now(timezone.utc), "attempt": 1}
    contact_id = add_contact(mongo, answers={"overall_rating": 5, "relevance_rating": 4}, attempts=3, transcript=[earlier])
    script.responses.append(("No problem, goodbye.", [("end_call", {"outcome": "call_back_later"})]))
    with connect(client) as ws:
        ws.send_json({"type": "setup", "callSid": "CA3"})
        ws.send_json({"type": "prompt", "voicePrompt": "not now", "last": True})
        hear_reply(ws)
        assert ws.receive_json() == {"type": "end"}
    context, greeting = script.seen[0][0]["content"], script.seen[0][1]["content"]
    assert '"field": "confidence_rating"' in context and '"overall_rating": 5' in context
    assert "calling back to finish" in greeting
    contact = mongo.call_contacts.find_one({})
    assert contact["status"] == "incomplete" and contact["outcome"] == "call_back_later"
    assert contact["answers"] == {"overall_rating": 5, "relevance_rating": 4}
    # The new call is appended after the earlier one, each line tagged with its attempt number.
    lines = client.get(f"/admin/calls/contacts/{contact_id}", headers=ADMIN).json()["transcript"]
    assert [line["attempt"] for line in lines] == [1, 3, 3, 3]


def test_cannot_end_as_completed_before_submission(client, mongo, script):
    add_contact(mongo)
    script.responses.extend(
        [
            ("Thanks, bye!", [("end_call", {"outcome": "completed"})]),
            ("Actually, one more question: how would you rate the course?", []),
        ]
    )
    with connect(client) as ws:
        ws.send_json({"type": "prompt", "voicePrompt": "ok", "last": True})
        assert "one more question" in hear_reply(ws)
        assert '"error"' in script.seen[1][-1]["content"][0]["content"]
        assert script.seen[1][-1]["content"][0]["is_error"] is True
    assert mongo.call_contacts.find_one({})["status"] == "incomplete"  # caller hung up mid-form


def test_declined_and_model_failure(client, mongo, script):
    add_contact(mongo)
    script.responses.append(("", [("end_call", {"outcome": "declined"})]))
    with connect(client) as ws:
        ws.send_json({"type": "prompt", "voicePrompt": "no thanks", "last": True})
        assert hear_reply(ws) == session.GOODBYE  # the model said nothing, so a stock goodbye is spoken
        assert ws.receive_json() == {"type": "end"}
    assert mongo.call_contacts.find_one({})["status"] == "declined"

    mongo.call_contacts.update_one({}, {"$set": {"status": "calling", "call_token": "tok123"}})
    script.responses.append(("I", [], "refusal"))
    with connect(client) as ws:
        ws.send_json({"type": "prompt", "voicePrompt": "hello", "last": True})
        assert session.TROUBLE in hear_reply(ws)
        assert ws.receive_json() == {"type": "end"}
    contact = mongo.call_contacts.find_one({})
    assert contact["status"] == "failed" and "refusal" in contact["last_error"]


def test_silent_caller_is_nudged_then_released(client, mongo, script, monkeypatch):
    monkeypatch.setattr(routes, "SILENCE_SECONDS", 0.05)
    add_contact(mongo)
    with connect(client) as ws:
        assert hear_reply(ws) == session.STILL_THERE
        assert hear_reply(ws) == session.NO_RESPONSE
        assert ws.receive_json() == {"type": "end"}
    contact = mongo.call_contacts.find_one({})
    assert contact["status"] == "incomplete" and contact["outcome"] == "no_response"


def test_websocket_requires_twilio_signature_and_live_token(client, mongo):
    add_contact(mongo)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/voice/ws/tok123", headers={"X-Twilio-Signature": "forged"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with connect(client, token="unknown-token"):
            pass


# --- placing calls -----------------------------------------------------------


def test_status_callback(client, mongo):
    contact_id = add_contact(mongo)
    path = f"/voice/status/{contact_id}"
    params = {"CallSid": "CA1", "CallStatus": "no-answer"}
    assert client.post(path, data=params, headers={"X-Twilio-Signature": "forged"}).status_code == 403
    assert mongo.call_contacts.find_one({})["status"] == "calling"

    signature = sign("https://feedback.test" + path, params)
    assert client.post(path, data=params, headers={"X-Twilio-Signature": signature}).status_code == 204
    assert mongo.call_contacts.find_one({})["status"] == "no_answer"

    # A late callback never overwrites a status the conversation already settled.
    mongo.call_contacts.update_one({}, {"$set": {"status": "completed"}})
    params = {"CallSid": "CA1", "CallStatus": "completed"}
    client.post(path, data=params, headers={"X-Twilio-Signature": sign("https://feedback.test" + path, params)})
    assert mongo.call_contacts.find_one({})["status"] == "completed"


def test_start_calls(client, mongo, monkeypatch):
    pending = add_contact(mongo, status="pending", call_token=None, attempts=0)
    add_contact(mongo, email="done@example.com", status="completed", call_token=None)
    add_contact(mongo, email="no@example.com", status="declined", call_token=None)

    res = client.post("/admin/calls/start", headers=ADMIN, json={})
    assert res.status_code == 503 and "TWILIO_ACCOUNT_SID" in res.json()["detail"]

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    placed = []

    def fake_place_call(contact, token, course_title):
        placed.append((contact["phone"], telephony.twiml(contact, token, course_title)))
        return "CA999"

    monkeypatch.setattr(telephony, "place_call", fake_place_call)
    assert client.post("/admin/calls/start", headers=ADMIN, json={}).json() == {"queued": 1}

    for _ in range(50):
        contact = mongo.call_contacts.find_one({"_id": pending})
        if contact.get("call_sid"):
            break
        time.sleep(0.05)
    assert contact["status"] == "calling" and contact["attempts"] == 1 and contact["call_sid"] == "CA999"
    phone, twiml = placed[0]
    assert phone == "+919876543210" and len(placed) == 1
    assert f'url="wss://feedback.test/voice/ws/{contact["call_token"]}"' in twiml
    assert "AI Agents for DevOps &amp; Platform Engineers" in twiml and "Hi Asha" in twiml

    # Already in a call, so starting again does not dial a second time.
    assert client.post("/admin/calls/start", headers=ADMIN, json={}).json() == {"queued": 0}


def test_failed_dial_is_recorded(client, mongo, monkeypatch):
    contact_id = add_contact(mongo, status="pending", call_token=None, attempts=0)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def boom(*args):
        raise RuntimeError("The number +91... is not a valid phone number")

    monkeypatch.setattr(telephony, "place_call", boom)
    client.post("/admin/calls/start", headers=ADMIN, json={"contact_ids": [str(contact_id)]})
    for _ in range(50):
        contact = mongo.call_contacts.find_one({"_id": contact_id})
        if contact["status"] == "failed":
            break
        time.sleep(0.05)
    assert contact["status"] == "failed" and "not a valid phone number" in contact["last_error"]


# --- OpenAI as the conversation model ----------------------------------------


def openai_chunks(text, tools, finish):
    """Chat Completions stream chunks: text word by word, tool arguments split in two."""
    def chunk(content=None, tool_calls=None, finish_reason=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])

    async def gen():
        for word in text.split(" ") if text else []:
            yield chunk(content=word + " ")
        for i, (name, arguments) in enumerate(tools):
            half = len(arguments) // 2
            fn = SimpleNamespace(name=name, arguments=arguments[:half])
            yield chunk(tool_calls=[SimpleNamespace(index=i, id=f"call_{i}", function=fn)])
            fn = SimpleNamespace(name=None, arguments=arguments[half:])
            yield chunk(tool_calls=[SimpleNamespace(index=i, id=None, function=fn)])
        yield SimpleNamespace(choices=[])  # usage-only chunk
        yield chunk(finish_reason=finish or ("tool_calls" if tools else "stop"))

    return gen()


def test_openai_call_fills_and_submits_the_form(client, mongo, monkeypatch):
    monkeypatch.setenv("FEEDBACK_AGENT_PROVIDER", "openai")
    monkeypatch.setattr(session, "speech_seconds", lambda text: 0.0)
    answers = {
        "overall_rating": 4, "relevance_rating": 5, "confidence_rating": 4, "pace": "too_fast",
        "recommend_score": 8, "key_learning": "Writing specs for agents", "improvement": "More labs",
        "job_role": None, "testimonial_consent": False,
    }
    responses = [
        ("Great. How would you rate the course from 1 to 5?", [], None),
        ("", [("save_answers", "{not json")], None),
        ("", [("save_answers", json.dumps(answers))], None),
        ("Thank you, that is everything. Goodbye!", [("end_call", json.dumps({"outcome": "completed"}))], None),
    ]
    seen = []

    async def fake_stream(messages):
        seen.append(list(messages))
        return openai_chunks(*responses.pop(0))

    monkeypatch.setattr(agent_openai, "_stream", fake_stream)
    add_contact(mongo)
    with connect(client) as ws:
        ws.send_json({"type": "prompt", "voicePrompt": "sure", "last": True})
        assert hear_reply(ws).startswith("Great. How would you rate")
        ws.send_json({"type": "prompt", "voicePrompt": "four, five, four, too fast, eight...", "last": True})
        assert hear_reply(ws) == "Thank you, that is everything. Goodbye!"
        assert ws.receive_json() == {"type": "end"}

    # History is in OpenAI's format: assistant tool_calls answered by role=tool messages.
    history = seen[-1]
    assert history[0]["role"] == "user" and "<call_context>" in history[0]["content"]
    bad, good = [m for m in history if m["role"] == "tool"]
    assert "not valid JSON" in bad["content"] and bad["tool_call_id"] == "call_0"
    assert json.loads(good["content"])["form_submitted"] is True
    assert history[-2]["tool_calls"][0]["function"]["arguments"] == json.dumps(answers)

    feedback = mongo.feedbacks.find_one({})
    assert feedback["source"] == "call" and feedback["pace"] == "too_fast" and feedback["job_role"] is None
    assert mongo.call_contacts.find_one({})["status"] == "completed"


def test_openai_truncated_reply_ends_the_call_politely(client, mongo, monkeypatch):
    monkeypatch.setenv("FEEDBACK_AGENT_PROVIDER", "openai")
    monkeypatch.setattr(session, "speech_seconds", lambda text: 0.0)

    async def fake_stream(messages):
        return openai_chunks("Well", [], "length")

    monkeypatch.setattr(agent_openai, "_stream", fake_stream)
    add_contact(mongo)
    with connect(client) as ws:
        ws.send_json({"type": "prompt", "voicePrompt": "hello", "last": True})
        assert session.TROUBLE in hear_reply(ws)
        assert ws.receive_json() == {"type": "end"}
    assert mongo.call_contacts.find_one({})["status"] == "failed"


def test_openai_request_shape(monkeypatch):
    monkeypatch.setenv("FEEDBACK_AGENT_PROVIDER", "openai")
    tools = {t["function"]["name"]: t["function"] for t in agent_openai._TOOLS}
    assert set(tools) == {"save_answers", "end_call"}
    assert tools["save_answers"]["parameters"]["properties"]["pace"]["enum"] == ["too_slow", "just_right", "too_fast"]
    assert "eager_input_streaming" not in json.dumps(agent_openai._TOOLS)
    assert config.agent_model() == "gpt-6.1-terra"
    monkeypatch.setenv("FEEDBACK_AGENT_MODEL", "gpt-6-luna")
    assert config.agent_model() == "gpt-6-luna"


def test_provider_flag_decides_which_key_is_required(monkeypatch, mongo):
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_FROM_NUMBER"):
        monkeypatch.setenv(var, "x")
    assert config.missing_call_settings() == ["ANTHROPIC_API_KEY"]
    monkeypatch.setenv("FEEDBACK_AGENT_PROVIDER", "OpenAI")
    assert config.missing_call_settings() == ["OPENAI_API_KEY"]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert config.missing_call_settings() == []
    monkeypatch.setenv("FEEDBACK_AGENT_PROVIDER", "gemini")
    assert "FEEDBACK_AGENT_PROVIDER" in config.missing_call_settings()[0]


def test_delete_contact(client, mongo):
    on_call = add_contact(mongo)
    done = add_contact(mongo, email="done@example.com", status="completed", call_token=None)
    mongo.feedbacks.insert_one({"email": "done@example.com", "course_id": "ai-agents-devops"})

    assert client.delete(f"/admin/calls/contacts/{done}").status_code == 401
    assert client.delete(f"/admin/calls/contacts/{on_call}", headers=ADMIN).status_code == 409
    assert client.delete(f"/admin/calls/contacts/{done}", headers=ADMIN).status_code == 204
    assert client.delete(f"/admin/calls/contacts/{done}", headers=ADMIN).status_code == 404
    assert client.delete("/admin/calls/contacts/not-an-id", headers=ADMIN).status_code == 404

    assert [c["email"] for c in mongo.call_contacts.find()] == ["asha@example.com"]
    assert mongo.feedbacks.count_documents({}) == 1  # submitted feedback is untouched


def test_edit_contact(client, mongo):
    contact_id = add_contact(mongo, status="incomplete", call_token=None, answers={"overall_rating": 5})
    other = add_contact(mongo, email="other@example.com", status="pending", call_token=None)
    edit = lambda cid, **changes: client.patch(
        f"/admin/calls/contacts/{cid}",
        headers=ADMIN,
        json={"name": "Asha Rao", "email": "asha@example.com", "course_id": "ai-agents-devops",
              "phone": "+919876543210", **changes},
    )
    assert client.patch(f"/admin/calls/contacts/{contact_id}", json={}).status_code == 401

    # Name and phone are corrected in place; progress on the form is kept.
    res = edit(contact_id, name="  Asha R. Rao ", phone="+91 98765 00000")
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Asha R. Rao" and res.json()["phone"] == "+919876500000"
    assert res.json()["answered"] == 1 and res.json()["status"] == "incomplete"

    assert edit(contact_id, phone="12345").status_code == 422
    assert edit(contact_id, email="nope").status_code == 422
    assert edit(contact_id, course_id="basket-weaving").status_code == 422
    assert edit(contact_id, email="OTHER@example.com").status_code == 409  # clashes with another person
    assert edit("not-an-id").status_code == 404

    # Moving to a different course drops answers that were about the old one.
    res = edit(contact_id, name="Asha R. Rao", course_id="ai-agents-qa")
    assert res.json()["course_id"] == "ai-agents-qa" and res.json()["answered"] == 0

    # Cannot point someone at an email + course that already has feedback.
    mongo.feedbacks.insert_one({"email": "new@example.com", "course_id": "ai-agents-qa"})
    assert edit(contact_id, email="new@example.com", course_id="ai-agents-qa").status_code == 409

    # Once feedback is submitted, identity is locked but name and phone can still be fixed.
    mongo.call_contacts.update_one({"_id": other}, {"$set": {"status": "completed"}})
    assert edit(other, email="other@example.com", course_id="ai-agents-qa").status_code == 409
    assert edit(other, email="other@example.com", name="Other Person").json()["name"] == "Other Person"

    mongo.call_contacts.update_one({"_id": other}, {"$set": {"status": "calling"}})
    assert edit(other, email="other@example.com").status_code == 409
