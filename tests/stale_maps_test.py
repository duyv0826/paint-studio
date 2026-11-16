# -*- coding: utf-8 -*-
"""换算表过期（屏缩放被改过）时的兜底：用真实钩子事件走一遍 Tracker。

场景：DISPLAY2 实际 200%（换算 0.5），但程序手里的表还是「未缩放」（0.5→1.0）。
预期：按下点与当拍光标差 300 px > 120 → 退回当拍坐标、记 warn、skew_max 约 300；
      重读换算表后再按一次 → 回到精确坐标。
点击一律落在本进程自己置顶的窗口上，不改任何显示设置。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paint_studio_widget import MouseHook, Tracker      # noqa: E402

u = ctypes.windll.user32
h = MouseHook()
print("hook ok=", h.start(), "真实换算=", [round(m["kx"], 3) for m in h.maps])

root = tk.Tk()
root.title("过期换算表测试（本窗口会被点击）")
root.attributes("-topmost", True)
root.geometry("600x400+0+0")
root.update()

WRONG = [{"name": "WRONG", "virt": (0, 0, 1920, 1080), "phys": (0, 0, 1920, 1080),
          "primary": True, "kx": 1.0, "ky": 1.0}]


def press_at(tx, ty, label):
    """在 (tx,ty) 点一下，返回 Tracker 记到的这一笔。"""
    u.SetCursorPos(tx, ty)
    time.sleep(0.12)
    pt = wt.POINT()
    u.GetCursorPos(ctypes.byref(pt))          # 当拍（虚拟）光标位置
    tp = Tracker()
    tp.restrict = False
    tp.press_src = lambda: h.last("down")
    tp.sample(time.time() - 0.05, pt.x, pt.y, False, False, False, "photoshop.exe", True)
    n0 = len(h.events)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    for _ in range(25):
        root.update()
        time.sleep(0.02)
        if len(h.events) > n0:
            break
    t = time.time()
    tp.sample(t, pt.x, pt.y, True, False, False, "photoshop.exe", True)
    tp.sample(t + 0.05, pt.x + 20, pt.y, False, False, False, "photoshop.exe", True)
    st = tp.stroke_list[-1]
    print(f"{label}: 光标={pt.x}/{pt.y} 钩子事件={h.last('down')[1:] if h.last('down') else None} "
          f"记到的起笔=({st['x0']},{st['y0']}) skew_max={tp.hook_skew_max:.0f} warn={tp.skew_warn}")
    return st, tp


# ① 表过期（错 2 倍）：应触发兜底
h.maps, h.scale, h.maps_ts = WRONG, 1.0, time.time()
st1, tp1 = press_at(300, 300, "过期表")

# ② 重读换算表后再按：应回到精确
h.refresh(min_gap=0.0)
print("重读后换算=", [round(m["kx"], 3) for m in h.maps], "change_count=", h.change_count)
st2, tp2 = press_at(700, 400, "重读后")

print("结论:",
      "① 兜底生效" if (tp1.skew_warn and st1["x0"] == 300 and tp1.hook_skew_max > 120) else "① 未触发（需检查）",
      "| ② 恢复精确" if abs(st2["x0"] - 700) <= 2 else "| ② 仍偏")
root.destroy()
