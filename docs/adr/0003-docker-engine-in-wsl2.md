# ADR-0003: Docker Engine in WSL2 on Windows

Status: Proposed. Date: 2026-10-10.

## Context

No pilot school yet. Windows 11 is the likely host. Development happens on a Mac. The server and the worker run as Docker Compose services, and the worker's llama-server recipe uses an NVIDIA GPU through `gpus: all`.

Docker Desktop is proprietary, and its terms require a paid subscription for government entities, which may include state schools. That rules it out as the documented runtime (ADR-0001). The alternatives compared in `docs/research/container-runtime.md` are Podman Desktop, Rancher Desktop and Docker Engine (docker-ce) inside a WSL2 Ubuntu distro.

## Decision

On Windows, each machine runs Docker Engine with the Compose plugin inside WSL2 Ubuntu, with WSL mirrored networking so lab PCs reach the server on 443. GPU workers add the NVIDIA Container Toolkit; the only NVIDIA driver is the Windows one. Windows 11 22H2 or later is required.

Docker Engine, Compose, the NVIDIA Container Toolkit and WSL are all Apache-2.0 or MIT, with no organisation-size or government clause. It is the only option where NVIDIA documents the GPU path and our compose files run unchanged: Rancher Desktop has no GPU support, and Podman needs GPUs written as CDI devices, with compose GPU bugs closed "not planned".

The same compose files run on Ubuntu Server. If the Windows test machine shows WSL cannot keep the server stack up through a restart with nobody signed in, the server machine moves to Ubuntu Server and only GPU workers stay on Windows.

The model server may run in Docker or natively (ADR-0007). On the development Mac any Docker-compatible runtime is fine, and the worker uses a native model server on the Mac GPU.

## Consequences

Install is about ten Linux commands plus WSL settings, written up step by step in `docs/install.md`. Starting at boot with nobody signed in relies on a scheduled task that keeps WSL running, which Microsoft does not document and which has open bugs. Mirrored networking also has open bugs with Docker; NAT plus `netsh portproxy` is the fallback. Boot, LAN access and GPU speed are all checked on the Windows test machine in step 9 of the implementation plan. Windows 10 is not supported.
