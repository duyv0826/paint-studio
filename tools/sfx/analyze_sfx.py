# -*- coding: utf-8 -*-
"""量每一段音效的真实电平和时长，并按 v0.7 里的 VOLUME 算出有效响度。
用途：不用耳朵也能先看出哪一条偏轻/偏重、有没有削波。
"""
import json
import math
import wave
from pathlib import Path

SFX_DIR = Path(__file__).resolve().parent

VOLUME = {
    "ui_tap": 0.35,
    "ui_toggle": 0.40,
    "pomo_start": 0.55,
    "pomo_end": 0.60,
    "break_start": 0.50,
    "break_end": 0.50,
    "stretch": 0.40,
    "remind_sit": 0.50,
    "msg_in": 0.45,
    "join_room": 0.50,
    "disconnect": 0.45,
    "stroke_soft": 0.25,
    "ambience_studio": 0.18,
}


def db(x):
    if x <= 0:
        return float("-inf")
    return 20.0 * math.log10(x)


def measure(path):
    with wave.open(str(path), "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        return {"error": "expect 16-bit pcm, got %d bytes/sample" % sw}
    # 解 16-bit 小端
    import array
    a = array.array("h")
    a.frombytes(raw)
    if nch > 1:
        # 取各通道绝对值的最大值作为通道电平，几何平均近似整段 loudly
        frames = [a[i:i + nch] for i in range(0, len(a) - nch + 1, nch)]
        peak = max(max(abs(v) for v in fr) for fr in frames) if frames else 0
        ssum = 0.0
        cnt = 0
        for fr in frames:
            for v in fr:
                ssum += float(v) * float(v)
                cnt += 1
        rms = math.sqrt(ssum / cnt) if cnt else 0.0
    else:
        peak = max((abs(v) for v in a), default=0)
        ssum = sum(float(v) * float(v) for v in a)
        rms = math.sqrt(ssum / len(a)) if a else 0.0

    fs_peak = peak / 32768.0
    fs_rms = rms / 32768.0
    # 每秒 1 帧的短时能量包络，看它是不是「先响后弱」还是「一路平」
    frame = sr
    env = []
    mono = []
    for i in range(0, len(a) - nch + 1, nch):
        v = 0.0
        for k in range(nch):
            v += float(a[i + k])
        mono.append(v / nch)
    for s in range(0, len(mono), frame):
        chunk = mono[s:s + frame]
        if not chunk:
            continue
        e = math.sqrt(sum(x * x for x in chunk) / len(chunk))
        env.append(round(db(e / 32768.0), 1))

    return {
        "file": path.name,
        "seconds": round(n / float(sr), 3),
        "sample_rate": sr,
        "channels": nch,
        "peak_dbfs": round(db(fs_peak), 1),
        "rms_dbfs": round(db(fs_rms), 1),
        "peak_fs": round(fs_peak, 4),
        "clipped": fs_peak >= 0.999,
        "env_dbfs_per_sec": env,
        "bytes": path.stat().st_size,
    }


def main():
    rows = []
    for name, vol in VOLUME.items():
        p = SFX_DIR / (name + ".wav")
        if not p.exists():
            rows.append({"file": name + ".wav", "error": "missing"})
            continue
        m = measure(p)
        if "error" in m:
            rows.append(m)
            continue
        m["key"] = name
        m["volume"] = vol
        # 有效电平 = 源电平 + 20log10(增益)
        gdb = db(vol)
        m["gain_dbfs"] = round(gdb, 1)
        m["eff_rms_dbfs"] = round(m["rms_dbfs"] + gdb, 1)
        m["eff_peak_dbfs"] = round(m["peak_dbfs"] + gdb, 1)
        rows.append(m)

    out = SFX_DIR / "音效电平实测.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print("%-20s %6s %8s %8s %6s %10s %10s" % ("文件", "秒", "峰值", "RMS", "增益", "有效RMS", "有效峰值"))
    for r in rows:
        if "error" in r:
            print("%-20s %s" % (r["file"], r["error"]))
            continue
        print("%-20s %6.2f %8.1f %8.1f %6.2f %10.1f %10.1f%s" % (
            r["file"], r["seconds"], r["peak_dbfs"], r["rms_dbfs"], r["volume"],
            r["eff_rms_dbfs"], r["eff_peak_dbfs"], "  <削波>" if r["clipped"] else ""))
    print("\n写好：" + str(out))


if __name__ == "__main__":
    main()
