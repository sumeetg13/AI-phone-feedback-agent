"""The feedback form as the phone agent sees it: an ordered list of questions.

An answer is "given" once its field is present in the contact's `answers` dict. Optional
questions may be answered with None (asked, but the person had nothing to say). The next
question to ask is always the first one, in this order, that has no answer yet.
"""
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from ..schemas import FeedbackIn

QUESTIONS: List[Dict[str, Any]] = [
    {
        "field": "overall_rating",
        "question": "Overall, how would you rate the course, from 1 (poor) to 5 (excellent)?",
        "schema": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    {
        "field": "relevance_rating",
        "question": "How relevant was the course to your engineering domain, from 1 (not relevant) to 5 (spot on)?",
        "schema": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    {
        "field": "confidence_rating",
        "question": "How confident are you now using AI agents in your day-to-day work, from 1 (not confident) to 5 (very confident)?",
        "schema": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    {
        "field": "pace",
        "question": "How was the pace for a 10-hour course: too slow, just right, or too fast?",
        "schema": {"type": "string", "enum": ["too_slow", "just_right", "too_fast"]},
    },
    {
        "field": "recommend_score",
        "question": "How likely are you to recommend this certificate to a colleague, from 0 (not at all) to 10 (extremely likely)?",
        "schema": {"type": "integer", "minimum": 0, "maximum": 10},
    },
    {
        "field": "key_learning",
        "question": "What is the most valuable thing you learned?",
        "schema": {"type": "string"},
    },
    {
        "field": "improvement",
        "question": "What should we improve?",
        "schema": {"type": ["string", "null"]},
        "optional": True,
    },
    {
        "field": "job_role",
        "question": "What is your current role?",
        "schema": {"type": ["string", "null"]},
        "optional": True,
    },
    {
        "field": "testimonial_consent",
        "question": "May DotAI Labs quote your feedback, with your name, as a testimonial? (yes or no)",
        "schema": {"type": "boolean"},
    },
]

FIELDS = [q["field"] for q in QUESTIONS]
_OPTIONAL = {q["field"] for q in QUESTIONS if q.get("optional")}

# Valid placeholder values, so one field at a time can be checked against FeedbackIn's rules.
_BASE = {
    "name": "x",
    "email": "x@example.com",
    "course_id": "x",
    "overall_rating": 1,
    "relevance_rating": 1,
    "confidence_rating": 1,
    "pace": "just_right",
    "recommend_score": 0,
    "key_learning": "x",
}


def next_question(answers: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for q in QUESTIONS:
        if q["field"] not in answers:
            return {"field": q["field"], "question": q["question"], "optional": bool(q.get("optional"))}
    return None


def validate_answers(raw: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Check each proposed answer on its own. Returns (accepted, rejected-with-reason)."""
    if not isinstance(raw, dict):
        return {}, {"input": "expected an object of field -> answer"}
    accepted: Dict[str, Any] = {}
    rejected: Dict[str, str] = {}
    for field, value in raw.items():
        if field not in FIELDS:
            rejected[field] = "not a form field"
        elif value is None or (isinstance(value, str) and not value.strip()):
            if field in _OPTIONAL:
                accepted[field] = None
            else:
                rejected[field] = "this question needs an answer"
        else:
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            try:
                # strict: a spoken "four" must arrive as the integer 4, not be coerced from text
                checked = FeedbackIn.model_validate(dict(_BASE, **{field: value}), strict=True)
                accepted[field] = getattr(checked, field)
            except ValidationError as e:
                rejected[field] = e.errors()[0]["msg"]
    return accepted, rejected


def build_feedback(contact: Dict[str, Any]) -> Dict[str, Any]:
    """The finished form for a contact whose questions are all answered."""
    form = FeedbackIn(
        name=contact["name"],
        email=contact["email"],
        course_id=contact["course_id"],
        **{f: contact["answers"][f] for f in FIELDS},
    )
    return form.model_dump()
