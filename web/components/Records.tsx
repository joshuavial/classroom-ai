"use client";

// Admin page section: how long conversations are kept, the backup download,
// and the audit log.

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { onAuthError } from "@/lib/session";

type AuditRow = { id: number; username: string; action: string; detail: Record<string, unknown>; time: string };

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

  async function saveRetention(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(new FormData(event.currentTarget).get("days"));
    if (days !== null && value < days &&
        !window.confirm(`Conversations older than ${value} days will be deleted within the hour. This cannot be undone.`))
      return;
    try {
      setDays((await api<{ days: number }>("/admin/retention", { method: "PUT", body: { days: value } })).days);
      setMessage(`Conversations are now kept for ${value} days.`);
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
            Keep conversations for (days)
            <input name="days" type="number" min={1} max={3650} required defaultValue={days} key={days} />
          </label>
          <p>Lowering this deletes older conversations within the hour, with no undo.</p>
          <button type="submit">Save</button>
        </form>
      )}
      <h3>Backup</h3>
      <p>
        <a href="/api/admin/backup" download>
          Download a backup
        </a>{" "}
        of every account, setting and conversation. It holds students&apos; conversations, so keep it where only
        staff can read it.
      </p>
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
