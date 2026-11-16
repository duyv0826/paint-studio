# -*- coding: utf-8 -*-
"""真机验证：用主程序里的 MouseHook（含每屏换算表）在两块屏各点一次，
对比钩子报的坐标与 GetCursorPos 报的坐标。点击一律落在本进程自己置顶的窗口上。

结论（本机 2026-11-13）：DISPLAY2(4K@200%) 与 DISPLAY1(2560×1440@150%) 两处零点位，
钩子坐标换算后与 GetCursorPos 完全一致，双屏换算表可用。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paint_studio_widget import MouseHook      # noqa: E402

u = ctypes.windll.user32

h = MouseHook()
print("hook ok=", h.start(), "err=", h.err)
for m in h.maps:
    print("  screen", m["name"], "virt=", m["virt"], "phys=", m["phys"], "scale=", round(m["kx"], 3))
print("primary scale=", h.scale)

root = tk.Tk()
root.title("DPI 探针（本窗口会被点击）")
root.attributes("-topmost", True)
root.update()

# (虚拟目标点, 窗口左上角)
cases = [("DISPLAY2", 300, 300, "500x360+0+0"),
         ("DISPLAY1", 3300, 800, "500x360+3050+650")]
pt = wt.POINT()
for label, tx, ty, geo in cases:
    root.geometry(geo)
    root.update()
    u.SetCursorPos(tx, ty)
    time.sleep(0.15)
    u.GetCursorPos(ctypes.byref(pt))
    virt = (pt.x, pt.y)
    n0 = len(h.events)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    got = None
    for _ in range(30):
        root.update()
        time.sleep(0.02)
        for ev in list(h.events)[n0:]:
            if ev[0] == "down":
                got = (ev[2], ev[3])
                break
        if got:
            break
    delta = (got[0] - virt[0], got[1] - virt[1]) if got else None
    print(f"{label}: GetCursorPos={virt} hook={got} delta={delta}")

root.destroy()
