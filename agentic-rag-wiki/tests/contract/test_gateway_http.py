"""M4-bw gateway HTTP 门面契约：§5 "chat+wiki+admin **API**" 的 HTTP 半边落点验证。

补此前仅有 CLI/façade、无"浏览器可连 HTTP"的幻影产物——`App`/`Admin` façade 之上的 HTTP 层。断言口径：
①各端点 1:1 委派既有 façade 并返回可解析 JSON（本层零业务逻辑）；②ACL pre-filter 在 HTTP 面仍强制
（跨域提问拒答、越权出处读回 404，不给存在性泄露）；③演示 seed 使答案同时含 source/wiki 权威引用（wiki 优先）。
dev/演示档假设，不引质量结论。用 TestClient（httpx），不绑真实端口。
M4-by 追加写端点 `POST /api/ingest` 的往返契约：新文档经摄取链入库后即被同域问答命中（非幻影）、其 acl_tags
受 pre-filter 强制（越权主体拒答），并锁 T2 输入校验（空正文/非法 doc_id/非枚举 doc_type 一律 422 不进 façade）。
M4-bz 追加文件端点 `POST /api/ingest/file`（base64→FileParserPort 抽文本→同一摄取链）：.docx 入库后可被同域命中、
越权主体拒答；不支持扩展名 415、非法 base64/解析失败 422（fail-loud，不静默空入库）。
M4-ca 追加"知识域后端划分"契约：`GET /api/scope` 与带 `role` 的 `POST /api/ask` 都以 C 区角色聚合画像为唯一域口径
（画像未建立即 409 且不给任何域；role 在位时手工 `acl` 不参与，堵"多传一个 acl 就多一个域"的越权放大）。
M4-cb 追加 B 区人审台契约：`/api/wiki/{compile,pages,review}` 把"入库→草稿→人审→发布回写"这段此前只有 seed 程序化
走过的链路给到可达入口，并锁三条门——人审门（草稿不得直接发布、空署名不得发布，均由状态机判、门面只翻 409）、
可见域 pre-filter（越权页不列出、动作 404 不给存在性）、B0 总闸覆盖 B→A 回写（拉闸期发布被冻、复位后恢复）。
M4-cg 追加出处面的 D-2 块型契约：`/api/source` 的 `SourceView` 带 `chunk_type`/`parent_chunk_id`，且值来自切块结果
本身（表格块标 table 并以表头行起、不与散文同块）——右侧联动面板据此分排，门面不自行填默认值。
M4-cp 追加 B 区编译入口的租约契约：`POST /api/wiki/compile` 在 B0 拉闸期 503 且 fan-out 积压不减（冻结=推迟），
复位后再点才补上那轮编译并把租约走完（ADR-011）。
"""

from __future__ import annotations

import base64
import io
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from src.platform.config import Settings
from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.http import _create_app
from src.platform.providers.errors import BreakerTripped


def _minimal_docx(paragraphs: list[str]) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paragraphs)
    doc = f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path) -> TestClient:
    ctr: Container = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers=""))
    return TestClient(_create_app(ctr))


def test_health_declares_honest_assembly(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "assembly": "dev/fake", "quality": "echo-not-model", "seed": "seeded"}


def test_ask_delegates_and_orders_wiki_before_source(client: TestClient) -> None:
    r = client.post("/api/ask", json={"question": "出差住宿报销标准是什么", "acl": ["fin"]})
    assert r.status_code == 200
    ans: dict[str, Any] = r.json()
    assert set(ans) >= {"question", "text", "citations", "refused"}
    assert not ans["refused"]
    assert ans["citations"]
    # FR-WIKI-5 检索优先：wiki 权威页排在 source 原文之前
    authorities = [c["authority"] for c in ans["citations"]]
    assert authorities[0] == "wiki"
    assert authorities.index("wiki") < (authorities.index("source") if "source" in authorities else len(authorities))


def _polaris_payload(acl: list[str]) -> dict[str, Any]:
    return {
        "doc_id": "polaris-allowance",
        "version": 1,
        "title": "北极星专项津贴规定",
        "domain": "fin",
        "doc_type": "policy",
        "acl_tags": acl,
        # 含一个 seed 语料里不存在的独特词，令问答走通"新文档→可检索"这条往返而非蹭 seed 命中。
        "raw_text": "# 北极星专项津贴规定\n\n第一条：北极星专项津贴标准为每月八百八十元，随工资发放，无需申请。\n",
    }


