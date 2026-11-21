"""护栏产物 #1 `check_imports` 自测（P4：护栏须自带可验证性）。

重点回归两类"查存在不查正确"盲点（M4-v）：
①breaker 零 IO（P1 纯净内核）——os/subprocess/socket/pathlib 无 src 层映射，
  曾因 `tgt is None → continue` 先跳过而变成死代码，本测锁死其复活；
②§6 禁令1 横切 SDK（structlog）纳入黑名单后，service 层直连 import 必须被拦。
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.platform.harness import check_imports
from src.platform.harness.check_imports import _violations


def _viol(rel_str: str, source: str, blacklist: set[str]) -> list[str]:
    return _violations(Path(rel_str), ast.parse(source), blacklist)


def test_blacklist_declares_cross_cutting_and_orchestration_sdk() -> None:
    """§6 禁令1 例名 structlog + 禁令3 的 langgraph 须真实在黑名单（否则"机检见 §9-2"落空）。"""
    bl = {ln.strip() for ln in (check_imports._ROOT / "src/platform/harness/sdk_blacklist.txt").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    assert {"structlog", "langgraph", "langchain", "openai"} <= bl


def test_breaker_importing_pathlib_is_flagged() -> None:
    """P1 纯净内核：breaker import pathlib（文件 IO）须报"纯净性破坏"——曾经因早退 continue 成死代码。"""
    v = _viol("src/platform/breaker/engine.py", "import pathlib", set())
    assert any("纯净性破坏" in p for p in v)


def test_breaker_importing_io_stdlib_is_flagged() -> None:
    for mod in ("os", "subprocess", "socket"):
        v = _viol("src/platform/breaker/rules.py", f"import {mod}", set())
        assert any("纯净性破坏" in p for p in v), mod
    v = _viol("src/platform/breaker/engine.py", "from pathlib import Path", set())
    assert any("纯净性破坏" in p for p in v)


def test_breaker_pure_imports_not_flagged() -> None:
    """breaker 只引 types/breaker 契约与标准库非 IO 模块不得误报。"""
    v = _viol("src/platform/breaker/engine.py", "import json\nfrom src.platform.types import BreakerDecision", set())
    assert v == []


def test_non_breaker_layer_pathlib_not_purity_violation() -> None:
    """pathlib 对 harness/repo 属正常 IO，不得被当纯净内核违规（检查范围未越界）。"""
    v = _viol("src/platform/harness/loader.py", "import pathlib", set())
    assert not any("纯净性破坏" in p for p in v)


def test_service_importing_structlog_is_sdk_leak() -> None:
    """§6 禁令1：service 层直连横切 SDK structlog 须报 SDK 泄漏。"""
    v = _viol("src/partitions/ingest/service/ask/fusion.py", "import structlog", {"structlog"})
    assert any("SDK 泄漏" in p and "structlog" in p for p in v)


def test_providers_and_gateway_exempt_from_sdk_leak() -> None:
    """唯一豁免=providers/* 实现 + gateway 组合根：即便 import 黑名单 SDK 也不报泄漏。"""
    assert not any("SDK 泄漏" in p for p in _viol("src/platform/providers/llm_cloud.py", "import openai", {"openai"}))
    assert not any("SDK 泄漏" in p for p in _viol("src/platform/gateway/composition.py", "import structlog", {"structlog"}))


def test_orchestration_sdk_only_allowed_in_its_single_file() -> None:
    """§6 禁令3 真机检（M4-bk）：langgraph/langchain 即便在 providers/ 目录豁免内也仍收紧到唯一落点，
    别的 provider/组合根文件私自 import 编排框架必须被拦——否则'只允许出现在 orchestration_langgraph.py'只是文档空话。"""
    for mod in ("langgraph", "langchain"):
        # 非唯一落点：同目录另一 provider 文件私自引编排框架 → 拦。
        v = _viol("src/platform/providers/llm_router.py", f"import {mod}", {mod})
        assert any("§6 禁令3 越界" in p and mod in p for p in v), mod
        # 组合根即便享 SDK 泄漏豁免，仍不得吞编排框架 → 拦。
        assert any("§6 禁令3 越界" in p for p in _viol("src/platform/gateway/composition.py", f"import {mod}", {mod})), mod
    # 唯一落点自身：不报禁令3 越界（且函数内 import 经 ast.walk 亦识别）。
    v = _viol(
        "src/platform/providers/orchestration_langgraph.py",
        "def _load():\n    import langgraph\n",
        {"langgraph"},
    )
    assert not any("§6 禁令3 越界" in p for p in v)


def test_cross_zone_partition_import_is_flagged() -> None:
    """§6.1 三禁①：跨区直接 import 须报"跨区直连"。"""
    v = _viol("src/partitions/ingest/service/x.py", "import src.partitions.wiki.service.y", set())
    assert any("跨区直连" in p for p in v)


def test_bzone_importing_azione_writeback_channel_is_flagged() -> None:
    """§6.1 三禁②（check_imports docstring 执法点②，F2 跨源泄露安全关键向）：
    B 区（wiki）不得 import A 区（ingest）——回写只经 platform 端口 IngestionPort。
    此前仅测 A→B 方向，本测锁死反向（安全敏感的那一路），防"跨区直连"退化成只拦单向。"""
    for sub in ("service", "runtime", "repo"):
        v = _viol(f"src/partitions/wiki/{sub}/x.py", f"import src.partitions.ingest.{sub}.y", set())
        assert any("跨区直连" in p for p in v), sub
    # 正向对照：区内同子层链合法引用不误报（确保收紧未过界）。
    assert not any("跨区直连" in p for p in _viol("src/partitions/wiki/service/x.py", "import src.partitions.wiki.repo.y", set()))


def test_types_never_leaves_its_zone_is_flagged() -> None:
    """§6.1 三禁①"含经私有 _types"变体（docstring 执法点③，_types 不出区）：
    跨区引他区 `_types` 是最隐蔽的传递通道（绕过 service 名直接搬对象），须独立锁定；
    区内 `_types` 作为最内子层可被引用，不得误报。"""
    v = _viol("src/partitions/ingest/service/x.py", "import src.partitions.wiki._types", set())
    assert any("跨区直连" in p for p in v)
    # 区内 _types 是链首（_PREDECESSOR 各子层皆含 _types），正常引用不误报。
    for sub in ("repo", "service", "runtime"):
        assert not any("跨区直连" in p for p in _viol(f"src/partitions/wiki/{sub}/x.py", "import src.partitions.wiki._types", set())), sub


def test_real_repo_import_direction_green() -> None:
    """真实仓库集成守卫：依赖方向 + 跨区三禁 + breaker 零 IO 全绿（缺口即 CI 红）。"""
    assert check_imports.check() == []
