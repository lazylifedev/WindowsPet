from windows_pet.ui_automation import UIAControlNode, UIAInspectionCode, UIAutomationInspector, UIAWindowIdentity


def tree():
    return UIAControlNode("window", "root", "Settings", (
        UIAControlNode("button", "save", "Save"),
        UIAControlNode("password", "password", "secret-value"),
        UIAControlNode("button", "duplicate", "Same"),
        UIAControlNode("button", "duplicate", "Same"),
    ))


def test_bounded_tree_and_password_redaction():
    window = UIAWindowIdentity("hwnd-1", "Settings", 10)
    snapshot = UIAutomationInspector().inspect(window, tree(), max_depth=2, max_nodes=3)
    assert snapshot.node_count == 3 and snapshot.bounded
    password, code = UIAutomationInspector().find_control(snapshot, expected_window=window, automation_id="password")
    assert code is UIAInspectionCode.OK and password.name == "[REDACTED]"

    full = UIAutomationInspector().inspect(window, tree())
    password, code = UIAutomationInspector().find_control(full, expected_window=window, automation_id="password")
    assert code is UIAInspectionCode.OK and password.name == "[REDACTED]"


def test_stable_identity_and_ambiguous_control_rejection():
    inspector = UIAutomationInspector(); window = UIAWindowIdentity("hwnd-1", "Settings", 10); snapshot = inspector.inspect(window, tree())
    control, code = inspector.find_control(snapshot, expected_window=window, automation_id="save")
    assert code is UIAInspectionCode.OK and control.automation_id == "save"
    control, code = inspector.find_control(snapshot, expected_window=window, automation_id="duplicate")
    assert control is None and code is UIAInspectionCode.AMBIGUOUS
    control, code = inspector.find_control(snapshot, expected_window=UIAWindowIdentity("hwnd-2", "Settings", 10), automation_id="save")
    assert control is None and code is UIAInspectionCode.STALE_WINDOW