def test_ingest_roundtrip_makes_new_doc_queryable(client: TestClient) -> None:
    # M4-by 入库端点非幻影：写端点 1:1 委派摄取链后，用同一独特词提问即命中新文档（未拒答、出处含该 doc）。
    r = client.post("/api/ingest", json=_polaris_payload(["fin"]))
    assert r.status_code == 200
    ev = r.json()
    assert ev["doc_id"] == "polaris-allowance"
    assert ev["version"] == 1 and ev["domain"] == "fin"
    assert ev["token_count"] > 0  # 真过 canonicalize/切块，非空信封

    ans = client.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["fin"]}).json()
    assert not ans["refused"]
    assert any(c["doc_id"] == "polaris-allowance" for c in ans["citations"])


def test_ingest_acl_tags_are_enforced_by_prefilter(client: TestClient) -> None:
    # 入库声明可见域＝fin：以 hr 主体提问同一独特词，pre-filter 在检索前滤除，答案拒答（不给越权内容）。
    assert client.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    ans = client.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["hr"]}).json()
    assert ans["refused"] or all(c["doc_id"] != "polaris-allowance" for c in ans["citations"])


def test_ingest_validates_external_input(client: TestClient) -> None:
    # T2：空正文、非法 doc_id（含 # / 空格）、空 doc_id 一律 422，不进 façade（防破坏 chunk_id 解析）。
    assert client.post("/api/ingest", json=_polaris_payload(["fin"]) | {"raw_text": ""}).status_code == 422
    assert client.post("/api/ingest", json=_polaris_payload(["fin"]) | {"doc_id": "bad/id"}).status_code == 422
    assert client.post("/api/ingest", json=_polaris_payload(["fin"]) | {"doc_id": ""}).status_code == 422
    assert client.post("/api/ingest", json=_polaris_payload(["fin"]) | {"doc_type": "nonsense"}).status_code == 422


def test_ask_rejects_blank_and_oversized_question(client: TestClient) -> None:
    # T2 外部输入校验：空问题被 schema 拦下（422），不进 façade
    assert client.post("/api/ask", json={"question": "", "acl": ["fin"]}).status_code == 422
    assert client.post("/api/ask", json={"question": "问" * 513, "acl": ["fin"]}).status_code == 422


def test_acl_prefilter_refuses_cross_domain_question(client: TestClient) -> None:
    # fin 主体问 hr 域内容：pre-filter 在检索前就过滤掉，答案应拒答（无出处不硬答）
    r = client.post("/api/ask", json={"question": "员工年假怎么算、病假找谁审批", "acl": ["fin"]})
    assert r.status_code == 200
    ans = r.json()
    assert ans["refused"] or all(ct.get("acl_tags") in (None, []) or "fin" in ct.get("acl_tags", []) for ct in ans["citations"])


def test_source_readback_returns_view_and_enforces_acl(client: TestClient) -> None:
    ans = client.post("/api/ask", json={"question": "出差住宿报销标准是什么", "acl": ["fin"]}).json()
    chunk_id = ans["citations"][0]["chunk_id"]
    r = client.get("/api/source", params={"chunk_id": chunk_id, "acl": "fin"})
    assert r.status_code == 200
    view = r.json()
    assert view["chunk_id"] == chunk_id
    assert set(view) >= {"chunk_id", "doc_id", "doc_version", "text", "authority", "breadcrumb", "acl_tags"}
    assert "vector" not in view  # 只回展示字段，不外泄向量


def test_source_readback_unknown_id_is_404_without_privilege_leak(client: TestClient) -> None:
    # 不存在的 id 与越权 id 同为 404（不区分二者，免得探测存在性）
    assert client.get("/api/source", params={"chunk_id": "nope#0", "acl": "fin"}).status_code == 404


