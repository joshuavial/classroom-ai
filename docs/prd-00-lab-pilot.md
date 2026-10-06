# PRD 00: Lab pilot

Status: draft, 2026-10-06.

## Problem and context

A school has GPU machines in a lab and wants students to use local AI in lessons. The tech teacher needs to set it up without a devops background. The classroom teacher needs to see what students are doing with it while the lesson runs, and the school needs unsafe use caught and serious disclosures sent to a person. See `vision.md` for the wider case and `research/summary.md` for the evidence.

This increment covers one school piloting with one class for one lesson.

## Users and needs

| User | Needs in this increment |
| --- | --- |
| Tech teacher (installer, admin) | Install the server with one command. Add and remove GPU machines easily. See which machines are up. Back up and restore. |
| Classroom teacher | Create a class and get students in quickly. Choose models and instructions. Watch every conversation live. Read any transcript. Pause the class. See flags and act on them. |
| Student | Open a web page on whatever device they have, get in, and chat. Know that the teacher can see the chat. Get a clear message when something is blocked. |
| Safeguarding lead | Be notified of self-harm and similar flags through the school's own process, with the transcript available. |

## Outcome and success signals

- A tech teacher who has not seen the project before installs the server and joins one GPU machine using only the README, in under an hour.
- One class of up to 30 students uses it for a full lesson on their own devices.
- Every student message and model reply in that lesson appears in the teacher console. No conversation reaches a model without being recorded and checked.
- The teacher sees a new message within a few seconds of it being sent.
- Students get the start of a reply within about five seconds with a full class on the pilot hardware. The pilot measures the real number.
- Test prompts for each safety category are flagged or blocked as configured.

## Journeys and requirements

### 1. Install the server

- R1.1 The server installs on one machine on the school network with a single documented command, using Docker.
- R1.2 First run creates an admin account for the tech teacher.
- R1.3 The server works with no internet connection once installed. Installing needs internet only to download software and models.
- R1.4 Students reach it by a web address on the school network. The connection is encrypted.
- R1.5 The tech teacher can back up everything (settings and conversations) to a file and restore it.
- R1.6 Upgrading to a new release is one documented command and keeps existing data.

### 2. Join GPU machines

- R2.1 The admin console shows a join token and the one command to run on a GPU machine.
- R2.2 Running that command on a GPU machine starts serving models and registers the machine with the server.
- R2.3 The console lists each GPU machine with its status (up or down), the models it offers and how busy it is.
- R2.4 A machine that goes off is shown as down within a minute. Students keep working if another machine offers the same model.
- R2.5 Admin can remove a machine. A removed machine or a machine without the token can't serve students.
- R2.6 Students' devices can't reach GPU machines directly. The docs show how to enforce this on the network.

### 3. Choose models and set up a class

- R3.1 The teacher sees all models the GPU machines offer and turns each on or off. Students see only models that are on.
- R3.2 The teacher creates a class with a name and a set of instructions given to the AI for every conversation in that class (for example, "act as a tutor; guide, don't write the answer").
- R3.3 The teacher gets students into a class in one of two ways: a class join code plus the student's name, or student accounts the admin creates in bulk from a list. (Open question Q1 decides which comes first.)
- R3.4 The teacher can set a message limit per student per lesson.

### 4. Student chats

- R4.1 The student chat page works on current phone, tablet and desktop browsers, needs nothing installed, and meets basic accessibility (keyboard use, screen readers, readable contrast, text resize).
- R4.2 Before the first message, and visibly on every chat, the student is told that their teacher can read the conversation.
- R4.3 The student picks from the models that are on, sends messages and sees the reply appear as it is written.
- R4.4 The student can start a new conversation and see their own past conversations in this class.
- R4.5 When a message is blocked, the student sees a plain explanation, not an error.
- R4.6 When the class is paused, students see that it is paused and can't send messages.

### 5. Teacher watches the lesson

- R5.1 The teacher console shows every student in the class with their latest message, updating live.
- R5.2 Flagged conversations stand out in the console and stay highlighted until the teacher marks them reviewed.
- R5.3 The teacher can open any student's full transcript, including blocked messages and what triggered the flag.
- R5.4 The teacher can pause and resume the class.
- R5.5 Alerts appear in the console only.

### 6. Safety checks

