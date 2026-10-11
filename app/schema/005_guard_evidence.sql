-- Guard verdict evidence and winner-neutral category settings for Step 6.
-- Runs inside the migration runner's transaction: no BEGIN/COMMIT here.

ALTER TABLE messages ADD COLUMN guard_status text;
ALTER TABLE messages ADD CONSTRAINT messages_guard_status_value
    CHECK (guard_status IS NULL OR guard_status IN ('passed', 'blocked', 'error'));
ALTER TABLE messages ADD CONSTRAINT messages_blocked_guard_consistent
    CHECK (guard_status IS NULL OR ((status = 'blocked') = (guard_status = 'blocked')));
ALTER TABLE messages ADD CONSTRAINT messages_guard_error_is_message_error
    CHECK (guard_status IS DISTINCT FROM 'error' OR status = 'error');

CREATE TABLE guard_checks (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id   bigint NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('prompt', 'reply_segment')),
    sequence     integer NOT NULL CHECK (sequence >= 0),
    raw_response text NOT NULL CHECK (octet_length(raw_response) <= 8192),
    safe         boolean NOT NULL,
    categories   jsonb NOT NULL CHECK (jsonb_typeof(categories) = 'array'),
    duration_ms  bigint NOT NULL CHECK (duration_ms >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'prompt' OR sequence = 0),
    UNIQUE (message_id, kind, sequence)
);
CREATE INDEX guard_checks_message ON guard_checks (message_id, id);

INSERT INTO settings (key, value)
VALUES (
    'guard_categories',
    '{"version":1,"categories":{},"uncategorized":{"action":"block_flag","message":"This message needs a teacher to check it before chat can continue."}}'::jsonb
)
ON CONFLICT (key) DO NOTHING;
