# ADR-0008: Cpu guard on the server

Status: Accepted. Date: 2026-10-12.
## Context

Checks must work when no GPU worker is up.

## Decision

Qwen3Guard-Gen-0.6B Q8_0 (community GGUF, pinned by file hash) runs on a CPU llama-server inside the server stack. Controversial counts as unsafe. If the guard is down, chat stops.

The Step 5 bake-off compared it with Qwen3Guard-Gen-4B Q8_0 and Shieldstral-1.0-3B Q8_0 on a 1,500-item corpus (450 independent XSTest prompts and 1,050 templated original classroom items). Qwen 0.6B was selected based on independent prompt recall (95.50% on XSTest), complete recall across the 89 original unsafe frames, and CPU latency (4.45 times the batch-active throughput of Qwen 4B in the same-window interleaved pass). Qwen 4B had a lower benign false-positive rate on independent prompts (14.00% vs 21.60%), but its ~49 second CPU check latency is too slow for interactive generation. Shieldstral required 7 calls per check (~92 seconds on CPU) and failed on the templated PII reply frame. Because all original rows are template-generated, Wilson intervals apply only to independent XSTest prompts, and reply self-harm has no independent evidence. See `docs/guard-results.md` for frame-level results and limitations.

## Consequences

Guard speed on CPU limits class size. The bake-off latency is relative evidence from a shared development Mac. The reference-server load test measures absolute latency and 30-student capacity.
