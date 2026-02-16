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

### Launch failure attribution

- Check recent scale diagnostics and launch errors:
  - `sqlite3 control_plane.db "select id,event_type,payload_json from events where event_type like 'scale.%' or event_type like 'lease.%' order by id desc limit 100;"`
- `scale.launch_failed` payload includes `host_id`, `node_agent_url`, and transport error details (`error_type`, `error_detail`, `request_url`).
- In dashboard `/ui`, inspect **Recent Events** `Details` column for failure stage and endpoint hints.

### Node-agent interruption behavior

- If control-plane cannot reach node-agent during teardown, lease state is kept in `TERMINATING` and retried on future reconcile cycles.
- Event `lease.terminate_retry` records retry reason and latest delete error.
- Control-plane only marks lease `TERMINATED` after node-agent delete succeeds.
