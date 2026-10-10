"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { onAuthError, signOut, useStaff } from "@/lib/session";
import type { ClassInfo } from "@/lib/types";
import { ServerDown } from "@/app/server-down";
import { Lesson } from "@/app/teach/lesson";
import Models from "@/components/Models";
import forms from "@/app/forms.module.css";
import styles from "@/app/teach/teach.module.css";

function classBody(form: FormData) {
  const limit = String(form.get("message_limit") ?? "").trim();
  return {
    name: form.get("name"),
    instructions: form.get("instructions"),
    message_limit: limit === "" ? null : Number(limit),
  };
}

const MESSAGES: Record<string, string> = {
  bad_name: "Give the class a name (up to 100 characters).",
  bad_message_limit: "The message limit is a number from 1 to 1000, or empty for no limit.",
  bad_instructions: "Instructions can be up to 10,000 characters.",
  bad_count: "Choose 1 to 200 codes.",
  live_session: "This class already has a lesson running.",
};

function errorText(e: unknown): string {
  const code = e instanceof ApiError ? (e.body as { error?: string } | null)?.error : undefined;
  return (code && MESSAGES[code]) || "Something went wrong. Try again.";
}

function ClassFields({ initial }: { initial?: ClassInfo }) {
  return (
    <>
      <label>
        Class name
        <input name="name" required maxLength={100} defaultValue={initial?.name} />
      </label>
      <label>
        Instructions for the AI in every conversation
        <textarea name="instructions" rows={3} maxLength={10000} defaultValue={initial?.instructions} />
      </label>
      <label>
        Message limit per student per lesson (empty for none)
        <input name="message_limit" type="number" min={1} max={1000} defaultValue={initial?.message_limit ?? ""} />
      </label>
    </>
  );
}

function ClassItem({ info, onChange, admin }: { info: ClassInfo; onChange: () => void; admin?: string }) {
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api(`/classes/${info.id}`, { method: "PATCH", body: classBody(new FormData(event.currentTarget)) });
      setEditing(false);
      onChange();
    } catch (e) {
      onAuthError(e);
      setMessage(errorText(e));
    }
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const count = Number(new FormData(event.currentTarget).get("count"));
    try {
      await api(`/classes/${info.id}/sessions`, { method: "POST", body: { count } });
      onChange();
    } catch (e) {
      onAuthError(e);
      setMessage(errorText(e));
    }
  }

  return (
    <article className={styles.class} aria-label={info.name}>
      {editing ? (
        <form onSubmit={save} className={forms.form}>
          <ClassFields initial={info} />
          <span>
            <button type="submit">Save</button> <button type="button" onClick={() => setEditing(false)}>Cancel</button>
          </span>
        </form>
      ) : (
        <>
          <h2>{info.name}</h2>
          {admin && info.teacher !== admin && <p>Teacher: {info.teacher}</p>}
          <p>
            {info.message_limit ? `Limit: ${info.message_limit} messages per student.` : "No message limit."}{" "}
            <button type="button" onClick={() => setEditing(true)}>Edit class</button>
          </p>
        </>
      )}
      {info.live_session ? (
        <Lesson sessionId={info.live_session.id} onEnded={onChange} />
      ) : (
        <form onSubmit={start}>
          <label>
            Codes to print{" "}
            <input name="count" type="number" min={1} max={200} defaultValue={30} style={{ width: "4rem" }} />
          </label>{" "}
          <button type="submit">Start lesson</button>
        </form>
      )}
      <p role="status">{message}</p>
    </article>
  );
}

function Classes({ admin }: { admin?: string }) {
  const [classes, setClasses] = useState<ClassInfo[] | null>(null);
  const [message, setMessage] = useState("");
  const load = useCallback(
    () =>
      api<{ classes: ClassInfo[] }>("/classes")
        .then((r) => setClasses(r.classes))
        .catch((e: unknown) => {
          onAuthError(e);
          setMessage("Could not load your classes.");
        }),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    try {
      await api("/classes", { method: "POST", body: classBody(new FormData(formElement)) });
      formElement.reset();
      setMessage("");
      load();
    } catch (e) {
      onAuthError(e);
      setMessage(errorText(e));
    }
  }

  return (
    <>
      {classes?.map((c) => <ClassItem key={c.id} info={c} onChange={load} admin={admin} />)}
      {classes?.length === 0 && <p>No classes yet. Create one below.</p>}
      <section aria-labelledby="new-class" className={styles.class}>
        <h2 id="new-class">New class</h2>
        <form onSubmit={create} className={forms.form}>
          <ClassFields />
          <p role="status">{message}</p>
          <button type="submit">Create class</button>
        </form>
      </section>
    </>
  );
}

export default function TeachPage() {
  const state = useStaff("teacher");
  if (state.status === "error") return <ServerDown />;
  if (state.status !== "allowed") return <main className={forms.page}>Loading…</main>;
  return (
    <main className={styles.wide}>
      <h1>Teacher</h1>
      <p>
        Signed in as {state.staff.username}. <button type="button" onClick={signOut}>Sign out</button>
      </p>
      <Classes admin={state.staff.role === "admin" ? state.staff.username : undefined} />
      <Models />
    </main>
  );
}
