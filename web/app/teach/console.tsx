"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { onAuthError } from "@/lib/session";
import type { StudentCard, Transcript, Usage } from "@/lib/types";
import styles from "@/app/teach/console.module.css";

export function UsageStrip({ usage }: { usage: Usage | null }) {
  if (!usage) return null;
  const flags = usage.flags_by_category.map((f) => `${f.category} ${f.flags}`).join(", ");
  const models = usage.models.map((m) => `${m.model} ${m.conversations}`).join(", ");
  return (
    <section aria-label="Usage" className={styles.usage}>
      <span>
        Active in the last 5 minutes: <strong>{usage.active_students}</strong>
      </span>
      <span>
        Messages: <strong>{usage.total_messages}</strong>
      </span>
      <span>Models: {models || "none yet"}</span>
      <span>Flags: {flags || "none"}</span>
    </section>
  );
}

export function CardGrid({
  cards,
  selected,
  onSelect,
}: {
  cards: StudentCard[];
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  if (cards.length === 0) return <p>No students have joined yet.</p>;
  return (
    <ul className={styles.grid} aria-label="Students">
      {cards.map((card) => {
        const flagged = card.unreviewed_flags > 0;
        const classes = [styles.card, flagged && styles.flagged, selected === card.id && styles.selected];
        return (
          <li key={card.id}>
            <button
              type="button"
              className={classes.filter(Boolean).join(" ")}
              aria-pressed={selected === card.id}
              onClick={() => onSelect(card.id)}
            >
              <strong>{card.name}</strong> <span>({card.code})</span>
              {flagged && <span className={styles.badge}>Flagged: {card.unreviewed_flags}</span>}
              <br />
              <span>
                {card.messages} {card.messages === 1 ? "message" : "messages"}
              </span>
              <span className={styles.latest}>
                {card.latest
                  ? `${card.latest.role === "user" ? "" : "AI: "}${card.latest.text || "(no text)"}${
                      card.latest.status === "blocked" ? " [blocked]" : ""
                    }`
                  : "No messages yet"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function TranscriptPanel({
  transcript,
  onReview,
  onClose,
}: {
  transcript: Transcript;
  onReview: (flagId: number) => void;
  onClose: () => void;
}) {
  const { student } = transcript;
  const heading = useRef<HTMLHeadingElement>(null);
  // Move focus to the panel when a different student's transcript opens.
  useEffect(() => heading.current?.focus(), [student.id]);
  return (
    <section className={styles.panel} aria-label={`Transcript for ${student.name}`}>
      <h3 ref={heading} tabIndex={-1}>
        {student.name} ({student.code}){student.removed ? " (removed)" : ""}{" "}
        <button type="button" onClick={onClose}>
          Close
        </button>
      </h3>
      {transcript.conversations.length === 0 && <p>No conversations yet.</p>}
      {transcript.conversations.map((convo, i) => (
        <article key={convo.id} aria-label={`Conversation ${i + 1}`}>
          <h4>
            Conversation {i + 1}, {convo.model}
          </h4>
          {convo.messages.map((m) => (
            <div key={m.id} className={[styles.message, m.status === "blocked" && styles.blocked].filter(Boolean).join(" ")}>
              <strong>{m.role === "user" ? student.name : "AI"}:</strong> {m.text || "(no text)"}
              {m.status !== "ok" && <em> ({m.status})</em>}
              {m.flags.map((f) => (
                <div key={f.id} className={styles.flag}>
                  Flag: {f.category} ({f.action === "block_flag" ? "blocked and flagged" : "allowed and flagged"}).{" "}
                  {f.reviewed_at ? (
                    <span>Reviewed by {f.reviewed_by ?? "a removed account"}.</span>
                  ) : (
                    <button type="button" onClick={() => onReview(f.id)}>
                      Mark reviewed
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </article>
      ))}
    </section>
  );
}

/** Cards, usage strip and transcript for a lesson session; `version` bumps
 * on every live event so everything reloads in place. */
export function Console({ sessionId, version }: { sessionId: number; version: number }) {
  const [cards, setCards] = useState<StudentCard[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [notice, setNotice] = useState("");
  // The student whose transcript should be showing; a late reply for any
  // other student is dropped.
  const wanted = useRef<number | null>(null);
  const loadVersion = useRef(0);
  const transcriptVersion = useRef(0);

  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    try {
      const [c, u] = await Promise.all([
        api<{ students: StudentCard[] }>(`/sessions/${sessionId}/students`),
        api<Usage>(`/sessions/${sessionId}/usage`),
      ]);
      if (version !== loadVersion.current) return;
      setCards(c.students);
      setUsage(u);
    } catch (e) {
      if (version !== loadVersion.current) return;
      onAuthError(e);
    }
  }, [sessionId]);

  const loadTranscript = useCallback(async (id: number | null) => {
    const version = ++transcriptVersion.current;
    wanted.current = id;
    if (id === null) return setTranscript(null);
    try {
      const t = await api<Transcript>(`/students/${id}/transcript`);
      if (wanted.current === id && version === transcriptVersion.current) setTranscript(t);
    } catch (e) {
      onAuthError(e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, version]);
  useEffect(() => {
    loadTranscript(selected);
  }, [loadTranscript, selected, version]);

  async function review(flagId: number) {
    setNotice("");
    try {
      await api(`/flags/${flagId}/review`, { method: "POST" });
      setNotice("Marked reviewed.");
    } catch (e) {
      onAuthError(e);
      setNotice(
        e instanceof ApiError && e.status === 409
          ? "Someone has already marked that flag reviewed."
          : "Could not mark the flag reviewed. Try again.",
      );
    }
    load();
    loadTranscript(selected);
  }

  return (
    <>
      <UsageStrip usage={usage} />
      <p role="status">{notice}</p>
      <div className={[styles.layout, transcript && styles.open].filter(Boolean).join(" ")}>
        <CardGrid cards={cards} selected={selected} onSelect={(id) => setSelected(id === selected ? null : id)} />
        {transcript && <TranscriptPanel transcript={transcript} onReview={review} onClose={() => setSelected(null)} />}
      </div>
    </>
  );
}
