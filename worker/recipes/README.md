# Model server recipes

A worker is a model server plus the agent in `worker/compose.yml`. The agent publishes port 8081, the worker's only port on the school network. The model server must not be reachable from the network: students could otherwise use it without the worker's API key.

Pick one of the two recipes below. In both, set `MAX_CONCURRENT` for the agent to the number of chats the model server runs at once.

## llama-server in Docker

`compose.yml` here runs llama.cpp's `llama-server` next to the agent on the same compose network, with no published port.

```bash
export SERVER_URL=... JOIN_TOKEN=...          # from the admin page's join command
export BACKEND_URL=http://llama:8080
docker compose -f worker/compose.yml -f worker/recipes/compose.yml --profile nvidia up -d
```

- `--profile nvidia` uses the GPU. On Windows that needs Docker Engine and the NVIDIA Container Toolkit inside WSL2 (ADR-0003) and a current NVIDIA driver on Windows.
- `--profile cpu` runs anywhere, slowly. Gemma 4 E2B is the model that fits.
- `MODEL` picks the weights, a Hugging Face GGUF repo with a quant tag. The default is `ggml-org/gemma-4-E2B-it-GGUF:Q4_0`. For a GPU: `ggml-org/gemma-4-E4B-it-GGUF:Q4_0` on 8 GB, `ggml-org/gemma-4-12B-it-GGUF:Q4_0` on 16 GB. Weights download once into the `models` volume.
- `MODEL_NAME` is the name the admin and students see (default `gemma-4-e2b-it`).

## Ollama installed natively

Ollama uses the GPU directly on Windows, Linux and the Mac (Metal). Pull a model, for example `ollama pull gemma4:e2b` (or `gemma4:e4b`, `gemma4:12b`), and set `OLLAMA_NUM_PARALLEL` to the agent's `MAX_CONCURRENT` before starting Ollama. The agent's default `BACKEND_URL`, `http://host.docker.internal:11434`, is Ollama on the same machine.

Keep Ollama off the network:

- Mac: Ollama listens on 127.0.0.1 by default. Leave it there; Docker Desktop's `host.docker.internal` reaches it.
- Linux and WSL2: the agent container reaches the host through the Docker bridge, not 127.0.0.1. Set `OLLAMA_HOST` to the bridge address (usually `172.17.0.1:11434`, see `ip addr show docker0`), never `0.0.0.0`, and add a firewall rule that drops port 11434 from anything but the bridge.

## When the server cannot reach the agent

The server calls the agent at the address its heartbeat came from, port 8081. When that address is wrong, set `WORKER_URL` for the agent to an address the server can reach. The usual case is a worker on the same machine as the server, such as a Mac running both for development: `WORKER_URL=http://host.docker.internal:8081`.

## Only the server reaches the agent

Students' devices must not reach a worker at all (R2.6). The agent refuses requests without the worker's key, but also let only the server's address in on port 8081. The best place is the school network itself (a VLAN or switch rule for the GPU machines); on the worker:

- Linux, or Ubuntu with Docker in WSL2 in NAT mode: Docker's published ports skip `ufw`, so use Docker's own chain. Replace `10.0.0.2` with the server's address. Rules added this way are lost on restart; keep them with `iptables-persistent`. **untested**

  ```sh
  sudo iptables -I DOCKER-USER -p tcp --dport 8081 ! -s 10.0.0.2 -j DROP
  ```

- Windows with WSL2 in mirrored mode, in an administrator PowerShell: **untested**

  ```powershell
  New-NetFirewallHyperVRule -Name classroom-ai-agent -DisplayName "classroom-ai agent" -Direction Inbound `
      -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8081 -RemoteAddresses 10.0.0.2
  ```

- Mac: the macOS firewall filters by app, not by address, so use the network rule above. **untested**

Check from a student device: `curl http://<worker address>:8081/v1/models` must time out or be refused, while the admin page still shows the worker up.
