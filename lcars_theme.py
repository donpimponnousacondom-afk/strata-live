"""Colours and small helpers shared by the engineering console and its department pages."""
from __future__ import annotations

BG = "#000000"
ORANGE, PEACH, TAN, GOLD = "#FF9900", "#FF9966", "#FFCC99", "#FFCC66"
LILAC, VIOLET, BLUE, SKY = "#CC99CC", "#9977DD", "#9999FF", "#99CCFF"
RED, RED_HI, GREEN = "#CC4444", "#FF5544", "#66CC99"
INK, MUTED, GRID, PANEL = "#FFCC99", "#8f8576", "#1c1c24", "#0b0b10"
CORE_DIM, CORE_LIT, CORE_AMBER, CORE_DEAD = "#162a4d", "#bfe4ff", "#ffbb55", "#2a1414"
# where a request's experts came from (the warp coils): the card's cache, loaded into it from RAM, or over PCIe
COIL_CACHE, COIL_MISS, COIL_PCIE = "#bfe4ff", "#ff9a4d", "#b28cff"
TRIAL_GREEN, REALIGN_GOLD = "#7dffb0", "#ffd65a"
KIND_COLORS = {"coding turn": "#66CC99", "summary": "#FF9966", "failed summary": "#FF5544", "full re-read": "#FFCC66"}
BAR_ROW = 28


def mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def fmt_k(n) -> str:
    if n is None:
        return "—"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1000:.1f}K" if n >= 1000 else f"{n:.0f}"


def coil_mix(last: dict) -> tuple:
    """The last request's experts as fractions (cache hit, loaded from RAM into the cache, streamed over PCIe).
    Strata gives hit_rate = hits / lookups and pcie_share = offloaded / (lookups + offloaded)."""
    h, p = last.get("hit_rate"), last.get("pcie_share")
    if h is None:
        return None
    p = p or 0.0
    return h * (1 - p), (1 - h) * (1 - p), p
