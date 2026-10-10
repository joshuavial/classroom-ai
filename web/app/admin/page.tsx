"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { type Role, onAuthError, signOut, useStaff } from "@/lib/session";
import styles from "@/app/forms.module.css";
import { ServerDown } from "@/app/server-down";
import Models from "@/components/Models";
import Workers from "@/components/Workers";
import Records from "@/components/Records";

type StaffRow = { username: string; role: Role; created_at: string };

const MESSAGES: Record<string, string> = {
  username_taken: "That username is already taken.",
  bad_username: "Use 1 to 64 letters, numbers, dots, dashes or underscores for the username.",
  bad_password: "The password needs at least 10 characters.",
};

function StaffAccounts() {
  const [rows, setRows] = useState<StaffRow[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(
    () =>
      api<{ staff: StaffRow[] }>("/staff")
        .then((r) => setRows(r.staff))
        .catch((e: unknown) => {
          onAuthError(e);
          setMessage("Could not load the staff list.");
        }),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setMessage("");
    try {
      const made = await api<{ username: string }>("/staff", {
        method: "POST",
        body: { username: form.get("username"), password: form.get("password"), role: form.get("role") },
      });
      formElement.reset();
      setMessage(`Created ${made.username}.`);
      load();
    } catch (e) {
      onAuthError(e);
      const code = e instanceof ApiError ? (e.body as { error?: string } | null)?.error : undefined;
      setMessage((code && MESSAGES[code]) || "Something went wrong. Try again.");
    }
  }

  return (
    <section aria-labelledby="staff-heading">
      <h2 id="staff-heading">Staff accounts</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Username</th>
            <th scope="col">Role</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.username}>
              <td>{r.username}</td>
              <td>{r.role}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Create a staff account</h3>
      <form onSubmit={create} className={styles.form}>
        <label>
          Username
          <input name="username" required autoComplete="off" />
        </label>
        <label>
          Password
          <input name="password" type="password" required minLength={10} autoComplete="new-password" />
        </label>
        <label>
          Role
          <select name="role" defaultValue="teacher">
            <option value="teacher">Teacher</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <p role="status">{message}</p>
        <button type="submit">Create account</button>
      </form>
    </section>
  );
}

export default function AdminPage() {
  const state = useStaff("admin");
  if (state.status === "loading") return <main className={styles.page}>Loading…</main>;
  if (state.status === "error") return <ServerDown />;
  if (state.status === "forbidden")
    return (
      <main className={styles.page}>
        <h1>Admin</h1>
        <p>You need an admin account to see this page.</p>
        <p>
          <a href="/teach">Go to the teacher page</a>
        </p>
      </main>
    );
  return (
    <main className={styles.page}>
      <h1>Admin</h1>
      <p>
        Signed in as {state.staff.username}. <button type="button" onClick={signOut}>Sign out</button>
      </p>
      <Workers />
      <Models />
      <StaffAccounts />
      <Records />
    </main>
  );
}
