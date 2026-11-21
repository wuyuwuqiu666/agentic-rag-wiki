"""B 区页面仓库：page_id ↔ 源 doc 的倒排，供增量重编译与源失效定位（FR-WIKI-2/3）。

区内私有存储（内存 stand-in，执行档 M1+ 换真实库同接口）。跨区只经契约事件，不 import A/C 区。
"""

from __future__ import annotations

from src.platform.types import WikiPage


class PageRegistry:
    def __init__(self) -> None:
        self._pages: dict[str, WikiPage] = {}
        self._by_doc: dict[str, set[str]] = {}

    def put(self, page: WikiPage) -> None:
        """登记/顶替一页并刷新倒排（同 page_id 再编译=覆盖旧页）。"""
        old = self._pages.get(page.page_id)
        if old is not None:
            for doc in old.source_doc_ids:
                self._by_doc.get(doc, set()).discard(page.page_id)
        self._pages[page.page_id] = page
        for doc in page.source_doc_ids:
            self._by_doc.setdefault(doc, set()).add(page.page_id)

    def get(self, page_id: str) -> WikiPage | None:
        return self._pages.get(page_id)

    def all(self) -> list[WikiPage]:
        return list(self._pages.values())

    def pages_for_doc(self, doc_id: str) -> list[WikiPage]:
        """引用某源文档的全部页（脏页重编译 / 源失效定位的作用域，FR-WIKI-2「只影响所属域、不整库重跑」）。"""
        return [self._pages[pid] for pid in self._by_doc.get(doc_id, set()) if pid in self._pages]
