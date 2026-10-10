# Manual tests

Checks a person runs by hand, on real devices, before a pilot lesson. Automated tests cover the logic; these cover what only a screen, a finger and a network can show. Record each run in `docs/pilot-checks.md` with the date, devices and result.

## Setup

1. Start the stack on the Mac: `./scripts/init-env.sh` once, then `docker compose up -d --build`.
2. Note the server's LAN address. Devices must reach it.
3. On a laptop, open `/admin`, enter the setup code from `docker compose logs app`, create the admin, then open `/teach`.

## Teacher console on a laptop and an iPad

Run each on a laptop (Chrome or Firefox) and on an iPad (Safari).

| # | Do | Expect |
| --- | --- | --- |
| 1 | Create a class with instructions and a message limit, start a lesson with 30 codes. | The session bar shows the class name, "open", the time since start, Pause, Close lesson, Print slips and More codes. "No students have joined yet." |
| 2 | Print slips, and use print preview. | Three slips across on A4, one per unused code, each with the class name, the code and the chat address. Controls are not printed. |
| 3 | On two student devices, join with two codes and two names. | Within a few seconds, without a reload, a card appears for each student with name and code, and the roster shows the codes as used. |
| 4 | Pause the lesson. | Students see the paused notice within 15 seconds. Resume clears it. |
| 5 | Click a card. | The transcript opens beside the grid (below it on a narrow screen). Close hides it. |
| 6 | With a flagged message (step 6 onward), look at the grid. | The card has a thicker red border and a "Flagged" badge with a count; the badge text is there without colour. Mark reviewed in the transcript clears the badge and shows who reviewed it. |
| 7 | Use only the keyboard: Tab to a card, Enter to open, Tab to Mark reviewed, Enter. | Every control is reachable and shows focus. |
| 8 | Rotate the iPad and zoom the text to 200 percent. | Nothing is cut off; the grid reflows. |
| 9 | Close the lesson. | Students are signed out within 15 seconds; the class offers Start lesson again. |
| 10 | Resume the lesson if needed. On both student devices, start conversations and send messages. | Within five seconds of each stored prompt and reply, the matching card shows the latest text and its message count changes without a page reload. The other card does not change. |
| 11 | Watch the usage strip while the students chat with different enabled models. | Active students, total student messages, messages per student and model conversation counts match the activity. Assistant replies do not increase the message total. |
| 12 | Open each student's transcript after a completed reply, then remove one student and close the lesson. | Each transcript shows every prompt and reply with status and time. Both transcripts remain readable after removal and lesson closure. |
| 13 | After step 6 produces an allowed-and-flagged or blocked-and-flagged message, open its transcript and mark the flag reviewed. | The transcript includes the stored message, category and action. The flag can be reviewed once, the card badge clears within five seconds, and the transcript shows who reviewed it. |
