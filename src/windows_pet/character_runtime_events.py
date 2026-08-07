"""Pure runtime policy for character animation events.

The dispatcher deliberately has no Qt dependency.  The window supplies the small
``start`` callback, which makes ordering and completion behaviour easy to test.
"""
from enum import IntEnum


class CharacterEventPriority(IntEnum):
    IDLE = 100
    INTERACTION = 200
    NOTICE = 300
    WORKING = 400
    CRITICAL = 500


PERSISTENT_EVENTS = frozenset({"idle", "sleep", "thinking", "working", "waiting_confirmation", "error", "speaking", "notification"})
TRANSIENT_EVENTS = frozenset({"wave", "single_click", "double_click", "right_click", "hover_long", "drag_start", "drag_end"})
RUNTIME_EVENTS = PERSISTENT_EVENTS | TRANSIENT_EVENTS

_PRIORITIES = {
    "idle": CharacterEventPriority.IDLE, "sleep": CharacterEventPriority.IDLE,
    "wave": CharacterEventPriority.INTERACTION, "single_click": CharacterEventPriority.INTERACTION,
    "double_click": CharacterEventPriority.INTERACTION, "right_click": CharacterEventPriority.INTERACTION,
    "hover_long": CharacterEventPriority.INTERACTION, "drag_start": CharacterEventPriority.INTERACTION,
    "drag_end": CharacterEventPriority.INTERACTION, "speaking": CharacterEventPriority.NOTICE,
    "notification": CharacterEventPriority.NOTICE, "working": CharacterEventPriority.WORKING,
    "thinking": CharacterEventPriority.WORKING, "waiting_confirmation": CharacterEventPriority.CRITICAL,
    "error": CharacterEventPriority.CRITICAL,
}


class CharacterRuntimeEventDispatcher:
    def __init__(self, animations, start):
        self.animations = animations
        self._start = start
        self.desired_state = "idle"
        self.current_event = None
        self.current_kind = None
        self.generation = 0

    @staticmethod
    def priority_for(event_id):
        return _PRIORITIES.get(event_id)

    def _available(self, event_id):
        return event_id in self.animations

    def _begin(self, event_id, kind):
        self.generation += 1
        self.current_event, self.current_kind = event_id, kind
        self._start(event_id, self.generation)
        return True

    def set_state(self, event_id):
        if event_id not in PERSISTENT_EVENTS or not self._available(event_id):
            return False
        self.desired_state = event_id
        if self.current_kind == "transient":
            if _PRIORITIES[event_id] > _PRIORITIES[self.current_event]:
                return self._begin(event_id, "state")
            return True
        if self.current_event == event_id:
            return True
        # Persistent states are explicit state-machine transitions, not requests
        # competing with one another by priority.
        return self._begin(event_id, "state")

    def trigger(self, event_id):
        if event_id not in TRANSIENT_EVENTS or not self._available(event_id):
            return False
        if self.current_event is not None and _PRIORITIES[event_id] < _PRIORITIES[self.current_event]:
            return False
        return self._begin(event_id, "transient")

    def animation_completed(self, event_id, generation):
        if event_id != self.current_event or generation != self.generation:
            return False
        if self.current_kind != "transient":
            return False
        return self._begin(self.desired_state, "state") if self._available(self.desired_state) else False
