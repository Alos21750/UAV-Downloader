"""Shared editable bandwidth control; invalid input never disables a cap."""
import math
import customtkinter as ctk

from uav_downloader.core import config
from uav_downloader.i18n.locales import ui_font
from uav_downloader.ui.theme import (
    BG_INPUT, BORDER, BORDER_HOVER, ACCENT, ERROR_C, TEXT_PRI, TEXT_SEC,
)


def parse_bandwidth(value):
    try:
        number = float(str(value).strip())
    except (ValueError, TypeError):
        raise ValueError('Enter a finite download limit') from None
    if not math.isfinite(number) or not 0 <= number <= config.MAX_SPEED_LIMIT_MBPS:
        raise ValueError('Download limit is out of range')
    if 0 < number < 0.01:
        raise ValueError('Minimum nonzero limit is 0.01 MB/s')
    return number


class BandwidthControl(ctk.CTkFrame):
    def __init__(self, master, *, value, command):
        super().__init__(master, fg_color='transparent')
        self._command = command
        self._saved = float(value)
        self._value = ctk.StringVar(value=f'{value:g}')
        self._entry = ctk.CTkComboBox(
            self, variable=self._value, values=['0', '0.5', '1', '2', '5', '10', '15', '25'],
            width=110, height=36, corner_radius=8, fg_color=BG_INPUT,
            border_color=BORDER, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            font=(ui_font(), 12), command=self.commit)
        self._entry.pack(side='left')
        self._entry.bind('<Return>', self.commit)
        self._entry.bind('<FocusOut>', self.commit)
        ctk.CTkLabel(self, text='MB/s', text_color=TEXT_SEC,
                     font=(ui_font(), 11)).pack(side='left', padx=(6, 0))

    def commit(self, _event=None):
        try:
            value = parse_bandwidth(self._value.get())
        except ValueError:
            self._value.set(f'{self._saved:g}')
            self._entry.configure(border_color=ERROR_C)
            return False
        if value != self._saved:
            self._command(value)
            self._saved = value
        self._value.set(f'{value:g}')
        self._entry.configure(border_color=BORDER)
        return True

    def set_value(self, value):
        """Synchronize another visible control without triggering its callback."""
        self._saved = value
        self._value.set(f'{value:g}')
        self._entry.configure(border_color=BORDER)


def commit_bandwidth_controls(app):
    for name in ('_bandwidth_control', '_toolbar_bandwidth'):
        control = getattr(app, name, None)
        if control is not None:
            control.commit()
