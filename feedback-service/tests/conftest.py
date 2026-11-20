"""Tests run against a real MongoDB (MONGODB_URI, default localhost) in a throwaway database."""
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app import config

ADMIN = {"X-Admin-Token": "test-token"}


@pytest.fixture()
def mongo(monkeypatch):
    """Sync handle on the throwaway database the app under test uses."""
    name = f"dotai_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("MONGODB_DB", name)
    monkeypatch.setenv("FEEDBACK_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-test-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://feedback.test")
    monkeypatch.setenv("FEEDBACK_AGENT_PROVIDER", "claude")
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_FROM_NUMBER", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CALL_DEFAULT_COUNTRY_CODE", "CALL_LANGUAGE", "OPENAI_API_KEY", "FEEDBACK_AGENT_MODEL",
                "FEEDBACK_AGENT_EFFORT", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET"):
        monkeypatch.delenv(var, raising=False)
    client = MongoClient(config.mongodb_uri(), tz_aware=True, serverSelectionTimeoutMS=3000)
    yield client[name]
    client.drop_database(name)
    client.close()


@pytest.fixture()
def client(mongo):
    from app.main import app

    with TestClient(app) as c:
        yield c
