# -*- coding: utf-8 -*-
"""栏目五 发布管理的**管理面**接口。

页面四段（2026-10-19 用户口径：文件 → 规则 → 三元本体 以版本的形式资产化，
并允许被其他业务消费）：
  ① 工作区  现在能发什么：八层现状、与当前生效版本的差异、能不能冻结
  ② 版本    冻结 → 送审 → 签字发布 → 切为当前 / 回滚；每一版的八层快照
  ③ 消费    对外接口清单 + 资产包 + 消费台账（见 app/api/pub.py）
  ④ 存储    哪张表是资产、哪张表是工作区

**管理面与消费面分两个 router**，不是为了好看：
消费面 /api/pub/v1 是给别的系统用的，它只读、只给已发布版本、路径带 v1 承诺兼容；
管理面 /api/publish 会改状态、能看草稿。混在一个前缀下，日后想给消费面
加鉴权或限流就得逐个接口挑，挑错一个就是漏。
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from .. import db, release_pack as rp
from ..iam_auth import current_user
from . import op_log

router = APIRouter(prefix="/api/publish", tags=["发布管理"])


def require_engineer(request: Request) -> dict:
    """发布口角色闸（2026-10-26 IA 重排）：冻结/送审/发布/回滚是规则管理员的职责。
    GET 不拦（领导与审计只读场景要看版本列表）。"""
    u = current_user(request)
    if u["role_key"] not in ("engineer", "admin"):
        raise HTTPException(403, "该操作仅限规则管理员（当前角色：%s）"
                            % u.get("role_name", u["role_key"]))
    return u


def _err(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except rp.PackError as e:
        raise HTTPException(400, str(e))


# ── ① 工作区 ────────────────────────────────────────────────────────────────
@router.get("/workspace")
def workspace():
    """现在能发什么。**每条闸都带「怎么过」**，不然页面上只有一个红叉。"""
    return _err(rp.workspace)


# ── 规则试算（B-P1-F，2026-10-28）：改完规则未发布前，对历史单据只读回放 ──
# 「这版规则比现版会多命中/少命中哪些单」。SAP simulation run / 用友规则
# 试算的 POC 形态：双规则集对同一批单据各跑一遍 engine.evaluate（纯内存
# 判定，不调模型不出网不落任何业务表），输出逐单差异 + 按规则 ±命中表。
#
# 红线核查：evaluate 是纯函数（D 阶段不碰库），只读回放属薄 API 范畴；
# snapshot_fact / standards / tier_map 同为只读取数。写 op_log 一条
# （谁在发布前看了试算——签字发布的配套证据），不写业务表。
@router.post("/impact-preview")
def impact_preview(request: Request, body: dict = Body(None),
                   _g=Depends(require_engineer)):
    """发布前试算：当前生效规则集 vs 工作区规则集，对历史单据只读回放。

    body={scope: 'all' | 'real' | 'demo'（默认 all）， limit: 默认全量}
    输出：汇总条（结论翻转 N 张、变严 M 张、变宽 K 张、命中不变）/ 按规则
    ±命中表 / 逐单差异清单（单号可跳报告页、旧结论、新结论、差异规则）。
    """
    import time as _time
    from ..perception import reader
    from ..rules import engine

    b = body or {}
    scope = b.get("scope") or "all"
    if scope not in ("all", "real", "demo"):
        raise HTTPException(400, "scope 只能是 all / real / demo")
    limit = int(b.get("limit") or 0)

    where, params = "", []
    if scope == "real":
        where = " WHERE is_demo = FALSE"
    elif scope == "demo":
        where = " WHERE is_demo = TRUE"
    bill_nos = [r["bill_no"] for r in db.query(
        "SELECT bill_no FROM expense_bill%s ORDER BY bill_no" % where, tuple(params))]
    if limit > 0:
        bill_nos = bill_nos[:limit]
    if not bill_nos:
        raise HTTPException(400, "没有可回放的单据（scope=%s）" % scope)

    # 两套规则集：生效版走 engine.load_ruleset（effective_release 阶梯）；
    # 工作区版 = 生效版执行集（v1 行，demo_executable 口径与引擎一致）叠加
    # **未发布的修订**（B-P1-E 产生的 v2+ 行改判定口径，改的可编译性不影响
    # v1 已在执行集这个事实——修订行的 demo_executable=False 只是「未发布」
    # 标记，试算回答的正是「发布这版修订会怎样」）。没有修订时两版同集，
    # 提前返回零差异。
    t0 = _time.time()
    eff_ruleset = engine.load_ruleset()
    std, tm = reader.standards(), reader.tier_map()

    ws_exec = [dict(r) for r in eff_ruleset.rules]
    by_id = {r["rule_id"]: r for r in ws_exec}
    revised = db.query("""SELECT rule_id, version, name, control_type, priority, scope,
                                 logic, exception
                          FROM rule WHERE version > 1 AND revised_by IS NOT NULL
                          ORDER BY rule_id, version""")
    _JUDGE_COLS = ("name", "control_type", "priority", "scope", "logic", "exception")
    n_applied = 0
    for rv in revised:
        tgt = by_id.get(rv["rule_id"])
        if not tgt:
            continue                      # 修订的规则不在执行集（本就不跑），跳过
        for k in _JUDGE_COLS:
            if rv[k] is not None:
                tgt[k] = rv[k]
        n_applied += 1
    if not n_applied:
        return {"scope": scope, "bills": len(bill_nos), "same_set": True,
                "flips": [], "by_rule": [], "summary": {
                    "changed": 0, "stricter": 0, "looser": 0, "unchanged": len(bill_nos)},
                "elapsed_ms": int((_time.time() - t0) * 1000),
                "note": "工作区规则集与生效版口径一致（没有未发布的修订），"
                        "回放必然零差异。先修订一条规则，或装载新规则后再试算。"}
    ws_ruleset = engine.RuleSet(ws_exec, "工作区(试算)", eff_ruleset.ontology_version,
                                eff_ruleset.lookups, eff_ruleset.excluded,
                                release_id=None, effective_why="试算：工作区当前规则")

    CONCL_RANK = {"pass": 0, "review": 1, "reject": 2}
    by_rule: dict = {}
    flips = []
    n_changed = n_strict = n_loose = 0
    for no in bill_nos:
        fact, _meta = reader.snapshot_fact(no)
        if fact is None:
            continue
        f_a, _s = engine.evaluate(eff_ruleset, fact, std, tm)
        f_b, _s2 = engine.evaluate(ws_ruleset, fact, std, tm)
        hit_a = {x.rule_id for x in f_a if x.outcome == "hit"}
        hit_b = {x.rule_id for x in f_b if x.outcome == "hit"}
        c_a, c_b = engine.conclude(f_a, eff_ruleset), engine.conclude(f_b, ws_ruleset)
        delta = hit_b - hit_a
        gone = hit_a - hit_b
        if not delta and not gone and c_a == c_b:
            continue
        n_changed += 1
        direction = ("stricter" if CONCL_RANK[c_b] > CONCL_RANK[c_a]
                     else "looser" if CONCL_RANK[c_b] < CONCL_RANK[c_a] else "flat")
        if direction == "stricter":
            n_strict += 1
        elif direction == "looser":
            n_loose += 1
        for rid in delta:
            by_rule.setdefault(rid, {"rule_id": rid, "only_a": 0, "only_b": 0})
            by_rule[rid]["only_b"] += 1
        for rid in gone:
            by_rule.setdefault(rid, {"rule_id": rid, "only_a": 0, "only_b": 0})
            by_rule[rid]["only_a"] += 1
        # 逐单差异行的规则明细：新增命中（含 message）/ 消失命中
        detail = []
        rule_names = {rid: (r.get("name") or "") for rid, r in by_id.items()}
        for x in f_b:
            if x.rule_id in delta:
                detail.append({"rule_id": x.rule_id, "rule_name": rule_names.get(x.rule_id),
                               "change": "new_hit", "message": x.message})
        for x in f_a:
            if x.rule_id in gone:
                detail.append({"rule_id": x.rule_id, "rule_name": rule_names.get(x.rule_id),
                               "change": "gone_hit", "message": x.message})
        run = db.query_one(
            "SELECT run_id FROM review_run WHERE bill_no=%s ORDER BY started_at DESC LIMIT 1",
            (no,))
        flips.append({"bill_no": no, "run_id": run["run_id"] if run else None,
                      "old_conclusion": c_a, "new_conclusion": c_b,
                      "direction": direction,
                      "new_hits": sorted(delta), "gone_hits": sorted(gone),
                      "detail": detail})
    flips.sort(key=lambda x: ({"stricter": 0, "looser": 1, "flat": 2}[x["direction"]],
                              x["bill_no"]))
    by_rule_out = sorted(by_rule.values(),
                         key=lambda x: (-abs(x["only_b"] - x["only_a"]), x["rule_id"]))
    for x in by_rule_out:
        x["rule_name"] = by_id.get(x["rule_id"], {}).get("name")
    op_log.log_by_request(
        request, "publish", "试算差异", target="impact-preview",
        detail="回放 %d 张（%s）：结论翻转 %d（变严 %d / 变宽 %d）· 涉及规则 %d 条 · 耗时 %dms"
               % (len(bill_nos), {"all": "全部", "real": "仅真实单", "demo": "仅演示单"}[scope],
                  n_changed, n_strict, n_loose, len(by_rule_out),
                  int((_time.time() - t0) * 1000)))
    return {"scope": scope, "bills": len(bill_nos), "same_set": False,
            "eff_version": eff_ruleset.rule_set_version,
            "ws_rules": len(ws_exec), "eff_rules": len(eff_ruleset.rules),
            "flips": flips, "by_rule": by_rule_out,
            "summary": {"changed": n_changed, "stricter": n_strict,
                        "looser": n_loose, "unchanged": len(bill_nos) - n_changed},
            "elapsed_ms": int((_time.time() - t0) * 1000),
            "note": "基于历史单据回放，不代表未来单据分布；回放只读不落库、"
                    "不覆盖任何现有结论。变严=结论向不通过方向翻转，"
                    "变宽=向通过方向翻转，flat=命中集变化但结论档不变。"}


# ── ② 版本 ──────────────────────────────────────────────────────────────────
@router.get("/releases")
def releases():
    return rp.list_releases()


@router.get("/releases/{release_id}")
def release(release_id: str):
    return _err(rp.get_release, release_id)


@router.get("/releases/{release_id}/asset/{layer}")
def asset(release_id: str, layer: str):
    """看某一版某一层的**快照原文**。管理面可以看草稿，消费面不行。"""
    return _err(rp.asset_payload, release_id, layer)


@router.post("/freeze")
def freeze(request: Request, body: dict = Body(...), _g=Depends(require_engineer)):
    """把工作区的八层**原样抄一份**存进库，建成草稿。

    抄完之后改 YAML、重装规则、文件库重扫，都动不了这一份。
    """
    out = _err(rp.freeze, body.get("version"), body.get("by"), body.get("note"))
    op_log.log_by_request(request, "publish", "冻结新版本",
                          target=str(body.get("version") or ""),
                          detail=f"冻结人 {body.get('by') or ''}")
    return out


@router.post("/releases/{release_id}/review")
def review(release_id: str, request: Request, body: dict = Body(...),
           _g=Depends(require_engineer)):
    out = _err(rp.review, release_id, body.get("by"))
    op_log.log_by_request(request, "publish", "送审版本", target=_ver_of(release_id),
                          detail=f"送审人 {body.get('by') or ''}")
    return out


@router.post("/releases/{release_id}/publish")
def publish(release_id: str, request: Request, body: dict = Body(...), _g=Depends(require_engineer)):
    """签字发布。**不自动切为当前**——发布和生效是两件事。"""
    out = _err(rp.publish, release_id, body.get("by"))
    op_log.log_by_request(request, "publish", "签字发布", target=_ver_of(release_id),
                          detail=f"签字人 {body.get('by') or ''}")
    return out


@router.post("/releases/{release_id}/current")
def switch_current(release_id: str, request: Request, body: dict = Body(...),
                   _g=Depends(require_engineer)):
    out = _err(rp.switch_current, release_id, body.get("by"))
    op_log.log_by_request(request, "publish", "切换生效版本", target=_ver_of(release_id),
                          detail=f"操作人 {body.get('by') or ''}")
    return out


@router.post("/releases/{release_id}/rollback")
def rollback(release_id: str, request: Request, body: dict = Body(...), _g=Depends(require_engineer)):
    out = _err(rp.rollback, release_id, body.get("to"), body.get("by"), body.get("reason"))
    op_log.log_by_request(request, "publish", "回滚版本", target=_ver_of(release_id),
                          detail="回滚至 %s · 原因 %s · 操作人 %s"
                          % (_ver_of(body.get("to")) if body.get("to") else "（无生效版本）",
                             body.get("reason") or "", body.get("by") or ""))
    return out


def _ver_of(release_id) -> str:
    """release_id → 版本号。台账上人认的是 rs-20261021-01，不是 rel-0007。"""
    if not release_id:
        return ""
    return db.scalar("SELECT rule_set_version FROM release WHERE release_id = %s",
                     (str(release_id),)) or str(release_id)


@router.post("/releases/{release_id}/discard")
def discard(release_id: str, request: Request, body: dict = Body(...),
            _g=Depends(require_engineer)):
    """删草稿。已发布的版本删不掉——那是资产。"""
    out = _err(rp.discard, release_id, body.get("by"))
    op_log.log_by_request(request, "publish", "删除草稿版本", target=_ver_of(release_id),
                          detail=f"操作人 {body.get('by') or ''}")
    return out


# ── ③ 消费台账 ──────────────────────────────────────────────────────────────
@router.get("/access-log")
def access_log(limit: int = Query(50, ge=1, le=300)):
    return {"items": rp.access_log(limit),
            "note": "「允许被其他业务消费」要在页面上看得见才算做到。"
                    "**只记元数据**：版本号、资源名、调用方自报的名字、字节数；"
                    "不记请求参数、不记响应内容、不记 IP。"}


# ── ④ 存储 ──────────────────────────────────────────────────────────────────
@router.get("/storage")
def storage():
    return rp.storage()
