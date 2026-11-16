"""复现旧版 152×42 迷你态：测量标题条的 ✕ 与 — 是否落在窗口可视区内。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import paint_studio_widget as P

w = P.Widget(demo=True, no_lan=True, no_cloud=True)


def go():
    # 旧版路径：只藏 body、只显示两行文字、窗口 152×42
    w.mini_close.pack_forget()
    w.mini_expand.pack_forget()
    w.body.pack_forget()
    w.mini_frame.pack(fill="both", expand=True)
    w.root.geometry("152x42")
    w.root.update_idletasks()
    rw, rh = w.root.winfo_width(), w.root.winfo_height()
    print("旧版迷你窗口:", rw, rh)
    for name, wd in (("title-✕", w.btn_close), ("title-—", w.btn_mini), ("clock", w.mini_clock)):
        ox = wd.winfo_rootx() - w.root.winfo_rootx()
        oy = wd.winfo_rooty() - w.root.winfo_rooty()
        print(f"{name} 位置=({ox},{oy}) 尺寸={wd.winfo_width()}x{wd.winfo_height()} "
              f"在窗口内={0 <= ox and ox + wd.winfo_width() <= rw and 0 <= oy and oy + wd.winfo_height() <= rh}")
    w.root.after(500, w.quit)


w.root.after(700, go)
w.root.mainloop()
print("done")
