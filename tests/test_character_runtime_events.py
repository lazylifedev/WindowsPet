from windows_pet.character_runtime_events import CharacterRuntimeEventDispatcher


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
