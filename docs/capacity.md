# Capacity and Load Baseline

Date: 2026-11-06.

This document records the capacity testing harness architecture, the baseline execution parameters, the test environment, and measured baseline results.

## Load Testing Harness

The standalone load harness in `tests/load.py` evaluates the performance, stability, and integrity of the classroom chat service under concurrent student use.

### Architecture

1. **Student Simulation**
   The harness authenticates using teacher credentials provided via configuration, creates a class, generates a lesson session with access slips, and simulates multiple students concurrently. Each student maintains an independent session cookie jar, extracts CSRF tokens from the API, and joins the lesson session independently.

2. **Seeded Pacing**
   Student startup and pauses between turns use deterministic pseudo-random sequences derived from a configurable seed and student index. Each student waits an initial delay within a startup window, submits a prompt, reads the response, and pauses for a duration between configured minimum and maximum bounds before submitting the next turn.

3. **Incremental SSE Streaming**
   Each message is submitted to `/api/student/conversations/{id}/messages` and consumed as a Server-Sent Events (SSE) stream. The parser processes arbitrary byte chunks, handles UTF-8 byte sequences gracefully, and extracts text deltas. It records the time from request dispatch to receipt of the first text segment.

4. **Transcript Reconciliation**
   After all student turns complete, the harness queries the conversation history from `/api/student/conversations/{id}` for every student. It reconciles the server transcript against local attempts to verify:
   - Lost messages: every sent prompt appears in the stored transcript.
   - Unrecorded replies: every completed assistant reply matches the full streamed text.

5. **Guard Latency Reporting**
   The harness queries Postgres for guard check durations by check kind (`prompt` and `reply_segment`). Aggregates include p50, p95, and maximum durations. If guard checks have not been recorded, the harness reports `no_samples`.

6. **Exit Code Semantics**
   The harness emits an atomic JSON report and exits with code 0 only when all attempts succeed with zero lost messages, zero unrecorded replies, zero HTTP failures, zero stream drops, and no failed database queries.

## Baseline Execution

This baseline run acts as a pre-guard concurrency rehearsal, executed against the streaming fake worker to validate concurrent client load and stream reconciliation.

### Test Parameters

The baseline validation executed with the following configuration:

| Parameter | Value |
|---|---|
| Date | 2026-11-06 |
| Students | 30 |
| Turns per student | 3 |
| Total attempts | 90 |
| Worker concurrency (`MAX_CONCURRENT`) | 32 |
| Seed | 1 |
| Startup window | 5.0 s |
| Minimum pause | 0.2 s |
| Maximum pause | 1.0 s |
| Request timeout | 60.0 s |
| Model | gemma-4-e2b-it (harness --model argument) |
| Worker backend | Streaming fake model worker (`tests/fake_model_server.py`) with canned reply pieces |

### Environment

- Platform: macOS 26.5.2 (Build 25F84)
- Hardware: Apple Silicon Mac (Mac16,6), 128 GB memory
- GPU: None (CPU only execution on development Mac)
- Container runtime: Docker 29.8.1
- Services: Docker Compose stack with Caddy 2.11.7, Starlette application, Web client, and PostgreSQL 18.6 (guard service absent during this pre-guard rehearsal)
- Python runtime: Python 3.14.8 managed by uv 0.13.4

### Baseline Results

The baseline run completed cleanly with zero infrastructure drops and full transcript consistency:

| Metric | Result |
|---|---|
| Attempted | 90 |
| Completed successfully (`done`) | 90 |
| Blocked | 0 |
| Error terminals | 0 |
| HTTP failures | 0 |
| Stream failures | 0 |
| Lost messages | 0 |
| Unrecorded replies | 0 |
| Elapsed time | 13.97 s |
| Throughput | 6.44 attempts/s |

### Time to First Segment

Time to first segment measures the latency between client request dispatch and receipt of the first streaming text delta:

| Percentile | Latency (ms) |
|---|---|
| p50 | 39.13 |
| p95 | 136.90 |
| max | 229.35 |

### Guard Latency Status

The database query in `tests/load.py` returned status `no_samples` (`available: false`).

The `guard_checks` table exists in PostgreSQL from schema migration 005, but zero check rows were written during this rehearsal because the server stack ran without the guard service prior to its integration in commit `0d096c8`. While `docs/pilot-checks.md` anticipates rehearsal runs reporting `pending_step_6` before schema migration 005, once migration 005 exists the query executes against the schema and reports `no_samples`. Live guard latency metrics will be measured with the active safety guard on the reference server.

### Reference Server Capacity Note

The figures recorded above represent development Mac measurements with a streaming fake worker. Final pilot capacity claims must be measured on the reference server (the dedicated Windows GPU host) as required by the implementation plan and decision records.
