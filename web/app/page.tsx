"use client";

import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Student } from "@/lib/types";
import { StudentHeader } from "@/app/student-header";
import forms from "@/app/forms.module.css";
import styles from "@/app/student.module.css";

const MESSAGES: Record<string, string> = {
  bad_code: "That code does not work. Check your slip, or ask your teacher.",
  bad_name: "Type your name (up to 60 characters).",
  too_many_attempts: "Too many tries. Wait a minute and try again.",
};

function JoinForm({ onJoined }: { onJoined: (s: Student) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const { student } = await api<{ student: Student }>("/join", {
        method: "POST",
        body: { code: form.get("code"), name: form.get("name") },
      });
      onJoined(student);
    } catch (e) {
      const code = e instanceof ApiError ? (e.body as { error?: string } | null)?.error : undefined;
      setError((code && MESSAGES[code]) || "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <main className={forms.page}>
      <h1>Join your class</h1>
      <p>Your teacher can read everything you write here.</p>
      <form onSubmit={submit} className={forms.form}>
        <label>
          Code from your slip
          <input name="code" required inputMode="numeric" autoComplete="off" />
        </label>
        <label>
          Your name
          <input name="name" required maxLength={60} autoComplete="name" />
        </label>
        <p role="alert" className={forms.error}>
          {error}
        </p>
        <button type="submit" disabled={busy}>
          Join
        </button>
      </form>
    </main>
  );
}

export default function StudentPage() {
  const [student, setStudent] = useState<Student | null | undefined>(undefined);

  useEffect(() => {
    const check = () =>
      api<{ student: Student | null }>("/student/me")
        .then((r) => setStudent(r.student))
        .catch(() => setStudent((s) => s ?? null));
    check();
    // Notice a pause, resume or close without a reload (live updates come in step 7).
    const timer = setInterval(check, 15000);
    return () => clearInterval(timer);
  }, []);

  if (student === undefined) return <main className={forms.page}>Loading…</main>;
  if (student === null) return <JoinForm onJoined={setStudent} />;
  return (
    <>
      <StudentHeader student={student} />
      <main className={forms.page}>
        {student.state === "paused" && (
          <p role="status" className={styles.notice}>
            Your teacher has paused the class. You can't send messages until it starts again.
          </p>
        )}
        <p>Your teacher can read everything you write here.</p>
        <p>Chat is not switched on yet.</p>
      </main>
    </>
  );
}
