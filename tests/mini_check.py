"""迷你态退出入口自检：收成小条后，展开/关闭两个按钮是否真的在窗口可视区内、能不能点。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import paint_studio_widget as P

OUT = os.path.join(ROOT, "测试", "迷你态_修复后.png")
w = P.Widget(demo=True, no_lan=True, no_cloud=True)


def go():
    w.toggle_mini()
    w.root.update_idletasks()
    rw, rh = w.root.winfo_width(), w.root.winfo_height()
    print("mini window:", rw, rh)
    for name, wd in (("expand", w.mini_expand), ("close", w.mini_close)):
        ox = wd.winfo_rootx() - w.root.winfo_rootx()
        oy = wd.winfo_rooty() - w.root.winfo_rooty()
        print(name, "mapped=", wd.winfo_ismapped(),
              "pos=", ox, oy, "size=", wd.winfo_width(), wd.winfo_height(),
              "inside=", 0 <= ox and ox + wd.winfo_width() <= rw
              and 0 <= oy and oy + wd.winfo_height() <= rh)
    w.root.after(900, phase2)


def phase2():
    w._capture(OUT)
    w.toggle_mini()                     # 再展开回 336×306，确认标题条回到顶部
    w.root.update_idletasks()
    print("展开后:", w.root.winfo_width(), w.root.winfo_height(),
          "标题条可见=", w.bar.winfo_ismapped(), "标题条 y=", w.bar.winfo_y(),
          "主体 y=", w.body.winfo_y())
    w.root.after(800, lambda: (w._capture(os.path.join(ROOT, "测试", "迷你态_展开后.png")),
                               w.root.after(300, w.quit)))


w.root.after(700, go)
w.root.mainloop()
print("done")
