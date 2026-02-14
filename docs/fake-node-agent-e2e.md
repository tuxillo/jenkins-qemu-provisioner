# Fake Node-Agent Local E2E Contract

This defines the fake node-agent used for local control-plane loop testing.

## Goal

- Allow local end-to-end scaler/provisioner/reconciler loops without real QEMU hosts.
- Keep behavior deterministic and read/write safe for development.

## API behavior

Fake node-agent exposes the same minimal API shape expected by control-plane:

- `PUT /v1/vms/{vm_id}`
  - Idempotently records VM as running.
  - Returns VM state object.
- `GET /v1/vms/{vm_id}`
  - Returns stored VM state.
- `DELETE /v1/vms/{vm_id}`
  - Idempotently removes VM record.
  - Returns terminated response with `deleted_overlay=true`.
- `GET /v1/vms`
  - Returns in-memory VM list.
- `GET /v1/capacity`
  - Returns static host capacity and selected accelerator.
- `GET /healthz`
  - Service liveness and host metadata.

## Host registration and heartbeat

- Fake service has a background worker:
  - registers host to control-plane with bootstrap token
  - sends periodic heartbeats with current running VM IDs
- Registration and heartbeat include platform capability fields:
  - `os_family`, `os_version`, `qemu_binary`, `supported_accels`, `selected_accel`

## Control-plane compatibility mode

- For local development only, control-plane can auto-create unknown hosts during register when env flag is enabled.
- This avoids manual DB seeding in compose-based local E2E tests.

## Non-goals

- No real VM launch, no QEMU process management, no overlay file operations.
- No production usage.
