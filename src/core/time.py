"""Helpers for clock times used by the API."""

from __future__ import annotations

SECONDS_PER_DAY = 24 * 60 * 60


def parse_clock_seconds(value: object) -> int:
    """Accept an integer number of seconds or an ``HH:MM[:SS]`` string."""
    if isinstance(value, bool):
        raise ValueError("clock time must be seconds or an HH:MM string")
    if isinstance(value, (int, float)):
        seconds = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("clock time cannot be empty")
        parts = text.split(":")
        if len(parts) not in (2, 3):
            raise ValueError("clock time must use HH:MM or HH:MM:SS")
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            secs = int(parts[2]) if len(parts) == 3 else 0
        except ValueError as exc:
            raise ValueError("clock time must contain numeric hours, minutes and seconds") from exc
        if not 0 <= minutes <= 59:
            raise ValueError("minutes must be between 0 and 59")
        if not 0 <= secs <= 59:
            raise ValueError("seconds must be between 0 and 59")
        seconds = hours * 3600 + minutes * 60 + secs
    else:
        raise ValueError("clock time must be seconds or an HH:MM string")

    if not 0 <= seconds <= SECONDS_PER_DAY:
        raise ValueError("clock time must be between 00:00 and 24:00")
    return seconds


def format_clock(seconds: int) -> str:
    """Format seconds since midnight as ``HH:MM``. ``24:00`` is preserved."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"
