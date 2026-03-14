# Node-Agent Runbook

This runbook covers install, configuration, service management, and troubleshooting
for the host node-agent on Linux (KVM) and DragonFlyBSD (NVMM).

## 1) Install

From repo root:

```bash
./deploy/install-node-agent.sh
```

On DragonFlyBSD, the installer applies `deploy/constraints-dragonfly.txt` by default
to keep `maturin` compatible with older Cargo toolchains. Override with
`NODE_AGENT_PIP_CONSTRAINT=/path/to/constraints.txt` if needed.

Then edit `/etc/jenkins-qemu-node-agent/env`:

- `NODE_AGENT_HOST_ID`
- `NODE_AGENT_BOOTSTRAP_TOKEN`
- `NODE_AGENT_CONTROL_PLANE_URL`
- `NODE_AGENT_BIND_HOST` (defaults to `0.0.0.0`)
- `NODE_AGENT_BIND_PORT` (defaults to `9000`)
- `NODE_AGENT_ADVERTISE_ADDR` (reachable from control-plane, e.g. `192.168.5.136:9000`)
- `NODE_AGENT_ALLOCATABLE_VCPU` (optional explicit vCPU budget for managed VMs)
- `NODE_AGENT_ALLOCATABLE_RAM_MB` (optional explicit RAM budget for managed VMs)
- `NODE_AGENT_HOST_STATS_INTERVAL_SEC` (optional host stats sampling interval; defaults to `2`)
- `NODE_AGENT_NETWORK_BACKEND` (`bridge`, `tap`, or `user`)
- `NODE_AGENT_NETWORK_INTERFACE` (required for `bridge`/`tap`)

OS family/flavor, CPU architecture, and accelerator support are auto-detected at
runtime and reported to control-plane; they are no longer configured in env.

`NODE_AGENT_HOST_ID` and `NODE_AGENT_BOOTSTRAP_TOKEN` must match a host record in
the control-plane (or run control-plane with unknown host registration enabled in
dev mode).

DragonFlyBSD defaults to `NODE_AGENT_NETWORK_BACKEND=user` to avoid bridge setup
requirements during initial bring-up. Switch to `bridge`/`tap` only after host
network interfaces are provisioned.

`NODE_AGENT_BIND_HOST` / `NODE_AGENT_BIND_PORT` control what address the service
listens on. `NODE_AGENT_ADVERTISE_ADDR` is the host:port the control-plane calls;
it should usually use the same port and a routable address for that host.

Base image cache model:

- Control-plane resolves Jenkins labels through exact-label policy and sends node-agent
  an explicit `guest_image` plus `base_image` selection.
- Node-agent treats `NODE_AGENT_BASE_IMAGE_DIR` as a local cache of immutable qcow2
  artifacts.
- Cached artifacts live at `NODE_AGENT_BASE_IMAGE_DIR/<base_image_id>.qcow2`.
- Cached metadata lives at `NODE_AGENT_BASE_IMAGE_DIR/<base_image_id>.json`.
- Hosts advertise cached images back to control-plane as `available_images`, and the
  scheduler prefers warm caches before falling back to permitted cold fetch.

Source modes:

- `manual_local`
  - operator places the qcow2 in the base image dir ahead of time
  - node-agent fails the launch if the artifact is missing
- `remote_cache`
  - node-agent downloads the artifact on demand, verifies digest, and caches it locally
  - first boot on a cold host may take longer

Capacity model:

- `cpu_total` / `ram_total_mb` remain the physical host totals reported for visibility.
- `NODE_AGENT_ALLOCATABLE_VCPU` / `NODE_AGENT_ALLOCATABLE_RAM_MB` define the VM pool budget used for scheduling.
- If allocatable values are unset, node-agent falls back to the physical totals for backward compatibility.
- Control-plane schedules from `cpu_free` / `ram_free_mb`, which are computed as allocatable budget minus active managed VM reservations.

Example shared host budget:

```bash
NODE_AGENT_ALLOCATABLE_VCPU=8
NODE_AGENT_ALLOCATABLE_RAM_MB=16384
```

This means a 16-core / 64 GiB host can reserve half its resources for the OS and
other workloads while still advertising the full physical machine size in the UI.

## 2) Linux service management (systemd)

```bash
sudo cp deploy/systemd/jenkins-qemu-node-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jenkins-qemu-node-agent
sudo systemctl status jenkins-qemu-node-agent
```

