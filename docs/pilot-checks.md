# Pilot checks

Use this checklist for the Windows 11 NVIDIA test machine and for the physical
phone LAN check. It records how to run the checks and where to record the
results. No hardware check in this document is a claimed result. Leave
`Pass/fail` as `not run` until the check has been performed.

If a check fails, create a Beads issue. Put the issue ID in that row's `Notes`
column. Record enough detail to reproduce the failure, including the machine,
device, network mode, and relevant logs.

Keep the completed tables with the pilot evidence. The source procedures are
[`docs/install.md`](install.md), the [implementation plan](implementation-plan.md),
the [pilot acceptance criteria](prd-00-lab-pilot.md),
[`scripts/windows/wslconfig`](../scripts/windows/wslconfig),
[`scripts/windows/start-at-boot.ps1`](../scripts/windows/start-at-boot.ps1), and
[`tests/load.py`](../tests/load.py).

## 1. Physical phone LAN chat check

Use a server Mac, a worker with an enabled model, and a physical phone
on the same LAN. Record the Mac LAN IP and the Docker Compose project name.

1. On the server Mac, run:

   ```sh
   GUARD_DISABLED=1 BIND_ADDRESS=0.0.0.0 HTTP_PORT=8080 docker compose up -d --build
   ```

   `GUARD_DISABLED=1` disables safety checks. Use this only for the isolated
   pilot check, and do not leave this stack running after the test.

   Run `docker compose ls` and record the project name shown for this stack.

2. In the admin page, join a worker and enable its model.
3. On the Mac, open `http://<Mac-LAN-IP>:8080/teach` as a teacher or admin.
4. Create or open a class, start a lesson, and take an unused six-digit code
   from `Codes`.
5. On a phone on the same LAN, open `http://<Mac-LAN-IP>:8080/`.
6. Enter the code and a student name, then join.
7. Confirm all of the following on the phone:
   - the monitoring notice is shown before chat;
   - the enabled model appears;
   - `New conversation` works;
   - a reply renders incrementally;
   - the conversation remains in the list.
8. Finish with:

   ```sh
   COMPOSE_PROJECT_NAME=<the-project-name> docker compose down
   ```

Record the result here. Do not mark it passed from a build or code inspection.

| Check | Pass/fail | Date | Machine | Notes |
| --- | --- | --- | --- | --- |
| Phone joins over the Mac LAN and completes the chat checks above | not run |  |  |  |

## 2. Step 9: Windows NVIDIA install and acceptance checks

Start with a clean Windows 11 NVIDIA test machine. Follow the [README](../README.md)
alone for acceptance 1. Then follow [Install](install.md) for the Windows,
worker, certificate, firewall, and start-on-boot checks. Do not record a pass
until the expected evidence is visible from the stated test device.

### Acceptance 1: README-only server and admin sign-in

On the clean machine, follow only the README quick start. Confirm that the
server is running and the admin can sign in. Do not use this row to claim that
the Windows install or worker setup passed.

### Windows and worker procedure

Run every applicable item below. The `untested` labels in [Install](install.md)
identify the areas that this run must check.

