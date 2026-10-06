# Model serving and the student chat UI

Checked 6 October 2026. OSI below means MIT or Apache-2.0. Anything else is flagged.

## Serving about 30 students

All four speak an OpenAI-compatible HTTP API, so the chat app can change engines later [2][7][8][13].

**llama.cpp `llama-server`** (MIT) serves OpenAI chat, responses, and embeddings, with parallel slots and continuous batching [1][2]. `-np` is how many chats run at once. `-c` is one shared context budget. Further requests queue [3]. Image: `ghcr.io/ggml-org/llama.cpp:server` [4]. GGUF is what fits an 8GB to 24GB card.

**Ollama** (MIT) serves that API at `http://localhost:11434/v1` and ignores the client key [5][7]. `OLLAMA_NUM_PARALLEL` defaults to 1, so the second student waits. The queue holds 512, then rejects. Four slots of a 2K context allocate 8K [6]. Use it when the teacher needs `ollama pull`, and set those variables first.

**vLLM** (Apache-2.0) adds PagedAttention and continuous batching, which fills a free slot as soon as another request ends [8][10]. It needs Linux and NVIDIA compute capability 7.5 or higher (T4, RTX 20-series and newer). No native Windows build [9]. **SGLang** (Apache-2.0) is the same class of CUDA server, plus RadixAttention, which reuses KV cache for a shared prefix such as a class system prompt [11][12][13].

**One box versus one model per PC.** The docs describe a KV-cache budget, not a user count [3][6]. 4-bit weights, before KV cache: Qwen3-4B about 3.1GB, Qwen3-8B about 6.3GB, gpt-oss-20b about 17GB, Qwen3-32B about 25GB, Gemma 4 31B about 24GB [14]. An 8GB PC runs a 4B, or a tight 8B, for one student. A 24GB box runs an 8B and several short slots. A 32B Q4 leaves almost no room for a class. Per-PC removes the queue and multiplies installs and unapproved models. Prefer one GPU server. Run per-PC only with no shared GPU, and keep the chat app central.

## Models to pin

One allowlisted checkpoint.

- **Qwen3-8B** (8.2B, 32,768 context, Apache-2.0) on 16GB to 24GB at Q4 or Q5. Qwen3-4B on 8GB [14][15].
- **Gemma 4** (E2B, E4B, 12B, 26B MoE, 31B) is Apache-2.0, the first Gemma on that licence [16][17]. E2B, E4B, and 12B leave KV room. A 31B Q4 fills 24GB [14].
- **gpt-oss-20b** (20.9B total, 3.6B active, 12.8 GiB, Apache-2.0) fits about 16GB and must use the harmony format. The 120B needs 80GB [18][19][20].
- **Skip Llama 4.** Scout is 109B / 17B active; the single-GPU path is INT4 on an H100 [22]. The Community License is not OSI (700 million MAU gate, "Built with Llama" notice) [21][23]. The use policy withholds multimodal rights from individuals and companies domiciled in the EU, not from end users of a product built on it [24].

## Chat UIs

**Open WebUI v0.6.6+** is not OSI: BSD-3 plus a branding clause. The project says it would fail OSI review. v0.6.5 stays BSD-3 [25]. Local accounts, LDAP, OIDC (Google, Microsoft), SCIM [26]. Admins can open chats (`ENABLE_ADMIN_CHAT_ACCESS` defaults on) [27]. Models can be limited to a group [28]. Filter Functions can block or rate-limit; a Langfuse filter exists, and its tutorial points at a Langfuse signup [29][30]. One Docker image. The licence is non-OSI at every scale. The 50-user threshold (up to 50 end users per rolling 30 days) only decides whether branding may be removed. Above it, branding must stay unless you have written permission or an enterprise licence [25].

**LibreChat** (MIT): local, LDAP, and OIDC including Entra [31][32][33]. Per-user and per-IP limits [34]. `modelSpecs.enforce: true` is an allowlist [35]. Langfuse and OpenTelemetry are documented [36][37]. Admin Insights shows counts and the first message, plus a Langfuse link, not a live transcript wall [36][38]. Install is Compose, MongoDB, and YAML.

**AnythingLLM** (MIT) has Docker roles (admin, manager, default) and local accounts [39][40]. No documented Google or Entra OIDC, rate limit, moderation hook, or Langfuse/OTel. It is a document workspace.

**LobeChat** is not OSI. Apache-2.0 plus a requirement for a commercial licence to develop and distribute a derivative work. Unmodified self-hosting is allowed [41].

**Hugging Face chat-ui** (Apache-2.0): optional OpenID, any OpenAI-compatible backend, MongoDB or an embedded dev database [42][43]. No documented admin transcript view, per-user limits, allowlist, or moderation.

**Chatbot UI** (MIT): Next.js, Supabase, optional Ollama URL, email allowlist. No LDAP, OIDC, or teacher view. The published container is years old [44][45].

## Custom chat server

Yes. You need a named student, a stored transcript, a teacher page that updates during the lesson, and a gate that blocks and alerts. Open WebUI is the only ready-made UI that stores chats and lets an admin open them, and current releases fail an OSI rule [25][27]. LibreChat is the OSI fallback, with full text in Langfuse [36][38].

A smaller proxy fits better. One process and SQLite calls `llama-server` `/v1/chat/completions`, stores each turn, and runs the guardrail before the model. Students never see the model port. Start with local accounts. Add Authentik or Keycloak later for Google Workspace or Entra. Do not build LDAP into the chat app.

