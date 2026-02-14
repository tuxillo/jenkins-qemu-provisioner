# MVP Acceptance Checklist

- [ ] Queue item for a configured label triggers control-plane provisioning.
- [ ] Control-plane creates one Jenkins node (`executors=1`) per lease.
- [ ] One VM is launched per lease and receives only node name + JNLP secret.
- [ ] Agent connects inbound and runs one job only.
- [ ] Terminal job state (`SUCCESS`, `FAILURE`, `ABORTED`, `UNSTABLE`, `NOT_BUILT`) triggers immediate VM teardown and Jenkins node deletion.
- [ ] Connect deadline cleanup works for never-connected VMs.
- [ ] Disconnected grace cleanup works for unexpected disconnects.
- [ ] Hard TTL cleanup prevents leaked VMs.
- [ ] Host registration requires bootstrap token.
- [ ] Heartbeat requires valid non-expired session token.
- [ ] Disabled hosts receive no new placements.
- [ ] Reconcile loop fixes node-without-vm and vm-without-lease drift.
- [ ] Control-plane restart does not duplicate leases or leave permanent orphan resources.
- [ ] `GET /v1/leases` and manual terminate endpoint are functional.
- [ ] Metrics endpoint publishes core counters used for operations.
