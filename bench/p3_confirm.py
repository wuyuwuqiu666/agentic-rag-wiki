# -*- coding: utf-8 -*-
"""P3 复测：加密批档找真实拐点 + 可复现性 + 超订行为确认"""
import os
import time
import torch
from sentence_transformers import SentenceTransformer

MODEL = os.environ.get("P3_MODEL", "Qwen3-Embedding-0.6B")
base = ("企业知识库制度文档示例段落，规定信息化项目的立项流程、预算审批权限与验收标准。"
        "项目组应当在启动前完成需求调研，并提交可行性研究报告供委员会评审。")
chunk = (base * 20)[:950]

m = SentenceTransformer(MODEL, device="cuda").half()
print("max_seq", m.max_seq_length)

for b in [16, 24, 32, 48, 64, 96]:
    data = [chunk + f"（第{i}块）" for i in range(b)]
    m.encode(data[:2], batch_size=2, show_progress_bar=False)  # 预热
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t = time.perf_counter()
    m.encode(data, batch_size=b, normalize_embeddings=True, convert_to_numpy=True,
             show_progress_bar=False)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t
    peak = torch.cuda.max_memory_reserved() / 2**30
    phys = torch.cuda.get_device_properties(0).total_memory / 2**30
    print(f"batch={b:3d} | {b/dt:6.1f} chunk/s | 峰值预留 {peak:5.2f}GB"
          f"{'  ⚠超物理显存→WDDM 落系统内存' if peak > phys else ''}")

print("\n== 可复现性：batch=32 连测 3 次 ==")
data = [chunk + f"（第{i}块）" for i in range(32)]
for i in range(3):
    torch.cuda.synchronize()
    t = time.perf_counter()
    m.encode(data, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
             show_progress_bar=False)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t
    print(f"  run{i+1}: {32/dt:5.1f} chunk/s | {32/dt*531/1000:5.1f}k tok/s")
