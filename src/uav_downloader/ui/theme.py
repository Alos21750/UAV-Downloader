#!/usr/bin/env python
# coding: utf-8
"""Shared visual system for the UAV Browser and UAV Watcher desktop apps."""


# A restrained graphite / warm-paper palette with one vermilion brand accent.
# CustomTkinter resolves every (light, dark) tuple automatically.
ACCENT = ('#C83E4B', '#F0525D')
ACCENT_HOVER = ('#B23441', '#D9444F')
ACCENT_DIM = ('#F8E7E9', '#2B171B')

SUCCESS = ('#2F7D4E', '#4FBA78')
SUCCESS_DIM = ('#E6F1EA', '#15261B')
WARNING = ('#A76812', '#DEA23C')
WARNING_DIM = ('#F7EEDC', '#2A2112')
ERROR_C = ('#B63B47', '#EA6A75')
ERROR_DIM = ('#F8E7E9', '#2B171B')

BG_DARK = ('#F5F3EF', '#111113')
BG_CARD = ('#FFFFFF', '#19191C')
BG_CARD_HOVER = ('#F0EEEA', '#222226')
BG_INPUT = ('#FAF9F7', '#151518')
BG_HEADER = ('#FBFAF8', '#0D0D0F')
BG_SECTION = ('#EEEAE5', '#151518')
BG_SIDEBAR = ('#EFEBE6', '#0E0E10')
BG_BADGE = ('#ECE9E4', '#242328')

TEXT_PRI = ('#1B1817', '#F5F2EF')
TEXT_SEC = ('#68615D', '#B7B0AB')
TEXT_DIM = ('#706B66', '#A5ABB5')
TEXT_LINK = ('#B23441', '#F07A82')

BORDER = ('#E1DDD7', '#2B2A2E')
BORDER_HOVER = ('#CEC8C0', '#403E44')
BORDER_CARD = ('#E6E1DA', '#252429')

WHITE = ('#FFFFFF', '#FFFFFF')
CARD_RADIUS = 10
CONTROL_RADIUS = 8


def color_for_mode(token, mode='dark'):
    """Resolve a CustomTkinter color tuple for plain Tk fallback widgets."""
    if not isinstance(token, (tuple, list)):
        return token
    return token[0 if str(mode).lower() == 'light' else 1]


def browse_columns_for_width(width):
    """Readable browse-card density for the root window width."""
    width = max(0, int(width or 0))
    if width >= 1500:
        return 4
    if width >= 1080:
        return 3
    return 2


def category_columns_for_width(width):
    """UAV Watcher category density without clipping localized labels."""
    return 3 if int(width or 0) >= 1120 else 2