## 3) DragonFlyBSD service management (rc.d)

```bash
sudo cp deploy/rc.d/jenkins_qemu_node_agent /usr/local/etc/rc.d/
sudo chmod +x /usr/local/etc/rc.d/jenkins_qemu_node_agent
echo 'jenkins_qemu_node_agent_enable="YES"' | sudo tee -a /etc/rc.conf
echo 'jenkins_qemu_node_agent_logfile="/var/log/jenkins-qemu-node-agent.log"' | sudo tee -a /etc/rc.conf
sudo service jenkins_qemu_node_agent start
sudo service jenkins_qemu_node_agent status
```

The rc.d service wraps the Python node-agent entrypoint with `/usr/sbin/daemon`, so it runs detached with
pidfile `/var/run/jenkins_qemu_node_agent/jenkins_qemu_node_agent.pid`.
Privilege drop is handled by `daemon -u jenkins-qemu-agent`.
The script tracks the daemon wrapper PID (`daemon -P`), so `start/stop/status`
operate consistently.
By default, node-agent stdout/stderr is appended to
`/var/log/jenkins-qemu-node-agent.log` via `daemon -o`; override that path with
`jenkins_qemu_node_agent_logfile` in `/etc/rc.conf`.
On DragonFlyBSD, rotate this logfile with a service restart.

## 4) Validation

- Agent health: `curl http://<host>:<NODE_AGENT_BIND_PORT>/healthz`
- Capacity: `curl http://<host>:<NODE_AGENT_BIND_PORT>/v1/capacity`
- Service log: `tail -f /var/log/jenkins-qemu-node-agent.log`
- In control-plane UI (`/ui`), host should appear with matching platform (`family/flavor/arch`), selected accelerator, physical totals, allocatable totals, and current free schedulable capacity.
- `/v1/capacity` now also includes optional cached host-stat diagnostics such as `stats_collected_at`, `disk_busy_frac`, `disk_read_mb_s`, and `disk_write_mb_s` when the active platform backend provides them.

## 5) Manual base image customization

Boot the base image directly when you need to install packages or tune the guest:

```bash
./deploy/boot-base-image.sh --image /var/lib/jenkins-qemu/base/default.qcow2 --ssh-forward 2222
```

Then customize from console (or over SSH if enabled in the image), shut down the VM,
and keep using the same qcow2 as your base image.

If you are using `manual_local` image catalog entries, also write a matching metadata
sidecar so the host advertises the cache entry accurately. Minimal example:

```json
{
  "guest_image": "default",
  "base_image_id": "default",
  "source_kind": "manual_local",
  "source_url": null,
  "source_digest": null,
  "format": "qcow2",
  "cpu_arch": "x86_64"
}
```

Minimum guest requirements for automatic Jenkins inbound bootstrap:

- `cloud-init` enabled in the base image
- `bash` available in PATH (`/usr/local/bin/bash` on DragonFlyBSD)
- Java available in PATH (`java`)
- `curl` or `fetch` available to download `agent.jar`

Transport notes:

- Control-plane defaults to Jenkins WebSocket agent transport (`JENKINS_AGENT_TRANSPORT=websocket`).
- In WebSocket mode, the guest only needs reachability to Jenkins HTTP(S) URL/port.
- Classic inbound TCP (`JENKINS_AGENT_TRANSPORT=tcp`) requires Jenkins agent listener reachability (typically port `50000`).

Artifact cleanup notes:

- Node-agent removes per-VM overlays, cloud-init ISO files, and runtime directories under `NODE_AGENT_CLOUD_INIT_DIR` on termination paths.
- Periodic safety cleanup also prunes orphan overlay files and orphan runtime directories.
- Optional `NODE_AGENT_DEBUG_ARTIFACT_RETENTION_SEC` can retain artifacts for postmortem (default `0` for immediate cleanup).

DragonFlyBSD cloud-init datasource guard (required on some images):

- If cloud-init crashes during datasource import (for example `DataSourceAzure` +
  Python `crypt` traceback), pin datasource selection to NoCloud in the base image.
- Inside the customization VM, run as root:

```bash
./deploy/apply-cloud-init-nocloud-fix.sh
reboot
```

- The script writes `/etc/cloud/cloud.cfg.d/99-datasource-nocloud.cfg` with:
  - `datasource_list: [ NoCloud, None ]`
