"""FR-ING-1/2 源连接器契约（§5 repo/source「源连接器（SourcePort）」、U5「实现 repo 层源接口」的落点）。

连接器开发者只实现 `SourcePort`（复用 types 已有 Document/ChangeEvent 契约），
**不触碰 service/runtime**（本文件仅 import types+stdlib，check_imports 执法）；
产出的存量文档/增量变更由 `IngestPipeline.pull` 消费。真实源系统读取端
（OA/网盘/Git/SharePoint，C4：MCP 连接器为 repo/source 的一种实现）=放行项㉒；
本模块只锁定可离线机检的契约缝 + 内存档 `DevSource`（smoke/contract 演练）+ 本地目录档
`FileSystemSource`（M4-ce 1b 批量入库，读真实文件但仍是本地目录、绝不冒充真实源系统读取端）。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator, MutableMapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.platform.types import ChangeEvent, Document
from src.platform.types.document import DocType, PageState


@runtime_checkable
class ParsedBody(Protocol):
    """本层对注入的解析供给**只提所需形状**（承 U5/`SourcePort` 的反向口径：repo 声明、provider 满足，
    repo 不 import providers——§6 跨区禁令由 `check_imports` 执法）。

    要 `page_state` 而不是只 `str`（ADR-009）：三态判据只有摸过容器字节的人能给，事后猜＝同一份文档
    从不同入口进来得到不同页态（本项目反复拦的口径分叉族）。
    """

    @property
    def text(self) -> str: ...

    @property
    def page_state(self) -> PageState: ...


@runtime_checkable
class SourcePort(Protocol):
    """源系统读取端契约：列举存量 + 拉取增量变更（B1 判定挂在此消费入口，FR-ING-2）。"""

    def list_documents(self) -> Iterator[Document]:
        """全量存量遍历（分片/断点续传的真实调度=PG 队列实机=放行项②）。"""
        ...

    def poll_changes(self) -> list[ChangeEvent]:
        """自上次位点以来的增量变更（upsert/delete/acl_change/domain_move）。"""
        ...


class DevSource:
    """内存档源连接器：把预置的 Document/ChangeEvent 当作源系统输出，仅供离线演练。"""

    def __init__(
        self,
        documents: list[Document] | None = None,
        changes: list[ChangeEvent] | None = None,
    ) -> None:
        self._documents = list(documents or [])
        self._changes = list(changes or [])

    def list_documents(self) -> Iterator[Document]:
        return iter(self._documents)

    def poll_changes(self) -> list[ChangeEvent]:
        return list(self._changes)


_DOC_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def doc_id_for(relative_path: str) -> str:
    """文件名→稳定 doc_id（递归路径去后缀后把非法字符收敛成 `-`，守住 `{doc}/{ver}#{seq}` 可解析性）。

    同 M4-by 的入参口径：doc_id 只能落在 `^[A-Za-z0-9._-]+$`，否则检索期引用 id 解析会错位。
    """
    stem = relative_path.rsplit(".", 1)[0] if "." in Path(relative_path).name else relative_path
    cleaned = _DOC_ID_SAFE.sub("-", stem.replace("\\", "/")).strip("-")
    return cleaned or "untitled"


class FileSystemSource:
    """本地目录档源连接器（M4-ce 1b，FR-ING-1 存量遍历的离线可跑实现）。

    本类只"走目录 + 把字节交给注入的 `parse` 抽正文"，**不 import providers/service**（repo 层纯净由
    `check_imports` 执法）；解析供给由调用侧注入 `parse(filename, data)->ParsedBody`（执行档=`DevFileParser`，
    G5 换真实版面/OCR 解析器不动本类）。路径围栏不在本层执法（越界目录属调用方输入门，见 `gateway/dir_ingest.py`）。

    位点＝**调用方持有的指纹账**（`fingerprints: doc_id -> 正文字节 sha256`；进程内 dict＝dev 档，
    跨进程位点＝真实 PG 放行项②／真实连接器放行项㉒）+ **索引在册判据**（`is_indexed`，执行档注入 `IngestPipeline.is_indexed`）：
    - `list_documents()` 走一遍目录，逐个抽出正文并登记指纹；解析失败/超限/撞 id/已在册的文件**不静默丢弃**，
      一律进 `skipped`/`conflicts`/`unchanged`/`changed` 供回执逐条陈列（`unchanged`＝指纹相同的幂等跳过）。
      位点记得但索引上已无此文档（人工下架或账实背离）＝按源为真相**重新入库**并记进 `reingested`。
    - `poll_changes()` 再走一遍并比对指纹：**消失的文件→`delete` 事件**（由 `apply_changes` 走
      `_purge`+墓碑+B 区失效，语义已由 M4-cc/M4-ce 1a 收口）；**内容变过的文件不发 `upsert`**——
      执行档的 upsert＝"按 objectstore 旧正文重拉重放"，用它表达"文件改了"会把**旧内容再入库一次**
      （静默错值，比不处理更坏）。变更文件列进 `changed` 由回执指路：先经 `POST /api/ingest/change`
      下架、再入库；"同 doc_id 版本递增时旧版本块的摘除"属 canonical 版本语义＝M1 冻结评审面，本刀不自造。

    两条与"子目录目标"相关的口径（都是防静默破坏，勿改回）：
    - **doc_id 相对 `identity_root`（允许根目录）而非扫描目录**：同一文件无论从哪个子目录发起扫描都得到同一身份，
      否则"先扫根、再扫子目录"会把同一文件当两份新文档重复入库。
    - **消失判定只在整根扫描（`full_scan`）时执行**：扫子目录时看不见兄弟目录的文件，若照样比对位点账就会把
      兄弟目录误判成已删除＝静默批量下架（T3 高危），故子目录扫描的 `poll_changes()` 返回空批。
    """

    def __init__(
        self,
        root: str | Path,
        *,
        parse: Callable[[str, bytes], ParsedBody],
        domain: str = "default",
        doc_type: DocType = "other",
        acl_tags: Sequence[str] = (),
        source_label: str = "filesystem",
        max_bytes: int = 2_000_000,
        fingerprints: MutableMapping[str, str] | None = None,
        is_indexed: Callable[[str], bool] | None = None,
        identity_root: str | Path | None = None,
    ) -> None:
        self._root = Path(root)
        # 身份根＝允许根目录：doc_id 由"相对身份根的路径"决定，同一文件无论从哪个子目录发起扫描都得到同一 doc_id
        # （否则"先扫根、再扫子目录"会把同一文件当成两份新文档重复入库）。缺省＝扫描根自身。
        self._identity = Path(identity_root) if identity_root is not None else self._root
        self.full_scan = self._identity == self._root
        self._parse = parse
        self._domain = domain
        self._doc_type = doc_type
        self._acl_tags = list(acl_tags)
        self._source_label = source_label
        self._max_bytes = max_bytes
        self._seen: MutableMapping[str, str] = fingerprints if fingerprints is not None else {}
        # 索引在册判据（执行档注入 `IngestPipeline.is_indexed`）：位点账只加速，在册与否才是"要不要进"的裁决。
        # 缺省＝永视为在册（纯位点档：无管线时可单测本类的幂等/变更陈列）。
        self._is_indexed: Callable[[str], bool] = is_indexed or (lambda _doc_id: True)
        self.skipped: list[dict[str, str]] = []
        self.conflicts: list[dict[str, str]] = []
        self.changed: list[dict[str, str]] = []
        self.unchanged: list[dict[str, str]] = []
        self.reingested: list[dict[str, str]] = []

    def _walk(self) -> list[Path]:
        """确定性遍历：按相对路径排序，跳过**相对根目录**的隐藏部件（绝对路径前缀不参与判定，
        否则根目录自身落在 `.venv`/`.qoder` 一类目录下时会整批看不见）；缺目录即 fail-loud。"""
        if not self._root.is_dir():
            raise NotADirectoryError(f"摄取目录不存在或不是目录：{self._root}")
        files: list[Path] = []
        for p in self._root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self._root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            files.append(p)
        return sorted(files, key=lambda p: p.relative_to(self._root).as_posix())

    def _fingerprint(self, path: Path) -> bytes:
        data = path.read_bytes()
        if len(data) > self._max_bytes:
            raise ValueError(f"单文件 {len(data)} 字节超上限 {self._max_bytes}（目录批量档，勿整库吞）")
        return data

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _id_for(self, path: Path) -> str:
        """doc_id＝相对**身份根**的路径归一（子目录扫描与整根扫描对同一文件给出同一身份）。"""
        return doc_id_for(path.relative_to(self._identity).as_posix())

    def list_documents(self) -> Iterator[Document]:
        produced: set[str] = set()
        for path in self._walk():
            rel = path.relative_to(self._root).as_posix()
            doc_id = self._id_for(path)
            try:
                data = self._fingerprint(path)
            except ValueError as exc:
                self.skipped.append({"file": rel, "reason": str(exc)})
                continue
            digest = self._digest(data)
            if doc_id in produced:  # 同批内两个不同文件归一出同一 doc_id：后到的不覆盖先入库的那份
                self.conflicts.append({"file": rel, "doc_id": doc_id, "reason": "本批内文件名归一后与另一文件相撞"})
                continue
            known = self._seen.get(doc_id)
            if known is not None and self._is_indexed(doc_id):
                # 索引上仍有此文档：指纹相同＝幂等跳过（重跑不重复入库），不同＝内容变过、只报不吃
                bucket = self.unchanged if known == digest else self.changed
                bucket.append({"file": rel, "doc_id": doc_id})
                continue
            if known is not None:
                # 位点记得、索引上却已无此文档（人工下架/账实背离）：源为真相→重新入库（同 FR-ING-4 补偿口径）
                self.reingested.append({"file": rel, "doc_id": doc_id})
            try:
                parsed = self._parse(path.name, data)
            except Exception as exc:  # noqa: BLE001 — 连接器边界：单文件解析失败不得中断整批，逐条陈列原因
                self.skipped.append({"file": rel, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            if not parsed.text.strip():
                self.skipped.append({"file": rel, "reason": "抽出正文为空（不静默入空文档）"})
                continue
            self._seen[doc_id] = digest
            produced.add(doc_id)
            yield Document(
                doc_id=doc_id,
                version=1,
                source=self._source_label,
                title=path.stem,
                domain=self._domain,
                doc_type=self._doc_type,
                acl_tags=list(self._acl_tags),
                raw_text=parsed.text,
                page_state=parsed.page_state,  # 三态随正文一起落账（FR-ING-8）：目录批量与单文件上传同一口径
            )

    def poll_changes(self) -> list[ChangeEvent]:
        """只发"消失"这一种变更，且**只在整根扫描时判定**：子目录扫描看不见兄弟目录的文件，
        若照样比对位点账就会把兄弟目录误判成已删除（静默批量下架＝T3 高危），故直接返回空批。

        本档源不发 `upsert`（理由见类 docstring），也不发 `acl_change`（目录不承载 ACL 真相）。
        """
        if not self.full_scan:
            return []
        present: set[str] = {self._id_for(path) for path in self._walk()}
        gone = [doc_id for doc_id in list(self._seen) if doc_id not in present]
        for doc_id in gone:
            del self._seen[doc_id]  # 位点随源走：文件真没了就不再认得它（墓碑账在管线侧，不在此）
        return [
            ChangeEvent(
                doc_id=doc_id,
                kind="delete",
                version=1,
                domain_before=self._domain,
                acl_tags_before=list(self._acl_tags),
            )
            for doc_id in sorted(gone)
        ]
