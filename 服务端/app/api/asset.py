# -*- coding: utf-8 -*-
"""领域知识资产 5 个二级菜单的接口（大模型配置已移到「系统」组）。

栏目按**规则加工链**排序，不是按字母或重要性：
  制度文件库 → 规则抽取 → 规则本体 → 流程本体 → 发布管理
这个顺序本身就是信息：上游没有的东西，下游不可能凭空长出来。
每条规则都必须能回溯到制度文件库里的某一份文件、某一条条款。
"""
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from .. import db
from . import op_log
from .common import demo_banner, must_exist, paginate

router = APIRouter(prefix="/api/asset", tags=["领域知识资产"])


# ─────────────────────────────────────────────────────────────────────────────
# 导航：规则资产 5 个二级菜单 + 系统组
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/nav")
def nav():
    """返回 6 个栏目及各自的实测条数。

    条数直接从库里数，不写死——写死的数字会和实际数据漂移，
    而前端上那个数字恰恰是用户判断「这块有没有东西」的依据。
    """
    counts = {
        "doc-lib": db.scalar("SELECT count(*) FROM policy_doc") or 0,
        "extract": db.scalar("SELECT count(*) FROM extract_candidate") or 0,
        "onto": db.scalar("SELECT count(*) FROM rule WHERE version=1") or 0,
        "proc": db.scalar("SELECT count(*) FROM process_node "
                          "WHERE version=(SELECT version FROM ontology_version WHERE is_current)") or 0,
        "publish": db.scalar("SELECT count(*) FROM release") or 0,
        "llm": db.scalar("SELECT count(*) FROM llm_config") or 0,
    }
    items = [
        {"key": "doc-lib", "label": "制度文件库", "order": 1,
         "desc": "规则的唯一上游。每条规则都要能回溯到这里的某份文件、某条条款"},
        {"key": "extract", "label": "规则抽取", "order": 2,
         "desc": "两档：自动抽取给草稿，人工确认才落规则本体。没有自动发布这条路"},
        {"key": "onto", "label": "规则本体", "order": 3,
         "desc": "八要素 + 对齐客户配置器的 4 个字段；查找表在这里维护"},
        {"key": "proc", "label": "流程本体", "order": 4,
         "desc": "节点与迁移全部数自 50 页真实审批记录，不是设计出来的"},
        {"key": "publish", "label": "发布管理", "order": 5,
         "desc": "版本化 + 人工签字。reviewed_by 为空不允许进 published"},
        # 2026-10-18 移到「系统」组。条数接口仍然回它——
        # 前端是按 key 取 count 的，少回一项侧栏那个数字就空着，
        # 而空着看起来像「库里没配置」，不是「没在这组里」。
        {"key": "llm", "label": "大模型配置", "order": 1, "group": "sys",
         "desc": "系统级配置。Key 只回掩码，明文永不出库"},
    ]
    for it in items:
        it["count"] = counts[it["key"]]
    return {"items": items, "banner": demo_banner()}


# ─────────────────────────────────────────────────────────────────────────────
# 栏目一 · 制度文件库
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/docs")
def list_docs(page: int = 1, size: int = 20):
    return paginate(
        "SELECT count(*) FROM policy_doc",
        """SELECT doc_id, file_name, doc_number, title, issuer, issued_on,
                  page_count, parse_status, parse_reason, has_text_layer,
                  ocr_engine, ocr_pages, uploaded_at
           FROM policy_doc ORDER BY uploaded_at DESC""", (), page, size)


@router.get("/docs/{doc_id}")
def get_doc(doc_id: str):
    doc = must_exist(db.query_one("SELECT * FROM policy_doc WHERE doc_id=%s", (doc_id,)),
                     "制度文件 " + doc_id)
    doc["clauses"] = db.query(
        "SELECT clause_id, clause_no, page_no, confidence, text_content "
        "FROM policy_clause WHERE doc_id=%s ORDER BY clause_no", (doc_id,))
    doc["pages"] = db.query(
        "SELECT page_no, text_source, confidence, length(text_content) AS chars "
        "FROM policy_doc_page WHERE doc_id=%s ORDER BY page_no, text_source", (doc_id,))
    # 哪些规则引用了这份文件——反向可追溯性，栏目一存在的理由
    doc["referenced_by"] = db.query(
        """SELECT rule_id, name AS rule_name FROM rule
           WHERE version=1 AND source_clause->>'file' IS NOT NULL
             AND %s LIKE '%%' || (source_clause->>'file') || '%%'
           ORDER BY rule_id""", (doc["file_name"],))
    return doc


