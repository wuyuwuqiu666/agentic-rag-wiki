"""LlmPort dev 档（dev_echo）：模板回显，零依赖跑通问答闭环。

不代表生成质量；执行档主档=cloud（受控网关出口），本地 3B GGUF 为按需换载位。
dev 档遵守引用协议：从依据块逐条吐出带 `[chunk_id]` 标记的接地句，使 FR-ASK-2 校验器可端到端验证。
"""

from __future__ import annotations

import re

_REF = re.compile(r"^\[([^\[\]\s]+)\]", re.MULTILINE)


class DevEchoLlm:
    def __init__(self, prefix: str = "[dev-echo]") -> None:
        self._prefix = prefix

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        refs = _REF.findall(prompt)
        if not refs:
            head = prompt.strip().splitlines()[0] if prompt.strip() else ""
            return f"{self._prefix} 依据上下文应答：{head[:80]}"
        return "。".join(f"该项有出处支持[{r}]" for r in refs) + "。"
