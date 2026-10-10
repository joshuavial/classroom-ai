"use client";

import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { sendMessage } from "@/lib/chat";
import type { ChatMessage, Conversation, Student } from "@/lib/types";
import styles from "@/app/chat.module.css";

const REFUSALS: Record<string, string> = {
  paused: "Your teacher has paused the class. You can't send messages until it starts again.",
  limit_reached: "You have used all your messages for this lesson.",
  model_off: "That model has been turned off. Pick another one.",
  chat_unavailable: "Chat is unavailable right now. Tell your teacher.",
  bad_text: "Type a message of up to 4,000 characters.",
};

function refusal(e: unknown): { code?: string; text: string } {
  const code = e instanceof ApiError ? (e.body as { error?: string } | null)?.error : undefined;
  return { code, text: (code && REFUSALS[code]) || "Something went wrong. Try again." };
}

export function Chat({ student, onChanged }: { student: Student; onChanged: () => void }) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const loadModels = useCallback(async () => {
    const { models } = await api<{ models: string[] }>("/student/models").catch(() => ({ models: [] as string[] }));
    setModels(models);
    setModel((m) => (models.includes(m) ? m : (models[0] ?? "")));
  }, []);
  const loadConversations = useCallback(async () => {
    const r = await api<{ conversations: Conversation[] }>("/student/conversations").catch(() => null);
    if (r) setConversations(r.conversations);
  }, []);

  useEffect(() => {
    loadModels();
    loadConversations();
    // A model turned off disappears from the picker within 15 seconds.
    const timer = setInterval(loadModels, 15000);
    return () => clearInterval(timer);
  }, [loadModels, loadConversations]);

  async function open(convo: Conversation) {
    setNotice("");
    const r = await api<{ messages: ChatMessage[] }>(`/student/conversations/${convo.id}`).catch(() => null);
    setCurrent(convo);
    setMessages(r?.messages ?? []);
  }

  async function startNew() {
    setNotice("");
    try {
      const convo = await api<Conversation>("/student/conversations", { method: "POST", body: { model } });
      setCurrent(convo);
      setMessages([]);
      loadConversations();
    } catch (e) {
      const { code, text } = refusal(e);
      setNotice(text);
      if (code === "model_off") loadModels();
    }
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!current || !text || busy) return;
    setBusy(true);
    setNotice("");
    const replyId = `reply-${Date.now()}`;
    setMessages((m) => [
      ...m,
      { id: `you-${Date.now()}`, role: "user", text, status: "ok" },
      { id: replyId, role: "assistant", text: "", status: "pending" },
    ]);
    setDraft("");
    const update = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((all) => all.map((m) => (m.id === replyId ? fn(m) : m)));
    try {
      const outcome = await sendMessage(current.id, text, (delta) => update((m) => ({ ...m, text: m.text + delta })));
      if (outcome.kind === "done") update((m) => ({ ...m, status: "ok" }));
      else if (outcome.kind === "blocked")
        update((m) => ({ ...m, status: "blocked", text: m.text ? `${m.text}\n${outcome.message}` : outcome.message }));
      else update((m) => ({ ...m, status: "error", text: m.text ? `${m.text}\n${outcome.message}` : outcome.message }));
    } catch (e) {
      const { code, text: message } = refusal(e);
      setMessages((all) => all.filter((m) => m.id !== replyId));
      setNotice(message);
      if (code === "model_off") loadModels();
      if (code === "paused" || code === "limit_reached") onChanged();
    } finally {
      setBusy(false);
      loadConversations();
      onChanged();
    }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  const left =
    student.message_limit === null ? null : Math.max(0, student.message_limit - (student.messages_used ?? 0));
  const paused = student.state === "paused";

  return (
    <div className={styles.chat}>
      <nav aria-label="Your conversations" className={styles.side}>
        <label>
          Model{" "}
          <select value={model} onChange={(e) => setModel(e.target.value)} onFocus={loadModels}>
            {models.length === 0 && <option value="">No models available</option>}
            {models.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={startNew} disabled={!model}>
          New conversation
        </button>
        <ul>
          {conversations.map((c) => (
            <li key={c.id}>
              <button type="button" onClick={() => open(c)} aria-current={current?.id === c.id}>
                {c.title || "New conversation"} <small>({c.model})</small>
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <section aria-label="Chat" className={styles.main}>
        <p className={styles.monitor}>Your teacher can read everything you write here.</p>
        {left !== null && <p>Messages left this lesson: {left}</p>}
        <p role="status">{notice}</p>
        {current ? (
          <>
            <div role="log" aria-live="polite" aria-label="Conversation" className={styles.log}>
              {messages.map((m) => (
                <p key={m.id} className={m.role === "user" ? styles.you : styles.ai}>
                  <strong>{m.role === "user" ? "You" : "AI"}:</strong> {m.text || (m.status === "pending" ? "…" : "")}
                </p>
              ))}
            </div>
            <form onSubmit={send} className={styles.form}>
              <label htmlFor="chat-input">Your message</label>
              <textarea
                id="chat-input"
                value={draft}
                rows={3}
                maxLength={4000}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={keyDown}
                disabled={busy || paused || left === 0}
              />
              <button type="submit" disabled={busy || paused || left === 0 || !draft.trim()}>
                Send
              </button>
            </form>
          </>
        ) : (
          <p>Pick a model and start a new conversation, or open one from the list.</p>
        )}
      </section>
    </div>
  );
}
