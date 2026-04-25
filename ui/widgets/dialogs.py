from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.model_profiles import ALLOWED_PROVIDERS, generate_profile_id, normalize_profiles_payload, sanitize_profile_id
from ui.theme import TEXT_MUTED, TEXT_PRIMARY
from .foundation import CollapsibleSection, _fa_icon, _make_mono_font


class ModelSettingsDialog(QDialog):
    profiles_saved = Signal(object)

    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModelSettingsDialog")
        self.setWindowTitle("Model Profiles")
        self.setModal(True)
        self.resize(1020, 620)
        self.setMinimumSize(900, 540)

        normalized = normalize_profiles_payload(payload or {})
        self._profiles: list[dict[str, Any]] = [dict(item) for item in normalized.get("profiles", [])]
        self._active_profile = str(normalized.get("active_profile") or "").strip()
        self._name_manual_flags: list[bool] = []
        self._selected_row = -1
        self._loading_form = False
        self._filter_text = ""
        self._result_payload = normalized
        self._name_manual_flags = self._compute_initial_name_manual_flags()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        hero_card = QFrame()
        hero_card.setObjectName("ModelSettingsHeroCard")
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_layout.setSpacing(16)

        hero_copy = QVBoxLayout()
        hero_copy.setContentsMargins(0, 0, 0, 0)
        hero_copy.setSpacing(6)

        header_title = QLabel("Model Profiles")
        header_title.setObjectName("ModelSettingsTitle")
        hero_copy.addWidget(header_title, 0, Qt.AlignLeft | Qt.AlignTop)

        header_hint = QLabel(
            "Keep providers tidy, switch the active profile for new runs, and tune image support without losing your place."
        )
        header_hint.setObjectName("ModelSettingsSubtitle")
        header_hint.setWordWrap(True)
        hero_copy.addWidget(header_hint)

        active_name = str(self._active_profile or "").strip() or "none"
        self.active_profile_label = QLabel(f"Active now: {active_name}")
        self.active_profile_label.setObjectName("ModelSettingsMeta")
        self.active_profile_label.setWordWrap(True)
        hero_copy.addWidget(self.active_profile_label)
        hero_layout.addLayout(hero_copy, 1)

        hero_stats = QVBoxLayout()
        hero_stats.setContentsMargins(0, 0, 0, 0)
        hero_stats.setSpacing(8)

        self.profile_count_chip = QLabel("")
        self.profile_count_chip.setObjectName("ModelSettingsChip")
        hero_stats.addWidget(self.profile_count_chip, 0, Qt.AlignRight)

        self.left_meta_chip = QLabel("")
        self.left_meta_chip.setObjectName("ModelSettingsChip")
        hero_stats.addWidget(self.left_meta_chip, 0, Qt.AlignRight)
        hero_stats.addStretch(1)
        hero_layout.addLayout(hero_stats, 0)
        root.addWidget(hero_card)

        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(8)

        left_container = QFrame()
        left_container.setObjectName("ModelSettingsPane")
        left = QVBoxLayout(left_container)
        left.setContentsMargins(12, 12, 12, 12)
        left.setSpacing(8)

        left_header = QHBoxLayout()
        left_header.setContentsMargins(0, 0, 0, 0)
        left_header.setSpacing(6)
        left_label = QLabel("Library")
        left_label.setObjectName("SectionTitle")
        left_header.addWidget(left_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        left_header.addStretch(1)
        left.addLayout(left_header)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("ModelSettingsSearchField")
        self.search_edit.setPlaceholderText("Search by name, provider, or model")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("Profile search")
        self.search_edit.setAccessibleDescription("Filter profiles by name, provider, or model")
        self.search_edit.addAction(_fa_icon("fa5s.search", color=TEXT_MUTED, size=12), QLineEdit.LeadingPosition)
        left.addWidget(self.search_edit)

        self.profile_list = QListWidget()
        self.profile_list.setObjectName("ModelProfileList")
        self.profile_list.setMinimumWidth(280)
        self.profile_list.setAccessibleName("Profile list")
        self.profile_list.setAccessibleDescription("Select a model profile to edit")
        self.profile_list.currentRowChanged.connect(self._on_selection_changed)
        left.addWidget(self.profile_list, 1)

        left_buttons = QHBoxLayout()
        left_buttons.setSpacing(6)
        self.add_button = QPushButton("New Profile")
        self.add_button.setObjectName("SettingsAddButton")
        self.add_button.setIcon(_fa_icon("fa5s.plus", color=TEXT_PRIMARY, size=11))
        self.delete_button = QPushButton("Remove")
        self.delete_button.setObjectName("SettingsDeleteButton")
        self.delete_button.setIcon(_fa_icon("fa5s.trash", color="#F08F8F", size=11))
        left_buttons.addWidget(self.add_button)
        left_buttons.addWidget(self.delete_button)
        left.addLayout(left_buttons)

        left_hint = QLabel("Tip: leave Name empty and it will be generated from the model automatically.")
        left_hint.setObjectName("ModelSettingsMeta")
        left_hint.setWordWrap(True)
        left.addWidget(left_hint)

        right_container = QFrame()
        right_container.setObjectName("ModelSettingsPane")
        right = QVBoxLayout(right_container)
        right.setContentsMargins(12, 12, 12, 12)
        right.setSpacing(10)

        right_header = QHBoxLayout()
        right_header.setContentsMargins(0, 0, 0, 0)
        right_header.setSpacing(6)
        right_label = QLabel("Editor")
        right_label.setObjectName("SectionTitle")
        right_header.addWidget(right_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        self.profile_state_chip = QLabel("No profile selected")
        self.profile_state_chip.setObjectName("ModelSettingsChip")
        right_header.addWidget(self.profile_state_chip, 0, Qt.AlignLeft | Qt.AlignVCenter)
        right_header.addStretch(1)

        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setObjectName("ModelSettingsInlineButton")
        self.duplicate_button.setIcon(_fa_icon("fa5s.copy", color=TEXT_PRIMARY, size=11))
        self.duplicate_button.setEnabled(False)
        right_header.addWidget(self.duplicate_button, 0, Qt.AlignRight | Qt.AlignVCenter)
        right.addLayout(right_header)

        self.form_hint = QLabel("Select a profile to review credentials, model settings, and image support.")
        self.form_hint.setObjectName("ModelSettingsMeta")
        self.form_hint.setWordWrap(True)
        right.addWidget(self.form_hint)

        self.save_state_label = QLabel("")
        self.save_state_label.setObjectName("ModelSettingsMeta")
        self.save_state_label.setWordWrap(True)
        self.save_state_label.setVisible(False)
        right.addWidget(self.save_state_label)

        self.summary_card = QFrame()
        self.summary_card.setObjectName("ModelSettingsSummaryCard")
        summary_layout = QHBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(8)

        self.summary_provider = QLabel("Provider: —")
        self.summary_provider.setObjectName("ModelSettingsSummaryLabel")
        self.summary_model = QLabel("Model: —")
        self.summary_model.setObjectName("ModelSettingsSummaryLabel")
        self.summary_images = QLabel("Image input: off")
        self.summary_images.setObjectName("ModelSettingsSummaryLabel")
        summary_layout.addWidget(self.summary_provider)
        summary_layout.addWidget(self.summary_model, 1)
        summary_layout.addWidget(self.summary_images)
        right.addWidget(self.summary_card)

        editor_scroll = QScrollArea()
        editor_scroll.setObjectName("ModelSettingsScrollArea")
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QFrame.NoFrame)

        editor_content = QWidget()
        editor_content.setObjectName("ModelSettingsEditorContent")
        editor_layout = QVBoxLayout(editor_content)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)

        form_frame = QFrame()
        form_frame.setObjectName("ModelSettingsFormCard")
        form_layout = QFormLayout(form_frame)
        form_layout.setContentsMargins(10, 10, 10, 10)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(8)
        form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form_layout.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignTop)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("profile-id")
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.setAccessibleName("Profile name")

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["openai", "gemini"])
        self.provider_combo.setAccessibleName("Provider")

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("e.g. openai/gpt-oss-120b or gemini-1.5-flash")
        self.model_edit.setClearButtonEnabled(True)
        self.model_edit.setAccessibleName("Model")

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("API key")
        self.api_key_edit.setAccessibleName("API key")

        self.api_key_reveal_button = QToolButton()
        self.api_key_reveal_button.setObjectName("ModelSettingsInlineToolButton")
        self.api_key_reveal_button.setIcon(_fa_icon("fa5s.eye", color=TEXT_MUTED, size=12))
        self.api_key_reveal_button.setToolTip("Show or hide API key")
        self.api_key_reveal_button.setAccessibleName("Toggle API key visibility")

        self.api_key_copy_button = QToolButton()
        self.api_key_copy_button.setObjectName("ModelSettingsInlineToolButton")
        self.api_key_copy_button.setIcon(_fa_icon("fa5s.copy", color=TEXT_MUTED, size=12))
        self.api_key_copy_button.setToolTip("Copy API key")
        self.api_key_copy_button.setAccessibleName("Copy API key")

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.base_url_edit.setClearButtonEnabled(True)
        self.base_url_edit.setAccessibleName("Base URL")

        self.supports_images_checkbox = QCheckBox("Image input support")
        self.supports_images_checkbox.setObjectName("ModelSupportsImagesCheckbox")
        self.supports_images_checkbox.setToolTip("Allow image attachments for this profile.")
        self.supports_images_checkbox.setAccessibleName("Image input support")

        self.images_hint_label = QLabel("Enables sending images with prompts when the selected model supports vision input.")
        self.images_hint_label.setObjectName("ModelSettingsHintText")
        self.images_hint_label.setWordWrap(True)

        api_key_row = QWidget()
        api_key_row.setObjectName("ModelSettingsFieldRow")
        api_key_layout = QHBoxLayout(api_key_row)
        api_key_layout.setContentsMargins(0, 0, 0, 0)
        api_key_layout.setSpacing(6)
        api_key_layout.addWidget(self.api_key_edit, 1)
        api_key_layout.addWidget(self.api_key_reveal_button, 0, Qt.AlignVCenter)
        api_key_layout.addWidget(self.api_key_copy_button, 0, Qt.AlignVCenter)

        images_row = QWidget()
        images_layout = QVBoxLayout(images_row)
        images_layout.setContentsMargins(0, 2, 0, 2)
        images_layout.setSpacing(4)
        images_layout.addWidget(self.supports_images_checkbox)
        images_layout.addWidget(self.images_hint_label)

        label_width = 84
        name_label = QLabel("&Name")
        provider_label = QLabel("&Provider")
        model_label = QLabel("&Model")
        api_key_label = QLabel("&API Key")
        base_url_label = QLabel("Base &URL")
        images_label = QLabel("I&mages")
        for label in (name_label, provider_label, model_label, api_key_label, base_url_label, images_label):
            label.setObjectName("ModelSettingsFieldLabel")
            label.setFixedWidth(label_width)

        name_label.setBuddy(self.name_edit)
        provider_label.setBuddy(self.provider_combo)
        model_label.setBuddy(self.model_edit)
        api_key_label.setBuddy(self.api_key_edit)
        base_url_label.setBuddy(self.base_url_edit)
        images_label.setBuddy(self.supports_images_checkbox)

        form_layout.addRow(name_label, self.name_edit)
        form_layout.addRow(provider_label, self.provider_combo)
        form_layout.addRow(model_label, self.model_edit)
        form_layout.addRow(api_key_label, api_key_row)
        form_layout.addRow(base_url_label, self.base_url_edit)
        form_layout.addRow(images_label, images_row)
        editor_layout.addWidget(form_frame)

        helper_card = QFrame()
        helper_card.setObjectName("ModelSettingsHelperCard")
        helper_layout = QVBoxLayout(helper_card)
        helper_layout.setContentsMargins(12, 10, 12, 10)
        helper_layout.setSpacing(4)
        helper_title = QLabel("Editing notes")
        helper_title.setObjectName("ModelSettingsHelperTitle")
        helper_body = QLabel(
            "Only enabled profiles can become active. Toggling a model keeps your current place in the list and preserves the editor state."
        )
        helper_body.setObjectName("ModelSettingsHintText")
        helper_body.setWordWrap(True)
        helper_layout.addWidget(helper_title)
        helper_layout.addWidget(helper_body)
        editor_layout.addWidget(helper_card)
        editor_layout.addStretch(1)

        editor_scroll.setWidget(editor_content)
        right.addWidget(editor_scroll, 1)

        left_container.setMinimumWidth(320)
        right_container.setMinimumWidth(430)
        self.body_splitter.addWidget(left_container)
        self.body_splitter.addWidget(right_container)
        self.body_splitter.setSizes([330, 580])
        root.addWidget(self.body_splitter, 1)

        actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        actions.setObjectName("ModelSettingsActions")
        self.save_button = actions.button(QDialogButtonBox.StandardButton.Save)
        if self.save_button is not None:
            self.save_button.setObjectName("PrimaryButton")
            self.save_button.setIcon(_fa_icon("fa5s.save", color="#FFFFFF", size=11))
            self.save_button.setMinimumHeight(30)
        self.close_button = actions.button(QDialogButtonBox.StandardButton.Close)
        if self.close_button is not None:
            self.close_button.setMinimumHeight(30)
        root.addWidget(actions)

        self.add_button.clicked.connect(self._add_profile)
        self.delete_button.clicked.connect(self._delete_selected_profile)
        self.duplicate_button.clicked.connect(self._duplicate_selected_profile)
        self.search_edit.textChanged.connect(self._apply_profile_filter)
        if self.save_button is not None:
            self.save_button.clicked.connect(self._save_and_accept)
        if self.close_button is not None:
            self.close_button.clicked.connect(self.reject)

        self.name_edit.textEdited.connect(self._on_name_edited)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        self.model_edit.textChanged.connect(self._on_model_changed)
        self.api_key_edit.textChanged.connect(self._on_form_changed)
        self.api_key_reveal_button.clicked.connect(self._toggle_api_key_visibility)
        self.api_key_copy_button.clicked.connect(self._copy_api_key)
        self.base_url_edit.textChanged.connect(self._on_form_changed)
        self.supports_images_checkbox.checkStateChanged.connect(self._on_form_changed)

        self._refresh_profile_list()
        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(self._preferred_row_for_open())
        else:
            self._set_form_enabled(False)
        self._refresh_profile_counts()

    def result_payload(self) -> dict[str, Any]:
        return dict(self._result_payload)

    def _current_row(self) -> int:
        return self.profile_list.currentRow()

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self.name_edit,
            self.provider_combo,
            self.model_edit,
            self.api_key_edit,
            self.base_url_edit,
            self.supports_images_checkbox,
        ):
            widget.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        if enabled:
            self._update_base_url_field_state(self.provider_combo.currentText())

    def _set_save_state(self, message: str) -> None:
        text = str(message or "").strip()
        self.save_state_label.setText(text)
        self.save_state_label.setVisible(bool(text))

    def _profile_id_for_row(self, row: int) -> str:
        if row < 0 or row >= len(self._profiles):
            return ""
        return str(self._profiles[row].get("id") or "").strip()

    def _row_for_profile_id(self, profile_id: str) -> int:
        target_id = str(profile_id or "").strip()
        if not target_id:
            return -1
        for idx, profile in enumerate(self._profiles):
            if str(profile.get("id") or "").strip() == target_id:
                return idx
        return -1

    def _refresh_profile_counts(self) -> None:
        total = len(self._profiles)
        enabled = sum(1 for profile in self._profiles if bool(profile.get("enabled", True)))
        self.profile_count_chip.setText(f"{total} total")
        self.left_meta_chip.setText(f"{enabled} enabled")
        active_name = str(self._active_profile or "").strip() or "none"
        self.active_profile_label.setText(
            f"Active now: <span style='color:#D5D9DF;font-weight:700'>{active_name}</span>"
        )

    def _apply_profile_filter(self, value: str) -> None:
        self._filter_text = str(value or "").strip().lower()
        visible_rows: list[int] = []
        for row in range(self.profile_list.count()):
            item = self.profile_list.item(row)
            if item is None:
                continue
            haystack = " ".join((str(item.data(Qt.UserRole) or ""), str(item.toolTip() or ""))).lower()
            should_hide = bool(self._filter_text) and self._filter_text not in haystack
            item.setHidden(should_hide)
            if not should_hide:
                visible_rows.append(row)

        current_row = self.profile_list.currentRow()
        if current_row < 0:
            if visible_rows:
                self.profile_list.setCurrentRow(visible_rows[0])
            return

        current_item = self.profile_list.item(current_row)
        if current_item is not None and current_item.isHidden():
            if visible_rows:
                self.profile_list.setCurrentRow(visible_rows[0])
            else:
                self.profile_list.clearSelection()
                self._sync_form_to_profile(-1)

    def _update_base_url_field_state(self, provider: str) -> None:
        provider_normalized = str(provider or "").strip().lower()
        enabled = provider_normalized == "openai"
        self.base_url_edit.setEnabled(enabled)
        if enabled:
            self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
            self.base_url_edit.setToolTip("")
        else:
            self.base_url_edit.setPlaceholderText("Not used for gemini")
            self.base_url_edit.setToolTip("Base URL is only used for openai profiles.")

    def _display_name(self, profile: dict[str, str]) -> str:
        profile_id = str(profile.get("id") or "").strip()
        provider = str(profile.get("provider") or "").strip()
        model_name = str(profile.get("model") or "").strip()
        marker = ""
        if profile_id and profile_id == self._active_profile:
            marker = " • active"
        elif not bool(profile.get("enabled", True)):
            marker = " • disabled"
        title = profile_id if profile_id else "(unnamed)"
        if marker:
            title = f"{title}{marker}"
        details = " · ".join(part for part in (provider, model_name) if part)
        return f"{title}\n{details}" if details else title

    def _refresh_profile_item_states(self) -> None:
        current_row = self._current_row()
        for row in range(self.profile_list.count()):
            item = self.profile_list.item(row)
            widget = self.profile_list.itemWidget(item) if item is not None else None
            if widget is None:
                continue
            profile = self._profiles[row] if 0 <= row < len(self._profiles) else {}
            is_selected = row == current_row and not bool(item.isHidden()) if item is not None else False
            is_active = bool(str(profile.get("id") or "").strip() and str(profile.get("id") or "").strip() == self._active_profile)
            is_enabled = bool(profile.get("enabled", True))
            widget.setProperty("selectedProfile", is_selected)
            widget.setProperty("activeProfile", is_active)
            widget.setProperty("disabledProfile", not is_enabled)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _toggle_api_key_visibility(self) -> None:
        reveal = self.api_key_edit.echoMode() != QLineEdit.Normal
        self.api_key_edit.setEchoMode(QLineEdit.Normal if reveal else QLineEdit.Password)
        icon_name = "fa5s.eye-slash" if reveal else "fa5s.eye"
        self.api_key_reveal_button.setIcon(_fa_icon(icon_name, color=TEXT_MUTED, size=12))

    def _copy_api_key(self) -> None:
        QApplication.clipboard().setText(self.api_key_edit.text())
        self._set_save_state("API key copied to clipboard.")

    def _build_profile_item_widget(self, profile: dict[str, Any], row: int) -> QWidget:
        container = QWidget()
        container.setObjectName("ModelProfileRowCard")
        is_enabled = bool(profile.get("enabled", True))
        container.setProperty("disabledProfile", not is_enabled)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.setSpacing(12)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)

        first_row = QHBoxLayout()
        first_row.setContentsMargins(0, 0, 0, 0)
        first_row.setSpacing(4)

        profile_id = str(profile.get("id") or "").strip() or "(unnamed)"
        is_active = bool(profile_id and profile_id != "(unnamed)" and profile_id == self._active_profile)
        title_label = QLabel(profile_id)
        title_label.setObjectName("ModelProfileItemTitle")
        title_label.setEnabled(is_enabled)
        title_label.setMargin(0)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title_label.setMinimumWidth(0)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        first_row.addWidget(title_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        provider = str(profile.get("provider") or "").strip()
        if provider:
            provider_label = QLabel(provider)
            provider_label.setObjectName("ModelProfileItemBadge")
            provider_label.setProperty("badgeVariant", "provider")
            first_row.addWidget(provider_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        if is_active:
            active_label = QLabel("Active")
            active_label.setObjectName("ModelProfileItemBadge")
            active_label.setProperty("badgeVariant", "active")
            first_row.addWidget(active_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        elif not is_enabled:
            disabled_label = QLabel("Disabled")
            disabled_label.setObjectName("ModelProfileItemBadge")
            disabled_label.setProperty("badgeVariant", "muted")
            first_row.addWidget(disabled_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        first_row.addStretch(1)
        text_column.addLayout(first_row)

        model_name = str(profile.get("model") or "").strip()
        details = " · ".join(part for part in (provider, model_name) if part)
        details_label = QLabel(details)
        details_label.setObjectName("ModelProfileItemMeta")
        details_label.setEnabled(is_enabled)
        details_label.setMargin(0)
        details_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        details_label.setWordWrap(False)
        details_label.setMinimumWidth(0)
        details_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_column.addWidget(details_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        layout.addLayout(text_column, 1)

        enabled_switch = QCheckBox()
        enabled_switch.setObjectName("ModelProfileEnabledSwitch")
        enabled_switch.setChecked(is_enabled)
        enabled_switch.setCursor(Qt.PointingHandCursor)
        enabled_switch.setToolTip("Temporarily enable or disable this model")
        enabled_switch.setFocusPolicy(Qt.NoFocus)
        enabled_switch.setFixedSize(QSize(34, 20))
        enabled_switch.pressed.connect(lambda target_row=row: self.profile_list.setCurrentRow(target_row))
        enabled_switch.toggled.connect(lambda checked, target_row=row: self._toggle_profile_enabled(target_row, checked))
        layout.addWidget(enabled_switch, 0, Qt.AlignRight | Qt.AlignVCenter)
        container.ensurePolished()
        title_label.ensurePolished()
        details_label.ensurePolished()
        title_label.setMinimumHeight(title_label.fontMetrics().height() + 6)
        details_label.setMinimumHeight(details_label.fontMetrics().height() + 4)
        container.adjustSize()
        return container

    def _refresh_profile_list(
        self,
        preferred_row: int | None = None,
        *,
        preferred_profile_id: str = "",
        restore_scroll_value: int | None = None,
    ) -> None:
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for row, profile in enumerate(self._profiles):
            item_widget = self._build_profile_item_widget(profile, row)
            item = QListWidgetItem("")
            item.setData(Qt.UserRole, self._display_name(profile))
            item_widget.ensurePolished()
            widget_hint = item_widget.sizeHint()
            minimum_hint = item_widget.minimumSizeHint()
            item_height = max(72, widget_hint.height(), minimum_hint.height()) + 6
            item.setSizeHint(QSize(widget_hint.width(), item_height))
            provider = str(profile.get("provider") or "").strip()
            model_name = str(profile.get("model") or "").strip()
            enabled = "yes" if bool(profile.get("enabled", True)) else "no"
            item.setToolTip(f"Provider: {provider}\nModel: {model_name}\nEnabled: {enabled}".strip())
            self.profile_list.addItem(item)
            self.profile_list.setItemWidget(item, item_widget)
        self.profile_list.blockSignals(False)
        if self.save_button is not None:
            self.save_button.setEnabled(bool(self._profiles))

        if not self._profiles:
            self._selected_row = -1
            self._set_form_enabled(False)
            self.form_hint.setText("Add a profile to start configuring models.")
            self.profile_state_chip.setText("No profile selected")
            self.summary_provider.setText("Provider: —")
            self.summary_model.setText("Model: —")
            self.summary_images.setText("Image input: off")
            self.duplicate_button.setEnabled(False)
            return

        row = preferred_row
        if preferred_profile_id:
            resolved_row = self._row_for_profile_id(preferred_profile_id)
            if resolved_row >= 0:
                row = resolved_row
        if row is None:
            row = self._preferred_row_for_open()
        row = max(0, min(row, len(self._profiles) - 1))
        self.profile_list.setCurrentRow(row)
        self._apply_profile_filter(self.search_edit.text())
        self._refresh_profile_item_states()
        if restore_scroll_value is not None:
            self.profile_list.verticalScrollBar().setValue(restore_scroll_value)

    def _preferred_row_for_open(self) -> int:
        active_id = str(self._active_profile or "").strip()
        if active_id:
            for index, profile in enumerate(self._profiles):
                if str(profile.get("id") or "").strip() == active_id:
                    return index
        return self._current_row() if self._current_row() >= 0 else 0

    def _sync_form_to_profile(self, row: int) -> None:
        if row < 0 or row >= len(self._profiles):
            self._selected_row = -1
            self._set_form_enabled(False)
            self.form_hint.setText("Select a profile and edit fields on the right.")
            self.profile_state_chip.setText("No profile selected")
            self.summary_provider.setText("Provider: —")
            self.summary_model.setText("Model: —")
            self.summary_images.setText("Image input: off")
            self.duplicate_button.setEnabled(False)
            self._refresh_profile_item_states()
            return
        profile = self._profiles[row]
        self._loading_form = True
        self._set_form_enabled(True)
        self.name_edit.setText(str(profile.get("id", "")))
        provider = str(profile.get("provider", "openai")).strip().lower()
        if provider not in ALLOWED_PROVIDERS:
            provider = "openai"
        self.provider_combo.setCurrentText(provider)
        self.model_edit.setText(str(profile.get("model", "")))
        self.api_key_edit.setText(str(profile.get("api_key", "")))
        self.base_url_edit.setText(str(profile.get("base_url", "")))
        self.supports_images_checkbox.setChecked(bool(profile.get("supports_image_input")))
        self._update_base_url_field_state(provider)
        self._loading_form = False
        self._selected_row = row
        profile_id = str(profile.get("id") or "").strip() or "(unnamed)"
        status = "enabled" if bool(profile.get("enabled", True)) else "disabled"
        self.form_hint.setText(f"Editing profile: {profile_id} ({status})")
        is_active = bool(profile_id != "(unnamed)" and profile_id == self._active_profile)
        self.profile_state_chip.setText("Active profile" if is_active else status.title())
        self.duplicate_button.setEnabled(True)
        provider_text = str(profile.get("provider") or "—").strip() or "—"
        model_name = str(profile.get("model") or "—").strip() or "—"
        self.summary_provider.setText(f"Provider: {provider_text}")
        self.summary_model.setText(f"Model: {model_name}")
        self.summary_images.setText(
            "Image input: on" if bool(profile.get("supports_image_input")) else "Image input: off"
        )
        self._refresh_profile_counts()
        self._refresh_profile_item_states()

    def _sync_current_profile_from_form(self, row: int | None = None) -> None:
        if self._loading_form:
            return
        target_row = self._current_row() if row is None else row
        if target_row < 0 or target_row >= len(self._profiles):
            return
        provider = str(self.provider_combo.currentText() or "").strip().lower()
        if provider not in ALLOWED_PROVIDERS:
            provider = "openai"
        base_url = str(self.base_url_edit.text() or "").strip() if provider == "openai" else ""
        self._profiles[target_row] = {
            "id": str(self.name_edit.text() or "").strip(),
            "provider": provider,
            "model": str(self.model_edit.text() or "").strip(),
            "api_key": str(self.api_key_edit.text() or "").strip(),
            "base_url": base_url,
            "supports_image_input": self.supports_images_checkbox.isChecked(),
            "enabled": bool(self._profiles[target_row].get("enabled", True)),
        }
        item = self.profile_list.item(target_row)
        if item is not None:
            item.setData(Qt.UserRole, self._display_name(self._profiles[target_row]))
            provider_text = str(self._profiles[target_row].get("provider") or "").strip()
            model_name = str(self._profiles[target_row].get("model") or "").strip()
            enabled = "yes" if bool(self._profiles[target_row].get("enabled", True)) else "no"
            item.setToolTip(f"Provider: {provider_text}\nModel: {model_name}\nEnabled: {enabled}".strip())
        self._refresh_profile_counts()

    def _reconcile_active_profile(self) -> None:
        enabled_ids = [
            str(profile.get("id") or "").strip()
            for profile in self._profiles
            if bool(profile.get("enabled", True)) and str(profile.get("id") or "").strip()
        ]
        active_id = str(self._active_profile or "").strip()
        if active_id in enabled_ids:
            return
        self._active_profile = enabled_ids[0] if enabled_ids else ""

    def _toggle_profile_enabled(self, row: int, state: bool | int) -> None:
        if row < 0 or row >= len(self._profiles):
            return
        self._sync_current_profile_from_form(self._selected_row)
        preferred_profile_id = self._profile_id_for_row(row)
        scroll_value = self.profile_list.verticalScrollBar().value()
        if isinstance(state, bool):
            is_enabled = bool(state)
        elif isinstance(state, int):
            is_enabled = state == Qt.Checked
        else:
            is_enabled = bool(state)
        self._profiles[row]["enabled"] = is_enabled
        self._reconcile_active_profile()
        self._set_save_state("")
        self._refresh_profile_list(
            preferred_row=row,
            preferred_profile_id=preferred_profile_id,
            restore_scroll_value=scroll_value,
        )
        self._refresh_profile_counts()

    def _suggest_unique_id(self, model_text: str, *, row: int) -> str:
        used = {
            str(profile.get("id") or "").strip()
            for idx, profile in enumerate(self._profiles)
            if idx != row and str(profile.get("id") or "").strip()
        }
        return generate_profile_id(model_text, used)

    def _compute_initial_name_manual_flags(self) -> list[bool]:
        flags: list[bool] = []
        for idx, profile in enumerate(self._profiles):
            profile_id = str(profile.get("id") or "").strip()
            if not profile_id:
                flags.append(False)
                continue
            expected_auto_id = self._suggest_unique_id(str(profile.get("model") or ""), row=idx)
            flags.append(profile_id != expected_auto_id)
        return flags

    def _on_selection_changed(self, row: int) -> None:
        previous_row = self._selected_row
        if previous_row != row:
            self._sync_current_profile_from_form(previous_row)
        self._sync_form_to_profile(row)

    def _on_name_edited(self, text: str) -> None:
        row = self._current_row()
        if 0 <= row < len(self._name_manual_flags):
            self._name_manual_flags[row] = bool(str(text or "").strip())
        self._sync_current_profile_from_form()

    def _on_model_changed(self, _text: str) -> None:
        if self._loading_form:
            return
        row = self._current_row()
        if row < 0 or row >= len(self._profiles):
            return
        current_name = str(self.name_edit.text() or "").strip()
        if (not self._name_manual_flags[row]) or not current_name:
            self._loading_form = True
            self.name_edit.setText(self._suggest_unique_id(self.model_edit.text(), row=row))
            self._loading_form = False
        self._on_form_changed()

    def _on_form_changed(self) -> None:
        self._set_save_state("")
        self._sync_current_profile_from_form()

    def _on_provider_changed(self, provider: str) -> None:
        if self._loading_form:
            return
        self._update_base_url_field_state(provider)
        if str(provider or "").strip().lower() != "openai":
            self._loading_form = True
            self.base_url_edit.clear()
            self._loading_form = False
        self._on_form_changed()

    def _add_profile(self) -> None:
        self._sync_current_profile_from_form(self._selected_row)
        self._profiles.append(
            {
                "id": "",
                "provider": "openai",
                "model": "",
                "api_key": "",
                "base_url": "",
                "supports_image_input": False,
                "enabled": True,
            }
        )
        self._name_manual_flags.append(False)
        self._set_save_state("")
        self._refresh_profile_list(preferred_row=len(self._profiles) - 1)
        self.name_edit.setFocus()

    def _duplicate_selected_profile(self) -> None:
        row = self._current_row()
        if row < 0 or row >= len(self._profiles):
            return
        self._sync_current_profile_from_form(self._selected_row)
        source = dict(self._profiles[row])
        duplicated = dict(source)
        duplicated["id"] = ""
        duplicated["enabled"] = bool(source.get("enabled", True))
        self._profiles.insert(row + 1, duplicated)
        self._name_manual_flags.insert(row + 1, False)
        self._set_save_state("Duplicated profile. Rename it before saving if needed.")
        self._refresh_profile_list(preferred_row=row + 1)
        self.name_edit.setFocus()

    def _delete_selected_profile(self) -> None:
        row = self._current_row()
        if row < 0 or row >= len(self._profiles):
            return
        self._sync_current_profile_from_form(self._selected_row)
        removed_id = str(self._profiles[row].get("id") or "").strip()
        self._profiles.pop(row)
        self._name_manual_flags.pop(row)

        if removed_id and removed_id == self._active_profile:
            self._active_profile = ""
        self._reconcile_active_profile()

        self._set_save_state("")
        self._refresh_profile_list(preferred_row=row)
        self._refresh_profile_counts()

    def _validated_payload(self) -> dict[str, Any] | None:
        self._sync_current_profile_from_form(self._selected_row)
        profiles: list[dict[str, str]] = []
        used_ids: set[str] = set()

        for idx, profile in enumerate(self._profiles):
            provider = str(profile.get("provider") or "").strip().lower()
            model_name = str(profile.get("model") or "").strip()
            if provider not in ALLOWED_PROVIDERS:
                self.profile_list.setCurrentRow(idx)
                QMessageBox.warning(self, "Validation", "Provider must be openai or gemini.")
                return None
            if not model_name:
                self.profile_list.setCurrentRow(idx)
                QMessageBox.warning(self, "Validation", "Model cannot be empty.")
                return None

            requested_id = sanitize_profile_id(profile.get("id") or "")
            if not requested_id:
                requested_id = model_name
            profile_id = generate_profile_id(requested_id, used_ids)
            profiles.append(
                {
                    "id": profile_id,
                    "provider": provider,
                    "model": model_name,
                    "api_key": str(profile.get("api_key") or "").strip(),
                    "base_url": str(profile.get("base_url") or "").strip(),
                    "supports_image_input": bool(profile.get("supports_image_input")),
                    "enabled": bool(profile.get("enabled", True)),
                }
            )

        active = str(self._active_profile or "").strip()
        enabled_ids = [item["id"] for item in profiles if bool(item.get("enabled", True))]
        if active not in enabled_ids:
            active = enabled_ids[0] if enabled_ids else ""
        return {"active_profile": active or None, "profiles": profiles}

    def _save_and_accept(self) -> None:
        validated = self._validated_payload()
        if validated is None:
            return
        self._result_payload = normalize_profiles_payload(validated)
        self._profiles = [dict(item) for item in self._result_payload.get("profiles", [])]
        self._active_profile = str(self._result_payload.get("active_profile") or "").strip()
        self._name_manual_flags = self._compute_initial_name_manual_flags()
        current_row = self._current_row()
        preferred_row = current_row if current_row >= 0 else self._preferred_row_for_open()
        self._refresh_profile_list(preferred_row=preferred_row)
        self._refresh_profile_counts()
        self._set_save_state("Saved. You can keep this window open and continue editing.")
        self.profiles_saved.emit(dict(self._result_payload))


class ApprovalDialog(QDialog):
    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice: tuple[bool, bool] = (False, False)
        self.setObjectName("ApprovalDialog")
        self.setWindowTitle("Approval required")
        self.setModal(True)
        self.resize(680, 460)
        self.setMinimumSize(560, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
        risk_level = str(summary.get("risk_level", "unknown") or "unknown")
        impacts = [str(item).strip() for item in list(summary.get("impacts", []) or []) if str(item).strip()]
        tools = list(payload.get("tools", []) or [])
        default_approve = bool(summary.get("default_approve"))

        hero_card = QFrame()
        hero_card.setObjectName("ApprovalRequestCard")
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel("Protected action review")
        title.setObjectName("ApprovalCardTitle")
        title_row.addWidget(title)

        self.dialog_risk_badge = QLabel(risk_level.title())
        self.dialog_risk_badge.setObjectName("ApprovalRiskBadge")
        self.dialog_risk_badge.setProperty("riskLevel", risk_level)
        style = self.dialog_risk_badge.style()
        if style is not None:
            style.unpolish(self.dialog_risk_badge)
            style.polish(self.dialog_risk_badge)
        title_row.addWidget(self.dialog_risk_badge, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        hero_layout.addLayout(title_row)

        summary_label = QLabel(
            f"Please confirm before the agent continues. {len(tools)} protected action(s) requested. "
            f"Default policy: {'approve' if default_approve else 'deny'}."
        )
        summary_label.setObjectName("ApprovalCardSummary")
        summary_label.setWordWrap(True)
        hero_layout.addWidget(summary_label)

        impacts_label = QLabel(f"Impacts: {', '.join(impacts)}" if impacts else "Impacts: local state")
        impacts_label.setObjectName("ApprovalCardImpacts")
        impacts_label.setWordWrap(True)
        hero_layout.addWidget(impacts_label)
        layout.addWidget(hero_card)

        scroll = QScrollArea()
        scroll.setObjectName("ApprovalDialogScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(10)
        for tool in tools:
            card = QFrame()
            card.setObjectName("ApprovalToolCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 12, 12, 12)
            card_layout.setSpacing(8)
            display_name = str(tool.get("display") or tool.get("name") or "tool").strip() or "tool"
            name_label = QLabel(display_name)
            name_label.setObjectName("ApprovalDialogToolTitle")
            card_layout.addWidget(name_label)

            metadata = tool.get("policy", {}) if isinstance(tool.get("policy"), dict) else {}
            tool_meta = []
            if metadata.get("mutating"):
                tool_meta.append("mutating")
            if metadata.get("destructive"):
                tool_meta.append("destructive")
            if metadata.get("requires_approval"):
                tool_meta.append("approval")
            if tool_meta:
                meta_label = QLabel(" · ".join(tool_meta))
                meta_label.setObjectName("ApprovalCardImpacts")
                card_layout.addWidget(meta_label)

            args_view = QPlainTextEdit()
            args_view.setObjectName("ApprovalArgsView")
            args_view.setReadOnly(True)
            args_view.setFont(_make_mono_font())
            args_view.setPlainText(json.dumps(tool.get("args", {}), ensure_ascii=False, indent=2))
            args_view.setFixedHeight(108)
            card_layout.addWidget(CollapsibleSection("Request details", args_view, expanded=False))
            container_layout.addWidget(card)
        container_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox()
        approve_button = QPushButton(_fa_icon("fa5s.check", color="white", size=12), "Approve")
        approve_button.setObjectName("PrimaryButton")
        always_button = QPushButton("Always for this session")
        always_button.setObjectName("SecondaryButton")
        deny_button = QPushButton(_fa_icon("fa5s.times", color="white", size=12), "Deny")
        deny_button.setObjectName("DangerButton")
        buttons.addButton(approve_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(always_button, QDialogButtonBox.ActionRole)
        buttons.addButton(deny_button, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        approve_button.clicked.connect(self._approve)
        always_button.clicked.connect(self._always)
        deny_button.clicked.connect(self._deny)

    def _approve(self) -> None:
        self.choice = (True, False)
        self.accept()

    def _always(self) -> None:
        self.choice = (True, True)
        self.accept()

    def _deny(self) -> None:
        self.choice = (False, False)
        self.reject()