## Recommendation

One compose file: `llama-server` with Qwen3-8B Q4 or Q5 on 16GB to 24GB, or Qwen3-4B on 8GB, a handful of `-np` slots, and a small SQLite chat app with local accounts and a teacher view [3][4][14][15].

Use Ollama only if `ollama pull` is what the teacher will run, and set `OLLAMA_NUM_PARALLEL` first [6]. Move to vLLM or SGLang only on Linux, NVIDIA compute capability 7.5+, once a real class is too slow [8][9]. Skip per-PC models unless there is no shared GPU.

If custom code is refused: LibreChat in front of the same server, with `modelSpecs.enforce` and per-user limits, and read full transcripts in Langfuse [34][35]. Do not ship current Open WebUI or LobeChat under a strict OSI rule [25][41].

## Sources

1. https://github.com/ggml-org/llama.cpp/blob/master/LICENSE : llama.cpp MIT licence.
2. https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md : OpenAI API, parallel decoding, continuous batching.
3. https://llama.app/docs/serve : slots, shared context, `-np`.
4. https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md : official server image.
5. https://github.com/ollama/ollama/blob/main/LICENSE : Ollama MIT licence.
6. https://docs.ollama.com/faq : `OLLAMA_NUM_PARALLEL` default 1, queue of 512, memory scales with slots.
7. https://docs.ollama.com/api/openai-compatibility : local OpenAI-compatible API.
8. https://pypi.org/project/vllm/ : Apache-2.0, PagedAttention, continuous batching, OpenAI server.
9. https://docs.vllm.ai/en/latest/getting_started/installation/gpu/ : Linux, compute capability 7.5+, no native Windows.
10. https://blog.vllm.ai/2025/09/05/anatomy-of-vllm.html : continuous batching admits new requests mid-run.
11. https://github.com/sgl-project/sglang/blob/main/LICENSE : SGLang Apache-2.0.
12. https://arxiv.org/abs/2312.07104 : RadixAttention.
13. https://www.morphllm.com/comparisons/sglang-vs-vllm : both Apache-2.0 and OpenAI-compatible (Sept 2026).
14. https://gpus.io/en/models : 4-bit weight sizes used above.
15. https://huggingface.co/Qwen/Qwen3-8B-GGUF : Apache-2.0, 8.2B, 32,768 context.
16. https://ai.google.dev/gemma/docs/core/model_card_4 : Gemma 4 sizes and Apache-2.0.
17. https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html : first Gemma on Apache-2.0.
18. https://github.com/openai/gpt-oss : Apache-2.0, sizes, harmony format.
19. https://deploymentsafety.openai.com/gpt-oss/full-evaluations : 20.9B / 3.6B active, 12.8 GiB checkpoint.
20. https://github.com/huggingface/blog/blob/main/welcome-openai-gpt-oss.md : 20B described as fitting 16GB.
21. https://www.llama.com/llama4/license/ : Llama 4 Community License and the 700M MAU clause.
22. https://www.llama.com/docs/model-cards-and-prompt-formats/llama4/ : Scout 109B/17B, INT4 on one H100.
23. https://www.llama.com/faq/ : "Built with Llama" notice.
24. https://www.llama.com/llama4/use-policy/ : Acceptable Use Policy and the EU multimodal limit.
25. https://docs.openwebui.com/license : v0.6.6+ branding clause, not OSI at any scale; v0.6.5 stays BSD-3.
26. https://docs.openwebui.com/features/authentication-access/ : local, OIDC, LDAP, SCIM.
27. https://docs.openwebui.com/getting-started/advanced-topics/hardening : admin chat access and model ACL defaults.
28. https://docs.openwebui.com/features/workspace/models : per-group model access.
29. https://docs.openwebui.com/features/extensibility/pipelines/filters : block and rate-limit filters.
30. https://docs.openwebui.com/tutorials/integrations/monitoring/langfuse : Langfuse filter tutorial.
31. https://github.com/LibreChat-AI/LibreChat/blob/main/LICENSE : LibreChat MIT licence.
32. https://www.librechat.ai/docs/configuration/authentication/ldap : LDAP.
33. https://www.librechat.ai/docs/features/access_control : federated users and Entra groups.
34. https://www.librechat.ai/docs/configuration/mod_system : per-user and per-IP message limits.
35. https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/model_specs : `modelSpecs.enforce`.
36. https://www.librechat.ai/docs/configuration/langfuse : Langfuse tracing and the admin session link.
37. https://www.librechat.ai/docs/configuration/logging : OpenTelemetry export.
38. https://www.librechat.ai/docs/features/insights : admin view shows the first message, not the full chat.
39. https://github.com/Mintplex-Labs/anything-llm/blob/master/README.md : MIT, Docker multi-user.
40. https://docs.anythingllm.com/features/security-and-access : multi-user roles.
41. https://github.com/lobehub/lobehub/blob/canary/LICENSE : LobeHub Community License.
42. https://github.com/huggingface/chat-ui : Apache-2.0.
43. https://github.com/huggingface/chat-ui/blob/main/docs/source/index.md : OpenID and OpenAI-compatible backends.
44. https://github.com/mckaywrigley/chatbot-ui : MIT; container last published years ago.
45. https://github.com/mckaywrigley/chatbot-ui/blob/main/.env.local.example : Supabase, Ollama URL, email allowlist.
