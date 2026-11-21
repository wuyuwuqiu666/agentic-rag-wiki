"""A 区对账纯判定（FR-ING-4）：清单应索引 vs 索引实际 → 差集 + 背离口径 + markdown 报告。

无 IO、无跨区 import：只做集合差与文本渲染，落盘由 runtime 层负责。B5 熔断的"背离债"计数沿用
`reconcile()`→`GuardPort`（阈值在 `b5.yaml`）；本模块补上 PRD §8.1 FR-ING-4 明确要求的两件产物：
①对账报告落 `workspace/reconcile/`；②"一键补偿"=按缺失块反查受影响 doc，交 runtime 用清单指针重拉 objectstore 正文重放。

真实 PG↔Milvus 分钟级背离传感属双存储实机（放行项），此处口径以 stand-in 清单为账、可离线复现同一算法。
"""

from __future__ import annotations


def diff(expected_ids: set[str], actual_ids: set[str]) -> tuple[list[str], list[str]]:
    """(missing=应索引却缺, extra=索引里有但清单无=孤儿)，均按 id 升序（报告可读、可 diff）。"""
    return sorted(expected_ids - actual_ids), sorted(actual_ids - expected_ids)


def divergence_pct(missing_count: int, expected_count: int) -> float:
    """背离率（%）：缺块数 / 应索引总数；应索引为 0 时记 0（空库不告警）。"""
    if expected_count <= 0:
        return 0.0
    return 100.0 * missing_count / expected_count


def render_report(
    *,
    expected_count: int,
    actual_count: int,
    missing: list[str],
    extra: list[str],
    generated_at: float,
    writeback_missing: dict[str, list[str]] | None = None,
) -> str:
    """markdown 对账报告正文（落 workspace/reconcile/）。数字为清单↔索引实测差，非质量结论。

    `writeback_missing`（缺块的页归属分组）单列一节：回写块的正文只活在 B 区的页里，A 区按 canonical 重放
    只会造出源块，故这些 id **不经补偿作业补**；报告把它们和源块缺账分开陈列，处置口径（重编译→人审→重发布）
    就直接写在产物上，而不是只存在于代码注释里。
    """
    dv = divergence_pct(len(missing), expected_count)
    wb_total = sum(len(ids) for ids in (writeback_missing or {}).values())
    lines = [
        f"# 对账报告 @ {generated_at:.0f}",
        "",
        f"- 应索引块（清单账=源块册＋回写块册）：{expected_count}",
        f"- 实际块（索引账）：{actual_count}",
        f"- 缺失源块：{len(missing)}（背离率 {dv:.4f}%）",
        f"- 缺失权威页块（属已发布页，另列）：{wb_total}",
        f"- 孤儿块（索引有、两册清单皆无）：{len(extra)}",
        "",
        "> 报告由 B5 对账产出；背离债计数交熔断闸（阈值见 `breaker_rules/b5.yaml`）。"
        "补偿=按缺失源块反查受影响 doc 重拉 objectstore 正文重放；权威页块不经本作业补（处置在 B 区：重编译→人审→重发布）。",
        "",
    ]
    if missing:
        lines.append("## 缺失源块（待补偿）")
        lines += [f"- `{cid}`" for cid in missing]
        lines.append("")
    if writeback_missing:
        lines.append("## 缺失权威页块（交 B 区重编译，不重拉正文）")
        for page_id in sorted(writeback_missing):
            lines.append(f"- 页 `{page_id}`：{', '.join(f'`{cid}`' for cid in writeback_missing[page_id])}")
        lines.append("")
    if extra:
        lines.append("## 孤儿块（交背离债，不自动删）")
        lines += [f"- `{cid}`" for cid in extra]
        lines.append("")
    if not missing and not writeback_missing and not extra:
        lines.append("✅ 账实相符，无背离。")
    return "\n".join(lines) + "\n"
