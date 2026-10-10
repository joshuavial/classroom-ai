"use client";

// Admin page section: the join command, the worker list, remove and rotate.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { onAuthError } from "@/lib/session";

export const POLL_MS = 5000;

export type Worker = {
  id: string;
  address: string;
  status: "up" | "down" | "removed";
  models: string[];
  capacity: number;
  in_flight: number;
  last_heartbeat: string;
};

type Join = { bash: string; powershell: string };

export default function Workers() {
  const [workers, setWorkers] = useState<Worker[] | null>(null);
  const [join, setJoin] = useState<Join | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api<{ workers: Worker[] }>("/workers");
      setWorkers(data.workers);
      setError("");
    } catch (e) {
      onAuthError(e);
      setError("Could not load the workers. Retrying.");
    }
  }, []);

  const loadJoin = useCallback(async () => {
    try {
      setJoin(await api<Join>("/workers/join"));
    } catch (e) {
      onAuthError(e);
      setError("Could not load the join command.");
    }
  }, []);

  useEffect(() => {
    load();
    loadJoin();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load, loadJoin]);

  async function remove(worker: Worker) {
    if (!window.confirm(`Remove worker ${worker.address}? It will not be able to rejoin with this ID.`)) return;
    const rotate = window.confirm(
      "Also rotate the join token? That stops this machine rejoining under a new ID, " +
        "but every other worker stops serving until you run the new join command on it.",
    );
    let failed = false;
    try {
      await api(`/workers/${encodeURIComponent(worker.id)}/remove`, { method: "POST", body: { rotate } });
    } catch (e) {
      onAuthError(e);
      failed = true;
    }
    await load();
    if (rotate) await loadJoin();
    // After the reload, which clears any earlier error.
    if (failed) setError("Could not remove the worker.");
  }

  async function rotateToken() {
    if (!window.confirm("Rotate the join token? Every worker stops serving until you run the new join command on it."))
      return;
    let failed = false;
    try {
      await api("/workers/rotate", { method: "POST" });
    } catch (e) {
      onAuthError(e);
      failed = true;
    }
    await loadJoin();
    await load();
    if (failed) setError("Could not rotate the join token.");
  }

  return (
    <section aria-labelledby="workers-heading">
      <h2 id="workers-heading">Workers</h2>
      {error && <p role="alert">{error}</p>}
      <h3>Join a worker</h3>
      <p>On the GPU machine, in the classroom-ai folder, run one of these.</p>
      {join ? (
        <>
          <label>
            Linux, WSL2 Ubuntu or Mac (bash)
            <textarea readOnly value={join.bash} rows={3} />
          </label>
          <label>
            Windows (PowerShell)
            <textarea readOnly value={join.powershell} rows={3} />
          </label>
        </>
      ) : (
        <p>Loading the join command.</p>
      )}
      <button type="button" onClick={rotateToken}>
        Rotate join token
      </button>
      <h3>Machines</h3>
      {workers === null ? (
        <p>Loading.</p>
      ) : workers.length === 0 ? (
        <p>No workers have joined yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Address</th>
              <th scope="col">Status</th>
              <th scope="col">Models</th>
              <th scope="col">Busy</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.id}>
                <td>{w.address}</td>
                <td>{w.status}</td>
                <td>{w.models.join(", ")}</td>
                <td>
                  {w.in_flight} of {w.capacity}
                </td>
                <td>
                  {w.status !== "removed" && (
                    <button type="button" onClick={() => remove(w)} aria-label={`Remove ${w.address}`}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
