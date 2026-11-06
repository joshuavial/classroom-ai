-- Seed the selected guard model's complete category list without replacing an
-- administrator's winner-neutral settings.
INSERT INTO settings (key, value)
VALUES (
  'guard_categories',
  '{
    "version": 1,
    "categories": {
      "Violent": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Non-violent Illegal Acts": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Sexual Content or Sexual Acts": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "PII": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Suicide & Self-Harm": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Unethical Acts": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Politically Sensitive Topics": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Copyright Violation": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
      "Jailbreak": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."}
    },
    "uncategorized": {"action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."}
  }'::jsonb
)
ON CONFLICT (key) DO UPDATE
SET value = jsonb_set(
  CASE
    WHEN jsonb_typeof(settings.value) = 'object' THEN settings.value
    ELSE '{"version": 1, "categories": {}, "uncategorized": {"action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."}}'::jsonb
  END,
  '{categories}',
  '{
    "Violent": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Non-violent Illegal Acts": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Sexual Content or Sexual Acts": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "PII": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Suicide & Self-Harm": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Unethical Acts": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Politically Sensitive Topics": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Copyright Violation": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."},
    "Jailbreak": {"enabled": true, "action": "block_flag", "message": "This message needs a teacher to check it before chat can continue."}
  }'::jsonb || COALESCE(settings.value->'categories', '{}'::jsonb)
),
updated_at = now();
