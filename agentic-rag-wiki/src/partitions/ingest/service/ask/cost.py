"""LLM 逐次调用 token/成本核算（§15 E4「LLM 调用逐次计 token 成本」离线代码路径）。

口径：离线代理 token 估算=len//4（与切块 `chunker`/`Chunk.input_token_limit` 同口径，可机检、可对账）。
真实 tokenizer 计数与云侧逐次 usage/成本回传=实机放行项（需真实 LLM 客户端，见 AGENTS §8）；
本模块只保证"每次出口调用都被逐次记账并落入审计事件"这一不变式，价格档由 settings 注入、dev 默认 0。
"""

from __future__ import annotations

from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    """粗估 token（离线代理口径）：非负、确定性，供逐次成本核算与对账。"""
    return max(len(text) // 4, 0)


@dataclass(frozen=True)
class CallCost:
    """一次 LLM 出口调用的 token 记账（逐次，非会话聚合）。"""

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def est_money(self, *, per_1k_in: float, per_1k_out: float) -> float:
        """按 settings 单价折算成本（dev 单价=0→成本 0；真实单价属云侧配置=放行项）。"""
        return self.prompt_tokens / 1000 * per_1k_in + self.completion_tokens / 1000 * per_1k_out


def account(prompt: str, completion: str) -> CallCost:
    """对一次（prompt, 生成文本）逐次计数。"""
    return CallCost(estimate_tokens(prompt), estimate_tokens(completion))
