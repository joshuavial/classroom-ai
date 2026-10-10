"use client";

import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import styles from "@/app/forms.module.css";

const MESSAGES: Record<string, string> = {
  wrong_code: "That setup code is not right. Check the server log and try again.",
  setup_done: "Setup is already done. Sign in instead.",
  bad_username: "Use 1 to 64 letters, numbers, dots, dashes or underscores for the username.",
  bad_password: "The password needs at least 10 characters.",
  too_many_attempts: "Too many attempts. Wait a minute and try again.",
};

export default function SetupPage() {
  const [needed, setNeeded] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ needed: boolean }>("/setup").then((r) => setNeeded(r.needed));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("/setup", {
        method: "POST",
        body: { code: form.get("code"), username: form.get("username"), password: form.get("password") },
      });
      window.location.assign("/admin");
    } catch (e) {
      const code = e instanceof ApiError ? (e.body as { error?: string } | null)?.error : undefined;
      setError((code && MESSAGES[code]) || "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  if (needed === null) return <main className={styles.page}>Loading…</main>;
  if (!needed)
    return (
      <main className={styles.page}>
        <h1>Setup is done</h1>
        <p>
          <a href="/login">Sign in</a>
        </p>
      </main>
    );
  return (
    <main className={styles.page}>
      <h1>Set up classroom-ai</h1>
      <p>Enter the setup code from the server log, then choose the admin username and password.</p>
      <form onSubmit={submit} className={styles.form}>
        <label>
          Setup code
          <input name="code" required autoComplete="off" />
        </label>
        <label>
          Admin username
          <input name="username" required autoComplete="username" />
        </label>
        <label>
          Password
          <input name="password" type="password" required minLength={10} autoComplete="new-password" />
        </label>
        <p role="alert" className={styles.error}>
          {error}
        </p>
        <button type="submit" disabled={busy}>
          Create admin account
        </button>
      </form>
    </main>
  );
}
