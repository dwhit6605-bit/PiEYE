"""Arm/disarm scheduling.

When disarmed the cameras keep streaming and the live view works; nothing is
recorded and nothing notifies. Handy for "don't alert while I'm home".
"""


def parse_hhmm(value, default_minutes):
    """'22:00' -> minutes since midnight. Falls back on anything unparseable."""
    try:
        h, m = str(value).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except Exception:
        pass
    return default_minutes


def scheduled_armed(now_minutes, arm_at, disarm_at):
    """Should the system be armed at now_minutes?

    Handles windows that wrap midnight (arm 22:00 -> disarm 07:00). Returns None
    when the two times are equal, i.e. the window is meaningless.
    """
    a = parse_hhmm(arm_at, 22 * 60)
    d = parse_hhmm(disarm_at, 7 * 60)
    if a == d:
        return None
    if a < d:                       # same-day window, e.g. 09:00 -> 17:00
        return a <= now_minutes < d
    return now_minutes >= a or now_minutes < d   # wraps midnight


def now_minutes(dt):
    return dt.hour * 60 + dt.minute
