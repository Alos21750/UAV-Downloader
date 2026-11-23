"""Lightweight, shared UI for local-first subtitle translation settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional

import customtkinter as ctk
from uav_downloader.ui.scroll import ScrollableFrame

from uav_downloader.i18n.locales import T, ui_font
from uav_downloader.subtitles.settings import (
    ANTHROPIC,
    GEMINI,
    LOCAL,
    OPENAI,
    OPENAI_COMPATIBLE,
    TranslationSettings,
    TranslationSettingsError,
    clear_translation_api_key,
    get_translation_settings,
    save_translation_profile,
)
from uav_downloader.ui.theme import (
    ACCENT,
    ACCENT_HOVER,
    BG_CARD,
    BG_CARD_HOVER,
    BG_DARK,
    BG_INPUT,
    BORDER,
    BORDER_CARD,
    BORDER_HOVER,
    CARD_RADIUS,
    CONTROL_RADIUS,
    SUCCESS,
    TEXT_DIM,
    TEXT_PRI,
    TEXT_SEC,
    WARNING,
    WHITE,
)


_PROVIDER_ORDER = (
    LOCAL,
    OPENAI,
    ANTHROPIC,
    GEMINI,
    OPENAI_COMPATIBLE,
)
_PROVIDER_LABEL_KEYS = {
    LOCAL: "translation_provider_local",
    OPENAI: "translation_provider_openai",
    ANTHROPIC: "translation_provider_anthropic",
    GEMINI: "translation_provider_gemini",
    OPENAI_COMPATIBLE: "translation_provider_compatible",
}
_DIALOG_TEXT_WRAP = 460
_DIALOG_FIELD_HELP_WRAP = 330
_DIALOG_INLINE_WRAP = 480

# OpenAI-compatible providers accept a complete Chat Completions endpoint.
# Models stay editable because availability differs by account and installation.
_COMPATIBLE_PRESETS = {
    "DeepSeek": (
        "https://api.deepseek.com/chat/completions",
        "deepseek-v4-flash",
    ),
    "OpenRouter": (
        "https://openrouter.ai/api/v1/chat/completions",
        "openai/gpt-4.1-mini",
    ),
    "Groq": (
        "https://api.groq.com/openai/v1/chat/completions",
        "llama-3.3-70b-versatile",
    ),
    "Ollama": (
        "http://127.0.0.1:11434/v1/chat/completions",
        "your-installed-model",
    ),
    "LiteLLM": (
        "http://127.0.0.1:4000/v1/chat/completions",
        "your-model",
    ),
}


def provider_display_name(provider: str) -> str:
    """Return a localized, stable display name for a provider id."""
    return T(_PROVIDER_LABEL_KEYS.get(provider, "translation_provider_local"))


def translation_provider_summary(*, short: bool = False) -> str:
    """Describe the selected provider without exposing credentials or URLs."""
    settings = get_translation_settings()
    name = provider_display_name(settings.provider)
    if settings.provider == LOCAL:
        return (
            T("translation_provider_summary_local_short")
            if short
            else T("translation_provider_summary_local")
        )
    if short:
        return name
    if settings.api_key_source == "environment":
        state = T("translation_key_state_environment_short")
    elif settings.api_key_source == "protected":
        state = T("translation_key_state_saved_short")
    elif settings.provider == OPENAI_COMPATIBLE:
        state = T("translation_key_state_optional_short")
    else:
        state = T("translation_key_state_missing_short")
    return T("translation_provider_summary_cloud", provider=name, state=state)


def translation_failure_message(error: object) -> str:
    """Add a localized Local fallback hint without exposing profile details."""
    message = str(error or "").strip()
    try:
        uses_api = get_translation_settings().uses_api
    except Exception:
        uses_api = False
    if not uses_api:
        return message
    hint = T("translation_api_failure_local_hint")
    return f"{message} {hint}".strip()


def _provider_labels() -> list[str]:
    return [provider_display_name(provider) for provider in _PROVIDER_ORDER]


def _provider_from_label(label: str) -> str:
    by_label = {
        provider_display_name(provider): provider
        for provider in _PROVIDER_ORDER
    }
    return by_label.get(str(label or ""), LOCAL)


class TranslationSettingsDialog(ctk.CTkToplevel):
    """Modal translation provider editor shared by both desktop entrypoints."""

    def __init__(
        self,
        parent,
        *,
        on_saved: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self._on_saved = on_saved
        self._provider = get_translation_settings().provider
        self._key_source = "none"

        self.title(T("translation_dialog_title"))
        self.configure(fg_color=BG_DARK)
        self.geometry(self._initial_geometry(parent))
        self.minsize(600, 520)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()
        self._load_provider(self._provider)
        self.after_idle(self._finish_open)

    def _initial_geometry(self, parent) -> str:
        try:
            screen_w = int(parent.winfo_screenwidth())
            screen_h = int(parent.winfo_screenheight())
        except (AttributeError, tk.TclError):
            screen_w, screen_h = 1280, 800
        width = min(720, max(600, screen_w - 48))
        height = min(720, max(520, screen_h - 80))
        return f"{width}x{height}"

    def _finish_open(self):
        try:
            self.update_idletasks()
            x = max(0, (self.winfo_screenwidth() - self.winfo_width()) // 2)
            y = max(0, (self.winfo_screenheight() - self.winfo_height()) // 3)
            self.geometry(f"+{x}+{y}")
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self):
        font = ui_font()

        header = ctk.CTkFrame(
            self, fg_color=BG_CARD, corner_radius=0, height=82)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header,
            text=T("translation_dialog_title"),
            text_color=TEXT_PRI,
            font=(font, 18, "bold"),
        ).pack(anchor="w", padx=24, pady=(15, 2))
        ctk.CTkLabel(
            header,
            text=T("translation_dialog_subtitle"),
            text_color=TEXT_DIM,
            font=(font, 10),
        ).pack(anchor="w", padx=24)

        body = ScrollableFrame(
            self,
            fg_color=BG_DARK,
            corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER,
        )
        body.pack(fill="both", expand=True)
        content = ctk.CTkFrame(body, fg_color="transparent")
        content.pack(fill="x", padx=24, pady=20)

        source_card = ctk.CTkFrame(
            content,
            fg_color=BG_CARD,
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=BORDER_CARD,
        )
        source_card.pack(fill="x")
        ctk.CTkLabel(
            source_card,
            text=T("translation_source_title"),
            text_color=TEXT_PRI,
            font=(font, 13, "bold"),
        ).pack(anchor="w", padx=18, pady=(16, 3))
        ctk.CTkLabel(
            source_card,
            text=T("translation_source_desc"),
            text_color=TEXT_DIM,
            font=(font, 10),
            wraplength=_DIALOG_TEXT_WRAP,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        self._provider_var = ctk.StringVar()
        ctk.CTkOptionMenu(
            source_card,
            variable=self._provider_var,
            values=_provider_labels(),
            command=self._on_provider_change,
            height=38,
            corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT,
            button_color=BORDER_HOVER,
            button_hover_color=ACCENT,
            text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font, 11),
            dropdown_font=(font, 11),
        ).pack(fill="x", padx=18, pady=(0, 18))

        self._local_card = ctk.CTkFrame(
            content,
            fg_color=BG_CARD,
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=BORDER_CARD,
        )
        ctk.CTkLabel(
            self._local_card,
            text=T("translation_local_title"),
            text_color=TEXT_PRI,
            font=(font, 13, "bold"),
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            self._local_card,
            text=T("translation_local_desc"),
            text_color=TEXT_SEC,
            font=(font, 10),
            wraplength=_DIALOG_TEXT_WRAP,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 16))

        self._cloud_card = ctk.CTkFrame(
            content,
            fg_color=BG_CARD,
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=BORDER_CARD,
        )
        self._cloud_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self._cloud_card,
            text=T("translation_cloud_privacy"),
            text_color=TEXT_PRI,
            font=(font, 10, "bold"),
            wraplength=_DIALOG_TEXT_WRAP,
            justify="left",
        ).grid(
            row=0, column=0, columnspan=3, sticky="w",
            padx=18, pady=(16, 4))
        ctk.CTkLabel(
            self._cloud_card,
            text=T("translation_cloud_policy_notice"),
            text_color=WARNING,
            font=(font, 9),
            wraplength=_DIALOG_TEXT_WRAP,
            justify="left",
        ).grid(
            row=1, column=0, columnspan=3, sticky="w",
            padx=18, pady=(0, 14))

        self._url_label = ctk.CTkLabel(
            self._cloud_card,
            text=T("translation_base_url_label"),
            text_color=TEXT_SEC,
            font=(font, 10, "bold"),
            width=110,
            anchor="w",
        )
        self._url_label.grid(
            row=2, column=0, sticky="w", padx=(18, 8), pady=5)
        self._base_url_var = ctk.StringVar()
        self._base_url_entry = ctk.CTkEntry(
            self._cloud_card,
            textvariable=self._base_url_var,
            height=36,
            corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT_PRI,
            font=(font, 10),
        )
        self._base_url_entry.grid(
            row=2, column=1, columnspan=2, sticky="ew",
            padx=(0, 18), pady=5)

        ctk.CTkLabel(
            self._cloud_card,
            text=T("translation_model_label"),
            text_color=TEXT_SEC,
            font=(font, 10, "bold"),
            width=110,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=(18, 8), pady=5)
        self._model_var = ctk.StringVar()
        ctk.CTkEntry(
            self._cloud_card,
            textvariable=self._model_var,
            height=36,
            corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT_PRI,
            font=(font, 10),
        ).grid(
            row=3, column=1, columnspan=2, sticky="ew",
            padx=(0, 18), pady=5)

        ctk.CTkLabel(
            self._cloud_card,
            text=T("translation_api_key_label"),
            text_color=TEXT_SEC,
            font=(font, 10, "bold"),
            width=110,
            anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=(18, 8), pady=5)
        self._api_key_var = ctk.StringVar()
        self._api_key_entry = ctk.CTkEntry(
            self._cloud_card,
            textvariable=self._api_key_var,
            placeholder_text=T("translation_api_key_placeholder"),
            show="•",
            height=36,
            corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT_PRI,
            font=(font, 10),
        )
        self._api_key_entry.grid(
            row=4, column=1, sticky="ew", padx=(0, 8), pady=5)
        self._clear_key_btn = ctk.CTkButton(
            self._cloud_card,
            text=T("translation_clear_key"),
            width=78,
            height=34,
            corner_radius=CONTROL_RADIUS,
            fg_color="transparent",
            border_width=1,
            border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER,
            text_color=TEXT_SEC,
            font=(font, 9),
            command=self._clear_key,
        )
        self._clear_key_btn.grid(
            row=4, column=2, sticky="e", padx=(0, 18), pady=5)

        self._key_status = ctk.CTkLabel(
            self._cloud_card,
            text="",
            text_color=TEXT_DIM,
            font=(font, 9),
            anchor="w",
            wraplength=_DIALOG_FIELD_HELP_WRAP,
            justify="left",
        )
        self._key_status.grid(
            row=5, column=1, columnspan=2, sticky="w",
            padx=(0, 18), pady=(0, 7))

        self._compatible_frame = ctk.CTkFrame(
            self._cloud_card, fg_color="transparent")
        self._compatible_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self._compatible_frame,
            text=T("translation_compatible_preset"),
            text_color=TEXT_SEC,
            font=(font, 10, "bold"),
            width=110,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self._preset_var = ctk.StringVar(
            value=T("translation_compatible_custom"))
        ctk.CTkOptionMenu(
            self._compatible_frame,
            variable=self._preset_var,
            values=[
                T("translation_compatible_custom"),
                *_COMPATIBLE_PRESETS.keys(),
            ],
            command=self._on_preset,
            height=34,
            corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT,
            button_color=BORDER_HOVER,
            button_hover_color=ACCENT,
            text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font, 10),
            dropdown_font=(font, 10),
        ).grid(row=0, column=1, sticky="ew", pady=5)
        ctk.CTkLabel(
            self._compatible_frame,
            text=T("translation_compatible_help"),
            text_color=TEXT_DIM,
            font=(font, 9),
            wraplength=_DIALOG_FIELD_HELP_WRAP,
            justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(0, 6))
        self._compatible_frame.grid(
            row=6, column=0, columnspan=3, sticky="ew",
            padx=18, pady=(0, 8))

        self._inline_status = ctk.CTkLabel(
            content,
            text="",
            text_color=TEXT_DIM,
            font=(font, 10, "bold"),
            wraplength=_DIALOG_INLINE_WRAP,
            justify="left",
        )
        self._inline_status.pack(anchor="w", pady=(12, 0))

        footer = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        footer.pack(fill="x")
        ctk.CTkButton(
            footer,
            text=T("translation_close"),
            width=98,
            height=38,
            corner_radius=CONTROL_RADIUS,
            fg_color="transparent",
            border_width=1,
            border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI,
            font=(font, 10),
            command=self.destroy,
        ).pack(side="right", padx=(8, 20), pady=14)
        self._save_btn = ctk.CTkButton(
            footer,
            text=T("translation_save_replace"),
            width=142,
            height=38,
            corner_radius=CONTROL_RADIUS,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=WHITE,
            font=(font, 10, "bold"),
            command=self._save,
        )
        self._save_btn.pack(side="right", pady=14)

    def _on_provider_change(self, label: str):
        self._load_provider(_provider_from_label(label))

    def _load_provider(self, provider: str):
        self._provider = provider
        settings = get_translation_settings(provider)
        self._key_source = settings.api_key_source
        self._provider_var.set(provider_display_name(provider))
        self._api_key_var.set("")  # Never decrypt a stored key into the UI.
        self._inline_status.configure(text="")

        if provider == LOCAL:
            self._cloud_card.pack_forget()
            self._local_card.pack(
                fill="x", pady=(14, 0), before=self._inline_status)
            self._save_btn.configure(text=T("translation_use_local"))
            return

        self._local_card.pack_forget()
        self._cloud_card.pack(
            fill="x", pady=(14, 0), before=self._inline_status)
        self._save_btn.configure(text=T("translation_save_replace"))
        self._base_url_var.set(settings.base_url)
        self._model_var.set(settings.model)
        compatible = provider == OPENAI_COMPATIBLE
        # Official provider credentials must go only to their official
        # endpoint. Custom endpoints belong under OpenAI-compatible.
        self._base_url_entry.configure(
            state="normal" if compatible else "disabled")
        self._url_label.configure(
            text=T(
                "translation_chat_endpoint_label"
                if compatible else "translation_base_url_label"))
        if compatible:
            self._compatible_frame.grid()
        else:
            self._compatible_frame.grid_remove()
        self._refresh_key_status(settings)

    def _refresh_key_status(self, settings: TranslationSettings):
        self._key_source = settings.api_key_source
        if settings.api_key_source == "environment":
            text = T("translation_key_state_environment")
            color = SUCCESS
            self._clear_key_btn.configure(state="disabled")
        elif settings.api_key_source == "protected":
            text = T("translation_key_state_saved")
            color = SUCCESS
            self._clear_key_btn.configure(state="normal")
        elif settings.provider == OPENAI_COMPATIBLE:
            text = T("translation_key_state_optional")
            color = TEXT_DIM
            self._clear_key_btn.configure(state="normal")
        else:
            text = T("translation_key_state_missing")
            color = WARNING
            self._clear_key_btn.configure(state="normal")
        self._key_status.configure(text=text, text_color=color)

    def _on_preset(self, preset: str):
        values = _COMPATIBLE_PRESETS.get(preset)
        if values is None:
            return
        endpoint, model = values
        self._base_url_var.set(endpoint)
        self._model_var.set(model)

    def _clear_key(self):
        try:
            settings = clear_translation_api_key(self._provider)
        except TranslationSettingsError as exc:
            messagebox.showerror(
                T("translation_error_title"), str(exc), parent=self)
            return
        self._api_key_var.set("")
        self._refresh_key_status(settings)
        self._inline_status.configure(
            text=T("translation_key_cleared"), text_color=SUCCESS)
        if self._on_saved:
            self._on_saved()

    def _save(self):
        if self._provider == LOCAL:
            try:
                save_translation_profile(LOCAL)
            except TranslationSettingsError as exc:
                messagebox.showerror(
                    T("translation_error_title"), str(exc), parent=self)
                return
            self._inline_status.configure(
                text=T("translation_saved_local"), text_color=SUCCESS)
            if self._on_saved:
                self._on_saved()
            return

        candidate_key = self._api_key_var.get().strip()
        if (
            self._provider != OPENAI_COMPATIBLE
            and not candidate_key
            and self._key_source == "none"
        ):
            messagebox.showerror(
                T("translation_error_title"),
                T("translation_api_key_required"),
                parent=self,
            )
            return
        try:
            settings = save_translation_profile(
                self._provider,
                base_url=self._base_url_var.get(),
                model=self._model_var.get(),
                api_key=(candidate_key if candidate_key else None),
            )
        except TranslationSettingsError as exc:
            messagebox.showerror(
                T("translation_error_title"), str(exc), parent=self)
            return
        self._api_key_var.set("")
        self._refresh_key_status(settings)
        self._inline_status.configure(
            text=T("translation_saved_cloud"), text_color=SUCCESS)
        if self._on_saved:
            self._on_saved()


def open_translation_settings_dialog(parent, *, on_saved=None):
    """Open one provider settings dialog per parent window."""
    existing = getattr(parent, "_translation_settings_dialog", None)
    try:
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return existing
    except tk.TclError:
        pass

    dialog = TranslationSettingsDialog(parent, on_saved=on_saved)
    parent._translation_settings_dialog = dialog

    def _forget(_event=None):
        if getattr(parent, "_translation_settings_dialog", None) is dialog:
            parent._translation_settings_dialog = None

    dialog.bind("<Destroy>", _forget, add="+")
    return dialog
