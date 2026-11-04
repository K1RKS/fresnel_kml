"""Zone color parsing and the constrained random pick."""

from __future__ import annotations

import colorsys
import random
import re
from dataclasses import dataclass

DEFAULT_ALPHA = 0x59
RANDOM_HUE_RANGE = (75.0, 330.0)
RANDOM_SATURATION = (0.65, 1.0)
RANDOM_VALUE = (0.75, 1.0)

NAMED_COLORS = {
    "green": "#00ff00",
    "blue": "#0000ff",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "purple": "#800080",
    "pink": "#ff69b4",
    "white": "#ffffff",
    "orange": "#ffa500",
    "red": "#ff0000",
    "yellow": "#ffff00",
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


class ColorError(ValueError):
    pass


@dataclass(frozen=True)
class ZoneColor:
    r: int
    g: int
    b: int
    a: int
    random: bool = False

    @property
    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def kml(self) -> str:
        """KML aabbggrr form."""
        return f"{self.a:02x}{self.b:02x}{self.g:02x}{self.r:02x}"

    @property
    def kml_opaque(self) -> str:
        return f"ff{self.b:02x}{self.g:02x}{self.r:02x}"

    def to_dict(self) -> dict:
        return {"hex": self.hex, "alpha": self.a, "kml": self.kml, "random": self.random}


def random_color(rng: random.Random | None = None) -> ZoneColor:
    rng = rng or random.Random()
    hue = rng.uniform(*RANDOM_HUE_RANGE)
    sat = rng.uniform(*RANDOM_SATURATION)
    val = rng.uniform(*RANDOM_VALUE)
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, sat, val)
    return ZoneColor(round(r * 255), round(g * 255), round(b * 255), DEFAULT_ALPHA, random=True)


def parse_color(value, rng: random.Random | None = None) -> ZoneColor:
    """Parse "random", "#RRGGBB", "#RRGGBBAA" or a named color."""
    if value is None:
        return random_color(rng)
    if not isinstance(value, str):
        raise ColorError("Color must be a string such as \"random\", \"#00ff00\" or \"green\".")
    text = value.strip().lower()
    if text in ("", "random"):
        return random_color(rng)
    text = NAMED_COLORS.get(text, text)
    m = _HEX_RE.match(text)
    if not m or not text.startswith("#"):
        names = ", ".join(NAMED_COLORS)
        raise ColorError(f"'{value}' is not a color. Use \"random\", #RRGGBB, #RRGGBBAA or one of: {names}.")
    rgb = m.group(1)
    alpha = int(m.group(2), 16) if m.group(2) else DEFAULT_ALPHA
    return ZoneColor(int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16), alpha)
