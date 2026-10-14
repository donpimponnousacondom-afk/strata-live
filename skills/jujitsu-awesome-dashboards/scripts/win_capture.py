"""win_capture: screenshot one window on Windows by its title, standard library only (ctypes + zlib).

Uses PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT), so it works while the window is covered or off-screen, and
captures real pixels (per-monitor DPI aware). Optional: size the client area exactly first, crop, zoom (nearest
neighbour, for inspecting edges pixel by pixel).

    python win_capture.py out.png --title "My Dashboard"
    python win_capture.py out.png --title "My Dashboard" --size 1280x780          # resize client area, then grab
    python win_capture.py zoom.png --title "My Dashboard" --crop 0,0,400,240 --zoom 3
    python win_capture.py out.png --title "(selftest)" --exact                    # title must match exactly

    python win_capture.py out.png --title "My Dashboard" --method bitblt           # if PrintWindow gives black

Exit code 2 if no visible window matches, 3 if the capture failed. Prints "W H" of the saved image.
Windows only. Prefer a unique title (e.g. the app adds "(selftest)" in test runs) or --exact.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time
import zlib


def png_rgb(w: int, h: int, rgb: bytes) -> bytes:
    stride = 3 * w
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _api():
    """user32 / gdi32 with handle types declared: without restype, 64-bit handles come back as truncated c_int."""
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    u.GetDC.restype, u.GetDC.argtypes = H, [wt.HWND]
    u.ReleaseDC.argtypes = [wt.HWND, H]
    u.PrintWindow.restype, u.PrintWindow.argtypes = wt.BOOL, [wt.HWND, H, wt.UINT]
    u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    u.IsIconic.argtypes = [wt.HWND]
    u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = H, [H]
    g.CreateCompatibleBitmap.restype, g.CreateCompatibleBitmap.argtypes = H, [H, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype, g.SelectObject.argtypes = H, [H, H]
    g.DeleteObject.argtypes = [H]
    g.DeleteDC.argtypes = [H]
    g.BitBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, ctypes.c_int, ctypes.c_int,
                         wt.DWORD]
    g.GetDIBits.argtypes = [H, H, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    return u, g


def find_window(title: str, exact: bool) -> int | None:
    """The best visible match: Tk's own top-level class first, then the biggest. Never the console running us
    (cmd.exe / Windows Terminal put the command line, and so the title you searched for, in their title)."""
    user32, _ = _api()
    console = ctypes.windll.kernel32.GetConsoleWindow()
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd) and hwnd != console:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if (buf.value == title) if exact else (title in buf.value):
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                rc = wt.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rc))
                found.append((cls.value == "TkTopLevel", rc.right * rc.bottom, hwnd))
        return True
    user32.EnumWindows(cb, 0)
    return max(found)[2] if found else None


def set_client_size(hwnd: int, w: int, h: int) -> None:
    """Resize so the client area is w x h (restores a maximized window first), then correct any remainder."""
    user32, _ = _api()
    user32.ShowWindow(hwnd, 9)                   # SW_RESTORE: a maximized window ignores SetWindowPos
    SWP = 0x0002 | 0x0004 | 0x0010               # NOMOVE | NOZORDER | NOACTIVATE
    for _ in range(3):                            # menus and DPI can make the first guess off by a few px
        rc, wr = wt.RECT(), wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        dw, dh = w - rc.right, h - rc.bottom
        if not dw and not dh:
            return
        user32.SetWindowPos(hwnd, None, 0, 0, wr.right - wr.left + dw, wr.bottom - wr.top + dh, SWP)
        time.sleep(0.2)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(hwnd: int, method: str = "printwindow") -> tuple[int, int, bytes]:
    """The client area as (w, h, RGB bytes). printwindow works while covered; bitblt copies what's on screen."""
    user32, gdi32 = _api()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)               # SW_RESTORE: a minimized window has no client area
        time.sleep(0.5)
    rc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    w, h = rc.right, rc.bottom
    if w <= 0 or h <= 0:
        raise RuntimeError("the window has no client area (minimized or hidden?)")
    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if method == "bitblt":
            ok = gdi32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)       # SRCCOPY
        else:
            ok = user32.PrintWindow(hwnd, mdc, 1 | 2)   # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        if not ok:
            raise RuntimeError(f"{method} failed; try --method bitblt with the window visible")
        gdi32.SelectObject(mdc, old)             # GetDIBits wants the bitmap not selected into a DC
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0) != h:
            raise RuntimeError("GetDIBits failed")
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
    bgra = buf.raw
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return w, h, bytes(rgb)


def crop_zoom(w, h, rgb, crop=None, zoom=1):
    if crop:
        x, y, cw, ch = crop
        x, y = max(0, min(x, w - 1)), max(0, min(y, h - 1))
        cw, ch = max(1, min(cw, w - x)), max(1, min(ch, h - y))
        rgb = b"".join(rgb[(y + j) * w * 3 + x * 3:(y + j) * w * 3 + (x + cw) * 3] for j in range(ch))
        w, h = cw, ch
    if zoom > 1:
        rows = []
        for j in range(h):
            row = rgb[j * w * 3:(j + 1) * w * 3]
            wide = b"".join(row[i * 3:i * 3 + 3] * zoom for i in range(w))
            rows.append(wide * zoom)
        rgb, w, h = b"".join(rows), w * zoom, h * zoom
    return w, h, rgb


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--title", required=True, help="part of the window title (or all of it with --exact)")
    ap.add_argument("--exact", action="store_true")
    ap.add_argument("--size", help="WxH: set the client area to this size first")
    ap.add_argument("--settle", type=float, default=1.5, help="seconds to wait after --size")
    ap.add_argument("--crop", help="x,y,w,h in client pixels")
    ap.add_argument("--zoom", type=int, default=1)
    ap.add_argument("--dpi-awareness", type=int, default=1, choices=(0, 1, 2),
                    help="match the app's SetProcessDpiAwareness (default 1, system aware)")
    ap.add_argument("--method", choices=("printwindow", "bitblt"), default="printwindow",
                    help="bitblt if PrintWindow returns black or the whole window (rare drivers / builds)")
    a = ap.parse_args()
    try:
        # use the same awareness as the app you capture (most Tk apps call SetProcessDpiAwareness(1)); with
        # different settings on a scaled display, GetClientRect and the captured bitmap can disagree in size
        ctypes.windll.shcore.SetProcessDpiAwareness(a.dpi_awareness)
    except Exception:
        pass
    hwnd = find_window(a.title, a.exact)
    if not hwnd:
        print(f"no visible window titled {a.title!r}", file=sys.stderr)
        return 2
    if a.size:
        w, h = (int(v) for v in a.size.lower().split("x"))
        set_client_size(hwnd, w, h)
        time.sleep(a.settle)
    try:
        w, h, rgb = grab(hwnd, a.method)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 3
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    w, h, rgb = crop_zoom(w, h, rgb, crop, a.zoom)
    with open(a.out, "wb") as f:
        f.write(png_rgb(w, h, rgb))
    print(w, h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
