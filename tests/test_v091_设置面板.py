# -*- coding: utf-8 -*-
"""验证盲化时设置面板里不出现「这一轮有同桌（关＝独桌条件）」勾选框。

需要能开窗的 Python（本机 py -3.12 有 tkinter）。跑法：
    py -3.12 测试\test_v091_设置面板.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import paint_studio_widget as w  # noqa: E402


def texts_of_settings(widget):
    """打开设置面板，取回面板里所有控件的文本。"""
    widget.open_settings()
    win = None
    for c in widget.root.winfo_children():
        if c.winfo_class() == "Toplevel":
            win = c
    if win is None:
        return None
    out = []
    for c in win.winfo_children():
        try:
            out.append((c.winfo_class(), str(c.cget("text"))))
        except Exception:
            out.append((c.winfo_class(), ""))
    win.destroy()
    return out


def main():
    fails = []

    # 盲化：不该有条件勾选框
    ww = w.Widget(demo=False, no_lan=True, no_cloud=True, solo=True, covert=True, name="被试01")
    items = texts_of_settings(ww)
    has_check = any(k == "Checkbutton" and "这一轮有同桌" in t for k, t in items)
    print("[%s] 盲化时无勾选框 → %s" % ("OK" if not has_check else "FAIL", items))
    if has_check:
        fails.append("covert 下仍出现条件勾选框")
    # 顺带确认没把其它控件弄丢
    need = ["昵称", "口令"]
    for kw in need:
        if not any(kw in t for _, t in items):
            fails.append("设置面板丢了「%s」这一项" % kw)
            print("[FAIL] 设置面板丢了「%s」" % kw)
    ww.root.after(200, ww.root.destroy)
    try:
        ww.root.mainloop()
    except Exception:
        pass

    # 非盲化（日常/有同桌条件）：必须还在
    w2 = w.Widget(demo=False, no_lan=True, no_cloud=True, solo=False, covert=False, name="被试01")
    items2 = texts_of_settings(w2)
    has2 = any(k == "Checkbutton" and "这一轮有同桌" in t for k, t in items2)
    print("[%s] 非盲化时勾选框仍在" % ("OK" if has2 else "FAIL"))
    if not has2:
        fails.append("非盲化时勾选框被误删")
    w2.root.after(200, w2.root.destroy)
    try:
        w2.root.mainloop()
    except Exception:
        pass

    print("-" * 40)
    if fails:
        print("FAILED: " + "; ".join(fails))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
