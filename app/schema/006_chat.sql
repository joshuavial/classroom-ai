-- Step 4: chat turns.
-- A user message is 'pending' from the moment it is stored until the guard's
-- verdict is stored; only 'ok' prompts ever go to a worker.
ALTER TABLE messages DROP CONSTRAINT messages_status_check;
ALTER TABLE messages ADD CONSTRAINT messages_status_check
    CHECK (status IN ('pending', 'ok', 'blocked', 'error'));
-- Assistant replies: how many characters of text the student was shown.
-- The rest (held back by the guard, or never released) stays for the teacher.
ALTER TABLE messages ADD COLUMN shown_length integer CHECK (shown_length >= 0);
-- The guard's verdict on this message: action, categories, student message.
ALTER TABLE messages ADD COLUMN verdict jsonb;
