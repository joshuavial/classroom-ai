"use client";

// Admin page section: how long messages are kept, the backup download, one
// student's export and delete, and the audit log.

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, apiBlob, saveBlob } from "@/lib/api";
import { onAuthError } from "@/lib/session";

type AuditRow = { id: number; username: string; action: string; detail: Record<string, unknown>; time: string };
type Student = {
  id: number;
  code: string;
  name: string | null;
  removed: boolean;
  lesson_session_id: number;
  opened: string;
  class: string;
  messages: number;
};

function Students({ onChange }: { onChange: () => void }) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Student[] | null>(null);
  const [message, setMessage] = useState("");

  const search = useCallback(async (q: string) => {
    try {
      setRows((await api<{ students: Student[] }>(`/admin/students?q=${encodeURIComponent(q)}`)).students);
    } catch (e) {
      onAuthError(e);
      setMessage("Could not load the students.");
    }
  }, []);

  useEffect(() => {
    search("");
  }, [search]);

  async function exportOne(s: Student) {
    try {
      const { blob, filename } = await apiBlob(`/admin/students/${s.id}/export`);
      saveBlob(blob, filename);
      setMessage(`Exported ${s.name ?? s.code}.`);
      onChange();
    } catch (e) {
      onAuthError(e);
      setMessage("Could not export that student.");
    }
  }

  async function deleteOne(s: Student) {
    const who = s.name ?? `code ${s.code}`;
    if (!window.confirm(`Delete ${who} and all ${s.messages} of their messages? This cannot be undone.`)) return;
    try {
      await api(`/admin/students/${s.id}`, { method: "DELETE" });
      setMessage(`Deleted ${who}.`);
    } catch (e) {
      onAuthError(e);
      setMessage("Could not delete that student.");
    }
    await search(query);
    onChange();
  }

  return (
    <>
      <h3>One student</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          search(query);
        }}
      >
        <label>
          Find a student by name or code
          <input value={query} maxLength={100} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <button type="submit">Find</button>
      </form>
      <p role="status">{message}</p>
      {rows === null ? (
        <p>Loading.</p>
      ) : rows.length === 0 ? (
        <p>No students found.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Code</th>
              <th scope="col">Class</th>
              <th scope="col">Lesson</th>
              <th scope="col">Messages</th>
              <th scope="col" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td>
                  {s.name ?? "(not joined)"}
                  {s.removed && " (removed)"}
                </td>
                <td>{s.code}</td>
                <td>{s.class}</td>
                <td>{new Date(s.opened).toLocaleString()}</td>
                <td>{s.messages}</td>
                <td>
                  <button type="button" onClick={() => exportOne(s)} aria-label={`Export ${s.name ?? s.code}`}>
                    Export
                  </button>{" "}
                  <button type="button" onClick={() => deleteOne(s)} aria-label={`Delete ${s.name ?? s.code}`}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export default function Records() {
  const [days, setDays] = useState<number | null>(null);
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [more, setMore] = useState(false);
  const [message, setMessage] = useState("");

  const loadAudit = useCallback(async (before?: number) => {
    try {
      const page = await api<{ audit: AuditRow[] }>(`/admin/audit${before ? `?before=${before}` : ""}`);
      setRows((current) => (before ? [...current, ...page.audit] : page.audit));
      setMore(page.audit.length === 100);
    } catch (e) {
      onAuthError(e);
      setMessage("Could not load the audit log.");
    }
  }, []);

  useEffect(() => {
    api<{ days: number }>("/admin/retention")
      .then((r) => setDays(r.days))
      .catch((e: unknown) => {
        onAuthError(e);
        setMessage("Could not load the retention setting.");
      });
    loadAudit();
  }, [loadAudit]);

  async function downloadBackup() {
    try {
      const { blob, filename } = await apiBlob("/admin/backup", "POST");
      saveBlob(blob, filename);
      setMessage("Backup downloaded.");
      loadAudit();
    } catch (e) {
      onAuthError(e);
      setMessage("The backup failed. Try again, or use scripts/backup.sh on the server.");
    }
  }

  async function saveRetention(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(new FormData(event.currentTarget).get("days"));
    if (days !== null && value < days &&
        !window.confirm(`Messages older than ${value} days will be deleted within the hour. This cannot be undone.`))
      return;
    try {
      setDays((await api<{ days: number }>("/admin/retention", { method: "PUT", body: { days: value } })).days);
      setMessage(`Messages are now kept for ${value} days.`);
      loadAudit();
    } catch (e) {
      onAuthError(e);
      setMessage("Use a whole number of days from 1 to 3650.");
    }
  }

  return (
    <section aria-labelledby="records-heading">
      <h2 id="records-heading">Records</h2>
      <p role="status">{message}</p>
      {days !== null && (
        <form onSubmit={saveRetention}>
          <label>
            Keep messages for (days)
            <input name="days" type="number" min={1} max={3650} required defaultValue={days} key={days} />
          </label>
          <p>Lowering this deletes older messages within the hour, with no undo.</p>
          <button type="submit">Save</button>
        </form>
      )}
      <h3>Backup</h3>
      <p>
        A backup holds every account, setting and conversation, so keep it where only staff can read it.
      </p>
      <button type="button" onClick={downloadBackup}>
        Download a backup
      </button>
      <Students onChange={() => loadAudit()} />
      <h3>Audit log</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Who</th>
            <th scope="col">What</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.time).toLocaleString()}</td>
              <td>{r.username}</td>
              <td>{r.action}</td>
              <td>{JSON.stringify(r.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {more && (
        <button type="button" onClick={() => loadAudit(rows[rows.length - 1].id)}>
          Older entries
        </button>
      )}
    </section>
  );
}
