# -*- utf-8 -*-
"""验证单文件版：① 13 个内联键与 sfx 目录逐字节一致；② 只算采样数据（跳过 WAV 头）的峰值。
WAV 头里的长度字段会被当成采样算进去，所以必须用 wave 模块读帧，不能直接对整文件取最大值。"""
import array
import base64
import io
import math
import re
import wave

HTML = r"C:\Users\duyv\DoubaoWork\chats\2026-11-13\new-chat-28\绘画自习室_单文件版_v0.7_20261114.html"
SFX = r"C:\Users\duyv\DoubaoWork\chats\2026-11-13\new-chat-28\sfx"

s = io.open(HTML, encoding="utf-8").read()
pat = re.compile(r'([a-z_]+): "(data:audio/wav;base64,[A-Za-z0-9+/=]+)"')
found = dict(pat.findall(s))


def peak_db_of_wav_bytes(raw):
    with wave.open(io.BytesIO(raw), "rb") as w:
        n = w.getnframes()
        data = w.readframes(n)
    a = array.array("h")
    a.frombytes(data)
    p = max((abs(v) for v in a), default=0) / 32768.0
    return (20 * math.log10(p)) if p else -999.0, round(n / float(w.getframerate()), 3)


print("内联键：%d 个\n" % len(found))
same_all = True
for name, uri in sorted(found.items()):
    raw = base64.b64decode(uri.split(",", 1)[1])
    disk = open(SFX + "\\" + name + ".wav", "rb").read()
    same = (disk == raw)
    db, sec = peak_db_of_wav_bytes(raw)
    if not same:
        same_all = False
    print("%-18s %5.2f 秒  采样峰值 %6.1f dBFS  与磁盘一致=%s" % (name, sec, db, same))

print("\n逐字节一致：%s" % ("是，13/13" if same_all else "有差异"))
print("sfx/ 出现的位置（应只在注释里）：")
for m in re.finditer(r"[^\\n]{0,50}sfx/[^\\n]{0,50}", s):
    print("  " + m.group(0).strip())
