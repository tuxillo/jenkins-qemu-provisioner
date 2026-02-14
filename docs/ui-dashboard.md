# Control-Plane UI Dashboard

Read-only dashboard served by control-plane at `/ui`.

## Scope (MVP)

- Single page dashboard.
- No client-side API calls.
- Data comes from a server-embedded JSON snapshot.
- Manual refresh and optional timed full-page reload.

## Snapshot Contract

`snapshot` JSON object embedded in HTML as:

```html
<script id="cp-snapshot" type="application/json">...</script>
```

Fields:

- `generated_at`: ISO-8601 UTC timestamp.
- `counts`:
  - `leases_total`
  - `hosts_total`
  - `events_total`
  - `leases_by_state` (map state -> count)
- `hosts`: list of host records:
  - `host_id`, `enabled`, `last_seen`
  - `cpu_total`, `cpu_free`, `ram_total_mb`, `ram_free_mb`, `io_pressure`
- `leases`: list of lease records:
  - `lease_id`, `vm_id`, `label`, `jenkins_node`, `state`, `host_id`
  - `created_at`, `updated_at`, `connect_deadline`, `ttl_deadline`, `last_error`
- `events`: latest event records:
  - `id`, `timestamp`, `lease_id`, `event_type`, `payload_json`
- `metrics`: in-memory counters from control-plane metrics endpoint.

## UI Sections

- Top summary cards (hosts, leases, recent events, hot states).
- Host health/capacity table.
- Lease table grouped by current state.
- Recent events stream.

## Performance and Safety

- Static CSS/JS from `/static`.
- Avoid heavy dependencies; use vanilla JS.
- Keep render path client-only from embedded snapshot data.
- No write actions in UI.
