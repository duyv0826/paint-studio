# -*- coding: utf-8 -*-
"""
绘画自习室 · 桌面悬浮小窗 v0.9
=================================
按录音 17 的原话做的形态：
  「它不是一个这样的页面，就是一个小窗悬浮在上面，它检测的是我（的）绘画软件」
  「它只是一个平台，它不是在那个里面画的，是监测我自己的」
  「直接读取鼠标的，数位板在 Windows 这边其实就相当于鼠标」
  「识别我的撤回步数啊，或者我画了多少笔啊……每分钟花了多少」

所以这个程序：
  1. 是一个无边框、置顶、可拖动的小窗，始终浮在你的绘画软件上方；
  2. 不在它里面画画——它读 Windows 全局指针（数位板落笔＝左键按下）与 Ctrl+Z，
     把连续输入切成一笔一笔的「笔触」（时长/长度/速度/停顿/直线度/撤回归属）；
  3. 只在你指定的绘画软件在前台时计入（默认开启，可用 --anywhere 关掉）；
  4. 番茄钟 + 静默/笔速断崖提醒 + 自习桌（局域网内真实同桌，可直接发问号）。

零依赖：只用 Python 标准库（tkinter + ctypes + socket）。

用法：
  python paint_studio_widget.py                        # 启动小窗（默认房间 302，昵称「我」）
  python paint_studio_widget.py --name 小林 --room 302   # 指定昵称与自习桌号
  python paint_studio_widget.py --anywhere             # 不限于绘画软件，全局都算
  python paint_studio_widget.py --selftest             # 跑核心逻辑自检（不开窗）
  python paint_studio_widget.py --probe 5              # 只读 5 秒全局指针，打印计数
  python paint_studio_widget.py --demo --exit-after 6  # 演示数据（截图用）

实验条件控制（v0.9.1 加入，供被试内 A/B 使用）：
  --solo   --covert          独桌条件下仍显示「同桌（离线）」座位行，不出现「独桌」字样，
                             界面在视觉上与有同桌条件等量，避免被试看出条件（需求特征污染）
  --covert-peers 阿岩,老周    盲化时占位用的同桌名字：填成被试那位同桌的真实昵称更像真的
  --pid P01 --session 1 --piece A --order AB1
                            把被试编号 / 场次 / 用的哪张练习稿 / ABBA 块写进导出，
                             文件名也带 P01_S1，20 多场次不会配错对
"""
import collections
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import math
import os
import queue
import select
import socket
import sys
import threading
import time
import random
import uuid
from datetime import datetime

APP = "绘画自习室 · 桌面悬浮小窗 v0.9（笔触 + 自习桌）"

LEVELS = {
    "gentle":   {"key": "gentle",   "label": "温柔", "idle": 60, "cliff_ratio": 0.28, "cliff_dwell": 4, "cooldown": 45},
    "standard": {"key": "standard", "label": "标准", "idle": 20, "cliff_ratio": 0.35, "cliff_dwell": 3, "cooldown": 30},
    "strict":   {"key": "strict",   "label": "严厉", "idle": 10, "cliff_ratio": 0.50, "cliff_dwell": 2, "cooldown": 15},
}
CLIFF_MIN_PEAK = 300          # px/s：峰值太低不判断崖（那是慢速细描）
SAMPLE_HZ = 30

# 盲化（--covert）时独桌条件用来占位的「同桌」名字：与有同桌条件的座位行视觉等量
COVERT_PEERS = ("阿岩", "小林")

# 常见绘画软件（前台进程名，小写）。不在表里也能用 --anywhere，或改这个集合。
DRAW_EXES = {
    "photoshop.exe", "painter.exe", "painter64.exe", "painter 2021.exe",
    "clipstudiopaint.exe", "sai2.exe", "sai.exe", "krita.exe",
    "medibangpaintpro.exe", "medibangpaint.exe", "firealpaca.exe",
    "illustrator.exe", "coreldrw.exe", "painttool sai.exe",
    "paint.exe", "mspaint.exe", "sketchbook.exe", "artrage.exe",
    "procreate.exe", "concepts.exe", "affinity photo.exe", "affinityphoto.exe",
    "photo.exe", "designer.exe", "hejiamian.exe", "huihua.exe",
}


# ══════════════ 自习桌（局域网组播，无服务器） ══════════════
class Room:
    """局域网通道：同一 Wi-Fi 下自动互相看见，不需要后端、不需要账号。

    组播地址 239.255.60.60:47826，TTL=1，开 LOOPBACK（同一台机器开两个实例也能互相看见，方便测试）。
    """
    GROUP = "239.255.60.60"
    PORT = 47826

    def __init__(self, code, name, pid=None):
        self.id = pid or uuid.uuid4().hex[:6]
        self.code = str(code)
        self.name = name
        self.peers = {}          # id -> {name, state, strokes, undo, reminded, last_seen, ...}
        self.inbox = queue.Queue()
        self.alive = True
        self.sock = None
        self._open()

    def _open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass
        s.bind(("", self.PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(self.GROUP) + socket.inet_aton("0.0.0.0"))
        s.setblocking(False)
        self.sock = s
        threading.Thread(target=self._recv, daemon=True).start()

    def _recv(self):
        while self.alive:
            try:
                r, _, _ = select.select([self.sock], [], [], 0.4)
                if not r:
                    continue
                data, _addr = self.sock.recvfrom(2048)
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("room") != self.code:
                continue
            if msg.get("id") == self.id:
                continue
            msg["_seen"] = time.time()
            self.inbox.put(msg)

    def send(self, msg):
        if not self.sock:
            return
        msg = dict(msg)
        msg.setdefault("id", self.id)
        msg["room"] = self.code
        msg.setdefault("name", self.name)
        msg.setdefault("ts", time.time())
        try:
            self.sock.sendto(json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                             (self.GROUP, self.PORT))
        except Exception:
            pass

    def close(self):
        self.alive = False
        try:
            self.send({"t": "bye"})
            self.sock.close()
        except Exception:
            pass
        self.sock = None


class Cloud:
    """公网通道：同一桌号，人在任何网络都能互相看见（公共 MQTT / TLS）。

    三个公共 broker 依次试，哪个通就用哪个；一个 broker 断久了自动换下一个。
    桌号可以再加一个口令——主题变成「桌号+口令」的哈希，光猜到桌号进不来。
    """
    BROKERS = [("broker.emqx.io", 8883),
               ("broker.hivemq.com", 8883),
               ("test.mosquitto.org", 8883)]
    PORT = 8883
    PREFIX = "huihua-zixishi/2026"
    SWITCH_AFTER = 25          # 断了这么久还不回来，就换下一个 broker

    def __init__(self, code, secret=""):
        self.code = str(code)
        self.secret = secret or ""
        self.topic = self._topic()
        self.inbox = queue.Queue()
        self.ready = False
        self.alive = True
        self.client = None
        self.broker = ""
        self.err = ""
        self._host_override = os.environ.get("ZIXI_MQTT_HOST", "").strip()
        threading.Thread(target=self._run, daemon=True).start()

    def _topic(self):
        if not self.secret:
            return f"{self.PREFIX}/{self.code}"
        tag = hashlib.sha256((self.code + "|" + self.secret).encode("utf-8")).hexdigest()[:16]
        return f"{self.PREFIX}/{tag}"

    def _brokers(self):
        if self._host_override:
            return [(self._host_override, self.PORT)]
        return self.BROKERS

    def _run(self):
        try:
            import paho.mqtt.client as mqtt
        except Exception as e:
            self.err = f"no paho: {e}"
            return
        idx = 0
        while self.alive:
            brokers = self._brokers()
            host, port = brokers[idx % len(brokers)]
            self.broker = f"{host}:{port}"
            try:
                kw = {}
                try:
                    kw["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
                except Exception:
                    pass
                c = mqtt.Client(client_id="zixi-" + uuid.uuid4().hex[:10], clean_session=True, **kw)
                c.tls_set()
                c.on_connect = self._on_connect
                c.on_disconnect = self._on_disconnect
                c.on_message = self._on_message
                c.connect(host, port, 30)
                self.client = c
                c.loop_start()
                last_ready = time.time()
                while self.alive:
                    time.sleep(1)
                    if self.ready:
                        last_ready = time.time()
                    elif time.time() - last_ready > self.SWITCH_AFTER:
                        self.err = f"{host} 没回来，换下一个"
                        break
                if self.alive:
                    c.loop_stop()
                    try:
                        c.disconnect()
                    except Exception:
                        pass
                    idx += 1
            except Exception as e:
                self.err = (type(e).__name__ + ": " + str(e))[:120]
                idx += 1
            self.ready = False
            time.sleep(3)

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if int(getattr(rc, "value", rc)) == 0:
            client.subscribe(self.topic, qos=0)
            self.err = ""
            self.ready = True

    def _on_disconnect(self, *a):
        self.ready = False

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        if isinstance(data, dict) and data.get("room") == self.code:
            data["_seen"] = time.time()
            data["_via"] = "cloud"
            self.inbox.put(data)

    def send(self, msg):
        if not self.ready or not self.client:
            return
        try:
            self.client.publish(self.topic, json.dumps(msg, ensure_ascii=False), qos=0)
        except Exception:
            pass

    def close(self):
        self.alive = False
        try:
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()
        except Exception:
            pass

# ══════════════ Windows 全局输入 ══════════════
class Win32:
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.pt = wt.POINT()

    def cursor(self):
        if not self.user32.GetCursorPos(ctypes.byref(self.pt)):
            return None
        return self.pt.x, self.pt.y

    def key_down(self, vk):
        # 0x8000 = 当前按下
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def foreground_exe(self):
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wt.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wt.DWORD(512)
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            self.kernel32.CloseHandle(handle)
        return ""

    def screen_size(self):
        return self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)


class DEVMODEW(ctypes.Structure):
    """EnumDisplaySettingsW 用的 DEVMODEW（只要模式与位置字段，其余按原样占位）。"""
    _fields_ = [("dmDeviceName", ctypes.c_wchar * 32),
                ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD),
                ("dmPosition", wt.POINT),
                ("dmDisplayOrientation", wt.DWORD), ("dmDisplayFixedOutput", wt.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", ctypes.c_wchar * 32),
                ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
                ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD),
                ("dmDisplayFlags", wt.DWORD), ("dmDisplayFrequency", wt.DWORD),
                ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
                ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD),
                ("dmReserved1", wt.DWORD), ("dmReserved2", wt.DWORD),
                ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD)]


