# Local guardrails and teacher alerts

Classify every prompt and every reply on the school GPU, then hand self-harm to a named adult. Classifiers miss assessed work. Put that rule in a custom criterion, with the live teacher view behind it.

Checked 6 October 2026. Only Prompt Guard 2 publishes milliseconds. Other guards emit a short label. Leave Granite thinking off on the hot path. Use gpt-oss-safeguard at high effort only on turns already flagged.

## Recommended stack

One Compose file on the lab server.

1. **llama.cpp** or **Ollama** serves the chat model and **Qwen3Guard-Gen-0.6B** (Apache-2.0, community Q4 about 0.5 GB). Official servers are vLLM and SGLang. There is no official Ollama tag. Score the prompt and the reply. For minors, Controversial counts as Unsafe.
2. **Granite Guardian 4.1 8B** (Apache-2.0, official Q4_K_M 5.12 GB, thinking off) runs on flagged turns, including a custom assessed-work criterion. Version 3.3 is English-only. Check the 4.1 card before te reo Māori.
3. **Presidio** (MIT) redacts names, emails, and phones on CPU before logs or a shared screen. It misses some PII.
4. A small proxy applies the verdict and the teacher view. Open WebUI filters can call the same check. From v0.6.6 that licence is non-OSI at every scale. The 50-user threshold (up to 50 end users per rolling 30 days) only decides whether branding may be removed.
5. **ntfy** (Apache-2.0 or GPL-2.0), signup off, default deny, no upstream. Dashboard flag plus the ntfy app. Optional mail stays on school Postfix. The alert is seat and category. Full text stays in a locked log.

Skip **LLM Guard** (MIT, archived 9 July 2026). **NeMo Guardrails** and **Guardrails AI** are Apache-2.0 and heavier than this proxy. **LiteLLM** is MIT for custom hooks and Presidio. Its Llama Guard, LLM Guard, secret-hiding, OpenAI, Google, Lakera, and Aporia callbacks need Enterprise.

Optional **Prompt Guard 2 22M** (Llama 4 Community, non-OSI): 19.3 ms per 512 tokens on an A100. The 86M model measured 92.4 ms.

## Guard models

| Model | Fit | Licence | Self-harm | Sexual | Violence | Academic integrity | PII | Jailbreak |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Llama Guard 3 1B / 8B | Ollama 1.6 / 4.9 GB | Llama 3.2 (1B) / Llama 3.1 (8B), non-OSI | S11 | S12 | S1 | S8 IP only | S7 | No |
| Llama Guard 4 12B | 24 GB bf16. No official GGUF | Llama 4, non-OSI, 700M-user clause | S11 | S12 | S1 | S8 | S7 | Prompt Guard 2 |
| Prompt Guard 2 22M / 86M | CPU. Transformers | Llama 4, non-OSI | No | No | No | No | No | 19.3 / 92.4 ms, A100 |
| ShieldGemma 2B / 9B / 27B | 2B community Q4 about 1.8 GB | Gemma Terms, non-OSI | No named class | Sexually explicit | Dangerous content | No | No | No |
| ShieldGemma 2 4B | Image model | Gemma, non-OSI | Image policies | Images | Images | No | No | No |
| Granite Guardian 4.1 8B | Official Q4_K_M 5.12 GB, llama.cpp | Apache-2.0 | Harm, violence, or custom | Yes | Yes | Custom criterion | Custom | Yes |
| Qwen3Guard-Gen 0.6B / 4B / 8B | 0.6B Q4 about 0.5 GB. vLLM/SGLang. Stream variant scores tokens live. 119 languages | Apache-2.0 | Yes | Yes | Yes | Copyright only | Yes | Input only |
| WildGuard 7B (Jun 2024) | Transformers. English | Apache-2.0, gated: HF account and research-purposes agreement | Physical harm | Yes | Yes | No | Private info | Adversarial, plus refusal |
| gpt-oss-safeguard 20B / 120B | 13.8 GB file. 120B needs one 80 GB GPU. Harmony format | Apache-2.0 | Policy you write | Policy | Policy | Policy | Policy | Policy |
| Shieldstral 1.0 ~3.8B (4 Aug 2026) | One 16 GB GPU, bf16. Text and image | Apache-2.0 | Yes/no question | Question | Question | Question | Question | Question |

