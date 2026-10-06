# Prior art: classroom AI with teacher oversight

I took licences from the repo pages wherever I could reach them. Anything I couldn't confirm is marked unverified.

## Open-source projects

- **Gen-Ed / CodeHelp** (Liffiton) [1]. AGPL-3.0-only (OSI). Has student and instructor roles, class enrolment, admin pages and data export [1]. CodeHelp checks every answer in three steps: is the question clear enough, write the answer, then rewrite it if it gives away code [2]. Borrow: the class and role model, and that final rewrite step. I couldn't confirm it works with local models.
- **telli / AIS.chat** (built by FWU for all 16 German states) [3][4]. AGPL-3.0 (OSI). Next.js, Fastify, Postgres, Valkey and Keycloak, run with Docker Compose [3]. Teachers build "dialogue partners" and learning scenarios for students to use [5]. Borrow: how it stores teacher-made bots and gives students access. It is the closest open project to a real school deployment. Whether teachers can see student chats is unverified. It is built for cloud models hosted in the EU [4], so pointing it at a local model server is untested.
- **Oak Aila** [6]. MIT (OSI). A lesson planner for teachers that runs on GPT-4o [7]. Students don't use it. Borrow: Oak's published transparency record, as a template for this repo's own safety statement [7].
- **Open WebUI** [8]. **Not OSI open source** at any scale since v0.6.6. The new licence took effect on 19 April 2025, and v0.6.6 was released on 5 May 2025. It bans removing its branding above 50 end users per rolling 30 days, and GitHub lists it as "Other" [8][9]. Versions up to v0.6.5 are BSD-3. Admins can read user chats unless `ENABLE_ADMIN_CHAT_ACCESS=false` [10]. Borrow: how it connects to Ollama and OpenAI-style servers, and its role-based permissions. A 30-seat lab is under the 50-user limit, which only affects branding. The licence is still non-OSI.
- **LibreChat** [11]. MIT (OSI). It runs strike scoring and temporary bans locally. Its only content filter calls the OpenAI Moderation API, which breaks the no-cloud rule [11]. The docs don't describe a way for admins to view other users' chats. Borrow: the strike and ban scoring.
- **ClassroomLM PoC** [12]. Licence unverified. Agents that students use pass what they learn about each student to agents that teachers use. Borrow the idea only.
- **AI Learning Box** [13]. Fully offline: Raspberry Pi 5 with 8GB, Gemma2 2B on Ollama, Open WebUI and Kolibri. No teacher monitoring, and I found no repo or licence. Borrow: the offline bundle pattern.
- **Computing at School KS2 workshop kit** [14]. A Pi 4 and locked-down tablets on a Wi-Fi hotspot with no internet. A presenter laptop starts, stops and monitors every station. All generated output was checked in advance (2,000 images). Borrow: the isolated Wi-Fi and the teacher control laptop.

**Not open source:**
- **CS50 duck:** runs on Azure OpenAI plus CS50's own course database [15][16]. Borrow only its teaching approach: guide the student, never hand over the answer.
- **schulKI:** a paid EU-hosted service. Students join by link, with no accounts [17]. Borrow: joining without personal data.

## What teachers expect to be able to do (from commercial products)

| Feature | Seen in |
|---|---|
| See every student chat live in one dashboard | SchoolAI Mission Control [18], Flint [19], Mizou [20] |
| Automatic alerts at the top of the dashboard (self-harm, bullying, violence, sexual content) | SchoolAI [18], MagicSchool [21] |
| Two alert levels: urgent (goes to counsellor or admin) and flagged (teacher reviews later) | MagicSchool [21], Khanmigo [22] |
| Email to named admins when an alert is serious enough | Khanmigo [22] |
| Pause or lock the room, or remove a student | SchoolAI [18], MagicSchool [21] |
| AI summaries per student and per class, plus teacher comments | Flint [19] |
| Each student's full chat history visible to the teacher and admins | MagicSchool [21], Flint [19] |

## Hardware and model sizes

- **Inria Bordeaux (French research institute):** ran vLLM for staff and students on V100 16GB and A100 40GB GPUs. Mixtral 8x7B on two A100s gave "just under 20 tokens per second for 20 simultaneous users". Twice as many simultaneous requests did not mean twice the wait [23].
- **16GB gaming GPUs:** a 2026 benchmark of RTX 5060 Ti and 5070 Ti cards ran Qwen3-8B and Gemma3-12B comfortably. 27B models had to be compressed to 4-bit. The cheaper cards gave the best speed per dollar [24].
- **Below 8GB:** below 8GB of VRAM, running models on the GPU isn't worth it [25].
- **Reported problems:** small models are weaker on hard tasks, slow machines are slow, and "someone in the building has to set it up and maintain it" [26]. Under-13s can't use online AI services, which is why the CAS kit went fully offline [14].