- R6.1 Every student message is checked before it reaches a model. Every reply is checked before the student sees it.
- R6.2 The checks run on school hardware and keep working when no GPU machine is up.
- R6.3 Categories at minimum: self-harm, sexual content, violence, hate and bullying, dangerous activities, attempts to bypass the rules (jailbreaks), and personal information.
- R6.4 For each category the admin chooses the action: allow and flag, block and flag, or block only.
- R6.5 Self-harm always flags, and shows the student a message written by the school (with local helplines such as 1737 in New Zealand). No AI-generated reply is shown for it. The admin edits that message.
- R6.6 Each flag records the category, the message, the time and the student.
- R6.7 The console shows the school's named safeguarding contact. Following up a self-harm flag is the school's process; the product makes the flag and transcript available and records who reviewed it and when.

### 7. Records and privacy

- R7.1 All conversations, flags and settings are stored only on the server.
- R7.2 The admin sets how long conversations are kept. Older ones are deleted automatically.
- R7.3 The admin can export one student's conversations, and delete one student's data.
- R7.4 Admin and teacher actions (model changes, pauses, flag reviews, exports, deletions) are recorded with who and when.

## Acceptance criteria

1. On a clean machine with Docker, following only the README, the server is running and the admin is signed in.
2. On a second machine with an NVIDIA GPU, the documented join command makes it appear as "up" in the console with its models listed.
3. With the model turned on, a student on a phone and a student on a laptop each send a message and get a streamed reply; both appear in the teacher console within five seconds.
4. Turning a model off removes it from student choice immediately; requests for it are refused.
5. A request sent straight to a GPU machine from a student device fails when the documented network rules are applied.
6. Stopping the GPU machine shows it as down within a minute; with a second machine offering the same model, students keep chatting.
7. A message from each category in R6.3 triggers the configured action. A self-harm message shows the school's message, flags red in the console and stores the transcript.
8. Unplugging the server's internet does not change any of the above.
9. Pausing the class blocks new student messages until resumed.
10. Backup, wipe and restore returns all settings and conversations.
11. Every component's licence is OSI-approved, and every default model's licence is open. A licence list ships with the release.
12. Thirty simulated students chatting at once on the pilot hardware: no lost or unrecorded messages; time to first reply is measured and published.

## Non-goals

- Phone, push, email or other alerts outside the console.
- Sign-in with Google, Microsoft or other school identity systems.
- Several schools on one server, or district-wide management.
- Uploading files or images, generating images, voice, web search, tools or agents.
- Analytics dashboards, lesson summaries and Langfuse.
- Models running on student devices, or any cloud model.
- LM Studio, Bionic or any other closed-source part.
- Automatic discipline or reporting to anyone outside the school.
- Activities, assignments or a teacher-built bot library.

## Assumptions

- A1 The school has at least one machine with an NVIDIA GPU of 8 GB or more, and one machine (it can be the same one) that can run Docker for the server.
- A2 Student devices and the server are on the same school network. The school can add a local address for the server, or students use its IP address.
- A3 One teacher runs one class at a time for the pilot. Several classes can exist, but the pilot only needs one live.
- A4 English first. Guard accuracy in te reo Māori and other languages is untested.
- A5 The school has a safeguarding contact and an acceptable-use policy that covers AI.
- A6 Default models follow the research: a small open guard model for checks, and an 8B-class open chat model on a 16 GB card.

## Risks

- Guard misses. Safety models miss paraphrase, slang and other languages. The docs must say the teacher's live view is what catches them.
- False positives. Over-blocking frustrates students and teachers. Per-category actions let the school tune it.
- Capacity. 30 students on one GPU may queue. The pilot measures it and the docs publish the numbers.
- Privacy and consent. Logging minors' chats needs school policy and parent communication. The project should ship a plain-language notice template.
- Bypass. Students may use public AI sites instead. Network blocking is a school decision; the docs give guidance but the product does not enforce it.
- HTTPS on a school LAN. Phones may warn about a certificate the school created itself. Install docs must cover this.

## Open questions

- Q1 Student entry: class join code plus name (fast, no accounts, but anyone with the code can join under any name) or admin-created accounts (slower setup, real identity)? Pilot needs one; which first?
- Q2 Default retention period for conversations.
- Q3 Pilot school and hardware: which GPUs and how many, which network constraints?
- Q4 Should academic-integrity checks ("write my essay") be a flag category in this increment, or handled only through class instructions?
- Q5 Project name.
