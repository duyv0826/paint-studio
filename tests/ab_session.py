# -*- coding: utf-8 -*-
"""A/B 回放台：在自建的白板上重放同一段手的动作（不碰任何别人的窗口）。

用途：给「有同桌 / 独桌」两种情况喂完全相同的输入，用来验证
      笔触测量是否被社交条件污染（不是用来证明同桌有效）。

用法：
    python ab_session.py            # 重放一段：5 笔 → 停 32 秒 → 5 笔
"""
import ctypes
import sys
import time
import tkinter as tk

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

W, H = 860, 520
OX, OY = 160, 150


def move(x, y):
    user32.SetCursorPos(int(x), int(y))


def click_down():
    """按下并确认真的按住了（Windows 偶尔会吞掉注入的点击）。"""
    for _ in range(4):
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.02)
        if user32.GetAsyncKeyState(0x0001) & 0x8000:
            return True
    print("[ab] 按下没生效", flush=True)
    return False


def click_up():
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def stroke(root, x0, y0, x1, y1, dur=1.1, steps=22):
    """一笔：按下 → 匀速拖过去 → 抬起。窗口每步都 update，别让它变成「未响应」。"""
    move(x0, y0)
    root.update()
    time.sleep(0.06)
    click_down()
    t0 = time.time()
    for i in range(1, steps + 1):
        k = i / steps
        move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
        root.update()               # 关键：保持窗口活着，否则系统会丢弃点击
        nxt = t0 + dur * k
        time.sleep(max(0, nxt - time.time()))
    click_up()
    root.update()
    time.sleep(0.08)


def main():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{W}x{H}+{OX}+{OY}")
    root.configure(bg="#ffffff")
    tk.Label(root, text="A/B 回放台 · 请勿动鼠标", bg="#ffffff", fg="#999999",
             font=("Microsoft YaHei UI", 9)).pack(pady=8)
    root.update()

    time.sleep(2.0)

    # 前 5 笔：同一组坐标与时长（两次会话完全一致）
    for i in range(5):
        stroke(root, OX + 80 + i * 40, OY + 120, OX + 620 - i * 20, OY + 300 + i * 20)
    print("[ab] 前 5 笔完成", flush=True)

    # 故意停 32 秒：标准档 20 秒触发系统提醒，6 秒后同桌补问号（独桌则没有）
    t_end = time.time() + 32
    while time.time() < t_end:
        root.update()
        time.sleep(0.05)
    print("[ab] 停顿结束", flush=True)

    for i in range(5):
        stroke(root, OX + 100 + i * 30, OY + 380, OX + 700 - i * 30, OY + 200 - i * 15)
    print("[ab] 全部完成", flush=True)

    root.update()
    root.destroy()


if __name__ == "__main__":
    main()
