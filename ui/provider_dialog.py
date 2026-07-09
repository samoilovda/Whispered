"""
Whispered – Cloud AI Provider Dialog
Small dialog for configuring the optional cloud provider (OpenAI-compatible
or Anthropic) used by the YouTube key-questions feature. Feature-scoped —
does not touch the main settings dialog or any other AI functionality.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QPushButton, QDialogButtonBox, QWidget, QLabel,
)

from config import get_config, save_config
from core.i18n import tr
from core.logger import get_logger

logger = get_logger(__name__)


class ProviderDialog(QDialog):
    """Configure the cloud AI provider used by the YouTube tab."""

    def __init__(self, kind: str = "openai", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("provider_dialog_title"))
        self._setup_ui()
        self._load_values(kind)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._kind_combo = QComboBox()
        self._kind_combo.addItem(tr("provider_openai"), "openai")
        self._kind_combo.addItem(tr("provider_anthropic"), "anthropic")
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        layout.addWidget(self._kind_combo)

        # OpenAI-compatible fields
        self._openai_form = QWidget()
        openai_layout = QFormLayout(self._openai_form)
        self._openai_url_edit = QLineEdit()
        openai_layout.addRow(tr("provider_base_url"), self._openai_url_edit)
        self._openai_key_edit, openai_key_row = self._make_password_row()
        openai_layout.addRow(tr("provider_api_key"), openai_key_row)
        self._openai_model_edit = QLineEdit()
        openai_layout.addRow(tr("provider_model"), self._openai_model_edit)
        layout.addWidget(self._openai_form)

        # Anthropic fields
        self._anthropic_form = QWidget()
        anthropic_layout = QFormLayout(self._anthropic_form)
        self._anthropic_key_edit, anthropic_key_row = self._make_password_row()
        anthropic_layout.addRow(tr("provider_api_key"), anthropic_key_row)
        self._anthropic_model_edit = QLineEdit()
        anthropic_layout.addRow(tr("provider_model"), self._anthropic_model_edit)
        layout.addWidget(self._anthropic_form)

        notice = QLabel(tr("youtube_privacy_notice"))
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(notice)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_password_row(self):
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(edit, stretch=1)
        show_btn = QPushButton(tr("provider_show_key"))
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda checked: edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        row.addWidget(show_btn)
        container = QWidget()
        container.setLayout(row)
        return edit, container

    def _on_kind_changed(self):
        kind = self._kind_combo.currentData()
        self._openai_form.setVisible(kind == "openai")
        self._anthropic_form.setVisible(kind == "anthropic")

    def _load_values(self, kind: str):
        cfg = get_config()
        idx = self._kind_combo.findData(kind)
        self._kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_kind_changed()

        self._openai_url_edit.setText(cfg.yt_openai_base_url)
        self._openai_key_edit.setText(cfg.yt_openai_api_key)
        self._openai_model_edit.setText(cfg.yt_openai_model)

        self._anthropic_key_edit.setText(cfg.yt_anthropic_api_key)
        self._anthropic_model_edit.setText(cfg.yt_anthropic_model)

    def _on_accept(self):
        cfg = get_config()
        cfg.yt_provider = self._kind_combo.currentData() or "openai"
        cfg.yt_openai_base_url = self._openai_url_edit.text().strip() or cfg.yt_openai_base_url
        cfg.yt_openai_api_key = self._openai_key_edit.text().strip()
        cfg.yt_openai_model = self._openai_model_edit.text().strip() or cfg.yt_openai_model
        cfg.yt_anthropic_api_key = self._anthropic_key_edit.text().strip()
        cfg.yt_anthropic_model = self._anthropic_model_edit.text().strip() or cfg.yt_anthropic_model
        save_config()
        self.accept()
