from control_plane.models import LeaseState
from control_plane.state_machine import can_transition


def test_valid_transitions():
    assert can_transition(LeaseState.REQUESTED.value, LeaseState.PROVISIONING.value)
    assert can_transition(LeaseState.PROVISIONING.value, LeaseState.BOOTING.value)
    assert can_transition(LeaseState.RUNNING.value, LeaseState.TERMINATING.value)
    assert can_transition(LeaseState.TERMINATING.value, LeaseState.TERMINATED.value)


def test_invalid_transition_rejected():
    assert not can_transition(LeaseState.REQUESTED.value, LeaseState.RUNNING.value)


def test_idempotent_transition_allowed():
    assert can_transition(LeaseState.BOOTING.value, LeaseState.BOOTING.value)
