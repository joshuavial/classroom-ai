# Manual tests

Checks a person runs by hand, on real devices, before a pilot lesson. Automated tests cover the logic; these cover what only a screen, a finger and a network can show. Record each run in `docs/pilot-checks.md` with the date, devices and result.

## Setup

1. Run `./scripts/init-env.sh` once, then start the stack on the Mac with `GUARD_DISABLED=1 BIND_ADDRESS=0.0.0.0 docker compose up -d --build`. `GUARD_DISABLED=1` is required for chat checks before the Step 6 guard lands. It disables safety checks, so use this LAN binding only for an isolated, supervised device check and do not leave the stack running afterward.
2. Note the server's LAN address with `ipconfig getifaddr en0`. If that is empty, use the active interface address shown in System Settings > Network. Open that address from each device before continuing.
3. On a laptop, open `/admin`, enter the setup code from `docker compose logs app`, and create the admin. Join a worker, enable one of its models, then open `/teach`.
4. After the checks, run `docker compose down` and return to the install guide's normal binding for ordinary use.

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
| 9 | While the lesson is open, use both student devices to start conversations and send messages. | Within five seconds of each stored prompt and reply, the matching card shows the latest text without a page reload. Its message count increases for each student prompt, not for assistant replies. The other card does not change. |
| 10 | Watch the usage strip and cards while the students chat with different enabled models. | The strip's active-student count, total student messages, model conversation counts and flag counts match the activity. Each card has that student's message count. Assistant replies do not increase the student-message total. |
| 11 | Open each student's transcript after a completed reply. In browser developer tools, record the student number from each `/api/students/NUMBER/transcript` request. Remove one student, then open `/api/students/NUMBER/transcript` for that student in a new tab. | Each transcript shows every prompt and reply with its status. The removed student's transcript request still returns the record. |
| 12 | After step 6 produces an allowed-and-flagged or blocked-and-flagged message, open its transcript and mark the flag reviewed. | The transcript includes the stored message, category and action. The flag can be reviewed once, the card badge clears within five seconds, and the transcript shows who reviewed it. |
| 13 | Close the lesson. In new tabs, open `/api/students/NUMBER/transcript` for both student numbers recorded in step 11. | Students are signed out within 15 seconds, the class offers Start lesson again, and both transcript requests still return the records after lesson closure. |