def test_source_readback_exposes_d2_chunk_type(client: TestClient) -> None:
    """M4-cg 出处面锁：右侧联动面板要按块型分排，`chunk_type` 必须真从切块透到 HTTP 回执（非门面自己填默认值）。

    断言的是**结构**（表格块以 `|` 表头行起、块型标 table 且不与散文同块），不是检索质量；
    原子小表 parent_chunk_id=None（无行组退化）——退化父子链本身由 tests/contract/test_structure_chunking.py 锁。
    """
    payload = _polaris_payload(["fin"]) | {
        "doc_id": "d2-shape",
        "raw_text": (
            "# 津贴附表\n\n北极星专项津贴按岗位分档，具体口径见下表。\n\n"
            "| 岗位 | 金额 |\n| --- | --- |\n| 一线 | 880 |\n| 二线 | 660 |\n"
        ),
    }
    assert client.post("/api/ingest", json=payload).status_code == 200
    views = []
    for seq in range(8):
        r = client.get("/api/source", params={"chunk_id": f"d2-shape/1#{seq}", "acl": "fin"})
        if r.status_code == 404:
            break
        views.append(r.json())
    assert views, "入库后出处逐 seq 读回为空=切块未落索引"
    assert {v["chunk_type"] for v in views} <= {"text", "table", "code"}  # 只能是 D-2 枚举，不放开成自由字符串
    tables = [v for v in views if v["chunk_type"] == "table"]
    assert tables and all(v["text"].startswith("| 岗位 | 金额 |") for v in tables)
    assert all(v["parent_chunk_id"] is None for v in tables)
    assert all("880" not in v["text"] for v in views if v["chunk_type"] == "text")  # 表格不污染散文块


def test_admin_endpoints_delegate_façade(client: TestClient) -> None:
    assert client.get("/api/admin/funnel").status_code == 200
    assert isinstance(client.get("/api/admin/pool").json(), dict)
    events = client.get("/api/admin/audit", params={"kind": "wiki_publish"}).json()
    assert isinstance(events, list)
    assert all(e["kind"] == "wiki_publish" for e in events)


# —— M4-bz 文件入库端点：base64 字节 → FileParserPort 抽文本 → 同一摄取链（1:1，非幻影）——


def _docx_file_payload(acl: list[str]) -> dict[str, Any]:
    docx = _minimal_docx(["# 北极星专项津贴规定", "第一条：北极星专项津贴标准为每月八百八十元，无需申请。"])
    return {
        "doc_id": "polaris-docx",
        "version": 1,
        "title": "北极星专项津贴（docx）",
        "domain": "fin",
        "doc_type": "policy",
        "acl_tags": acl,
        "filename": "polaris.docx",
        "content_b64": base64.b64encode(docx).decode(),
    }


def test_ingest_file_docx_roundtrip_makes_new_doc_queryable(client: TestClient) -> None:
    r = client.post("/api/ingest/file", json=_docx_file_payload(["fin"]))
    assert r.status_code == 200
    ev = r.json()
    assert ev["doc_id"] == "polaris-docx"
    assert ev["source_file"] == "polaris.docx"
    assert ev["extracted_chars"] > 0  # 真抽出文本、非空入库
    ans = client.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["fin"]}).json()
    assert not ans["refused"]
    assert any(c["doc_id"] == "polaris-docx" for c in ans["citations"])


def test_ingest_file_acl_enforced_by_prefilter(client: TestClient) -> None:
    assert client.post("/api/ingest/file", json=_docx_file_payload(["fin"])).status_code == 200
    ans = client.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["hr"]}).json()
    assert ans["refused"] or all(c["doc_id"] != "polaris-docx" for c in ans["citations"])


def test_ingest_file_rejects_unsupported_and_bad_input(client: TestClient) -> None:
    # 不支持的扩展名=415（在解析前拦，省 base64 解码）；非法 base64=422；合法 zip 但缺正文/垃圾 PDF=422。
    base = _docx_file_payload(["fin"])
    assert client.post("/api/ingest/file", json=base | {"filename": "ledger.xls"}).status_code == 415
    # M4-ce 1c：.doc 已进支持集，但**容器真伪门**在转换前执法——OOXML zip 挂 .doc 名＝容器不符→422（不是 415 早退）
    assert client.post("/api/ingest/file", json=base | {"filename": "legacy.doc"}).status_code == 422
    assert client.post("/api/ingest/file", json=base | {"content_b64": "!!!not-base64!!!"}).status_code == 422
    assert client.post("/api/ingest/file", json=base | {"content_b64": base64.b64encode(b"junk").decode()}).status_code == 422


# —— M4-ca 知识域后端划分：角色→可见域由 C 区聚合画像给出，调用侧不再手工勾选（防"多传 acl 就多一个域"）——


