export type LessonState = "open" | "paused" | "closed";

export type ClassInfo = {
  id: number;
  name: string;
  instructions: string;
  message_limit: number | null;
  live_session: { id: number; state: LessonState } | null;
  teacher: string;
};

export type RosterEntry = { id: number; code: string; name: string | null; bound_at: string | null };

export type LessonSession = {
  id: number;
  state: LessonState;
  class_id: number;
  class_name: string;
  opened_at: string;
  closed_at: string | null;
  roster: RosterEntry[];
};

export type Student = {
  name: string;
  code: string;
  class_name: string;
  state: LessonState;
  message_limit: number | null;
};
