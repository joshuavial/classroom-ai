"use client";

// Models on and off, for the admin and the teacher (R3.1). A model a worker
// offers for the first time starts off.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type Model = { name: string; enabled: boolean; offered: boolean };

export default function Models() {
  const [models, setModels] = useState<Model[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setModels((await api<{ models: Model[] }>("/models")).models);
      setError("");
    } catch {
      setError("Could not load the models.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function toggle(model: Model) {
    let failed = false;
    try {
      await api(`/models/${encodeURIComponent(model.name)}`, { method: "PUT", body: { enabled: !model.enabled } });
    } catch {
      failed = true;
    }
    await load();
    // After the reload, which clears any earlier error.
    if (failed) setError(`Could not change ${model.name}.`);
  }

  return (
    <section aria-labelledby="models-heading">
      <h2 id="models-heading">Models</h2>
      {error && <p role="alert">{error}</p>}
      {models === null ? (
        <p>Loading.</p>
      ) : models.length === 0 ? (
        <p>No worker has offered a model yet.</p>
      ) : (
        <ul>
          {models.map((m) => (
            <li key={m.name}>
              <label>
                <input type="checkbox" checked={m.enabled} onChange={() => toggle(m)} /> {m.name}
              </label>
              {!m.offered && " (no worker offers it right now)"}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
