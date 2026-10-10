"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";

export type Role = "admin" | "teacher";
export type Staff = { username: string; role: Role };

export type StaffState =
  | { status: "loading" }
  | { status: "allowed"; staff: Staff }
  | { status: "forbidden"; staff: Staff }
  | { status: "error" };

/** Where a staff member goes after signing in. */
export function homeFor(staff: Staff): string {
  return staff.role === "admin" ? "/admin" : "/teach";
}

/**
 * The signed-in staff member for a page that needs `role`. Anonymous
 * visitors are sent to /setup while no admin exists, otherwise to /login.
 * An admin passes a teacher check.
 */
const navigate = (path: string) => window.location.assign(path);

export function useStaff(role: Role, go: (path: string) => void = navigate): StaffState {
  const [state, setState] = useState<StaffState>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { staff } = await api<{ staff: Staff | null }>("/me").catch(() => ({ staff: undefined }));
      if (staff === undefined) {
        if (!cancelled) setState({ status: "error" });
        return;
      }
      if (cancelled) return;
      if (!staff) {
        const status = await api<{ needed: boolean }>("/setup").catch(() => undefined);
        if (cancelled) return;
        if (status === undefined) setState({ status: "error" });
        else go(status.needed ? "/setup" : "/login");
        return;
      }
      const ok = role === "teacher" || staff.role === "admin";
      setState({ status: ok ? "allowed" : "forbidden", staff });
    })();
    return () => {
      cancelled = true;
    };
  }, [role, go]);
  return state;
}

/** Sign out and go to /login, even if the server did not answer. */
export async function signOut(): Promise<void> {
  await api("/logout", { method: "POST" }).catch(() => undefined);
  window.location.assign("/login");
}

/** Send a staff page whose session has ended back to /login. */
export function onAuthError(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) window.location.assign("/login");
}
