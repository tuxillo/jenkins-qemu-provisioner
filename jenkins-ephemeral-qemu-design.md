# Jenkins Ephemeral QEMU VM Provisioning Design (Anti-Bullshit)

## Scope and Constraints

- Jenkins is scheduler/controller only.
- Each queued job gets one ephemeral QEMU VM (`1 job = 1 VM = 1 Jenkins node`).
- No Kubernetes, no cloud autoscalers, no managed runners, no Jenkins plugin as primary control plane.
- We own scaler/provisioner, host runtime, and VM lifecycle.
- VM lifecycle: create overlay disk -> boot -> inbound agent connects -> run job -> power off -> delete overlay.
- Must survive Jenkins/provisioner restarts.
- Hard TTL is required to kill leaks.

## Core Components

- Jenkins controller: queue, job assignment, build state, node records.
- External scaler/provisioner: computes desired capacity, creates/deletes Jenkins nodes, drives node-agent API, reconciles drift.
- Node agent (per host): creates qcow2 overlay, boots/stops QEMU, reports VM states, enforces local TTL watchdog.
- Ephemeral VM: cloud-init bootstrap, launches inbound Jenkins agent with pre-issued credentials, disposable filesystem.

## Architecture Decisions

- Scheduling authority
  - Scaler is the only component that decides how many VMs to launch.
  - Node agent executes launch/terminate requests only.
  - VM is not trusted to decide lifecycle, only to run workload.

- Identity mapping
  - Stable mapping per lease: `lease_id`, `vm_id`, `jenkins_node_name`, `label`, deadlines.
  - Jenkins node has `executors=1`.
  - Lease is persisted in scaler DB for restart recovery.

- Agent auth and registration (selected)
  - Pre-create Jenkins node plus inbound secret via Jenkins API.
  - VM gets only node name and one node secret via cloud-init.
  - No self-registration from VM.
  - Why: least privilege, deterministic cleanup, no broad Jenkins API permissions in VM, easy orphan handling.

- Lifecycle ownership
  - Scaler owns desired state and termination decisions.
  - Node agent owns process teardown and overlay deletion.
  - VM may run local watchdog for `lease_expires_at` as fallback safety.

- Reconciliation model
  - Every loop compares:
    1) Jenkins queue/running/node states
    2) scaler lease DB
    3) node-agent live VM inventory
  - Any mismatch is drift and is corrected immediately.

## Scaler Control Loop (Pseudocode)

```pseudo
loop every 5s:
  j = jenkins_snapshot()    # queue, running builds, node states
  a = agent_snapshot_all()  # host vm inventories
  s = scaler_state()        # persisted leases

  # 1) Reconcile drift
  reconcile(j, a, s):
    - if Jenkins node exists but VM missing -> delete Jenkins node
    - if VM exists but no valid lease -> terminate VM
    - if lease expired -> terminate VM + delete node
    - if disconnected too long -> terminate VM + delete node

  # 2) Compute launches per label
  for label in configured_labels:
    queued       = count_buildable_queue(j, label)
    inflight     = count_leases(s, label, states=[PROVISIONING, BOOTING, CONNECTING])
    ready_unused = count_connected_idle_nodes(j, label)  # should be ~0 by design

    raw_deficit = queued - inflight - ready_unused
    if raw_deficit <= 0: continue
    if cooldown_active(label): continue
    if inflight >= label.max_inflight: continue

    launchable = min(
      raw_deficit,
      label.max_burst_per_tick,
      label.max_total - active_vms(label),
      global.max_total - active_vms_global(),
      schedulable_host_slots(label)
    )
    launchable = max(launchable, 0)

    repeat launchable times:
      lease = create_lease(label, connect_deadline, absolute_vm_ttl)

      node = jenkins_create_node(
        name=lease.node_name,
        label=label,
        executors=1,
        mode=exclusive
      )
      secret = jenkins_get_inbound_secret(node)

      ok = node_agent_put_vm(
        vm_id=lease.vm_id,
        label=label,
        base_image_id=label.base_image_id,
        jenkins_node_name=node.name,
        jnlp_secret=secret,
        deadlines={connect_deadline, lease_expires_at}
      )

      if ok:
        persist_lease_state(lease, BOOTING)
      else:
        jenkins_delete_node(node.name)
        mark_lease_failed(lease)

    if launchable > 0:
      set_cooldown(label, now + label.cooldown_sec)

  # 3) GC checks
  for lease in s:
    if now > lease.connect_deadline and !jenkins_node_connected(lease.node_name):
      terminate_vm_and_delete_node(lease, reason="never_connected")
    if lease.disconnected_grace_exceeded:
      terminate_vm_and_delete_node(lease, reason="unexpected_disconnect")
    if now > lease.absolute_vm_ttl:
      terminate_vm_and_delete_node(lease, reason="ttl_expired")
```

