# Control-Plane Operations

## Alerts (Initial Thresholds)

- `host_stale_total > 0` for 60s: warning
- `leases_never_connected_total >= 3` in 5m: warning
- `orphan_vm_cleanup_total >= 1` in 5m: warning
- `retry_exhausted_total >= 1` in 5m: warning
- `queue_to_connect_p95_seconds > 180` in 15m: warning

## Operator Actions

- Inspect leases: `GET /v1/leases`
- Filter leases: `GET /v1/leases?label=<label>&state=<state>&host_id=<host>`
- Force termination: `POST /v1/leases/{lease_id}/terminate`
- Disable host: `POST /v1/hosts/{host_id}/disable`
- Enable host: `POST /v1/hosts/{host_id}/enable`

## Failure Triage

- If host is stale, disable host, investigate node-agent, then re-enable.
- If repeated connect timeouts occur, validate base image, cloud-init, and Jenkins URL/secret wiring.
- If retry exhaustion spikes, check Jenkins API and node-agent service health.
