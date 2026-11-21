"""gateway 问答/运维命令面（§5 line 93 "chat+wiki+admin API/**CLI**" 的 CLI 半边，执行档 stdlib 落点）。

组合根 `build_container()` 已把 `App`/`Admin` 程序化 façade 接成可达入口（M4-x）；本模块给出**人可敲的命令行**：
问答出口 + 运维读回（审计回放/分池预算/B5 对账/消费漏斗/SLO 降级方案）+ B5 缺失块补偿、影子索引全量重建、
搜索别名切回（三条写侧动作，M4-cj/M4-cm，均须 `--confirm`）+ 双人复位。全部委派既有、已单测的 façade 方法，
本层零业务逻辑、零新依赖（argparse/json 标准库）、不出网、不绑端口、不改冻结契约。

**边界（诚实标注，勿误认已交付）**：
- 真·HTTP "API" 门面＝**放行档**（FastAPI 属新依赖须 T4 放行 + 进程内起服务属运行时/R10），不在此伪造；
- **认证＝放行档**：`--acl` 由调用侧提供**已鉴权主体**的权限域，pre-filter 在 `App` 内执法；本 CLI 不实现 SSO/LDAP、不判"谁能问"；
- 双人复位的授权语义沿用 `Admin.reset_breaker`（两签署人相异且非空 + 必填理由），跨人审批工作流＝E9 治理放行。
- **命令名口径（审计 #9）**：执行档**没有** `agentic-rag` 这个可执行程序（`pyproject` 无 `[project.scripts]`、亦无 build-system ⇒ 不可 `pip install`），
  唯一可达调用形式＝仓库根目录下 `python -m src.platform.gateway.cli …`；`prog`、报错行前缀与全部处置页都按这一形式写（`_INVOKE` 单源），`tests/smoke/test_cli_invocations_documented.py` 机检。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from typing import cast

from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.dir_ingest import run_dir_ingest
from src.platform.types.breaker import BreakerId

_BREAKER_CHOICES = ["B0", "B1", "B2", "B3", "B4", "B5"]
_DOC_TYPE_CHOICES = ["policy", "contract", "meeting", "wiki", "runbook", "other"]

# 唯一可达调用形式（审计 #9）：usage、报错行、处置页三处**同一口径**，不分叉——运维照抄哪一处都能跑通。
_INVOKE = "python -m src.platform.gateway.cli"


def _jsonable(obj: object) -> object:
    """façade 返回值 → JSON 可序列化：pydantic(model_dump) / dataclass(asdict) / 容器递归 / 原样标量。"""
    dump = getattr(obj, "model_dump", None)
    if callable(dump):  # pydantic BaseModel（Answer / AuditEvent），不 import pydantic 以免引依赖
        return dump()
    if is_dataclass(obj) and not isinstance(obj, type):  # DegradePlan
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def _emit(obj: object) -> None:
    print(json.dumps(_jsonable(obj), ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    # prog＝**可直接照抄执行的那条命令**，不是展示用的短名。执行档没有 `[project.scripts]`（也没有 build-system），
    # 写 `agentic-rag …` 会让运维照抄即 command not found，而 argparse 的 usage/报错行正是运维最容易抄的一处（审计 #9）。
    p = argparse.ArgumentParser(
        prog=_INVOKE,
        description="Agentic RAG + LLM wiki 执行档命令面（dev/fake 装配，离线可跑；仓库根目录下执行）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="问答出口（检索漏斗→答案+引用，ACL 域由 --acl 提供）")
    ask.add_argument("question")
    ask.add_argument("--acl", action="append", default=None, help="已鉴权主体的权限域，可多次（pre-filter 据此过滤候选）")
    ask.add_argument("--trace-id", default=None, help="E4 会话贯通追踪 id")

    ing = sub.add_parser(
        "ingest",
        help="目录批量入库（FR-ING-1 存量遍历；读盘范围由 APP_INGEST_SOURCE_DIR 允许根目录围栏，未配即拒绝）",
    )
    ing.add_argument("--directory", default="", help="相对允许根目录的路径；缺省=根目录本身")
    ing.add_argument("--domain", default="default", help="本批文档的知识域缺省")
    ing.add_argument("--doc-type", default="other", choices=_DOC_TYPE_CHOICES, help="本批文档的类型缺省")
    ing.add_argument("--acl", action="append", default=None, help="本批文档的可见域，可多次")
    ing.add_argument("--confirm", action="store_true", required=True, help="整目录=批量写侧动作，须显式确认")

    adm = sub.add_parser("admin", help="运维/审计管理面读回与复位").add_subparsers(dest="admin_command", required=True)
    rep = adm.add_parser("audit-replay", help="U4 只读回放审计（append-only 不受影响）")
    rep.add_argument("--kind", action="append", default=None, help="事件类型过滤，可多次；缺省=全量")
    rep.add_argument("--since-ts", type=float, default=None, help="时间下界（含）")
    adm.add_parser("pool", help="§8.5 B2 分池预算三本独立账读数")
    adm.add_parser(
        "reconcile",
        help="FR-ING-4 B5 双册账读回（源块＋权威页回写块）；只读不传感，刷新不会把看账变成拉闸",
    )
    ah = adm.add_parser(
        "acl-history",
        help="FR-ING-5 受众变更前值账读回（从哪些域收到哪些域、落在在册块还是下架件）；只读不传感",
    )
    ah.add_argument("doc_id", help="文档 id")
    ah.add_argument("--version", type=int, default=None, help="只看该版本；缺省=该文档全部版本的历史")
    cp = adm.add_parser(
        "compensate",
        help="FR-ING-4 缺失块补偿（写侧动作：按清单指针重拉正文幂等重放）；须显式确认，B5 判门在写侧之前",
    )
    cp.add_argument("--confirm", action="store_true", required=True, help="批量改索引＝写侧动作，须显式确认")
    ri = adm.add_parser(
        "reindex",
        help="FR-ING-3 影子索引全量重建（写侧：重放全部在册文档→原子切别名→切换失败即时回滚）；须显式确认，B0 判门在重放之前",
    )
    ri.add_argument("--confirm", action="store_true", required=True, help="全量重放＝最重的写侧动作，须显式确认")
    ar = adm.add_parser(
        "alias-revert",
        help="§10 高危回滚点：搜索别名整域切回保留窗内的上一版本（写侧，切换成功后的主动人工回退）",
    )
    ar.add_argument("--from-alias", required=True, help="当前指向的新版本别名")
    ar.add_argument("--to-alias", required=True, help="要退回的旧版本别名（不得与 --from-alias 同名）")
    ar.add_argument("--confirm", action="store_true", required=True, help="改搜索指向＝写侧动作，须显式确认")
    fun = adm.add_parser("funnel", help="§8.5 FR-CAP-1 消费漏斗四层命中账本")
    fun.add_argument("--window-seconds", type=float, default=None, help="滚动窗秒数；缺省=全量")
    dp = adm.add_parser("degrade-plan", help="§15 E5 给定窗口可用性计数→错误预算透支判级")
    dp.add_argument("--total", type=int, required=True)
    dp.add_argument("--bad", type=int, required=True)
    dp.add_argument("--slo-target", type=float, default=0.995)
    rs = adm.add_parser("reset", help="§10 双人复位一条未复位硬断（解除 B0 冻结、恢复写侧）")
    rs.add_argument("--breaker", required=True, choices=_BREAKER_CHOICES)
    rs.add_argument("--operator", required=True)
    rs.add_argument("--approver", required=True)
    rs.add_argument("--reason", required=True, help="复位必填理由（R9 入审计）")
    return p


def _dispatch(container: Container, args: argparse.Namespace) -> None:
    if args.command == "ask":
        _emit(container.app.answer(args.question, user_acl=args.acl, trace_id=args.trace_id))
        return
    if args.command == "ingest":
        _emit(
            run_dir_ingest(
                container,
                directory=args.directory,
                domain=args.domain,
                doc_type=args.doc_type,
                acl_tags=args.acl,
            )
        )
        return
    admin = container.admin
    cmd = args.admin_command
    if cmd == "audit-replay":
        _emit(admin.replay_audit(kinds=set(args.kind) if args.kind else None, since_ts=args.since_ts))
    elif cmd == "pool":
        _emit(admin.pool_budget())
    elif cmd == "reconcile":
        # 1:1 委派 A 区管线的对账（与 GET /api/ingest/reconcile 同一函数、同一本账，不在命令面另算一遍口径）。
        # sense=False＝只读不传感：命令行可反复敲，不得每次敲都往 B5 台账记一条未复位触发。
        _emit(container.ingest.reconcile(sense=False))
    elif cmd == "acl-history":
        # 1:1 管线内前值账（与 GET /api/ingest/acl-history 同一函数、同一本账）：只读不传感、不改受众。
        _emit(
            {
                "doc_id": args.doc_id,
                "version": args.version,
                "entries": container.ingest.acl_history(args.doc_id, args.version),
            }
        )
    elif cmd == "compensate":
        # 1:1 委派 A 区补偿作业（与 POST /api/ingest/compensate 同一函数、同两道门、同一次传感）。
        # 判门在写侧之前：先 B0 总闸（M4-cv／审计 #14）再 B5 单类闸，硬断档下以 BreakerTripped 结束且零部分效果。
        _emit(container.ingest.compensate())
    elif cmd == "reindex":
        # 1:1 委派 A 区影子重建（与 POST /api/ingest/reindex 同一函数、同一次 B0 判门，命令面不另开口径）。
        # 故意不给"干跑看看"参数：本条每一步都是真实全量重放，要预览请先用 reconcile 只读入口看账（只读不传感）。
        _emit(container.ingest.shadow_rebuild())
    elif cmd == "alias-revert":
        # 1:1 委派手动切回（与 POST /api/ingest/alias/revert 同一函数）：同名或后端抛错原样上抛→退出码 1，不静默回退失败。
        container.ingest.revert_alias(from_alias=args.from_alias, to_alias=args.to_alias)
        _emit({"from_alias": args.from_alias, "to_alias": args.to_alias, "swapped": True})
    elif cmd == "funnel":
        _emit(admin.funnel_stats(window_seconds=args.window_seconds))
    elif cmd == "degrade-plan":
        _emit(admin.degrade_plan(total_requests=args.total, bad_requests=args.bad, slo_target=args.slo_target))
    elif cmd == "reset":
        admin.reset_breaker(
            cast(BreakerId, args.breaker), operator=args.operator, approver=args.approver, reason=args.reason
        )
        _emit({"reset": args.breaker, "operator": args.operator, "approver": args.approver, "reason": args.reason})
    else:  # pragma: no cover — argparse required 已拦
        raise SystemExit(f"未知 admin 子命令：{cmd}")


def main(argv: list[str] | None = None, *, container: Container | None = None) -> int:
    """解析 argv→委派 façade；façade 抛错（越权/双人校验/provider fail-loud）经 stderr 呈现并 exit 1，不 traceback 崩操作台。"""
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    ctr = container if container is not None else build_container()
    try:
        _dispatch(ctr, args)
    except Exception as exc:  # noqa: BLE001 — 命令面边界：把领域/校验异常转成人可读的非零退出（fail-loud 非静默）
        print(f"{_INVOKE}：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
