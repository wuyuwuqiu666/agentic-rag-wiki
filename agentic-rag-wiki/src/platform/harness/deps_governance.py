"""E8 依赖治理离线核心：许可证允许清单校验 + SBOM 生成/diff（单脚本源，承 secret_scan 同构）。

判定口径=纯函数（不依赖装了什么包、可单测）：`classify(license)→allowed|denied|unknown`、`diff(基线, 当前)`。
运行期从 `pyproject` 声明依赖经 `importlib.metadata` 读已装发行版许可证（缺装的可换 extras 跳过、不误崩），
生成 `workspace/sbom/sbom.json`；被禁 copyleft=CI 红（PRD"许可证…阻断"），许可证缺失=unknown（默认告警、
`--strict` 翻红，防离线机误崩）。**CVE 实时漏洞扫描需网络/漏洞库、SBOM 权威 SPDX 精确对账与企业策略清单
=外部供给/运行时放行档**，本模块只做本机可决的许可证门与 SBOM 快照/diff，不伪造 CVE 结论。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from importlib import metadata as imeta
from pathlib import Path
from typing import Literal

# 保守子串匹配：先判被禁（copyleft），再判允许（常见宽松许可证标识）；粗口径够 dev 门，精确 SPDX 对账=放行档。
_DENY_TOKENS = ("agpl", "gpl", "lgpl", "gnu", "copyleft", "gfdl", "cc-by-nc", "non-commercial")
_ALLOW_TOKENS = ("mit", "bsd", "apache", "isc", "python", "psf", "unlicense", "0bsd", "zlib", "x11", "expat", "openssl")

Status = Literal["allowed", "denied", "unknown"]


def classify(license: str | None) -> Status:
    if not license or not license.strip():
        return "unknown"
    s = license.strip().lower()
    if any(tok in s for tok in _DENY_TOKENS):
        return "denied"
    if any(tok in s for tok in _ALLOW_TOKENS) or "osi approved" in s:
        return "allowed"
    return "unknown"


def diff(base: list[dict[str, str]], cur: list[dict[str, str]]) -> dict[str, list[str]]:
    """SBOM 快照对比：按包名比新增/移除，同名比许可证变更（值=受影响包名列表）。"""
    b = {e["name"]: e.get("license", "") for e in base}
    c = {e["name"]: e.get("license", "") for e in cur}
    return {
        "added": sorted(set(c) - set(b)),
        "removed": sorted(set(b) - set(c)),
        "changed": sorted(n for n in set(b) & set(c) if b[n] != c[n]),
    }


def _normalize_name(requirement: str) -> str:
    """从 PEP 508 需求串取包名（丢弃 extras/版本说明符）。"""
    return re.split(r"[\[<>=!~; ]", requirement.strip(), maxsplit=1)[0].strip()


def _license_of(dist: imeta.Distribution) -> str | None:
    md = dist.metadata
    for key in ("License-Expression", "License"):
        val = md.get(key)
        if val and val.strip().lower() not in ("unknown", "none", ""):
            return val.strip()
    for c in md.get_all("Classifier") or []:  # 兜底：老包只在 trove classifier 标许可证
        if c.startswith("License ::"):
            return c.split("::")[-1].strip()
    return None


def declared_deps(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data["project"].get("dependencies", []))


def build_sbom(requirements: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    """已声明依赖 → SBOM 条目；未安装的（可换 extras/离线）跳过并回传跳过名单，不误崩。"""
    entries: list[dict[str, str]] = []
    skipped: list[str] = []
    for req in requirements:
        name = _normalize_name(req)
        try:
            dist = imeta.distribution(name)
        except imeta.PackageNotFoundError:
            skipped.append(name)
            continue
        lic = _license_of(dist)
        entries.append({"name": name, "version": dist.version, "license": lic or "", "status": classify(lic)})
    return entries, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E8 依赖许可证门 + SBOM（离线；CVE=放行档）")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--out", default="workspace/sbom/sbom.json")
    parser.add_argument("--baseline", default=None, help="对比既有 SBOM 快照打印增删改")
    parser.add_argument("--strict", action="store_true", help="unknown 许可证也翻红（默认仅告警）")
    parser.add_argument("--update-baseline", action="store_true", help="把当前 SBOM 写为基线（演进留痕）")
    args = parser.parse_args(argv)

    reqs = declared_deps(Path(args.pyproject))
    entries, skipped = build_sbom(reqs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"components": entries, "skipped_uninstalled": skipped}, ensure_ascii=False, indent=2), encoding="utf-8")

    denied = [e["name"] for e in entries if e["status"] == "denied"]
    unknown = [e["name"] for e in entries if e["status"] == "unknown"]
    if args.baseline and Path(args.baseline).exists():
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8")).get("components", [])
        print("SBOM diff:", json.dumps(diff(base, entries), ensure_ascii=False))
    elif args.baseline:
        print(f"基线不存在（{args.baseline}）→ 跳过 diff；首次用 --update-baseline 生成")
    if skipped:
        print(f"跳过（本机未装的可换 extras，非违规）：{skipped}")
    if unknown:
        print(f"许可证未识别（advisory）：{unknown}")
    if denied:
        print(f"许可证被禁（copyleft/非商用）→ 阻断：{denied}", file=sys.stderr)
        return 1
    if args.strict and unknown:
        print("--strict：许可证缺失也视为不合规 → 阻断", file=sys.stderr)
        return 1
    if args.update_baseline:
        baseline_path = Path(args.baseline or args.out)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({"components": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"基线已写入：{baseline_path}")
    print(f"许可证门通过：{len(entries)} 组件均 allowed（unknown={len(unknown)}，denied=0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
