# Checklist: sessions, cookies, connections and locks

Use this when a plan or a change touches sign-in, cookies, CSRF, the database pool or row locks. Every item here was found by a review seat after the code was written, one round at a time, during steps 1 and 3. Answer each in the plan instead.

## Sessions and CSRF

- List every path that creates, replaces or ends a session (setup, login, join, logout, close, unbind, remove). For each, say which CSRF check applies: pre-session (header equals cookie) or the session's own token.
- Replacing a live session on a device is a signed-in request: it needs that session's token.
- A fresh browser has no token. Say how the first state-changing request gets one, and test it with an empty cookie jar.
- A failed sign-out must not look like a successful one.
- Test cookies by sending the `Cookie` header directly. httpx drops cookies set with a `domain` it does not match, so such a test passes without exercising anything.

## Pool connections

- Never take a second pool connection while holding one (inside `pool.connection()` or a transaction). Load the session before opening the request's connection, or pass the connection down.
- Prove it with a one-connection pool: the request must finish.

## Locks and races

- For each write, name the rows it locks and in which order. Two paths that lock the same tables must lock them in the same order.
- For each read-then-write on state another request can change (bind, close, unbind, remove, the message limit), either lock the rows the decision depends on, or make the write conditional and handle zero rows.
- A race test must prove the request is actually waiting (check `pg_stat_activity` for a `Lock` wait), not sleep and hope.

## Tests

- Every regression test for a fix gets a negative control: run it against the code without the fix and see it fail. Record both runs in the evidence.

## Follow-ups that relax a guarantee

- If a seat's follow-up would weaken a security or correctness property (for example "redirect to /login even if logout failed"), it is not a cheap fold. Put it to the other seat first.
