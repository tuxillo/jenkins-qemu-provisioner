# Node-Agent Contract (Linux + DragonFlyBSD)

This document defines the runtime and API contract for the host node-agent.

## Platform and Capability Fields

Host registration and heartbeat payloads include:

- `os_family`: `linux` or `dragonflybsd`
- `os_version`: host OS version string
- `qemu_version`: runtime QEMU version string
- `qemu_binary`: absolute qemu binary path used by agent
- `supported_accels`: list of supported accelerators (for example `kvm`, `nvmm`, `tcg`)
- `selected_accel`: accelerator selected in agent config and used for VM launches

Rules:

- Linux default selected accelerator: `kvm`
- DragonFlyBSD default selected accelerator: `nvmm`
- If selected accelerator is not in supported list, host is unschedulable

## Node-Agent Runtime Configuration

Required config fields:

- `host_id`
- `bootstrap_token`
- `control_plane_url`
- `bind_host`, `bind_port`
- `state_db_path`
- `base_image_dir`, `overlay_dir`, `cloud_init_dir`
- `os_family`, `os_version`
- `qemu_binary`, `qemu_accel`, `qemu_machine`, `qemu_cpu`
- `network_backend`, `network_interface`
- `disk_interface`
- `service_manager` (`systemd` or `rcd`)

Optional fields:

- `node_agent_auth_token`
- `heartbeat_interval_sec`
- `ttl_check_interval_sec`
- `reconcile_interval_sec`
- `dry_run` (do not execute QEMU; used for dev/tests)

## Node-Agent API

- `PUT /v1/vms/{vm_id}`: ensure VM exists and is running (idempotent)
- `GET /v1/vms/{vm_id}`: VM state
- `DELETE /v1/vms/{vm_id}`: ensure VM terminated + overlay cleaned
- `GET /v1/vms`: list VM records for reconciliation
- `GET /v1/capacity`: report host free CPU/RAM and IO pressure
- `GET /healthz`: agent liveness

## Service Management

- Linux uses `systemd` unit
- DragonFlyBSD uses `rc.d` script

Core agent config and API remain the same across both platforms.
