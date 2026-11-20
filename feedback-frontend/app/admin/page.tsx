"use client";

import { useState, type FormEvent } from "react";
import { api, type Summary } from "@/lib/api";
import Calls from "./calls";
import Results from "./results";

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(null);
  const [tab, setTab] = useState<"results" | "calls">("results");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const entered = String(new FormData(e.currentTarget).get("token") ?? "");
    setChecking(true);
    setError(null);
    try {
      await api<Summary>("/admin/summary", { headers: { "X-Admin-Token": entered } });
      setToken(entered);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setChecking(false);
    }
  }

  if (!token) {
    return (
      <>
        <h1>Feedback admin</h1>
        <form className="card" onSubmit={onSubmit}>
          <label className="field">
            <span>Admin token</span>
            <input name="token" type="password" required autoComplete="off" />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" disabled={checking}>
            {checking ? "Checking…" : "Sign in"}
          </button>
        </form>
      </>
    );
  }

  return (
    <div className="admin">
      <h1>Feedback admin</h1>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "results"} onClick={() => setTab("results")}>
          Results
        </button>
        <button role="tab" aria-selected={tab === "calls"} onClick={() => setTab("calls")}>
          Phone calls
        </button>
      </div>
      {tab === "results" ? <Results token={token} /> : <Calls token={token} />}
    </div>
  );
}
