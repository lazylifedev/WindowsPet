from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UIAInspectionCode(StrEnum):
    OK = "ok"
    BOUNDED = "bounded"
    AMBIGUOUS = "ambiguous_control"
    STALE_WINDOW = "stale_window"
    NOT_FOUND = "control_not_found"


_SECRET_TYPES = {"password", "edit_password", "secret"}


@dataclass(frozen=True)
class UIAControlNode:
    control_type: str
    automation_id: str
    name: str
    children: tuple["UIAControlNode", ...] = ()
    enabled: bool = True

    def safe(self) -> "UIAControlNode":
        return UIAControlNode(self.control_type, self.automation_id, "[REDACTED]" if self.control_type.casefold() in _SECRET_TYPES else self.name, tuple(child.safe() for child in self.children), self.enabled)


@dataclass(frozen=True)
class UIAWindowIdentity:
    handle: str
    title: str
    process_id: int


@dataclass(frozen=True)
class UIASnapshot:
    window: UIAWindowIdentity
    root: UIAControlNode
    bounded: bool
    node_count: int


class UIAutomationInspector:
    """Read-only fake-friendly UIA foundation; no clicks, typing, or secret values."""

    def inspect(self, window: UIAWindowIdentity, root: UIAControlNode, *, max_depth: int = 5, max_nodes: int = 200) -> UIASnapshot:
        if max_depth < 0 or max_nodes < 1:
            raise ValueError("invalid_bounds")
        count = 0

        def visit(node, depth):
            nonlocal count
            if count >= max_nodes or depth > max_depth:
                return None
            count += 1
            children = tuple(child for item in node.children if (child := visit(item, depth + 1)) is not None)
            return UIAControlNode(node.control_type, node.automation_id, node.name, children, node.enabled).safe()

        safe_root = visit(root, 0)
        return UIASnapshot(window, safe_root, count < self._tree_size(root) or count >= max_nodes or self._depth(root) > max_depth, count)

    def find_control(self, snapshot: UIASnapshot, *, expected_window: UIAWindowIdentity, automation_id: str | None = None,
                     control_type: str | None = None, name: str | None = None) -> tuple[UIAControlNode | None, UIAInspectionCode]:
        if snapshot.window != expected_window:
            return None, UIAInspectionCode.STALE_WINDOW
        matches = []
        def visit(node):
            if ((automation_id is None or node.automation_id == automation_id) and
                    (control_type is None or node.control_type == control_type) and
                    (name is None or node.name == name)):
                matches.append(node)
            for child in node.children:
                visit(child)
        visit(snapshot.root)
        if not matches:
            return None, UIAInspectionCode.NOT_FOUND
        if len(matches) != 1:
            return None, UIAInspectionCode.AMBIGUOUS
        return matches[0], UIAInspectionCode.OK

    @staticmethod
    def _tree_size(node):
        return 1 + sum(UIAutomationInspector._tree_size(child) for child in node.children)

    @staticmethod
    def _depth(node):
        return 1 + max((UIAutomationInspector._depth(child) for child in node.children), default=0)
