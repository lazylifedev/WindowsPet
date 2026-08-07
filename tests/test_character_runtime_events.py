from windows_pet.character_runtime_events import CharacterRuntimeEventDispatcher
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QMouseEvent

from windows_pet.character_package_loader import load_builtin_default_character
from windows_pet.main import PetWindow


def _dispatcher(events=("idle", "thinking", "wave", "single_click", "double_click", "hover_long")):
    started = []
    return CharacterRuntimeEventDispatcher({event: object() for event in events}, lambda event, generation: started.append((event, generation))), started


def test_transient_completion_restores_desired_state_and_ignores_stale_completion():
    dispatcher, started = _dispatcher()
    assert dispatcher.set_state("idle")
    assert dispatcher.trigger("single_click")
    click_generation = dispatcher.generation
    assert dispatcher.set_state("thinking")  # higher priority preempts a click
    assert not dispatcher.animation_completed("single_click", click_generation)
    assert dispatcher.current_event == "thinking"
    assert dispatcher.set_state("idle")
    assert dispatcher.trigger("double_click")
    assert dispatcher.animation_completed("double_click", dispatcher.generation)
    assert dispatcher.current_event == "idle"


def test_priority_latest_equal_transient_and_missing_or_unknown_events_are_safe():
    dispatcher, started = _dispatcher(("idle", "single_click", "double_click"))
    assert dispatcher.set_state("idle")
    assert dispatcher.trigger("single_click")
    assert dispatcher.trigger("double_click")
    assert dispatcher.current_event == "double_click"
    assert not dispatcher.trigger("hover_long")
    assert not dispatcher.trigger("not_an_event")
    assert not dispatcher.set_state("not_a_state")


def test_higher_persistent_state_preempts_transient_but_lower_state_waits():
    dispatcher, started = _dispatcher()
    dispatcher.set_state("idle"); dispatcher.trigger("single_click")
    assert dispatcher.set_state("thinking")
    assert dispatcher.current_event == "thinking"
    assert not dispatcher.trigger("hover_long")


def test_stale_frame_callback_cannot_advance_preempting_animation(tmp_path, qapp):
    pet = PetWindow(load_builtin_default_character(Path("assets/animations")).animations, tmp_path / "position.json", quit_callback=lambda: None)
    assert pet.play("wave")
    stale_callback = pet._frame_timeout_callback
    assert pet.play("thinking")
    before = (pet._animation.event_id, pet._frame, pet.dispatcher.current_event, pet.dispatcher.generation)
    stale_callback()
    assert (pet._animation.event_id, pet._frame, pet.dispatcher.current_event, pet.dispatcher.generation) == before
    pet.close()


def test_once_completion_uses_start_generation_and_stale_completion_is_noop(tmp_path, qapp):
    pet = PetWindow(load_builtin_default_character(Path("assets/animations")).animations, tmp_path / "position.json", quit_callback=lambda: None)
    assert pet.play("wave")
    callback = pet._frame_timeout_callback
    pet.play("thinking")
    callback()
    assert pet.dispatcher.current_event == "thinking"
    pet.play("wave")
    pet._frame = len(pet._animation.frames) - 1
    pet._next_frame(pet._animation_generation)
    assert pet.dispatcher.current_event == pet.dispatcher.desired_state == "thinking"
    pet.close()


def test_hover_lifecycle_stops_for_right_click_and_resets_after_drag(tmp_path, qapp):
    pet = PetWindow(load_builtin_default_character(Path("assets/animations")).animations, tmp_path / "position.json", quit_callback=lambda: None)
    pet._pet_hovered = True; pet._hover_timer.start(2000)
    point = QPointF(20, 20)
    pet.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, point, point, point, Qt.RightButton, Qt.RightButton, Qt.NoModifier))
    assert not pet._hover_timer.isActive()
    pet.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, point, point, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    moved = QPointF(40, 20)
    pet.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, moved, moved, moved, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    pet.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, moved, moved, moved, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert not pet._dragged
    pet.leaveEvent(QEvent(QEvent.Type.Leave)); pet.enterEvent(QEnterEvent(point, point, point))
    assert pet._hover_timer.isActive()
    pet.close()
