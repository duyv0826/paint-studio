# -*- utf-8 -*-
"""给 ambience_studio 做个循环接缝交叉淡化。

问题：它是 loop=true 的环境声，18 秒一轮。结尾最后一个采样 -594、开头是什么见输出，
接缝处如果跳变，就会每 18 秒响一下「嗒」，25 分钟安静自习里要响 80 多次。
做法：把尾部 N 毫秒与头部 N 毫秒等权交叉，再截掉尾部 N 毫秒，接缝变成连续；
时长从 18.00 s 变成 17.97 s，差异对循环无影响。
"""
import array
import shutil
import wave
from pathlib import Path

SFX = Path(__file__).resolve().parent
BAK = SFX / "_原始电平_20261115"
NAME = "ambience_studio"
FADE_MS = 30


def seam_jump(samples):
    return abs(samples[0] - samples[-1])


def main():
    p = SFX / (NAME + ".wav")
    with wave.open(str(p), "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    a = array.array("h")
    a.frombytes(raw)

    # 逐帧取首通道看接缝；多通道按帧整体处理，保证通道对齐
    nb = int(sr * FADE_MS / 1000.0)
    l = seam_jump([a[i] for i in range(0, len(a), nch)])
    peak = max(abs(v) for v in a)
    print("原接缝跳变 %d（占峰值 %.1f%%）  时长 %.3f s" % (l, 100.0 * l / peak, n / sr))

    BAK.mkdir(exist_ok=True)
    bak = BAK / (NAME + "_改动前.wav")
    if not bak.exists():
        shutil.copy2(p, bak)

    data = a.tolist()
    frames = len(data) // nch
    keep = frames - nb
    out = [0] * (keep * nch)
    # 前 nb 帧：头部淡入 + 尾部淡出
    for f in range(nb):
        wgt = f / float(nb - 1)
        for c in range(nch):
            head = data[f * nch + c]
            tail = data[(keep + f) * nch + c]
            out[f * nch + c] = int(round(head * wgt + tail * (1.0 - wgt)))
    for f in range(nb, keep):
        for c in range(nch):
            out[f * nch + c] = data[f * nch + c]

    with wave.open(str(p), "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        w.writeframes(array.array("h", out).tobytes())

    b = array.array("h", out)
    l2 = seam_jump([b[i] for i in range(0, len(b), nch)])
    print("新接缝跳变 %d（占峰值 %.1f%%）  时长 %.3f s（短了 %.0f ms）"
          % (l2, 100.0 * l2 / peak, keep / sr, nb * 1000.0 / sr))
    print("备份：" + str(bak))


if __name__ == "__main__":
    main()
