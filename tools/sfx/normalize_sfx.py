# -*- coding: utf-8 -*-
"""只抬两条「听不见」的音效电平，其余一律不动。

依据 sfx\音效电平实测.json：
  stroke_soft  峰值 -48.2 dBFS，按 VOLUME 0.25 后有效峰值 -60.2 dBFS  → 等于没声音
  ui_tap       峰值 -27.7 dBFS，按 VOLUME 0.35 后有效峰值 -36.8 dBFS  → 很轻
做法：直接把 wav 的采样乘一个常数（增益），VOLUME 表一个数字都不改，所以
说明文档里的 VOLUME 口径、HTML 里的代码都不用动。
"""
import array
import math
import shutil
import wave
from pathlib import Path

SFX = Path(__file__).resolve().parent
BAK = SFX / "_原始电平_20261115"

# (名字, 目标有效峰值 dBFS)  有效峰值 = 源峰值 + 20log10(VOLUME)
VOLUME = {"stroke_soft": 0.25, "ui_tap": 0.35, "ui_toggle": 0.40}
TARGET_EFF_PEAK = {"stroke_soft": -24.0, "ui_tap": -24.0, "ui_toggle": -24.0}


def read(path):
    with wave.open(str(path), "rb") as w:
        params = (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
        raw = w.readframes(params[3])
    return params, raw


def write(path, params, samples_bytes):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(params[0])
        w.setsampwidth(params[1])
        w.setframerate(params[2])
        w.writeframes(samples_bytes)


def peak_of(raw):
    a = array.array("h")
    a.frombytes(raw)
    return max((abs(v) for v in a), default=0) / 32768.0


def main():
    BAK.mkdir(exist_ok=True)
    for name, target in TARGET_EFF_PEAK.items():
        src = SFX / (name + ".wav")
        params, raw = read(src)
        if params[1] != 2:
            raise SystemExit("%s 不是 16-bit，脚本不处理" % name)
        cur_peak = peak_of(raw)
        cur_peak_db = 20 * math.log10(cur_peak)
        eff_peak_db = cur_peak_db + 20 * math.log10(VOLUME[name])
        need_db = target - eff_peak_db
        if need_db <= 0.5:
            print("%s 已经够响（有效峰值 %.1f dBFS），跳过" % (name, eff_peak_db))
            continue
        factor = 10 ** (need_db / 20.0)
        new_peak = cur_peak * factor
        if new_peak > 0.95:
            raise SystemExit("%s 放大后会削波（%.3f FS），中止" % (name, new_peak))

        # 原文件先备份（只备份第一次）
        bak = BAK / (name + "_改动前.wav")
        if not bak.exists():
            shutil.copy2(src, bak)

        a = array.array("h")
        a.frombytes(raw)
        for i, v in enumerate(a):
            nv = int(round(v * factor))
            a[i] = 32767 if nv > 32767 else (-32768 if nv < -32768 else nv)
        write(src, params, a.tobytes())

        m = peak_of(a.tobytes())
        print("%s  +%.1f dB (×%.1f)  峰值 %.1f→%.1f dBFS  有效峰值 %.1f→%.1f dBFS"
              % (name, need_db, factor, cur_peak_db, 20 * math.log10(m),
                 eff_peak_db, 20 * math.log10(m) + 20 * math.log10(VOLUME[name])))

    print("\n备份在：" + str(BAK))


if __name__ == "__main__":
    main()