Llama and Gemma licences are not OSI-approved. The official Hugging Face repos for Llama, Llama Guard, Prompt Guard 2, Gemma 1-3 and ShieldGemma are gated and need an HF account. Gemma 4, Qwen3, Qwen3Guard, Granite Guardian, gpt-oss, gpt-oss-safeguard and Shieldstral are not gated. WildGuard is gated too. An OSI-only default is Qwen3Guard plus Granite Guardian. Shieldstral takes a plain-language policy and sees images. gpt-oss-safeguard 20B writes a reason the teacher can read.

## Duty of care

A block page is the wrong end state for self-harm. UK *Keeping children safe in education 2026* (in force 1 September 2026) is the statutory duty in England. A 2026 briefing of Part One names suicidal ideation and self-harm: designated safeguarding lead, 999 in immediate danger, NHS 111 for urgent mental-health help. In New Zealand the Ministry points schools to its self-harm guidelines (a named team, involve whānau) and the Traumatic Incident line 0800 848 326.

On that label, show a static message the school wrote (counsellor, 1737, 111). Raise urgent ntfy and a red dashboard row. Record who saw it. Do not ask the chat model to counsel. A generated reply can contain methods.

## Education guidance

England. DfE product safety expectations (22 January 2025) tell suppliers to stop harmful output, including adversarial prompts, and point schools at KCSIE plus the filtering and monitoring standards. Guidance updated 12 August 2025 allows pupil use only with supervision and with filtering and monitoring.

New Zealand. Ministry guidance (22 May 2026) keeps the teacher responsible, requires an acceptable-use policy, and forbids sending personal information of anyone under 13 to an AI tool. Netsafe (14 January 2026) says students still do the thinking, and objectionable AI images can be illegal. NZQA requires an authenticity policy covering AI, and bans generative AI in NCEA external assessment. On-prem logs meet that only while DNS and alerts have no upstream.

United States. The 22 July 2025 Department letter allows AI in federal programmes when use is educator-led and meets FERPA. It is grant guidance, not a filter spec. E-rate schools filter under the Children's Internet Protection Act. The October 2024 toolkit covers civil rights. Crisis path: 988.

## Lab lockdown

Student accounts have no admin rights. AdGuard Home (GPL-3.0) blocks public AI hostnames and points the chat name at the lab server. The firewall allows that server, school DNS, and image updates, and denies the rest. Windows Assigned Access or Firefox enterprise policies open only the school chat URL. AppLocker blocks installers, including local model servers.

Classifiers miss paraphrases, some languages, and coded speech. The live teacher view is the backstop. Agree categories with the safeguarding lead before the first class.

## Sources

