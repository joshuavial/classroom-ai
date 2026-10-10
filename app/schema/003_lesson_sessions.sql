-- Step 3: a class has at most one lesson session that is not closed (A3).
CREATE UNIQUE INDEX lesson_sessions_one_live ON lesson_sessions (class_id) WHERE state <> 'closed';