def test_scope_endpoint_divides_domains_from_portrait(client: TestClient) -> None:
    # 后端划分口径可被读回：finance-analyst 的画像只携带 fin，跨域角色 kb-auditor 携带其并集。
    assert client.get("/api/scope", params={"role": "finance-analyst"}).json() == {"role": "finance-analyst", "domains": ["fin"]}
    assert client.get("/api/scope", params={"role": "kb-auditor"}).json()["domains"] == ["eng", "fin"]


def test_scope_unknown_role_fails_loud_and_grants_nothing(client: TestClient) -> None:
    # 画像未建立＝409 且不给任何域（不是"200 + 空域"更不是静默全放行）。
    r = client.get("/api/scope", params={"role": "ghost-role"})
    assert r.status_code == 409
    assert "不划分任何知识域" in r.json()["detail"]


def test_ask_with_role_hits_own_domain_and_refuses_other(client: TestClient) -> None:
    # 带 role 的问答：本域命中（wiki 优先引用在架），越权域在 pre-filter 前即被滤除→拒答。
    own = client.post("/api/ask", json={"question": "出差住宿报销标准是什么", "role": "finance-analyst"}).json()
    assert not own["refused"] and own["citations"]
    cross = client.post("/api/ask", json={"question": "员工年假怎么算、病假找谁审批", "role": "finance-analyst"}).json()
    assert cross["refused"]


def test_declared_acl_does_not_stack_on_top_of_backend_division(client: TestClient) -> None:
    # 关键越权不变式：role 在位时手工 acl 一律不参与——多传 acl 不能扩域（后端划分为唯一口径）。
    ans = client.post(
        "/api/ask", json={"question": "员工年假怎么算、病假找谁审批", "role": "finance-analyst", "acl": ["hr"]}
    ).json()
    assert ans["refused"]


def test_scope_validates_external_input(client: TestClient) -> None:
    # T2：空角色标识被 schema 拦下（422），不进画像查询。
    assert client.get("/api/scope", params={"role": ""}).status_code == 422


def test_source_readback_with_role_uses_the_same_backend_division(client: TestClient) -> None:
    # 出处读回与问答共用同一划分口径：本角色画像含该域即可读回，换角色读不回（404 不区分不存在/越权）。
    ans = client.post("/api/ask", json={"question": "出差住宿报销标准是什么", "role": "finance-analyst"}).json()
    chunk_id = ans["citations"][0]["chunk_id"]
    assert client.get("/api/source", params={"chunk_id": chunk_id, "role": "finance-analyst"}).status_code == 200
    assert client.get("/api/source", params={"chunk_id": chunk_id, "role": "hr-partner"}).status_code == 404
    assert client.get("/api/source", params={"chunk_id": chunk_id, "role": "ghost-role"}).status_code == 409




# —— M4-cb B 区人审台：`/api/wiki/{compile,pages,review}`（此前"入库后成为权威页"零可达入口的孤立缝）——

_FIN_POLICY = "wiki/fin/polaris-allowance"


def _compiled_fin_draft(client: TestClient) -> str:
    """备一页待审草稿：经写端点入库→跑一轮编译→返回其 page_id（草稿不靠 seed，靠真实链路产出）。"""
    assert client.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    assert client.post("/api/wiki/compile").status_code == 200
    return _FIN_POLICY


def test_wiki_compile_makes_drafts_only_for_eligible_docs(client: TestClient) -> None:
    # 编译门在 triage（制度/规范/runbook/圈定/长文才候选、纪要类不沉淀），门面不另判一套。
    assert client.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    assert client.post(
        "/api/ingest", json=_polaris_payload(["fin"]) | {"doc_id": "polaris-minutes", "doc_type": "meeting"}
    ).status_code == 200
    made = client.post("/api/wiki/compile").json()["new_drafts"]
    assert made >= 1
    pages = client.get("/api/wiki/pages", params={"role": "finance-analyst"}).json()
    ids = {p["page_id"] for p in pages}
    assert _FIN_POLICY in ids and "wiki/fin/polaris-minutes" not in ids
    draft = next(p for p in pages if p["page_id"] == _FIN_POLICY)
    assert draft["status"] == "draft" and draft["triage_reason"]  # FR-WIKI-4 可回放判定依据
    assert draft["sections"] and draft["sections"][0]["source_chunk_ids"]  # FR-WIKI-3 溯源随页可见