- https://huggingface.co/Qwen/Qwen3Guard-Gen-0.6B : Apache-2.0. Suicide and self-harm. Jailbreak on input only. 119 languages.
- https://arxiv.org/abs/2510.14276 : Qwen3Guard report. 0.6B, 4B, 8B. Apache-2.0.
- https://huggingface.co/QuantFactory/Qwen3Guard-Gen-0.6B-GGUF : Community GGUF. Q4 around 0.5 GB.
- https://research.ibm.com/blog/granite-4-1-ai-foundation-models : Guardian 4.1, April 2026, Apache-2.0, replaces 3.3 8B.
- https://huggingface.co/ibm-granite/granite-guardian-4.1-8b-GGUF : Official GGUF. Q4_K_M is 5.12 GB.
- https://huggingface.co/ibm-granite/granite-guardian-3.3-8b : Criteria, custom criteria, English-only, Apache-2.0.
- https://ollama.com/library/llama-guard3 : Ollama packs, 1.6 GB and 4.9 GB, hazards S1 to S13.
- https://huggingface.co/blog/llama-guard-4 : Guard 4, 12B, 24 GB, hazards S1 to S14.
- https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/Llama-Prompt-Guard-2/86M/MODEL_CARD.md : Prompt Guard 2 latency on an A100.
- https://huggingface.co/google/shieldgemma-2b : Four text categories. Gemma licence, non-OSI.
- https://huggingface.co/mradermacher/shieldgemma-2b-GGUF : Community GGUF of the 2B. Q4 around 1.8 GB.
- https://deepmind.google/models/gemma/shieldgemma-2/ : ShieldGemma 2, 4B image classifier.
- https://huggingface.co/allenai/wildguard : WildGuard 7B, Apache-2.0, English. Gated: needs an HF account and a research-purposes agreement.
- https://openai.com/index/introducing-gpt-oss-safeguard : 20B and 120B, Apache-2.0, policy at inference.
- https://github.com/openai/gpt-oss-safeguard : 120B on one 80 GB GPU. Harmony format.
- https://huggingface.co/openai/gpt-oss-safeguard-20b : Weights about 13.8 GB. Apache-2.0.
- https://mistral.ai/news/shieldstral/ : 4 August 2026. Apache-2.0. One 16 GB GPU. Text and image.
- https://docs.mistral.ai/models/shieldstral-1-0 : About 3.8B. Apache-2.0.
- https://github.com/NVIDIA-NeMo/Guardrails : NeMo Guardrails, Apache-2.0.
- https://github.com/guardrails-ai/guardrails : Guardrails AI, Apache-2.0.
- https://github.com/protectai/llm-guard : MIT. Archived 9 July 2026.
- https://docs.litellm.ai/docs/enterprise : Enterprise-only callbacks. Presidio and custom hooks stay open.
- https://github.com/BerriAI/litellm : MIT outside the enterprise directory.
- https://docs.openwebui.com/features/extensibility/plugin/functions/ : Filter inlet, stream, outlet.
- https://docs.openwebui.com/license : Branding clause from v0.6.6. Non-OSI at every scale. Up to 50 end users per rolling 30 days only decides whether branding may be removed.
- https://github.com/data-privacy-stack/presidio : Presidio, MIT. Can miss PII.
- https://github.com/binwiederhier/ntfy : Apache-2.0 or GPL-2.0.
- https://www.gov.uk/government/publications/generative-ai-product-safety-expectations/generative-ai-product-safety-expectations : DfE expectations, 22 January 2025.
- https://www.gov.uk/government/publications/generative-artificial-intelligence-in-education/generative-artificial-intelligence-ai-in-education : Updated 12 August 2025.
- https://www.gov.uk/government/publications/keeping-children-safe-in-education--2 : KCSIE 2026, in force 1 September 2026.
- https://www.brownejacobson.com/insights/kcsie-2026/kcsie-2026-mental-health : Briefing of the 2026 self-harm path.
- https://www.education.govt.nz/school/digital-technology/generative-ai : Ministry guidance, 22 May 2026.
- https://netsafe.org.nz/artificial-intelligence/generative-ai-tools : Netsafe, 14 January 2026.
- https://www2.nzqa.govt.nz/ncea/ncea-for-teachers-and-schools/managing-national-assessment-in-schools/ai-guidance/ : NZQA. No GenAI in external assessment.
- https://www.education.govt.nz/news/support-schools-responding-self-harm-and-suspected-suicide : TI team 0800 848 326. Updated 28 January 2026.
- https://bpb-ap-se2.wpmucdn.com/blogs.auckland.ac.nz/dist/4/1059/files/2025/06/Self-Harm-Guidelines-FINAL-9-5-25.pdf : Auckland guidelines. Named team. Involve whānau.
- https://www.ed.gov/media/document/opepd-ai-dear-colleague-letter-7222025-110427.pdf : US letter, 22 July 2025. FERPA.
- https://cdt.org/insights/u-s-department-of-educations-ai-toolkit-and-nondiscrimination-resources-provides-lasting-guidance-for-educators-on-ai-and-civil-rights : October 2024 US toolkit.
- https://www.fcc.gov/consumers/guides/childrens-internet-protection-act : CIPA for E-rate schools.
- https://988lifeline.org/ : US 988.
- https://1737.org.nz/ : NZ 1737.
- https://github.com/AdguardTeam/AdGuardHome : GPL-3.0 DNS filter.
- https://learn.microsoft.com/en-us/windows/configuration/assigned-access/overview : Assigned Access kiosk.
- https://mozilla.github.io/policy-templates/ : Firefox enterprise policies.
- https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/applocker/applocker-overview : AppLocker.
