I found a model called Laya. It is real (Convai Innovations, Apache-2.0, ModernBERT encoder), but it is a general yes/no question classifier with no built-in safety categories, so it is not a drop-in replacement. Mistral's Shieldstral 3B (Apache-2.0, released 4 Aug 2026) is new since the current pick and is the strongest open alternative. Qwen3Guard-Gen-0.6B is still the best OSI-licensed model that covers all seven categories. Two gaps remain: nobody has published CPU latency for it, and bullying is folded into a broad "Unethical Acts" category.

## 1. Comparison table

Accuracy numbers are in section 2. Sizes marked ~ are my own estimates from parameter count.

| Model | Licence (OSI?) | Params / quantised size | Runtime | CPU latency per check | SH / Sex / Viol / Hate-bully / Danger / JB / PII | Languages | Known weaknesses |
|---|---|---|---|---|---|---|---|
| **Qwen3Guard-Gen-0.6B** | Apache-2.0 (yes) | 0.75B / ~0.5–0.8 GB Q4–Q8 | GGUF quants on HF (llama.cpp) | Not published | ✓ / ✓ / ✓ / ✓ via "Unethical Acts" / ✓ via illegal acts / ✓ (prompts only) / ✓ | 119 | Generative, so output has to be decoded. Weak on ToxicChat in strict mode (65.1). Paper admits it is vulnerable to obfuscation and has bias toward some groups |
| Qwen3Guard-Gen-4B | Apache-2.0 (yes) | 4B / ~2.5 GB Q4 | GGUF | Not published | same | 119 | Roughly 6× the compute of 0.6B for 1–4 points of F1 |
| Qwen3Guard-Stream 0.6B/4B | Apache-2.0 (yes) | 0.6B/4B | transformers (token classification head; llama.cpp support not confirmed) | Not published | same categories | 119 | About 2 F1 points below Gen. Worse on ambiguous phrasing and low-resource languages |
| **Shieldstral-1.0-3B** | Apache-2.0 (yes) | 3B (Ministral-3B base) / ~2 GB Q4 | vLLM, transformers, llama.cpp via GGUF conversion | Not published. Answer is one token, so decode cost is minimal | You write the policy. Training covered all seven, including suicide, PII and jailbreak | 12, Māori not among them | Card warns of uneven language coverage and weaker results on obfuscated or long input. Released two months ago, so little outside testing |
| Laya (`convaiinnovations/laya`) | Apache-2.0 (yes) | 421M EN (ModernBERT-L) / 322M multilingual (mmBERT) | Python `laya` package, safetensors | Card says 193–464 ms on CPU, 33 ms on a T4 | No fixed taxonomy; you write the questions | 100+ (multilingual variant) | General decision model with no safety benchmarks. Card says base checkpoints are near chance zero-shot and ship over-confident |
| Llama Guard 3 1B | Llama 3.2 Community (no) | 1.1B / ~1 GB | GGUF | Not published | ✓ / ✓ / ✓ / hate only, no harassment / partial (S2, S9) / ✗ / ✓ (S7 Privacy) | 8 | No jailbreak category. Meta's own numbers: F1 0.899 with 9% FPR, against 0.939 for the 8B |
| Llama Guard 3 8B | Llama 3.1 Community (no) | 8B / ~4.9 GB Q4 | GGUF | Too slow for CPU at this load | same as 1B | 8 | ToxicChat 53.8; response classification weak (SafeRLHF 45.2) |
| Llama Guard 4 12B | Llama 4 Community (no) | 12B / ~7 GB Q4 | transformers (multimodal) | Not viable on CPU | as 3, plus S14 code abuse | 8 | Lowest scores in independent comparisons (69.1 overall in the Shieldstral report). Meta reports English recall of 69% |
| Llama Prompt Guard 2 22M / 86M | Llama 4 Community (no) | 22M / 86M (DeBERTa) | transformers, ONNX-exportable | 19 ms (22M) and 92 ms (86M) on A100. CPU should be tens of ms but is unmeasured | Jailbreak and prompt injection only | 86M covers 8 languages; 22M is effectively English | Vulnerable to adaptive attacks. Covers no content categories |
| Granite Guardian 4.1 8B | Apache-2.0 (yes) | 8B / 6.9 GB (Ollama) | GGUF, Ollama | Too slow for CPU at this load | self-harm folded under violence / ✓ / ✓ / bias and profanity / unethical / ✓ / custom criteria only | English only | English only. The only 4.1 size I found is 8B |
| Granite Guardian HAP 38M | Apache-2.0 (yes) | 38.5M (RoBERTa, 4 layers) | transformers | Very fast (unmeasured) | Hate, abuse and profanity only | English | Does not cover most of the seven categories |
| ShieldGemma 2B | Gemma terms (no) | ~2.6B / ~1.6 GB | GGUF | Not published | "dangerous" may include self-harm / ✓ / partial / ✓ / ✓ / ✗ / ✗ | English | Card says it is very sensitive to how the policy is worded. Scores 49.9 on WildGuardTest responses |
| WildGuard 7B | Apache-2.0 (yes) | 7B (Mistral) / ~4.4 GB Q4 | GGUF | Too slow for CPU | ✓ "mental health crisis" / ✓ / ✓ / ✓ / ✓ / flags adversarial harmful prompts / ✓ | English | English only. No category output |
| gpt-oss-safeguard-20b | Apache-2.0 (yes) | 21B total, 3.6B active / ~13 GB | GGUF, Ollama | Seconds per check (writes a reasoning trace) | You write the policy | Not stated | Far too slow for every turn. Only fits as a slow second stage or offline labeller |
| Nemotron 3.5 Content Safety 4B | OpenMDW plus Gemma terms (no) | 4B (Gemma-3) | transformers, vLLM | Not published | Aegis 2.0 taxonomy, which includes self-harm, harassment and PII | 12 trained, ~140 zero-shot | Gemma terms rule it out under OSI-only |
| Opir (Knowledgator) | **Weights licence not found**; paper is CC-BY-4.0 | edge under 100M (Ettin-32M, mmBERT-small); multitask uses DeBERTaV3-large | GLiClass (encoder) | GPU: 9 ms (edge), 26 ms (large) at 1024 tokens. CPU unpublished | ✓ all seven: self-harm, sexual, violence, toxicity, PII, jailbreak, child safety | 23 | Macro F1 on OpenAI Mod 0.61 and ToxicChat 0.57. New, with no independent evaluation |
| Roblox PII Classifier v2 | Apache-2.0 (yes) | 0.6B (XLM-R-large) | transformers | Not published | PII only: asking, giving, moving users off-platform | 189 | Specialist model. Trained on teen game chat, including evasion spellings |
| Detoxify / unitary toxic-bert | Apache-2.0 (yes) | 110M–280M | transformers, ONNX | Fast (unmeasured) | ✗ / sexual_explicit / threat / ✓ / ✗ / ✗ / ✗ | EN, plus a multilingual XLM-R variant | Toxicity only. No self-harm |
| KoalaAI Text-Moderation | OpenRAIL-M variant (no) | ~100M DeBERTa-v3 | transformers | Fast | ✓ / ✓ / ✓ / ✓ / ✗ / ✗ / ✗ | English | Macro F1 0.326 on its own card |