def test_wiki_review_flow_publishes_authoritative_page(client: TestClient) -> None:
    pid = _compiled_fin_draft(client)
    # 提交人审：draft → in_review
    r = client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"})
    assert r.status_code == 200 and r.json()["page"]["status"] == "in_review"
    # 署名通过：in_review → published，并把回写块经 IngestionPort 入 A 区索引（authority=wiki）
    r = client.post("/api/wiki/review", json={"page_id": pid, "action": "approve", "reviewer": "fin-owner", "role": "finance-analyst"})
    assert r.status_code == 200
    body = r.json()
    assert body["page"]["status"] == "published" and body["page"]["reviewer"] == "fin-owner"
    assert body["indexed"] >= 1
    # 发布结果可在读者侧核对：回写块按同一 pre-filter 读回，且审计留 wiki_publish
    view = client.get("/api/source", params={"chunk_id": f"{pid}#0", "role": "finance-analyst"})
    assert view.status_code == 200 and view.json()["authority"] == "wiki"
    events = client.get("/api/admin/audit", params={"kind": "wiki_publish"}).json()
    assert any(e["doc_id"] == pid for e in events)


def test_wiki_review_gate_blocks_unsubmitted_and_unsigned_publish(client: TestClient) -> None:
    # 人审门（FR-WIKI-1）执法点唯一：状态机。门面只翻码，不放松也不加严。
    pid = _compiled_fin_draft(client)
    r = client.post("/api/wiki/review", json={"page_id": pid, "action": "approve", "reviewer": "fin-owner", "role": "finance-analyst"})
    assert r.status_code == 409 and "人审门" in r.json()["detail"]  # 草稿不得直接发布
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"}).status_code == 200
    for unsigned in ("", "   "):
        r = client.post("/api/wiki/review", json={"page_id": pid, "action": "approve", "reviewer": unsigned, "role": "finance-analyst"})
        assert r.status_code == 409 and "署名" in r.json()["detail"]
    # 两次被拒后门未被绕过：页仍 in_review、无署名、回写块未进索引
    page = next(p for p in client.get("/api/wiki/pages", params={"role": "finance-analyst"}).json() if p["page_id"] == pid)
    assert page["status"] == "in_review" and page["reviewer"] == ""
    assert client.get("/api/source", params={"chunk_id": f"{pid}#0", "role": "finance-analyst"}).status_code == 404


def test_wiki_review_rejects_illegal_transitions(client: TestClient) -> None:
    # 状态机外的动作一律 409：草稿不可打回（未进审），已发布页不可再提交人审（须由源变更标脏）。
    pid = _compiled_fin_draft(client)
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "reject", "role": "finance-analyst"}).status_code == 409
    client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"})
    client.post("/api/wiki/review", json={"page_id": pid, "action": "approve", "reviewer": "fin-owner", "role": "finance-analyst"})
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"}).status_code == 409


def test_wiki_review_reject_returns_page_to_draft_for_another_round(client: TestClient) -> None:
    # 打回（in_review → draft）是审核台的"改"路径：不写索引、署名清空，且必须能重新提审（回路闭合非死态）。
    pid = _compiled_fin_draft(client)
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"}).status_code == 200
    r = client.post("/api/wiki/review", json={"page_id": pid, "action": "reject", "role": "finance-analyst"})
    assert r.status_code == 200
    assert r.json()["page"]["status"] == "draft" and r.json()["page"]["reviewer"] == "" and r.json()["indexed"] == 0
    assert client.get("/api/source", params={"chunk_id": f"{pid}#0", "role": "finance-analyst"}).status_code == 404
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"}).status_code == 200


def test_wiki_pages_and_review_are_prefiltered_by_acl(client: TestClient) -> None:
    # 页清单带正文，故与问答同一 `can_read_field` 闸：越权域的页不列出，动它对越权主体 404（不给存在性）。
    pid = _compiled_fin_draft(client)
    fin_ids = {p["page_id"] for p in client.get("/api/wiki/pages", params={"role": "finance-analyst"}).json()}
    hr_ids = {p["page_id"] for p in client.get("/api/wiki/pages", params={"role": "hr-partner"}).json()}
    assert pid in fin_ids
    assert not any(i.startswith("wiki/fin/") for i in hr_ids)
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "hr-partner"}).status_code == 404
    assert client.get("/api/wiki/pages").json() == []  # 无主体=不放大权限（冷启动不放行全域）
    # 手工 acl 叠在 role 上不扩域（承 M4-ca 同一口径）
    stacked = client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "hr-partner", "acl": ["fin"]})
    assert stacked.status_code == 404


