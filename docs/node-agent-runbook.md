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
- `NODE_AGENT_OS_FAMILY` (`linux` or `dragonflybsd`)
- `NODE_AGENT_QEMU_ACCEL` (`kvm` for linux, `nvmm` for dragonflybsd)

`NODE_AGENT_HOST_ID` and `NODE_AGENT_BOOTSTRAP_TOKEN` must match a host record in
the control-plane (or run control-plane with unknown host registration enabled in
dev mode).

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
pidfile `/var/run/jenkins_qemu_node_agent.pid`.

## 4) Validation

- Agent health: `curl http://<host>:9000/healthz`
- Capacity: `curl http://<host>:9000/v1/capacity`
- In control-plane UI (`/ui`), host should appear with matching `os_family` and selected accelerator.

## 5) Token rotation

1. Rotate bootstrap token in control-plane host record.
2. Update `/etc/jenkins-qemu-node-agent/env` with new token.
3. Restart agent service.

## 6) Troubleshooting

- `selected_accel not supported by host`
  - Ensure `NODE_AGENT_QEMU_ACCEL` matches runtime support and OS family.
- Host not schedulable
  - Verify heartbeat reaches control-plane and host is `enabled=true`.
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
- Orphan overlays
  - Safety loop cleans unknown overlays in overlay directory; verify path settings.
