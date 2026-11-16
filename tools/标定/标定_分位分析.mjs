#!/usr/bin/env node
/**
 * 标定分位分析 —— 读「笔速标定采集页」导出的 JSON，给出三档阈值的建议取值。
 *
 * 用法：
 *   node 标定_分位分析.mjs "<采集 JSON 所在目录>"          # 读目录下所有 标定_*.json
 *   node 标定_分位分析.mjs "<目录>" --write                  # 同时写出 建议_三档阈值.md
 *   node 标定_分位分析.mjs --demo                           # 用合成数据自检，不需要真实文件
 *
 * 口径说明（写进论文/答辩时照这个说）：
 *   - n ≤ 10 只能说「初步标定（n=…）」，不能写「实验验证」。
 *   - 本脚本给的是分位数驱动的建议值，不是显著性结论。
 */

import fs from "node:fs";
import path from "node:path";

const argv = process.argv.slice(2);
const DEMO = argv.includes("--demo");
const WRITE = argv.includes("--write");
const dirArg = argv.find(a => !a.startsWith("--"));
const DIR = dirArg ? path.resolve(dirArg) : process.cwd();

/* ── 分位数（线性插值） ─────────────────────────────── */
function q(sorted, p) {
  if (!sorted.length) return NaN;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}
const qs = (arr, p) => q([...arr].sort((a, b) => a - b), p);
const r2 = v => Math.round(v * 100) / 100;
const r0 = v => Math.round(v);

/* ── 单个被试的派生指标 ─────────────────────────────── */
function metricsOf(rec, GATE) {
  const samples = rec.samples || [];
  const speeds = samples.map(s => s[1]).filter(v => v > 0);
  const gaps = rec.idle_gaps_sec || [];
  const drops = [];

  // 20 s 滚动窗口峰值（与原型断崖判定同一个窗口）
  let winStart = 0;
  let winPeak = 0;
  for (let i = 0; i < samples.length; i++) {
    const [t, v] = samples[i];
    while (winStart < i && samples[winStart][0] < t - 20000) winStart++;
    // 重算窗口峰值（数据量小，直接扫，保证正确）
    winPeak = 0;
    for (let j = winStart; j <= i; j++) if (samples[j][1] > winPeak) winPeak = samples[j][1];

    if (winPeak >= GATE && v > 20 && v < 0.95 * winPeak) drops.push(v / winPeak);
  }

  return {
    id: rec.participant_id || "未编号",
    dur: rec.duration_sec || 0,
    strokes: rec.strokes || 0,
    n: samples.length,
    peak: rec.peak_px_s || Math.max(...speeds, 0),
    slowRatio: samples.length ? r2(speeds.filter(v => v < 20).length / samples.length) : 0,
    speedQs: {
      p10: r0(qs(speeds, .10)), p25: r0(qs(speeds, .25)),
      p50: r0(qs(speeds, .50)), p75: r0(qs(speeds, .75)), p90: r0(qs(speeds, .90)),
    },
    gapQs: gaps.length ? {
      p25: r2(qs(gaps, .25)), p50: r2(qs(gaps, .50)), p75: r2(qs(gaps, .75)), p90: r2(qs(gaps, .90)),
      count: gaps.length, max: r2(Math.max(...gaps)),
    } : null,
    dropQs: drops.length ? {
      p25: r2(qs(drops, .25)), p50: r2(qs(drops, .50)), p75: r2(qs(drops, .75)),
      count: drops.length,
    } : null,
    _drops: drops,
    _gaps: gaps,
    _speeds: speeds,
  };
}

/* ── 合成数据（自检用） ─────────────────────────────── */
function synth(id, seed) {
  // 伪随机，保证每次自检结果可复现
  let s = seed;
  const rnd = () => { s = (s * 1664525 + 1013904223) % 4294967296; return s / 4294967296; };
  const samples = [];
  const gaps = [];
  let t = 0;
  const segs = 9;
  for (let g = 0; g < segs; g++) {
    const drawLen = 60 + Math.floor(rnd() * 110) * 4;       // 采样点数
    const base = 180 + rnd() * 620;                          // 该段基准笔速
    let v = base * 1.35;
    for (let i = 0; i < drawLen; i++) {
      // 段内先快后慢，制造断崖
      const fall = Math.max(0, 1 - i / (drawLen * 0.55));
      v = base * (0.28 + 0.72 * fall) + (rnd() - 0.5) * 40;
      samples.push([t, Math.max(0, Math.round(v))]);
      t += 250;
    }
    // 停手
    const idle = 30 + rnd() * 150;
    gaps.push(r2(idle));
    for (let ms = 0; ms < idle * 1000; ms += 250) {
      samples.push([t, 0]);
      t += 250;
    }
  }
  return {
    kind: "speed_calibration", participant_id: id, duration_sec: Math.round(t / 1000),
    canvas: { w: 900, h: 600, dpr: 1 }, sample_hz: 4, strokes: segs,
    peak_px_s: Math.max(...samples.map(s => s[1]), 0),
    samples, idle_gaps_sec: gaps,
  };
}

