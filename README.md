# Jenkins QEMU Provisioner

Lightweight control plane for running Jenkins builds on ephemeral QEMU VMs.

## What this project does

- Jenkins is the scheduler only.
- One queued job maps to one disposable VM (`1 job = 1 VM = 1 node`).
- VM lifecycle is ephemeral: boot -> run job -> terminate -> delete overlay disk.
- Control-plane and host node-agent logic are owned in this repo.

## Core components

- `control_plane/`: FastAPI + SQLite control-plane (scaler, provisioner, reconciler, GC).
- `docker-compose.yml`: local Jenkins + control-plane development stack.
- `jenkins/`: Jenkins bootstrap scripts for local admin/security initialization.
- `docs/`: architecture/design and operations notes.

## Quick start (local)

1. Copy environment file:

```bash
cp .env.example .env
```

2. Start Jenkins + control-plane:

```bash
docker compose up -d --build
```

3. Verify services:

- Jenkins: `http://localhost:8080`
- Control-plane health: `http://localhost:8000/healthz`
- Control-plane metrics: `http://localhost:8000/metrics`
- Control-plane UI: `http://localhost:8000/ui`
- Fake node-agent health (when enabled): `http://localhost:9000/healthz`

UI note:
- `/ui` is read-only and renders from a server-embedded snapshot.
- It does not query control-plane APIs from the browser.

Local E2E note:
- `fake-node-agent` is disabled by default; enable with profile: `docker compose --profile fake-agent up -d`.
- Control-plane loops are enabled by default and can point to fake node-agent via `CP_NODE_AGENT_URL`.
- Jenkins seeds a fake pipeline job (`fake-ephemeral-test`) with a `10s` timeout by default to exercise create/teardown paths quickly.
- Jenkins image includes `antisamy-markup-formatter` so bootstrap can render a clickable Control-plane dashboard button in system message.
- Jenkins bootstrap injects a Control-plane UI link (`JENKINS_CONTROL_PLANE_UI_URL`) in the Jenkins home message area.
- Control-plane connect deadline is set to `180s` by default in local compose (`CP_CONNECT_DEADLINE_SEC`) to allow slower boots before recycle.
- Ephemeral agents default to Jenkins WebSocket transport (`CP_JENKINS_AGENT_TRANSPORT=websocket`), so local dev does not require exposing port `50000`.
- Jenkins inbound TCP listener is disabled by default in dev (`JENKINS_AGENT_TCP_PORT=-1`).
- If Jenkins was already initialized, set `JENKINS_BOOTSTRAP_JOB_UPSERT=true` or recreate the `jenkins_home` volume to re-seed job config.
- If you change `JENKINS_CONTROL_PLANE_UI_URL`, restart Jenkins to refresh the injected UI link.

## Control-plane development

```bash
make install
make init-db
make run
make test
```

## Design and ops docs

- High-level design: `docs/jenkins-ephemeral-qemu-design.md`
- Operations notes: `docs/control-plane-operations.md`
- MVP checklist: `docs/mvp-acceptance-checklist.md`
- UI contract and scope: `docs/ui-dashboard.md`
- Node-agent contract: `docs/node-agent-contract.md`
- Node-agent runbook: `docs/node-agent-runbook.md`
- Fake local E2E contract: `docs/fake-node-agent-e2e.md`

## Task tracking

This repository uses `bd` (beads) for task tracking. See `AGENTS.md` for workflow requirements.
