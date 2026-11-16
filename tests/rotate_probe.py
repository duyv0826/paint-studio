# -*- coding: utf-8 -*-
"""物理旋转副屏后，换算表是否跟着变、坐标是否仍精确。

只旋转副屏（\\\\.\\DISPLAY1），不持久化（ChangeDisplaySettingsEx 不带 UPDATEREGISTRY），
并且带 30 秒看门狗，无论脚本怎么退出都会把屏幕转回去。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paint_studio_widget import MouseHook, Tracker, DEVMODEW      # noqa: E402

u = ctypes.windll.user32
DEV = "\\\\.\\DISPLAY1"
ENUM_CURRENT = 0xFFFFFFFF
DM_ORIENTATION, DM_PELS_W, DM_PELS_H = 0x00000080, 0x00000001, 0x00000002
DMDO_90 = 1

u.ChangeDisplaySettingsExW.argtypes = [wt.LPCWSTR, ctypes.POINTER(DEVMODEW),
                                       ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
u.ChangeDisplaySettingsExW.restype = ctypes.c_int         # DISP_CHANGE_*


def current_mode():
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not u.EnumDisplaySettingsW(DEV, ENUM_CURRENT, ctypes.byref(dm)):
        return None
    return dm


def apply(dm):
    return u.ChangeDisplaySettingsExW(DEV, ctypes.byref(dm), None, 0, None)


orig = current_mode()
if orig is None:
    print("读不到 DISPLAY1 的当前模式，放弃")
    raise SystemExit(1)
print(f"原始模式: {orig.dmPelsWidth}×{orig.dmPelsHeight} @({orig.dmPosition.x},{orig.dmPosition.y}) "
      f"方向={orig.dmDisplayOrientation}")

restored = threading.Event()


def restore():
    if restored.is_set():
        return
    r = apply(orig)
    print("恢复原方向 →", "OK" if r == 0 else f"DISP_CHANGE_{r}")
    restored.set()


timer = threading.Timer(30.0, restore)          # 看门狗：脚本崩了也会转回去
timer.daemon = True
timer.start()

h = MouseHook()                                  # 旋转前读一次基线（否则无从判断「变了」）
h.refresh()
print("旋转前基线:", [(m["name"], round(m["kx"], 3)) for m in h.maps], "change=", h.change_count)
if not h.start():
    print("钩子装不上：", h.err)
    raise SystemExit(1)

rot = current_mode()
rot.dmPelsWidth, rot.dmPelsHeight = orig.dmPelsHeight, orig.dmPelsWidth
rot.dmDisplayOrientation = DMDO_90
rot.dmFields = orig.dmFields | DM_ORIENTATION | DM_PELS_W | DM_PELS_H
rc = apply(rot)
print("旋转 90° →", "OK" if rc == 0 else f"DISP_CHANGE_{rc}（{rc} 非 0 则本机不支持热旋转）")

time.sleep(2)
root = tk.Tk()
root.title("旋转屏测试（本窗口会被点击）")
root.attributes("-topmost", True)

try:
    if rc == 0:
        h.refresh()                              # 旋转后重读：应当记一次「变更」
        print("旋转后换算:", [(m["name"], f'{m["virt"]}', f'{m["phys"]}',
                            round(m["kx"], 3), round(m["ky"], 3)) for m in h.maps],
              "change=", h.change_count)
        m1 = [m for m in h.maps if m["name"].upper().endswith("DISPLAY1")]
        if m1:
            mm = m1[0]
            cx = (mm["virt"][0] + mm["virt"][2]) // 2
            cy = (mm["virt"][1] + mm["virt"][3]) // 2
            root.geometry(f"400x300+{cx - 200}+{cy - 150}")
            root.update()
            h.maps_ts = time.time() - 99                  # 逼它在按下时自己重读一次
            u.SetCursorPos(cx, cy)
            time.sleep(0.15)
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
            ev = h.last("down")
            print(f"旋转屏上点击: 光标={pt.x}/{pt.y} 钩子={ev[1:] if ev else None} "
                  f"起笔=({st['x0']},{st['y0']}) skew_max={tp.hook_skew_max:.0f} warn={tp.skew_warn} "
                  f"change_count={h.change_count} refresh={h.refresh_count}")
            ok_xy = abs(st["x0"] - pt.x) <= 3 and abs(st["y0"] - pt.y) <= 3
            print("旋转后判定:", "坐标精确" if ok_xy else "坐标仍有偏差",
                  "| 变更已记录" if h.change_count >= 1 else "| 变更未记录")
    else:
        print("本机拒绝热旋转，跳过旋转后的采集验证")
finally:
    restore()
    timer.cancel()
    time.sleep(1.5)
    h.refresh()                                      # 同一个实例：旋转算一次变更，恢复算第二次
    print("恢复后换算:", [(m["name"], f'{m["virt"]}', round(m["kx"], 3)) for m in h.maps],
          "change=", h.change_count, "（旋转 1 次 + 恢复 1 次，应为 2）")
    root.destroy()
