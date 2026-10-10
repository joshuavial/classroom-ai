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
  messages_used?: number;
};

export type Conversation = { id: number; model: string; started_at: string; title: string };

export type ChatMessage = {
  id: number | string;
  role: "user" | "assistant";
  text: string;
  status: string;
};

export type StudentCard = {
  id: number;
  name: string;
  code: string;
  messages: number;
  latest: { role: "user" | "assistant"; text: string; status: string; at: string } | null;
  unreviewed_flags: number;
  last_active: string | null;
};

export type Usage = {
  active_students: number;
  total_messages: number;
  per_student: { id: number; name: string; messages: number }[];
  models: { model: string; conversations: number }[];
  flags_by_category: { category: string; flags: number }[];
};

export type Flag = {
  id: number;
  category: string;
  action: string;
  at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
};

export type Transcript = {
  student: { id: number; name: string; code: string; removed: boolean };
  conversations: {
    id: number;
    model: string;
    started_at: string;
    messages: { id: number; role: "user" | "assistant"; text: string; status: string; at: string; flags: Flag[] }[];
  }[];
};
