"""Transaction state machine. The single source of truth for valid transitions.

    CREATED ──► AUTHORIZED ──► EXECUTING ──► COMPLETED ──► DISPUTED ──► RECOVERY_PENDING ──► REFUNDED
       │  └──► AWAITING_APPROVAL ─┘   │            │
       └──► BLOCKED ◄─────────────────┘            └──► FAILED
"""
from app.domain.enums import TxState as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.AUTHORIZED, S.AWAITING_APPROVAL, S.BLOCKED}),
    S.AWAITING_APPROVAL: frozenset({S.AUTHORIZED, S.BLOCKED}),
    S.AUTHORIZED: frozenset({S.EXECUTING, S.FAILED}),
    S.EXECUTING: frozenset({S.COMPLETED, S.FAILED}),
    S.COMPLETED: frozenset({S.DISPUTED}),
    S.DISPUTED: frozenset({S.RECOVERY_PENDING}),
    S.RECOVERY_PENDING: frozenset({S.REFUNDED}),
    S.BLOCKED: frozenset(),
    S.FAILED: frozenset(),
    S.REFUNDED: frozenset(),
}

TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


class InvalidTransition(Exception):
    def __init__(self, current: S, target: S):
        super().__init__(f"invalid transition {current} -> {target}")
        self.current, self.target = current, target


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def is_reachable(current: S, target: S) -> bool:
    """True if `target` can still be reached from `current` (used to defer out-of-order events)."""
    seen, frontier = {current}, [current]
    while frontier:
        for nxt in TRANSITIONS[frontier.pop()]:
            if nxt == target:
                return True
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False
