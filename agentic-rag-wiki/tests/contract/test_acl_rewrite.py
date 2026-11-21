"""M4-cd②：`acl_change` 效应②＝两路索引与清单里的块 ACL 真的重写（此前只兑现效应③清缓存）。

补上这条才谈得上"收紧权限即刻生效"：只清缓存而块标量仍旧，检索期 pre-filter 仍按**旧域**放行＝
用户看到的可见性没变，回执却写着"已变更"＝声称↔可测点背离。四效应里①PG 权限行属真实双存储=放行项②。

判据一律走**检索结局**而不是翻 provider 内部：dev 两路各自都带 ACL 过滤，任何一路留旧域副本，
旧域读者就还能拿到引用——所以"旧域拒答 / 新域可答"这一对断言同时兜住向量路与词法路。
"""

from __future__ import annotations

from pathlib import Path

from src.platform.config import Settings
from src.platform.gateway import build_container
from src.platform.types import ChangeEvent, Document

_TEXT = "# 差旅报销政策\n\n" + "\n\n".join(f"第{i}条：出差交通与住宿的报销标准与审批说明。" for i in range(40))


def _ctr(tmp_path) -> object:
    return build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))  # type: ignore[arg-type]


def test_acl_change_rewrites_block_acl_so_old_domain_is_denied_and_new_domain_served(tmp_path) -> None:
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    assert c.app.answer("出差住宿报销标准", user_acl=["fin"]).citations
    blocks = len(c.inventory.expected_ids())

    stats = c.ingest.apply_changes(
        [ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_before=["fin"], acl_tags_after=["hr"])]
    )
    assert stats["acl_rewritten"] == blocks  # 在册块全部重写
    assert stats["acl_pending"] == 0
    assert stats["deleted"] == 0  # 重写不是删除（不动墓碑账）
    assert stats["cache_evicted"] >= 1  # ③ 的真实笔数入账：②"先摘旧块"顺带清的缓存也算，报 0＝少报

    assert c.app.answer("出差住宿报销标准", user_acl=["fin"]).refused  # 旧域即刻读不到（两路皆已换域）
    assert c.app.answer("出差住宿报销标准", user_acl=["hr"]).citations  # 新域照常读得到


def test_acl_rewrite_leaves_no_duplicate_or_stale_copy_in_either_index(tmp_path) -> None:
    # 残块/叠块＝越权可读或对账虚报：重写前后块数与 id 集必须逐条相符，一条多出来的都不许有。
    c = _ctr(tmp_path)
    c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin", "hr"], raw_text=_TEXT)
    )
    before = set(c.vectorstore.all_ids())
    c.ingest.apply_changes(
        [ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_before=["fin", "hr"], acl_tags_after=["hr"])]
    )
    assert set(c.vectorstore.all_ids()) == before  # 同批 id 覆写，不叠块
    rec = c.ingest.reconcile()
    assert rec["missing"] == 0 and rec["extra"] == 0  # 账实相符：清单与索引同时换域
    assert c.app.answer("出差住宿报销标准", user_acl=["fin"]).refused


def test_acl_change_without_body_pointer_counts_pending_and_claims_nothing(tmp_path) -> None:
    # 正文指针缺失（真孤儿）＝不臆造内容：如实计入 acl_pending，绝不报成"已重写"。
    c = _ctr(tmp_path)
    event = c.ingest.process_document(
        Document(doc_id="travel", version=1, domain="fin", doc_type="policy", acl_tags=["fin"], raw_text=_TEXT)
    )
    (Path(tmp_path) / "canonical-draft" / event.store_key).unlink()  # 清单在、正文丢

    stats = c.ingest.apply_changes([ChangeEvent(doc_id="travel", kind="acl_change", acl_tags_after=["hr"])])
    assert stats["acl_rewritten"] == 0
    assert stats["acl_pending"] > 0
    assert c.app.answer("出差住宿报销标准", user_acl=["fin"]).citations  # 未声称已重写，故行为也不变


def test_acl_change_on_unknown_doc_changes_nothing_and_claims_nothing(tmp_path) -> None:
    c = _ctr(tmp_path)
    stats = c.ingest.apply_changes([ChangeEvent(doc_id="ghost", kind="acl_change", acl_tags_after=["hr"])])
    assert stats["acl_rewritten"] == 0 and stats["acl_pending"] == 0
    assert c.ingest.reconcile()["actual"] == 0
