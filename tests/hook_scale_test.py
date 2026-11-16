# -*- coding: utf-8 -*-
"""--hook-scale 手动覆盖的实测：钉对系数要精确，钉错系数要被尺子校验拦住。

全程点自己进程的置顶窗口，不改任何显示设置。
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
root = tk.Tk()
root.title("--hook-scale 测试（本窗口会被点击）")
root.attributes("-topmost", True)
root.geometry("600x400+0+0")
root.update()


def one_press(h, tx, ty, label):
    u.SetCursorPos(tx, ty)
    time.sleep(0.12)
    pt = wt.POINT()
    u.GetCursorPos(ctypes.byref(pt))
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
    print(f"{label}: 手动系数={h.manual_scale} 光标={pt.x}/{pt.y} 钩子={h.last('down')[1:]} "
          f"起笔=({st['x0']},{st['y0']}) skew_max={tp.hook_skew_max:.0f} warn={tp.skew_warn} "
          f"maps={len(h.maps)} refresh={h.refresh_count}")
    return st, tp, pt


h = MouseHook()
h.manual_scale = 0.5                # 本机真实系数就是 0.5
print("start=", h.start(), "maps 应为空（手动模式不读表）:", len(h.maps))
st1, tp1, p1 = one_press(h, 300, 300, "① 钉对 0.5")
h.maps_ts = time.time() - 99        # 模拟挂了两分钟的会话
st2, tp2, p2 = one_press(h, 500, 350, "② 钉对 0.5 且表已过期")

h.manual_scale = 1.0                # 故意钉错
st3, tp3, p3 = one_press(h, 700, 400, "③ 钉错 1.0")

print("结论:",
      "① 精确" if abs(st1["x0"] - p1.x) <= 3 else f"① 偏 {st1['x0']} vs {p1.x}",
      "| ② 过期后仍精确" if abs(st2["x0"] - p2.x) <= 3 and h.maps == [] else
      f"| ② 被自动表顶掉 {st2['x0']} vs {p2.x} maps={len(h.maps)}",
      "| ③ 被校验拦住" if (tp3.skew_warn and abs(st3["x0"] - p3.x) <= 3) else
      f"| ③ 未拦住 {st3['x0']} vs {p3.x} warn={tp3.skew_warn}")
root.destroy()
