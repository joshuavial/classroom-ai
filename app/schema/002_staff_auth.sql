-- Step 1: each cookie session carries its own CSRF token. Nothing creates
-- auth_sessions rows before this migration, so no backfill is needed.
ALTER TABLE auth_sessions ADD COLUMN csrf_token text NOT NULL;
