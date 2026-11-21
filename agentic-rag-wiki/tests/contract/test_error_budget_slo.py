"""§15 E5 错误预算/SLO 降级判定离线契约：预算计算 + fast-burn 逐级降 + 外部计数先过门。

锁 `platform/breaker/slo.py` 的确定性计算口径与 `gateway.Admin.degrade_plan` 可达入口；
真·逐日作业/实时可用性/read 路径自动降档执行=运行时放行档，本文件不伪造调度器（见 AGENTS §8）。
"""

from __future__ import annotations

import pytest
from src.platform.breaker import slo
from src.platform.breaker.slo import ErrorBudget, degrade_level
from src.platform.gateway import build_container


def test_allowed_bad_is_ceil_of_one_minus_target_times_total() -> None:
    b = ErrorBudget(target=0.995, total=10_000, bad=0)
    assert b.allowed_bad == 50  # (1-0.995)*10000
    # 小额窗口向上取整，额度不被截成 0（否则永不胜任也永不爆）
    assert ErrorBudget(target=0.995, total=100, bad=0).allowed_bad == 1  # ceil(0.5)
    assert ErrorBudget(target=0.995, total=0, bad=0).allowed_bad == 0


def test_exhaustion_and_burn_ratio() -> None:
    b = ErrorBudget(target=0.995, total=10_000, bad=50)  # 恰好打满允许额
    assert b.allowed_bad == 50 and b.remaining == 0 and b.exhausted
    assert b.burn_ratio == 1.0
    ok = ErrorBudget(target=0.995, total=10_000, bad=10)
    assert not ok.exhausted and ok.remaining == 40 and ok.burn_ratio == pytest.approx(0.2)
    # allowed_bad=0 的极小窗口：任何坏即爆表
    tiny = ErrorBudget(target=0.999, total=10, bad=1)  # ceil(0.01*10)=1 → 允许 1
    assert tiny.allowed_bad == 1 and tiny.exhausted and tiny.burn_ratio == 1.0


def test_degrade_level_monotonic_fast_burn_ladder() -> None:
    assert degrade_level(ErrorBudget(0.995, 10_000, bad=25)) == 0  # 未透支
    assert degrade_level(ErrorBudget(0.995, 10_000, bad=50)) == 1  # 1×
    assert degrade_level(ErrorBudget(0.995, 10_000, bad=100)) == 2  # 2×
    assert degrade_level(ErrorBudget(0.995, 10_000, bad=200)) == 3  # 4×→封顶 3
    assert degrade_level(ErrorBudget(0.995, 10_000, bad=2000)) == slo.MAX_DEGRADE_LEVEL  # 不越界
    # 单调：坏越多，级别不减
    levels = [degrade_level(ErrorBudget(0.995, 10_000, bad=n)) for n in (0, 50, 100, 200, 400)]
    assert levels == sorted(levels) and levels[0] == 0


def test_assess_reason_and_zero_case() -> None:
    healthy = slo.assess(target=0.995, total=10_000, bad=5)
    assert healthy.level == 0 and healthy.exhausted is False and healthy.reason == ""
    hot = slo.assess(target=0.995, total=10_000, bad=100)
    assert hot.level == 2 and hot.exhausted and "FR-CAP-2" in hot.reason


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(target=0.0, total=100, bad=0),  # target 越界
        dict(target=1.0, total=100, bad=0),  # target 越界
        dict(target=0.995, total=-1, bad=0),  # 负总数
        dict(target=0.995, total=10, bad=11),  # bad>total
        dict(target=0.995, total=10, bad=-1),  # 负坏数
    ],
)
def test_external_counts_validated_at_construction(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ErrorBudget(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        slo.assess(**kwargs)  # type: ignore[arg-type]


def test_admin_degrade_plan_reachable_and_computes() -> None:
    """组合根可达入口（防"声称自动降级却无可决口径"的孤立缝）：Admin 由窗口计数返回降级方案。"""
    c = build_container()
    assert isinstance(c.admin.degrade_plan(total_requests=10_000, bad_requests=200).level, int)
    plan = c.admin.degrade_plan(total_requests=10_000, bad_requests=200)  # 4× → 第 3 级
    assert plan.level == 3 and plan.exhausted
    calm = c.admin.degrade_plan(total_requests=10_000, bad_requests=0)
    assert calm.level == 0 and calm.exhausted is False
    with pytest.raises(ValueError):
        c.admin.degrade_plan(total_requests=100, bad_requests=101)  # 越界计数在构造期即拒（T2）
