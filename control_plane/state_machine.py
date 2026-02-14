from control_plane.models import LeaseState


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    LeaseState.REQUESTED.value: {
        LeaseState.PROVISIONING.value,
        LeaseState.FAILED.value,
    },
    LeaseState.PROVISIONING.value: {LeaseState.BOOTING.value, LeaseState.FAILED.value},
    LeaseState.BOOTING.value: {
        LeaseState.CONNECTED.value,
        LeaseState.TERMINATING.value,
        LeaseState.FAILED.value,
    },
    LeaseState.CONNECTED.value: {
        LeaseState.RUNNING.value,
        LeaseState.TERMINATING.value,
        LeaseState.FAILED.value,
    },
    LeaseState.RUNNING.value: {LeaseState.TERMINATING.value, LeaseState.FAILED.value},
    LeaseState.TERMINATING.value: {
        LeaseState.TERMINATED.value,
        LeaseState.FAILED.value,
    },
    LeaseState.TERMINATED.value: set(),
    LeaseState.FAILED.value: {
        LeaseState.TERMINATING.value,
        LeaseState.TERMINATED.value,
    },
    LeaseState.ORPHANED.value: {
        LeaseState.TERMINATING.value,
        LeaseState.TERMINATED.value,
    },
}


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, set())
