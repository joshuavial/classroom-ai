"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { onAuthError } from "@/lib/session";
import type { LessonSession, LessonState, RosterEntry } from "@/lib/types";
import styles from "@/app/teach/teach.module.css";

function since(iso: string, now: number): string {
  const minutes = Math.max(0, Math.floor((now - Date.parse(iso)) / 60000));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function RosterRow({ entry, onChange }: { entry: RosterEntry; onChange: () => void }) {
  async function act(path: string, method = "POST", body?: unknown) {
    try {
      await api(`/students/${entry.id}${path}`, { method, body });
    } catch (e) {
      onAuthError(e);
      window.alert("That did not work. The roster will refresh.");
    }
    onChange();
  }
  return (
    <tr>
      <td>{entry.code}</td>
      <td>{entry.name ? "Used" : "Not used"}</td>
      <td>{entry.name ?? ""}</td>
      <td>
        {entry.name && (
          <>
            <button type="button"
              onClick={() => {
                const name = window.prompt(`New name for ${entry.name}`, entry.name ?? "");
                if (name) act("", "PATCH", { name });
              }}
            >
              Rename
            </button>{" "}
            <button type="button" onClick={() => act("/unbind")} aria-label={`Unbind code ${entry.code}`}>
              Unbind
            </button>{" "}
          </>
        )}
        <button type="button"
          onClick={() => window.confirm(`Remove code ${entry.code}? It will stop working.`) && act("/remove")}
          aria-label={`Remove code ${entry.code}`}
        >
          Remove
        </button>
      </td>
    </tr>
  );
}

/** Session bar, code roster and controls for one live lesson session. */
export function Lesson({ sessionId, onEnded }: { sessionId: number; onEnded: () => void }) {
  const [session, setSession] = useState<LessonSession | null>(null);
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(
    () =>
      api<LessonSession>(`/sessions/${sessionId}`)
        .then(setSession)
        .catch((e: unknown) => {
          onAuthError(e);
          setMessage("Could not load the lesson.");
        }),
    [sessionId],
  );
  useEffect(() => {
    load();
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, [load]);

  async function setState(state: LessonState) {
    if (state === "closed" && !window.confirm("Close the lesson? Every code stops working and students are signed out."))
      return;
    try {
      await api(`/sessions/${sessionId}/state`, { method: "POST", body: { state } });
    } catch (e) {
      onAuthError(e);
      setMessage("That did not work. Refresh and try again.");
      load();
      return;
    }
    if (state === "closed") onEnded();
    else load();
  }

  async function more(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const count = Number(new FormData(event.currentTarget).get("count"));
    try {
      await api(`/sessions/${sessionId}/codes`, { method: "POST", body: { count } });
      setMessage(`Added ${count} codes.`);
    } catch (e) {
      onAuthError(e);
      setMessage(e instanceof ApiError && e.status === 400 ? "Choose 1 to 200 codes." : "Could not add codes.");
    }
    load();
  }

  if (!session) return <p role="status">{message || "Loading the lesson…"}</p>;
  const used = session.roster.filter((r) => r.name).length;
  return (
    <section aria-label={`Lesson for ${session.class_name}`}>
      <div className={styles.bar}>
        <h2>{session.class_name}</h2>
        <span className={styles.state}>{session.state}</span>
        <span>Started {since(session.opened_at, now)} ago</span>
        {session.state === "paused" ? (
          <button type="button" onClick={() => setState("open")}>Resume</button>
        ) : (
          <button type="button" onClick={() => setState("paused")}>Pause</button>
        )}
        <button type="button" onClick={() => setState("closed")}>Close lesson</button>
        <a href={`/teach/slips?session=${session.id}`} target="_blank" rel="noopener">
          Print slips
        </a>
        <form onSubmit={more}>
          <label>
            More codes{" "}
            <input name="count" type="number" min={1} max={200} defaultValue={5} style={{ width: "4rem" }} />
          </label>{" "}
          <button type="submit">Generate</button>
        </form>
      </div>
      <p role="status">{message}</p>
      <h3>
        Codes ({used} of {session.roster.length} used) <button type="button" onClick={load}>Refresh</button>
      </h3>
      <table className={styles.roster}>
        <thead>
          <tr>
            <th scope="col">Code</th>
            <th scope="col">Status</th>
            <th scope="col">Name</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {session.roster.map((entry) => (
            <RosterRow key={entry.id} entry={entry} onChange={load} />
          ))}
        </tbody>
      </table>
    </section>
  );
}
