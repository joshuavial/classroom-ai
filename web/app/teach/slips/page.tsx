"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { onAuthError } from "@/lib/session";
import type { LessonSession } from "@/lib/types";
import styles from "@/app/teach/slips/slips.module.css";
import { Slips } from "@/app/teach/slips/slips";

export default function SlipsPage() {
  const [session, setSession] = useState<LessonSession | null>(null);
  const [message, setMessage] = useState("Loading…");

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("session");
    if (!id) {
      setMessage("No lesson chosen. Open this page from the teacher page.");
      return;
    }
    api<LessonSession>(`/sessions/${encodeURIComponent(id)}`)
      .then(setSession)
      .catch((e: unknown) => {
        onAuthError(e);
        setMessage("Could not load this lesson.");
      });
  }, []);

  if (!session) return <main className={styles.sheet}>{message}</main>;
  return <Slips session={session} address={`${window.location.origin}/`} />;
}
