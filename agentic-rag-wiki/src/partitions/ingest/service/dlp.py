"""内容安全单一口径（B4 注入/外泄）：入口准入隔离与出口答案脱敏共用同一密文模式集。

§8.5 B4 挂点=「平台·内容入口+出口」、§12 生成后门列「DLP 模式」作用于答案出口/缓存写入——
入口（`admission`）按此把含密块隔离不入库，出口（`runtime/ask`）按此把答案里的明文口令脱敏。
一处定义=防入口/出口口径漂移，否则入口拦得住、出口漏得出去即泄露通道。
真·结构化 DLP（PII/证件/卡号/正则库、可域配置）为外部供给=实机放行项；本层为确定性可单测的 dev 档模式集。
"""

from __future__ import annotations

import re

# §15 E8「DLP 规则版本化」：模式集版本戳（正则/关键字集变更须进位并留痕，使审计可回放"哪套规则做的脱敏"）。
# dev 档=确定性口令模式集；真·结构化 DLP 规则库（PII/证件/卡号、按域配置）接入时按 SemVer 升位并随放行项换版。
RULES_VERSION = "dev-secret-kv-v1"

# 覆盖常见键值对口令字面量（词边界宽松匹配，值取到首个空白止）。
SECRET_RE = re.compile(r"(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*\S+", re.IGNORECASE)
_MASK = "‹DLP-REDACTED›"


def ruleset_tag() -> dict[str, str]:
    """审计事件用的规则版本标签（入口隔离与出口脱敏同贴，供对账"生效规则版本"）。"""
    return {"dlp_rules_version": RULES_VERSION}


def find_secrets(text: str) -> list[str]:
    """命中列表（每处取关键字名）；空=无明文密文。"""
    return SECRET_RE.findall(text)


def redact(text: str) -> tuple[str, int]:
    """脱敏：把命中的 `key: value` 整段替换为掩码，返回 (净化文本, 命中数)。

    命中数>0 即答案出口触发 §12 生成后门 DLP 闸——上层据此脱敏呈现、发 B4 信号、且不得入缓存。
    """
    hits = len(SECRET_RE.findall(text))
    return SECRET_RE.sub(_MASK, text), hits
