from windows_pet.process_inspection import normalize_process_query, resolve_process_candidate


def rows(*names):
    return [{"name": name, "pid": i + 1} for i, name in enumerate(names)]


def test_exact_canonical_candidate_is_found():
    snapshot, candidate, reason = resolve_process_candidate("notepad", rows("notepad"))
    assert snapshot.processes == {1: "notepad"} and candidate == (1, "notepad") and reason == "inspection_candidate_found"


def test_japanese_alias_reaches_canonical_candidate():
    assert normalize_process_query("メモ帳") == "notepad"
    _, candidate, reason = resolve_process_candidate("メモ帳", rows("notepad"))
    assert candidate == (1, "notepad") and reason == "inspection_candidate_found"


def test_case_and_nfkc_are_deterministic():
    _, candidate, reason = resolve_process_candidate(" ＮＯＴＥＰＡＤ ", rows("Notepad"))
    assert candidate == (1, "Notepad") and reason == "inspection_candidate_found"


def test_missing_and_ambiguous_are_not_selected():
    assert resolve_process_candidate("calc", rows("notepad"))[2] == "inspection_process_not_found"
    assert resolve_process_candidate("notepad", rows("notepad", "NOTEPAD"))[2] == "inspection_candidate_ambiguous"


def test_truncation_is_not_reported_as_not_found():
    _, candidate, reason = resolve_process_candidate("calc", rows("notepad"), truncated=True)
    assert candidate is None and reason == "inspection_result_truncated"
