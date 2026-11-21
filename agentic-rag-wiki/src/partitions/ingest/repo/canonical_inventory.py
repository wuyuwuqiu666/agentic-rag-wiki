"""A 区 repo：canonical 清单账（PG 元数据的内存 stand-in，M1 接真实 PG 替换本实现）。

记录"应当已索引"的 chunk_id 全集，供 B5 对账与索引实际态做差；不改检索，只记账。

两本账分册（M4-cd）：源块账按 `(doc_id, version)` 带事件信封，缺块可按指针重拉 objectstore 正文重放；
**回写块账**只记 `chunk_id→页归属`，因为权威块的正文只存在于 B 区的页，A 区无从按 canonical 重造——
同一份"应索引"清单里混两册（缺块后的处置完全不同）＝把补偿语义搅成"猜页内容"，故分册而不合并。
"""

from __future__ import annotations

from collections import defaultdict

from src.platform.types import CanonicalEvent


class CanonicalInventory:
    def __init__(self) -> None:
        self._chunks_by_doc: dict[tuple[str, int], list[str]] = defaultdict(list)
        # F1：清单=PG 事实源 stand-in，除块 id 外存 canonical 事件信封（domain/acl/doc_type/store_key 指针），
        # 使"一键补偿"能按清单重拉 objectstore 正文重建 Document（不依赖事件流不丢，FR-ING-6 追平语义）。
        self._event_by_doc: dict[tuple[str, int], CanonicalEvent] = {}
        # 回写块账（M4-cd）：chunk_id→页归属。已发布权威页的块"应当已索引"，缺此册则 B5 对账把每一块
        # 发布过的权威块都报成孤儿（索引有、清单无）＝系统性虚报，真背离反被噪声淹没。
        self._writeback: dict[str, str] = {}

    def record(self, event: CanonicalEvent, chunk_ids: list[str]) -> None:
        self._chunks_by_doc[(event.doc_id, event.version)] = list(chunk_ids)
        self._event_by_doc[(event.doc_id, event.version)] = event

    def forget_doc(self, doc_id: str) -> None:
        for key in [k for k in self._chunks_by_doc if k[0] == doc_id]:
            del self._chunks_by_doc[key]
            self._event_by_doc.pop(key, None)

    def forget_version(self, doc_id: str, version: int) -> None:
        key = (doc_id, version)
        self._chunks_by_doc.pop(key, None)
        self._event_by_doc.pop(key, None)

    def chunk_ids_for(self, doc_id: str, version: int | None = None) -> list[str]:
        """解析该 doc（或指定 version）当前记的 chunk_id 全集，供精确删除（FR-ING-2 delete 收口，替代 doc 前缀占位）。"""
        out: list[str] = []
        for (did, ver), ids in self._chunks_by_doc.items():
            if did == doc_id and (version is None or ver == version):
                out.extend(ids)
        return out

    def expected_ids(self) -> set[str]:
        """B5 对账的"应索引"全集＝源块账 ∪ 回写块账（两册分记、一处做差）。"""
        return {cid for ids in self._chunks_by_doc.values() for cid in ids} | set(self._writeback)

    def record_writeback(self, owner_page_id: str, chunk_ids: list[str]) -> None:
        """登记 B 区回写块（发布即记账）：这些块从此是"应当已索引"的一部分，不再算孤儿。"""
        for cid in chunk_ids:
            if cid:
                self._writeback[cid] = owner_page_id

    def forget_writeback(self, chunk_ids: list[str]) -> None:
        """撤账（页离开 published／块已撤回）：不撤则已摘净的块会被对账报成"应索引却缺"的幻影背离。"""
        for cid in chunk_ids:
            self._writeback.pop(cid, None)

    def writeback_ids(self) -> set[str]:
        return set(self._writeback)

    def writeback_pages(self) -> dict[str, list[str]]:
        """回写块按页归组（对账报告与运维读回用：缺的是哪张权威页的块，处置是重编译而非重拉正文）。"""
        out: dict[str, list[str]] = {}
        for cid, page_id in self._writeback.items():
            out.setdefault(page_id, []).append(cid)
        for ids in out.values():
            ids.sort()
        return out

    def affected(self, doc_id: str, version: int | None = None) -> list[tuple[CanonicalEvent, list[str]]]:
        """删除前抓取受影响 (信封, 真实 chunk_id) 对，供墓碑记账（§10 软删除可恢复的回滚点）。

        version=None=整 doc 全版本、有=指定版本；仅返回登记过事件信封者（信封带 store_key/domain/acl=重放所需，无信封=无从重拉）。
        """
        out: list[tuple[CanonicalEvent, list[str]]] = []
        for (did, ver), ids in self._chunks_by_doc.items():
            if did == doc_id and (version is None or ver == version):
                event = self._event_by_doc.get((did, ver))
                if event is not None:
                    out.append((event, list(ids)))
        return out

    def docs(self) -> list[tuple[str, int]]:
        return sorted(self._chunks_by_doc)

    def docs_with_missing(self, missing_ids: set[str]) -> list[CanonicalEvent]:
        """反查含缺失块的 doc 信封（供补偿重拉）；同 doc 只要有一块缺即整 doc 重放（upsert 幂等修复全部块）。"""
        out: list[CanonicalEvent] = []
        for key, cids in self._chunks_by_doc.items():
            if set(cids) & missing_ids:
                event = self._event_by_doc.get(key)
                if event is not None:
                    out.append(event)
        return out
