"use client";

import { useEffect, useState } from "react";

type Health = { guard?: "ok" | "down" };

export default function GuardHealth() {
  const [guard, setGuard] = useState<"checking" | "ok" | "down">("checking");

  useEffect(() => {
    let active = true;
    const check = () => {
      fetch("/healthz", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(async (response) => (await response.json()) as Health)
        .then((health) => active && setGuard(health.guard === "ok" ? "ok" : "down"))
        .catch(() => active && setGuard("down"));
    };
    check();
    const timer = window.setInterval(check, 15_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  if (guard !== "down") return null;
  return (
    <p role="alert">
      The guard is unavailable. Student chat is blocked until the guard is healthy.
    </p>
  );
}
