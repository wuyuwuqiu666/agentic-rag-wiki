"""B 区 wiki 页生命周期状态机（FR-WIKI-1 人审门 / FR-WIKI-4 升降级 / FR-WIKI-5 撤旧）。

纯函数式判定：状态迁移合法性与本页状态机（`WikiPageStatus`）一一对应，人审门=必经 `in_review`。
草稿**不能**直接发布（缺审阅署名即 `ReviewError`），把"人审后才生效"钉成代码不变式而非文档自觉（P2）。
真·interrupt 语义挂 GraphPort（LangGraph 换档）；本模块只出合法迁移，零 IO。
"""

from __future__ import annotations

from src.platform.types import WikiPage, WikiPageStatus

# 合法迁移 (from → to)；不在表内=非法（含 draft 直接 published 的越门路径）
_ALLOWED: set[tuple[WikiPageStatus, WikiPageStatus]] = {
    ("draft", "in_review"),  # 提交人审
    ("in_review", "published"),  # 审阅通过（须署名）
    ("in_review", "draft"),  # 打回重写
    ("published", "stale"),  # 源大改/命中走低→失效待重编译
    ("published", "retracted"),  # 源删除→依据已失效（FR-WIKI-3）
    ("stale", "in_review"),  # 重编译后再审
    ("stale", "draft"),
    ("retracted", "draft"),
}


class ReviewError(Exception):
    """非法状态迁移或未署名发布（人审门违例）。"""


def _to(page: WikiPage, target: WikiPageStatus) -> None:
    if (page.status, target) not in _ALLOWED:
        raise ReviewError(f"非法迁移：{page.status} → {target}")
    page.status = target


def submit_for_review(page: WikiPage) -> None:
    """草稿提交人审：draft → in_review。"""
    _to(page, "in_review")


def check_approve(page: WikiPage, reviewer: str) -> None:
    """发布门**预检**（零副作用）：署名非空 + `in_review → published` 迁移合法，否则 `ReviewError`。

    存在理由=让发布链路"先判门、再写索引、最后改状态"：写侧若失败（B0 拉闸/F2 空 ACL/NFR-8 越界），
    页不得停在 published 态——否则出现"页声称已权威、A 区里却没有"的背离（同 B5 账实背离家族）。
    """
    if not reviewer.strip():
        raise ReviewError("发布须署名审阅人（人审门）")
    if (page.status, "published") not in _ALLOWED:
        raise ReviewError(f"非法迁移：{page.status} → published")


def approve(page: WikiPage, reviewer: str) -> None:
    """审阅通过：in_review → published。**人审门**——未署名审阅人不得发布。"""
    check_approve(page, reviewer)
    page.status = "published"
    page.reviewer = reviewer


def reject(page: WikiPage) -> None:
    """打回：in_review → draft。"""
    _to(page, "draft")


def mark_stale(page: WikiPage) -> None:
    """降级：published → stale（FR-WIKI-4 退草稿链路的失效态）。"""
    _to(page, "stale")


def retract(page: WikiPage) -> None:
    """撤回：published → retracted（源文档删除时标"依据已失效"，FR-WIKI-3）。"""
    _to(page, "retracted")
