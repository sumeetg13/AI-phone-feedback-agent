"""MongoDB access. Feedback forms live in `feedbacks`; people to call live in `call_contacts`."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from . import config

_client: Optional[AsyncMongoClient] = None


def now() -> datetime:
    return datetime.now(timezone.utc)


async def connect() -> AsyncDatabase:
    global _client
    _client = AsyncMongoClient(config.mongodb_uri(), tz_aware=True)
    db = get_db()
    await db.feedbacks.create_index([("email", 1), ("course_id", 1)], unique=True)
    await db.call_contacts.create_index([("email", 1), ("course_id", 1)], unique=True)
    await db.call_contacts.create_index("call_token", sparse=True)
    return db


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_db() -> AsyncDatabase:
    """FastAPI dependency."""
    if _client is None:
        raise RuntimeError("Database is not connected")
    return _client[config.mongodb_db()]


def object_id(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo document -> JSON-friendly dict (`_id` becomes `id`, datetimes become ISO strings)."""
    out = {k: (v.isoformat(timespec="seconds") if isinstance(v, datetime) else v) for k, v in doc.items()}
    out["id"] = str(out.pop("_id"))
    return out


# --- feedbacks ---------------------------------------------------------------


async def insert_feedback(db: AsyncDatabase, data: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Raises pymongo.errors.DuplicateKeyError if this email already reviewed this course."""
    doc = dict(data, email=data["email"].lower(), source=source, created_at=now())
    result = await db.feedbacks.insert_one(doc)
    return {"id": str(result.inserted_id), "created_at": doc["created_at"].isoformat(timespec="seconds")}


async def list_feedback(
    db: AsyncDatabase, course_id: Optional[str], limit: int, offset: int
) -> List[Dict[str, Any]]:
    query = {"course_id": course_id} if course_id else {}
    cursor = db.feedbacks.find(query).sort("_id", -1).skip(offset).limit(limit)
    return [serialize(doc) async for doc in cursor]


def _count_if(condition: Dict[str, Any]) -> Dict[str, Any]:
    return {"$sum": {"$cond": [condition, 1, 0]}}


async def summarize(db: AsyncDatabase, by_course: bool) -> List[Dict[str, Any]]:
    """One summary row per course, or a single all-courses row when by_course is False."""
    group = {
        "_id": "$course_id" if by_course else None,
        "responses": {"$sum": 1},
        "avg_overall": {"$avg": "$overall_rating"},
        "avg_relevance": {"$avg": "$relevance_rating"},
        "avg_confidence": {"$avg": "$confidence_rating"},
        "promoters": _count_if({"$gte": ["$recommend_score", 9]}),
        "detractors": _count_if({"$lte": ["$recommend_score", 6]}),
        "too_slow": _count_if({"$eq": ["$pace", "too_slow"]}),
        "just_right": _count_if({"$eq": ["$pace", "just_right"]}),
        "too_fast": _count_if({"$eq": ["$pace", "too_fast"]}),
    }
    cursor = await db.feedbacks.aggregate([{"$group": group}, {"$sort": {"_id": 1}}])
    rows = [
        {
            "course_id": g["_id"],
            "responses": g["responses"],
            "avg_overall": round(g["avg_overall"], 2),
            "avg_relevance": round(g["avg_relevance"], 2),
            "avg_confidence": round(g["avg_confidence"], 2),
            "nps": round(100 * (g["promoters"] - g["detractors"]) / g["responses"]),
            "pace": {k: g[k] for k in ("too_slow", "just_right", "too_fast")},
        }
        async for g in cursor
    ]
    if not by_course and not rows:
        rows = [
            {
                "course_id": None,
                "responses": 0,
                "avg_overall": None,
                "avg_relevance": None,
                "avg_confidence": None,
                "nps": None,
                "pace": {"too_slow": 0, "just_right": 0, "too_fast": 0},
            }
        ]
    return rows
