export type Course = { id: string; title: string };

export type Pace = "too_slow" | "just_right" | "too_fast";

export type FeedbackInput = {
  name: string;
  email: string;
  course_id: string;
  job_role: string | null;
  overall_rating: number;
  relevance_rating: number;
  confidence_rating: number;
  pace: Pace;
  recommend_score: number;
  key_learning: string;
  improvement: string | null;
  testimonial_consent: boolean;
};

export type Feedback = FeedbackInput & { id: string; created_at: string; source: "web" | "call" };

export type CourseSummary = {
  course_id: string | null;
  responses: number;
  avg_overall: number | null;
  avg_relevance: number | null;
  avg_confidence: number | null;
  nps: number | null;
  pace: Record<Pace, number>;
};

export type Summary = { overall: CourseSummary; by_course: CourseSummary[] };

export type ContactStatus =
  | "pending"
  | "queued"
  | "calling"
  | "completed"
  | "incomplete"
  | "no_answer"
  | "declined"
  | "failed";

export type Contact = {
  id: string;
  name: string;
  email: string;
  course_id: string;
  phone: string;
  status: ContactStatus;
  attempts: number;
  answered: number;
  total_questions: number;
  outcome: string | null;
  last_error: string | null;
  updated_at: string;
};

export type ContactDetail = Contact & {
  answers: Record<string, string | number | boolean | null>;
  transcript: { role: "agent" | "caller"; text: string; at: string; attempt?: number | null }[];
};

export type ContactList = { contacts: Contact[]; missing_settings: string[] };

export type UploadResult = {
  added: number;
  updated: number;
  skipped: number;
  errors: { row: number; error: string }[];
};

export const PACE_LABELS: Record<Pace, string> = {
  too_slow: "Too slow",
  just_right: "Just right",
  too_fast: "Too fast",
};

/** Calls the feedback-service through the /api proxy and throws a readable Error on failure. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, init);
  } catch {
    throw new Error("Could not reach the server. Check your connection and try again.");
  }
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const { detail } = await res.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{ loc: ["body", "email"], msg: "..." }]
      return detail
        .map((d) => `${String(d.loc?.[d.loc.length - 1] ?? "field").replace(/_/g, " ")}: ${d.msg}`)
        .join("; ");
    }
  } catch {
    // non-JSON body (e.g. the proxy could not reach the backend)
  }
  return res.status >= 500
    ? "The feedback service is unavailable right now. Please try again shortly."
    : `Request failed (${res.status})`;
}
