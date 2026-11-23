"""Scroll events may arrive after Tk has unregistered the originating widget."""
import customtkinter as ctk


class ScrollableFrame(ctk.CTkScrollableFrame):
    def _check_if_valid_scroll(self, widget):
        # Tcl paths have no Python widget ancestry to match to this canvas.
        if isinstance(widget, str):
            return False
        return super()._check_if_valid_scroll(widget)