def test_wiki_endpoints_validate_external_input(client: TestClient) -> None:
    pid = _compiled_fin_draft(client)
    assert client.post("/api/wiki/review", json={"page_id": "", "action": "submit", "role": "finance-analyst"}).status_code == 422
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "publish", "role": "finance-analyst"}).status_code == 422
    assert client.post("/api/wiki/review", json={"page_id": "wiki/fin/no-such", "action": "submit", "role": "finance-analyst"}).status_code == 404
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "ghost-role"}).status_code == 409
    assert client.get("/api/wiki/pages", params={"role": "ghost-role"}).status_code == 409


def test_wiki_publish_is_frozen_when_master_breaker_trips(wired: tuple[TestClient, Container]) -> None:
    # B0"冻结全部写侧"必须含 B→A 回写：人审发布=写索引，拉闸期不得绕过（复位后恢复）。
    client, ctr = wired
    pid = _compiled_fin_draft(client)
    assert client.post("/api/wiki/review", json={"page_id": pid, "action": "submit", "role": "finance-analyst"}).status_code == 200
    for _ in range(3):
        with pytest.raises(BreakerTripped):
            ctr.guard.check("batch_delete_ratio", 0.5, "B1", partition="ingest")
    r = client.post("/api/wiki/review", json={"page_id": pid, "action": "approve", "reviewer": "fin-owner", "role": "finance-analyst"})
    assert r.status_code == 503 and "拉闸" in r.json()["detail"]
    page = next(p for p in client.get("/api/wiki/pages", params={"role": "finance-analyst"}).json() if p["page_id"] == pid)
    assert page["status"] == "in_review"  # 被冻结的是写侧，不是把人审门一起绕过

    ctr.admin.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="runbook 处置完毕")
    r = client.post("/api/wiki/review", json={"page_id": pid, "action": "approve", "reviewer": "fin-owner", "role": "finance-analyst"})
    assert r.status_code == 200 and r.json()["page"]["status"] == "published"


def test_wiki_compile_freeze_defers_instead_of_consuming(wired: tuple[TestClient, Container]) -> None:
    """M4-cp/ADR-011：拉闸期点「跑一轮编译」＝503 且**一条事件都没消费**，复位后再点照旧出草稿。

    锁的是"冻结=推迟"而非"冻结=吃掉"：门写在 `drain` 之后时，这一轮会把 fan-out 取走又什么都不落地＝
    复位后既不重投、积压读数又显示已追平，运维以为没事（=审计 #6 的原始缺陷形态）。
    """
    client, ctr = wired
    assert client.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    queued = ctr.mq.depth("canonical.fanout")  # 含 seed 刻意留下的两条待编译事件，口径只比"有没有少"
    assert queued >= 1
    for _ in range(3):
        with pytest.raises(BreakerTripped):
            ctr.guard.check("batch_delete_ratio", 0.5, "B1", partition="ingest")

    r = client.post("/api/wiki/compile")
    assert r.status_code == 503 and "拉闸" in r.json()["detail"]
    assert "wiki/fin/polaris-allowance" not in _page_ids(client)  # 这轮根本没跑：没草稿、也没取事件
    assert ctr.mq.depth("canonical.fanout") == queued  # 一条没少：冻结=推迟，不是吃掉

    ctr.admin.reset_breaker("B1", operator="sre-a", approver="sre-b", reason="runbook 处置完毕")
    assert client.post("/api/wiki/compile").json()["new_drafts"] >= 1  # 推迟的那轮补上，一条不丢
    assert "wiki/fin/polaris-allowance" in _page_ids(client)
    assert ctr.mq.depth("canonical.fanout") == 0  # 租约走完（drain+ack）才归零


def _page_ids(client: TestClient) -> set[str]:
    return {p["page_id"] for p in client.get("/api/wiki/pages", params={"role": "finance-analyst"}).json()}


@pytest.fixture()
def wired(tmp_path) -> tuple[TestClient, Container]:
    ctr: Container = build_container(
        Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit"), shadow_breakers="")
    )
    return TestClient(_create_app(ctr)), ctr


