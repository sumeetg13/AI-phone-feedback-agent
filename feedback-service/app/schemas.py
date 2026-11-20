from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Pace = Literal["too_slow", "just_right", "too_fast"]


class Course(BaseModel):
    id: str
    title: str


class FeedbackIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    course_id: str
    job_role: Optional[str] = Field(default=None, max_length=120)

    # 1-5 ratings
    overall_rating: int = Field(ge=1, le=5)
    relevance_rating: int = Field(ge=1, le=5)  # relevance to the learner's domain
    confidence_rating: int = Field(ge=1, le=5)  # confidence using AI agents at work

    pace: Pace
    recommend_score: int = Field(ge=0, le=10)  # NPS question

    key_learning: str = Field(min_length=1, max_length=2000)
    improvement: Optional[str] = Field(default=None, max_length=2000)
    testimonial_consent: bool = False


class FeedbackOut(FeedbackIn):
    id: str
    created_at: str
    source: Literal["web", "call"] = "web"


class FeedbackCreated(BaseModel):
    id: str
    created_at: str


class CourseSummary(BaseModel):
    course_id: Optional[str]  # None for the all-courses rollup
    responses: int
    avg_overall: Optional[float]
    avg_relevance: Optional[float]
    avg_confidence: Optional[float]
    nps: Optional[int]  # % promoters (9-10) minus % detractors (0-6)
    pace: Dict[str, int]


class Summary(BaseModel):
    overall: CourseSummary
    by_course: List[CourseSummary]


# --- call campaigns ----------------------------------------------------------

ContactStatus = Literal[
    "pending",  # uploaded, not called yet
    "queued",  # waiting for the dialler to place the call
    "calling",  # call placed: ringing or in conversation
    "completed",  # feedback form submitted
    "incomplete",  # call ended before the form was finished; a retry resumes it
    "no_answer",  # busy, not picked up, or voicemail
    "declined",  # the person did not want to give feedback
    "failed",  # the call could not be placed or broke
]


class Contact(BaseModel):
    id: str
    name: str
    email: str
    course_id: str
    phone: str
    status: ContactStatus
    attempts: int
    answered: int  # how many form questions have an answer so far
    total_questions: int
    outcome: Optional[str] = None
    last_error: Optional[str] = None
    updated_at: str


class ContactEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    course_id: str
    phone: str


class TranscriptLine(BaseModel):
    role: Literal["agent", "caller"]
    text: str
    at: str
    attempt: Optional[int] = None  # which call this was said in; missing on lines saved before this was tracked


class ContactDetail(Contact):
    answers: Dict[str, Any]
    transcript: List[TranscriptLine]


class RowError(BaseModel):
    row: int  # 1-based line number in the CSV, counting the header as line 1
    error: str


class UploadResult(BaseModel):
    added: int
    updated: int
    skipped: int  # already has submitted feedback for that course
    errors: List[RowError]


class ContactList(BaseModel):
    contacts: List[Contact]
    missing_settings: List[str]  # non-empty means calls cannot be placed yet


class StartCalls(BaseModel):
    contact_ids: Optional[List[str]] = None  # omit to call everyone who is eligible


class StartCallsResult(BaseModel):
    queued: int