class MouseHook:
    """WH_MOUSE_LL：在按下/抬起的那一刻取到真实坐标。

    轮询只能保证「下一个 tick 才看见边沿」，最多丢掉一个采样间隔的运动量
    （500 px/s × 47 ms ≈ 24 px），起笔点还会被记到错的位置。钩子把这一截补回来。
    只记录、不拦截（一律 CallNextHookEx）；装不上就 ok=False，自动退回轮询。
    """

    def __init__(self):
        self.ok = False
        self.err = ""
        self.events = collections.deque(maxlen=16)      # [(kind, t, x, y)]
        self._hook = None
        self._cb = None
        self._thread = None
        self.scale = 1.0                                 # 主屏的钩子物理像素 → 本进程坐标的系数
        self.manual_scale = None                         # --hook-scale 手动指定时优先用它
        self.maps = []                                   # 每块屏的物理/虚拟矩形与系数
        self.maps_ts = 0.0                               # 换算表读过的时间（用于插拔后自动重读）
        self.refresh_count = 0                           # 重读了几次（例行重读也计数）
        self.change_count = 0                            # 换算表内容变了几次（屏配置真的变了）
        self._maps_sig = None

    @staticmethod
    def _coord_scale():
        """主屏的物理像素 → GetCursorPos 坐标空间的换算系数。

        实测（本机 3840×2160 / 200%）：SetCursorPos(300,300) 后 GetCursorPos 报 300，
        而 WH_MOUSE_LL 报 600 —— 未声明 DPI 感知的进程被虚拟化，钩子拿的是物理像素。
        DPI 相关 API（GetDpiForSystem / GetDpiForWindow）对这类进程一律报 96，不可信；
        改用「面板真实像素 / 进程看到的度量」求比值：感知进程两者相等→1.0，未感知→<1。
        """
        try:
            u = ctypes.windll.user32
            dm = DEVMODEW()
            dm.dmSize = ctypes.sizeof(dm)
            if u.EnumDisplaySettingsW(None, 0xFFFFFFFF, ctypes.byref(dm)):   # ENUM_CURRENT_SETTINGS
                real_w = int(dm.dmPelsWidth)
                seen_w = int(u.GetSystemMetrics(0))
                if real_w > 0 and seen_w > 0 and real_w > seen_w:
                    return seen_w / real_w
        except Exception:
            pass
        return 1.0

    @staticmethod
    def _monitor_maps():
        """列出每块屏：虚拟矩形（本进程看到的位置）+ 物理矩形（模式里的位置/像素）。

        实测两台屏（DISPLAY2 4K@200%、DISPLAY1 2560×1440@150%）：
        虚拟宽度 1920/1707，物理宽度 3840/2560，一比就是各自的系数；
        再配物理原点，才能把钩子给的物理坐标换算回本进程的虚拟坐标。
        """
        u = ctypes.windll.user32

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                        ("dwFlags", wt.DWORD), ("szDevice", ctypes.c_wchar * 32)]

        maps = []

        def _enum(hmon, hdc, rect, data):
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(mi)
            try:
                if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                    dm = DEVMODEW()
                    dm.dmSize = ctypes.sizeof(dm)
                    if u.EnumDisplaySettingsW(mi.szDevice, 0xFFFFFFFF, ctypes.byref(dm)):
                        vr = (mi.rcMonitor.left, mi.rcMonitor.top,
                              mi.rcMonitor.right, mi.rcMonitor.bottom)
                        pr = (dm.dmPosition.x, dm.dmPosition.y,
                              dm.dmPosition.x + int(dm.dmPelsWidth),
                              dm.dmPosition.y + int(dm.dmPelsHeight))
                        if vr[2] > vr[0] and pr[2] > pr[0]:
                            maps.append({"name": mi.szDevice, "virt": vr, "phys": pr,
                                         "primary": bool(mi.dwFlags & 1),
                                         "kx": (vr[2] - vr[0]) / (pr[2] - pr[0]),
                                         "ky": (vr[3] - vr[1]) / (pr[3] - pr[1])})
            except Exception:
                pass
            return 1

        try:
            u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFOEXW)]
            u.EnumDisplayMonitors(None, None,
                ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(wt.RECT), ctypes.c_void_p)(_enum), None)
        except Exception:
            return []
        return maps

    @staticmethod
    def map_point(maps, px, py, fallback=1.0):
        """物理像素 → 本进程坐标：按所在屏各自的系数与原点换算。落在所有屏外则退回主屏系数。"""
        hit = MouseHook._find(maps, px, py)
        if hit:
            return hit
        return px * fallback, py * fallback

    @staticmethod
    def _find(maps, px, py):
        for m in maps or ():
            pl, ptop, pright, pbottom = m["phys"]
            if pl <= px <= pright and ptop <= py <= pbottom:
                vl, vtop = m["virt"][0], m["virt"][1]
                return vl + (px - pl) * m["kx"], vtop + (py - ptop) * m["ky"]
        return None

    def refresh(self, min_gap=0.0):
        """重读换算表：插拔显示器、改缩放、旋转后调用。min_gap 用来节流，避免每次事件都查一遍。"""
        if time.time() - self.maps_ts < min_gap:
            return False
        new = self._monitor_maps()
        if not new:
            return False
        sig = tuple((m["name"], m["phys"], m["virt"]) for m in new)
        if self._maps_sig is not None and sig != self._maps_sig:
            self.change_count += 1                      # 屏配置真的变了（插拔/改缩放/旋转）
        self._maps_sig = sig
        self.maps = new
        self.maps_ts = time.time()
        self.refresh_count += 1
        for m in self.maps:
            if m["primary"]:
                self.scale = m["kx"]
        return True

    def _to_virtual(self, px, py):
        """按下点的物理坐标 → 本进程坐标。

        换算表在启动时读一次，另外两个时机会重读：
        ① 距上次读超过 10 秒（缩放/旋转中途被改过时能自己纠正）；
        ② 按下点落在所有已知屏之外（典型是插了新屏）。
        """
        if self.manual_scale:                            # --hook-scale 手动钉死：不查表，也不重读
            return px * self.manual_scale, py * self.manual_scale
        if time.time() - self.maps_ts > 10.0:
            self.refresh(min_gap=5.0)
        hit = self._find(self.maps, px, py)
        if hit is None:
            self.refresh(min_gap=2.0)
            hit = self._find(self.maps, px, py)
        if hit is None:
            return px * self.scale, py * self.scale
        return hit

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="mouse-hook")
        self._thread.start()
        for _ in range(40):                              # 最多等 2 秒让钩子装好
            if self.ok or self.err:
                break
            time.sleep(0.05)
        return self.ok

    def last(self, kind):
        """最近一次按下/抬起：返回 (t, x, y) 或 None（不做时间过滤，由调用方按自己的 t 判断）。"""
        for ev in reversed(self.events):
            if ev[0] == kind:
                return ev[1], ev[2], ev[3]
        return None

    def _run(self):
        try:
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                      ctypes.c_size_t, ctypes.c_size_t)

            class MSLL(ctypes.Structure):
                _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                            ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

            u.SetWindowsHookExW.restype = ctypes.c_void_p
            u.SetWindowsHookExW.argtypes = [ctypes.c_int, PROC, ctypes.c_void_p, ctypes.c_uint]
            u.CallNextHookEx.restype = ctypes.c_ssize_t
            u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                         ctypes.c_size_t, ctypes.c_size_t]
            u.GetMessageW.restype = ctypes.c_int
            u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), ctypes.c_void_p,
                                      ctypes.c_uint, ctypes.c_uint]

            def _cb(ncode, wparam, lparam):
                if ncode == 0 and wparam in (0x0201, 0x0202):       # LEFTDOWN / LEFTUP
                    try:
                        ms = ctypes.cast(lparam, ctypes.POINTER(MSLL)).contents
                        vx, vy = self._to_virtual(ms.pt.x, ms.pt.y)
                        self.events.append(("down" if wparam == 0x0201 else "up", time.time(),
                                            int(round(vx)), int(round(vy))))
                    except Exception:
                        pass
                return u.CallNextHookEx(getattr(self, "_hook", None), ncode, wparam, lparam)

            self.scale = self.manual_scale if self.manual_scale else self._coord_scale()
            if not self.manual_scale:
                self.refresh()                                 # 每块屏各自的系数
                if not self.maps:
                    self.maps_ts = time.time()
            self._cb = PROC(_cb)                                  # 必须留引用，否则被回收
            self._hook = u.SetWindowsHookExW(14, self._cb, None, 0)  # WH_MOUSE_LL
            if not self._hook:
                self.err = f"SetWindowsHookExW 失败 err={k.GetLastError()}"
                return
            self.ok = True
            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            self.err = repr(e)


