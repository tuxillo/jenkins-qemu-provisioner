# Jenkins QEMU Provisioner

Lightweight control plane for running Jenkins builds on ephemeral QEMU VMs.

## Index

- [What this project does](#what-this-project-does)
- [Core components](#core-components)
- [Quick start (local)](#quick-start-local)
- [Control-plane development](#control-plane-development)
- [Design and ops docs](#design-and-ops-docs)
- [DragonFly jail helper](#dragonfly-jail-helper)
- [Task tracking](#task-tracking)

## What this project does

- Jenkins is the scheduler only.
- One queued job maps to one disposable VM (`1 job = 1 VM = 1 node`).
- VM lifecycle is ephemeral: boot -> run job -> terminate -> delete overlay disk.
- Control-plane and host node-agent logic are owned in this repo.

## Core components

- `control_plane/`: FastAPI + SQLite control-plane (scaler, provisioner, reconciler, GC).
- `docker-compose.yml`: local Jenkins + control-plane development stack.
- `jenkins/`: Jenkins bootstrap scripts for local admin/security initialization.
- `docs/`: architecture/design and operations notes.

## Quick start (local)

1. Copy environment file:

```bash
cp .env.example .env
```

2. Start Jenkins + control-plane:

```bash
docker compose up -d --build
```

3. Verify services:

- Jenkins: `http://localhost:8080`
- Control-plane health: `http://localhost:8000/healthz`
- Control-plane metrics: `http://localhost:8000/metrics`
- Control-plane UI: `http://localhost:8000/ui`
- Fake node-agent health (when enabled): `http://localhost:9000/healthz`

UI note:
- `/ui` is read-only and renders from a server-embedded snapshot.
- It does not query control-plane APIs from the browser.
- Host capacity in the UI distinguishes physical totals from allocatable VM budgets.

Guest image policy note:
- Control-plane resolves Jenkins labels through exact-label JSON policy in `control_plane/label_policies.json`.
- Policy keys must match the queued Jenkins label exactly, including full label expressions when Jenkins submits one.
- The image catalog in `control_plane/image_catalog.json` defines the desired artifact, source kind (`manual_local` or `remote_cache`), and cache policy (`require_warm` or `prefer_warm`).
- Node-agents advertise locally cached images, and the scheduler prefers warm caches before falling back to permitted cold fetches.
- `GUEST_IMAGE_COMPAT_MODE=true` temporarily restores legacy unmapped-label fallback during rollout, but the intended steady state is explicit exact-label policy for every schedulable label.

Local E2E note:
- `fake-node-agent` is disabled by default; enable with profile: `docker compose --profile fake-agent up -d`.
- Control-plane loops are enabled by default and can point to fake node-agent via `CP_NODE_AGENT_URL`.
- Jenkins seeds a fake pipeline job (`fake-ephemeral-test`) with a `10s` timeout by default to exercise create/teardown paths quickly.
- Jenkins image includes `antisamy-markup-formatter` so bootstrap can render a clickable Control-plane dashboard button in system message.
- Jenkins bootstrap injects a Control-plane UI link (`JENKINS_CONTROL_PLANE_UI_URL`) in the Jenkins home message area.
- Control-plane connect deadline is set to `180s` by default in local compose (`CP_CONNECT_DEADLINE_SEC`) to allow slower boots before recycle.
- Ephemeral agents default to Jenkins WebSocket transport (`CP_JENKINS_AGENT_TRANSPORT=websocket`), so local dev does not require exposing port `50000`.
- Jenkins inbound TCP listener is disabled by default in dev (`JENKINS_AGENT_TCP_PORT=-1`).
- Inbound agent JVM temp dir defaults to `/var/tmp` (`CP_JENKINS_AGENT_TMPDIR`) to avoid `/tmp` tmpfs disk-monitor false trips.
- If Jenkins was already initialized, set `JENKINS_BOOTSTRAP_JOB_UPSERT=true` or recreate the `jenkins_home` volume to re-seed job config.
- If you change `JENKINS_CONTROL_PLANE_UI_URL`, restart Jenkins to refresh the injected UI link.

## Control-plane development

```bash
make install
make init-db
make run
make test
```

## Design and ops docs

- High-level design: `docs/jenkins-ephemeral-qemu-design.md`
- Operations notes: `docs/control-plane-operations.md`
- MVP checklist: `docs/mvp-acceptance-checklist.md`
- UI contract and scope: `docs/ui-dashboard.md`
- Node-agent contract: `docs/node-agent-contract.md`
- Node-agent runbook: `docs/node-agent-runbook.md`
- Fake local E2E contract: `docs/fake-node-agent-e2e.md`

## DragonFly jail helper

- `scripts/manage-dfly-jail.sh` manages DragonFly jails backed by HAMMER2 PFSes and published `world` artifacts.
- It is intended to run on a DragonFly host as root.
- It supports `create`, `destroy`, `start`, `stop`, `status`, `list`, `verify`, and `rebuild-network` subcommands.
- `create` requires `/build/jails` or another chosen parent path to live on a mounted HAMMER2 filesystem.
- Jail names must use only letters, numbers, and underscores. The name `network` is reserved by the manager.
- Managed jail roots are mounted via `/etc/fstab.<name>`, while host jail configuration is written to `/etc/rc.conf`.
- It supports two network modes:
  - `private-loopback` (default): shared private subnet on `lo1`
  - `interface-alias`: jail service IP is added directly to a chosen host interface
- It regenerates manager-owned alias configuration in `/etc/rc.conf` for all managed jails.
- By default it caches downloaded world artifacts in `/var/cache/dfly-jails` and keeps the latest three verified artifacts.
- It can optionally bootstrap `pkg` plus install packages inside the prepared jail root during `create`.
- Before mutating `/etc/rc.conf` or a managed `/etc/fstab.<name>`, it stores timestamped backups in `/var/backups/dfly-jail-manager/`.

Prerequisites:

- Run it on a DragonFly BSD host as `root`.
- Ensure the jail parent path, by default `/build/jails`, is on a mounted HAMMER2 filesystem.
- Ensure the host has `/usr/local/bin/bash` installed, since the manager script itself is Bash.
- Run the script directly or with Bash, not with `sh`.
- Ensure the host can reach `https://avalon.dragonflybsd.org/snapshots/x86_64/assets/releases/` unless you override the release URL or use a populated cache.

What `create` does:

- Discovers the newest `DragonFly-x86_64-*.world.tar.gz` artifact.
- Reuses a verified cached copy from `/var/cache/dfly-jails` when possible.
- Creates a HAMMER2 PFS for the jail and mounts it at `/build/jails/<name>` by default.
- Extracts the world tarball into the jail root.
- Writes jail-local `etc/rc.conf` and `etc/resolv.conf`.
- Writes host jail configuration to `/etc/rc.conf`.
- Writes the jail root mount to `/etc/fstab.<name>`.
- Ensures the required jail network aliases exist live on the host interfaces selected by the jail's network mode.
- Refuses loopback or service IPs that are already configured on the host.
- Optionally bootstraps `pkg` and installs packages inside the jail root.

Basic workflow:

```bash
sudo ./scripts/manage-dfly-jail.sh create --name web01 --bootstrap-pkg --packages "bash curl tmux"
sudo service jail start web01
sudo ./scripts/manage-dfly-jail.sh status --name web01
sudo ./scripts/manage-dfly-jail.sh verify
sudo ./scripts/manage-dfly-jail.sh stop --name web01
sudo ./scripts/manage-dfly-jail.sh destroy --name web01
```

Interface-alias workflow:

```bash
sudo ./scripts/manage-dfly-jail.sh create \
  --name web02 \
  --network-mode interface-alias \
  --service-iface re0 \
  --service-ip 192.168.5.50
sudo service jail start web02
sudo ./scripts/manage-dfly-jail.sh status --name web02
```

Useful commands:

```bash
sudo ./scripts/manage-dfly-jail.sh list
sudo ./scripts/manage-dfly-jail.sh create --dry-run --name web01
sudo ./scripts/manage-dfly-jail.sh start --name web01
sudo ./scripts/manage-dfly-jail.sh stop --name web01
sudo ./scripts/manage-dfly-jail.sh status --name web01
sudo jls
sudo ./scripts/manage-dfly-jail.sh verify
sudo ./scripts/manage-dfly-jail.sh rebuild-network
```

Default network model:

- Each jail gets two IPs:
  - a jail-local loopback address in `127.0.0.0/8`, allocated from the `127.0.0.0/24` pool by default
  - a private service address in `10.200.0.0/24`
- The host uses:
  - `lo0` for jail-local loopback aliases like `127.0.0.2`
  - `lo1` for the shared private jail subnet, with `10.200.0.1/24` as the host-side address by default
- The manager writes a dedicated `dfly-jail-manager:network` block into `/etc/rc.conf` to own those aliases.
- If you override `--private-iface`, that per-jail interface choice is preserved in the manager-owned metadata and used again by `start`, `destroy`, and `rebuild-network`.
- Host-local traffic between the host and jails should work without PF. Internet access from the jail requires host NAT/firewall configuration.

Alternative `interface-alias` mode:

- Still uses `lo0` for the jail-local `127.0.0.X` address.
- Adds the service IP directly to a real host interface such as `re0`.
- Requires both:
  - `--service-iface`
  - `--service-ip`
- Does not auto-allocate the service IP. You must choose an address that is valid for that interface and subnet.
- Does not require PF/NAT just to reach the LAN, though host firewall policy still applies.

Expected `rc.conf` network block:

```sh
# BEGIN dfly-jail-manager:network
cloned_interfaces="${cloned_interfaces:+${cloned_interfaces} }lo1"
ifconfig_lo1="inet 10.200.0.1 netmask 0xffffff00"
ifconfig_lo0_alias0="inet 127.0.0.2 netmask 0xff000000"
ifconfig_lo1_alias0="inet 10.200.0.2 netmask 0xffffff00"
# END dfly-jail-manager:network
```

Verification after `create` and `service jail start <name>`:

```bash
sudo ./scripts/manage-dfly-jail.sh status --name web01
ifconfig lo0
ifconfig lo1
jls
jexec 7 ifconfig
jexec 7 ping -c 1 10.200.0.1
jexec 7 ping -c 1 127.0.0.1
```

The JID in the examples above is just an example. Use `jls` to find the actual JID.

Minimal PF example for outbound jail Internet access:

- This example assumes:
  - the host uplink interface is `re0`
  - jail traffic uses `10.200.0.0/24`
  - you want outbound NAT only, not inbound port forwards yet
- The host in our test setup had its default route on `re0`.

Example `/etc/pf.conf`:

```pf
ext_if = "re0"
jail_net = "10.200.0.0/24"

set skip on { lo0 lo1 }

nat on $ext_if inet from $jail_net to any -> ($ext_if)

pass quick on lo1 from $jail_net to 10.200.0.1 keep state
pass out all keep state
```

Then enable PF on the host:

```sh
printf '\npf_enable="YES"\n' >> /etc/rc.conf
service pf start
pfctl -sr
pfctl -sn
```

After PF/NAT is enabled, test from inside the jail:

```bash
jexec 7 ping -c 1 10.200.0.1
jexec 7 ping -c 1 1.1.1.1
jexec 7 fetch -qo - https://avalon.dragonflybsd.org/
jexec 7 pkg update
```

Notes:

- `service jail` on DragonFly does not support a `status` subcommand. Use `jls` and `./scripts/manage-dfly-jail.sh status --name <name>` instead.
- The manager no longer relies on `jail_<name>_interface`, because DragonFly `rc.d/jail` cannot correctly alias a comma-separated dual-IP jail definition.
- `interface-alias` mode requires an explicit `--service-ip`; this is deliberate so the script does not guess addresses on a real network.
- `create` refuses to reuse an IP address that is already configured on the host, and `destroy` only removes addresses that are actually host aliases.
- The PF example above is intentionally minimal. Add explicit `rdr` rules later if you want inbound host or LAN traffic forwarded to a jail service.
- The shared artifact cache is not per-jail. Destroying a jail does not clear `/var/cache/dfly-jails`.
- Use `verify` to check that manager-owned jail state in `/etc/rc.conf` is internally consistent. Use `rebuild-network` to regenerate only the `dfly-jail-manager:network` block from parsed jail metadata.

## Task tracking

This repository uses `bd` (beads) for task tracking. See `AGENTS.md` for workflow requirements.
