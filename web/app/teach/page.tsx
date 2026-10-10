"use client";

import { signOut, useStaff } from "@/lib/session";
import styles from "@/app/forms.module.css";
import { ServerDown } from "@/app/server-down";
import Models from "@/components/Models";

export default function TeachPage() {
  const state = useStaff("teacher");
  if (state.status === "error") return <ServerDown />;
  if (state.status !== "allowed") return <main className={styles.page}>Loading…</main>;
  return (
    <main className={styles.page}>
      <h1>Teacher</h1>
      <p>
        Signed in as {state.staff.username}. <button onClick={signOut}>Sign out</button>
      </p>
      <Models />
    </main>
  );
}