1. Create the local Windows stack account and complete the WSL2 installation
   described in [Install, Windows: Docker Engine in WSL2](install.md#windows-docker-engine-in-wsl2).
   On the GPU worker, install the Windows NVIDIA driver. Do not install an
   NVIDIA driver inside Ubuntu.
2. Copy [`wslconfig`](../scripts/windows/wslconfig) to the stack account's
   Windows profile. Verify and record whether these settings are accepted and
   remain in effect after `wsl --shutdown` and a restart:
   `networkingMode=mirrored`, `vmIdleTimeout=-1`, and
   `instanceIdleTimeout=-1`.
3. Clone the repository inside the Ubuntu file system, not under `/mnt/c`.
   Enable systemd in WSL2 and install Docker Engine in Ubuntu by following
   [Install](install.md#windows-docker-engine-in-wsl2). Verify Docker with
   `hello-world` and `docker compose version`.
4. On the GPU worker, follow the NVIDIA Container Toolkit and model-server
   instructions in [worker recipes](../worker/recipes/README.md). Verify that
   the model is available to the worker container. Record the GPU, driver,
   toolkit, model, and image or recipe used. Do not assume GPU support from a
   successful container build.
5. Verify that Windows is not already using ports 80 or 443. Apply the
   documented Hyper-V firewall rule. If another lab PC cannot connect, apply
   and test the possible Windows Defender Firewall rule described in [Install](install.md#windows-docker-engine-in-wsl2).
   If mirrored networking fails and the documented NAT and port-proxy fallback
   is used, record that mode and the WSL address.
6. Copy the server root certificate from [Install, Trust the certificate](install.md#trust-the-certificate)
   and test HTTPS on every applicable device type. Test both the device's
   normal browser and Firefox when Firefox is in scope.
7. After HTTPS works for the original `SERVER_NAME`, give the server a second
   test DNS name that resolves to the same address. Change `SERVER_NAME` in
   `.env`, run `docker compose up -d`, and confirm that the second name opens
   over HTTPS without a warning on a device that trusts the server root.
   Restore the original name and run `docker compose up -d` again.
8. Join the worker from the server admin page using the current join command.
   With HTTPS enabled, put the root certificate at `worker/server-ca.crt` as
   described in [Install, Join a worker](install.md#join-a-worker). Confirm
   that the worker appears `up`, its models are listed, and the intended model
   can be enabled.
9. Apply the documented network restriction so only the server can reach the
   worker agent's published port 8081. From a student device, run
   `curl http://<worker-address>:8081/v1/models` and confirm that it times out
   or is refused while the admin page still shows the worker as up. Record the
   source device, worker address, and refusal evidence.
10. Copy [`start-at-boot.ps1`](../scripts/windows/start-at-boot.ps1) and run it
   as described in [Install, Start on boot](install.md#start-on-boot). Inspect
   the scheduled task and record each of these facts separately:
   - `vmIdleTimeout=-1` and `instanceIdleTimeout=-1` were accepted in the WSL
     configuration;
   - the task has a zero execution time limit;
   - the task uses `RunLevel Limited`;
   - the task has a stored password and is configured to run whether the user
     is signed in or not;
   - the task starts with nobody signed in;
   - the account does or does not need the `Log on as a batch job` right.
11. Restart the machine and do not sign in. From another PC, open the server
    page. On a worker, use the admin page to confirm that the worker is up with
    its models listed.

### Acceptance and untested-area results

Use one row per result. For certificate checks, use `Machine` for the device
type and model, for example `iPhone, Safari` or `Windows 11, Edge`.

| Check | Pass/fail | Date | Machine | Notes |
| --- | --- | --- | --- | --- |
| 1. README-only server running and admin signed in | not run |  |  |  |
| WSL2 installation on clean Windows 11 | not run |  |  |  |
| Repository cloned inside the Ubuntu file system | not run |  |  |  |
| Docker Engine in Ubuntu, including hello-world and Compose | not run |  |  |  |
| Mirrored networking reaches the published service | not run |  |  |  |
| `vmIdleTimeout=-1` accepted and retained | not run |  |  |  |
| `instanceIdleTimeout=-1` accepted and retained | not run |  |  |  |
| Hyper-V firewall rule permits the required lab access | not run |  |  |  |
| Windows Defender Firewall rule was needed and, if needed, works | not run |  |  |  |
| GPU is available to the worker container | not run |  |  |  |
| Changing `SERVER_NAME` on the running server works, then the original name is restored | not run |  |  |  |
| HTTPS root trust: Windows | not run |  |  |  |
| HTTPS root trust: macOS | not run |  |  |  |
| HTTPS root trust: iPhone or iPad | not run |  |  |  |
| HTTPS root trust: Android | not run |  |  |  |
| HTTPS root trust: ChromeOS | not run |  |  |  |
| HTTPS root trust: Firefox | not run |  |  |  |
| 2. Second NVIDIA machine joins and appears up with models | not run |  |  |  |
| Worker starts with nobody signed in | not run |  |  |  |
| Scheduled task has zero execution time limit | not run |  |  |  |
| Scheduled task uses `RunLevel Limited` | not run |  |  |  |
| Scheduled task stores the account password | not run |  |  |  |
| Account has or does not need `Log on as a batch job` | not run |  |  |  |
| 5. Direct student-device access to the GPU is refused | not run |  |  |  |
| 8. Unplugging server internet changes none of the relevant behavior | not run |  |  |  |

For acceptance 8, keep the school LAN connected while removing the server's
internet connection. Repeat the relevant server, worker, student chat, and
direct-GPU-access checks. Record exactly what was repeated and any change.

## 3. Step 10: GPU load test

Run this from the server checkout, where the HTTPS endpoint is reachable and
Docker Compose can read the server Postgres container. The prerequisites are:

- the server and GPU worker are up;
- the intended model is enabled;
- teacher credentials are ready;
- the Step 6 guard schema and pipeline are present for the pilot capacity run.

The harness can be rehearsed against the fake guard before Step 6 lands. That
rehearsal is not the pilot capacity result, and its guard latency must remain
`pending_step_6`.

Use the current harness and its existing options. Replace each placeholder,
including the supplied teacher password and the CA certificate path:

```sh
uv run python tests/load.py --students 30 --teacher-password '<teacher-password>' --model '<model-name>' --base-url 'https://<server-name>' --verify '<path-to-classroom-ai-root.crt>' --output "artifacts/load-$(date +%Y-%m-%d).json"
```

The harness requires the teacher password as a command-line argument, which can
appear in shell history and the process list. Use a temporary password that is
not used anywhere else, run from the trusted server, and replace the password
after the run.

The command creates 30 simulated students, records the JSON artifact, and
returns a failure status for HTTP, stream, terminal, lost-message, or
unrecorded-reply failures. Do not add tuning flags that do not exist in
[`tests/load.py`](../tests/load.py).

From the captured JSON, including the guard latency that the harness reads from
Postgres, record:

- hardware, GPU, driver, model, model-server configuration, worker
  concurrency, server configuration, and the run date;
- `attempted`, `done`, `blocked`, and every failure count;
- `lost_messages` and `unrecorded_replies`;
- `time_to_first_segment` p50, p95, and max;
- guard latency by `prompt` and `reply_segment`, each with p50, p95, and max.

The harness reports guard latency with `status=pending_step_6` until the Step 6
schema and pipeline land. Keep that status as pending. Do not use a rehearsal
with pending guard latency as the pilot result. If the first reply is far over
five seconds, repeat with longer guard segments or more model-server
concurrency, using only settings supported by the current implementation.
Record every run in `docs/capacity.md`. Create or update that document from the
captured JSON after the run, including the configuration and the reason for
each repeat.

### Per-run results

| Run | Pass/fail | Date | Machine | Notes |
| --- | --- | --- | --- | --- |
| Pilot baseline after Step 6, 30 students | not run |  |  |  |
| Repeat, if first reply is far over five seconds | not run |  |  |  |

## 4. Step 7 engineering browser check

- Host: macOS with Docker Desktop.
- Browser: Playwright Chromium, first at the default laptop viewport and then at an 820 x 1180 iPad-class viewport.
- Participants: one teacher context and two isolated student browser contexts.
- Stack: fresh isolated Compose projects for the server and worker, the real worker agent, and the repository fake compatible model backend. The guard seam was disabled for this smoke because guard service integration belongs to step 6.
- Result: passed at both viewports. Both student cards appeared without a reload. One student received the streamed reply, and the matching teacher card showed it within five seconds. The second card remained separate. The usage strip reported one student message and one model conversation. The iPad-class page had no horizontal clipping or overlapping controls.
- Visual artifact: `.etude/tmp/cai-1ir.8/ipad-console.png`, SHA-256 `00eee80916ea338c3a431cf6defed17358d1d779894191633253ce176384ca04`.

The physical laptop and iPad Safari pass, including print preview, keyboard-only use, rotation and 200 percent text zoom, remains open on `cai-1ir.31`.
