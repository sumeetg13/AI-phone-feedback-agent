import pytest

from conftest import ADMIN


def payload(**overrides):
    base = {
        "name": "Asha Rao",
        "email": "Asha@example.com",
        "course_id": "ai-agents-software-engineering",
        "job_role": "Backend engineer",
        "overall_rating": 5,
        "relevance_rating": 4,
        "confidence_rating": 4,
        "pace": "just_right",
        "recommend_score": 9,
        "key_learning": "Breaking work into agent-sized tasks.",
        "improvement": "",
        "testimonial_consent": True,
    }
    base.update(overrides)
    return base


def test_courses(client):
    res = client.get("/courses")
    assert res.status_code == 200
    assert any(c["id"] == "ai-agents-software-engineering" for c in res.json())


def test_submit_and_list(client):
    res = client.post("/feedback", json=payload())
    assert res.status_code == 201, res.text
    assert isinstance(res.json()["id"], str)

    rows = client.get("/admin/feedback", headers=ADMIN).json()
    assert len(rows) == 1
    assert rows[0]["email"] == "asha@example.com"
    assert rows[0]["testimonial_consent"] is True
    assert rows[0]["source"] == "web"
    assert rows[0]["id"] == res.json()["id"]


def test_duplicate_is_rejected(client):
    assert client.post("/feedback", json=payload()).status_code == 201
    assert client.post("/feedback", json=payload(email="ASHA@example.com")).status_code == 409


@pytest.mark.parametrize(
    "bad",
    [
        {"overall_rating": 6},
        {"recommend_score": 11},
        {"email": "not-an-email"},
        {"pace": "warp"},
        {"key_learning": "   "},
        {"course_id": "nope"},
    ],
)
def test_validation(client, bad):
    assert client.post("/feedback", json=payload(**bad)).status_code == 422


def test_admin_requires_token(client, monkeypatch):
    assert client.get("/admin/feedback").status_code == 401
    assert client.get("/admin/summary", headers={"X-Admin-Token": "wrong"}).status_code == 401
    monkeypatch.delenv("FEEDBACK_ADMIN_TOKEN")
    assert client.get("/admin/feedback", headers=ADMIN).status_code == 503


def test_summary_when_empty(client):
    s = client.get("/admin/summary", headers=ADMIN).json()
    assert s["overall"]["responses"] == 0 and s["overall"]["nps"] is None
    assert s["by_course"] == []


def test_summary(client):
    client.post("/feedback", json=payload(email="a@example.com", recommend_score=10, overall_rating=5))
    client.post("/feedback", json=payload(email="b@example.com", recommend_score=9, overall_rating=4))
    client.post("/feedback", json=payload(email="c@example.com", recommend_score=7, pace="too_fast"))
    client.post(
        "/feedback",
        json=payload(email="d@example.com", recommend_score=3, course_id="ai-agents-devops"),
    )
    s = client.get("/admin/summary", headers=ADMIN).json()
    assert s["overall"]["responses"] == 4
    assert s["overall"]["nps"] == 25  # (2 promoters - 1 detractor) / 4
    assert s["overall"]["pace"] == {"too_slow": 0, "just_right": 3, "too_fast": 1}
    by_course = {c["course_id"]: c for c in s["by_course"]}
    assert by_course["ai-agents-devops"]["nps"] == -100
    assert by_course["ai-agents-software-engineering"]["responses"] == 3
