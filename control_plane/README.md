# Control Plane (FastAPI + SQLite)

## Local Development

- Install dependencies: `make install`
- Apply DB migrations: `make init-db`
- Start API and loops: `make run`
- Run tests: `make test`

## Environment Variables

- `JENKINS_URL`
- `JENKINS_USER`
- `JENKINS_API_TOKEN`
- `DATABASE_URL` (default `sqlite:///./control_plane.db`)
- `LOOP_INTERVAL_SEC`, `GC_INTERVAL_SEC`
- `GLOBAL_MAX_VMS`, `LABEL_MAX_INFLIGHT`, `LABEL_BURST`
- `CONNECT_DEADLINE_SEC`, `DISCONNECTED_GRACE_SEC`, `VM_TTL_SEC`
- `DISABLE_BACKGROUND_LOOPS=true` (useful for tests)
