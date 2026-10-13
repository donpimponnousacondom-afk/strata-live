"""The base of a department page: it draws inside the area right of the warp core."""
from __future__ import annotations

from lcars_theme import ORANGE


class Page:
    code, title, sub, color = "00", "PAGE", "", ORANGE

    def __init__(self, w):
        self.w = w
        self.c = w.c
        self.area = (0, 0, 0, 0)

    def build(self, area) -> None:
        """Static layout (on resize or when the page opens)."""
        self.area = area

    def draw(self, s: dict) -> None:
        """Readouts, once a second (everything tagged "dyn")."""

    def on_wheel(self, e) -> bool:
        return False

    def cols(self, n: int = 2, gap: int = 16, area=None):
        x0, y0, x1, y1 = area or self.area
        w = (x1 - x0 - (n - 1) * gap) / n
        return [(x0 + i * (w + gap), y0, x0 + i * (w + gap) + w, y1) for i in range(n)]

    def sub_header(self, x0, y, x1, label, color):
        self.w.header(x0, y, x1, label, color, tags=("dyn",))
