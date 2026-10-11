"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "@/app/forms.module.css";
import { api } from "@/lib/api";
import { onAuthError } from "@/lib/session";

export type GuardAction = "allow_flag" | "block_flag" | "block";
export type GuardCategory = { enabled: boolean; action: GuardAction; message: string };
export type GuardSettingsDocument = {
  version: 1;
  categories: Record<string, GuardCategory>;
  uncategorized: { action: GuardAction; message: string };
};

const ACTIONS: { value: GuardAction; label: string }[] = [
  { value: "allow_flag", label: "Allow and flag" },
  { value: "block_flag", label: "Block and flag" },
  { value: "block", label: "Block" },
];

export default function GuardSettings() {
  const [settings, setSettings] = useState<GuardSettingsDocument | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      setSettings(await api<GuardSettingsDocument>("/admin/guard"));
      setMessage("");
    } catch (error) {
      onAuthError(error);
      setMessage("Guard settings are unavailable. Chat remains blocked until they can be loaded.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function category(name: string, patch: Partial<GuardCategory>) {
    setSettings((current) =>
      current
        ? { ...current, categories: { ...current.categories, [name]: { ...current.categories[name], ...patch } } }
        : current,
    );
  }

  function fallback(patch: Partial<GuardSettingsDocument["uncategorized"]>) {
    setSettings((current) =>
      current ? { ...current, uncategorized: { ...current.uncategorized, ...patch } } : current,
    );
  }

  async function save() {
    if (!settings) return;
    setMessage("");
    try {
      const saved = await api<GuardSettingsDocument>("/admin/guard", { method: "PUT", body: settings });
      setSettings(saved);
      setMessage("Guard settings saved.");
    } catch (error) {
      onAuthError(error);
      setMessage("Could not save the guard settings. Chat remains blocked if the configuration is invalid.");
    }
  }

  return (
    <section aria-labelledby="guard-settings-heading">
      <h2 id="guard-settings-heading">Guard categories</h2>
      <p>Choose what happens and what a student sees when the guard reports each category.</p>
      {settings === null ? (
        <p role={message ? "alert" : undefined}>{message || "Loading guard settings."}</p>
      ) : (
        <div className={styles.form}>
          {Object.entries(settings.categories).length === 0 && (
            <p>Categories will appear here when the guard model is selected.</p>
          )}
          {Object.entries(settings.categories).map(([name, row]) => (
            <fieldset key={name}>
              <legend>{name}</legend>
              <label>
                <input
                  type="checkbox"
                  checked={row.enabled}
                  onChange={(event) => category(name, { enabled: event.target.checked })}
                />{" "}
                Act on this category
              </label>
              <label>
                Action
                <select value={row.action} onChange={(event) => category(name, { action: event.target.value as GuardAction })}>
                  {ACTIONS.map((action) => (
                    <option key={action.value} value={action.value}>{action.label}</option>
                  ))}
                </select>
              </label>
              <label>
                Student message
                <textarea
                  value={row.message}
                  maxLength={500}
                  required
                  onChange={(event) => category(name, { message: event.target.value })}
                />
              </label>
            </fieldset>
          ))}
          <fieldset>
            <legend>Uncategorized</legend>
            <p>This fallback is always active for unsafe results with no known category.</p>
            <label>
              Action
              <select
                value={settings.uncategorized.action}
                onChange={(event) => fallback({ action: event.target.value as GuardAction })}
              >
                {ACTIONS.map((action) => (
                  <option key={action.value} value={action.value}>{action.label}</option>
                ))}
              </select>
            </label>
            <label>
              Student message
              <textarea
                value={settings.uncategorized.message}
                maxLength={500}
                required
                onChange={(event) => fallback({ message: event.target.value })}
              />
            </label>
          </fieldset>
          <p role="status">{message}</p>
          <button type="button" onClick={save}>Save guard settings</button>
        </div>
      )}
    </section>
  );
}