/* ── 读入 ───────────────────────────────────────────── */
let records = [];
if (DEMO) {
  records = [synth("S01", 7), synth("S02", 11), synth("S03", 23), synth("S04", 41), synth("S05", 59), synth("S06", 97)];
  console.log("【自检模式】使用合成数据，仅验证脚本逻辑，结果不可当作标定值。\n");
} else {
  if (!fs.existsSync(DIR)) { console.error("目录不存在：" + DIR); process.exit(1); }
  const files = fs.readdirSync(DIR).filter(f => f.toLowerCase().endsWith(".json") && f.includes("标定"));
  if (!files.length) { console.error("该目录下没有 标定_*.json：" + DIR); process.exit(1); }
  for (const f of files) {
    try {
      const rec = JSON.parse(fs.readFileSync(path.join(DIR, f), "utf8"));
      if (rec.kind !== "speed_calibration") { console.warn("跳过（不是采集文件）：" + f); continue; }
      records.push(rec);
    } catch (e) { console.warn("跳过（解析失败）：" + f + " — " + e.message); }
  }
}

if (!records.length) { console.error("没有可用的采集记录。"); process.exit(1); }

/* ── 画布一致性（px/s 可比的前提） ───────────────────── */
const canvases = [...new Set(records.map(r => `${r.canvas?.w}x${r.canvas?.h}`))];
if (canvases.length > 1) {
  console.warn("⚠ 画布尺寸不一致（" + canvases.join(" / ") + "）：px/s 不可直接合并，以下结果仅供参考。\n");
}

/* ── 逐人指标 ───────────────────────────────────────── */
const GATE = 300;                        // 与原型断崖门槛一致，用于提取"掉落比例"样本
const per = records.map(r => metricsOf(r, GATE));
const allGaps = per.flatMap(p => p._gaps);
const allDrops = per.flatMap(p => p._drops);
const allSpeeds = per.flatMap(p => p._speeds);

console.log(`样本：${per.length} 人 · 总采样点 ${per.reduce((a, p) => a + p.n, 0)} · 停笔段 ${allGaps.length} · 掉落样本 ${allDrops.length}\n`);

console.log("被试 | 时长(s) | 落笔 | 峰值 | 停笔段 | 停笔 p25/p50/p75 | 笔速 p25/p50/p75 | 掉落 p25/p50/p75");
console.log("-".repeat(118));
for (const p of per) {
  const g = p.gapQs ? `${p.gapQs.p25}/${p.gapQs.p50}/${p.gapQs.p75}` : "—";
  const d = p.dropQs ? `${p.dropQs.p25}/${p.dropQs.p50}/${p.dropQs.p75}` : "—";
  const s = `${p.speedQs.p25}/${p.speedQs.p50}/${p.speedQs.p75}`;
  console.log(`${p.id.padEnd(5)} | ${String(p.dur).padStart(7)} | ${String(p.strokes).padStart(4)} | ${String(p.peak).padStart(5)} | ${String(p.gapQs ? p.gapQs.count : 0).padStart(6)} | ${g.padEnd(17)} | ${s.padEnd(17)} | ${d}`);
}

/* ── 汇总分位 ───────────────────────────────────────── */
const pooled = {
  gap: { p25: qs(allGaps, .25), p50: qs(allGaps, .50), p75: qs(allGaps, .75), p90: qs(allGaps, .90) },
  drop: { p25: qs(allDrops, .25), p50: qs(allDrops, .50), p75: qs(allDrops, .75) },
  speed: { p10: qs(allSpeeds, .10), p25: qs(allSpeeds, .25), p50: qs(allSpeeds, .50), p90: qs(allSpeeds, .90) },
};

console.log("\n汇总（全样本合并）");
console.log(`  停笔段  p25=${r2(pooled.gap.p25)}s  p50=${r2(pooled.gap.p50)}s  p75=${r2(pooled.gap.p75)}s  p90=${r2(pooled.gap.p90)}s`);
console.log(`  掉落比例 p25=${r2(pooled.drop.p25)}  p50=${r2(pooled.drop.p50)}  p75=${r2(pooled.drop.p75)}`);
console.log(`  笔速    p10=${r0(pooled.speed.p10)}  p25=${r0(pooled.speed.p25)}  p50=${r0(pooled.speed.p50)}  p90=${r0(pooled.speed.p90)} px/s`);

/* ── 建议取值 ───────────────────────────────────────── */
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// 三档由松到紧：温柔取"更宽容"的分位，严厉取"更敏感"的分位
let idle   = [r0(clamp(pooled.gap.p75, 25, 150)), r0(clamp(pooled.gap.p50, 12, 60)), r0(clamp(pooled.gap.p25, 5, 30))];
let blur   = idle.map(v => r0(clamp(v * 1.5, 15, 180)));
let ratio  = [r2(clamp(pooled.drop.p25, .2, .55)), r2(clamp(pooled.drop.p50, .25, .6)), r2(clamp(pooled.drop.p75, .3, .65))];
let gate   = r0(clamp(pooled.speed.p25, 120, 600));