## Capacity and Anti-Thrash Rules

- Per-label desired launches
  - `raw_deficit = queued - inflight - ready_unused`
  - `target_new = clamp(raw_deficit, 0, launch_limit)`
- `launch_limit` is the minimum of:
  - `label.max_burst_per_tick`
  - `label.remaining_cap`
  - `global.remaining_cap`
  - `host_schedulable_slots`
- Control limits
  - Per-label cooldown after non-zero launch (for example 10 to 20 seconds).
  - Per-label `max_inflight` boot cap.
  - Global launch rate limit (N VMs per time window).
- Reconcile loop can terminate overprovisioned/orphaned capacity quickly.

## Failure Handling Matrix

| Failure case | Detection | Action |
|---|---|---|
| VM booted but never connected | `now > connect_deadline` and Jenkins node offline | Terminate VM, delete overlay, delete Jenkins node, release lease |
| Agent disconnects unexpectedly mid-build | Jenkins node offline and running lease exceeds disconnected grace | Terminate VM, delete node; rely on Jenkins retry policy for job recovery |
| Jenkins node exists but VM is gone | Node present in Jenkins, missing from node-agent inventory | Delete stale Jenkins node; reprovision only if queue still requires capacity |
| VM exists but scaler lost state after restart | Node-agent reports unknown `vm_id` | Attempt adopt by metadata/lease tags; if no valid match, terminate orphan |
| Scaler crashes during provisioning | Scaler heartbeat absent while node-agent VMs still run | Node-agent watchdog enforces TTL cleanup; scaler reconciles on restart |
| Host crash or reboot | Host heartbeat and inventory missing | Mark leases unknown; after grace, delete Jenkins nodes and reprovision |

## Storage Design and qcow2 Pitfalls

- Chain depth
  - Always `overlay -> immutable base` (depth 1 only).
  - Never overlay-on-overlay.

- Base image lifecycle
  - Version base images using `base_image_id`.
  - Launch new jobs only on current base ID.
  - Drain and retire old base only after active overlays reach zero.

- Performance guardrails
  - Prefer fast local SSD or NVMe for overlays.
  - Use QEMU I/O settings suitable for CI workloads (for example `cache=none` and virtio-backed disk).
  - Feed per-host IO pressure into placement (`host_schedulable_slots`).

- Rot prevention
  - Delete overlay immediately on VM termination.
  - Sweep leaked overlays by lease metadata.
  - Run periodic image and filesystem checks (for example `qemu-img check`).

- Operational policy
  - Roll base images explicitly (blue/green base IDs), never mutate in place.

## Minimal Scaler to Node-Agent API (Idempotent)

- `PUT /v1/vms/{vm_id}`
  - Purpose: ensure VM exists and is running for a lease.
  - Request fields:
    - `vm_id`, `label`, `base_image_id`, `overlay_path`
    - `vcpu`, `ram_mb`, `disk_gb`
    - `lease_expires_at`, `connect_deadline`
    - `jenkins_url`, `jenkins_node_name`, `jnlp_secret`
    - `cloud_init_user_data_b64`, `metadata`
  - Response fields:
    - `vm_id`, `state` (`PROVISIONING|BOOTING|RUNNING|FAILED`)
    - `host_id`, `qemu_pid`, `created_at`

- `GET /v1/vms/{vm_id}`
  - Purpose: fetch current VM state.
  - Response fields: `vm_id`, `state`, `last_transition_at`, `ip`, `reason`, `lease_expires_at`

- `DELETE /v1/vms/{vm_id}`
  - Purpose: ensure VM is terminated and overlay is deleted.
  - Request/query fields: `reason`, `force`
  - Response fields: `vm_id`, `state=TERMINATED`, `deleted_overlay`

- `GET /v1/vms?label=&state=&host_id=`
  - Purpose: list VMs for reconciliation.
  - Response fields per item: `vm_id`, `label`, `state`, `lease_expires_at`, `jenkins_node_name`, `host_id`

- `GET /v1/capacity`
  - Purpose: return schedulable host capacity.
  - Response fields per host: `cpu_free`, `ram_free_mb`, `max_new_vms_per_min`, `io_pressure`, `schedulable`

## Suggested Initial Defaults

- Reconcile loop interval: `5s`
- Per-label cooldown: `15s`
- Per-label max inflight boots: `5`
- Per-label burst per tick: `3`
- Connect deadline: `4m`
- Disconnected grace: `60s` (or 2x Jenkins heartbeat interval)
- Absolute VM TTL: `2x` expected max job duration with a hard upper cap
- Global launch rate: tune by host fleet, for example `20 VMs / 10s`