@router.get("/docs/{doc_id}/parse-status")
def parse_status(doc_id: str):
    """解析状态四态，**failed 必须配原因**。

    「解析失败」和「文件本来就是空的」是两件事，混了就再也查不出来。
    实测：6 份制度 PDF 里 2 份有文字层、4 份纯扫描（含最关键的 27 页那份）。
    """
    d = must_exist(db.query_one(
        "SELECT doc_id, parse_status, parse_reason, has_text_layer, ocr_engine, ocr_pages "
        "FROM policy_doc WHERE doc_id=%s", (doc_id,)), "制度文件 " + doc_id)
    if d["parse_status"] == "failed" and not d["parse_reason"]:
        d["_warning"] = "解析失败但没有记原因——这条数据本身就是个问题"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 栏目二 · 规则抽取
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/extract/tasks")
def list_extract_tasks(page: int = 1, size: int = 20):
    return paginate(
        "SELECT count(*) FROM extract_task",
        """SELECT t.task_id, t.doc_id, d.file_name, t.mode, t.status,
                  t.candidate_cnt, t.accepted_cnt, t.started_at, t.finished_at, t.error
           FROM extract_task t LEFT JOIN policy_doc d ON d.doc_id=t.doc_id
           ORDER BY t.created_at DESC""", (), page, size)


@router.get("/extract/candidates")
def list_candidates(task_id: str = None, state: str = None, page: int = 1, size: int = 20):
    where, params = [], []
    if task_id:
        where.append("c.task_id = %s")
        params.append(task_id)
    if state:
        where.append("c.review_state = %s")
        params.append(state)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return paginate(
        "SELECT count(*) FROM extract_candidate c" + w,
        """SELECT c.id, c.task_id, c.clause_id, c.draft, c.confidence,
                  c.review_state, c.reviewer, c.reviewed_at, c.reject_reason
           FROM extract_candidate c""" + w + " ORDER BY c.id", tuple(params), page, size)


@router.get("/extract/policy")
def extract_policy():
    """抽取的处置口径。前端要把这段显示出来，不能只给个「确认」按钮。"""
    return {
        "two_tier": "自动抽取给草稿，人工确认才落规则本体",
        "no_auto_publish": True,
        "why": "规则是制度的投影，判错一条就是对一批单据误报或漏报。"
               "模型抽出来的八要素里，「判定逻辑」和「例外」两项最容易丢细节——"
               "实测市内交通费那条，制度原文有 40 元这一档（仅往返中转站），"
               "漏掉就会把样本里 120 元的正常单判成超标准。",
        "states": ["pending", "accepted", "rejected", "merged"],
        "requires_reviewer": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 栏目三 · 规则本体
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/rules")
def list_rules(dimension: str = None, rule_class: str = None,
               compiled: bool = None, executable: bool = None,
               q: str = None, page: int = 1, size: int = 50):
    where, params = ["version=1"], []
    if dimension:
        where.append("dimension=%s")
        params.append(dimension)
    if rule_class:
        where.append("rule_class=%s")
        params.append(rule_class)
    if compiled is not None:
        where.append("compiled=%s")
        params.append(compiled)
    if executable is not None:
        where.append("demo_executable=%s")
        params.append(executable)
    if q:
        where.append("(rule_id ILIKE %s OR name ILIKE %s)")
        params += ["%%%s%%" % q, "%%%s%%" % q]
    w = " WHERE " + " AND ".join(where)
    out = paginate(
        "SELECT count(*) FROM rule" + w,
        """SELECT rule_id, name AS rule_name, dimension, risk_class, rule_class,
                  control_type,
                  priority, algo_code, algo_type, configurator_status,
                  compiled, compiled_reason, demo_executable, demo_blocker, demo_scope,
                  binding_node, impl_path, feasibility, external_table, owner
           FROM rule""" + w + " ORDER BY dimension, rule_id", tuple(params), page, size)
    out["summary"] = rules_summary()
    return out


@router.get("/rules/summary")
def rules_summary():
    """三个数并列，**不能只报可编译数**。

    「可编译 37」和「演示可执行 12」是两件事：前者是规则资产层面，
    后者是手上有没有数据。只报一个，产品缺口和取数缺口就分不清了。
    """
    row = db.query_one("""SELECT count(*) total,
                          sum(CASE WHEN compiled THEN 1 ELSE 0 END) compiled,
                          sum(CASE WHEN demo_executable THEN 1 ELSE 0 END) executable
                          FROM rule WHERE version=1""")
    by_dim = db.query("""SELECT dimension, count(*) n,
                         sum(CASE WHEN compiled THEN 1 ELSE 0 END) c,
                         sum(CASE WHEN demo_executable THEN 1 ELSE 0 END) e
                         FROM rule WHERE version=1 GROUP BY dimension ORDER BY dimension""")
    by_class = db.query("SELECT rule_class, count(*) n FROM rule WHERE version=1 "
                        "GROUP BY rule_class")
    by_control = db.query("SELECT control_type, count(*) n FROM rule WHERE version=1 "
                          "GROUP BY control_type")
    return {"total": row["total"], "compiled": row["compiled"],
            "executable": row["executable"],
            "note": "compiled = 规则资产层面已编译；executable = 用当前数据真能跑。"
                    "两者不同的那 %d 条，差在「字段取不到」而不是「规则没写好」"
                    % (row["compiled"] - row["executable"]),
            "by_dimension": by_dim, "by_class": by_class, "by_control": by_control}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: str):
    r = must_exist(db.query_one("SELECT * FROM rule WHERE rule_id=%s AND version=1",
                                (rule_id,)), "规则 " + rule_id)
    # SELECT * 起不了别名，在出口处改键：叫 name 的字段会被人名脱敏吃成「付**********」。
    # 规则名不是人名——但字段名一样，策略就一样。
    r["rule_name"] = r.pop("name", None)
    # oc.name 必须起别名：叫 name 的字段会被人名脱敏吃成「核****」
    r["concepts"] = db.query("""SELECT rc.concept_id, rc.role,
                                       oc.name AS concept_name, oc.availability,
                                       oc.gap_id, oc.entity_id
                                FROM rule_concept rc
                                LEFT JOIN ontology_concept oc
                                  ON oc.concept_id = rc.concept_id
                                 AND oc.version = (SELECT version FROM ontology_version
                                                   WHERE is_current)
                                WHERE rc.rule_id=%s AND rc.version=1""", (rule_id,))
    r["entities"] = db.query("SELECT entity_id, semantics FROM rule_entity "
                             "WHERE rule_id=%s AND version=1", (rule_id,))
    r["relations"] = db.query("""SELECT rel_type, other_rule, note, needs_confirm, gap_id
                                 FROM rule_relation WHERE rule_id=%s AND version=1""",
                              (rule_id,))
    r["recent_findings"] = db.query("""SELECT f.run_id, f.outcome, f.message, r.bill_no
                                       FROM review_finding f
                                       JOIN review_run r ON r.run_id=f.run_id
                                       WHERE f.rule_id=%s ORDER BY f.id DESC LIMIT 10""",
                                    (rule_id,))
    return r


