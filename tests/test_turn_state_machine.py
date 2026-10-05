import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@given(
    terminal=st.sampled_from(
        [
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "failed_before_execution",
            "cancelled_before_execution",
            "outcome_unknown",
            "recovery_required",
        ]
    ),
    active=st.sampled_from(
        ["queued", "waiting_for_memory", "assigned", "running", "finalizing"]
    ),
)
@settings(deadline=None)
def test_terminal_turns_cannot_return_to_active_state(terminal, active) -> None:
    from app.turns.state_machine import IllegalTurnTransition, TurnStateMachine

    with pytest.raises(IllegalTurnTransition):
        TurnStateMachine.require_transition(terminal, active)


def test_running_requires_execution_nonce() -> None:
    from app.turns.state_machine import IllegalTurnTransition, TurnStateMachine

    with pytest.raises(IllegalTurnTransition, match="execution nonce"):
        TurnStateMachine.require_transition("assigned", "running")

    TurnStateMachine.require_transition(
        "assigned", "running", execution_nonce="nonce-1"
    )


@pytest.mark.parametrize("current", ["queued", "assigned", "running"])
def test_completed_is_only_reachable_from_finalizing(current) -> None:
    from app.turns.state_machine import IllegalTurnTransition, TurnStateMachine

    with pytest.raises(IllegalTurnTransition):
        TurnStateMachine.require_transition(current, "completed")

    TurnStateMachine.require_transition("finalizing", "completed")
