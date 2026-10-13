"""Capture the Strata Engineering window with PrintWindow (works while covered). Usage: grab_monitor.py out.png [W H]"""
import ctypes
import ctypes.wintypes as w
import os
import sys
import time

from PIL import Image

ctypes.windll.shcore.SetProcessDpiAwareness(2)
u, g = ctypes.windll.user32, ctypes.windll.gdi32
hw = []


@ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
def cb(h, _):
    n = u.GetWindowTextLengthW(h)
    if n and u.IsWindowVisible(h):
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        if os.environ.get("GRAB_TITLE", "Strata Engineering") in b.value and (os.environ.get("GRAB_TITLE") or not b.value.endswith(")")):
            hw.append(h)
    return True


u.EnumWindows(cb, 0)
h = hw[0]
if len(sys.argv) > 3:
    # size the client area exactly
    r = w.RECT(0, 0, int(sys.argv[2]), int(sys.argv[3]))
    u.AdjustWindowRectEx(ctypes.byref(r), u.GetWindowLongW(h, -16), False, u.GetWindowLongW(h, -20))
    u.SetWindowPos(h, 0, 0, 0, r.right - r.left, r.bottom - r.top, 0x0002 | 0x0004 | 0x0010)
    time.sleep(2.5)
rc = w.RECT()
u.GetClientRect(h, ctypes.byref(rc))
W, H = rc.right, rc.bottom
hdc = u.GetDC(h)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, W, H)
g.SelectObject(mdc, bmp)
u.PrintWindow(h, mdc, 3)   # client only + full content


class BMI(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD)]


bi = BMI(ctypes.sizeof(BMI), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
buf = ctypes.create_string_buffer(W * H * 4)
g.GetDIBits(mdc, bmp, 0, H, buf, ctypes.byref(bi), 0)
Image.frombuffer("RGB", (W, H), buf, "raw", "BGRX", 0, 1).save(sys.argv[1])
print(W, H)