# ══════════════ 核心：把指针采样变成「画画行为」 ══════════════
class Tracker:
    """纯逻辑，可脱离 GUI 单独喂样本测试（--selftest 就是喂它）。"""

    def __init__(self, profile=None):
        self.profile = profile or LEVELS["standard"]
        self.reset()

    def reset(self):
        self.pos = None
        self.t_last = None
        self.down = False
        self.last_down = False
        self.ctrl_last = False
        self.z_last = False

        self.strokes = 0
        self.undos = 0
        self.path = 0.0
        self.move_t = 0.0
        self.peak = 0.0
        self.cur = 0.0
        self.hist = []                 # [(t, speed)] 近 20s
        self.last_pen_t = time.time()  # 最后一次落笔移动或起笔
        self.drawing = False
        self.exe = ""
        self.in_draw_app = True

        # 笔触（stroke）：一笔一记，这是原话里说的「检测笔触」的最小单位
        self.stroke_list = []          # 每笔特征，最多留 2000 笔
        self.cur_stroke = None         # 正在进行的这一笔
        self.last_undo_t = 0.0
        self.press_src = None          # 可选：按下/抬起瞬间的真实坐标来源（MouseHook）
        self.release_src = None
        self.hook_skew_max = 0.0       # 钩子坐标与当拍光标坐标的最大偏差（尺子错位的现场证据）
        self.skew_warn = False

        self.reminders = []            # [{signal, at, delay_ms, exe}]
        self.pending = None
        self.armed = True
        self.last_reminder_t = 0.0
        self.cliff_since = 0.0
        self.state = "idle"            # idle / draw / fish / out

    # ---- 一个采样 ----
    def sample(self, t, x, y, down, ctrl, z, exe="", count_enabled=True):
        if self.t_last is None:
            self.t_last, self.pos = t, (x, y)
            self.last_down, self.ctrl_last, self.z_last = down, ctrl, z
            self.last_pen_t = t
            return

        dt = max(1e-3, t - self.t_last)
        dist = math.hypot(x - self.pos[0], y - self.pos[1])
        self.t_last, self.pos = t, (x, y)

        if count_enabled:
            # 落笔起始边沿（数位板落笔＝左键按下）→ 开一笔
            if down and not self.last_down:
                self.strokes += 1
                self.cur = 0.0
                x0, y0, pre, t_press = self._press_origin(t, x, y)
                self.last_pen_t = t_press
                self.pen_back(t)      # 重新落笔＝重新武装 + 补上一条提醒的 delay_ms
                if pre >= 0.5:        # 补回「按下 → 首个 tick」之间已经走过的距离
                    self.path += pre
                    self.move_t += max(1e-3, t - t_press)
                self.cur_stroke = {"i": len(self.stroke_list) + 1, "t0": t_press, "x0": x0, "y0": y0,
                                   "x1": x, "y1": y, "path": pre, "dur": 0.0, "peak": 0.0,
                                   "pauses": 0, "last_mv": t,
                                   "samples": 1 if pre >= 0.5 else 0, "undo": False}
            # 落笔中移动
            if down and self.last_down and dist >= 0.5:
                inst = dist / dt
                self.cur = self.cur * 0.7 + inst * 0.3 if self.cur else inst
                self.peak = max(self.peak, self.cur)
                self.path += dist
                self.move_t += dt
                self.last_pen_t = t
                st = self.cur_stroke
                if st is not None:
                    st["path"] += dist
                    st["x1"], st["y1"] = x, y
                    st["peak"] = max(st["peak"], self.cur)
                    st["samples"] += 1
                    if t - st["last_mv"] > 0.25:     # 笔内停顿（悬笔犹豫）
                        st["pauses"] += 1
                    st["last_mv"] = t
            # 抬笔 → 收笔，落成一条笔触记录
            if not down and self.last_down:
                t_up, up = self._release_origin(t)
                self.last_pen_t = t_up
                if self.cur_stroke is not None and up is not None:
                    self.cur_stroke["x1"], self.cur_stroke["y1"] = up   # 终点也用抬起瞬间的真实坐标
                self.close_stroke(t_up)

            # 撤回：Ctrl + Z 的按下边沿（全局键盘，不需要软件授权）
            if ctrl and z and not self.z_last:
                self.undos += 1
                self.last_undo_t = t
                self.mark_undo(t)

        # 速度衰减 + 20s 采样
        if t - self.last_pen_t > 0.22:
            self.cur *= 0.85
            if self.cur < 1:
                self.cur = 0.0
        if not self.hist or t - self.hist[-1][0] >= 0.25:
            self.hist.append((t, self.cur))
        while self.hist and t - self.hist[0][0] > 20:
            self.hist.pop(0)

        self.last_down, self.ctrl_last, self.z_last = down, ctrl, z
        self.exe = exe
        self.in_draw_app = count_enabled
        self._update_state(t, down)

    def _update_state(self, t, down):
        if not self.in_draw_app and self.profile is not None and self.restrict:
            self.state = "out"
        elif t - self.last_pen_t <= 3:
            self.state = "draw"
        else:
            self.state = "idle"

    restrict = True

    # ---- 落笔/抬笔的真实时刻与坐标（有钩子时用钩子，没有就退回 tick） ----
    def _press_origin(self, t, x, y):
        if self.press_src:
            ev = self.press_src()
            if ev and abs(t - ev[0]) < 0.6 and ev[0] <= t + 0.002:
                px, py = ev[1], ev[2]
                # 尺子校验：按下点到当拍光标正常只差一个采样间隔（500 px/s × 47 ms ≈ 25 px）。
                # 差出上百像素说明两套坐标空间的换算系数不对（典型：副屏缩放与主屏不同），
                # 这时不静默，导出里落一个 warn 供事后剔除。
                skew = max(abs(x - px), abs(y - py))
                self.hook_skew_max = max(self.hook_skew_max, skew)
                if skew > 120:
                    self.skew_warn = True
                    return x, y, 0.0, t          # 尺子明显错位时宁可退回当拍坐标
                return px, py, math.hypot(x - px, y - py), ev[0]
        return x, y, 0.0, t

    def _release_origin(self, t):
        if self.release_src:
            ev = self.release_src()
            if ev and abs(t - ev[0]) < 0.6:
                return ev[0], (ev[1], ev[2])
        return t, None

    # ---- 守护判定 ----
    def check(self, t, enabled=True):
        """返回 (signal, extra) 或 None；同时把 pending 的 delay_ms 补上。"""
        if not enabled or not self.armed:
            return None
        if t - self.last_reminder_t < self.profile["cooldown"]:
            return None

        if self.in_draw_app and t - self.last_pen_t > self.profile["idle"]:
            return self._fire(t, "idle", {})
        if self.in_draw_app:
            recent_peak = max((v for _, v in self.hist), default=0.0)
            drawing = (t - self.last_pen_t) < 3
            if drawing and recent_peak >= CLIFF_MIN_PEAK and self.cur > 20 and self.cur < recent_peak * self.profile["cliff_ratio"]:
                if not self.cliff_since:
                    self.cliff_since = t
                elif t - self.cliff_since > self.profile["cliff_dwell"]:
                    return self._fire(t, "cliff", {"recent_peak": round(recent_peak), "now": round(self.cur)})
            elif self.cur > 20:
                self.cliff_since = 0.0
        return None

    def _fire(self, t, signal, extra):
        self.last_reminder_t = t
        self.armed = False
        rec = {"signal": signal, "at": datetime.fromtimestamp(t).isoformat(timespec="seconds"),
               "delay_ms": None, "exe": self.exe}
        rec.update(extra)
        self.reminders.append(rec)
        self.pending = rec
        self.cliff_since = 0.0
        self.state = "fish"
        return signal, rec

    def pen_back(self, t):
        """重新落笔：补 delay_ms 并重新武装。"""
        resumed = False
        if self.pending is not None:
            self.pending["delay_ms"] = int((t - self.last_reminder_t) * 1000)
            self.pending = None
            resumed = True
        self.armed = True
        return resumed

    # ---- 派生指标 ----
    def strokes_per_min(self, active_sec):
        return round(self.strokes / max(active_sec / 60.0, 0.05), 1)

    def avg_speed(self):
        return int(self.path / self.move_t) if self.move_t > 0.2 else 0

    def session_sps(self, active_sec):
        return round(self.strokes / max(active_sec, 1.0), 2)

    # ---- 笔触：一笔一记 ----
    def close_stroke(self, t):
        st, self.cur_stroke = self.cur_stroke, None
        if st is None:
            return
        st["t1"] = t
        st["dur"] = round(max(0.0, t - st["t0"]), 3)
        st["path"] = round(st["path"], 1)
        chord = math.hypot(st["x1"] - st["x0"], st["y1"] - st["y0"])
        st["straight"] = round(chord / st["path"], 3) if st["path"] > 1 else 0.0
        st["avg_v"] = int(st["path"] / st["dur"]) if st["dur"] > 0.05 else 0
        for k in ("x0", "y0", "x1", "y1"):
            st[k] = int(st[k])
        st["at"] = datetime.fromtimestamp(st["t0"]).isoformat(timespec="seconds")
        self.stroke_list.append(st)
        if len(self.stroke_list) > 2000:
            self.stroke_list.pop(0)

    def mark_undo(self, t):
        """撤回打在它前后 3 秒内的那笔上（判断「这一笔画废了」）。"""
        for st in reversed(self.stroke_list[-6:]):
            if t - st["t0"] <= 3.0:
                st["undo"] = True
                break

    def brush(self):
        """笔触级聚合：原话要的「笔触」，落到可算的字段上。"""
        ss = self.stroke_list
        if not ss:
            return {"stroke_count": 0, "avg_len_px": 0, "median_len_px": 0, "max_len_px": 0,
                    "median_dur_s": 0.0, "short_ratio": 0.0, "undo_rate": 0.0, "undo_strokes": 0,
                    "median_gap_s": None, "rhythm_cv": None, "rhythm_mad_ratio": None,
                    "gap_p90_s": None, "long_gaps": 0, "pauses_total": 0, "straight_median": None}
        lens = sorted(s["path"] for s in ss)
        durs = sorted(s["dur"] for s in ss)
        raw_gaps = [max(0.0, ss[i]["t0"] - ss[i - 1]["t1"]) for i in range(1, len(ss))]
        gaps = sorted(raw_gaps)
        strs = sorted(s["straight"] for s in ss)
        undo_strokes = sum(1 for s in ss if s.get("undo"))
        cv = None
        if len(gaps) >= 3:
            mean = sum(gaps) / len(gaps)
            if mean > 0:
                var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
                cv = round((var ** 0.5) / mean, 2)
        # 稳健口径：MAD/中位数 + p90 + 超长间隔计数。
        # rhythm_cv 对单个长间隔极敏感（一次 30s 停顿就能把整场判成「散」），仅留作对照。
        med = gaps[len(gaps) // 2] if gaps else None
        devs = sorted(abs(g - med) for g in gaps) if gaps else []
        mad = devs[len(devs) // 2] if devs else 0.0
        mad_ratio = round(1.4826 * mad / med, 2) if (med and med > 0) else None
        p90 = round(gaps[int(0.9 * (len(gaps) - 1))], 2) if gaps else None
        long_gaps = sum(1 for g in gaps if med and med > 0 and g >= 2 * med)
        return {
            "stroke_count": len(ss),
            "avg_len_px": int(sum(lens) / len(lens)),
            "median_len_px": int(lens[len(lens) // 2]),
            "max_len_px": int(lens[-1]),
            "median_dur_s": round(durs[len(durs) // 2], 2),
            "short_ratio": round(sum(1 for v in lens if v < 20) / len(lens), 2),
            "undo_rate": round(self.undos / len(ss), 2),
            "undo_strokes": undo_strokes,
            "median_gap_s": round(med, 2) if med is not None else None,
            "rhythm_cv": cv,
            "rhythm_mad_ratio": mad_ratio,
            "gap_p90_s": p90,
            "long_gaps": long_gaps,
            "pauses_total": sum(s["pauses"] for s in ss),
            "straight_median": round(strs[len(strs) // 2], 2) if strs else None,
        }

    def recent_strokes(self, n=300):
        return [dict(s) for s in self.stroke_list[-n:]]


# ══════════════ UI ══════════════
# 两套配色：formal＝正式版（默认）；paper＝旧文创观感，`--theme paper` 可整窗回退
THEMES = {
    "formal": {
        "PAPER": "#f2f4f6", "PAPER_HI": "#ffffff", "INK": "#1b1f24",
        "INK_SOFT": "#4e555c", "INK_FAINT": "#8a9099",
        "ZHU": "#b03a2e", "ZHU_SOFT": "#f7e8e5",
        "LV": "#1f6f5c", "LV_SOFT": "#e7f1ec", "LINE": "#dce0e4",
        "MARK": "#1b1f24",
        "F_UI": ("Microsoft YaHei UI", 9), "F_SM": ("Microsoft YaHei UI", 8),
        "F_BIG": ("Segoe UI", 21, "bold"), "F_KAI": ("Microsoft YaHei UI", 11, "bold"),
        "F_NUM": ("Segoe UI", 14, "bold"),
    },
    "paper": {
        "PAPER": "#f7efdc", "PAPER_HI": "#fbf8f1", "INK": "#23282c",
        "INK_SOFT": "#4a4438", "INK_FAINT": "#8a8272",
        "ZHU": "#b4482f", "ZHU_SOFT": "#f8e9e3",
        "LV": "#2f6f61", "LV_SOFT": "#e8f1eb", "LINE": "#d9cfba",
        "MARK": "#b4482f",
        "F_UI": ("Microsoft YaHei UI", 9), "F_SM": ("Microsoft YaHei UI", 8),
        "F_BIG": ("KaiTi", 22), "F_KAI": ("KaiTi", 12),
        "F_NUM": ("KaiTi", 14),
    },
}
_theme_name = "formal"
if "--theme" in sys.argv:
    _i = sys.argv.index("--theme")
    if _i + 1 < len(sys.argv) and sys.argv[_i + 1] in THEMES:
        _theme_name = sys.argv[_i + 1]
_TH = THEMES[_theme_name]
PAPER, PAPER_HI, INK = _TH["PAPER"], _TH["PAPER_HI"], _TH["INK"]
INK_SOFT, INK_FAINT, LINE = _TH["INK_SOFT"], _TH["INK_FAINT"], _TH["LINE"]
ZHU, ZHU_SOFT, LV, LV_SOFT = _TH["ZHU"], _TH["ZHU_SOFT"], _TH["LV"], _TH["LV_SOFT"]
F_UI, F_SM, F_BIG, F_KAI = _TH["F_UI"], _TH["F_SM"], _TH["F_BIG"], _TH["F_KAI"]
F_NUM = _TH["F_NUM"]
MARK = _TH["MARK"]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def app_dir():
    """源码运行时＝脚本目录；打包成 exe 后＝exe 所在目录（配置和导出都留在人手边）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(app_dir(), "自习室配置.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


class Widget:
    def __init__(self, anywhere=False, demo=False, exit_after=None, snapshot=None,
                 name=None, room_code=None, snap_delay=1.5, qtest=None,
                 no_lan=False, no_cloud=False, pass_code=None, solo=False,
                 covert=False, subject_id=None, session_no=None, piece=None, order_block=None,
                 covert_peers=None):
        import tkinter as tk
        self.tk = tk
        self.win32 = Win32()
        self.tracker = Tracker()
        self.tracker.restrict = not anywhere
        # 落笔/抬笔的真实坐标：低层鼠标钩子（只记录不拦截）；装不上或 --no-hook 时退回轮询
        self.mhook = None
        if not demo and "--no-hook" not in sys.argv:
            self.mhook = MouseHook()
            if "--hook-scale" in sys.argv:      # 手动钉死换算系数（多显示器/异形缩放时用）
                try:
                    i = sys.argv.index("--hook-scale")
                    self.mhook.manual_scale = float(sys.argv[i + 1])
                except Exception:
                    pass
            if self.mhook.start():
                self.tracker.press_src = lambda: self.mhook.last("down")
                self.tracker.release_src = lambda: self.mhook.last("up")
        self.anywhere = anywhere
        self.demo = demo
        self.exit_after = exit_after

        self.cfg = load_config()
        self.me = (name or self.cfg.get("name") or "我").strip()[:8] or "我"
        self.room_code = str(room_code or self.cfg.get("room") or "302")[:6]
        self.pass_code = str(pass_code if pass_code is not None else (self.cfg.get("pass") or ""))[:24]
        self.room = None
        self.cloud = None
        self.no_lan = no_lan
        self.no_cloud = no_cloud
        # ---------- 实验标识（被试内 A/B 配对用；不填不影响日常使用） ----------
        self.launch_solo = bool(solo)
        self.covert = bool(covert) and bool(solo)   # 盲化只在独桌条件有意义
        self.covert_peers = tuple(
            [x.strip()[:3] for x in str(covert_peers).split(",") if x.strip()][:3]
        ) if covert_peers else COVERT_PEERS
        self.subject_id = (str(subject_id).strip()[:16] if subject_id else "")
        s_no = str(session_no or "").strip()
        self.session_no = int(s_no) if s_no.isdigit() else (1 if self.subject_id else None)
        self.piece = (str(piece).strip()[:8] if piece else "")
        self.order_block = (str(order_block).strip()[:8] if order_block else "")
        self.pid = uuid.uuid4().hex[:6]
        self.peer_table = {}
        self.seen_q = collections.deque(maxlen=200)

        self.pomodoro_min = 25
        self.remaining = self.pomodoro_min * 60
        self.running = False
        self.started_at = None
        self.active_sec = 0.0
        self._act_last = None      # 上次累加 active_sec 的真实时刻（按时间差累加，不按 tick 数）
        self.ticks = 0             # 实际跑过多少个采样 tick
        self.t_first_tick = None
        self.pomo_rounds = 0
        self.peers_on = bool(self.cfg.get("peers_on", True)) and not solo
        self.guard_on = True
        self.beep_on = True
        self.mini = False
        self.q_received = 0
        self.q_sent = 0
        self.pressure = []
        self.toast_until = 0.0
        self.flash_until = 0.0
        self.last_bc = 0.0
        self.demo_peers = [
            {"name": "阿岩", "state": "draw"},
            {"name": "小林", "state": "draw"},
            {"name": "老周", "state": "idle"},
        ]
        self.t0 = time.time()
        self.sched_after = None
        self.probe_written = None

        self._build()
        self._bind()
        if not demo:
            self._boot_room()
        self._loop()
        if exit_after:
            self.root.after(int(exit_after * 1000), self.quit)
        if snapshot:
            self.root.after(int(snap_delay * 1000), lambda: (self._capture(snapshot), self.quit()))
        if qtest:
            self.root.after(int(qtest * 1000), self.send_q)

    # ---------- 界面 ----------
    def _build(self):
        tk = self.tk
        self.root = tk.Tk()
        self.root.title("绘画自习室")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.97)
        except Exception:
            pass
        self.root.configure(bg=PAPER)
        g = self.win32.screen_size()
        w, h = 336, 306
        self.root.geometry(f"{w}x{h}+{g[0]-w-40}+{g[1]-h-90}")

        # 外框
        self.frame = tk.Frame(self.root, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
        self.frame.pack(fill="both", expand=True)

        # 标题条（可拖）
        bar = tk.Frame(self.frame, bg=PAPER_HI, height=30)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self.bar = bar
        self.seal = tk.Label(bar, text="习", bg=MARK, fg=PAPER_HI, font=F_KAI, width=2)
        self.seal.pack(side="left", padx=(8, 6), pady=5)
        self.title = tk.Label(bar, text=f"绘画自习室 · {self.room_code}", bg=PAPER_HI, fg=INK, font=F_KAI)
        self._title_full = self.title.cget("text")
        self.title.pack(side="left")
        self.title.bind("<Button-1>", lambda e: self.open_settings(), add="+")
        self.lbl_link = tk.Label(bar, text="", bg=PAPER_HI, fg=INK_FAINT, font=F_SM)
        self.lbl_link.pack(side="left", padx=(6, 0))
        self.drag_zone = [bar, self.title, self.seal]
        self.btn_mini = tk.Label(bar, text="—", bg=PAPER_HI, fg=INK_FAINT, font=F_SM, width=2)
        self.btn_mini.pack(side="right", padx=(0, 2))
        self.btn_close = tk.Label(bar, text="✕", bg=PAPER_HI, fg=INK_FAINT, font=F_SM, width=2)
        self.btn_close.pack(side="right")
        self.btn_close.bind("<Button-1>", lambda e: self.quit())
        self.btn_mini.bind("<Button-1>", lambda e: self.toggle_mini())

        # 主体
        self.body = tk.Frame(self.frame, bg=PAPER)
        self.body.pack(fill="both", expand=True, padx=12, pady=(8, 10))

        # 时钟行
        row1 = tk.Frame(self.body, bg=PAPER)
        row1.pack(fill="x")
        self.lbl_clock = tk.Label(row1, text="25:00", bg=PAPER, fg=INK, font=F_BIG)
        self.lbl_clock.pack(side="left")
        self.lbl_state = tk.Label(row1, text="● 未开始", bg=PAPER, fg=INK_FAINT, font=F_SM)
        self.lbl_state.pack(side="left", padx=(8, 0), pady=(12, 0))
        self.btn_start = tk.Label(row1, text="开始", bg=INK, fg=PAPER_HI, font=F_UI, width=6, pady=3)
        self.btn_start.pack(side="right", pady=(8, 0))
        self.btn_start.bind("<Button-1>", lambda e: self.toggle_timer())

        # 提示条
        self.lbl_tip = tk.Label(self.body, text="笔触在别的软件里，这一格只管读它", bg=PAPER, fg=INK_FAINT,
                                font=F_SM, anchor="w")
        self.lbl_tip.pack(fill="x", pady=(2, 6))

        # 四格：笔触是单位，不是「落笔次数」
        grid = tk.Frame(self.body, bg=PAPER)
        grid.pack(fill="x")
        self.metrics = {}
        for i, (k, cap) in enumerate([("strokes", "笔触"), ("avg_len", "均笔触"),
                                      ("undo_rate", "撤回率"), ("permin", "每分钟")]):
            cell = tk.Frame(grid, bg=PAPER_HI, highlightbackground=LINE, highlightthickness=1)
            cell.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 4, 0))
            tk.Label(cell, text=cap, bg=PAPER_HI, fg=INK_FAINT, font=F_SM).pack(anchor="w", padx=6, pady=(4, 0))
            v = tk.Label(cell, text="0", bg=PAPER_HI, fg=INK, font=F_NUM)
            v.pack(anchor="w", padx=6, pady=(0, 4))
            self.metrics[k] = v
            grid.grid_columnconfigure(i, weight=1)

        self.lbl_brush = tk.Label(self.body, text="笔速 — · 峰值 — · 节奏 —", bg=PAPER, fg=INK_FAINT,
                                  font=F_SM, anchor="w")
        self.lbl_brush.pack(fill="x", pady=(4, 0))

        # 档位 + 座位
        row3 = tk.Frame(self.body, bg=PAPER)
        row3.pack(fill="x", pady=(9, 0))
        self.level_chips = {}
        for key in ("gentle", "standard", "strict"):
            c = tk.Label(row3, text=LEVELS[key]["label"], font=F_SM, padx=7, pady=2,
                         bg=PAPER if key != "standard" else INK,
                         fg=INK_SOFT if key != "standard" else PAPER_HI,
                         highlightbackground=LINE, highlightthickness=1)
            c.pack(side="left", padx=(0, 4))
            c.bind("<Button-1>", lambda e, k=key: self.set_level(k))
            self.level_chips[key] = c

        self.seats_row = tk.Frame(self.body, bg=PAPER)
        self.seats_row.pack(fill="x", pady=(8, 0))
        self.lbl_seats = tk.Label(self.seats_row, text="同桌", bg=PAPER, fg=INK_FAINT, font=F_SM)
        self.lbl_seats.pack(side="left")
        self.seat_labels = tk.Frame(self.seats_row, bg=PAPER)
        self.seat_labels.pack(side="left", padx=(4, 0))
        self.btn_q = tk.Label(self.seats_row, text="？", bg=ZHU_SOFT, fg=ZHU, font=F_SM, padx=7, pady=1,
                              highlightbackground=ZHU, highlightthickness=1)
        self.btn_q.pack(side="right")
        self.btn_q.bind("<Button-1>", lambda e: self.send_q())

        # 底部：导出 + 守护
        row4 = tk.Frame(self.body, bg=PAPER)
        row4.pack(fill="x", pady=(9, 0))
        self.lbl_export = tk.Label(row4, text="导出数据", bg=LV, fg=PAPER_HI, font=F_UI, padx=9, pady=3)
        self.lbl_export.pack(side="left")
        self.lbl_export.bind("<Button-1>", lambda e: self.export_json())
        self.lbl_guard = tk.Label(row4, text="守护 开", bg=PAPER, fg=LV, font=F_SM, padx=6, pady=2,
                                  highlightbackground=LINE, highlightthickness=1)
        self.lbl_guard.pack(side="left", padx=(6, 0))
        self.lbl_guard.bind("<Button-1>", lambda e: self.toggle_guard())
        self.lbl_exe = tk.Label(row4, text="", bg=PAPER, fg=INK_FAINT, font=F_SM, anchor="e")
        self.lbl_exe.pack(side="right")

        # 迷你态（收成小条）：必须自带展开与关闭，否则 overrideredirect 窗口没有系统标题栏、没有任务栏按钮、Alt+F4 也无效
        self.mini_frame = tk.Frame(self.frame, bg=PAPER_HI)
        self.mini_close = tk.Label(self.mini_frame, text="✕", bg=PAPER_HI, fg=INK_FAINT, font=F_SM, width=2)
        self.mini_close.pack(side="right", padx=(0, 4))
        self.mini_close.bind("<Button-1>", lambda e: self.quit())
        self.mini_expand = tk.Label(self.mini_frame, text="⤢", bg=PAPER_HI, fg=INK_FAINT, font=F_SM, width=2)
        self.mini_expand.pack(side="right")
        self.mini_expand.bind("<Button-1>", lambda e: self.toggle_mini())
        self.mini_clock = tk.Label(self.mini_frame, text="25:00", bg=PAPER_HI, fg=INK, font=F_NUM)
        self.mini_clock.pack(side="left", padx=(8, 4))
        self.mini_clock.bind("<Button-1>", lambda e: self.toggle_mini())
        self.mini_state = tk.Label(self.mini_frame, text="未开始", bg=PAPER_HI, fg=INK_FAINT, font=F_SM)
        self.mini_state.pack(side="left")

        if self.demo:
            self._fill_demo()

    def _bind(self):
        for w in self.drag_zone:
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        # 迷你态没有标题条，靠小条本身拖动
        for w in (self.mini_frame, self.mini_state):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<Escape>", lambda e: self.quit())
        # 右键菜单是最后一道门：窗口用的是 overrideredirect，没有系统标题栏与任务栏按钮
        self.root.bind_all("<Button-3>", self._ctx_menu)

    def _drag_start(self, ev):
        self._drag = (ev.x_root, ev.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, ev):
        if not getattr(self, "_drag", None):
            return
        dx, dy = ev.x_root - self._drag[0], ev.y_root - self._drag[1]
        self.root.geometry(f"+{self._drag[2] + dx}+{self._drag[3] + dy}")

    # ---------- 行为 ----------
    def set_level(self, key):
        self.tracker.profile = LEVELS[key]
        for k, c in self.level_chips.items():
            c.configure(bg=INK if k == key else PAPER, fg=PAPER_HI if k == key else INK_SOFT)
        self.toast(f"{LEVELS[key]['label']}档 · 静默 {LEVELS[key]['idle']}s")

    def toggle_guard(self):
        self.guard_on = not self.guard_on
        self.lbl_guard.configure(text="守护 开" if self.guard_on else "守护 关",
                                 fg=LV if self.guard_on else INK_FAINT)
        self.toast("守护已开" if self.guard_on else "守护已关，本轮不提醒")

    def toggle_timer(self):
        if not self.running:
            self.running = True
            self.remaining = self.pomodoro_min * 60 if self.remaining <= 0 else self.remaining
            self.timer_end = time.time() + self.remaining
            self._act_last = None      # 暂停期间不计入 active_sec
            if self.started_at is None:
                self.started_at = now_iso()
            self.btn_start.configure(text="暂停")
            self.lbl_state.configure(text="● 自习中", fg=LV)
        else:
            self.running = False
            self._act_last = None
            self.remaining = max(0, int(self.timer_end - time.time()))
            self.btn_start.configure(text="继续")
            self.lbl_state.configure(text="● 已暂停", fg="#b3853e")

    def toggle_mini(self):
        self.mini = not self.mini
        if self.mini:
            self.body.pack_forget()
            self.bar.pack_forget()          # 旧版留着标题条：窗口缩到 152 宽后 ✕ 被排到 x=293，落在窗口外，点不到
            self.mini_frame.pack(fill="both", expand=True)
            self.frame.configure(bg=PAPER_HI)
            self.root.geometry("186x34")    # 一条到底：时间 / 状态 / 展开 / 关闭
        else:
            self.mini_frame.pack_forget()
            self.bar.pack(fill="x", side="top")
            self.body.pack(fill="both", expand=True, padx=12, pady=(8, 10))
            self.frame.configure(bg=PAPER)
            self.root.geometry("336x306")

    def _ctx_menu(self, ev):
        try:
            if ev.widget.winfo_toplevel() is not self.root:
                return
        except Exception:
            pass
        if getattr(self, "_menu", None) is None:
            m = tk.Menu(self.root, tearoff=0, bd=0, bg=PAPER_HI, fg=INK,
                        activebackground=INK, activeforeground=PAPER_HI)
            m.add_command(label="展开 / 收成小条", command=self.toggle_mini)
            m.add_command(label="导出数据", command=self.export_json)
            m.add_separator()
            m.add_command(label="退出", command=self.quit)
            self._menu = m
        try:
            self._menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self._menu.grab_release()

    def toast(self, msg):
        self.lbl_tip.configure(text=msg, fg=ZHU)
        self.toast_until = time.time() + 3

    def flash(self):
        self.flash_until = time.time() + 3.2

    def beep(self):
        if not self.beep_on:
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    # ---------- 自习桌（真实同桌：局域网 + 公网两路并行） ----------
    def peers(self):
        if self.demo:
            return [{"id": f"demo{i}", "name": p["name"], "state": p["state"], "reminded": False}
                    for i, p in enumerate(self.demo_peers)][:3]
        now = time.time()
        out = []
        for pid, p in list(self.peer_table.items()):
            if now - p.get("last_seen", 0) > 8:
                self.peer_table.pop(pid, None)
                continue
            out.append({"id": pid, "name": p.get("name", "?"), "state": p.get("state", "idle"),
                        "reminded": p.get("reminded", False), "via": p.get("via", "")})
        out.sort(key=lambda x: x["name"])
        return out[:4]

    def _broadcast(self, msg):
        """一条消息同时走局域网和公网，谁先到算谁。"""
        if self.demo:
            return
        base = dict(msg, id=self.pid, room=self.room_code, name=self.me, ts=time.time())
        if self.room:
            self.room.send(base)
        if self.cloud:
            self.cloud.send(base)

    def _boot_room(self):
        """起两条通道：局域网组播（同 Wi-Fi 零配置）+ 公网 MQTT（异地也能同桌）。"""
        if not self.no_lan:
            self.room = Room(self.room_code, self.me, self.pid)
        if not self.no_cloud:
            self.cloud = Cloud(self.room_code, self.pass_code)
        self.peer_table.clear()
        self._broadcast({"t": "hi"})

    def send_q(self, peer_id=None):
        if not self.peers_on:
            self.toast("没有同伴在线，问号没有送出" if self.covert else "这一轮是独桌，没有同伴可发")
            return
        ps = self.peers()
        if not ps:
            self.toast("房里只有你 —— 让同桌也打开小窗、输同一个桌号")
            return
        tgt = next((p for p in ps if p["id"] == peer_id), None)
        if tgt is None:
            tgt = next((p for p in ps if p["state"] != "draw"), ps[0])
        if self.demo:
            tgt["state"] = "draw"
        else:
            self._broadcast({"t": "q", "to": tgt["id"], "qid": uuid.uuid4().hex[:8]})
        self.q_sent += 1
        self.toast(f"给 {tgt['name']} 发了一个问号")

    def peer_state(self):
        tp = self.tracker
        if tp.pending:
            return "fish"
        if not self.running:
            return "idle"
        if not tp.in_draw_app and tp.restrict:
            return "out"
        return "draw" if (time.time() - tp.last_pen_t) <= 3 else "idle"

    def _room_tick(self, t):
        if self.demo:
            return
        if t - self.last_bc >= 2.0:
            self.last_bc = t
            self._broadcast({"t": "s", "state": self.peer_state(), "strokes": self.tracker.strokes,
                             "reminded": bool(self.tracker.pending), "pomo": self.running})
        for src in (self.room, self.cloud):
            if not src:
                continue
            while True:
                try:
                    msg = src.inbox.get_nowait()
                except queue.Empty:
                    break
                self._on_room_msg(msg, t)
        # 系统提醒 6 秒后，房里在画的同桌补一个问号（社交压力第二段，由同伴客户端发出）
        if self.peers_on:
            for pid, p in self.peer_table.items():
                since = p.get("reminded_since")
                if since and t - since >= 6 and not p.get("nudged"):
                    p["nudged"] = True
                    self._broadcast({"t": "q", "to": pid, "auto": True, "qid": uuid.uuid4().hex[:8]})

    def _on_room_msg(self, msg, now):
        if not self.peers_on:
            # 独桌条件一律不接收同伴消息：否则一条问号就会写进 pressure，污染 H2 的反应时
            return
        pid, typ = msg.get("id"), msg.get("t")
        if msg.get("room") != self.room_code or pid == self.pid:
            return
        if typ == "bye":
            self.peer_table.pop(pid, None)
            return
        if typ in ("hi", "s"):
            p = self.peer_table.setdefault(pid, {})
            p.update({"name": (msg.get("name") or "?").strip()[:8],
                      "state": msg.get("state", "idle"),
                      "strokes": msg.get("strokes", 0),
                      "reminded": bool(msg.get("reminded")),
                      "last_seen": now,
                      "via": msg.get("_via", "lan")})
            if p["reminded"]:
                p.setdefault("reminded_since", now)
            else:
                p.pop("reminded_since", None)
                p.pop("nudged", None)
            return
        if typ == "q" and msg.get("to") == self.pid:
            qid = msg.get("qid")
            if qid and qid in self.seen_q:
                return
            if qid:
                self.seen_q.append(qid)
            self.q_received += 1
            self.pressure.append({
                "at": now_iso(),
                "from": msg.get("name", "同桌"),
                "via": msg.get("_via", "lan"),
                "auto": bool(msg.get("auto")),
                "after_reminder_ms": int((now - self.tracker.last_reminder_t) * 1000) if self.tracker.pending else None,
                "reaction_ms": None,
            })
            self.flash()
            self.beep()
            self.toast(f"{msg.get('name', '同桌')} 发来一个问号")

    def link_text(self):
        if self.demo:
            return "演示", INK_FAINT
        if self.covert:
            # 盲化：实际两条通道都关了，但徽标要与「有同桌」条件一致，
            # 否则被试一看「离线 vs 仅局域网」就知道这一轮是哪个条件
            return "仅局域网", "#b3853e"
        if self.cloud and self.cloud.ready:
            return "云 · 联", LV
        if self.room:
            return "仅局域网", "#b3853e"
        return "离线", INK_FAINT

    # ---------- 设置 ----------
    def open_settings(self):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.title("自习室设置")
        win.configure(bg=PAPER)
        win.attributes("-topmost", True)
        win.geometry(f"260x250+{self.root.winfo_x() + 12}+{self.root.winfo_y() + 40}")
        win.resizable(False, False)

        tk.Label(win, text="昵称", bg=PAPER, fg=INK_SOFT, font=F_SM).pack(anchor="w", padx=14, pady=(12, 2))
        e_name = tk.Entry(win, font=F_UI, relief="flat", bg=PAPER_HI, fg=INK, highlightthickness=1,
                          highlightbackground=LINE, highlightcolor=ZHU)
        e_name.insert(0, self.me)
        e_name.pack(fill="x", padx=14, ipady=3)

        tk.Label(win, text="自习桌号（同桌输同一个号）", bg=PAPER, fg=INK_SOFT, font=F_SM).pack(anchor="w", padx=14, pady=(8, 2))
        e_room = tk.Entry(win, font=F_UI, relief="flat", bg=PAPER_HI, fg=INK, highlightthickness=1,
                          highlightbackground=LINE, highlightcolor=ZHU)
        e_room.insert(0, self.room_code)
        e_room.pack(fill="x", padx=14, ipady=3)

        tk.Label(win, text="口令（可留空；两人要一样）", bg=PAPER, fg=INK_SOFT, font=F_SM).pack(anchor="w", padx=14, pady=(8, 2))
        e_pass = tk.Entry(win, font=F_UI, relief="flat", bg=PAPER_HI, fg=INK, highlightthickness=1,
                          highlightbackground=LINE, highlightcolor=ZHU)
        e_pass.insert(0, self.pass_code)
        e_pass.pack(fill="x", padx=14, ipady=3)

        var = tk.IntVar(value=1 if self.peers_on else 0)
        if not self.covert:
            # 盲化运行时不放出这个开关，否则被试一开设置就看到条件
            tk.Checkbutton(win, text="这一轮有同桌（关＝独桌条件）", variable=var, bg=PAPER, fg=INK_SOFT,
                           activebackground=PAPER, selectcolor=PAPER_HI, font=F_SM, bd=0,
                           highlightthickness=0).pack(anchor="w", padx=10, pady=(8, 0))

        def apply():
            self.me = (e_name.get().strip() or "我")[:8]
            self.room_code = (e_room.get().strip() or "302")[:6]
            if not self.covert:
                self.peers_on = bool(var.get())
            self.pass_code = (e_pass.get().strip() or "")[:24]
            self.cfg.update({"name": self.me, "room": self.room_code,
                             "peers_on": self.peers_on, "pass": self.pass_code})
            save_config(self.cfg)
            self.title.configure(text=f"绘画自习室 · {self.room_code}")
            if self.room:
                self.room.close()
            if self.cloud:
                self.cloud.close()
            self.room, self.cloud = None, None
            if not self.demo:
                self._boot_room()
            win.destroy()
            self.toast(f"已换到 {self.room_code} 桌 · {self.me}")

        btn = tk.Label(win, text="保存", bg=INK, fg=PAPER_HI, font=F_UI, padx=16, pady=4)
        btn.pack(pady=(12, 0))
        btn.bind("<Button-1>", lambda e: apply())
        win.bind("<Return>", lambda e: apply())
        win.transient(self.root)

    def pen_down_edge(self):
        """重新落笔时补 pressure 的 reaction_ms。"""
        if self.pressure and self.pressure[-1]["reaction_ms"] is None:
            self.pressure[-1]["reaction_ms"] = int((time.time() - self.t0) * 1000)  # 占位，下面用真实差值
        # 真实差值：用记录时间
        if self.pressure:
            last = self.pressure[-1]
            if last["reaction_ms"] is not None:
                try:
                    t_rec = datetime.fromisoformat(last["at"]).timestamp()
                    last["reaction_ms"] = int((time.time() - t_rec) * 1000)
                except Exception:
                    pass

    # ---------- 主循环 ----------
    def _loop(self):
        self._sample()
        self.root.after(int(1000 / SAMPLE_HZ), self._loop)

    def _sample(self):
        t = time.time()
        self.ticks += 1
        if self.t_first_tick is None:
            self.t_first_tick = t
        if self.demo:
            if self.running:
                self.remaining = max(0, int(self.timer_end - t))
            self.tracker.in_draw_app = True
            self.tracker.exe = "photoshop.exe"
            self.tracker.cur = 340.0
            self.tracker.last_pen_t = t - 1.2
            self._render()
            return
        cur = self.win32.cursor()
        if cur is None:
            return
        down = self.win32.key_down(0x01)
        ctrl = self.win32.key_down(0x11)
        z = self.win32.key_down(0x5A)
        exe = self.win32.foreground_exe()
        count = self.anywhere or exe in DRAW_EXES

        was_down = self.tracker.last_down
        prev_strokes = self.tracker.strokes
        self.tracker.sample(t, cur[0], cur[1], down, ctrl, z, exe, count_enabled=count)

        # 重新落笔：解除警示 + 补 delay_ms
        if count and down and not was_down:
            self.tracker.pen_back(t)
            self.flash_until = 0
            if self.pressure and self.pressure[-1]["reaction_ms"] is None:
                rec = self.pressure[-1]
                try:
                    rec["reaction_ms"] = int((t - datetime.fromisoformat(rec["at"]).timestamp()) * 1000)
                except Exception:
                    rec["reaction_ms"] = 0

        if self.running:
            self.remaining = max(0, int(self.timer_end - t))
            # 按真实时间差累加：after(33ms) 实测只有约 21Hz，按 tick/30 会少算三成
            if self._act_last is not None:
                self.active_sec += max(0.0, t - self._act_last)
            self._act_last = t
            if self.remaining <= 0:
                self.running = False
                self._act_last = None
                self.pomo_rounds += 1
                self.remaining = self.pomodoro_min * 60
                self.btn_start.configure(text="开始")
                self.lbl_state.configure(text="● 本轮完成", fg=ZHU)
                self.beep()
                self.flash()
                self.root.after(3000, lambda: self.lbl_state.configure(text="● 未开始", fg=INK_FAINT))

        sig = self.tracker.check(t, enabled=self.guard_on and self.running)
        if sig:
            signal, rec = sig
            self.flash()
            self.beep()
            if signal == "idle":
                self.toast(f"停笔 {self.tracker.profile['idle']}s · 画笔寂寞了")
            else:
                self.toast(f"笔速 {rec.get('recent_peak')}→{rec.get('now')} px/s")
        self._room_tick(t)
        self._render()

    def _render(self):
        if self.mini:
            self.mini_clock.configure(text=self._clock_text())
            self.mini_state.configure(text=self._state_text()[1])
            return

        self.lbl_clock.configure(text=self._clock_text())
        st = self._state_text()
        self.lbl_state.configure(text="● " + st[1], fg=st[0])

        tp = self.tracker
        br = tp.brush()
        self.metrics["strokes"].configure(text=str(tp.strokes))
        self.metrics["avg_len"].configure(text=f"{br['avg_len_px']}")
        self.metrics["undo_rate"].configure(text=f"{int(br['undo_rate'] * 100)}%")
        self.metrics["permin"].configure(text=f"{tp.strokes_per_min(max(self.active_sec, 1)):.1f}")
        self.lbl_brush.configure(
            text=f"笔速 {int(tp.cur)} · 峰值 {int(tp.peak)} · 节奏 "
                 f"{'稳' if (br['rhythm_mad_ratio'] is not None and br['rhythm_mad_ratio'] < 0.6) else ('散' if br['rhythm_mad_ratio'] is not None else '—')}"
                 f" · 短笔触 {int(br['short_ratio'] * 100)}%")

        if time.time() > self.toast_until:
            self.lbl_tip.configure(text="在我画的软件里落笔就开始计时" if self.tracker.in_draw_app
                                   else "当前不在绘画软件里（不计入）", fg=INK_FAINT)

        if time.time() < self.flash_until:
            self.frame.configure(highlightbackground=ZHU, highlightthickness=2)
        else:
            self.frame.configure(highlightbackground=LINE, highlightthickness=1)

        txt, col = self.link_text()
        self.lbl_link.configure(text=txt, fg=col)

        # 座位（真实同桌，点名字＝给他发问号）
        for w in self.seat_labels.winfo_children():
            w.destroy()
        if not self.peers_on:
            if self.covert:
                # 盲化：独桌条件也显示同桌座位行，只显示为离线，绝不出现「独桌」字样
                for nm in self.covert_peers:
                    self.tk.Label(self.seat_labels, text="●" + nm, bg=PAPER, fg=INK_FAINT,
                             font=F_SM).pack(side="left", padx=(0, 5))
                self.tk.Label(self.seat_labels, text="（都不在线）", bg=PAPER, fg=INK_FAINT,
                         font=F_SM).pack(side="left")
            else:
                self.tk.Label(self.seat_labels, text="（独桌 · 本轮无同桌）", bg=PAPER, fg=INK_FAINT,
                         font=F_SM).pack(side="left")
        else:
            ps = self.peers()
            if not ps:
                self.tk.Label(self.seat_labels, text="（房里就你一个）", bg=PAPER, fg=INK_FAINT,
                         font=F_SM).pack(side="left")
            else:
                for p in ps:
                    if p["state"] == "draw":
                        color = LV
                    elif p["state"] in ("fish",) or p.get("reminded"):
                        color = ZHU
                    else:
                        color = INK_FAINT
                    lab = self.tk.Label(self.seat_labels, text="●" + p["name"][:3], bg=PAPER, fg=color, font=F_SM)
                    lab.pack(side="left", padx=(0, 5))
                    lab.bind("<Button-1>", lambda e, pid=p["id"]: self.send_q(pid))

        self.lbl_exe.configure(text=(self.tracker.exe or "—")[:16], fg=LV if self.tracker.in_draw_app else INK_FAINT)

    def _clock_text(self):
        sec = self.remaining if not self.running else max(0, int(self.timer_end - time.time()))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _state_text(self):
        tp = self.tracker
        if not self.running:
            return INK_FAINT, "未开始" if self.started_at is None else "已暂停"
        if not tp.in_draw_app and tp.restrict:
            return "#b3853e", "不在绘画软件"
        if tp.pending:
            return ZHU, "被提醒"
        idle = int(time.time() - tp.last_pen_t)
        if idle <= 3:
            return LV, "在画"
        return INK_FAINT, f"停 {idle}s"

    # ---------- 导出 ----------
    def payload(self):
        tp = self.tracker
        prof = tp.profile
        return {
            "schema_version": 3,
            "app": APP,
            "exported_at": now_iso(),
            "source": {"input": "windows global pointer + Ctrl+Z", "foreground_filter": not self.anywhere,
                       "draw_apps": sorted(DRAW_EXES), "sample_hz": SAMPLE_HZ,
                       "measured_hz": (round(self.ticks / max(1e-3, time.time() - self.t_first_tick), 1)
                                       if self.t_first_tick is not None else None),
                       "mouse_hook": bool(self.mhook and self.mhook.ok),
                       "dpi_scale": (round(self.mhook.scale, 3) if self.mhook else None),
                       "hook_scale_manual": (self.mhook.manual_scale if self.mhook else None),
                       "dpi_refresh_count": (self.mhook.refresh_count if self.mhook else None),
                       "dpi_change_count": (self.mhook.change_count if self.mhook else None),
                       "dpi_monitors": ([{"device": m["name"], "phys": list(m["phys"]),
                                          "virt": list(m["virt"]), "scale": round(m["kx"], 3)}
                                         for m in (self.mhook.maps if self.mhook else [])] or None),
                       "hook_skew_max_px": (round(tp.hook_skew_max, 1) if self.mhook and self.mhook.ok else None),
                       "hook_scale_warn": bool(tp.skew_warn)},
            "session": {
                # 被试内 A/B 配对键：同一被试两场的记录靠这四项拉通
                "participant_id": self.subject_id or None,
                "session_no": self.session_no,
                "piece_id": self.piece or None,
                "order_block": self.order_block or None,
                "covert": self.covert,
                "solo_flag": self.launch_solo,
                "started_at": self.started_at,
                "ended_at": now_iso(),
                "active_sec": round(self.active_sec, 1),
                "condition": prof["key"],
                "social_condition": "with_peers" if self.peers_on else "system_only",
                "planned_min": self.pomodoro_min,
            },
            "desk": {
                "stroke_count": tp.strokes,
                "undo_count": tp.undos,
                "path_px": int(tp.path),
                "pen_down_sec": round(tp.move_t, 1),
                "avg_speed_px_s": tp.avg_speed(),
                "peak_speed_px_s": int(tp.peak),
                "current_speed_px_s": int(tp.cur),
                "strokes_per_min": tp.strokes_per_min(max(self.active_sec, 1)),
                # 笔触级：原话说的「检测笔触」落在这里
                "brush": tp.brush(),
                "stroke_samples": tp.recent_strokes(300),
            },
            "guard": {
                "enabled": self.guard_on,
                "level": prof["key"],
                "thresholds": {"idle_sec": prof["idle"], "cliff_ratio": prof["cliff_ratio"],
                               "cliff_dwell_sec": prof["cliff_dwell"], "cooldown_sec": prof["cooldown"],
                               "cliff_min_peak": CLIFF_MIN_PEAK},
                "reminder_count": len(tp.reminders),
                "reminders": tp.reminders,
            },
            "room": {
                "code": self.room_code,
                "me": self.me,
                "peer_id": self.pid,
                "lan": bool(self.room),
                "cloud": bool(self.cloud and self.cloud.ready),
                "cloud_broker": (self.cloud.broker if self.cloud else None),
                "secured": bool(self.pass_code),
                "peers_enabled": self.peers_on,
                "demo_peers": bool(self.demo),
                "peer_states": self.peers(),
                "qmark_sent": self.q_sent,
                "qmark_received": self.q_received,
                "pressure": self.pressure,
            },
            "pomodoro": {"rounds_completed": self.pomo_rounds, "focus_sec": round(self.active_sec, 1)},
            "data_valid": tp.strokes > 0,
            "limits": [
                "只读全局指针与键盘：撤销按 Ctrl+Z 计，若软件用别的快捷键撤销会漏记。",
                "落笔＝左键按下；数位板落笔通常等同左键，但部分软件/驱动可自定义，需现场试一次。",
                "未做前台进程白名单以外的兼容：不在表中的绘画软件请加进 DRAW_EXES 或用 --anywhere。",
                "同桌走局域网组播（同一 Wi-Fi 才能互相看见），跨网段/校园网隔离会看不见。",
                "不读取任何软件的笔压/倾角/工程文件，只做输入行为统计；笔触是几何口径，不含力度。",
            ],
        }

    def export_json(self):
        tp = self.tracker
        out_dir = os.path.join(app_dir(), "导出")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = ""
        if self.subject_id:
            sid = self.subject_id if self.subject_id.upper().startswith("P") else "P" + self.subject_id
            tag = f"{sid}_S{self.session_no or 1}_"
        cond_txt = "with_peers" if self.peers_on else "system_only"
        name = f"自习室_{tag}{tp.profile['key']}_{cond_txt}_{stamp}.json"
        if self.covert:
            # 盲化时文件名与提示不带条件字样，避免被试从屏幕上看出来
            name = f"自习室_{tag}{tp.profile['key']}_{stamp}.json"
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.payload(), f, ensure_ascii=False, indent=2)
        self.toast("已导出数据" if self.covert else ("已导出 " + name))
        self.probe_written = path
        return path

    # ---------- 演示 / 退出 ----------
    def _capture(self, path):
        """用 PrintWindow 抓自己的窗口（不依赖屏幕截图权限）。"""
        try:
            from PIL import Image
        except Exception as e:
            print("no PIL:", e)
            return None
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        hwnd = self.root.winfo_id()
        parent = u.GetParent(hwnd)
        while parent:
            hwnd, parent = parent, u.GetParent(parent)
        rect = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        wdc = u.GetWindowDC(hwnd)
        mdc = g.CreateCompatibleDC(wdc)
        bmp = g.CreateCompatibleBitmap(wdc, w, h)
        g.SelectObject(mdc, bmp)
        u.PrintWindow(hwnd, mdc, 2)   # PW_RENDERFULLCONTENT

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BI(ctypes.Structure):
            _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]

        bi = BI()
        bi.bmiHeader.biSize = ctypes.sizeof(BIH)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h      # 负数＝自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", w * 4, 1).convert("RGB")
        img.save(path, "PNG")
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(hwnd, wdc)
        print("snapshot ok", path, w, h, "lines", got)
        return path

    def _fill_demo(self):
        tp = self.tracker
        tp.strokes, tp.undos, tp.path, tp.move_t = 128, 9, 41200.0, 604.0
        tp.peak, tp.cur = 612.0, 340.0
        tp.exe, tp.in_draw_app, tp.state = "photoshop.exe", True, "draw"
        tp.last_pen_t = time.time() - 1.5
        # 造 128 条笔触（固定种子，截图每次都一样）
        random.seed(7)
        tp.stroke_list = []
        t0 = time.time() - 968
        for i in range(128):
            length = max(18.0, random.gauss(322, 145))
            dur = max(0.12, length / max(60.0, random.gauss(330, 95)))
            x0, y0 = random.randint(300, 2400), random.randint(200, 1300)
            ang = random.uniform(0, 6.28)
            tp.stroke_list.append({
                "i": i + 1, "t0": t0, "t1": t0 + dur, "dur": round(dur, 3),
                "x0": x0, "y0": y0,
                "x1": int(x0 + length * 0.7 * math.cos(ang)), "y1": int(y0 + length * 0.7 * math.sin(ang)),
                "path": round(length, 1), "peak": round(min(900, length / dur * 1.6), 1),
                "pauses": 0, "last_mv": t0 + dur, "samples": 20,
                "undo": random.random() < 0.055, "straight": round(random.uniform(0.2, 0.9), 2),
                "avg_v": int(length / dur),
                "at": datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
            })
            t0 += random.uniform(3.0, 9.5)
        self.active_sec = 968.0
        self.remaining = 412
        self.running = True
        self.timer_end = time.time() + self.remaining
        self.pomo_rounds = 1
        self.started_at = now_iso()
        self.q_received, self.q_sent = 1, 2
        self.btn_start.configure(text="暂停")
        self.demo_peers[0]["state"] = "draw"
        self.demo_peers[2]["state"] = "fish"
        self.toast_until = time.time() + 60
        self.lbl_tip.configure(text="标准档 · 停笔会被提醒，6 秒后同桌补一个问号", fg=INK_SOFT)

    def quit(self):
        if self.room:
            self.room.close()
        if self.cloud:
            self.cloud.close()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------- 实机联通测试（只在自家窗口里点，不碰别人的窗口） ----------
    def livetest(self):
        self.anywhere = True
        u = ctypes.windll.user32
        geo = (self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height())
        cx, cy = geo[0] + geo[2] // 2, geo[1] + 120
        steps = []

        def pos(i):
            u.SetCursorPos(cx + i * 3, cy + int(math.sin(i / 3.0) * 8))

        def press():
            u.mouse_event(0x0002, 0, 0, 0, 0)     # LEFTDOWN

        def release():
            u.mouse_event(0x0004, 0, 0, 0, 0)     # LEFTUP

        pos(0)
        self.root.after(80, press)
        for i in range(1, 26):
            steps.append(self.root.after(80 + i * 30, lambda i=i: pos(i)))
        self.root.after(80 + 26 * 30, release)

        def report():
            tp = self.tracker
            print(json.dumps({"strokes": tp.strokes, "path_px": int(tp.path),
                              "peak_speed": int(tp.peak), "pen_down_sec": round(tp.move_t, 2),
                              "foreground": self.win32.foreground_exe()}, ensure_ascii=False))
            self.quit()

        self.root.after(80 + 26 * 30 + 400, report)


# ══════════════ 自检 / 探针 ══════════════
def selftest():
    ok, fails = 0, []
    lines = []

    def chk(name, cond, detail=""):
        nonlocal ok
        if cond:
            ok += 1
            lines.append(f"[OK]   {name}")
        else:
            fails.append(f"{name} ← {detail}")
            lines.append(f"[FAIL] {name} ← {detail}")

    # 1 三次落笔 → 3 笔
    tp = Tracker()
    tp.restrict = True
    t = 100.0
    for i in range(3):
        tp.sample(t, 0, 0, False, False, False, "photoshop.exe", True); t += 0.05
        tp.sample(t, 10, 10, True, False, False, "photoshop.exe", True); t += 0.05   # 落笔
        for j in range(1, 8):
            tp.sample(t, 10 + j * 20, 10 + j * 5, True, False, False, "photoshop.exe", True); t += 0.05
        tp.sample(t, 150, 45, False, False, False, "photoshop.exe", True); t += 0.2   # 抬笔
    chk("落笔计数=3", tp.strokes == 3, tp.strokes)
    chk("路径长度>0", tp.path > 0, tp.path)
    chk("平均笔速>0", tp.avg_speed() > 0, tp.avg_speed())
    chk("笔触记录=3 条（一笔一记）", len(tp.stroke_list) == 3, len(tp.stroke_list))
    chk("笔触带时长/长度/速度",
        all(s["dur"] > 0 and s["path"] > 0 and s["avg_v"] > 0 for s in tp.stroke_list),
        tp.stroke_list[-1])
    chk("笔触带起止点与直线度",
        all({"x0", "y0", "x1", "y1", "straight"} <= set(s) for s in tp.stroke_list), tp.stroke_list[-1])
    br = tp.brush()
    chk("笔触聚合有均长度/中位数", br["avg_len_px"] > 0 and br["median_len_px"] > 0, br)

    # 2 Ctrl+Z 三次，只有按下边沿计数
    tp = Tracker(); t = 200.0
    tp.sample(t, 0, 0, False, False, False, "ps", True); t += 0.02
    for i in range(3):
        tp.sample(t, 0, 0, False, True, True, "ps", True); t += 0.02
        tp.sample(t, 0, 0, False, True, True, "ps", True); t += 0.02
        tp.sample(t, 0, 0, False, False, False, "ps", True); t += 0.02
    chk("撤回=3（按住不重复计）", tp.undos == 3, tp.undos)

    # 2b 撤回打在刚才那笔上（笔画废了）
    tp = Tracker(); t = 210.0
    tp.sample(t, 0, 0, False, False, False, "ps", True); t += 0.05
    tp.sample(t, 0, 0, True, False, False, "ps", True)
    for j in range(1, 6):
        t += 0.05
        tp.sample(t, j * 30, j * 10, True, False, False, "ps", True)
    t += 0.05
    tp.sample(t, 150, 50, False, False, False, "ps", True); t += 0.2
    tp.sample(t, 0, 0, False, True, True, "ps", True)
    chk("撤回标记到前一笔 undo=true", tp.stroke_list and tp.stroke_list[-1].get("undo") is True, tp.stroke_list[-1])
    chk("撤回率=1/1", tp.brush()["undo_rate"] == 1.0, tp.brush())

    # 3 非绘画软件前台 → 不计
    tp = Tracker(); t = 300.0
    tp.sample(t, 0, 0, False, False, False, "chrome.exe", False); t += 0.05
    tp.sample(t, 10, 10, True, False, False, "chrome.exe", False); t += 0.05
    for j in range(1, 8):
        tp.sample(t, 10 + j * 20, 10, True, False, False, "chrome.exe", False); t += 0.05
    chk("非绘画软件不计数", tp.strokes == 0, tp.strokes)
    chk("非绘画软件状态=out", tp.state == "out" or not tp.in_draw_app, tp.state)

    # 4 静默提醒（严厉档 10s）+ 一次提醒后不重复
    tp = Tracker(); tp.profile = LEVELS["strict"]; t = 400.0
    tp.sample(t, 0, 0, False, False, False, "ps", True)
    t += 11
    sig = tp.check(t)
    chk("静默 11s 触发 idle", sig and sig[0] == "idle", sig)
    sig2 = tp.check(t + 1)
    chk("未重新落笔不再提醒", sig2 is None, sig2)
    tp.sample(t + 1.2, 0, 0, True, False, False, "ps", True)
    chk("重新落笔后补 delay_ms", tp.reminders[0]["delay_ms"] is not None and tp.reminders[0]["delay_ms"] > 0,
        tp.reminders[0]["delay_ms"])
    chk("重新落笔后重新武装", tp.armed is True, tp.armed)

    # 5 笔速断崖
    tp = Tracker(); tp.profile = LEVELS["standard"]; t = 500.0
    tp.sample(t, 0, 0, False, False, False, "ps", True); t += 0.05
    tp.sample(t, 0, 0, True, False, False, "ps", True)
    for i in range(20):     # 高速 ≈400 px/s
        t += 0.05
        tp.sample(t, i * 20, 0, True, False, False, "ps", True)
    for i in range(20):     # 骤降 ≈40 px/s
        t += 0.05
        tp.sample(t, 380 + i * 2, 0, True, False, False, "ps", True)
    first = tp.check(t, enabled=True)
    for i in range(80):     # 持续慢速，跨过 dwell(3s)
        t += 0.05
        tp.sample(t, 420 + i, 0, True, False, False, "ps", True)
    sig = tp.check(t, enabled=True)
    chk("笔速断崖触发 cliff", (sig and sig[0] == "cliff") or (first and first[0] == "cliff"), (first, sig))
    chk("断崖记录带峰值", any("recent_peak" in r for r in tp.reminders), tp.reminders)

    # 6 每分钟落笔
    tp = Tracker(); t = 600.0
    tp.sample(t, 0, 0, False, False, False, "ps", True); t += 0.1
    for i in range(5):
        tp.sample(t, 0, 0, True, False, False, "ps", True); t += 0.1
        tp.sample(t, 0, 0, False, False, False, "ps", True); t += 0.1
    chk("每分钟落笔=5/0.5min≈10", 9.0 <= tp.strokes_per_min(30) <= 11.0, tp.strokes_per_min(30))

    # 7 节奏口径：一次长间隔不该把整场判成「散」
    def _mk(long_break=False):
        tp = Tracker()
        t0 = 800.0
        for i in range(10):
            if long_break and i == 5:
                t0 += 30.0                       # 中途起身 30 秒
            t0 += 0.5 + (0.1 if i % 2 else 0.0)  # 笔间 0.5/0.6s 交替
            tp.stroke_list.append({"i": i + 1, "t0": t0, "t1": t0 + 1.0, "path": 500.0,
                                   "dur": 1.0, "straight": 1.0, "pauses": 0, "undo": False})
            t0 += 1.0
        return tp

    br_a = _mk().brush()
    chk("节奏：无停顿 → long_gaps=0 且判稳",
        br_a["long_gaps"] == 0 and br_a["rhythm_mad_ratio"] < 0.6, br_a)
    br_b = _mk(True).brush()
    chk("节奏：一个 30s 长间隔只记 1 次", br_b["long_gaps"] == 1, br_b["long_gaps"])
    chk("节奏：长间隔不把稳健口径拉爆",
        br_b["rhythm_mad_ratio"] is not None and br_b["rhythm_mad_ratio"] < 0.6, br_b["rhythm_mad_ratio"])
    chk("节奏：旧口径 CV 已被长间隔拉爆（仅留对照）", br_b["rhythm_cv"] > 1.0, br_b["rhythm_cv"])

    # 8 鼠标钩子：起点/时长用按下与抬起的真实时刻（没有钩子时退回 tick）
    tp = Tracker(); tp.press_src = lambda: (900.0, 100.0, 100.0)
    tp.sample(900.05, 0, 0, False, False, False, "ps", True)
    t = 900.09
    tp.sample(t, 124, 100, True, False, False, "ps", True)          # 边沿迟到 40ms，指针已跑 24px
    for j in range(1, 6):
        t += 0.045
        tp.sample(t, 124 + j * 20, 100, True, False, False, "ps", True)
    t_up = t + 0.02
    tp.release_src = lambda: (t_up, 224.0, 100.0)
    t += 0.045
    tp.sample(t, 230, 100, False, False, False, "ps", True)
    st = tp.stroke_list[-1]
    chk("钩子：起笔点=按下瞬间坐标", (st["x0"], st["y0"]) == (100, 100), (st["x0"], st["y0"]))
    chk("钩子：补回按下→首个 tick 的 24px", st["path"] >= 118, st["path"])
    chk("钩子：时长按真实按下→抬起", abs(st["dur"] - 0.335) <= 0.02, st["dur"])

    tp = Tracker()
    tp.sample(950.05, 0, 0, False, False, False, "ps", True)
    tp.sample(950.09, 124, 100, True, False, False, "ps", True)
    chk("无钩子：退回 tick 位置（旧行为不变）", tp.cur_stroke["x0"] == 124, tp.cur_stroke["x0"])

    # 9 尺子校验：钩子坐标与当拍坐标差得离谱（换算系数错）时，退回当拍坐标并标 warn
    tp = Tracker(); tp.press_src = lambda: (960.0, 10.0, 10.0)
    tp.sample(960.1, 2000, 1000, False, False, False, "ps", True)
    tp.sample(960.15, 2100, 1000, True, False, False, "ps", True)
    chk("尺子校验：偏差>120px → 退回当拍坐标 + skew_warn",
        tp.cur_stroke["x0"] == 2100 and tp.skew_warn and tp.hook_skew_max >= 2090,
        (tp.cur_stroke["x0"], tp.skew_warn, tp.hook_skew_max))

    tp = Tracker(); tp.press_src = lambda: (962.0, 20.0, 20.0)
    tp.sample(962.1, 0, 0, False, False, False, "ps", True)
    tp.sample(962.15, 30, 25, True, False, False, "ps", True)
    chk("尺子校验：正常偏差（一个采样间隔内）不误报",
        tp.cur_stroke["x0"] == 20 and not tp.skew_warn and tp.hook_skew_max <= 120,
        (tp.cur_stroke["x0"], tp.skew_warn, tp.hook_skew_max))

    # 10 双屏换算：物理→虚拟按每块屏各自的系数（本机实测的两块屏做样本）
    maps = [
        {"name": "DISPLAY2", "virt": (0, 0, 1920, 1080), "phys": (0, 0, 3840, 2160),
         "primary": True, "kx": 0.5, "ky": 0.5},
        {"name": "DISPLAY1", "virt": (2560, 240, 4267, 1200), "phys": (3840, 360, 6400, 1800),
         "primary": False, "kx": 1707 / 2560, "ky": 960 / 1440},
    ]
    chk("双屏：4K@200% 物理(600,600)→虚拟(300,300)",
        all(abs(a - b) <= 1 for a, b in zip(MouseHook.map_point(maps, 600, 600), (300, 300))),
        MouseHook.map_point(maps, 600, 600))
    chk("双屏：副屏@150% 物理(4950,1200)→虚拟(3300,800)",
        all(abs(a - b) <= 1 for a, b in zip(MouseHook.map_point(maps, 4950, 1200), (3300, 800))),
        MouseHook.map_point(maps, 4950, 1200))
    chk("双屏：落在所有屏之外 → 退回主屏系数",
        all(abs(a - b) <= 1 for a, b in zip(MouseHook.map_point(maps, 9999, 9999, 0.5),
                                            (4999.5, 4999.5))),
        MouseHook.map_point(maps, 9999, 9999, 0.5))
    maps_p = [{"name": "PORTRAIT", "virt": (4800, 0, 5760, 1707), "phys": (5120, 0, 6560, 2560),
               "primary": False, "kx": 960 / 1440, "ky": 1707 / 2560}]
    chk("旋转屏：竖屏 1440×2560@150% 物理(6120,2000)→虚拟(5467,1333)",
        all(abs(a - b) <= 1 for a, b in zip(MouseHook.map_point(maps_p, 6120, 2000),
                                            (5466.7, 1333.3))),
        MouseHook.map_point(maps_p, 6120, 2000))

    # 11 兜底路径：读不到屏幕矩形（远程桌面/权限受限）→ 退回主屏系数，不崩
    orig_maps = MouseHook.__dict__["_monitor_maps"]      # 存描述符，恢复时不能拆掉 staticmethod
    try:
        MouseHook._monitor_maps = staticmethod(lambda: [])
        h = MouseHook()
        h.maps, h.scale, h.maps_ts = [], 0.5, 0.0
        got = h._to_virtual(600, 600)
        chk("读不到屏幕矩形：退回主屏系数（远程桌面兜底）",
            all(abs(a - b) <= 1 for a, b in zip(got, (300.0, 300.0))) and h.refresh_count == 0, got)
    finally:
        MouseHook._monitor_maps = orig_maps

    h2 = MouseHook()
    h2.refresh()
    h2.refresh(min_gap=0.0)
    chk("例行重读不误记为屏配置变化", h2.change_count == 0 and len(h2.maps) >= 1,
        (h2.refresh_count, h2.change_count, len(h2.maps)))

    h3 = MouseHook()
    h3.manual_scale = 0.5
    h3.maps, h3.maps_ts = [], time.time() - 99          # 表过期很久：手动系数不能被自动表顶掉
    got3 = h3._to_virtual(600, 600)
    chk("--hook-scale 手动系数不被自动重读顶掉",
        all(abs(a - b) <= 1 for a, b in zip(got3, (300.0, 300.0))) and h3.refresh_count == 0, got3)


    total = ok + len(fails)
    verdict = "PASS" if not fails else "FAIL"
    here = (os.path.abspath(sys.executable) if getattr(sys, "frozen", False)
            else os.path.abspath(__file__))
    report = [
        "绘画自习室 · 自检报告",
        f"时间       {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"结果       {verdict} {ok}/{total}",
        f"运行方式   {'打包 exe' if getattr(sys, 'frozen', False) else '源码'}  {here}",
        f"Python     {sys.version.split()[0]}",
        f"钩子换算   主屏 {MouseHook._coord_scale()}；各屏 " +
        (" / ".join(f"{m['name']}={round(m['kx'], 3)}" for m in MouseHook._monitor_maps()) or "（读不到）") +
        "（--hook-scale 可手动指定）",
        "",
    ] + lines
    try:
        out = os.path.join(app_dir(), "自检结果.txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(report) + "\n")
        out_msg = out
    except Exception as e:      # 只读目录/权限问题不该让自检判失败
        out_msg = f"（报告写文件失败：{e}）"
    print(f"SELFTEST {verdict} {ok}/{total}")
    for item in fails:
        print("  FAIL", item)
    print(f"  报告：{out_msg}")
    return 0 if not fails else 1


def probe(seconds=5):
    w32 = Win32()
    tp = Tracker()
    t0 = time.time()
    last = None
    while time.time() - t0 < seconds:
        t = time.time()
        cur = w32.cursor()
        if cur:
            tp.sample(t, cur[0], cur[1], w32.key_down(0x01), w32.key_down(0x11), w32.key_down(0x5A),
                      w32.foreground_exe(), True)
        time.sleep(1 / SAMPLE_HZ)
        last = cur
    print(json.dumps({
        "seconds": seconds, "cursor": last, "foreground": w32.foreground_exe(),
        "strokes": tp.strokes, "undos": tp.undos, "path_px": int(tp.path),
        "peak_speed": int(tp.peak), "avg_speed": tp.avg_speed()
    }, ensure_ascii=False))


def nettest():
    """两个 Room 实例互相看得见吗（组播回环，同机也能测）。"""
    a, b = Room("302", "A"), Room("302", "B")
    c = Room("999", "C")          # 别的房间，应该收不到
    time.sleep(0.4)
    a.send({"t": "s", "state": "draw"})
    b.send({"t": "s", "state": "idle"})
    time.sleep(1.2)

    def drain(room):
        got = []
        while True:
            try:
                got.append(room.inbox.get_nowait())
            except queue.Empty:
                return got

    got_b = drain(b)
    got_a = drain(a)
    got_c = drain(c)
    b.send({"t": "q", "to": a.id})
    time.sleep(0.6)
    got_a += drain(a)
    result = {
        "b_sees_a": any(m.get("id") == a.id for m in got_b),
        "a_sees_b": any(m.get("id") == b.id for m in got_a),
        "q_delivered": any(m.get("t") == "q" and m.get("to") == a.id for m in got_a),
        "other_room_ignored": len(got_c) == 0,
        "a_msgs": [m.get("t") for m in got_a],
        "b_msgs": [m.get("t") for m in got_b],
        "c_msgs": len(got_c),
    }
    ok = result["b_sees_a"] and result["a_sees_b"] and result["q_delivered"] and result["other_room_ignored"]
    print(("NETTEST PASS " if ok else "NETTEST FAIL ") + json.dumps(result, ensure_ascii=False))
    a.close(); b.close(); c.close()
    return 0 if ok else 1


def roomwatch(seconds, name, code):
    """跨进程验证：起一个 Room，打印收到的消息。"""
    r = Room(code, name)
    t0 = time.time()
    seen = []
    while time.time() - t0 < seconds:
        r.send({"t": "s", "state": "draw"})
        time.sleep(0.5)
        while True:
            try:
                m = r.inbox.get_nowait()
            except queue.Empty:
                break
            seen.append((round(m.get("_seen", 0) - t0, 1), m.get("id"), m.get("name"), m.get("t")))
    print(json.dumps({"me": r.name, "id": r.id, "got": seen}, ensure_ascii=False))
    r.close()
    return 0


def cloudtest(seconds, name, code, secret=""):
    """公网通道验证：同一个桌号，两个进程能不能透过公共 broker 互相看见。"""
    c = Cloud(code, secret)
    t0 = time.time()
    while time.time() - t0 < 4 and not c.ready:
        time.sleep(0.2)
    seen = []
    while time.time() - t0 < seconds:
        c.send({"t": "s", "id": "pid-" + name, "room": code, "name": name, "state": "draw", "ts": time.time()})
        time.sleep(1.0)
        while True:
            try:
                m = c.inbox.get_nowait()
            except queue.Empty:
                break
            seen.append((m.get("name"), m.get("t"), m.get("_via")))
    print(json.dumps({"me": name, "ready": c.ready, "err": c.err, "got": seen,
                      "topic": c.topic}, ensure_ascii=False))
    c.close()
    return 0 if c.ready else 1


def main():
    args = sys.argv[1:]
    anywhere = "--anywhere" in args
    if "--selftest" in args:
        sys.exit(selftest())
    if "--net-test" in args:
        sys.exit(nettest())
    if "--cloud-test" in args:
        i = args.index("--cloud-test")
        secs = float(args[i + 1]) if len(args) > i + 1 else 8
        cname = args[args.index("--name") + 1] if "--name" in args else "cloud"
        ccode = args[args.index("--room") + 1] if "--room" in args else "302"
        cpass = args[args.index("--pass") + 1] if "--pass" in args else ""
        sys.exit(cloudtest(secs, cname, ccode, cpass))
    if "--room-watch" in args:
        i = args.index("--room-watch")
        secs = float(args[i + 1]) if len(args) > i + 1 else 5
        name = args[args.index("--name") + 1] if "--name" in args else "watch"
        code = args[args.index("--room") + 1] if "--room" in args else "302"
        sys.exit(roomwatch(secs, name, code))
    if "--probe" in args:
        n = args[args.index("--probe") + 1] if len(args) > args.index("--probe") + 1 else "5"
        probe(float(n))
        return
    demo = "--demo" in args
    exit_after = None
    if "--exit-after" in args:
        exit_after = float(args[args.index("--exit-after") + 1])
    snapshot = None
    if "--snapshot" in args:
        snapshot = args[args.index("--snapshot") + 1]
    name = args[args.index("--name") + 1] if "--name" in args else None
    room_code = args[args.index("--room") + 1] if "--room" in args else None
    pass_arg = args[args.index("--pass") + 1] if "--pass" in args else None
    snap_delay = float(args[args.index("--snap-delay") + 1]) if "--snap-delay" in args else 1.5
    qtest = float(args[args.index("--qtest") + 1]) if "--qtest" in args else None
    solo = "--solo" in args
    # 实验标识：被试编号 / 场次 / 练习稿 / ABBA 块（不填就是日常使用，行为不变）
    def _arg(flag):
        return args[args.index(flag) + 1] if flag in args and len(args) > args.index(flag) + 1 else None
    w = Widget(anywhere=anywhere or "--livetest" in args, demo=demo, exit_after=exit_after,
               snapshot=snapshot, name=name, room_code=room_code, snap_delay=snap_delay, qtest=qtest,
               # 独桌条件强制断掉两条社交通道：--solo 单独用时也不会偷偷连进自习桌
               no_lan=("--no-lan" in args or solo), no_cloud=("--no-cloud" in args or solo),
               pass_code=pass_arg, solo=solo,
               covert=("--covert" in args), subject_id=_arg("--pid"), session_no=_arg("--session"),
               piece=_arg("--piece"), order_block=_arg("--order"), covert_peers=_arg("--covert-peers"))
    if "--autostart" in args:
        w.root.after(600, w.toggle_timer)
    if "--livetest" in args:
        w.root.after(400, w.livetest)
    if "--export-test" in args:
        delay = int(float(args[args.index("--export-after") + 1]) * 1000) if "--export-after" in args else 1200
        w.root.after(delay, lambda: (print(w.export_json()), w.root.after(300, w.quit)))
    if demo:
        here = os.path.dirname(os.path.abspath(__file__))
        w.root.update_idletasks()
        geom = {"x": w.root.winfo_x(), "y": w.root.winfo_y(),
                "w": w.root.winfo_width(), "h": w.root.winfo_height()}
        with open(os.path.join(here, "_geom.json"), "w", encoding="utf-8") as f:
            json.dump(geom, f)
    w.root.mainloop()


if __name__ == "__main__":
    main()