- After reboot, verify:
  - `cloud-init status --long`
  - `grep -i datasource /var/log/cloud-init.log`

Useful flags:

- `--accel auto|kvm|nvmm|tcg`
- `--network user|bridge|tap`
- `--network-if <iface>` for `bridge`/`tap`
- `--headless`
- `--dry-run`

## 6) Token rotation

1. Rotate bootstrap token in control-plane host record.
2. Update `/etc/jenkins-qemu-node-agent/env` with new token.
3. Restart agent service.

## 7) Troubleshooting

- `selected_accel not supported by host`
  - Verify host runtime supports hardware accel; node-agent will auto-fallback to `tcg` when needed.
- Host not schedulable
  - Verify heartbeat reaches control-plane and host is `enabled=true`.
  - Verify host free capacity is non-zero in control-plane (`cpu_free`, `ram_free_mb`).
  - Verify allocatable budget is large enough for the requested VM profile (`cpu_allocatable`, `ram_allocatable_mb`).
- VMs fail to launch after lease creation
  - Check node-agent service log (`/var/log/jenkins-qemu-node-agent.log`) for launch stage details (`cloud-init`, overlay, `qemu` command).
  - Verify base image exists at `NODE_AGENT_BASE_IMAGE_DIR/<base_image_id>.qcow2`.
  - For `remote_cache` images, verify the configured source URL is reachable and the catalog digest matches the downloaded artifact.
  - Verify `NODE_AGENT_ADVERTISE_ADDR` resolves from control-plane and matches node-agent bind/listen port.
  - For Jenkins bootstrap env, cloud-init writes `/usr/local/etc/jenkins-qemu/jenkins-agent.env` (fallback `/etc/jenkins-agent.env`).

- Need guest boot serial output for debugging
  - Per-VM serial console is written to `NODE_AGENT_CLOUD_INIT_DIR/<vm_id>/serial.log`.
  - Example: `tail -f /var/lib/jenkins-qemu/cloud-init/<vm_id>/serial.log`.
  - Cloud-init seed is attached as explicit CD-ROM (`cidata`) for NoCloud detection.

- Need one-shot VM bootstrap diagnostics without guest SSH
  - Use node-agent debug endpoint:
    - `curl http://<host>:<NODE_AGENT_BIND_PORT>/v1/vms/<vm_id>/debug`
  - Response includes `serial_tail`, generated `user_data`, sanitized `jenkins_env`, and QEMU `launch_command`.
  - Bootstrap stage markers appear as `BOOTSTRAP_STAGE=...` lines in `serial_tail`.
- VM launches fail on Linux
  - Validate KVM availability and QEMU permissions (`/dev/kvm`).
- VM launches fail on DragonFlyBSD
  - Validate NVMM support and `-accel nvmm` functionality.
- Install fails building `pydantic-core` with a `maturin`/Cargo `edition2024` error
  - Keep the default DragonFlyBSD installer constraint (`maturin>=1.9.4,<1.10`) for Cargo 1.79.
  - Long-term fix is upgrading Rust/Cargo so the constraint can be removed.
- Host register returns `422 Unprocessable Entity`
  - Ensure register payload values meet API minimums (for example `ram_total_mb >= 256`).
  - Check node-agent service log (`/var/log/jenkins-qemu-node-agent.log`) for response body details.
- Host register returns `401` or `404`
  - Verify `NODE_AGENT_HOST_ID` and `NODE_AGENT_BOOTSTRAP_TOKEN` against control-plane records.
  - In dev only, set `ALLOW_UNKNOWN_HOST_REGISTRATION=true` on control-plane.
- `daemon: ppidfile ... Permission denied` on DragonFlyBSD
  - Update to latest `deploy/rc.d/jenkins_qemu_node_agent` and recopy it to `/usr/local/etc/rc.d/`.
  - Ensure `/var/run/jenkins_qemu_node_agent` is writable by `jenkins-qemu-agent`.
- `daemon: process already running` but rc.d says `is not running`
  - Update to latest `deploy/rc.d/jenkins_qemu_node_agent` and restart service.
  - The script now uses daemon wrapper pid tracking and clears stale pidfiles on start/stop.
- Orphan overlays
  - Safety loop cleans unknown overlays in overlay directory; verify path settings.
