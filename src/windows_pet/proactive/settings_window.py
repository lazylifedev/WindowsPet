from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout

from .models import ProactiveSettings


class ProactiveSettingsWindow(QDialog):
    """Small local-only settings UI; it does not store speech bodies."""

    def __init__(self, settings: ProactiveSettings, on_save, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自発発話設定")
        self.level = QComboBox(); self.level.addItems(["off", "low", "normal"])
        self.level.setCurrentText("off" if not settings.enabled else getattr(settings, "speech_level", "normal"))
        self.cooldown = QSpinBox(); self.cooldown.setRange(1, 1440); self.cooldown.setValue(settings.minimum_cooldown_minutes)
        self.cap = QSpinBox(); self.cap.setRange(0, 24); self.cap.setValue(settings.daily_cap)
        form = QFormLayout(); form.addRow(QLabel("自発発話"), self.level); form.addRow(QLabel("クールダウン（分）"), self.cooldown); form.addRow(QLabel("1日の上限"), self.cap)
        save = QPushButton("保存"); cancel = QPushButton("キャンセル"); buttons = QHBoxLayout(); buttons.addWidget(cancel); buttons.addWidget(save)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addLayout(buttons)
        save.clicked.connect(self._save); cancel.clicked.connect(self.reject); self._on_save = on_save; self._settings = settings

    def _save(self):
        level = self.level.currentText()
        self._on_save(ProactiveSettings(enabled=level != "off", speech_level=level,
                                         minimum_cooldown_minutes=self.cooldown.value(), daily_cap=self.cap.value(),
                                         quiet_start=self._settings.quiet_start, quiet_end=self._settings.quiet_end,
                                         suppress_during_focus=self._settings.suppress_during_focus,
                                         suppress_during_critical_operation=self._settings.suppress_during_critical_operation,
                                         recent_interaction_suppression_minutes=self._settings.recent_interaction_suppression_minutes))
        self.accept()
