"use client";

import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { type Staff, homeFor } from "@/lib/session";
import styles from "@/app/forms.module.css";

export default function LoginPage() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const { staff } = await api<{ staff: Staff }>("/login", {
        method: "POST",
        body: { username: form.get("username"), password: form.get("password") },
      });
      window.location.assign(homeFor(staff));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(
        status === 401
          ? "That username and password do not match."
          : status === 429
            ? "Too many attempts. Wait a minute and try again."
            : "Something went wrong. Try again.",
      );
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <h1>Staff sign in</h1>
      <form onSubmit={submit} className={styles.form}>
        <label>
          Username
          <input name="username" required autoComplete="username" />
        </label>
        <label>
          Password
          <input name="password" type="password" required autoComplete="current-password" />
        </label>
        <p role="alert" className={styles.error}>
          {error}
        </p>
        <button type="submit" disabled={busy}>
          Sign in
        </button>
      </form>
    </main>
  );
}
