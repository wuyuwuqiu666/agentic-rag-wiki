# -*- coding: utf-8 -*-
"""R8 P3 实测定标：本机 RTX 5060 Laptop 8GB × 本地 embedding 吞吐基准
用途：填 R8 §10 P3 留白行；反推 S2 档所需推理单元数。
口径：合成中文语料 ~800 token/chunk（对齐 R8 t̄=800 设计点），fp16 CUDA，
     批档 8/32/64/128，每档 3 次计时丢弃首轮预热。
用法：python p3_embedding_bench.py [模型路径或名称]
"""
import json
import math
import sys
import time

import torch
from sentence_transformers import SentenceTransformer

MODEL = sys.argv[1] if len(sys.argv) > 1 else "Qwen3-Embedding-0.6B"
BATCHES = [8, 32, 64, 128]
REP = 3

CHUNK_CHARS = 950  # Qwen 分词器中文约 1 字≈1 token，对齐 ~800 token 设计点
base = ("企业知识库制度文档示例段落，规定信息化项目的立项流程、预算审批权限与验收标准。"
        "项目组应当在启动前完成需求调研，并提交可行性研究报告供委员会评审。")
chunk = (base * 20)[:CHUNK_CHARS]


def main():
    assert torch.cuda.is_available(), "CUDA 不可用——检查 torch 是否为 cu128 构建"
    props = torch.cuda.get_device_properties(0)
    print(f"torch {torch.__version__} (cuda {torch.version.cuda}) | {props.name} | "
          f"{props.total_memory / 2**30:.2f} GB | capability {torch.cuda.get_device_capability(0)}")

    t0 = time.perf_counter()
    m = SentenceTransformer(MODEL, device="cuda").half()
    print(f"模型 {MODEL} | 加载 {time.perf_counter() - t0:.1f}s | max_seq={m.max_seq_length}")

    tok = m.preprocess([chunk])
    ids = tok["input_ids"]
    n_tok = len(ids[0]) if hasattr(ids, "__getitem__") else len(ids[0].tolist())
    print(f"实测单块 token 数 = {n_tok}")

    results = {}
    oom = []
    for b in BATCHES:
        data = [chunk + f"（第{i}块变体后缀）" for i in range(b)]
        times = []
        try:
            for r in range(REP):
                torch.cuda.synchronize()
                t = time.perf_counter()
                out = m.encode(data, batch_size=b, normalize_embeddings=True,
                               convert_to_numpy=True, show_progress_bar=False)
                torch.cuda.synchronize()
                dt = time.perf_counter() - t
                if r > 0:
                    times.append(dt)
        except torch.cuda.OutOfMemoryError:
            print(f"batch={b:4d} -> OOM（8GB 档上限，此档不计）")
            torch.cuda.empty_cache()
            oom.append(b)
            continue
        mean_dt = sum(times) / len(times)
        cps = b / mean_dt
        results[b] = {
            "chunks_per_s": round(cps, 1),
            "tokens_per_s": int(cps * n_tok),
            "ms_per_batch": round(mean_dt * 1000, 1),
            "ms_per_chunk": round(mean_dt * 1000 / b, 1),
            "dim": int(out.shape[1]),
            "vram_alloc_GB": round(torch.cuda.memory_allocated() / 2**30, 2),
            "vram_reserved_GB": round(torch.cuda.memory_reserved() / 2**30, 2),
        }
        print(f"batch={b:4d} -> {cps:6.1f} chunk/s | {results[b]['tokens_per_s']/1000:6.1f}k tok/s | "
              f"单块 {results[b]['ms_per_chunk']:5.1f}ms | dim={results[b]['dim']} | "
              f"显存 占用{results[b]['vram_alloc_GB']}GB/缓存{results[b]['vram_reserved_GB']}GB")

    best = max(r["chunks_per_s"] for r in results.values())
    print(f"\n== 反推：本机最优 {best} chunk/s（800 token 块）==")
    for name, need in {"96h 存量(58)": 58, "72h 冲刺(77)": 77, "增量峰值(140)": 140}.items():
        cards = math.ceil(need / best)
        print(f"  {name} chunk/s -> 同档卡 ×{cards}，N+1 冗余 = ×{cards + 1}")
    print(f"  对照 R8 S2 锚点 150 chunk/s/单元：本机达成度 = {best / 150:.2f}×")

    with open("p3_bench_result.json", "w", encoding="utf-8") as f:
        json.dump({"torch": torch.__version__, "cuda": torch.version.cuda,
                   "gpu": props.name, "vram_GB": round(props.total_memory / 2**30, 2),
                   "model": MODEL, "dtype": "fp16", "tokens_per_chunk": n_tok,
                   "batches": results,
                   "oom_batches": oom,
                   "best_chunks_per_s": best,
                   "units_needed": {n: math.ceil(v / best) for n, v in
                                    {"96h": 58, "72h": 77, "peak": 140}.items()}},
                  f, ensure_ascii=False, indent=2)
    print("已写 p3_bench_result.json")


if __name__ == "__main__":
    main()
