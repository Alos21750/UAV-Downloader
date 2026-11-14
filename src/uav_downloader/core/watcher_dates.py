"""Local-date shortcuts and staged calendar data shared by desktop frontends."""
import calendar
from datetime import date, datetime, timedelta

from uav_downloader.core.watcher import _months_before


def preset_date(index, today=None):
    today = today or datetime.now().astimezone().date()
    if index == 0:
        return (today - timedelta(days=1)).isoformat()
    months = [1, 2, 3, 6]
    return _months_before(today, months[index - 1]).isoformat() if 1 <= index <= 4 else ''


def calendar_seed(value):
    try:
        selected = date.fromisoformat(str(value))
    except ValueError:
        selected = datetime.now().astimezone().date()
    return dict(year=selected.year, month=selected.month, selected=selected.isoformat())


def month_days(year, month):
    year, month = int(year), int(month)
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    days = [day for week in weeks for day in week]
    days += [0] * (42 - len(days))
    return dict(title=f'{year:04d}-{month:02d}', days=[
        dict(day=day, value=date(year, month, day).isoformat() if day else '') for day in days])
