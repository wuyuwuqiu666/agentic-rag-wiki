"""M4-c FR-CAP-2/5 降级路径演练（§13 M4：in-flight 打满→逐级降不雪崩、读侧保）。

dev/合成档演练：证明"降级优于雪崩"的**结构不变式**——越 soft/hard 阈只告警降级、绝不抛未捕获异常、
读侧始终可服务、并发预算进出可回收。真机 1 QPS 持续压测/RT 数字属放行实测项（见文件末 skip 用例）。
"""

from __future__ import annotations

import os
import threading

import pytest
from src.partitions.ingest.service.ask import ladder
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import Document

_TEXT = "# 检索\n\n" + "\n\n".join(f"第{i}段：向量检索与 BM25 融合的降级演练说明。" for i in range(30))


def _drill_container(tmp_path):
    c = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=""))
    c.ingest.process_document(Document(doc_id="d1", version=1, domain="hr", acl_tags=["hr"], raw_text=_TEXT))
    return c


def test_inflight_saturation_alarms_degrades_but_never_blocks_reads(tmp_path) -> None:
    """打满在飞预算：B2 只 alarm（level=soft，越 hard 也不 block）；问答读侧照常服务、无未捕获异常、预算可回收。"""
    c = _drill_container(tmp_path)
    tracker = c.inflight

    for _ in range(6):  # 越过 b2.yaml soft=6、未达 hard=8 = FR-CAP-2 档 1
        tracker.acquire()

    b2 = [e for e in c.audit.read_all() if e.extra.get("breaker") == "B2"]
    assert b2, "越 soft 阈须留 B2 告警"
    assert all(e.extra.get("action") == "alarm" for e in b2)  # 全程只告警，绝不 block（不阻断=不雪崩）

    # 档 1=弃④：多跳预算收走，单跳检索+生成照常——第 7 个在飞仍拿到带引用的真答案，非报错
    first = c.app.answer("向量检索融合", user_acl=["hr"])
    assert first is not None and not first.refused and first.hops == 1 and first.citations

    tracker.acquire()
    tracker.acquire()  # 补到 hard=8 = 档 2：弃④③、停 LLM 生成
    # 换一个问法：①层缓存命中本就不该被降档拦掉（命中=零成本），此处要验的是**未命中**路径的降档
    second = c.app.answer("向量检索融合的降级", user_acl=["hr"])
    # 次好答案的档 2 形态=可呈现的降级说明件（Answer，不是异常/超时）；本域无②层已编译页→如实告知、不硬编答案
    assert second.degraded and second.text.startswith(ladder.DEGRADE_NOTICE)
    assert second.refused and not second.citations

    for _ in range(8):
        tracker.release()
    assert tracker.count == 0  # 并发预算进出对称，不虚增、不永久占用


def test_five_concurrent_sessions_served_without_avalanche(tmp_path) -> None:
    """5 并发会话同时问答：全部拿到可服务答案（或优雅拒答），无一崩溃/未捕获异常。"""
    c = _drill_container(tmp_path)
    barrier = threading.Barrier(5)
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=5)  # 制造真实重叠窗口
            ans = c.app.answer("向量检索融合", user_acl=["hr"])
            with lock:
                results.append(ans)
        except BaseException as exc:  # noqa: BLE001 - 演练需捕获任意异常证明不雪崩
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"并发降级不得雪崩：{errors}"
    assert len(results) == 5  # 5 路全返回
    assert c.inflight.count == 0  # 出口全部回收


@pytest.mark.skipif(
    os.getenv("RUN_PERF") != "1",
    reason="FR-CAP-5 真机压测数字=放行实测项（需真模型/真负载，非合成档可自证）",
)
def test_sustained_1qps_real_numbers() -> None:  # pragma: no cover - 放行后实机执行
    raise AssertionError("占位：真机 1 QPS 持续压测 + RT P95 采集，待用户放行后接真实供给执行")
