ACTIVE_TURN_STATES = (
    "queued",
    "waiting_for_memory",
    "assigned",
    "running",
    "finalizing",
)

TERMINAL_TURN_STATES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "failed_before_execution",
        "cancelled_before_execution",
        "outcome_unknown",
        "recovery_required",
    }
)

_ALLOWED_TRANSITIONS = {
    "queued": {
        "waiting_for_memory",
        "assigned",
        "running",
        "failed_before_execution",
        "cancelled_before_execution",
        "interrupted",
    },
    "waiting_for_memory": {
        "queued",
        "assigned",
        "running",
        "failed_before_execution",
        "cancelled_before_execution",
        "interrupted",
    },
    "assigned": {
        "running",
        "failed_before_execution",
        "cancelled_before_execution",
        "interrupted",
    },
    "running": {
        "finalizing",
        "failed",
        "cancelled",
        "interrupted",
        "outcome_unknown",
        "recovery_required",
    },
    "finalizing": {
        "completed",
        "failed",
        "cancelled",
        "outcome_unknown",
        "recovery_required",
    },
}


class IllegalTurnTransition(ValueError):
    pass


class TurnStateMachine:
    @staticmethod
    def require_transition(
        current: str,
        next_status: str,
        *,
        execution_nonce: str | None = None,
    ) -> None:
        if next_status not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise IllegalTurnTransition(
                f"Turn cannot transition from {current} to {next_status}"
            )
        if next_status == "running" and not execution_nonce:
            raise IllegalTurnTransition(
                "Transition to running requires an execution nonce"
            )
