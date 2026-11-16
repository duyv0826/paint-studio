# -*- utf-8 -*-
"""查每条音效结尾是不是「一刀切」——最后一个采样离 0 很远，播放时就是一记爆点。"""
import array
import wave
from pathlib import Path

SFX = Path(__file__).resolve().parent
NAMES = ["ui_tap", "ui_toggle", "pomo_start", "pomo_end", "break_start", "break_end",
         "stretch", "remind_sit", "msg_in", "join_room", "disconnect", "stroke_soft",
         "ambience_studio"]

print("%-18s %6s %8s %10s %10s %s" % ("文件", "秒", "末样本", "末样本/峰值", "末5ms均abs", "判断"))
for n in NAMES:
    p = SFX / (n + ".wav")
    with wave.open(str(p), "rb") as w:
        nch, sr = w.getnchannels(), w.getframerate()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    frames = [a[i] for i in range(0, len(a), nch)]  # 取首通道近似
    peak = max(abs(v) for v in frames) or 1
    last = frames[-1]
    tail = frames[-max(1, int(sr * 0.005)):]
    tail_rms = (sum(v * v for v in tail) / len(tail)) ** 0.5
    ratio = abs(last) / peak
    verdict = "有爆点风险" if (ratio > 0.08 or tail_rms / peak > 0.05) else "OK"
    print("%-18s %6.2f %8d %10.3f %10.4f %s" % (n, len(frames) / sr, last, ratio, tail_rms / peak, verdict))
