# -*- coding: utf-8 -*-
"""P3 定标 v2：块长真校准到 800 token + 显存安全 batch 上界 + 稳态长跑
教训记录：v1 误用 tokenize(...)[0] 取到 BatchEncoding 首键，块长校准恒判为 6 token，
        实际跑了 ~1200 token 块，数据作废。v2 用 tok(text)['input_ids'] 计数。
"""
import subprocess
import time
import torch
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer

MODEL = r"C:\Users\z1499\.cache\modelscope\models\Qwen--Qwen3-Embedding-0.6B\snapshots\master"
TARGET = 800
PHYS_GB = torch.cuda.get_device_properties(0).total_memory / 2**30
SUSTAIN = 150

tok = AutoTokenizer.from_pretrained(MODEL)


def ntok(s):
    return len(tok(s, add_special_tokens=False)["input_ids"])


unit = ("企业知识库制度文档示例段落，规定信息化项目的立项流程、预算审批权限与验收标准。"
        "项目组应当在启动前完成需求调研，并提交可行性研究报告供委员会评审。")
CHUNK = unit
while ntok(CHUNK) < TARGET:
    CHUNK += unit
while ntok(CHUNK) > TARGET + 15:
    CHUNK = CHUNK[:int(len(CHUNK) * 0.97)]
N = ntok(CHUNK)
print(f"块长校准：{len(CHUNK)} 字 = {N} token（目标 {TARGET}）| 物理显存 {PHYS_GB:.2f}GB")

m = SentenceTransformer(MODEL, device="cuda").half()
m.max_seq_length = TARGET + 64  # 别让模型截断，也别让超长序列吃爆显存

print("\n== batch 扫描（800 token 块），标注显存安全线 ==")
rows = []
for b in [8, 16, 24, 32, 48]:
    data = [CHUNK + f"（第{i}块）" for i in range(b)]
    m.encode(data[:2], batch_size=2, show_progress_bar=False)
    torch.cuda.synchronize(); t = time.perf_counter()
    m.encode(data, batch_size=b, normalize_embeddings=True, convert_to_numpy=True,
             show_progress_bar=False)
    torch.cuda.synchronize(); dt = time.perf_counter() - t
    peak = torch.cuda.max_memory_reserved() / 2**30
    cps = b / dt
    rows.append((b, cps, peak))
    print(f"  batch={b:2d} | {cps:5.1f} chunk/s | {cps*N/1000:5.1f}k tok/s | "
          f"峰值预留 {peak:4.2f}GB {'⚠超订' if peak > PHYS_GB * 0.9 else ''}")

best = max(r for r in rows if r[2] <= PHYS_GB * 0.9)
print(f"\n安全操作点：batch={best[0]} → {best[1]:.1f} chunk/s（{best[1]*N/1000:.1f}k tok/s）"
      f"| 峰值 {best[2]:.2f}GB")

import math
print(f"\n== 稳态长跑 {SUSTAIN}s（batch={best[0]}）==")
b = best[0]
samples = []
start = time.perf_counter()
last = 0
while time.perf_counter() - start < SUSTAIN:
    el = time.perf_counter() - start
    data = [CHUNK + f"（w{int(el)}-{i}）" for i in range(b)]
    t = time.perf_counter()
    m.encode(data, batch_size=b, normalize_embeddings=True, convert_to_numpy=True,
             show_progress_bar=False)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t
    if int(el) // 30 > last:
        last = int(el) // 30
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=power.draw,temperature.gpu,clocks.sm",
                 "--format=csv,noheader,nounits"], text=True).strip()
            w, tp, clk = [float(x) for x in out.split(",")]
            samples.append((b / dt, w, tp, clk))
            print(f"  t={el:5.1f}s | {b/dt:5.1f} chunk/s | {w:5.1f}W | {tp:3.0f}°C | {clk:4.0f}MHz")
        except Exception:
            samples.append((b / dt, 0, 0, 0))
            print(f"  t={el:5.1f}s | {b/dt:5.1f} chunk/s | 采样失败")

if samples:
    cps_list = [s[0] for s in samples]
    print(f"\n稳态：均值 {sum(cps_list)/len(cps_list):.1f} chunk/s | "
          f"最低 {min(cps_list):.1f} | 温度 {samples[0][2]:.0f}→{samples[-1][2]:.0f}°C | "
          f"时钟 {samples[0][3]:.0f}→{samples[-1][3]:.0f}MHz")

steady = (sum(cps_list) / len(cps_list)) if samples else best[1]
print(f"\n== 反推（稳态 {steady:.1f} chunk/s @800tok，保守取最低 {min(cps_list) if samples else best[1]:.1f}）==")
lo = min(cps_list) if samples else best[1]
for name, need in {"96h 存量": 58, "72h 冲刺": 77, "增量峰值": 140}.items():
    print(f"  {name} {need} chunk/s -> 均值档 ×{math.ceil(need/steady)} | "
          f"保守档 ×{math.ceil(need/lo)} | +1 冗余 = ×{math.ceil(need/lo)+1}")
print(f"  vs R8 S2 锚点 150 chunk/s/单元：本机达成度 {steady/150:.2f}×")