# ─────────────────────────────────────────────────────────────────────────────
# 冲突对比与裁决（2026-10-27 客户点名）：rule_relation.conflicts_with 下钻。
# 数据早就在库里——note JSON 里连对方原文 their_text 都备好了，缺的只是
# 把两条口径并排摆出来、差异高亮、拍板留痕的这一层。
# ─────────────────────────────────────────────────────────────────────────────
SRC_CODES = ("规则表", "要点", "制度", "场景表", "配置器", "解答")


def _rule_brief(rule_id: str) -> dict:
    """对比抽屉一侧的规则要素。**八要素 + 可执行性**，够拍板用。"""
    r = db.query_one("""SELECT rule_id, name, dimension, risk_class, rule_class,
                               control_type, source_clause, scope, logic, exception,
                               priority, owner, effective_from, effective_to,
                               compiled, demo_executable
                        FROM rule WHERE rule_id=%s AND version=1""", (rule_id,))
    if not r:
        return None
    r["rule_name"] = r.pop("name")          # name 会被脱敏当人名打星，出口改键
    for k in ("effective_from", "effective_to"):
        if r.get(k):
            r[k] = str(r[k])
    return r


def _find_clause(code: str, clause_no: str) -> dict:
    """类别代号（制度/要点/解答…）+ 条款号 → 反查 doclib_clause 全文。

    代号→文件沿用 onto_coord.code_targets 的口径（文件名含代号且唯一命中），
    唯一命中才算钉上；对不上就如实回 None——猜错文件会把证据链挂错地方。
    """
    hits = [f["file_name"] for f in
            db.query("SELECT file_name FROM doc_lib_file WHERE NOT missing")
            if code in f["file_name"]]
    if len(hits) != 1:
        return None
    c = db.query_one("""SELECT c.clause_id, c.clause_no, c.body, c.title,
                               c.text_source AS confidence, f.file_name, f.doc_title
                        FROM doclib_clause c
                        JOIN doc_lib_file f ON f.file_id = c.file_id
                        WHERE f.file_name = %s AND c.clause_no = %s""",
                     (hits[0], clause_no))
    return c


def _parse_counterpart(other_rule: str):
    """把 other_rule 拆成 [(代号, 条款号)]。「制度 第十条 / 要点 2.1.20」两段。"""
    out = []
    for seg in (other_rule or "").split("/"):
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split(" ", 1)
        if len(parts) == 2 and parts[0] in SRC_CODES:
            out.append((parts[0], parts[1].strip()))
    return out


def _clause_nums(clause: dict) -> list:
    """条款里的数值（带单位），供前端在原文上打 <mark>。按单位分桶防误报——
    直接把所有数字都标出来，「150%」和「150元」会互相误伤。"""
    import re
    unit_re = re.compile(r"(\d+(?:\.\d+)?)\s*(元|天|日|人|次|公里|级|%|％)")
    body = (clause or {}).get("body") or ""
    return [{"value": m.group(1), "unit": m.group(2), "at": m.start()}
            for m in unit_re.finditer(body)]


