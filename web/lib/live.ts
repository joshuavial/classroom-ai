"use client";

import { useEffect, useRef } from "react";

/** Event names the app sends on a lesson session's stream. */
export const LIVE_EVENTS = ["hello", "resync", "join", "state", "roster", "message", "flag"] as const;

/**
 * Call `onChange` (with bounded coalescing) whenever the lesson session's live
 * stream says something changed, and on connect and reconnect ("hello") so
 * the page fetches fresh state. The browser's EventSource reconnects on its
 * own.
 */
export function useLiveSession(sessionId: number, onChange: () => void, delay = 300): void {
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    let source: EventSource;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const fire = () => {
      if (timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        latest.current();
      }, delay);
    };
    const connect = () => {
      source = new EventSource(`/api/sessions/${sessionId}/events`);
      for (const name of LIVE_EVENTS) source.addEventListener(name, fire);
      // EventSource retries a dropped stream itself, but gives up for good
      // after an HTTP error such as 401; try again every 15 seconds.
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) {
          clearTimeout(retry);
          retry = setTimeout(connect, 15000);
        }
      };
    };
    connect();
    return () => {
      clearTimeout(timer);
      clearTimeout(retry);
      source.close();
    };
  }, [sessionId, delay]);
}
