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
- `NODE_AGENT_ADVERTISE_ADDR` (reachable from control-plane, e.g. `192.168.5.136:9000`)
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
sudo service jenkins_qemu_node_agent start
sudo service jenkins_qemu_node_agent status
```

The rc.d service wraps uvicorn with `/usr/sbin/daemon`, so it runs detached with
pidfile `/var/run/jenkins_qemu_node_agent/jenkins_qemu_node_agent.pid`.
Privilege drop is handled by `daemon -u jenkins-qemu-agent`.
Service status is tracked via the daemon wrapper process.

## 4) Validation

- Agent health: `curl http://<host>:9000/healthz`
- Capacity: `curl http://<host>:9000/v1/capacity`
- In control-plane UI (`/ui`), host should appear with matching platform (`family/flavor/arch`) and selected accelerator.

## 5) Manual base image customization

Boot the base image directly when you need to install packages or tune the guest:

```bash
./deploy/boot-base-image.sh --image /var/lib/jenkins-qemu/base/default.qcow2 --ssh-forward 2222
```

Then customize from console (or over SSH if enabled in the image), shut down the VM,
and keep using the same qcow2 as your base image.

Minimum guest requirements for automatic Jenkins inbound bootstrap:

- `cloud-init` enabled in the base image
- `bash` available in PATH (`/usr/local/bin/bash` on DragonFlyBSD)
- Java available in PATH (`java`)
- `curl` or `fetch` available to download `agent.jar`

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
- VMs fail to launch after lease creation
  - Verify base image exists at `NODE_AGENT_BASE_IMAGE_DIR/<base_image_id>.qcow2`.
  - Check node-agent logs for launch stage details (`cloud-init`, overlay, `qemu` command).
  - Verify `NODE_AGENT_ADVERTISE_ADDR` resolves from control-plane and matches node-agent bind/listen port.
  - For Jenkins bootstrap env, cloud-init writes `/usr/local/etc/jenkins-qemu/jenkins-agent.env` (fallback `/etc/jenkins-agent.env`).

- Need guest boot serial output for debugging
  - Per-VM serial console is written to `NODE_AGENT_CLOUD_INIT_DIR/<vm_id>/serial.log`.
  - Example: `tail -f /var/lib/jenkins-qemu/cloud-init/<vm_id>/serial.log`.
  - Cloud-init seed is attached as explicit CD-ROM (`cidata`) for NoCloud detection.
- VM launches fail on Linux
  - Validate KVM availability and QEMU permissions (`/dev/kvm`).
- VM launches fail on DragonFlyBSD
  - Validate NVMM support and `-accel nvmm` functionality.
- Install fails building `pydantic-core` with a `maturin`/Cargo `edition2024` error
  - Keep the default DragonFlyBSD installer constraint (`maturin>=1.9.4,<1.10`) for Cargo 1.79.
  - Long-term fix is upgrading Rust/Cargo so the constraint can be removed.
- Host register returns `422 Unprocessable Entity`
  - Ensure register payload values meet API minimums (for example `ram_total_mb >= 256`).
  - Check node-agent logs for response body details.
- Host register returns `401` or `404`
  - Verify `NODE_AGENT_HOST_ID` and `NODE_AGENT_BOOTSTRAP_TOKEN` against control-plane records.
  - In dev only, set `ALLOW_UNKNOWN_HOST_REGISTRATION=true` on control-plane.
- `daemon: ppidfile ... Permission denied` on DragonFlyBSD
  - Update to latest `deploy/rc.d/jenkins_qemu_node_agent` and recopy it to `/usr/local/etc/rc.d/`.
  - Restart service after updating the script.
- `daemon: process already running` but rc.d says `is not running`
  - Update to latest `deploy/rc.d/jenkins_qemu_node_agent` and restart service.
  - The script now tracks daemon pid correctly and removes stale pidfiles before start.
- Orphan overlays
  - Safety loop cleans unknown overlays in overlay directory; verify path settings.