@router.get("/rules/{rule_id}/relations/compare")
def relation_compare(rule_id: str, other: str = Query(...)):
    """组装冲突对比抽屉的数据：左侧=本条规则（+来源条款原文），右侧=对方。

    对方两种形态：库内规则（other 是 TR-Dxx-xx）或「制度 第十条」类出处节点
    （note.with 的原样字符串）。后者按 代号+条款号 反查条款全文，
    反查不到（文件没切条款/不在可比对集）就如实标——note 里备份的
    their_text 仍然给出来，证据链不断。
    """
    rel = db.query_one("""SELECT rel_type, other_rule, note, needs_confirm, gap_id
                          FROM rule_relation
                          WHERE rule_id=%s AND rel_type='conflicts_with' AND version=1""",
                       (rule_id,))
    if not rel:
        raise HTTPException(404, "这条规则没有冲突关系：%s" % rule_id)
    note = {}
    try:
        note = json.loads(rel["note"]) if rel["note"] else {}
    except (ValueError, TypeError):
        note = {}
    # 对方定位串优先用调用方给的 other（列表点进来的是 relations 行的原值）
    other_str = other or rel["other_rule"] or (note.get("with") or "")

    left = _rule_brief(rule_id)
    left["source_clause_full"] = None
    if left and (left.get("source_clause") or {}).get("file"):
        c = _find_clause(left["source_clause"]["file"],
                         left["source_clause"].get("clause") or "")
        if c:
            left["source_clause_full"] = {**c, "nums": _clause_nums(c)}
        else:
            # 条款号对不上（规则清单的来源写法与切分器的条款号体系不一致）时，
            # source_clause.text 仍是装载时备份的原文——**证据链不断，只是少了
            # 反查到的全文字段**。展示面拿 text 兜底，不装作没有出处。
            txt = left["source_clause"].get("text") or ""
            left["source_clause_full"] = {
                "clause_no": left["source_clause"].get("clause") or "",
                "body": txt, "text_source": left["source_clause"].get("confidence") or "",
                "file_name": left["source_clause"].get("file") or "",
                "nums": _clause_nums({"body": txt}),
                "fallback_text": True,
            }

    right = {"kind": "unknown", "label": other_str}
    upper = (other_str or "").strip()
    if upper.startswith("TR-"):
        r = _rule_brief(upper)
        if r:
            right = {"kind": "rule", "label": upper, "rule": r}
            if (r.get("source_clause") or {}).get("file"):
                c = _find_clause(r["source_clause"]["file"],
                                 r["source_clause"].get("clause") or "")
                if c:
                    right["rule"]["source_clause_full"] = {**c, "nums": _clause_nums(c)}
    else:
        segs = _parse_counterpart(other_str)
        sources = []
        for code, no in segs:
            c = _find_clause(code, no)
            if c:
                sources.append({
                    "code": code, "clause_no": no, "found": True,
                    "clause": {**c, "nums": _clause_nums(c)},
                })
            else:
                # 反查不到时给 note 里备份的 their_text（单段出处时即对方全文）；
                # 没有备份就如实标「原文未入库」——不猜文件、不编原文。
                body = (note.get("their_text") or "") if len(segs) == 1 else ""
                sources.append({
                    "code": code, "clause_no": no, "found": False,
                    "clause": ({"clause_no": no, "body": body,
                                "text_source": "", "file_name": code,
                                "nums": _clause_nums({"body": body}),
                                "fallback_text": True} if body else None),
                })
        right = {"kind": "clause", "label": other_str, "sources": sources}

    return {
        "rule_id": rule_id,
        "relation": {"rel_type": rel["rel_type"], "other_rule": rel["other_rule"],
                     "needs_confirm": rel["needs_confirm"], "gap_id": rel["gap_id"]},
        "note": note,
        "left": left,
        "right": right,
    }


REL_DECISIONS = ("left", "right", "merge")


