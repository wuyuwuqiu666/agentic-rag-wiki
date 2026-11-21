"""FR-CAP-1① 语义缓存准入判定（纯规则，无 IO；§12 生成后门 / v0.6 安全硬条款）。

三条不变式（缺一即越权/污染缓存通道）：
1. 缓存键必含 ACL 域指纹——答案只在同权限分区内复用，跨域分发=越权；
2. 无 chunk 引用（未接地）的答案不得入缓存；
3. 无 ACL 域（空指纹）或已拒答的条目不得入缓存。
真·相似匹配（embedding 距离召回历史问题）属换档实现；本层键结构不变，dev 精确键=同键同域命中。
"""

from __future__ import annotations

from src.platform.types import Answer


def acl_fingerprint(acl_tags: list[str]) -> str:
    """ACL 域指纹：去重排序的稳定串；空域=空指纹（不得作为缓存分区）。"""
    return ",".join(sorted(set(t for t in acl_tags if t)))


def cache_key(question: str, acl_tags: list[str]) -> str:
    """键=ACL 域指纹 + 归一化问题；指纹恒在键首段，保证分区隔离不可跨域命中。"""
    fp = acl_fingerprint(acl_tags)
    q = " ".join(question.strip().lower().split())
    return f"{fp}::{q}"


def admissible(answer: Answer, acl_tags: list[str]) -> bool:
    """入缓存门：拒答 / 无引用 / 无 ACL 域 一律不入（§9 #7 尺子口径之外的出口安全闸）。"""
    return not answer.refused and bool(answer.citations) and bool(acl_fingerprint(acl_tags))
