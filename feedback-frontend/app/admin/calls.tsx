"use client";

import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import {
  api,
  type Contact,
  type ContactDetail,
  type ContactList,
  type ContactStatus,
  type Course,
  type UploadResult,
} from "@/lib/api";

const STATUS_LABELS: Record<ContactStatus, string> = {
  pending: "Not called yet",
  queued: "Queued",
  calling: "On call",
  completed: "Feedback submitted",
  incomplete: "Unfinished",
  no_answer: "No answer",
  declined: "Declined",
  failed: "Failed",
};
// Mirrors CALLABLE in the backend: people who declined are not called again.
const CALLABLE: ContactStatus[] = ["pending", "queued", "incomplete", "no_answer", "failed"];
const IN_FLIGHT: ContactStatus[] = ["queued", "calling"];

/** Error text as plain words: drops terminal colour codes and collapses whitespace. */
const plain = (text: string) =>
  text
    .replace(/\x1b\[[0-9;]*m/g, "")
    .replace(/\s+/g, " ")
    .trim();

export default function Calls({ token }: { token: string }) {
  const [list, setList] = useState<ContactList | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmOne, setConfirmOne] = useState<string | null>(null); // id of the row awaiting a second click
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // id of the row being edited
  const [editError, setEditError] = useState<string | null>(null);
  const [open, setOpen] = useState<ContactDetail | null>(null);
  const [tip, setTip] = useState<{ text: string; left: number; top: number } | null>(null);

  // The reason a call failed is shown on hover, anchored under the status badge. It is
  // positioned against the window so the scrolling table cannot clip it.
  function showTip(badge: HTMLElement, text: string) {
    const box = badge.getBoundingClientRect();
    setTip({ text: plain(text), left: Math.min(box.left, window.innerWidth - 336), top: box.bottom + 6 });
  }

  const load = useCallback(
    () =>
      api<ContactList>("/admin/calls/contacts", { headers: { "X-Admin-Token": token } })
        .then(setList)
        .catch((e: Error) => setError(e.message)),
    [token],
  );

  useEffect(() => {
    load();
    api<Course[]>("/courses")
      .then((courses) => setTitles(Object.fromEntries(courses.map((c) => [c.id, c.title]))))
      .catch(() => {});
  }, [load]);

  // While calls are queued or in progress, keep the table fresh.
  const inFlight = list?.contacts.some((c) => IN_FLIGHT.includes(c.status)) ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [inFlight, load]);

  async function onUpload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    setBusy(true);
    setError(null);
    setUpload(null);
    try {
      const result = await api<UploadResult>("/admin/calls/contacts/upload", {
        method: "POST",
        headers: { "X-Admin-Token": token },
        body: new FormData(formEl),
      });
      setUpload(result);
      formEl.reset();
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Calls everyone eligible, or just one person when given their id. */
  async function startCalls(contactId?: string) {
    setBusy(true);
    setError(null);
    try {
      await api("/admin/calls/start", {
        method: "POST",
        headers: { "X-Admin-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify(contactId ? { contact_ids: [contactId] } : {}),
      });
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setConfirming(false);
      setConfirmOne(null);
    }
  }

  async function saveEdit(e: FormEvent<HTMLFormElement>, contactId: string) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setEditError(null);
    try {
      await api<Contact>(`/admin/calls/contacts/${contactId}`, {
        method: "PATCH",
        headers: { "X-Admin-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify({
          name: f.get("name"),
          email: f.get("email"),
          course_id: f.get("course_id"),
          phone: f.get("phone"),
        }),
      });
      setEditing(null);
      await load();
    } catch (err) {
      setEditError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteContact(contactId: string) {
    setBusy(true);
    setError(null);
    try {
      // The endpoint answers with an empty 204, so this does not go through api(), which parses JSON.
      const res = await fetch(`/api/admin/calls/contacts/${contactId}`, {
        method: "DELETE",
        headers: { "X-Admin-Token": token },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ?? `Could not delete (${res.status})`);
      }
      if (open?.id === contactId) setOpen(null);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setConfirmDelete(null);
    }
  }

  async function toggleDetail(contact: Contact) {
    if (open?.id === contact.id) return setOpen(null);
    try {
      setOpen(
        await api<ContactDetail>(`/admin/calls/contacts/${contact.id}`, {
          headers: { "X-Admin-Token": token },
        }),
      );
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const contacts = list?.contacts ?? [];
  const callable = contacts.filter((c) => CALLABLE.includes(c.status)).length;
  const missing = list?.missing_settings ?? [];

  return (
    <>
      <h2>Add people to call</h2>
      <form className="card" onSubmit={onUpload}>
        <label className="field">
          <span>CSV file</span>
          <input name="file" type="file" accept=".csv,text/csv" required />
        </label>
        <p className="muted small">
          Columns: <code>name, email, course, phoneNum</code>. The course is its id or exact title;
          the phone number includes the country code, like <code>+919876543210</code>. Re-uploading
          a person updates their name and number.
        </p>
        <button type="submit" disabled={busy}>
          Upload
        </button>
        {upload && (
          <div role="status">
            <p>
              {upload.added} added, {upload.updated} updated
              {upload.skipped > 0 && `, ${upload.skipped} skipped (already gave feedback)`}
              {upload.errors.length > 0 && `, ${upload.errors.length} rejected`}.
            </p>
            {upload.errors.length > 0 && (
              <ul className="error small">
                {upload.errors.map((e) => (
                  <li key={e.row}>
                    Line {e.row}: {e.error}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </form>

      <h2>People</h2>
      {missing.length > 0 && (
        <p className="card warn">
          Calls can&apos;t be placed yet. Set these in the feedback-service environment and restart
          it: <code>{missing.join(", ")}</code>
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="actions">
        {confirming ? (
          <>
            <span>
              This phones {callable} {callable === 1 ? "person" : "people"} now.
            </span>
            <button onClick={() => startCalls()} disabled={busy}>
              Yes, start calling
            </button>
            <button className="secondary" onClick={() => setConfirming(false)} disabled={busy}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button onClick={() => setConfirming(true)} disabled={busy || callable === 0 || missing.length > 0}>
              Call {callable} {callable === 1 ? "person" : "people"}
            </button>
            <button className="secondary" onClick={load}>
              Refresh
            </button>
          </>
        )}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Course</th>
              <th>Phone</th>
              <th>Status</th>
              <th>Form</th>
              <th>Calls</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {contacts.map((c) => (
              <Fragment key={c.id}>
                <tr>
                  <td>
                    {c.name}
                    <div className="muted small">{c.email}</div>
                  </td>
                  <td>{titles[c.course_id] ?? c.course_id}</td>
                  <td>{c.phone}</td>
                  <td>
                    {c.last_error ? (
                      <span
                        className={`badge ${c.status} has-tip`}
                        tabIndex={0}
                        aria-label={`${STATUS_LABELS[c.status]}: ${plain(c.last_error)}`}
                        onMouseEnter={(e) => showTip(e.currentTarget, c.last_error!)}
                        onFocus={(e) => showTip(e.currentTarget, c.last_error!)}
                        onMouseLeave={() => setTip(null)}
                        onBlur={() => setTip(null)}
                      >
                        {STATUS_LABELS[c.status]}
                      </span>
                    ) : (
                      <span className={`badge ${c.status}`}>{STATUS_LABELS[c.status]}</span>
                    )}
                  </td>
                  <td>
                    {c.answered} / {c.total_questions}
                  </td>
                  <td>{c.attempts}</td>
                  <td>
                    <div className="row-actions">
                      {CALLABLE.includes(c.status) &&
                        (confirmOne === c.id ? (
                          <>
                            <button className="small" onClick={() => startCalls(c.id)} disabled={busy}>
                              Call now
                            </button>
                            <button className="secondary small" onClick={() => setConfirmOne(null)} disabled={busy}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            className="secondary small"
                            onClick={() => setConfirmOne(c.id)}
                            disabled={busy || missing.length > 0}
                          >
                            {c.attempts > 0 ? "Call again" : "Call"}
                          </button>
                        ))}
                      {c.attempts > 0 && (
                        <button className="secondary small" onClick={() => toggleDetail(c)}>
                          {open?.id === c.id ? "Hide" : "Transcript"}
                        </button>
                      )}
                      {c.status !== "calling" && (
                        <button
                          className="secondary small"
                          onClick={() => {
                            setEditError(null);
                            setEditing(editing === c.id ? null : c.id);
                          }}
                          disabled={busy}
                        >
                          Edit
                        </button>
                      )}
                      {c.status !== "calling" &&
                        (confirmDelete === c.id ? (
                          <>
                            <button className="small danger" onClick={() => deleteContact(c.id)} disabled={busy}>
                              Delete for good
                            </button>
                            <button className="secondary small" onClick={() => setConfirmDelete(null)} disabled={busy}>
                              Keep
                            </button>
                          </>
                        ) : (
                          <button className="secondary small" onClick={() => setConfirmDelete(c.id)} disabled={busy}>
                            Delete
                          </button>
                        ))}
                    </div>
                  </td>
                </tr>
                {editing === c.id && (
                  <tr>
                    <td colSpan={7} className="wrap">
                      <form className="edit" onSubmit={(e) => saveEdit(e, c.id)}>
                        <label className="field">
                          <span>Name</span>
                          <input name="name" defaultValue={c.name} required maxLength={120} />
                        </label>
                        <label className="field">
                          <span>Email</span>
                          <input name="email" type="email" defaultValue={c.email} required />
                        </label>
                        <label className="field">
                          <span>Course</span>
                          <select name="course_id" defaultValue={c.course_id} required>
                            {Object.entries(titles).map(([id, title]) => (
                              <option key={id} value={id}>
                                {title}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field">
                          <span>Phone</span>
                          <input name="phone" type="tel" defaultValue={c.phone} required />
                        </label>
                        <div className="edit-actions">
                          <button type="submit" className="small" disabled={busy}>
                            Save
                          </button>
                          <button type="button" className="secondary small" onClick={() => setEditing(null)} disabled={busy}>
                            Cancel
                          </button>
                          {c.answered > 0 && c.status !== "completed" && (
                            <span className="muted small">Changing the course clears the answers collected so far.</span>
                          )}
                          {editError && (
                            <span className="error small" role="alert">
                              {editError}
                            </span>
                          )}
                        </div>
                      </form>
                    </td>
                  </tr>
                )}
                {open?.id === c.id && (
                  <tr>
                    <td colSpan={7} className="wrap">
                      {open.transcript.length === 0 && <p className="muted">No conversation recorded.</p>}
                      {open.transcript.map((line, i) => (
                        <Fragment key={i}>
                          {/* A heading wherever a new call starts. */}
                          {(i === 0 || line.attempt !== open.transcript[i - 1].attempt) && (
                            <p className="attempt">
                              {line.attempt ? `Call attempt ${line.attempt}` : "Earlier call"}
                              <span className="muted"> · {new Date(line.at).toLocaleString()}</span>
                            </p>
                          )}
                          <p className="line">
                            <b>{line.role === "agent" ? "Agent" : c.name.split(" ")[0]}:</b> {line.text}
                          </p>
                        </Fragment>
                      ))}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {list && contacts.length === 0 && (
              <tr>
                <td colSpan={7}>Nobody here yet. Upload a CSV to add people.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {tip && (
        <div className="tip" role="tooltip" style={{ left: Math.max(tip.left, 16), top: tip.top }}>
          {tip.text}
        </div>
      )}
    </>
  );
}
