# Control Plane (FastAPI + SQLite)

## Local Development

- Install dependencies: `make install`
- Apply DB migrations: `make init-db`
- Start API and loops: `make run`
- Run tests: `make test`

## Docker Compose Development

- Copy env template: `cp .env.example .env`
- Build and start services: `docker compose up -d --build`
- Verify Jenkins: `http://localhost:8080`
- Verify control-plane health: `http://localhost:8000/healthz`
- Verify control-plane metrics: `http://localhost:8000/metrics`
- Open control-plane UI: `http://localhost:8000/ui`
- Verify fake node-agent: `http://localhost:9000/healthz`

Notes:
- Compose includes a `fake-node-agent` service and enables control-plane loops by default for local E2E behavior.
- Jenkins seeds a fake pipeline job with a short timeout (`10s` default) to trigger queue/provision/reconcile flows quickly.
- Control-plane connect deadline is tuned low (`10s`) in compose for fast recycle of never-connected leases.
- Ephemeral Jenkins agents use WebSocket transport by default in dev (`CP_JENKINS_AGENT_TRANSPORT=websocket`).
- Control-plane stores SQLite data in the `control_plane_data` volume at `/data/control_plane.db`.
- UI is read-only and uses a server-embedded snapshot (no browser API polling).

## Environment Variables

- `JENKINS_URL`
- `JENKINS_USER`
- `JENKINS_API_TOKEN`
- `JENKINS_AGENT_TRANSPORT` (`websocket` or `tcp`, default `websocket`)
- `DATABASE_URL` (default `sqlite:///./control_plane.db`)
- `NODE_AGENT_URL` (default `http://localhost:9000`)
- `NODE_AGENT_AUTH_TOKEN` (optional)
- `LOOP_INTERVAL_SEC`, `GC_INTERVAL_SEC`
- `GLOBAL_MAX_VMS`, `LABEL_MAX_INFLIGHT`, `LABEL_BURST`
- `CONNECT_DEADLINE_SEC`, `DISCONNECTED_GRACE_SEC`, `VM_TTL_SEC`
- `DISABLE_BACKGROUND_LOOPS=true` (useful for tests)
