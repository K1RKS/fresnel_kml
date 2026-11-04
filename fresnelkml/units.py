"""Display formatting for heights and distances in metric or US units.

All calculations stay in metres; these helpers only format values for people.
"""

from __future__ import annotations

FEET = 0.3048
MILE = 1609.344
UNITS = ("m", "ft")


def fmt_height(metres: float | None, units: str = "m", digits: int = 1) -> str:
    if metres is None:
        return "unknown"
    if units == "ft":
        return f"{metres / FEET:.{digits}f} ft"
    return f"{metres:.{digits}f} m"


def fmt_gap(metres: float, units: str = "m") -> str:
    """Describe line of sight minus terrain height."""
    where = "below" if metres >= 0 else "above"
    return f"terrain {fmt_height(abs(metres), units)} {where} the line of sight"


def fmt_distance(metres: float, units: str = "m", digits: int = 3) -> str:
    """Short distances in m or ft, longer ones in km or miles."""
    if units == "ft":
        feet = metres / FEET
        if abs(feet) < 1000:
            return f"{feet:.0f} ft"
        return f"{metres / MILE:.{digits}f} mi"
    if abs(metres) < 1000:
        return f"{metres:.0f} m"
    return f"{metres / 1000:.{digits}f} km"