# —— M4-ce 变更入口（下架 / 改可见域）+ 墓碑选单与恢复 ——


@pytest.fixture()
def observing(tmp_path) -> TestClient:
    """M1 观察口径档（`shadow_breakers` 默认 B1,B5＝只记不拦）：下架/恢复的正常往返在此口径下才可发生。

    与 `client`（shadow 清空＝全硬断）分档的用意：B1「批量删除占比」把"一次只删一条"算成 100% 删除占比，
    硬断档下人工单文档下架必被判超限——该口径误伤属熔断阈值语义（禁改区），故此处按里程碑分档各自断言，
    不偷偷放宽阈值让绿灯好绿。
    """
    ctr: Container = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))
    return TestClient(_create_app(ctr))


def _change_payload(doc_id: str, **over: Any) -> dict[str, Any]:
    return {"doc_id": doc_id, "kind": "delete", "actor": "sre-a", "confirm": True} | over


def test_ingest_change_delete_purges_both_indexes_and_stops_answering(observing: TestClient) -> None:
    """下架不是"改个标记"：问答不再命中、出处直读 404（向量+词法两路同清），且正文仍可恢复。"""
    assert observing.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    ask = {"question": "北极星专项津贴每月多少元", "acl": ["fin"]}
    before = observing.post("/api/ask", json=ask).json()
    assert not before["refused"]
    chunk_id = next(c["chunk_id"] for c in before["citations"] if c["doc_id"] == "polaris-allowance")

    r = observing.post("/api/ingest/change", json=_change_payload("polaris-allowance"))
    assert r.status_code == 200
    out = r.json()
    assert out["deleted"] >= 1
    assert out["recoverable"] >= 1 and "/api/ingest/restore" in out["hint"]

    after = observing.post("/api/ask", json=ask).json()
    assert after["refused"] or all(c["doc_id"] != "polaris-allowance" for c in after["citations"])
    assert observing.get("/api/source", params={"chunk_id": chunk_id, "acl": "fin"}).status_code == 404

    row = next(t for t in observing.get("/api/ingest/deletes").json()["items"] if t["doc_id"] == "polaris-allowance")
    assert row["recoverable"] is True and row["has_body"] is True and row["seconds_left"] > 0


def test_ingest_restore_replays_the_document_back(observing: TestClient) -> None:
    """§10 保留期内一键回滚：恢复后同一条提问重新命中，墓碑从待恢复账上消失（幂等复现，非新内容）。"""
    assert observing.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    ask = {"question": "北极星专项津贴每月多少元", "acl": ["fin"]}
    assert observing.post("/api/ingest/change", json=_change_payload("polaris-allowance")).status_code == 200
    assert observing.post("/api/ask", json=ask).json()["refused"]

    r = observing.post("/api/ingest/restore", json={"doc_id": "polaris-allowance", "actor": "sre-a", "confirm": True})
    assert r.status_code == 200
    assert r.json()["restored"] == 1 and r.json()["expired_unrecoverable"] == 0

    back = observing.post("/api/ask", json=ask).json()
    assert not back["refused"] and any(c["doc_id"] == "polaris-allowance" for c in back["citations"])
    assert [t for t in observing.get("/api/ingest/deletes").json()["items"] if t["doc_id"] == "polaris-allowance"] == []


def test_deletes_view_separates_window_from_body(tmp_path) -> None:
    """M4-cn⑧ 门面口径：读回三个汇总数与恢复回执同一把尺——窗内行数＝`recoverable + body_missing`，正文已丢的不进可回滚面。

    锁这条是因为可恢复性分处两层算（`pending_deletes` 逐行 vs `restore_deletes` 汇总）：任一层各算各的，
    运维就会在同一页上看到两个"还能找回几条"，而其中一个是虚报的。
    """
    ctr: Container = build_container(Settings(artifact_dir=str(tmp_path), audit_dir=str(tmp_path / "audit")))
    app = TestClient(_create_app(ctr))
    assert app.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    assert app.post("/api/ingest/change", json=_change_payload("polaris-allowance")).status_code == 200

    ctr.objectstore._path("polaris-allowance/1").unlink()  # 正文物理丢：墓碑仍在窗内，但恢复必失败

    view = app.get("/api/ingest/deletes").json()
    rows = [t for t in view["items"] if t["doc_id"] == "polaris-allowance"]
    assert len(rows) == 1 and rows[0]["recoverable"] is True and rows[0]["has_body"] is False
    assert view["recoverable"] == 0 and view["body_missing"] == 1 and view["expired"] == 0

    out = app.post(
        "/api/ingest/restore", json={"doc_id": "polaris-allowance", "actor": "sre-a", "confirm": True}
    ).json()
    assert out["restored"] == 0 and out["recoverable"] == 0 and out["body_missing"] == 1


