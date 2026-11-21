"""FR-CAP-1① 语义缓存代码路径 + v0.6 安全硬条款（§12 生成后门：缓存键含 ACL 域指纹、无引用不得入缓存）。

命中率数字仅企业档生效（§8 撤下承诺），本测试只验**结构不变式**：跨 ACL 域绝不命中、未接地/拒答绝不入缓存。
dev 档=MemoryCache 精确键；真·相似召回为企业换插档（G5：键/准入不变）。
"""

from __future__ import annotations

from src.partitions.ingest.service.ask import cache
from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.providers.cache_memory import MemoryCache
from src.platform.types import Answer, Citation, Document


def _grounded(question: str) -> Answer:
    return Answer(
        question=question,
        text="有据答案",
        citations=[Citation(chunk_id="d#0", doc_id="d", doc_version=1)],
        confidence=0.9,
    )


def test_key_carries_acl_fingerprint_first() -> None:
    """键首段恒为 ACL 域指纹：排序去重稳定，不同域必不同键。"""
    k = cache.cache_key("What is X", ["hr", "fin"])
    assert k.startswith("fin,hr::")
    # 归一化大小写/空白，同义问题同键
    assert cache.cache_key("  what IS x ", ["fin", "hr"]) == k


def test_cross_acl_domain_never_shares_key() -> None:
    assert cache.cache_key("q", ["hr"]) != cache.cache_key("q", ["fin"])


def test_admission_gate_blocks_uncited_refused_and_empty_domain() -> None:
    assert cache.admissible(_grounded("q"), ["hr"]) is True
    assert cache.admissible(Answer(question="q", refused=True), ["hr"]) is False  # 拒答不入
    assert cache.admissible(Answer(question="q", text="无据"), ["hr"]) is False  # 无引用不入（硬条款）
    assert cache.admissible(_grounded("q"), []) is False  # 无 ACL 域指纹不入（防越权复用）


def test_memory_cache_scopes_by_key() -> None:
    c = MemoryCache()
    c.put(cache.cache_key("q", ["hr"]), _grounded("q"))
    assert c.get(cache.cache_key("q", ["hr"])) is not None  # 同域命中
    assert c.get(cache.cache_key("q", ["fin"])) is None  # 跨域绝不命中


def test_agent_serves_from_cache_hit_short_circuits_llm() -> None:
    """漏斗①入口命中：预置同域缓存 → answer 直接返回缓存件（非 dev_echo 的拒答），证明读路径已接。"""
    container = build_container()
    q = "薪酬制度如何规定"
    acl = ["hr"]
    container.cache.put(cache.cache_key(q, acl), _grounded(q))
    got = container.app.answer(q, user_acl=acl)
    assert got.refused is False and got.text == "有据答案"


def test_agent_does_not_leak_across_acl_domains() -> None:
    """预置 hr 域缓存 → fin 用户同问不得命中（必走实链→dev 档拒答），封堵跨域越权复用通道。"""
    container = build_container()
    q = "薪酬制度如何规定"
    container.cache.put(cache.cache_key(q, ["hr"]), _grounded(q))
    got = container.app.answer(q, user_acl=["fin"])
    assert got.text != "有据答案"  # 未取到 hr 缓存


def _ctr(tmp_path) -> object:  # noqa: ANN001 - Container
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))  # type: ignore[arg-type]


_TEXT = "# 差旅报销政策\n\n" + "\n\n".join(f"第{i}条：出差交通与住宿的报销标准与审批说明。" for i in range(40))
_TEXT_V2 = "# 差旅报销政策（修订）\n\n" + "\n\n".join(f"第{i}条：修订后的出差交通与住宿报销上限与审批人。" for i in range(40))


def test_reingest_evicts_the_domains_stale_answer(tmp_path) -> None:
    """M4-cn④ FR-ING-5③ 同源机制补空白：新正文入库即刻按块 ACL 域清缓存。

    不清的后果不是错答而是**过时窗＝缓存存活期**（dev 档无 TTL＝进程寿命）：文档已可检索、同问句仍答旧件。
    """
    c = _ctr(tmp_path)
    q = "出差住宿报销标准"
    c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.app.answer(q, user_acl=["fin"]).citations
    key = cache.cache_key(q, ["fin"])
    assert c.cache.get(key) is not None  # 先证明首轮真入了缓存，否则本锁什么都没锁住

    c.ingest.process_document(
        Document(doc_id="travel", version=2, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT_V2)
    )

    assert c.cache.get(key) is None  # 修订版入库→旧件作废，同问必走实链
    assert c.app.answer(q, user_acl=["fin"]).citations  # 新内容照常答得出（清缓存没把可答性一起清掉）


def test_reingest_does_not_evict_unrelated_acl_domains(tmp_path) -> None:
    """剔除只按新块的 ACL 域走：牵连清无关域＝把缓存整体打空，用成本问题冒充正确性修复。"""
    c = _ctr(tmp_path)
    other = cache.cache_key("年假天数", ["hr"])
    c.cache.put(other, _grounded("年假天数"))
    c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.cache.get(other) is not None  # fin 域入库不动 hr 域缓存

