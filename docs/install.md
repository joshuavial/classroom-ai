# Install

How to install the classroom-ai server on a school network, keep it running, trust its certificate on each device, and upgrade it. The choices behind these steps are in [architecture.md](architecture.md), [ADR-0003](adr/0003-docker-engine-in-wsl2.md) and [research/container-runtime.md](research/container-runtime.md).

Steps marked **untested** have been put together from the cited documentation and have not been run yet. They are checked on the Windows test machine in step 9 of the [implementation plan](implementation-plan.md). Everything else was run on a Mac with Docker; the HTTPS checks there used other host ports, because that Mac's port 443 is taken by another program.

## What you need

- One machine for the server, on the school network, that stays on during lessons. It needs no GPU. Windows 11 22H2 or later, Ubuntu (Server or Desktop) 24.04 or later, or a Mac.
- Docker Engine with the Compose plugin. On Windows that runs inside WSL2 Ubuntu (below); on Ubuntu install it from [Docker's apt repository](https://docs.docker.com/engine/install/ubuntu/). Docker Desktop is not needed and not recommended for schools (its licence may require a paid subscription for government entities).
- `git`, and internet access while installing. Once installed, the server needs no internet (R1.3).
- A name for the server that students will type: a local DNS name such as `classroom.school.lan`, or the server's fixed IPv4 address. Ask whoever runs the school network to give the server a fixed address (a DHCP reservation).

## Install the server (Linux, or Ubuntu inside WSL2)

On Windows, first do [Windows: Docker Engine in WSL2](#windows-docker-engine-in-wsl2), then run these commands inside Ubuntu.

```sh
git clone <repository URL> ~/classroom-ai
cd ~/classroom-ai
./scripts/init-env.sh classroom.school.lan    # your server name or IP address
docker compose up -d --build
```

`init-env.sh` writes `.env` once, with a generated database password, `SERVER_NAME` set to the name you gave, `BIND_ADDRESS=0.0.0.0` so lab PCs can reach ports 80 and 443, and `COMPOSE_FILE=compose.yml:compose.https.yml`, which adds port 443 to every `docker compose` command run in this directory. It never overwrites an existing `.env`. The first `up` builds the app and web images, which takes a few minutes.

Check it:

```sh
docker compose ps                                  # every service running, db healthy
curl -k https://classroom.school.lan/healthz       # {"status":"ok","db":"ok"}
```

Then open `https://<server name>` in a browser on another machine. Until that device trusts the server's certificate the browser shows a warning; see [Trust the certificate](#trust-the-certificate).

Students must use exactly the name in `SERVER_NAME`. The certificate covers only that name, so typing the IP address when `SERVER_NAME` is a DNS name (or the other way round) shows a certificate error. To change the name, edit `SERVER_NAME` in `.env` and run `docker compose up -d`. A `.env` written for development has none of these lines: add `SERVER_NAME=<name>`, `BIND_ADDRESS=0.0.0.0` and `COMPOSE_FILE=compose.yml:compose.https.yml`, because `init-env.sh` never changes an existing `.env`.

### Development on a Mac

`./scripts/init-env.sh` with no server name leaves `SERVER_NAME` unset: Caddy serves plain HTTP on `http://localhost`, and compose publishes only port 80, on 127.0.0.1.

## HTTPS

Caddy serves HTTPS on 443 for `SERVER_NAME` with a certificate from its own local certificate authority (`tls internal`, set for every site by the `local_certs` option in `Caddyfile`), and redirects port 80 to 443. It needs no internet and no public domain. The authority's root certificate and key live in the `caddy_data` volume; keep that volume, or every device has to trust a new root. Caddy renews the server certificate on its own. The root is valid for ten years.

## Trust the certificate

Get the root certificate from the server once:

```sh
cd ~/classroom-ai
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./classroom-ai-root.crt
```

Copy `classroom-ai-root.crt` to each device (a USB stick, a shared drive, or your device management system), then trust it as below. Only trust a root certificate you took from your own server: a trusted root can vouch for any website.

| Device | How | Status |
| --- | --- | --- |
| Windows | Double-click the file, Install Certificate, Local Machine, "Place all certificates in the following store", Trusted Root Certification Authorities. For many PCs: `certutil -addstore -f Root classroom-ai-root.crt` in an administrator prompt, or a Group Policy under Computer Configuration, Public Key Policies, Trusted Root Certification Authorities. | untested |
| macOS | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain classroom-ai-root.crt`, or open it in Keychain Access, add it to System, and set "When using this certificate" to Always Trust. | untested |
| iPad and iPhone | Open the file (AirDrop or a link) to download the profile, install it in Settings, General, VPN and Device Management, then turn it on in Settings, General, About, Certificate Trust Settings. Managed devices: push it in a configuration profile. | untested |
| Android | Settings, Security, Encryption and credentials (the path varies by maker), Install a certificate, CA certificate. Managed devices: push it with your device management system. | untested |
| ChromeOS | `chrome://certificate-manager`, Local certificates, Custom, Import, and allow it to identify websites. Managed Chromebooks: Google Admin console, Devices, Networks, Certificates. | untested |
| Firefox (any system) | Firefox may keep its own list: Settings, Privacy and Security, Certificates, View Certificates, Authorities, Import, "Trust this CA to identify websites". | untested |

Test: open `https://<server name>` on the device. There should be no warning.

## Windows: Docker Engine in WSL2

Windows 11 22H2 or later. Do these steps on the server and on every Windows GPU worker. Sources for each step are in [research/container-runtime.md](research/container-runtime.md).

1. **untested** Create a local Windows account for the stack, for example `classroomai`, with a password that does not expire. WSL belongs to one Windows user, so do every step below signed in as this account.
2. On a GPU worker only: install the current NVIDIA driver for Windows from nvidia.com. Never install an NVIDIA driver inside Ubuntu.
3. In an administrator PowerShell: `wsl --install -d Ubuntu-24.04`, then restart Windows. If it hangs, add `--web-download`. Open Ubuntu from the Start menu and create the Linux user when asked. **untested**
4. Copy [`scripts/windows/wslconfig`](../scripts/windows/wslconfig) to `C:\Users\classroomai\.wslconfig`. It turns on mirrored networking, so lab PCs reach ports published inside WSL, and stops WSL shutting down when idle. **untested**
5. In Ubuntu, turn on systemd: add these lines to `/etc/wsl.conf` (`sudo nano /etc/wsl.conf`), then run `wsl --shutdown` in PowerShell and reopen Ubuntu. **untested**

   ```ini
   [boot]
   systemd=true
   ```

6. Install Docker Engine in Ubuntu, from Docker's apt repository. **untested**

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

   Close and reopen Ubuntu, then check `docker run --rm hello-world` and `docker compose version`. Being in the `docker` group is the same as being root inside Ubuntu.
7. Clone the repository inside Ubuntu's own file system (`~/classroom-ai`), not under `/mnt/c`, which is much slower.

On the server, continue with [Install the server](#install-the-server-linux-or-ubuntu-inside-wsl2), then:

8. Check nothing else on Windows uses ports 80 or 443, for example IIS: `netstat -ano | findstr ":443"` in PowerShell. In mirrored mode those ports are shared with Windows. **untested**
9. Let lab PCs in on 80 and 443, in an administrator PowerShell: **untested**

   ```powershell
   New-NetFirewallHyperVRule -Name classroom-ai-web -DisplayName "classroom-ai web" -Direction Inbound `
       -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 80,443
   ```

   If lab PCs still cannot connect, a Windows Defender Firewall rule may be needed too: `New-NetFirewallRule -DisplayName "classroom-ai web" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow -Profile Domain,Private`.
10. From another PC, open `https://<server name>`. If it does not connect, mirrored networking may be the problem (it has open bugs with Docker): set `networkingMode=nat` in `.wslconfig`, run `wsl --shutdown`, and forward the ports to WSL with `netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=443 connectaddress=<address from: wsl hostname -I>`, and the same for 80. The WSL address can change after a restart, so this fallback needs checking after each restart. **untested**

### Start on boot

Every service in the compose files has `restart: unless-stopped`, so the stack comes back whenever Docker starts. On Linux, Docker starts with the machine. On Windows, WSL has to be started and kept running; Microsoft documents no way to do that with nobody signed in, so this relies on a scheduled task:

1. Copy `scripts\windows\start-at-boot.ps1` to the Windows disk (from `\\wsl$\Ubuntu-24.04\home\<linux user>\classroom-ai`), then in an administrator PowerShell: `powershell -ExecutionPolicy Bypass -File .\start-at-boot.ps1 -User classroomai -Distro Ubuntu-24.04`. The account needs the "Log on as a batch job" right for a task that runs with nobody signed in; if the task never starts, grant it in Local Security Policy, User Rights Assignment. It asks for the account's password and registers a task that runs `wsl.exe -d Ubuntu-24.04 --exec /usr/bin/sleep infinity` at startup, whether or not anyone is signed in, with no time limit. **untested**
2. Restart the machine, do not sign in, and from another PC open the server page (or, on a worker, check the admin page shows it up). **untested**

If the stack does not come up without a sign-in, the fallbacks are signing `classroomai` in automatically with the screen locked (weaker for a shared lab PC), or running the server on Ubuntu Server, where none of this is needed.

## Upgrade

```sh
cd ~/classroom-ai
git pull
docker compose up -d --build
```

This rebuilds the app and web images and restarts them. The app applies any new database migrations as it starts, and the `pgdata` and `caddy_data` volumes are kept, so accounts, conversations, settings and the certificate authority survive. Never run `docker compose down -v`: `-v` deletes the volumes, and with them all the data.

A PostgreSQL major version upgrade is different: it is a dump and restore, done in a release that comes with its own instructions.
