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

## Guard Memory Sizing and Resource Limits

The guard service runs `llama-server` hosting Qwen3Guard-Gen-0.6B with 8-bit quantized weights (`Q8_0`).

### Production Sizing

The production reference configuration in `compose.yml` provisions 30 parallel slots (`--parallel 30`) with a unified context window of 4,096 tokens per slot (`--kv-unified-per-slot 4096`) using 16-bit floating point key and value cache storage (`--cache-type-k f16 --cache-type-v f16`).

This configuration pre-allocates 13,440 MiB (approximately 13.1 GiB) exclusively for the key-value context buffer, resulting in approximately 14.5 GiB of resident memory for the guard process alone.

### Development Environment Constraints

Development machines running Docker Desktop inside a virtual machine often operate under a restricted memory ceiling (such as 32 GiB total virtual machine RAM). When the Docker virtual machine hosts the full application stack alongside adjacent local development services, allocating 13,440 MiB for the guard KV cache can exceed available virtual memory and trigger the Linux kernel out-of-memory killer.

To prevent out-of-memory terminations in development and test environments while maintaining production defaults, `compose.yml` parameterizes guard sizing through environment variables:

- `GUARD_PARALLEL`: number of parallel inference slots (defaults to 30)
- `GUARD_THREADS`: number of CPU inference threads (defaults to 8)
- `GUARD_CACHE_K`: key cache format (defaults to `f16`, overrideable to `q8_0`)
- `GUARD_CACHE_V`: value cache format (defaults to `f16`, overrideable to `q8_0`)
- `GUARD_CACHE_RAM`: prompt cache memory in MiB (defaults to 0)

Setting `GUARD_CACHE_K=q8_0` and `GUARD_CACHE_V=q8_0` reduces the key-value buffer size to approximately 7,140 MiB (~7.0 GiB), allowing concurrent rehearsals and test suites to execute reliably within development virtual machines without kernel out-of-memory intervention.

### Timeout and Row Lifecycle Contract

The guard client timeout in `app/guard.py` is standardized at `GUARD_TIMEOUT_SECONDS = 60.0`. The row lifecycle separates prompt evaluation from assistant streaming:

1. **User Pending Row Lifecycle**:
   When a student submits a prompt, `admit` inserts a user row with status `pending`. The live sweep only expires pending user rows that exceed `chat.PENDING_STALE_MINUTES = 2` (120.0 seconds).
   The prompt guard check and database persistence stage (`store_verdict`) marks the user row as `ok` or `blocked` immediately upon verdict receipt, before assistant reply generation starts.
   `GUARD_TIMEOUT_SECONDS = 60.0` bounds each individual transport operation (connect, write, read, and pool acquire) to 60.0 seconds. Under typical conditions, prompt guard check execution completes well within this window, maintaining an expected 60-second safety margin before the 120-second user stale threshold (`chat.PENDING_STALE_MINUTES = 2`). A pending row that exceeds 120 seconds becomes eligible for cleanup during the student's next admission or upon server restart. If a sweep occurs before a delayed verdict returns, `store_verdict` detects the swept state and fails closed by refusing late verdict persistence. If the guard call fails or times out directly, the error handler at `app/chat.py:313-319` immediately marks the pending row as `error` without waiting for a sweep.

2. **Assistant Row Lifecycle**:
   Assistant generation streams into a separate assistant message row. Reply checks execute per segment via `ReplyGate`. For each segment check, `GUARD_TIMEOUT_SECONDS = 60.0` bounds individual transport operations (connect and chunk reads) rather than imposing an overall wall-clock deadline. If any transport operation times out or fails, the gate fails closed, marking the assistant row with status `error` or `blocked` without releasing unchecked text to the student.

## Live Guard Load Rehearsal

Date: 2026-11-06.

This rehearsal evaluates concurrent student load against the full application stack with the active CPU guard service enabled, running alongside the streaming fake model worker.

The rehearsal was executed under `GUARD_TIMEOUT_SECONDS = 30.0` using development environment overrides (`GUARD_THREADS=12`, `GUARD_CACHE_K=q8_0`, `GUARD_CACHE_V=q8_0`, `GUARD_CACHE_RAM=0`) before establishing the production standard of 60.0 seconds. Under 30 concurrent students on CPU, maximum reply segment latency reached 27.6 seconds (within 2.4 seconds of the 30.0-second limit). To provide reliable operational headroom against CPU scheduling spikes during concurrent bursts, `GUARD_TIMEOUT_SECONDS` was subsequently standardized at 60.0 seconds.

### Rehearsal Parameters

| Parameter | Value |
|---|---|
| Date | 2026-11-06 |
| Students | 30 |
| Turns per student | 3 |
| Total attempts | 90 |
| Worker concurrency (`MAX_CONCURRENT`) | 32 |
| Seed | 1 |
| Startup window | 90.0 s |
| Minimum pause | 10.0 s |
| Maximum pause | 20.0 s |
| Request timeout | 60.0 s |
| Guard timeout (`GUARD_TIMEOUT_SECONDS`) | 30.0 s |
| Model | gemma-4-e2b-it |
| Guard backend | Qwen3Guard-Gen-0.6B (Q8_0 weights, q8_0 KV cache, 12 CPU threads) |
| Worker backend | Streaming fake model worker (`tests/fake_model_server.py`) |

### Rehearsal Results

The live guard rehearsal completed cleanly with zero failures and zero transcript drops:

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
| Elapsed time | 215.55 s |
| Throughput | 0.42 attempts/s |

### Measured Guard Latency

Guard latency metrics queried directly from the `guard_checks` table in PostgreSQL by check kind:

| Check Kind | Sample Count | p50 (ms) | p95 (ms) | Max (ms) |
|---|---|---|---|---|
| Prompt (`prompt`) | 90 | 7,611.0 | 15,691.0 | 17,651.0 |
| Reply Segment (`reply_segment`) | 180 | 10,871.5 | 24,079.0 | 27,643.0 |

All 90 prompt checks and all 180 reply segment checks completed successfully within the 30.0-second rehearsal threshold, well within the standardized 60.0-second guard timeout.

### Time to First Segment Under Guard

Time to first segment measures the duration between client request dispatch, prompt guard evaluation, and receipt of the first streaming text delta:

| Percentile | Latency (ms) |
|---|---|
| p50 | 24,909.86 |
| p95 | 36,907.14 |
| max | 37,362.45 |
