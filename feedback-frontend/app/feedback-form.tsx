"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, PACE_LABELS, type Course, type FeedbackInput, type Pace } from "@/lib/api";

const FIVE = [1, 2, 3, 4, 5];
const ELEVEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

export default function FeedbackForm() {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api<Course[]>("/courses")
      .then(setCourses)
      .catch((e: Error) => setLoadError(e.message));
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const text = (k: string) => String(f.get(k) ?? "").trim();
    const payload: FeedbackInput = {
      name: text("name"),
      email: text("email"),
      course_id: text("course_id"),
      job_role: text("job_role") || null,
      overall_rating: Number(f.get("overall_rating")),
      relevance_rating: Number(f.get("relevance_rating")),
      confidence_rating: Number(f.get("confidence_rating")),
      pace: text("pace") as Pace,
      recommend_score: Number(f.get("recommend_score")),
      key_learning: text("key_learning"),
      improvement: text("improvement") || null,
      testimonial_consent: f.get("testimonial_consent") === "on",
    };

    setSubmitting(true);
    setError(null);
    try {
      await api("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setDone(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="card notice" role="status">
        <h2>Thank you — feedback received.</h2>
        <p>We read every response and use it to shape the next version of the course.</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="card error" role="alert">
        Couldn&apos;t load the form: {loadError}
      </div>
    );
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <div className="row">
        <label className="field">
          <span>Your name</span>
          <input name="name" required maxLength={120} autoComplete="name" />
        </label>
        <label className="field">
          <span>Email</span>
          <input name="email" type="email" required autoComplete="email" />
        </label>
      </div>

      <div className="row">
        <label className="field">
          <span>Course</span>
          <select name="course_id" required defaultValue="" disabled={!courses}>
            <option value="" disabled>
              {courses ? "Select your course" : "Loading courses…"}
            </option>
            {courses?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>
            Your role <em>(optional)</em>
          </span>
          <input name="job_role" maxLength={120} placeholder="e.g. Backend engineer" />
        </label>
      </div>

      <Scale name="overall_rating" legend="Overall, how would you rate the course?" values={FIVE} low="Poor" high="Excellent" />
      <Scale name="relevance_rating" legend="How relevant was it to your engineering domain?" values={FIVE} low="Not relevant" high="Spot on" />
      <Scale name="confidence_rating" legend="How confident are you now using AI agents in your day-to-day work?" values={FIVE} low="Not confident" high="Very confident" />

      <fieldset className="scale">
        <legend>How was the pace for a 10-hour course?</legend>
        <div className="options">
          {(Object.keys(PACE_LABELS) as Pace[]).map((p) => (
            <label key={p} className="pill wide">
              <input type="radio" name="pace" value={p} required />
              <span>{PACE_LABELS[p]}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <Scale name="recommend_score" legend="How likely are you to recommend this certificate to a colleague?" values={ELEVEN} low="Not at all" high="Extremely likely" />

      <label className="field">
        <span>What&apos;s the most valuable thing you learned?</span>
        <textarea name="key_learning" required maxLength={2000} rows={3} placeholder="A technique, workflow, or mindset shift you're already using" />
      </label>

      <label className="field">
        <span>
          What should we improve? <em>(optional)</em>
        </span>
        <textarea name="improvement" maxLength={2000} rows={3} />
      </label>

      <label className="check">
        <input type="checkbox" name="testimonial_consent" />
        <span>DotAI Labs may quote my feedback, with my name, as a testimonial.</span>
      </label>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <button type="submit" disabled={submitting || !courses}>
        {submitting ? "Sending…" : "Send feedback"}
      </button>
    </form>
  );
}

function Scale(props: { name: string; legend: string; values: number[]; low: string; high: string }) {
  return (
    <fieldset className="scale">
      <legend>{props.legend}</legend>
      <div className="options">
        {props.values.map((v) => (
          <label key={v} className="pill">
            <input type="radio" name={props.name} value={v} required />
            <span>{v}</span>
          </label>
        ))}
      </div>
      <div className="ends">
        <span>{props.low}</span>
        <span>{props.high}</span>
      </div>
    </fieldset>
  );
}
