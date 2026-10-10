# classroom-ai

A free, open-source kit that turns a school's own GPUs into a private AI service for its students. Students chat from any browser, the teacher sees every conversation live, and safety checks run on school hardware.

Working name. The lab pilot is being built: the server stack runs, and the student, teacher and admin features arrive step by step (see the implementation plan).

## Quick start

On a machine with Docker Engine and the Compose plugin (on Windows, inside WSL2 Ubuntu; see [docs/install.md](docs/install.md)):

```sh
git clone <repository URL> classroom-ai
cd classroom-ai
./scripts/init-env.sh classroom.school.lan    # the name or IP address students will type
docker compose up -d --build
```

Then open `https://classroom.school.lan` from another machine on the network, and trust the server's certificate on each device as [docs/install.md](docs/install.md) describes. For development on one machine, run `./scripts/init-env.sh` with no name and use `http://localhost`.

## Documents

- [Install](docs/install.md): server install, Windows with WSL2, HTTPS and trusting the certificate, start on boot, upgrade.
- [Vision](docs/vision.md): why this exists, who it is for and the principles behind it.
- [PRD 00: Lab pilot](docs/prd-00-lab-pilot.md): the first increment, one class piloting it for one lesson.
- [Worker recipes](worker/recipes/README.md): model servers for a GPU machine, and keeping students off it.
- [Architecture](docs/architecture.md) and [decision records](docs/adr/README.md): how it will be built.
- [Implementation plan](docs/implementation-plan.md): the ordered build steps for the lab pilot.
- [Research](docs/research/summary.md): the evidence behind these choices.

## Licence

MIT. See [LICENSE](LICENSE).
