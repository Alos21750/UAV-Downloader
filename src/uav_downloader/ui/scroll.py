"""Scrollable frame compatible with unresolved Tk event widget paths."""

import customtkinter as ctk


class ScrollableFrame(ctk.CTkScrollableFrame):
    def _check_if_valid_scroll(self, widget):
        # Tk can report a Tcl-only or destroyed widget as a string path.
        if isinstance(widget, str):
            return False
        return super()._check_if_valid_scroll(widget)
