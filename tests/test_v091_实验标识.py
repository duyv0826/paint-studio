# -*- coding: utf-8 -*-
"""v0.9.1 实验标识与盲化的无头测试（不需要 GUI，不弹窗）。

跑法：
    python 测试\test_v091_实验标识.py        （在 小窗 目录下）

覆盖四条：
  1. --covert 时导出文件名不含 with_peers / system_only 字样（被试看不出条件）
  2. --pid/--session/--piece/--order 写进 export.session，文件名带 P01_S1_
  3. 非盲化时文件名仍带条件字样（日常使用与其他论文归档不受影响）
  4. 独桌条件（peers_on=False）收到问号消息不写 pressure（否则 H2 反应时被污染）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import paint_studio_widget as w  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("[OK]   " + name)
    else:
        print("[FAIL] " + name + ("  → " + detail if detail else ""))
        FAILS.append(name)


class Stub(object):
    """只喂 payload()/export_json() 需要的字段，GUI 相关的用默认值。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, item):
        if item in ("peers", "recent_strokes"):
            return lambda *a, **k: []
        if item in ("avg_speed", "strokes_per_min"):
            return lambda *a, **k: 0.0
        if item == "brush":
            return lambda: {}
        return None


class FakeHook(object):
    ok = False
    scale = 1.0
    manual_scale = None
    refresh_count = 0
    change_count = 0
    maps = []


class FakeTracker(object):
    profile = w.LEVELS["standard"]
    strokes = 0
    undos = 0
    path = 0
    move_t = 0.0
    peak = 0
    cur = 0
    reminders = []
    hook_skew_max = 0.0
    skew_warn = False
    exe = None
    in_draw_app = False

    def avg_speed(self):
        return 0.0

    def strokes_per_min(self, sec):
        return 0.0

    def brush(self):
        return {}

    def recent_strokes(self, n):
        return []


def make_stub(covert, peers_on, pid="P01", session="1", piece="A", order="AB1"):
    s = Stub(
        covert=covert, peers_on=peers_on, launch_solo=not peers_on,
        subject_id=pid or "", session_no=int(session) if session else None,
        piece=piece or "", order_block=order or "",
        tracker=FakeTracker(), mhook=FakeHook(), room_code="302", me="被试",
        pass_code="", room=None, cloud=None, demo=False, pid_field="abc123",
        q_sent=0, q_received=0, pressure=[], pomo_rounds=0, active_sec=0.0,
        started_at=None, ticks=0, t_first_tick=None, pomodoro_min=25,
    )
    s.pid = "abc123"
    s.seen_q = []
    s.flash = lambda *a: (_ for _ in ()).throw(AssertionError("盲化/独桌下不该闪"))
    s.beep = lambda *a: (_ for _ in ()).throw(AssertionError("盲化/独桌下不该响"))
    s.toast = lambda *a: None
    s.payload = lambda: w.Widget.payload(s)
    return s


def run():
    # ---- 1 / 2：盲化 + 实验标识 ----
    s = make_stub(covert=True, peers_on=False)
    p = w.Widget.payload(s)
    name = "自习室_P%s_S%s_standard_%s.json" % (
        p["session"]["participant_id"], p["session"]["session_no"], "20261114_120000")
    tmp = tempfile.mkdtemp()
    real_out = os.path.join(tmp, "导出")
    old_app_dir = w.app_dir
    w.app_dir = lambda: tmp
    try:
        path = w.Widget.export_json(s)
    finally:
        w.app_dir = old_app_dir
    base = os.path.basename(path)
    check("1 盲化文件名不含条件字样",
          ("with_peers" not in base) and ("system_only" not in base), base)
    check("2 文件名带被试场次前缀", base.startswith("自习室_P01_S1_"), base)
    check("2 session 块含 5 个实验字段",
          all(k in p["session"] for k in
              ("participant_id", "session_no", "piece_id", "order_block", "covert")),
          str(list(p["session"].keys())))
    check("2 字段值正确",
          p["session"]["participant_id"] == "P01" and p["session"]["session_no"] == 1
          and p["session"]["piece_id"] == "A" and p["session"]["order_block"] == "AB1"
          and p["session"]["covert"] is True and p["session"]["solo_flag"] is True,
          str({k: p["session"][k] for k in ("participant_id", "session_no", "piece_id",
                                            "order_block", "covert", "solo_flag")}))
    check("2 两份平行数据能靠 pid+session 配对",
          (p["session"]["participant_id"], p["session"]["session_no"]) == ("P01", 1))

    # ---- 3：非盲化（日常使用）文件名不变形 ----
    s2 = make_stub(covert=False, peers_on=False, pid=None, session=None, piece=None, order=None)
    w.app_dir = lambda: tmp
    try:
        base2 = os.path.basename(w.Widget.export_json(s2))
    finally:
        w.app_dir = old_app_dir
    check("3 非盲化文件名仍带 system_only", "system_only" in base2, base2)
    check("3 未填 pid 时不加前缀", not base2.startswith("自习室_P"), base2)
    p2 = w.Widget.payload(s2)
    check("3 未填时字段为 None 而非假值",
          p2["session"]["participant_id"] is None and p2["session"]["piece_id"] is None,
          str(p2["session"]))

    # ---- 4：独桌条件收到问号不写 pressure ----
    s3 = make_stub(covert=True, peers_on=False)
    s3.pressure = []
    msg = {"id": "other", "t": "q", "to": "abc123", "qid": "q1", "room": "302", "name": "阿岩"}
    try:
        w.Widget._on_room_msg(s3, msg, 0.0)
        raised = None
    except AssertionError as e:
        raised = str(e)
    check("4 独桌条件问号被拦下（未触发闪烁/提示）", raised is None, raised or "")
    check("4 pressure 未被写入", s3.pressure == [], str(s3.pressure))


if __name__ == "__main__":
    print("v0.9.1 实验标识测试")
    run()
    print("-" * 40)
    if FAILS:
        print("FAILED %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        sys.exit(1)
    print("ALL PASS")
