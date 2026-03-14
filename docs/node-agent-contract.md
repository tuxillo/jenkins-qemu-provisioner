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
- `cpu_total`, `ram_total_mb`: physical host totals for visibility
- `cpu_allocatable`, `ram_allocatable_mb`: schedulable VM pool budget
- `cpu_free`, `ram_free_mb`: current free capacity inside the allocatable VM pool
- `io_pressure`: normalized `0.0..1.0` host storage pressure hint for soft scheduling preference

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
- `allocatable_vcpu`, `allocatable_ram_mb`
- `host_stats_interval_sec`
- `heartbeat_interval_sec`
- `ttl_check_interval_sec`
- `reconcile_interval_sec`
- `dry_run` (do not execute QEMU; used for dev/tests)

## Node-Agent API

- `PUT /v1/vms/{vm_id}`: ensure VM exists and is running (idempotent)
- `GET /v1/vms/{vm_id}`: VM state
- `DELETE /v1/vms/{vm_id}`: ensure VM terminated + overlay cleaned
- `GET /v1/vms`: list VM records for reconciliation
- `GET /v1/capacity`: report physical totals, allocatable totals, free schedulable CPU/RAM, and IO pressure
- `GET /healthz`: agent liveness

### Host Stats Contract

Node-agent host stats collection is platform-specific internally, but emits a generic
contract externally.

- Heartbeat continues to send only generic scheduler-facing metrics; today that means
  `io_pressure` plus the allocatable/free capacity fields above.
- `GET /v1/capacity` may additionally expose optional host-diagnostic fields:
  - `stats_collected_at`: timestamp of the last cached stats sample
  - `disk_busy_frac`: normalized `0.0..1.0` busy fraction for the VM-storage device set
  - `disk_read_mb_s`: sampled read throughput
  - `disk_write_mb_s`: sampled write throughput
- Diagnostic fields are additive and may be `null` when the active platform backend does
  not provide them yet.
- Platform-native raw counters must remain internal to node-agent and must not leak into
  the control-plane API contract.
- Current backends:
  - Linux derives disk throughput and busy time from `/proc/diskstats` for the filesystem
    devices backing node-agent storage paths.
  - DragonFlyBSD derives disk throughput and busy time from `kern.devstat.all`, matching
    the same underlying `busy_time` accounting used by `systat vmstat`.

## Service Management

- Linux uses `systemd` unit
- DragonFlyBSD uses `rc.d` script

Core agent config and API remain the same across both platforms.