Mistral Shieldstral exists ([announcement](https://mistral.ai/news/shieldstral/), [model card](https://huggingface.co/mistralai/Shieldstral-1.0-3B), [paper](https://arxiv.org/pdf/2607.25857)).

## 2. Shared-benchmark table (F1 %)

| Benchmark | Qwen3G-0.6B Gen (strict/loose)ᵃ | Qwen3G-4B Genᵃ | Qwen3G-0.6B Streamᵃ | Qwen3G-8Bᵇ | Shieldstral-3Bᵇ | gpt-oss-sg-20Bᵇ | WildGuard-7Bᵇ | LG3-8Bᵃ | LG4-12Bᵇ | ShieldGemma-9Bᵇ | Granite G 4.1 8Bᶜ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ToxicChat (prompt) | 65.1 / 77.7 | 69.5 / 82.8 | 72.0 / 75.5 | 75.6 | **84.1** | 79.8 | 71.2 | 53.8 | 51.0 | 62.4 | 78 |
| OpenAI Mod (prompt) | 66.5 / 77.6 | 68.3 / 80.7 | 68.3 / 76.0 | 74.7 | 81.4 | **84.0** | 72.0 | 79.5 | 73.9 | 78.6 | – |
| Aegis 2.0 (prompt) | 85.0 / 83.3 | 85.8 / 82.1 | 84.9 / 81.7 | 84.6 | 86.2 | 84.4 | 81.6 | 76.4 | 71.5 | 65.8 | 83 (Aegis) |
| WildGuardTest (prompt) | 87.7 / 85.1 | 85.6 / 85.1 | 87.1 / 86.0 | 88.2 | 88.1 | 87.3 | **88.9** | 76.4 | 74.3 | 46.0 | – |
| BeaverTails (response) | 86.1 / 85.4 | 86.6 / 85.2 | 84.5 / 84.0 | **85.9** | 85.0 | 83.8 | 84.3 | 67.9 | 69.8 | 54.0 | 79 |
| XSTest (response) | 89.7 / 91.3 | 92.7 / 92.4 | 84.8 / 83.3 | 92.0 | 93.5 | 93.8 | **94.7** | 89.8 | 89.0 | 80.6 | 90 |
| WildGuardTest (response) | 76.3 / 77.3 | 79.5 / 77.3 | 76.3 / 75.8 | 79.6 | 80.4 | **80.7** | 75.4 | 69.5 | 66.8 | 34.5 | – |

Sources:
- ᵃ [Qwen3Guard tech report](https://arxiv.org/html/2510.14276v1), Tables 2, 3, 11 and 12.
- ᵇ [Shieldstral report, Fig. 5](https://arxiv.org/pdf/2607.25857). Qwen3Guard is averaged over strict and loose there. Overall averages: Shieldstral 84.9, gpt-oss-sg-20B 84.9, Qwen3G-8B 84.0, WildGuard 79.5, LG4 69.1, ShieldGemma 54.7.
- ᶜ [Granite Guardian 4.1 card](https://huggingface.co/ibm-granite/granite-guardian-4.1-8b).
- ShieldGemma 2B on its own card ([source](https://huggingface.co/google/shieldgemma-2b)) scores OpenAI Mod 81.2 and ToxicChat 70.4, using an optimal threshold, so it is not directly comparable.
- [Artificial Analysis (June 2026)](https://artificialanalysis.ai/articles/guardrail-safety-benchmark) tested 19 guards on WildGuardTest, ToxicChat and XSTest with B200 latency, but publishes only charts.

Small encoder models do not report on these benchmarks in comparable form. Opir reports macro F1 (OpenAI Mod 0.61, Aegis 0.93, WildGuard prompt 0.98, ToxicChat 0.57; [paper](https://arxiv.org/html/2605.29659v1)). No published numbers exist for 0.6B-class guards on children's or school content.

## 3. Recommendation

**Primary: keep Qwen3Guard-Gen-0.6B.**
- It is the only OSI-licensed model under 1B that names all seven categories, including PII and jailbreak.
- It covers 119 languages and runs in llama.cpp.
- Its accuracy is within 1–4 F1 of its 4B and 8B siblings and above Llama Guard 3/4.
- Run it in strict mode, so "controversial" counts as unsafe; loose mode misses more.
- To save CPU, read only the first output tokens ("Safety: X") rather than decoding the full answer.

**Main risk is CPU throughput, which is unmeasured.** Per-sentence reply checks dominate the load: 30 students × about 10 sentences per reply is roughly 5 checks per second at peak, not dozens per minute. My estimate for a 0.6B model on a typical 8-core box, with ~300 template tokens of prefill, is 0.3–1 s per check. That needs llama.cpp parallel slots and probably checks on 2–3 sentence chunks rather than every sentence. This is the first thing the bake-off has to settle.

**Two-stage design:** it makes sense only with this ordering.
- The cheap stage must be the high-recall one. Escalating flagged turns to a bigger guard can only remove false positives; anything stage 1 misses never reaches stage 2.
- The CPU encoders with an OSI licence that cover self-harm are weak or unproven (KoalaAI isn't OSI and scores 0.33 macro F1; Opir's licence is unknown; Laya has no safety benchmarks). None of them is good enough to be the recall gate for self-harm.
- So: run **Qwen3Guard-Gen-0.6B on everything**.
- Escalate unsafe, controversial and low-margin results to **Shieldstral-3B** (or Qwen3Guard-Gen-4B), which is CPU-feasible at that lower volume.
- Optionally run **Roblox PII v2** (Apache-2.0, built for teen chat evasions) alongside it as a specialist PII check.
- For self-harm, it is better to tolerate false positives and route them to a gentle response plus an adult alert than to block silently.

**If Llama Guard were allowed:** I would still not switch.
- LG3-1B lacks a jailbreak category and is behind the 0.6B Qwen model.
- LG4-12B is too large for CPU and scores lowest in the independent comparison.
- The Meta model worth adding is **Prompt Guard 2 86M** as a fast jailbreak pre-filter. It is multilingual and very fast; Meta's 92 ms figure is on an A100, CPU is unmeasured.

## 4. Proposed bake-off

**Candidates:**
1. Qwen3Guard-Gen-0.6B Q8 (the baseline).
2. Qwen3Guard-Gen-4B Q4.
3. Shieldstral-3B Q4 GGUF.
4. Opir-edge-multilang or multitask, as a candidate encoder pre-filter, only if its weights licence checks out. Otherwise swap in Prompt Guard 2 86M for the jailbreak slot, if the OSI rule is relaxed. Roblox PII v2 runs as a side check for the PII category.

**Test set (about 1,500 items, labelled by two adults, with a safeguarding lead deciding disagreements):**
- **Positives:** about 100 per category across the seven, split into prompts and replies. Each category includes:
  - direct and indirect phrasing ("I just want it all to stop", "kms", "unalive")
  - teen slang and misspellings ("k!ll", leetspeak, emoji)
  - homework framing ("for a story…") and role-play jailbreaks
  - multi-turn escalation
  - PII: phone numbers, addresses and school names, including spaced or spelled-out digits and requests to move to Snapchat
- **Benign controls (about 600, aimed at false positives):**
  - curriculum questions that touch hard topics: WW2, the Treaty of Waitangi and the land wars, reproduction and puberty in health class, drug education, Shakespeare's violence, history of racism
  - a student asking for help for a friend, which should not be blocked
  - XSTest-style safe prompts that look unsafe ("how do I kill a Python process")
- **Te reo Māori and NZ English (about 150):**
  - mixed-language text ("kia ora, can you help with my mahi")
  - Māori place and personal names, which must not trip the PII check
  - NZ slang ("chur", "munted", "hard out")
  - Māori translations of a subset of the positives and the benign controls
  - Native-speaker review is required.

**Measures:**
- Recall per category: self-harm recall ≥ 0.95 is a hard gate.
- False-positive rate on benign school questions: target ≤ 3%.
- Results broken out by language and slang subset.
- Agreement with the human-assigned category.
- On the actual server box: p50 and p95 latency, and the checks per second it sustains at 30 simulated students, with reply checks per sentence and per 3-sentence chunk.
- RAM use, and cold-start time.

## 5. Unverified

- CPU latency for every LLM guard here. Nobody publishes it, so all my latency figures are estimates.
- Whether the Qwen3Guard pairs are strict/loose in that order. They were read through a summariser, not directly from the tables.
- Whether Qwen3Guard-Stream runs in llama.cpp, and whether it uses the same category list as Gen.
- Whether te reo Māori is among Qwen3Guard's 119 languages or Roblox PII's 189.
- The licence on Opir's weights, its exact HF repo names, and its parameter counts.
- Whether a Granite Guardian HAP 125M exists, and whether 4.1 comes in any size other than 8B.
- Whether ShieldGemma's "dangerous content" definition explicitly includes self-harm.
- Laya's safety-specific accuracy. A "96.7% moderation accuracy" figure appeared only in a search snippet and not on the card. Fine-tunes such as `sentinel-laya` and `bunker-laya` are third-party.
- Quantised file sizes are estimates, except Granite's 6.9 GB.
- Shieldstral's CPU behaviour, how good its GGUF conversion is, and any independent replication of its results.
