-- Initial schema for the lab pilot. See docs/architecture.md "Data".
-- Runs inside the migration runner's transaction: no BEGIN/COMMIT here.

CREATE TABLE staff (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role          text NOT NULL CHECK (role IN ('admin', 'teacher')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE classes (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    teacher_id    bigint NOT NULL REFERENCES staff (id),
    name          text NOT NULL,
    instructions  text NOT NULL DEFAULT '',
    message_limit integer CHECK (message_limit > 0),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lesson_sessions (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    class_id  bigint NOT NULL REFERENCES classes (id) ON DELETE CASCADE,
    state     text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'paused', 'closed')),
    opened_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    CHECK ((state = 'closed') = (closed_at IS NOT NULL))
);

-- One row per code. Unbinding marks the row removed and adds a fresh row with
-- the same code, so only rows that are not removed must be unique.
CREATE TABLE students (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lesson_session_id bigint NOT NULL REFERENCES lesson_sessions (id) ON DELETE CASCADE,
    code              text NOT NULL CHECK (code ~ '^[0-9]{6}$'),
    name              text,
    bound_at          timestamptz,
    removed           boolean NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK ((name IS NULL) = (bound_at IS NULL))
);
CREATE UNIQUE INDEX students_live_code ON students (lesson_session_id, code) WHERE NOT removed;
CREATE INDEX students_code ON students (code) WHERE NOT removed;

-- Cookie sessions for staff and students. Only the sha256 of the cookie token
-- is stored. Exactly one of staff_id and student_id is set.
CREATE TABLE auth_sessions (
    token_hash bytea PRIMARY KEY CHECK (length(token_hash) = 32),
    staff_id   bigint REFERENCES staff (id) ON DELETE CASCADE,
    student_id bigint REFERENCES students (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK ((staff_id IS NULL) <> (student_id IS NULL))
);
CREATE INDEX auth_sessions_staff ON auth_sessions (staff_id);
CREATE INDEX auth_sessions_student ON auth_sessions (student_id);

CREATE TABLE workers (
    id             text PRIMARY KEY,
    address        text NOT NULL,
    api_key        text NOT NULL,
    models         jsonb NOT NULL DEFAULT '[]',
    capacity       integer NOT NULL CHECK (capacity > 0),
    last_heartbeat timestamptz NOT NULL,
    removed        boolean NOT NULL DEFAULT false
);

CREATE TABLE models (
    name       text PRIMARY KEY,
    enabled    boolean NOT NULL DEFAULT false,
    first_seen timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id bigint NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    model      text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_student ON conversations (student_id);

CREATE TABLE messages (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id bigint NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('user', 'assistant')),
    text            text NOT NULL DEFAULT '',
    status          text NOT NULL CHECK (status IN ('ok', 'blocked', 'error')),
    worker_id       text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation ON messages (conversation_id, id);
CREATE INDEX messages_created ON messages (created_at);

-- Flags exist only for the flagging actions; "block" alone records no flag.
CREATE TABLE flags (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id  bigint NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    category    text NOT NULL,
    action      text NOT NULL CHECK (action IN ('allow_flag', 'block_flag')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    reviewed_by bigint REFERENCES staff (id) ON DELETE SET NULL,
    reviewed_at timestamptz
);
CREATE INDEX flags_message ON flags (message_id);

CREATE TABLE settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Staff actions (R7.4). The username is copied so history survives the
-- account being deleted.
CREATE TABLE audit (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    staff_id   bigint REFERENCES staff (id) ON DELETE SET NULL,
    username   text NOT NULL,
    action     text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_created ON audit (created_at);
