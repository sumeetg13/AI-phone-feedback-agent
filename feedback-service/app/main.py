from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from . import db
from .calls.routes import router as calls_router
from .deps import load_courses, require_admin
from .schemas import Course, FeedbackCreated, FeedbackIn, FeedbackOut, Summary


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="DotAI Labs Feedback Service", lifespan=lifespan)
app.include_router(calls_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/courses", response_model=List[Course])
def courses():
    return load_courses()


@app.post("/feedback", response_model=FeedbackCreated, status_code=201)
async def submit_feedback(payload: FeedbackIn, database: AsyncDatabase = Depends(db.get_db)):
    if payload.course_id not in {c.id for c in load_courses()}:
        raise HTTPException(422, "Unknown course")
    try:
        return await db.insert_feedback(database, payload.model_dump(), source="web")
    except DuplicateKeyError:
        raise HTTPException(409, "Feedback for this course was already submitted with this email")


@app.get("/admin/feedback", response_model=List[FeedbackOut], dependencies=[Depends(require_admin)])
async def list_feedback(
    course_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    database: AsyncDatabase = Depends(db.get_db),
):
    return await db.list_feedback(database, course_id, limit, offset)


@app.get("/admin/summary", response_model=Summary, dependencies=[Depends(require_admin)])
async def summary(database: AsyncDatabase = Depends(db.get_db)):
    return {
        "overall": (await db.summarize(database, by_course=False))[0],
        "by_course": await db.summarize(database, by_course=True),
    }