// 保证单调（温柔 > 标准 > 严厉 的阈值；比例则相反）
if (idle[0] <= idle[1]) idle[0] = idle[1] + 10;
if (idle[1] <= idle[2]) idle[1] = idle[2] + 5;
if (blur[1] <= idle[1]) blur[1] = idle[1] + 15;
if (blur[0] <= blur[1]) blur[0] = blur[1] + 15;
if (ratio[0] >= ratio[1]) ratio[0] = r2(ratio[1] - .05 < .2 ? .2 : ratio[1] - .05);
if (ratio[1] >= ratio[2]) ratio[2] = r2(ratio[1] + .05 > .65 ? .65 : ratio[1] + .05);

const suggest = `const LEVEL_PROFILES = {
  gentle:  { key:"gentle",   label:"温柔", idleSec:${idle[0]}, blurSec:${blur[0]}, cliffRatio:${ratio[0]}, cliffDwellSec:4, cooldownSec:45 },
  standard:{ key:"standard", label:"标准", idleSec:${idle[1]}, blurSec:${blur[1]}, cliffRatio:${ratio[1]}, cliffDwellSec:3, cooldownSec:30 },
  strict:  { key:"strict",   label:"严厉", idleSec:${idle[2]}, blurSec:${blur[2]}, cliffRatio:${ratio[2]}, cliffDwellSec:2, cooldownSec:15 }
};`;

console.log("\n建议取值（分位数驱动，非显著性结论）");
console.log(suggest);
console.log(`\n断崖门槛由 300 px/s 调整为 ${gate} px/s（本样本笔速 p25；下限保护 ${gate === 120 ? "120 已生效" : "120"}，门槛过低会把慢速细画误判为断崖）。`);
console.log("保持不动的参数：cliffDwellSec 4/3/2、cooldownSec 45/30/15（这两组靠屏幕刷新粒度与提醒节奏决定，改它们会连带改变交互手感，与笔速分布无关）。");

/* ── 落盘 ───────────────────────────────────────────── */
if (WRITE) {
  const out = path.join(DEMO ? process.cwd() : DIR, "建议_三档阈值.md");
  const md = [
    `# 三档阈值建议（${new Date().toISOString().slice(0, 10)}）`,
    "",
    `样本：**${per.length} 人**（${per.map(p => p.id).join(" / ")}）· 总采样点 ${per.reduce((a, p) => a + p.n, 0)}`,
    `口径：**初步标定（n=${per.length}）**，不是实验验证。n 再小，就只能当前期取值用。`,
    "",
    "## 逐人指标",
    "",
    "| 被试 | 时长(s) | 落笔 | 峰值 | 停笔段 | 停笔 p25/p50/p75 | 笔速 p25/p50/p75 | 掉落 p25/p50/p75 |",
    "|---|---|---|---|---|---|---|---|",
    ...per.map(p => {
      const g = p.gapQs ? `${p.gapQs.p25}/${p.gapQs.p50}/${p.gapQs.p75}` : "—";
      const d = p.dropQs ? `${p.dropQs.p25}/${p.dropQs.p50}/${p.dropQs.p75}` : "—";
      const s = `${p.speedQs.p25}/${p.speedQs.p50}/${p.speedQs.p75}`;
      return `| ${p.id} | ${p.dur} | ${p.strokes} | ${p.peak} | ${p.gapQs ? p.gapQs.count : 0} | ${g} | ${s} | ${d} |`;
    }),
    "",
    "## 合并分位",
    "",
    `- 停笔段：p25 ${r2(pooled.gap.p25)}s / p50 ${r2(pooled.gap.p50)}s / p75 ${r2(pooled.gap.p75)}s / p90 ${r2(pooled.gap.p90)}s`,
    `- 掉落比例：p25 ${r2(pooled.drop.p25)} / p50 ${r2(pooled.drop.p50)} / p75 ${r2(pooled.drop.p75)}`,
    `- 笔速：p10 ${r0(pooled.speed.p10)} / p25 ${r0(pooled.speed.p25)} / p50 ${r0(pooled.speed.p50)} / p90 ${r0(pooled.speed.p90)} px/s`,
    "",
    "## 建议写入原型",
    "",
    "```js",
    suggest,
    "```",
    "",
    `- 断崖门槛：300 → **${gate} px/s**（本样本 p25）`,
    "- 保持不变：`cliffDwellSec` 4/3/2、`cooldownSec` 45/30/15（与笔速分布无关，改动会连带改变交互手感）",
    "",
    "## 写这些数字时该配的话",
    "",
    `> 三档阈值取自 ${per.length} 人自由绘画的笔速与停笔分布（p25/p50/p75 分位），为初步标定；样本量不足，不构成实验验证。`,
    "",
    "页面里对应把「设计取值，非实测标定」改成「n=" + per.length + " 初步标定」，并在方法一节写明采样方式（4 Hz、900×600 画布、自由绘画、含自然停笔）。",
    "",
  ].join("\n");
  fs.writeFileSync(out, md, "utf8");
  console.log("\n已写出：" + out);
}
