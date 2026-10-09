# Container runtime on Windows without Docker Desktop

Research date: 2026-10-10. Sources were read on that date. Nothing here has been run on a Windows machine; GPU and boot behaviour still need checking on the Windows test machine.

## Why this is needed

The server and worker stacks run as Docker Compose services (ADR-0003). Docker Desktop is proprietary. Its terms require a paid subscription for "Government entities" and for organisations with 250 or more employees or US$10M or more revenue ([Docker Desktop licence](https://docs.docker.com/subscription/desktop-license/), [pricing FAQ, clause 4.2](https://www.docker.com/pricing/faq/)). The same licence page lists education as free use, so whether a New Zealand state school counts as a government entity or as education is not settled by either page. ADR-0001 treats this as a reason to find an open alternative.

## Comparison

| | Podman Desktop + Podman machine | Rancher Desktop (moby) | Docker Engine in WSL2 Ubuntu |
| --- | --- | --- | --- |
| Licence | Apache-2.0 (Podman, Podman Desktop, podman-machine-os). Machine image is Fedora; Fedora terms page not reachable | Apache-2.0 (app and its Alpine-based WSL distro) | Apache-2.0 (moby, CLI, compose, buildx, containerd, NVIDIA toolkit). WSL is MIT. Ubuntu free for internal organisational use |
| Org-size or government limit | None found | None found | None found |
| Windows 10 | Dropped in Podman 6.0. Would need the 5.8.x line | Dropped from 1.21 | WSL still runs on Windows 10 2004+, but systemd, `vmIdleTimeout` and mirrored networking are documented as Windows 11 only |
| NVIDIA GPU in containers | Documented via CDI inside the machine. `gpus: all` and `driver: nvidia` do not reach the GPU through docker-compose on Podman; the compose file must change to CDI device syntax | Not supported. Maintainer says not feasible in 1.x | Documented by NVIDIA. `gpus: all` is a standard Compose feature that docker-ce honours |
| Compose file unchanged | No (GPU syntax). Profiles and healthchecks work in podman-compose; `service_healthy` against docker-compose on Podman unconfirmed | Yes, except GPU | Yes |
| Port 443 | Needs a rootful machine | Needs admin install with the Privileged Service | Rootful dockerd, no extra step |
| Start on boot, no user logged in | Not documented. Open bug | Not possible. Runs in a user session | Not documented by Microsoft. Possible with Task Scheduler; open WSL bugs. Untested |
| LAN access | Up to WSL: mirrored mode or portproxy | Privileged Service plus firewall rule | Up to WSL: mirrored mode or portproxy |
| Install effort | Installer or winget, machine init, toolkit inside machine | MSI or winget, GUI, no GPU | About ten Linux commands plus WSL settings and a scheduled task |

## Findings

### Podman Desktop and Podman machine

- Licences: Podman ([LICENSE](https://github.com/containers/podman/blob/main/LICENSE)), Podman Desktop ([LICENSE](https://github.com/containers/podman-desktop/blob/main/LICENSE)) and [podman-machine-os](https://github.com/containers/podman-machine-os) are Apache-2.0. The WSL machine image is built from `quay.io/fedora/fedora` ([Containerfile.WSL](https://raw.githubusercontent.com/containers/podman-machine-os/main/podman-image/Containerfile.WSL)). Fedora's legal pages at docs.fedoraproject.org returned a bot-protection "Access Denied" page, so the distro terms are not verified here. No Red Hat account is needed except for optional Red Hat extensions ([Red Hat](https://www.redhat.com/en/topics/containers/what-is-podman-desktop)).
- Versions: Podman 6.1.3 and 5.8.8 (29 September 2026), Podman Desktop 1.29.3 (1 September 2026), podman-compose 1.6.0 ([Podman releases](https://github.com/containers/podman/releases), [Podman Desktop releases](https://github.com/containers/podman-desktop/releases), [podman-compose releases](https://github.com/containers/podman-compose/releases)).
- Windows 10: Podman 6.0 "removed support for running on Windows 10" ([v6.0.0 notes](https://github.com/containers/podman/releases/tag/v6.0.0)). The Podman Desktop install page still lists Windows 10 ([install docs](https://podman-desktop.io/docs/installation/windows-install)), which conflicts.
- GPU: the documented path is to install `nvidia-container-toolkit` inside the machine, run `nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`, and run containers with `--device nvidia.com/gpu=all`. The spec must be regenerated after a driver upgrade ([Podman Desktop GPU docs](https://podman-desktop.io/docs/podman/gpu)). GPU is WSL only, not Hyper-V machines.
- GPU through compose: `podman compose` uses docker-compose if installed, otherwise podman-compose ([podman-compose(1)](https://docs.podman.io/en/latest/markdown/podman-compose.1.html)). Podman's Docker-compatible API only acts on device requests with driver `cdi` ([containers_create.go](https://raw.githubusercontent.com/containers/podman/main/pkg/api/handlers/compat/containers_create.go)), so `gpus: all` and `driver: nvidia` are not expected to reach the GPU. This is a reading of the source, not a test. `driver: cdi` with `device_ids: [nvidia.com/gpu=all]` works from 5.4 ([PR #25171](https://github.com/containers/podman/pull/25171)), and plain `devices: [nvidia.com/gpu=all]` was fixed in 6.0 ([PR #28495](https://github.com/containers/podman/pull/28495)). podman-compose maps `driver: nvidia` to CDI but has no handling for `gpus:` ([podman_compose.py](https://raw.githubusercontent.com/containers/podman-compose/main/podman_compose.py)). Related reports: [#28309](https://github.com/containers/podman/issues/28309) and [#28436](https://github.com/containers/podman/issues/28436), both closed "not planned" in May 2026; [#25196](https://github.com/containers/podman/issues/25196), closed but later comments say it still fails.
- Compose: podman-compose supports profiles (1.1.0), `COMPOSE_PROFILES` (1.6.0), and enforces `service_healthy` (1.4.0) ([releases](https://github.com/containers/podman-compose/releases)). No Podman-maintained statement was found that `service_healthy` works with docker-compose against Podman.
- Ports: machines are rootless by default and rootless containers cannot publish ports below 1024; the tutorial suggests a rootful machine for 80 and 443 ([Podman for Windows](https://github.com/containers/podman/blob/main/docs/tutorials/podman-for-windows.md), [podman machine init](https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html)).
- Boot: no documented way to start before login. [#20770](https://github.com/containers/podman/issues/20770) (Task Scheduler with "run whether user is logged on or not" fails) is open since November 2023. Podman Desktop starts the engine on login ([settings reference](https://podman-desktop.io/docs/configuration/settings-reference)).
- LAN: a maintainer says LAN reachability is "up to WSL and not something we control" ([#29377](https://github.com/containers/podman/issues/29377)). Port forwarding was broken in 6.0.2 and 6.1.0 and fixed in 6.1.1, which needs the machine recreated ([v6.1.1](https://github.com/containers/podman/releases/tag/v6.1.1)). [#29778](https://github.com/containers/podman/issues/29778), a mirrored-networking port conflict, is open.

### Rancher Desktop

- Licences: Apache-2.0 for the app ([LICENSE](https://raw.githubusercontent.com/rancher-sandbox/rancher-desktop/main/LICENSE)) and its WSL distro, which is built from Alpine ([rancher-desktop-wsl-distro](https://github.com/rancher-sandbox/rancher-desktop-wsl-distro)). Bundled docker CLI 29.6.2, Compose 5.3.1 and buildx 0.35.0 in 1.24.0 are Apache-2.0 ([v1.24.0](https://github.com/rancher-sandbox/rancher-desktop/releases/tag/v1.24.0)). No account, price or org-size limit found ([installation docs](https://docs.rancherdesktop.io/getting-started/installation/)).
- Windows 10: listed up to 1.20; from 1.21 only Windows 11 and Server 2025 ([1.20 docs](https://docs.rancherdesktop.io/1.20/getting-started/installation/), [1.21 docs](https://docs.rancherdesktop.io/1.21/getting-started/installation/)).
- GPU: not supported. [#3968](https://github.com/rancher-sandbox/rancher-desktop/issues/3968) is open; installing the NVIDIA toolkit in the distro did not work, and the maintainer said on 30 November 2025 that it is not reasonably feasible in 1.x. [#8487](https://github.com/rancher-sandbox/rancher-desktop/issues/8487) is open. Rancher Desktop 2.0 alpha moves to openSUSE on Lima, not WSL, says GPU is "not built yet" and is "not for production" ([2.0 blog post](https://docs.rancherdesktop.io/blog/welcome-to-rancher-desktop-2/)).
- Boot: runs in the user's session; it can start at login ([behaviour preferences](https://docs.rancherdesktop.io/ui/preferences/application/behavior/)). Running as a Windows service is an open request ([#7156](https://github.com/rancher-sandbox/rancher-desktop/issues/7156)). Autostart at login is reported unreliable with moby and mirrored networking ([#10272](https://github.com/rancher-sandbox/rancher-desktop/issues/10272), open).
- LAN: without the admin-installed Privileged Service, ports bind to 127.0.0.1 only, and 80 and 443 need admin ([installation docs](https://docs.rancherdesktop.io/getting-started/installation/), [port forwarding](https://docs.rancherdesktop.io/ui/port-forwarding/)). [#2808](https://github.com/rancher-sandbox/rancher-desktop/issues/2808) (published port not reachable on external IP) is open.
- Install: MSI or `winget install SUSE.RancherDesktop`; settings can be locked by registry or Group Policy ([deployment profiles](https://docs.rancherdesktop.io/getting-started/deployment/)). The docs page on WSL network preferences returned 404.

### Docker Engine inside WSL2 Ubuntu

- Licences: moby ([LICENSE](https://github.com/moby/moby/blob/master/LICENSE)), docker CLI, Compose ([LICENSE](https://github.com/docker/compose/blob/main/LICENSE)), buildx and containerd are Apache-2.0. Docker says the licensing of Docker Engine and other Moby projects is not changing ([Docker Desktop licence](https://docs.docker.com/subscription/desktop-license/)). WSL is MIT and open source since May 2025, except a few WSL1 and file-redirector components ([announcement](https://blogs.windows.com/windowsdeveloper/2025/05/19/the-windows-subsystem-for-linux-is-now-open-source/), [repo](https://github.com/microsoft/WSL)). The NVIDIA Container Toolkit is Apache-2.0 ([LICENSE](https://github.com/NVIDIA/nvidia-container-toolkit/blob/main/LICENSE)). Canonical's policy says Ubuntu is freely available to organisations for internal use ([IP policy](https://canonical.com/legal/intellectual-property-policy)); no WSL-specific Ubuntu licence page was found.
- Versions: WSL 3.0.1 (29 September 2026) ([releases](https://github.com/microsoft/WSL/releases)), NVIDIA Container Toolkit 1.20.1 (19 September 2026) ([releases](https://github.com/NVIDIA/nvidia-container-toolkit/releases)). The plain "Ubuntu" WSL distro tracks the newest LTS, now 26.04 ([Ubuntu WSL distributions](https://ubuntu.com/wsl/docs/latest/reference/distributions/)), which Docker supports ([Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
- GPU: NVIDIA's guide says to install only the Windows driver and never a Linux display driver inside WSL, and lists Docker with the Container Toolkit as supported ([CUDA on WSL user guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)). The toolkit is installed from NVIDIA's apt repo and wired in with `nvidia-ctk runtime configure --runtime=docker` ([install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)); on WSL2 the runtime uses just-in-time CDI ([CDI support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)). Compose `gpus: all` is part of the Compose spec ([services reference](https://docs.docker.com/reference/compose-file/services/)) and was added in Compose 2.30.0 ([release](https://github.com/docker/compose/releases/tag/v2.30.0)). Open bug: `nvidia-ctk cdi generate` cannot find `libdxcore.so` on WSL2 ([#1739](https://github.com/NVIDIA/nvidia-container-toolkit/issues/1739), open since March 2026); it affects generated CDI specs, and the issue has a manual workaround. The guide also notes Maxwell GPUs are not officially supported.
- Boot: systemd is turned on with `[boot] systemd=true` in `/etc/wsl.conf`, and idle shutdown is controlled by `instanceIdleTimeout` and `vmIdleTimeout` in the user's `.wslconfig` ([WSL settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)). Microsoft documents no way to start WSL at boot without a logon. Store WSL not accessible from session 0 is open ([#9231](https://github.com/microsoft/WSL/issues/9231)), a boot-start feature request was closed "not planned" ([#9346](https://github.com/microsoft/WSL/issues/9346)), and a Task Scheduler attempt on Server 2025 failed with no maintainer reply ([discussion #14261](https://github.com/microsoft/WSL/discussions/14261)). Two open bugs report WSL stopping or restarting under docker-ce when idle timeouts are left at defaults ([#13416](https://github.com/microsoft/WSL/issues/13416), [#40363](https://github.com/microsoft/WSL/issues/40363)).
- LAN: in the default NAT mode only localhost is forwarded; LAN access needs `netsh interface portproxy` to the WSL address, which can change. Mirrored mode (Windows 11 22H2 or later) allows LAN connections once the Hyper-V firewall allows them ([WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking), [Hyper-V firewall](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/hyper-v-firewall)). The Hyper-V firewall cannot be set by Group Policy, only PowerShell or Intune. In [#41284](https://github.com/Microsoft/wsl/issues/41284) other LAN machines could reach published ports in mirrored mode while the host's own IP could not. Open mirrored-mode bugs with Docker: [#40984](https://github.com/microsoft/WSL/issues/40984) ("address already in use"), [#13868](https://github.com/microsoft/WSL/issues/13868), [#40591](https://github.com/microsoft/WSL/issues/40591). Docker-published ports bypass ufw ([packet filtering](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).

### Other options

- Ubuntu Server installed on the server PC instead of Windows: same Apache-2.0 software, native systemd boot and native LAN ports, and the NVIDIA toolkit guide targets it directly. It avoids every boot and networking issue above, if the school will run a Linux machine.
- Hyper-V Linux VM: workable for the CPU-only server, but GPU passthrough (DDA) needs Windows Server as the host ([DDA](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/deploy/deploying-graphics-devices-using-dda)), so not for workers.
- WSL containers (`wslc`), generally available in WSL 3.0.1: supports `--gpus all` but not Compose, and a Docker API endpoint is an open request ([preview post](https://devblogs.microsoft.com/commandline/wsl-container-is-now-available-for-public-preview/), [#40976](https://github.com/microsoft/WSL/issues/40976)). Not usable for this stack yet.

## Recommendation

Use Docker Engine (docker-ce) with the Compose plugin inside a WSL2 Ubuntu distro on Windows 11, with mirrored networking. Do not support Windows 10.

What tips it:

- It is the only option where NVIDIA documents the GPU path and the planned `gpus: all` compose files run unchanged. Rancher Desktop has no GPU support, and Podman needs the GPU written as CDI devices, with a history of compose GPU bugs closed "not planned".
- Every part is Apache-2.0 or MIT with no org-size or government clause, and it is the same Docker Engine and Compose used on Linux and in development, so there is one set of instructions.
- None of the three options has a documented way to run with nobody logged in. docker-ce in WSL has the fewest layers between the task and the containers, and the same compose files move unchanged to Ubuntu Server if WSL boot proves unreliable.

If the test machine shows the stack cannot be started at boot without a login, put the server on Ubuntu Server and keep WSL only for the GPU workers, which can sit at a logged-in lab account.

## Install steps (Windows 11)

Run these on the Windows test machine first. Steps marked "untested, check on the Windows test machine" are put together from the cited docs and have not been run.

### Both machines

1. Check Windows 11 is version 22H2 or later (Settings, System, About). Mirrored networking needs it.
2. Create a local Windows account for the stack, for example `classroomai`, with a password that does not expire. WSL distros and `.wslconfig` belong to one Windows user, so every WSL step below is done signed in as this account. Untested, check on the Windows test machine.
3. On a GPU worker only: install the current NVIDIA Game Ready or Studio driver for Windows from nvidia.com. Do not install any NVIDIA driver inside Ubuntu ([CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)).
4. In an administrator PowerShell, run `wsl --install -d Ubuntu-24.04` (or `Ubuntu` for the newest LTS), then restart Windows. If it hangs, add `--web-download` ([WSL install](https://learn.microsoft.com/en-us/windows/wsl/install)). Sign in as `classroomai`, open Ubuntu from the Start menu and create the Linux user when asked.
5. Create `C:\Users\classroomai\.wslconfig` with:

   ```ini
   [wsl2]
   networkingMode=mirrored
   vmIdleTimeout=-1

   [general]
   instanceIdleTimeout=-1
   ```

   ([WSL settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)). The idle settings stop WSL shutting the stack down; see [#40363](https://github.com/microsoft/WSL/issues/40363).
6. In Ubuntu, turn on systemd: `sudo nano /etc/wsl.conf`, add

   ```ini
   [boot]
   systemd=true
   ```

   then in PowerShell run `wsl --shutdown` and reopen Ubuntu.
7. Install Docker Engine from Docker's apt repository ([Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)):

   ```sh
   sudo apt-get update
   sudo apt-get install -y ca-certificates curl git
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc
   sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
   Types: deb
   URIs: https://download.docker.com/linux/ubuntu
   Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
   Components: stable
   Signed-By: /etc/apt/keyrings/docker.asc
   EOF
   sudo apt-get update
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo usermod -aG docker $USER
   ```

   Close and reopen Ubuntu, then check with `docker run --rm hello-world` and `docker compose version`. Docker starts with systemd by default ([post-install](https://docs.docker.com/engine/install/linux-postinstall/)). Membership of the `docker` group is equivalent to root inside Ubuntu.
8. Clone the repository inside the Ubuntu file system, not under `/mnt/c`, for file performance ([WSL file systems](https://learn.microsoft.com/en-us/windows/wsl/filesystems)): `git clone <repository URL> ~/classroom-ai`.

### Server machine

9. `cd ~/classroom-ai`, copy and edit `.env` as `docs/install.md` describes, then `docker compose up -d`. Check with `docker compose ps` that every service is healthy.
10. Make sure nothing else on Windows uses ports 80 or 443 (for example IIS). In mirrored mode they are shared with Windows. Run `netstat -ano | findstr ":443"` in PowerShell. Untested, check on the Windows test machine.
11. Allow 80 and 443 in through the Hyper-V firewall, in an administrator PowerShell ([WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking)):

    ```powershell
    New-NetFirewallHyperVRule -Name classroom-ai-web -DisplayName "classroom-ai web" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 80,443
    ```

    Whether a Windows Defender Firewall rule is also needed is untested, check on the Windows test machine. If it is: `New-NetFirewallRule -DisplayName "classroom-ai web" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow -Profile Domain,Private`.
12. From another lab PC, browse to `https://<server name or IP>`. Expect the certificate warning until the root certificate is trusted. Untested, check on the Windows test machine; mirrored mode with Docker has open bugs ([#40984](https://github.com/microsoft/WSL/issues/40984)). If it fails, switch `.wslconfig` back to NAT and forward with `netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=443 connectaddress=<address from wsl hostname -I>` (and the same for 80), remembering the address can change after a restart.

### GPU worker machine

13. Install the NVIDIA Container Toolkit in Ubuntu ([install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)):

    ```sh
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    ```

14. Test the GPU: `docker run --rm --gpus all ubuntu nvidia-smi` ([sample workload](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/sample-workload.html)). It should list the card. Untested, check on the Windows test machine. If it fails with an NVML error, see [#1739](https://github.com/NVIDIA/nvidia-container-toolkit/issues/1739).
15. Run the join command from the admin console (bash form) inside Ubuntu, with the `nvidia` profile for the llama-server recipe. Check `docker compose -f worker/compose.yml ps`.
16. Allow the agent's port in through the Hyper-V firewall as in step 11, using that port instead of 80,443, and check the server's admin page shows the worker live. Untested, check on the Windows test machine.

### Start on boot (both machines)

17. Give every service `restart: unless-stopped` in the compose files so Docker brings the stack back when it starts. Then WSL only needs to be running.
18. In Task Scheduler, create a task: trigger "At startup"; run as `classroomai`; "Run whether user is logged on or not"; action `wsl.exe` with arguments `-d Ubuntu-24.04 --exec /usr/bin/sleep infinity`; on the Settings tab, untick "Stop the task if it runs longer than". The long-running `sleep` keeps the WSL instance up. Untested, check on the Windows test machine. Open WSL and Podman bugs report this kind of task failing without a login ([WSL #9231](https://github.com/microsoft/WSL/issues/9231), [Podman #20770](https://github.com/containers/podman/issues/20770)).
19. Restart the machine, do not sign in, and from another PC check the server page (or the worker showing live). If the stack is not up, the fallbacks are automatic sign-in of `classroomai` with the screen locked, which is weaker for a shared lab, or Ubuntu Server for the server machine.

## Open questions for the Windows test machine

- Does the scheduled task in step 18 bring the stack up with nobody signed in, and does it stay up for a school day?
- Do lab PCs reach 443 in mirrored mode, and is a Defender firewall rule needed as well as the Hyper-V rule?
- Does `gpus: all` in compose reach the card, and does llama-server use it at the expected speed?
- Does the stack survive a Windows Update restart and a WSL update?