def test_ingest_change_and_restore_validate_external_input(observing: TestClient) -> None:
    """T2 + 破坏性动作门：无 confirm／confirm=false／空署名一律 422；执行档无消费点的 kind 也拒收（不静默收下不做事）。"""
    base = _change_payload("polaris-allowance")
    assert observing.post("/api/ingest/change", json={k: v for k, v in base.items() if k != "confirm"}).status_code == 422
    assert observing.post("/api/ingest/change", json=base | {"confirm": False}).status_code == 422
    assert observing.post("/api/ingest/change", json=base | {"actor": ""}).status_code == 422
    assert observing.post("/api/ingest/change", json=base | {"kind": "upsert"}).status_code == 422
    assert observing.post("/api/ingest/change", json=base | {"kind": "domain_move"}).status_code == 422
    assert (
        observing.post(
            "/api/ingest/change",
            json={"doc_id": "polaris-allowance", "kind": "acl_change", "actor": "sre-a", "confirm": True},
        ).status_code
        == 422
    )
    assert observing.post("/api/ingest/restore", json={"actor": "sre-a", "confirm": False}).status_code == 422


def test_acl_change_endpoint_evicts_cache_and_declares_pending_effects(observing: TestClient) -> None:
    """FR-ING-5 效应②③④ 在 HTTP 面真落地；①（PG 权限行）未落地即如实列在 effects_pending，不谎称全量生效。

    回执的 `effects_done` 必须与可测点一一对上：②看检索结局（旧域拒答/新域可答）、③看 `cache_evicted`
    真实笔数（含②重写途中"先摘旧块"顺带清掉的那笔），④只声称"随 fan-out 在下一轮编译标脏撤块"而不声称即刻改完。
    """
    assert observing.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    assert not observing.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["fin"]}).json()["refused"]

    r = observing.post(
        "/api/ingest/change",
        json={
            "doc_id": "polaris-allowance",
            "kind": "acl_change",
            "actor": "sre-a",
            "confirm": True,
            "acl_before": ["fin"],
            "acl_after": ["hr"],
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out["cache_evicted"] >= 1  # ③：真实剔除数入账，不因②先清而报 0（少报=声称与实际背离）
    assert out["acl_rewritten"] >= 1 and out["acl_pending"] == 0  # ②：在册块全部换域，无"没改却不说"
    assert out["recoverable"] == 0  # 非删除动作不产生墓碑
    assert out["effects_pending"] == ["PG 权限行（效应①，真实双存储=放行项②）"]
    assert observing.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["fin"]}).json()["refused"]
    assert not observing.post("/api/ask", json={"question": "北极星专项津贴每月多少元", "acl": ["hr"]}).json()["refused"]


def test_hard_b1_blocks_single_document_takedown(wired: tuple[TestClient, Container]) -> None:
    """B1 硬断档（M4 口径）下"一次删一条"＝100% 删除占比：门面不得绕过闸，也不得留下半生效状态。

    这是阈值语义本身对人工单文档下架的口径误伤（属禁改区，须 ADR 定标后才谈调整），本用例把它钉成可见行为：
    503 带可读原因 → 文档仍可答、墓碑账为空（既未部分生效，也未谎称"已受理"）。
    """
    client, _ctr = wired
    assert client.post("/api/ingest", json=_polaris_payload(["fin"])).status_code == 200
    ask = {"question": "北极星专项津贴每月多少元", "acl": ["fin"]}

    r = client.post("/api/ingest/change", json=_change_payload("polaris-allowance"))
    # 回执点名真闸（M4-cj 顺带纠正的一条误标）：这一例是 B1 拦停，写成"（B0）"会让人去复位没触发的总闸。
    assert r.status_code == 503 and "（B1）" in r.json()["detail"] and "（B0）" not in r.json()["detail"]
    assert not client.post("/api/ask", json=ask).json()["refused"]
    assert client.get("/api/ingest/deletes").json()["items"] == []
