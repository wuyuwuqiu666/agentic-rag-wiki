"""E8/T1 凭证扫描单一脚本源（护栏产物 #5/#6 与 CI 共用，杜绝"本地绿 CI 红"）。

`detect-secrets` 是可选工具（本机/未装档可跳过、CI 强校验）；本模块只负责其后的**判定口径**——
无基线全量扫描命中任一 secret 即非零退出（T1/NFR-3"字面密钥=提交即阻断"，无 allowlist 基线）。
判定为纯函数（`assert_no_findings`），无需装 detect-secrets 即可单测，锁死"查得到≠查得准"盲点。

用法：`detect-secrets scan --all-files | python -m src.platform.harness.secret_scan`
或（工具在位时）`python -m src.platform.harness.secret_scan --run` 由本模块自调扫描器。
"""

from __future__ import annotations

import json
import subprocess
import sys


def assert_no_findings(payload: dict[str, object]) -> None:
    """detect-secrets 的 JSON results={文件:[命中...]}：任一非空即违规、抛 ValueError。

    detect-secrets 输出恒含 `results` 键；缺失=输出结构漂移（非可信扫描），必须 fail-loud 而非当空集放行。
    """
    if "results" not in payload:
        raise ValueError("detect-secrets 输出缺 results 键（格式漂移，判定不可信）")
    results = payload["results"]
    if not isinstance(results, dict):
        raise ValueError("detect-secrets results 非字典（格式漂移，判定不可信）")
    hits = {f: d for f, d in results.items() if d}
    if hits:
        raise ValueError(f"发现疑似字面密钥（T1/NFR-3 提交即阻断）：{list(hits)}")


def main(argv: list[str]) -> int:
    if "--run" in argv:  # 工具在位时的自足入口：直接调 detect-secrets 再判定
        proc = subprocess.run(["detect-secrets", "scan", "--all-files"], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print("detect-secrets 扫描失败", file=sys.stderr)
            return proc.returncode
        raw = proc.stdout
    else:  # 管道输入（hook/CI 用）：从 stdin 读扫描结果 JSON
        raw = sys.stdin.read()
    try:
        assert_no_findings(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"secret_scan：{exc}", file=sys.stderr)
        return 1
    print("secrets: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
