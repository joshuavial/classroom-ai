# Vision

Working name: classroom-ai. The real name is still to be chosen.

## Why

This project exists to give schools free, local and fully private AI. Everything runs on hardware the school already owns, nothing leaves the building, and there is nothing to pay for. It comes as a technical package that the school's IT staff and tech teachers can read, audit and understand.

Schools that want AI today mostly get cloud products. Those send students' words to companies overseas and charge every year. Many schools can't use them at all: in New Zealand, personal information of under-13s can't go to an AI tool.

Many schools already have the hardware to do it themselves. Computer labs, media rooms and esports clubs have gaming GPUs that sit idle most of the day. Open models that run on a 16 GB card can tutor a teenager through algebra or explain a poem.

No open-source project packages this for schools. The closest, telli, is built for cloud models. The products that give teachers a view of student chats and run safety checks (SchoolAI, MagicSchool, Flint, Khanmigo) are paid cloud services.

## What it is

A free, open-source kit that turns a school's own GPUs into a private AI service for its students.

- Students use any browser: a phone, a tablet, a Chromebook or a lab PC. Their device needs no install and no GPU.
- One small server, started with Docker, sits in the middle. It signs students in, records every conversation, checks messages for safety and shows the teacher what is happening.
- Any number of GPU machines join that server and run the models. Adding a machine is one command on that machine.
- The teacher decides which models students can use, what the AI is told to do (for example, tutor without writing the homework), and what happens when a safety check trips.

Everything runs inside the school. Once the models are downloaded, it works with the internet unplugged.

## Sharing GPUs between schools

The server and the GPU machines are separate on purpose, so a GPU machine doesn't have to be in the same building. A school with spare GPU capacity can join its machines to another school's server and give that school AI it couldn't otherwise run. Time zones help. A lab in New Zealand sits idle overnight while a school in Europe or the Americas is teaching, and the reverse.

The architecture is designed for this. Each school's own server sits between its students and any GPU, so it can anonymise traffic before it leaves the school. Sign-in, names, records, safety checks and the teacher view stay on the school's server. The GPU machines at the other school see only the conversation text, with no student name, class or school attached. The server can also remove personal details a student types, such as names, phone numbers and addresses, before a request crosses over. The lending school sees nothing it could trace back to a child, and the borrowing school still has a full record of its own.

Sharing still needs a written agreement between the schools and an encrypted link between the sites. It is a later increment; the pilot keeps everything on one school's network.

## Principles

1. Open source throughout. OSI-licensed software and openly licensed models only, with no vendor accounts, subscriptions or telemetry sent home. A school can fork it and keep running it if this project stops.
2. The school owns the data. Conversations stay on school hardware. The school decides how long to keep them and can delete them.
3. Students know they are monitored. Every chat tells the student that their teacher can read it. The teacher's view is supervision of a learning tool, the same as walking around the lab.
4. A person handles serious disclosures. Safety checks flag and block, and never counsel. A disclosure of self-harm reaches a named adult at the school, and the student sees a message the school wrote.
5. One teacher can run it. Install, upgrade and adding a GPU each take one documented command. A tech teacher should be able to do each from the README alone.
6. Small enough to audit. The code and storage are plain enough that a school IT person can read how it works in an afternoon.

## Who it is for

| Person | What they need |
| --- | --- |
| Tech teacher or school IT person | Installs it and looks after it. |
| Classroom teacher | Sets up a class, chooses what the AI may do, watches the lesson, follows up on flags. |
| Student | A private, safe AI tutor at school. |
| Safeguarding lead and principal | Confidence that serious issues reach a person, and a way to explain the service to parents. |

## Possible later increments

- Room controls during a lesson: lock, remove a student, push a prompt to everyone.
- Per-student and per-class summaries at the end of a lesson.
- Sign-in with the school's Google or Microsoft accounts.
- Teacher-built activities, such as a tutor set up for one assignment.
- Measured sizing guides: how many students a given GPU can serve.
- A transparency and consent pack schools can hand to parents and boards.
- GPU sharing between schools: anonymised requests, an encrypted link, and an agreement template.
- Guard coverage for te reo Māori and other languages students use.

## Background

Research behind these choices is in `docs/research/`. Start with `summary.md` and `design.md`. LM Studio Bionic was considered as a client and ruled out: it can't point at a custom server, and LM Link needs an LM Studio account and internet.