@router.post("/rules/{rule_id}/relations/resolve")
def relation_resolve(rule_id: str, request: Request, body: dict = None):
    """裁决一条冲突关系（2026-10-27 客户点名功能）。

    decision=left|right|merge；结论必填——三个月后看这条规则的人只看得到这段话。
    落库：note 追加 resolved 段（原 note 一字不动，天然留痕）＋
    needs_confirm 置 false；动作进 sys_op_log（category=rule）。
    merge 不在这里改规则——改口径必须走修订+发布，这里只记裁决意图。
    """
    body = body or {}
    decision = body.get("decision")
    if decision not in REL_DECISIONS:
        raise HTTPException(400, "decision 只能是 left/right/merge")
    resolution = (body.get("resolution") or "").strip()
    if not resolution:
        raise HTTPException(400, "裁决必须写结论——谁拍的板、拍成什么，"
                                 "在制度冲突上和结论本身一样重要")
    by = (body.get("by") or "").strip()
    if not by:
        raise HTTPException(400, "缺少裁决人署名")
    rel = db.query_one("""SELECT id, note, other_rule FROM rule_relation
                          WHERE rule_id=%s AND rel_type='conflicts_with' AND version=1""",
                       (rule_id,))
    if not rel:
        raise HTTPException(404, "这条规则没有冲突关系：%s" % rule_id)
    note = {}
    try:
        note = json.loads(rel["note"]) if rel["note"] else {}
    except (ValueError, TypeError):
        note = {}
    import datetime
    note["resolved"] = {"decision": decision, "resolution": resolution,
                        "resolved_by": by,
                        "resolved_at": datetime.datetime.now().isoformat(timespec="seconds")}
    n = db.execute("""UPDATE rule_relation SET needs_confirm=false, note=%s
                      WHERE id=%s""", (json.dumps(note, ensure_ascii=False), rel["id"]))
    if not n:
        raise HTTPException(404, "冲突关系不存在或已被并发裁决")
    op_log.log_by_request(
        request, "rule", "裁决规则冲突",
        target="%s vs %s" % (rule_id, rel["other_rule"] or ""),
        detail="裁决=%s · 结论=%s · 裁决人=%s" % (
            {"left": "保留本条规则", "right": "保留对方口径",
             "merge": "人工合并(需经发布生效)"}[decision], resolution[:200], by))
    return {"rule_id": rule_id, "decision": decision, "ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 异议收件箱（B-P0-1，2026-10-27）：审核岗「不认可这条命中」回流的消费侧。
#
# 回流侧早就在写（review.py manual_review → feedback_log，metric=
# finding_vs_human / deviation_kind=false_positive / source=human_review），
# 但规则管理员侧零呈现——审核岗提了异议没人收，客户 #2 的
# 「意见回流至规则库侧汇总处理」断在中间。这里补消费侧：
#   GET  /api/asset/feedbacks                  按规则聚合（页签计数用 pending）
#   GET  /api/asset/feedbacks/{rule_id}        一条规则的异议明细（抽屉数据源）
#   POST /api/asset/feedbacks/{fid}/resolve    处置（keep 维持 / revise 确认为规则问题）
#
# 与领导 Deviation 页同源双视图：共用 feedback_log 与同一聚合口径
# （/api/deviation 的 false_positive_by_rule 与本接口的 n 是同一条 WHERE），
# 不另起一套算法。处置动作（keep/revise）以 sys_op_log 为准——
# op target 用精确键 feedback:{id}，聚合态从这里反查，不做字符串猜测。
# ─────────────────────────────────────────────────────────────────────────────
_FB_WHERE = """f.metric = 'finding_vs_human'
   AND f.deviation_kind = 'false_positive'
   AND f.source = 'human_review'
   AND f.actual->>'rule_id' IS NOT NULL"""


def _fb_decisions(fids: list) -> dict:
    """异议 id → 处置动作（keep/revise）。处置动作的真身是 sys_op_log
    （target=feedback:{id} 精确键）；resolved 三列只说「处置过了」，
    「处置成什么」要看台账——台账只 INSERT 不删改，比任何状态列都可信。"""
    out = {}
    if not fids:
        return out
    for o in db.query(
            """SELECT target, detail FROM sys_op_log
               WHERE category='rule' AND action='处置异议'
                 AND target = ANY(%s) ORDER BY id""",
            (["feedback:%s" % i for i in fids],)):
        try:
            fid = int(o["target"].split(":", 1)[1])
        except (IndexError, ValueError):
            continue
        out[fid] = "revise" if "确认为规则问题" in (o["detail"] or "") else "keep"
    return out


@router.get("/feedbacks")
def feedback_inbox(state: str = None, dimension: str = None):
    """异议聚合列表（规则库「异议」页签数据源）。

    state：pending 待处置 / keep 已维持 / revise 已修订 / all 全部（默认）。
    排序：待处置优先、异议次数降序、最近时间倒序。聚合行不查明细——
    明细点开抽屉再取（一页签一次聚合查询，量级 = 有异议的规则数）。
    """
    if state not in (None, "", "all", "pending", "keep", "revise"):
        raise HTTPException(400, "state 只能是 pending / keep / revise / all")
    where, params = _FB_WHERE, []
    if dimension:
        where += " AND r.dimension = %s"
        params.append(dimension)
    rows = db.query("""
        SELECT f.actual->>'rule_id' AS rule_id,
               max(r.name)          AS rule_name,
               max(r.dimension)     AS dimension,
               count(*)                                        AS n,
               count(*) FILTER (WHERE f.resolved_by IS NULL)   AS pending_n,
               count(*) FILTER (WHERE f.actual->>'note' IS NOT NULL) AS note_n,
               max(f.observed_at)   AS last_at,
               array_agg(f.id ORDER BY f.observed_at DESC)     AS fids
        FROM feedback_log f
        LEFT JOIN rule r ON r.rule_id = f.actual->>'rule_id' AND r.version = 1
        WHERE """ + where + """
        GROUP BY 1
        ORDER BY (count(*) FILTER (WHERE f.resolved_by IS NULL)) DESC, count(*) DESC,
                 max(f.observed_at) DESC""", tuple(params))
    decisions = _fb_decisions([i for r in rows for i in (r["fids"] or [])])
    items = []
    for r in rows:
        fids = r["fids"] or []
        if (r["pending_n"] or 0) > 0:
            st = "pending"
        else:
            acts = [decisions.get(i) for i in fids]
            st = "revise" if acts and all(a == "revise" for a in acts) else "keep"
        items.append({
            "rule_id": r["rule_id"], "rule_name": r["rule_name"],
            "dimension": r["dimension"], "n": r["n"], "pending_n": r["pending_n"],
            "note_n": r["note_n"],
            "last_at": r["last_at"].strftime("%Y-%m-%d %H:%M") if r["last_at"] else None,
            "state": st,
        })
    if state in ("pending", "keep", "revise"):
        items = [i for i in items if i["state"] == state]
    return {"items": items, "total": len(items),
            "pending": sum(1 for i in items if i["state"] == "pending"),
            "note": "按规则聚合审核岗勾「不认可这条命中」的回流记录；"
                    "明细与处置动作点开规则行可见。处置留痕进操作日志（规则类）。"}


@router.get("/feedbacks/{rule_id}")
def feedback_detail(rule_id: str):
    """一条规则的异议明细（聚合抽屉数据源）。每条 = 单据链接 / 审核员 / 时间 /
    当时判定 message 原文（标注可下钻）/ 逐条意见原话（A-P0-1 的 note，
    有则显示无则不渲染，历史行天然兼容）/ 该条的处置状态与说明。"""
    rows = db.query("""
        SELECT f.id, f.subject_id AS bill_no, f.observed_at,
               f.actual->>'run_id' AS run_id,
               f.actual->>'note'   AS note,
               f.resolved_by, f.resolved_note, f.resolved_at
        FROM feedback_log f
        WHERE """ + _FB_WHERE + " AND f.actual->>'rule_id' = %s ORDER BY f.observed_at DESC",
        (rule_id,))
    decisions = _fb_decisions([x["id"] for x in rows])
    items = []
    for x in rows:
        # 当时判定的 message 原文从该轮 review_finding 取——收件箱点开要看到
        # 「当时 AI 说了什么」；提出异议的审核员取该轮 manual_review 署名
        # （出口叫 user_label：reviewer 字段名会被 M01 打星，署名不许打星）。
        msg, reviewer = None, None
        if x["run_id"]:
            f0 = db.query_one(
                "SELECT message FROM review_finding WHERE run_id=%s AND rule_id=%s "
                "AND outcome='hit' LIMIT 1", (x["run_id"], rule_id))
            msg = f0["message"] if f0 else None
            m0 = db.query_one(
                "SELECT reviewer AS user_label FROM manual_review WHERE run_id=%s "
                "ORDER BY decided_at DESC LIMIT 1", (x["run_id"],))
            reviewer = m0["user_label"] if m0 else None
        items.append({
            "id": x["id"], "bill_no": x["bill_no"], "run_id": x["run_id"],
            "at": x["observed_at"].strftime("%Y-%m-%d %H:%M") if x["observed_at"] else None,
            "note": x["note"], "finding_message": msg, "user_label": reviewer,
            "decision": decisions.get(x["id"]),
            "resolved_by": x["resolved_by"], "resolved_note": x["resolved_note"],
            "resolved_at": x["resolved_at"].strftime("%Y-%m-%d %H:%M")
            if x["resolved_at"] else None,
        })
    r = db.query_one("""SELECT rule_id, name AS rule_name, dimension, risk_class,
                               rule_class, control_type, scope, logic, exception,
                               priority, owner, compiled, demo_executable
                        FROM rule WHERE rule_id=%s AND version=1""", (rule_id,))
    if r:
        for k in ("scope", "logic"):
            if r.get(k) and not isinstance(r[k], str):
                r[k] = json.dumps(r[k], ensure_ascii=False)
    return {"rule_id": rule_id, "rule": r, "items": items, "total": len(items),
            "note": "异议明细含审核岗逐条意见原话（勾「不认可」旁的选填输入）。"}


RESOLVE_DECISIONS = ("keep", "revise")


@router.post("/feedbacks/{fid}/resolve")
def feedback_resolve(fid: int, request: Request, body: dict = None):
    """处置一条异议（B-P0-1）：keep=维持规则 / revise=确认为规则问题。

    keep：note 必填——「为什么维持」提出异议的审核员看得到这段话。
    revise：note 选填（修订去向摘要）；实际修订走规则修订流程（B-P1-E），
    这里只记处置意图并返回预填素材，不改规则口径。
    落库：feedback_log 处置三列（迁移 0048，只 UPDATE 这三列不动存量字段）+
    sys_op_log（category=rule，target=feedback:{id} 精确键——聚合态反查用）。
    角色闸：规则管理员的动作（engineer/admin），审核岗不许自处置自己的异议。
    """
    from ..iam_auth import current_user
    u = current_user(request)
    if u["role_key"] not in ("engineer", "admin"):
        raise HTTPException(403, "异议处置是规则管理员的动作（当前角色：%s）"
                            % u.get("role_name", u["role_key"]))
    body = body or {}
    decision = body.get("decision")
    if decision not in RESOLVE_DECISIONS:
        raise HTTPException(400, "decision 只能是 keep（维持规则）/ revise（确认为规则问题）")
    note = str(body.get("note") or "").strip()
    if decision == "keep" and not note:
        raise HTTPException(400, "维持规则必须填写说明——提出异议的审核员看得到这段话")
    row = db.query_one("""SELECT id, subject_id, actual, resolved_by
                          FROM feedback_log WHERE id=%s AND metric='finding_vs_human'
                            AND deviation_kind='false_positive'
                            AND source='human_review'""", (fid,))
    if not row:
        raise HTTPException(404, "异议记录 %d 不存在（或不是审核岗回流的不认可异议）" % fid)
    if row["resolved_by"]:
        raise HTTPException(409, "该异议已由 %s 处置过，不重复处置；留痕见操作日志"
                            % row["resolved_by"])
    n = db.execute("""UPDATE feedback_log SET resolved_by=%s, resolved_note=%s,
                      resolved_at=now() WHERE id=%s AND resolved_by IS NULL""",
                   (u["account"], note or None, fid))
    if not n:
        raise HTTPException(409, "该异议刚被并发处置，刷新后查看")
    rid = (row["actual"] or {}).get("rule_id") or ""
    op_log.log_by_request(
        request, "rule", "处置异议", target="feedback:%d" % fid,
        detail="%s · 规则 %s · 单据 %s%s" % (
            {"keep": "维持规则", "revise": "确认为规则问题，转规则修订"}[decision],
            rid or "—", row["subject_id"] or "—",
            " · 说明：%s" % note[:120] if note else ""))
    prefill = None
    if decision == "revise":
        recent = db.query_one(
            """SELECT actual->>'note' AS note FROM feedback_log
               WHERE metric='finding_vs_human' AND deviation_kind='false_positive'
                 AND source='human_review' AND actual->>'rule_id'=%s
                 ORDER BY observed_at DESC LIMIT 1""", (rid,))
        cnt = db.scalar(
            """SELECT count(*) FROM feedback_log
               WHERE metric='finding_vs_human' AND deviation_kind='false_positive'
                 AND source='human_review' AND actual->>'rule_id'=%s""", (rid,))
        prefill = "审核岗异议 %d 条，最近一条：%s" % (
            cnt or 0, ((recent or {}).get("note") or "未附文字意见")[:60])
    return {"ok": True, "id": fid, "decision": decision, "rule_id": rid,
            "prefill": prefill,
            "note": "处置已留痕（操作日志·规则类）。%s" % (
                "修订理由预填已生成，转规则修订流程发起修订。" if decision == "revise"
                else "维持说明对提出异议的审核员可见。")}


@router.get("/lookup-tables")
def list_lookup_tables():
    return {"items": db.query(
        """SELECT table_id, version, kind, source_file, source_rows, row_count,
                  key_fields, fallback_key, on_miss, note, loaded_at
           FROM lookup_table ORDER BY table_id""")}


@router.get("/lookup-tables/{table_id}")
def get_lookup_table(table_id: str):
    t = must_exist(db.query_one("SELECT * FROM lookup_table WHERE table_id=%s", (table_id,)),
                   "查找表 " + table_id)
    t["rows"] = db.query("""SELECT id, row_key, row_value, enabled, confidence,
                            source_rows, must_hit, must_not_hit, conflict, gap_id, note
                            FROM lookup_row WHERE table_id=%s ORDER BY id""", (table_id,))
    # 对账：row_count 合计是否等于源表行数
    if t["source_rows"]:
        s = db.scalar("SELECT coalesce(sum(source_rows),0) FROM lookup_row WHERE table_id=%s",
                      (table_id,))
        t["reconciliation"] = {"sum_source_rows": s, "declared": t["source_rows"],
                               "ok": s == t["source_rows"]}
    return t


@router.get("/ontology")
def ontology():
    v = db.query_one("SELECT version, source_hash, loaded_at, stats FROM ontology_version "
                     "WHERE is_current")
    if not v:
        raise HTTPException(404, "库里没有当前本体版本")
    # e.name AS entity_name：实测 HTTP 出来是「报**」「费***」——
    # 本体元素名不是人名，但字段名一样就走同一条策略
    v["entities"] = db.query("""SELECT e.entity_id, e.name AS entity_name, e.source,
                                 count(c.concept_id) AS concepts
                                FROM ontology_entity e
                                LEFT JOIN ontology_concept c
                                  ON c.entity_id=e.entity_id AND c.version=e.version
                                WHERE e.version=%s GROUP BY 1,2,3 ORDER BY 1""",
                             (v["version"],))
    v["availability"] = db.query("""SELECT availability, count(*) n FROM ontology_concept
                                    WHERE version=%s GROUP BY 1 ORDER BY 2 DESC""",
                                 (v["version"],))
    v["enums"] = db.query("""SELECT e.enum_id, e.name AS enum_name,
                                    count(ev.value) AS values
                             FROM ontology_enum e
                             LEFT JOIN ontology_enum_value ev
                               ON ev.enum_id=e.enum_id AND ev.version=e.version
                             WHERE e.version=%s GROUP BY 1,2 ORDER BY 1""", (v["version"],))
    return v


# ─────────────────────────────────────────────────────────────────────────────
# 栏目四 · 流程本体
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/process")
def process():
    v = db.scalar("SELECT version FROM ontology_version WHERE is_current")
    # node_name：这一页的节点链一直显示的是「提*」「部******」
    nodes = db.query("""SELECT node_id, name AS node_name, is_bypass, occurrences, note
                        FROM process_node WHERE version=%s ORDER BY node_id""", (v,))
    trans = db.query("""SELECT from_node, to_node, cond, blocking, note
                        FROM process_transition WHERE version=%s ORDER BY from_node, to_node""",
                     (v,))
    # 实测的节点出现次数——流程图上要显示，让人看出哪条路是主路
    measured = db.query("""SELECT node, count(*) n FROM approval_record
                           GROUP BY node ORDER BY n DESC""")
    return {
        "version": v, "nodes": nodes, "transitions": trans,
        "measured_from_real_approvals": measured,
        "note": "节点与迁移全部数自 50 页真实审批记录，不是设计出来的。"
                "N04 AI审核 → N05 共享审核 的 blocking=false 是实测结论："
                "FJPG-BX2610130821 在 2026-10-13 17:25:03 得到「AI审核不通过」，"
                "17:35:41 仍进入共享审核并由人工给出退回理由。AI 结论是参考，闸门在人工手里。",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 栏目五 · 发布管理
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/releases")
def list_releases():
    rows = db.query("""SELECT r.*, (c.release_id IS NOT NULL) AS is_current
                       FROM release r
                       LEFT JOIN release_current c ON c.release_id=r.release_id
                       ORDER BY r.created_at DESC""")
    return {"items": rows,
            "policy": {"requires_signature": True,
                       "why": "规则集一变，所有在跑的单据的判定口径就跟着变。"
                              "让抽取结果自动发布，等于让模型直接改生产口径。"
                              "reviewed_by 为空的 release 不允许进 published"
                              "（库里有 CHECK 约束 ck_release_signed 钉着）。"}}


@router.get("/releases/{release_id}")
def get_release(release_id: str):
    r = must_exist(db.query_one("SELECT * FROM release WHERE release_id=%s", (release_id,)),
                   "发布 " + release_id)
    r["items"] = db.query("""SELECT i.rule_id, i.version, i.change,
                                    ru.name AS rule_name, ru.control_type
                             FROM release_item i
                             LEFT JOIN rule ru ON ru.rule_id=i.rule_id AND ru.version=i.version
                             WHERE i.release_id=%s ORDER BY i.change, i.rule_id""",
                          (release_id,))
    return r


# ─────────────────────────────────────────────────────────────────────────────
# 大模型配置：已移到「系统」组
# ---------------------------------------------------------------------------
# 实现在 app/api/sys_llm.py，路径是 /api/sys/llm-*。
# 这里留两条 307 跳转，不是为了好看——
# 已经发出去的链接、写进文档的 curl、别人存的书签，不该因为菜单换了归类就 404。
# 307 而不是 301/302：307 保留请求方法，POST 跳过去还是 POST。
#
# **逻辑一行都不留在这里。** 留一份「简化版」实现是最坏的做法：
# 两边口径会漂移，而漂移的那一天没人知道前端调的是哪一份。
# 原先这里存 Key 用的是 sha256（不可逆），新实现改成了 Fernet 可逆加密——
# 两份并存就等于两种存储格式并存，测试连通时取不回明文的那条永远测不通。
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/llm-configs", include_in_schema=False)
def list_llm_moved():
    return RedirectResponse("/api/sys/llm-configs", status_code=307)


@router.post("/llm-configs", include_in_schema=False)
def upsert_llm_moved():
    return RedirectResponse("/api/sys/llm-configs", status_code=307)