I found no write-up of a school GPU lab running local models with teacher monitoring.

## Gaps this repo could fill

1. No OSI-licensed offline project combines student chat, a live teacher dashboard and local safety alerts. telli is closest but built for cloud models. Open WebUI has most of the parts but a non-OSI licence and no alerts.
2. Every open-source content filter I found calls a cloud API. No open project runs a local safety classifier (e.g. Llama Guard on the same model server) with urgent and flagged alert levels like SchoolAI's.
3. Room controls (pause, lock, remove a student) and joining with a code instead of an account only exist in paid products.
4. Nobody has published sizing for "one model per lab PC on 8-16GB GPUs" versus "one central server". The repo could ship measured numbers.
5. A transparency and consent template for logging children's chats, modelled on Oak's record [7].

## Sources

1. https://github.com/liffiton/Gen-Ed (Gen-Ed/CodeHelp repo, AGPL-3.0-only, roles and class management)
2. https://arxiv.org/abs/2308.06921v1 (CodeHelp paper, three-step answer checks)
3. https://github.com/FWU-DE/ais-chat (telli/AIS.chat repo, AGPL-3.0, tech stack)
4. https://www.business-punk.com/ki-kommt-ins-klassenzimmer-sachsen-anhalt-startet-ais-chat-an-schulen/ (renamed AIS.chat, 16 states, EU-hosted models)
5. https://checkpoint-elearning.de/schule/kichatbot-telli-neue-funktionen-fuer-lehrkraefte (telli teacher features)
6. https://github.com/oaknational/oak-ai-lesson-assistant (Aila repo, MIT)
7. https://www.gov.uk/algorithmic-transparency-records/oak-national-academy-aila-oaks-ai-lesson-assistant (Aila transparency record, GPT-4o)
8. https://docs.openwebui.com/license/ (Open WebUI licence, branding clause, 50-user exception)
9. https://scancode-licensedb.aboutcode.org/open-webui-2025.html (Open WebUI licence listed as custom)
10. https://docs.openwebui.com/features/authentication-access/rbac/roles (admin chat access setting)
11. https://www.librechat.ai/docs/configuration/mod_system (LibreChat local bans, OpenAI moderation)
12. https://awesome.ecosyste.ms/projects/github.com%2F13point5%2Fclassroom-lm-poc (ClassroomLM PoC)
13. https://online.coolestprojects.org/projects/24549 (AI Learning Box, offline Pi 5 setup)
14. https://www.computingatschool.org.uk/forum-news-blogs/2025/july/interactive-and-safe-ai-for-ks2-upwards/ (CAS offline KS2 kit)
15. https://cs50.harvard.edu/x/2025/notes/ai/ (CS50 duck runs on Azure and OpenAI)
16. https://cs50.readthedocs.io/cs50.ai/ (CS50.ai docs)
17. https://www.ki-syndikat.de/tools/schul-ki/ (schulKI, paid service, students join by link)
18. https://help.schoolai.com/en/articles/14756370-student-safety-critical-alerts-real-time-monitoring (SchoolAI alerts, Mission Control)
19. https://www.flintk12.com/flint-vs-chatgpt (Flint teacher visibility and summaries)
20. https://www.ecml.at/en/Resources/ICT-tools/InventoryID/357 (Mizou teacher monitoring)
21. https://help.magicschool.ai/en/articles/15634681-student-moderations (MagicSchool urgent and flagged alerts, room lock)
22. https://support.khanacademy.org/hc/vi/articles/21943797567629 (Khanmigo alerts and admin emails)
23. https://arxiv.org/html/2409.14887v4 (Inria vLLM deployment, numbers for simultaneous users)
24. https://arxiv.org/abs/2601.09527 (16GB gaming GPU benchmarks)
25. https://nullprogram.com/blog/2024/11/10/ (local model sizing notes, 8GB VRAM floor)
26. https://hackernoon.com/how-to-run-a-sandboxed-llm-in-a-school-lab-with-no-cloud-bill (downsides of running models in a school lab)
