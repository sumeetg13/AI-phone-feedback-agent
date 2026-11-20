"""People to call: CSV import and the `call_contacts` collection."""
import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import EmailStr, TypeAdapter, ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from .. import config, db
from ..schemas import Course
from .form import FIELDS

MAX_ROWS = 2000
# Statuses from which a contact may be (re)dialled. `declined` is deliberately absent.
CALLABLE = ["pending", "queued", "incomplete", "no_answer", "failed"]

_EMAIL = TypeAdapter(EmailStr)
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_HEADER_ALIASES = {
    "name": "name",
    "email": "email",
    "course": "course",
    "phonenum": "phone",
    "phone": "phone",
    "phonenumber": "phone",
}


def normalize_phone(raw: str) -> Optional[str]:
    """Return the number in E.164 form (+919876543210), or None if it is not usable."""
    digits = re.sub(r"[\s\-().]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    default_cc = config.env("CALL_DEFAULT_COUNTRY_CODE")  # e.g. "+91"
    if not digits.startswith("+") and default_cc and digits.isdigit():
        digits = default_cc + digits.lstrip("0")
    return digits if _E164.match(digits) else None


def parse_csv(content: bytes, courses: List[Course]) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """Parse an upload with columns name,email,course,phoneNum into (valid rows, row errors)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [{"row": 1, "error": "File is not UTF-8 text. Export it as CSV (UTF-8)."}]

    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return [], [{"row": 1, "error": "File is empty"}]
    columns = [_HEADER_ALIASES.get(re.sub(r"[\s_]", "", h).lower()) for h in header]
    missing = [c for c in ("name", "email", "course", "phone") if c not in columns]
    if missing:
        wanted = "name, email, course, phoneNum"
        return [], [{"row": 1, "error": f"Missing column(s): {', '.join(missing)}. Expected: {wanted}"}]

    by_key = {}
    for c in courses:
        by_key[c.id.lower()] = c.id
        by_key[c.title.lower()] = c.id

    rows: List[Dict[str, str]] = []
    errors: List[Dict[str, Any]] = []
    seen = set()
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(rows) + len(errors) >= MAX_ROWS:
            errors.append({"row": line, "error": f"Only the first {MAX_ROWS} rows were read"})
            break
        raw = {col: val.strip() for col, val in zip(columns, values) if col}
        problems = []
        if not raw.get("name"):
            problems.append("name is empty")
        try:
            email = _EMAIL.validate_python(raw.get("email", "")).lower()
        except ValidationError:
            email = ""
            problems.append("email is not valid")
        course_id = by_key.get(raw.get("course", "").lower())
        if not course_id:
            problems.append(f"unknown course '{raw.get('course', '')}'")
        phone = normalize_phone(raw.get("phone", ""))
        if not phone:
            problems.append("phone number must include the country code, e.g. +919876543210")
        if not problems and (email, course_id) in seen:
            problems.append("duplicate of an earlier row (same email and course)")
        if problems:
            errors.append({"row": line, "error": "; ".join(problems)})
            continue
        seen.add((email, course_id))
        rows.append({"name": raw["name"][:120], "email": email, "course_id": course_id, "phone": phone})
    return rows, errors


async def import_rows(database: AsyncDatabase, rows: List[Dict[str, str]]) -> Dict[str, int]:
    counts = {"added": 0, "updated": 0, "skipped": 0}
    for row in rows:
        key = {"email": row["email"], "course_id": row["course_id"]}
        if await database.feedbacks.find_one(key, {"_id": 1}):
            counts["skipped"] += 1  # already gave feedback (web form or an earlier call)
            continue
        result = await database.call_contacts.update_one(
            key,
            {
                "$set": {"name": row["name"], "phone": row["phone"], "updated_at": db.now()},
                "$setOnInsert": {
                    "status": "pending",
                    "answers": {},
                    "transcript": [],
                    "attempts": 0,
                    "created_at": db.now(),
                },
            },
            upsert=True,
        )
        counts["added" if result.upserted_id else "updated"] += 1
    return counts


def public(contact: Dict[str, Any], detail: bool = False) -> Dict[str, Any]:
    out = db.serialize(contact)
    out["answered"] = len(out.get("answers", {}))
    out["total_questions"] = len(FIELDS)
    if detail:
        out["transcript"] = [
            dict(line, at=line["at"].isoformat(timespec="seconds")) for line in out.get("transcript", [])
        ]
    else:
        out.pop("answers", None)
        out.pop("transcript", None)
    out.pop("call_token", None)
    return out


async def list_contacts(database: AsyncDatabase) -> List[Dict[str, Any]]:
    cursor = database.call_contacts.find({}, {"transcript": 0, "call_token": 0}).sort("_id", -1).limit(MAX_ROWS)
    return [public(c) async for c in cursor]


async def set_status(database: AsyncDatabase, contact_id: Any, status: str, **fields: Any) -> None:
    await database.call_contacts.update_one(
        {"_id": contact_id}, {"$set": dict(fields, status=status, updated_at=db.now())}
    )


async def add_transcript(database: AsyncDatabase, contact: Dict[str, Any], role: str, text: str) -> None:
    """Append a line, tagged with the call attempt it was said in (attempts counts every dial)."""
    line = {"role": role, "text": text, "at": db.now(), "attempt": contact.get("attempts")}
    await database.call_contacts.update_one({"_id": contact["_id"]}, {"$push": {"transcript": line}})
