# ADR-0001: Open source only

Status: Proposed. Date: 2026-10-06.
## Context

Schools need something they can audit, fork and keep running without a vendor. Many classroom AI products are paid cloud services.

## Decision

Every component is OSI-licensed and every model openly licensed. No SaaS, vendor accounts or telemetry sent home. LM Studio, Bionic, Open WebUI from v0.6.6, LobeChat, and models under the Llama Community License or the Gemma Terms of Use (Gemma 3 and earlier, ShieldGemma) are out. Gemma 4 is Apache-2.0 and allowed.

## Consequences

Some strong tools are excluded. Docker Desktop is out as the documented runtime. It is proprietary and free only for organisations under 250 employees and US$10M revenue. Its terms say government entities need a paid subscription, which may include state schools. ADR-0003 uses Docker Engine, which is Apache-2.0, inside WSL2 instead.
