-- Worker registry (implementation plan step 2). The join token lives in
-- settings under 'join_token' as {"token": ..., "generation": n}; rotating it
-- bumps the generation, and a worker counts as live only while its last
-- heartbeat carried the current generation.

ALTER TABLE workers
    ADD COLUMN token_generation integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT workers_id_uuid
        CHECK (id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    ADD CONSTRAINT workers_capacity_max CHECK (capacity <= 64);
