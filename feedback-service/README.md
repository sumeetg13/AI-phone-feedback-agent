# feedback-service

FastAPI + MongoDB backend for DotAI Labs AI Certificate course feedback. Feedback arrives two
ways: the web form, and phone calls made by an AI agent that asks the form's questions and
submits the form on the person's behalf. Both end up in the `feedbacks` collection.

## Run

Needs Python 3.10+ and a MongoDB to connect to.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # then fill it in
.venv/bin/uvicorn app.main:app --reload --port 8010
```

Interactive API docs: http://localhost:8010/docs

## Configuration

See `.env.example` for every setting. The web form and results only need
`FEEDBACK_ADMIN_TOKEN` and MongoDB. Phone calls additionally need Twilio credentials, a public
URL, and an API key for the conversation model.

`FEEDBACK_AGENT_PROVIDER` chooses who runs the phone conversation: `openai` (needs
`OPENAI_API_KEY`) or `claude` (needs `ANTHROPIC_API_KEY`). Both use the same instructions and
tools; `FEEDBACK_AGENT_MODEL` overrides the provider's default model.

Edit `courses.json` to change the courses; it is re-read on every request. Keep a course's `id`
stable once feedback exists for it.

## Phone calls

1. In the admin UI, upload a CSV with the columns `name,email,course,phoneNum`. `course` is a
   course id or its exact title; `phoneNum` includes the country code (`+919876543210`).
2. Press **Call N people**. Each person is phoned through Twilio; the agent introduces itself
   as an AI, asks the next unanswered question on that person's form, and saves each answer as
   it is given.
3. When the last question is answered the form is submitted to `feedbacks` with
   `source: "call"`. If a call drops midway the answers so far are kept, and calling that person
   again resumes at the next unanswered question.

People who already submitted feedback for a course are skipped on upload, and people who
declined are not called again.

### Setting up Twilio

- Buy or use a voice-capable number and set it as `TWILIO_FROM_NUMBER`.
- Enable Conversation Relay: Twilio Console > Voice > Settings > Privacy & Security, accept the
  "Predictive and Generative AI/ML Features Addendum".
- Twilio must be able to reach this service. For local development run a tunnel
  (`ngrok http 8010`) and set `PUBLIC_BASE_URL` to its https address.
- Trial accounts can only call verified numbers.

### Trying the agent without a phone

```bash
.venv/bin/python -m app.calls.simulate --name "Asha Rao" --course ai-agents-devops
```

You type what the person would say. It needs MongoDB and the selected provider's API key but not Twilio, and
it writes to a separate `<MONGODB_DB>_simulation` database.

### Where things live

| File | What it does |
| --- | --- |
| `app/calls/form.py` | The questions, their order, and answer validation. Change the script here. |
| `app/calls/agent.py` | The agent's instructions and its two tools, shared by both providers. |
| `app/calls/agent_openai.py`, `agent_claude.py` | The model API call for each provider. |
| `app/calls/session.py` | One conversation: saves answers, submits the form, records the outcome. |
| `app/calls/telephony.py` | Placing calls with Twilio and checking Twilio's request signatures. |
| `app/calls/contacts.py` | CSV import and the `call_contacts` collection. |
| `app/calls/routes.py` | Admin endpoints, Twilio's status callback, and the call WebSocket. |

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | |
| GET | `/courses` | Course list for the form. |
| POST | `/feedback` | Submit the web form. One response per email per course (409 on a repeat). |
| GET | `/admin/feedback` | All responses, newest first. `course_id`, `limit`, `offset` query params. |
| GET | `/admin/summary` | Averages, NPS and pace split, overall and per course. |
| POST | `/admin/calls/contacts/upload` | Upload the CSV of people to call. |
| GET | `/admin/calls/contacts` | People, their call status and form progress. |
| GET | `/admin/calls/contacts/{id}` | One person's answers so far and call transcript. |
| PATCH | `/admin/calls/contacts/{id}` | Correct a person's name, email, course or phone. |
| DELETE | `/admin/calls/contacts/{id}` | Remove a person and their call history. Submitted feedback is kept. |
| POST | `/admin/calls/start` | Start calling. Body `{}` calls everyone eligible; `{"contact_ids": [...]}` a subset. |
| POST | `/voice/status/{id}` | Called by Twilio when a call ends. |
| WS | `/voice/ws/{token}` | Called by Twilio for the live conversation. |

`/admin/*` needs the `X-Admin-Token` header. `/voice/*` only accepts requests signed by Twilio.

## Tests

```bash
.venv/bin/python -m pytest
```

The tests use a real MongoDB at `MONGODB_URI`, each in its own throwaway database. Twilio and
the model APIs are replaced with stand-ins, so no calls are placed and nothing is billed.
