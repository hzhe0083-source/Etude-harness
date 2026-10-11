"""The single write path: lock, load current state, admit commands, replace ledger atomically."""

from . import handoff, store
from .errors import RuleViolation
from .rules import admit
from .state import apply

SYSTEM_ACTOR = {"type": "system", "id": "research-cli"}


def handoff_ids(root):
    return handoff.issued_ids(root)


def load_state(root):
    return store.load_state(root)


class Writer:
    """Builds events for one agent and appends them atomically under the store lock."""

    def __init__(self, root, agent, role=None):
        self.root = root
        self.agent = agent
        self.role = role

    def event(self, state, etype, payload, subject=None, actor=None, recorded_by=None, role=None):
        """A new event; `subject=None` means the event creates a record whose id is the event_id."""
        event_id = store.new_ulid(after=state.last_event_id)
        return {
            "event_id": event_id, "type": etype, "ts": store.now_ts(),
            "actor": actor or self.agent, "recorded_by": recorded_by or self.agent,
            "role": role or self.role, "subject": subject or event_id, "payload": payload,
        }

    def transact(self, build):
        """build(state) -> [event]; every event is admitted against the state it lands on, then saved atomically."""
        with store.locked(self.root):
            state = load_state(self.root)
            events = build(state)
            _admit_all(state, events, handoff_ids(self.root))
            store.save_state(self.root, state)
        return events


def _admit_all(state, events, hids):
    """Admit each event against the state left by the previous one; refuse the batch on any violation."""
    for event in events:
        violations = admit(state, event, hids)
        if violations:
            raise RuleViolation(violations)
        apply(state, event)
