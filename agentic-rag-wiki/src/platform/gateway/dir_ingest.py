"""目录批量入库的单点入口（M4-ce 1b，FR-ING-1 存量遍历的产品可达面）。

HTTP 门面与 CLI **都只 1:1 委派本模块**：路径围栏只在此执法一处（两处各写一份＝口径分叉，
越界目录一边拦一边不拦＝幻影安全）。本层零业务逻辑——抽正文＝装配注入的 `FileParserPort`，
入库＝`IngestPipeline.pull`（B0 写侧总闸、B1 删除占比闸、DLP/准入、幂等重放全在管线内，不旁路）。

四道门（缺任何一道都不许读盘）：
1. **未配置允许根目录（`APP_INGEST_SOURCE_DIR`）即拒**——`DirIngestNotConfigured`→HTTP 501，绝不"谁指哪就读哪"；
2. **越出根目录即拒**——请求目录先 `resolve()`（吃掉 `..` 与符号链接）再 `is_relative_to(root)`，`DirOutsideRoot`→403；
3. **不是目录即拒**——`NotAnIngestDirectory`→422（给方向而非静默返空批）；
4. **目标覆盖系统产物目录即拒**——`IngestRootOverlapsArtifact`→422：把 canonical 草稿/决议记录/对账报告当源文档
   吸回收＝自灌回路，且"删草稿"会被误判成"删文档"（T3 覆盖类高危）。

指纹位点＝`Container.dir_ingest_state`（进程内 dict，跨进程/重启位点＝真实 PG＋真实连接器＝放行项②／㉒）：
"文件消失"→`delete` 事件（走 M4-cc/1a 的三路同清+墓碑）；"文件内容变了"→列进回执 `changed` 并指路
先下架再入库，**不自造同 doc_id 版本递增**（那属 canonical 版本语义＝M1 冻结评审面）。
在册判据＝`IngestPipeline.is_indexed`（位点只加速、索引账才裁决）：已人工下架而源里仍在的文件按"源为真相"
重新入库，记进 `reingested`（与 FR-ING-4 补偿同口径）。

子目录目标的两条护栏（回执可核）：doc_id 相对**允许根目录**算（同一文件换目标不会二次入库）；
**消失判定只在扫整根时执行**（`delete_scan`="root"），扫子目录时看不见兄弟目录，照样比对位点账＝把兄弟误判成删除，
故该档返回空批并如实标 `delete_scan`="subdir-off"。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.partitions.ingest.repo.source import FileSystemSource
from src.platform.types.document import DocType

if TYPE_CHECKING:  # 仅类型标注，运行期不 import（守 gateway→composition 单向）
    from src.platform.gateway.composition import Container


class DirIngestError(ValueError):
    """目录批量入库的门拒——一律 fail-loud，绝不当"这批没文件"静默通过。"""


class DirIngestNotConfigured(DirIngestError):
    """未配置 `APP_INGEST_SOURCE_DIR`：本入口整档不可用（执行档默认态，非缺陷）。"""


class DirOutsideRoot(DirIngestError):
    """请求目录越出允许根目录（含 `..` 与符号链接逃逸）：路径围栏拦截。"""


class NotAnIngestDirectory(DirIngestError):
    """允许根目录内的目标不存在或不是目录。"""


class IngestRootOverlapsArtifact(DirIngestError):
    """目标目录包含系统自己的产物目录（`artifact_dir`）：把 canonical 草稿/对账报告再吸回收=自灌回路。"""


def allowed_root(container: Container) -> Path:
    configured = container.settings.ingest_source_dir
    if not configured:
        raise DirIngestNotConfigured(
            "未配置目录入库的允许根目录（APP_INGEST_SOURCE_DIR）——本入口 fail-loud，不默认放开任意读盘；"
            "配好后请求目录仍须落在该根内"
        )
    return Path(configured).resolve()


def resolve_target(container: Container, directory: str) -> tuple[Path, Path]:
    """(根, 目标)：目标先 resolve 再验围栏；`directory=""`＝根目录本身。"""
    root = allowed_root(container)
    rel = (directory or "").strip()
    target = (root / rel).resolve() if rel else root
    if target != root and not target.is_relative_to(root):
        raise DirOutsideRoot(f"请求目录 {rel!r} 越出允许根目录 {root}（resolve 后仍不在其内＝路径穿越）")
    if not target.is_dir():
        raise NotAnIngestDirectory(f"{target} 不存在或不是目录；允许根目录={root}")
    # 自灌围栏：产物目录（canonical 草稿/决议记录/对账报告/画像册）落在扫描范围内时整批拒绝——
    # 把系统自己写出的中间件当源文档吸回来，既污染检索语料又会让"删草稿"变成"删文档"（T3 覆盖类高危）。
    artifact = (Path.cwd() / container.settings.artifact_dir).resolve()
    if artifact == target or artifact.is_relative_to(target):
        raise IngestRootOverlapsArtifact(
            f"目标目录 {target} 包含系统产物目录 {artifact}（APP_ARTIFACT_DIR）——不给自灌；"
            "请把摄取根目录与产物目录分开放"
        )
    return root, target


def run_dir_ingest(
    container: Container,
    *,
    directory: str = "",
    domain: str = "default",
    doc_type: DocType = "other",
    acl_tags: list[str] | None = None,
) -> dict[str, Any]:
    """走一遍目录→抽正文→逐份进摄取链；返回逐项可核的批次账（无一静默丢弃）。"""
    root, target = resolve_target(container, directory)
    source = FileSystemSource(
        target,
        parse=container.fileparser.parse,
        domain=domain,
        doc_type=doc_type,
        acl_tags=list(acl_tags or []),
        fingerprints=container.dir_ingest_state,
        is_indexed=container.ingest.is_indexed,
        identity_root=root,
    )
    stats = container.ingest.pull(source)
    return {
        "root": str(root),
        "directory": str(target),
        "relative": target.relative_to(root).as_posix() or ".",
        "delete_scan": "root" if source.full_scan else "subdir-off",
        "domain": domain,
        "doc_type": doc_type,
        "acl_tags": list(acl_tags or []),
        "skipped": source.skipped,
        "changed": source.changed,
        "conflicts": source.conflicts,
        "unchanged": source.unchanged,
        "reingested": source.reingested,
        "not_ingested": len(source.skipped) + len(source.changed) + len(source.conflicts) + len(source.unchanged),
        "hint": (
            "changed 列出的文件本批未入库：执行档没有“文件内容变了”的安全表达（upsert 会重放旧正文）。"
            "路径＝先经 POST /api/ingest/change{kind:delete} 下架该 doc_id，再重跑本入口"
            "（下架后索引在册判据转假，文件会经 reingested 重新入库）"
        ),
        **stats,
    }
