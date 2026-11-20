import json
import os
import secrets
from pathlib import Path
from typing import List, Optional

from fastapi import Header, HTTPException

from .schemas import Course

COURSES_FILE = Path(__file__).resolve().parent.parent / "courses.json"


def load_courses() -> List[Course]:
    path = Path(os.environ.get("FEEDBACK_COURSES_FILE", str(COURSES_FILE)))
    return [Course(**c) for c in json.loads(path.read_text())]


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("FEEDBACK_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "Admin access is disabled: FEEDBACK_ADMIN_TOKEN is not set")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "Invalid admin token")
