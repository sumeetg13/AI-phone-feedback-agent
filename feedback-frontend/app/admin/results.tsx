"use client";

import { useEffect, useState } from "react";
import { api, PACE_LABELS, type Course, type Feedback, type Summary } from "@/lib/api";

type Data = { summary: Summary; feedback: Feedback[]; titles: Record<string, string> };

const fmt = (n: number | null, suffix = "") => (n === null ? "—" : `${n}${suffix}`);

export default function Results({ token }: { token: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const headers = { "X-Admin-Token": token };
    Promise.all([
      api<Summary>("/admin/summary", { headers }),
      api<Feedback[]>("/admin/feedback?limit=500", { headers }),
      api<Course[]>("/courses"),
    ])
      .then(([summary, feedback, courses]) =>
        setData({ summary, feedback, titles: Object.fromEntries(courses.map((c) => [c.id, c.title])) }),
      )
      .catch((e: Error) => setError(e.message));
  }, [token]);

  if (error) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (!data) return <p className="muted">Loading…</p>;

  const { summary, feedback, titles } = data;
  const o = summary.overall;
  const title = (id: string | null) => (id && titles[id]) || id || "—";

  return (
    <>
      <div className="tiles">
        <Tile label="Responses" value={String(o.responses)} />
        <Tile label="Overall" value={fmt(o.avg_overall, " / 5")} />
        <Tile label="Domain relevance" value={fmt(o.avg_relevance, " / 5")} />
        <Tile label="Confidence with agents" value={fmt(o.avg_confidence, " / 5")} />
        <Tile label="NPS" value={fmt(o.nps)} />
      </div>

      <h2>By course</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Course</th>
              <th>Responses</th>
              <th>Overall</th>
              <th>Relevance</th>
              <th>Confidence</th>
              <th>NPS</th>
              <th>Pace (slow / right / fast)</th>
            </tr>
          </thead>
          <tbody>
            {summary.by_course.map((c) => (
              <tr key={c.course_id}>
                <td>{title(c.course_id)}</td>
                <td>{c.responses}</td>
                <td>{fmt(c.avg_overall)}</td>
                <td>{fmt(c.avg_relevance)}</td>
                <td>{fmt(c.avg_confidence)}</td>
                <td>{fmt(c.nps)}</td>
                <td>
                  {c.pace.too_slow} / {c.pace.just_right} / {c.pace.too_fast}
                </td>
              </tr>
            ))}
            {summary.by_course.length === 0 && (
              <tr>
                <td colSpan={7}>No feedback yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <h2>Responses</h2>
      {feedback.map((f) => (
        <article key={f.id} className="card response">
          <header>
            <strong>{f.name}</strong>
            {f.job_role && <span> · {f.job_role}</span>}
            <span className="muted">
              {" "}
              · {f.email} · {new Date(f.created_at).toLocaleDateString()} ·{" "}
              {f.source === "call" ? "by phone" : "web form"}
            </span>
          </header>
          <p className="muted">
            {title(f.course_id)} — overall {f.overall_rating}/5, relevance {f.relevance_rating}/5,
            confidence {f.confidence_rating}/5, recommend {f.recommend_score}/10, pace:{" "}
            {PACE_LABELS[f.pace].toLowerCase()}
          </p>
          <p>
            <b>Learned:</b> {f.key_learning}
          </p>
          {f.improvement && (
            <p>
              <b>Improve:</b> {f.improvement}
            </p>
          )}
          {f.testimonial_consent && <span className="tag">OK to quote</span>}
        </article>
      ))}
    </>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="tile">
      <div className="tile-value">{value}</div>
      <div className="tile-label">{label}</div>
    </div>
  );
}
