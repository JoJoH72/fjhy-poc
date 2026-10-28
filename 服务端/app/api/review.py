# -*- coding: utf-8 -*-
"""报账单 AI 审核端 + 审计接口 + 调试接口。

后两个是**脱敏的旁路出口**，专门放在同一个文件里，提醒它们和审核报告一样
要过同一套脱敏规则。做安全的那个模块往往只守住主输出路径（报告正文），
而审计接口、调试查询这些根本不经过它。
"""
import os

from fastapi import APIRouter, Body, HTTPException, Query, Request

from .. import config, db
from ..audit import recorder as rec
from ..iam_auth import current_user
from . import op_log
from .common import demo_banner, must_exist, paginate

router = APIRouter(prefix="/api", tags=["报账单审核"])


# ─────────────────────────────────────────────────────────────────────────────
# 审核岗出口层文案映射（打磨规划 P0-7，2026-10-27）
#
# 引擎产出的 message / pending_reason 里带着内部标识（缺口编号 G-04、
# 规则号互引「比 TR-D6-03 严厉得多」）。这些是**规则管理员的语言**，
# 不是审核员的语言——审核岗不知道 G-04 是什么，也不知道为什么要在意
# TR-D6-06 比 TR-D6-03 严厉。**引擎一个字不动**（impl.py 的文案服务于
# 偏差归因与规则治理），只在审核出口把内部编号翻成白话：
#   · 「缺口 G-04」→「系统暂缺 OA 出差审批单数据，无法自动判定」
#   · 规则号互引 → 「上一条住宿/伙食天数待人工的原因相同」
# 管理员的规则详情页（/asset/rules/:id）不走这条出口，仍看原文编号。
# ─────────────────────────────────────────────────────────────────────────────
_GAP_TEXT = {
    "G-04": "系统暂缺 OA 出差审批单数据，无法自动判定",
}


def _plain_for_auditor(text: str) -> str:
    """出口层白话转换：缺口编号 → 人话；规则号互引 → 人话。只动展示文本。"""
    if not text:
        return text
    out = text
    for gid, plain in _GAP_TEXT.items():
        # 各种实测写法：「缺口 G-04」「（需 OA 同步，缺口 G-04）」「缺口 G-04）」
        out = out.replace("（需 OA 同步，缺口 %s）" % gid, "（%s）" % plain)
        out = out.replace("（出差地点需 OA 同步，缺口 %s）" % gid, "（%s）" % plain)
        out = out.replace("（缺口 %s）" % gid, "（%s）" % plain)
        out = out.replace("，尚未同步（缺口 %s）。" % gid, "，系统暂未接入，%s。" % _GAP_TEXT[gid])
    # 规则号互引（TR-D6-06 实测原文）：「比 TR-D6-03 严厉得多」对审核员无信息量
    out = out.replace("比 TR-D6-03 严厉得多，拿不准时更不能硬判",
                      "连带影响交通费、伙食、市内交通三项判定，需人工确认单次出差天数")
    return out


def _humanize_finding(f: dict) -> dict:
    """对一条 finding 的展示文本做出口转换（message / pending_reason）。"""
    if f.get("message"):
        f["message"] = _plain_for_auditor(f["message"])
    if f.get("pending_reason"):
        f["pending_reason"] = _plain_for_auditor(f["pending_reason"])
    _attach_hint(f)
    _attach_compare(f)
    return f


# ── 人工核点提示（P1-6，2026-10-27）───────────────────────────────────────────
# Concur 三段式：什么被违反 → 检测到什么 → **建议你核什么**。第三段是给
# 审核员的：35 张真实单平均 3.4 个待人工项，看到「判不了的原因」之后，
# 「那我该去看什么」没有行动指向。默认文案按规则号给（出口层兜底，规则
# logic 后续加 human_check_hint 字段后由规则管理员维护覆盖——本次不动规则
# 资产，只做出口映射，与 P0-7 的缺口脱敏同一层）。
_HINTS = {
    "TR-D1-02": "请打开住宿费发票核对：①抬头是否为核算主体全称 ②发票金额与明细住宿费是否一致",
    "TR-D1-03": "请核对交通费票据的行程信息与明细金额",
    "TR-D4-02": "请按出差地点对应档位核对该笔住宿费是否超标准",
    "TR-D4-04": "请确认出差地是否属西藏/青海/新疆（伙食补助 100/120 两档）",
    "TR-D4-06": "请核对市内交通费与出差天数是否匹配",
    "TR-D6-03": "请对照 OA 出差审批单核对本单的单次连续出差天数",
    "TR-D6-05": "请核对交通费凭证的往返行程与时间",
    "TR-D6-06": "请确认单次出差天数后，核验交通费/伙食/市内交通三项是否成立",
    "TR-D10-06": "请核对是否同一事项拆单报账（同主体/同收款方/近日期）",
}


def _attach_hint(f: dict) -> None:
    """hit/pending 附人工核点提示。规则资产未带 hint 字段前先按规则号兜底。"""
    if f.get("outcome") not in ("hit", "pending"):
        return
    f["human_check_hint"] = _HINTS.get(f.get("rule_id") or "")


# ── 申报值 vs 制度规定值对比（P1-2，2026-10-27）──────────────────────────────
# 证据 KV 里数字都在（住宿费/住宿上限、本单金额/阈值、伙食标准/伙食补助费），
# 但审核员要自己在脑子里做「600 > 400」这道减法。出口层把成对键翻成
# compare 结构（label/actual/limit/unit），差异与百分比由前端算——数值语义
# 在出口，呈现语义在页面。键对映射按实测 evidence 键名（规则 logic 稳定后
# 可由规则管理员在资产里维护覆盖，本次不动规则资产）。
_COMPARE_PAIRS = [
    # (规则号或 None=任意, 实际值键, 限额键, 标签, 单位)
    (None, "住宿费", "住宿上限", "住宿费", "元"),
    ("TR-D10-06", "本单金额", "阈值", "大额校验", "元"),
    (None, "伙食补助费", "伙食标准", "伙食补助", "元"),
    (None, "交通费", "交通上限", "交通费", "元"),
]


def _attach_compare(f: dict) -> None:
    """evidence 键对 → compare 数组。只在该 finding 的 evidence 里同时
    存在成对键且两个值都能当数字读时输出——读不出就不给 compare，
    没有把握的对比比没有对比更糟。"""
    ev = f.get("evidence") or {}
    out = []
    for rid, ak, lk, label, unit in _COMPARE_PAIRS:
        if rid and f.get("rule_id") != rid:
            continue
        if ak in ev and lk in ev:
            try:
                actual, limit = float(ev[ak]), float(ev[lk])
            except (TypeError, ValueError):
                continue
            out.append({"label": label, "actual": actual, "limit": limit, "unit": unit})
    if out:
        f["compare"] = out


# ─────────────────────────────────────────────────────────────────────────────
# 审核端配置出口（P0-12：数字不写死红线）
#
# 此前「大额」阈值 10000 写死在前端两处（Workbench KPI 卡、报告页队列筛选），
# 改口径要发版。口径属于业务配置，出口统一给：
#   GET /api/review/config → { big_amount_threshold, return_categories, note }
# 默认 10000（与此前前端写死的值一致，行为不变）；生产用环境变量
# FJHY_BIG_AMOUNT_THRESHOLD 覆盖，不动引擎、不进规则表。
# 退回原因分类（P1-4）也从这里下发——分类是统计维度，闭集合由后端管。
# ─────────────────────────────────────────────────────────────────────────────
RETURN_CATEGORIES = ("缺附件", "超标准", "信息有误", "疑似重复", "其他")


@router.get("/review/config")
def review_config():
    return {
        "big_amount_threshold": config.BIG_AMOUNT_THRESHOLD,
        "return_categories": list(RETURN_CATEGORIES),
        "note": "大额阈值：单张报账金额 ≥ 该值视为大额待审（队列「大额」筛选与"
                "工作台 KPI 同用这一口径）。阈值由系统配置下发，不在页面写死。",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 审核岗工作台
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/bills")
def list_bills(request: Request, conclusion: str = None, is_demo: bool = None,
               q: str = None, manual: str = None, claim: str = None,
               page: int = 1, size: int = 20):
    if manual not in (None, "none", "done"):
        raise HTTPException(400, "manual 只能是 none（还没人工结论）/ done（已有结论）")
    if claim not in (None, "all", "none", "mine"):
        raise HTTPException(400, "claim 只能是 none（未认领）/ mine（我认领的）/ all（不过滤）")
    # 单位范围（2026-10-26 客户待办#1：按账号权限匹配待审工单）。
    # 账号在 sys_user_org 挂了单位 → 工单只看所挂单位（审核专业组）；
    # 没挂 → 看全池。任何角色挂了都过滤——这是数据范围语义，不挑角色；
    # 领导/管理员正常不挂，即看全部。
    u = current_user(request)
    where, params = [], []
    my_orgs = [r["org_id"] for r in
               db.query("SELECT org_id FROM sys_user_org WHERE account = %s", (u["account"],))]
    if my_orgs:
        where.append("b.org_id = ANY(%s)")
        params.append(my_orgs)
    if is_demo is not None:
        where.append("b.is_demo = %s")
        params.append(is_demo)
    if q:
        where.append("(b.bill_no ILIKE %s OR b.summary ILIKE %s)")
        params += ["%%%s%%" % q, "%%%s%%" % q]
    if conclusion:
        where.append("r.conclusion = %s")
        params.append(conclusion)
    # 人工复核回流（2026-10-25）：manual=none 只取还没人工结论的单——复核一张，
    # none 就少一张，刷新页面、换台机器都不回弹。review_run.conclusion **不动**：
    # 那是 AI 的结论；人工结论叠加在旁边（m.* 列）。两个结论分开存，
    # 「AI 说待复核、人核了没问题」这件事才追得清。
    if manual == "none":
        where.append("m.id IS NULL")
    elif manual == "done":
        where.append("m.id IS NOT NULL")
    # 认领状态（2026-10-26）：none=抢单池（未认领），mine=我认领的（我的待办）。
    if claim == "none":
        where.append("c.assignee IS NULL")
    elif claim == "mine":
        where.append("c.assignee = %s")
        params.append(u["account"])
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = """FROM expense_bill b
              LEFT JOIN org o ON o.org_id = b.org_id
              LEFT JOIN LATERAL (SELECT * FROM review_run rr WHERE rr.bill_no=b.bill_no
                                 ORDER BY rr.started_at DESC LIMIT 1) r ON TRUE
              LEFT JOIN LATERAL (SELECT id, decision, decided_at
                                 FROM manual_review mr WHERE mr.bill_no=b.bill_no
                                 ORDER BY mr.decided_at DESC LIMIT 1) m ON TRUE
              LEFT JOIN LATERAL (SELECT a.assignee, a.claimed_at, s.user_name AS claim_name
                                 FROM review_assignment a
                                 JOIN sys_user s ON s.account = a.assignee
                                 WHERE a.bill_no=b.bill_no AND a.released_at IS NULL
                                 ORDER BY a.claimed_at DESC LIMIT 1) c ON TRUE""" + w
    out = paginate(
        "SELECT count(*) " + base,
        """SELECT b.bill_no, b.doc_type, b.doc_date, b.biz_type, b.amount, b.summary,
                  b.is_demo, b.applicant_rank, b.remark IS NOT NULL AS has_remark,
                  b.org_id, o.name AS org_name,
                  r.run_id, r.conclusion, r.hit_count, r.pending_count,
                  r.rule_executed, r.rule_candidates, r.finished_at,
                  m.decision AS manual_decision, m.decided_at AS manual_decided_at,
                  c.assignee AS claim_account, c.claim_name AS claim_label,
                  c.claimed_at
           """ + base + " ORDER BY b.doc_date DESC NULLS LAST, b.bill_no", tuple(params),
        page, size)
    out["banner"] = demo_banner()
    if my_orgs:
        # 过滤口径要在出口说清楚，否则「为什么少了单」要翻代码才知道
        out["scope_note"] = "当前账号按单位范围过滤（%d 个核算主体）" % len(my_orgs)
    return out


@router.get("/bills/{bill_no}")
def get_bill(bill_no: str):
    b = must_exist(db.query_one("""SELECT b.*, o.name AS org_name FROM expense_bill b
                                   LEFT JOIN org o ON o.org_id=b.org_id
                                   WHERE b.bill_no=%s""", (bill_no,)), "报账单 " + bill_no)
    # 有效认领（2026-10-26）：详情页/报告页直达 URL 时也要知道这单在谁手上。
    # 字段名 claim_label 不叫 reviewer——mask M01 按字段名打星，署名不许打星。
    act = _active_claim(bill_no)
    b["claim_account"] = act["assignee"] if act else None
    b["claim_label"] = act["claim_name"] if act else None
    b["claimed_at"] = act["claimed_at"] if act else None
    b["details"] = db.query("SELECT expense_item, amount, alloc_dept FROM expense_detail "
                            "WHERE bill_no=%s ORDER BY id", (bill_no,))
    b["approvals"] = db.query("""SELECT seq, node, approver, opinion, approved_at
                                 FROM approval_record WHERE bill_no=%s ORDER BY seq""",
                              (bill_no,))
    b["travelers"] = db.query("SELECT emp_no, name FROM bill_traveler WHERE bill_no=%s "
                              "ORDER BY ord", (bill_no,))
    # 附件。**不回 stored_path**：绝对路径是服务器的内部结构，前端用 id 取图就够了
    # （/api/attachments/{id}/file）。source_ref 要回——那是「这张图出自哪份 xlsx
    # 的哪一行哪个 media」，是事后回查原件的唯一入口。
    b["attachments"] = db.query(
        """SELECT id, attach_type, file_name, pages, seq, byte_size, content_type,
                  source_kind, source_ref, type_code, type_source, type_conf,
                  -- 有没有图可取。**前端要按这个判，不要按 source_kind 判**：
                  -- 原来缩略图写的是 `source_kind === 'xlsx内嵌'`，于是演示单配了
                  -- 合成示意图之后，页面照旧显示「无原图」——条件写死在来源上，
                  -- 就跟不上数据的变化。stored_path 本身不回（那是服务器绝对路径）。
                  (stored_path IS NOT NULL) AS has_file
             FROM attachment WHERE bill_no=%s
            ORDER BY (source_kind <> 'xlsx内嵌'), seq NULLS LAST, id""", (bill_no,))
    # 附件数**两个口径并列**，页面分两行显示，谁也不覆盖谁。
    #   attach_count      真实原图数（xlsx 内嵌，点得开）。**语义不变**——
    #                     _attach_metrics_note 与「申报张数」的对比都指它。
    #   attach_demo_count 演示合成数（类型是生成器给的，图是带水印的示意图）
    # 实测踩过：只摆一个口径且标签写「实际附件数」，演示单于是出现
    # 「头部写『附件（2 个）』、指标卡写『实际附件数 0』」同页两个数对不上。
    b["attach_count"] = sum(1 for a in b["attachments"] if a["source_kind"] == "xlsx内嵌")
    b["attach_demo_count"] = sum(1 for a in b["attachments"]
                                 if a["source_kind"] == "演示合成")
    # 「申报张数」与「实际附件数」是**两个指标**，页面并列显示，谁也不覆盖谁。
    # 实测 34/40 张单两者不等，而那不等于「申报不实」（见迁移 0035 的列注释）。
    b["_attach_metrics_note"] = (
        "「附件张数」(%s) 来自**审批意见 PDF** 的申报值；「实际附件数」(%d) 是从 xlsx "
        "内嵌图片里抽出来、真的落了盘的那几张。**两者是两个指标，不是一个**——"
        "实测 40 张真实单里 34 张不等（申报多 32、申报少 2）。"
        "「张数」可能按纸质页数算、一张图可能是两页合并、申报数也可能含未扫描的原件，"
        "所以**没有**把不等做成规则：在口径搞清楚之前，那会是一条 32/40 命中的误报机。"
        % (b.get("attach_pages") if b.get("attach_pages") is not None else "未填",
           b["attach_count"]))
    # **这段文案必须跟 attach_type 的实测值走，一个字都不许写死。**
    # 实测踩过（2026-10-23 修）：原文在 `if attachments and attach_count` 下
    # **无条件**断言「附件类型全部为空、TR-D6-01 仍然不能开」——那是 0/219 已判时
    # 写的真话；等 attachment_classify.py 把 219 条全判完之后，它变成了每张真实单上
    # 都加粗显示的**假告警**，使用者读到的结论是「附件没弄好」，于是来问
    # 「怎么真实数据和模拟数据都没有附件」。**假话比没有这句话更危险。**
    # 同一次还修掉了演示单落进 if/elif 空档（attachments 非空而 attach_count=0，
    # 两个分支都不进，_attachment_note 压根没赋值）导致的「零解释」。
    real_atts = [a for a in b["attachments"] if a["source_kind"] == "xlsx内嵌"]
    demo_atts = [a for a in b["attachments"] if a["source_kind"] == "演示合成"]
    untyped = [a for a in real_atts if not a["attach_type"]]
    # 合成图是否已生成，也按实测判：库里 content_type 有值才算有图。
    # 不写死「演示单有图」——seed.py 的 clear() 会清表重建，重建后又会变成没图。
    demo_with_img = [a for a in demo_atts if a["content_type"]]
    if not b["attachments"]:
        b["_attachment_note"] = (
            "本单没有附件记录。注意区分两件事：真实单的附件已于 2026-10-21 从 xlsx "
            "内嵌图片抽取入库（219 张，覆盖 40 张真实单）；**演示单（is_demo）的附件是合成的**。"
            "这一单两者都没有。")
    elif real_atts and untyped:
        b["_attachment_note"] = (
            "本单 %d 张真实附件里**还有 %d 张没判类型**——「没判」不是「没有类型」。"
            "从一张 PNG 判它是出差审批单还是住宿费发票必须读图"
            "（必要附件决策表 15 种细类里 7 种标着 ocr_needed）。未判完之前"
            "**TR-D6-01（必要附件完整性）不能开**：缺的那几张到底是「真的没附件」"
            "还是「还没判」，分不清。" % (len(real_atts), len(untyped)))
    elif real_atts:
        b["_attachment_note"] = (
            "本单 %d 张真实附件的类型**已全部判出**（读图判定，原图点得开）。"
            "**TR-D6-01（必要附件完整性）目前仍未开启**——这是待定的项目决策，"
            "**不是数据不够**：全库 219 张真实附件已全部判完。"
            "开它就会开始对真实单报「缺件」，口径要先定。" % len(real_atts))
    elif demo_with_img and len(demo_with_img) == len(demo_atts):
        b["_attachment_note"] = (
            "本单 %d 条附件**全部是演示合成的**：类型和文件名由生成器给定，"
            "图是带「演示数据」水印的合成示意图，**不是真实凭证**——"
            "不要拿它验证识别效果。真实单的附件是从客户 xlsx 内嵌图片抽出来的真图。"
            % len(demo_atts))
    else:
        b["_attachment_note"] = (
            "本单 %d 条附件是**演示合成**的，而且其中 %d 条连合成示意图都还没生成"
            "（只有类型和文件名，点开会是 404）。真实单的附件不受影响。"
            % (len(demo_atts), len(demo_atts) - len(demo_with_img)))
    # 最近一次审核结论 + 违规规则（供单据详情页展示「违规了哪些制度」）
    # rule_name 不叫 name，避免脱敏模块按字段名把规则名打成星号（踩坑三次）
    latest_run = db.query_one("""
        SELECT run_id, conclusion, hit_count, pending_count, rule_executed, finished_at
        FROM review_run WHERE bill_no=%s ORDER BY started_at DESC LIMIT 1
    """, (bill_no,))
    if latest_run:
        if (latest_run.get("hit_count") or 0) > 0:
            latest_run["hits"] = [_humanize_finding(f) for f in db.query("""
                SELECT rf.rule_id, rl.name AS rule_name,
                       rf.control_type, rf.message, rf.needs_human
                FROM review_finding rf
                LEFT JOIN rule rl ON rl.rule_id = rf.rule_id AND rl.version = rf.rule_version
                WHERE rf.run_id = %s AND rf.outcome = 'hit'
                ORDER BY rf.rule_id
            """, (latest_run["run_id"],))]
        else:
            latest_run["hits"] = []
        if (latest_run.get("pending_count") or 0) > 0:
            latest_run["pending"] = [_humanize_finding(f) for f in db.query("""
                SELECT rf.rule_id, rl.name AS rule_name, rf.message, rf.pending_reason
                FROM review_finding rf
                LEFT JOIN rule rl ON rl.rule_id = rf.rule_id AND rl.version = rf.rule_version
                WHERE rf.run_id = %s AND rf.outcome = 'pending'
                ORDER BY rf.rule_id
            """, (latest_run["run_id"],))]
        else:
            latest_run["pending"] = []
    b["latest_review"] = latest_run
    return b


@router.get("/reviews/{run_id}")
def get_review(run_id: str):
    """一份审核报告。**三个数必须并列出现。**

    只报「命中 N 条」会让人以为其余都查过了。
    执行多少、命中多少、判不了多少、被剔除多少——四个数缺一不可。
    """
    r = must_exist(db.query_one("SELECT * FROM review_run WHERE run_id=%s", (run_id,)),
                   "审核记录 " + run_id)
    # 人工结论叠加（2026-10-25 回流）：与 AI 结论并列返回，谁判的一目了然。
    # None = 该轮还没有人工结论（闸门待提交）。user_label 命名理由见 bill_manuals。
    manual = db.query_one("""SELECT decision, reviewer AS user_label, decided_at,
                                    return_reason
                             FROM manual_review WHERE run_id=%s
                             ORDER BY decided_at DESC LIMIT 1""", (run_id,))
    findings = db.query("""SELECT rule_id, rule_class, control_type, outcome, message,
                           evidence, clause_ref, matched_text, pending_reason,
                           llm_involved, llm_rationale, needs_human
                           FROM review_finding WHERE run_id=%s
                           ORDER BY CASE outcome WHEN 'hit' THEN 0 WHEN 'pending' THEN 1
                                                 ELSE 2 END, rule_id""", (run_id,))
    # 出口白话化（P0-7）：G-04 等内部缺口编号与规则号互引只给规则管理员看，
    # 审核报告上翻成人话。脱敏映射与 _plain_for_auditor 的注释见文件头。
    findings = [_humanize_finding(f) for f in findings]
    hit = [f for f in findings if f["outcome"] == "hit"]
    pending = [f for f in findings if f["outcome"] == "pending"]
    passed = [f for f in findings if f["outcome"] == "pass"
              and not (f["evidence"] or {}).get("not_applicable")]
    na = [f for f in findings if f["outcome"] == "pass"
          and (f["evidence"] or {}).get("not_applicable")]
    return {
        "run": r,
        "manual": manual,
        "coverage": {
            "candidates": r["rule_candidates"], "executed": r["rule_executed"],
            "excluded": len(r["rule_excluded"] or []),
            "hit": len(hit), "pending": len(pending),
            "passed": len(passed), "not_applicable": len(na),
            "note": "「不适用」是 scope 不覆盖本单，不等于「已查且合规」。"
                    "「待人工」更不等于「通过」——把它算进通过，"
                    "就等于声称复核了没复核的东西。",
        },
        "findings": {"hit": hit, "pending": pending, "passed": passed,
                     "not_applicable": na},
        # 被剔除的规则单列一节。审核岗看到「执行 12 条」时必须同时看到这个。
        # 这是**存库的 JSON**（老行里键就叫 name），所以在出口处转键：
        # 叫 name 会被人名脱敏吃掉，报告上「没跑的规则」整列变星号。
        "excluded_rules": [dict(x, rule_name=x.pop("name", None))
                           if isinstance(x, dict) and "name" in x else x
                           for x in (r["rule_excluded"] or [])],
        "excluded_note": "这些规则本次**没有执行**。不是通过，是没跑。"
                         "让 AI 声称复核了它读不到的数据，比少复核更危险。",
        "ai_is_advisory": {
            "value": True,
            "why": "AI 结论不阻断流程。实测 FJPG-BX2610130821 在 2026-10-13 17:25:03 "
                   "得到「AI审核不通过」，17:35:41 仍进入共享审核并由人工给出退回理由。"
                   "闸门在人工复核，不在这份报告。",
        },
    }


@router.get("/bills/{bill_no}/reviews")
def bill_reviews(bill_no: str):
    return {"items": db.query("""SELECT run_id, conclusion, hit_count, pending_count,
                                 rule_executed, rule_candidates, started_at, finished_at,
                                 rule_set_version, ontology_version
                                 FROM review_run WHERE bill_no=%s
                                 ORDER BY started_at DESC""", (bill_no,))}


@router.get("/bills/{bill_no}/peers")
def bill_peers(bill_no: str, days: int = Query(7, ge=1, le=90)):
    """同类单据（P1-9，纯查询）：同核算主体 + （同金额 或 同收款方 或 同业务类型）
    在 N 日内的其它单据，含各自 AI 结论与人工结论。

    审核「这张是不是重报」是高频动作。TR-D10-06 的 peer_bills_2d 只用于引擎
    判定、不暴露给审核员——这里独立按同一口径数据做 SELECT，不动引擎。
    金额完全相等的重复报账是最强信号，单列 same_amount 供前端标红。"""
    me = must_exist(db.query_one(
        "SELECT bill_no, org_id, amount, payee, biz_type, doc_date, is_demo "
        "FROM expense_bill WHERE bill_no=%s", (bill_no,)), "报账单 " + bill_no)
    rows = db.query("""
        SELECT b.bill_no, b.amount, b.payee, b.biz_type, b.doc_date, b.is_demo,
               o.name AS org_name,
               r.run_id, r.conclusion, r.hit_count, r.pending_count,
               m.decision AS manual_decision, m.decided_at AS manual_decided_at
          FROM expense_bill b
          LEFT JOIN org o ON o.org_id = b.org_id
          LEFT JOIN LATERAL (SELECT run_id, conclusion, hit_count, pending_count
                             FROM review_run rr WHERE rr.bill_no=b.bill_no
                             ORDER BY rr.started_at DESC LIMIT 1) r ON TRUE
          LEFT JOIN LATERAL (SELECT decision, decided_at FROM manual_review mr
                             WHERE mr.bill_no=b.bill_no
                             ORDER BY mr.decided_at DESC LIMIT 1) m ON TRUE
         WHERE b.bill_no <> %s
           AND b.org_id = %s
           AND b.doc_date BETWEEN (%s::date - %s::int) AND (%s::date + %s::int)
           AND (b.amount = %s OR b.payee = %s OR b.biz_type = %s)
         ORDER BY b.amount = %s DESC, b.doc_date DESC NULLS LAST
         LIMIT 20""", (bill_no, me["org_id"], me["doc_date"], days, me["doc_date"], days,
                       me["amount"], me["payee"], me["biz_type"], me["amount"]))
    for x in rows:
        x["same_amount"] = me["amount"] is not None and x["amount"] == me["amount"]
    return {"items": rows, "window_days": days, "total": len(rows),
            "note": "同主体 %s 日内同金额/同收款方/同业务类型的单据（最多 20 条）。"
                    "金额相同为重复报账最强信号；AI 结论与人工结论并列供比对。"
                    % days}


@router.get("/bills/{bill_no}/manuals")
def bill_manuals(bill_no: str):
    """一张单的人工复核历史（复核记录卡的数据源）。

    此前 manual_review 只写不读：复核记录只能显示「本会话」的动作，
    刷新即失忆——留痕看不见等于没留。
    署名字段出口叫 user_label：叫 reviewer 会被 M01 按字段名打成「陈*」，
    打星的署名落进留痕等于签名作废（doclib 审定人同一个坑）。
    """
    must_exist(db.query_one("SELECT bill_no FROM expense_bill WHERE bill_no=%s", (bill_no,)),
               "报账单 " + bill_no)
    return {"items": db.query("""SELECT id, run_id, decision, reviewer AS user_label,
                                        decided_at, return_reason, return_category,
                                        disagree_with, agree_with
                                 FROM manual_review WHERE bill_no=%s
                                 ORDER BY decided_at DESC, id DESC""", (bill_no,))}


# ─────────────────────────────────────────────────────────────────────────────
# 工单认领（2026-10-26 客户待办#1：个人工作台 / 按账号匹配待审工单）
#
# 模型 = 抢单池（对位客户在用共享系统的「拉式」派工）：待审池对审核岗公开可见，
# 认领即锁定——有效认领期间他人不可提交该单复核；释放盖时间戳不删行。
# 认领不是提交的前置条件（未认领的单可直接复核，提交时自动补认领留痕），
# 这样旧流程不破，锁定语义也完整。
# ─────────────────────────────────────────────────────────────────────────────
def _active_claim(bill_no: str) -> dict | None:
    return db.query_one(
        """SELECT a.id, a.assignee, a.claimed_at, s.user_name AS claim_name
             FROM review_assignment a JOIN sys_user s ON s.account=a.assignee
            WHERE a.bill_no=%s AND a.released_at IS NULL
            ORDER BY a.claimed_at DESC LIMIT 1""", (bill_no,))


@router.post("/bills/{bill_no}/claim")
def claim_bill(bill_no: str, request: Request):
    u = current_user(request)
    if u["role_key"] != "auditor":
        raise HTTPException(403, "认领是审核岗的动作（当前角色：%s）"
                            % u.get("role_name", u["role_key"]))
    must_exist(db.query_one("SELECT bill_no FROM expense_bill WHERE bill_no=%s", (bill_no,)),
               "报账单 " + bill_no)
    act = _active_claim(bill_no)
    if act:
        # 错误文案不带认领人姓名：detail 是自由文本，闭集合脱敏会把「林嘉」打成
        # 「林*」，和列表里的真名署名（claim_label）同屏打架。真名看列表/详情，
        # 错误只说事实。
        raise HTTPException(409, "该单据已被认领（%s），不可重复认领；认领人见单据列表"
                            % act["claimed_at"].strftime("%H:%M"))
    db.execute("INSERT INTO review_assignment (bill_no, assignee) VALUES (%s, %s)",
               (bill_no, u["account"]))
    # 动作必留痕（P0-10）：认领改了工单归属，管理员操作台账要能看到。
    # op_log 的 CATEGORIES 早已预留 "review"，此前从未接线。
    op_log.log_by_request(request, "review", "认领工单", target=bill_no,
                          detail="认领即锁定，他人不可代审")
    return {"ok": True, "bill_no": bill_no, "assignee": u["account"],
            "claim_label": u["user_name"],
            "note": "认领即锁定：有效认领期间他人不可复核此单；不处理可在个人工作台释放。"}


@router.post("/bills/{bill_no}/release")
def release_bill(bill_no: str, request: Request, body: dict = Body(None)):
    u = current_user(request)
    if u["role_key"] != "auditor":
        raise HTTPException(403, "释放认领是审核岗的动作（当前角色：%s）"
                            % u.get("role_name", u["role_key"]))
    act = _active_claim(bill_no)
    if not act:
        raise HTTPException(409, "该单据当前没有有效认领，无需释放")
    if act["assignee"] != u["account"]:
        # 只许认领人自己释放：他人代释放等于替人退单，责任链就断了。
        # 管理员要强制回收走数据库，不留后门接口。
        raise HTTPException(403, "只有认领人可以释放此认领")
    note = str((body or {}).get("reason") or "").strip() or None
    db.execute("UPDATE review_assignment SET released_at=now(), release_note=%s WHERE id=%s",
               (note, act["id"]))
    # 动作必留痕（P0-10）：释放把工单放回共享池，与认领对偶。
    op_log.log_by_request(request, "review", "释放工单", target=bill_no,
                          detail="认领已释放，工单回到待认领池")
    return {"ok": True, "bill_no": bill_no, "released": u["account"]}


@router.get("/my/work")
def my_work(request: Request):
    """个人工作台（审核岗首页）：我的待办 / 可认领池 / 我的已审 / 我的统计。

    「历史审核数据」按 manual_review.reviewer = 当前账号姓名 查——
    复核留痕的署名存的就是姓名（沿用既有列，不加第二套署名键）。
    代价是改姓名会断历史，属已知取舍：账号是唯一键但留痕里没有它，
    生产化时应在 manual_review 补 account 列，这里先在 note 里说清楚。
    """
    u = current_user(request)
    if u["role_key"] != "auditor":
        raise HTTPException(403, "个人工作台是审核岗页面（当前角色：%s）"
                            % u.get("role_name", u["role_key"]))
    my_orgs = [r["org_id"] for r in
               db.query("SELECT org_id FROM sys_user_org WHERE account = %s", (u["account"],))]
    org_w, org_p = (" AND b.org_id = ANY(%s)", [my_orgs]) if my_orgs else ("", [])
    # 三段共用同一套单据基座（最新run/人工结论/有效认领），与 /api/bills 同口径
    base = """FROM expense_bill b
              LEFT JOIN org o ON o.org_id = b.org_id
              LEFT JOIN LATERAL (SELECT * FROM review_run rr WHERE rr.bill_no=b.bill_no
                                 ORDER BY rr.started_at DESC LIMIT 1) r ON TRUE
              LEFT JOIN LATERAL (SELECT id FROM manual_review mr WHERE mr.bill_no=b.bill_no
                                 ORDER BY mr.decided_at DESC LIMIT 1) m ON TRUE
              LEFT JOIN LATERAL (SELECT a.assignee, a.claimed_at
                                 FROM review_assignment a
                                 WHERE a.bill_no=b.bill_no AND a.released_at IS NULL
                                 ORDER BY a.claimed_at DESC LIMIT 1) c ON TRUE"""
    sel = """SELECT b.bill_no, b.doc_date, b.biz_type, b.amount, b.is_demo,
                    o.name AS org_name, r.run_id, r.conclusion, r.hit_count,
                    r.pending_count, r.finished_at, c.claimed_at """

    def seg(where1: str, extra: list, order: str, limit: int) -> tuple[list, int]:
        sql_where = " WHERE " + where1 + org_w if where1 else ""
        items = db.query(sel + base + sql_where + " " + order + " LIMIT %s",
                         extra + org_p + [limit])
        n = db.scalar("SELECT count(*)" + base + sql_where, extra + org_p)
        return items, n

    todo, todo_n = seg("m.id IS NULL AND c.assignee = %s", [u["account"]],
                       "ORDER BY c.claimed_at DESC NULLS LAST", 50)
    pool, pool_n = seg("m.id IS NULL AND c.assignee IS NULL", [],
                       "ORDER BY b.doc_date DESC NULLS LAST, b.bill_no", 50)
    hist = db.query(
        """SELECT mr.bill_no, mr.run_id, mr.decision, mr.decided_at, mr.return_reason,
                  b.amount, b.is_demo, o.name AS org_name, r.conclusion AS ai_conclusion
             FROM manual_review mr
             JOIN expense_bill b ON b.bill_no = mr.bill_no
             LEFT JOIN org o ON o.org_id = b.org_id
             LEFT JOIN LATERAL (SELECT conclusion FROM review_run rr
                                WHERE rr.bill_no = mr.bill_no
                                ORDER BY rr.started_at DESC LIMIT 1) r ON TRUE
            WHERE mr.reviewer = %s
            ORDER BY mr.decided_at DESC, mr.id DESC LIMIT 50""", (u["user_name"],))
    hist_n = db.scalar("SELECT count(*) FROM manual_review WHERE reviewer = %s",
                       (u["user_name"],))
    st = db.query_one(
        """SELECT count(*) AS total,
                  count(*) FILTER (WHERE decision IN ('agree','agree_with_note')) AS agree,
                  count(*) FILTER (WHERE decision = 'return') AS ret,
                  count(*) FILTER (WHERE decided_at::date = current_date) AS today,
                  count(*) FILTER (WHERE decided_at >= date_trunc('month', current_date)) AS month
             FROM manual_review WHERE reviewer = %s""", (u["user_name"],)) or {}
    return {
        "me": {"account": u["account"], "user_name": u["user_name"], "orgs": my_orgs},
        "todo": {"items": todo, "total": todo_n},
        "pool": {"items": pool, "total": pool_n},
        "history": {"items": hist, "total": hist_n},
        "stats": st,
        "note": "历史与统计按复核署名（姓名）归集；生产化将在留痕表补账号列，避免重名/改名歧义。",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 人工复核闸门 —— **真正的闸在这里**
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/reviews/{run_id}/manual")
def manual_review(run_id: str, body: dict, request: Request):
    # 角色闸（2026-10-25 深夜补）：复核留痕是审核岗的职责动作。SessionMiddleware
    # 只验「登录」不验「角色」，前端菜单收敛不是权限边界——四角色深测实测
    # leader 会话直接 POST 本接口返回 200，越权落了一条复核留痕。签字类写操作
    # 必须在服务端认角色，403 不是 401：人已登录，只是权限不够。
    u = current_user(request)
    if u["role_key"] != "auditor":
        raise HTTPException(403, "复核提交仅限审核岗（当前角色：%s）" % u.get("role_name", u["role_key"]))
    run = must_exist(db.query_one(
        "SELECT bill_no, cycle_id, agent_id FROM review_run WHERE run_id=%s", (run_id,)),
        "审核记录 " + run_id)
    decision = body.get("decision")
    if decision not in ("agree", "return", "agree_with_note"):
        raise HTTPException(400, "decision 只能是 agree / return / agree_with_note")
    if decision == "return" and not body.get("return_reason"):
        raise HTTPException(400, "退回必须给理由——退回理由是误报/漏报回流的起点")
    # 退回原因分类（P1-4）：结构化维度，偏差页按原因出分布。闭集合校验——
    # 分类要进统计，自由文本的「分类」等于没有分类。集合定义在文件头（配置出口）。
    category = body.get("return_category")
    if category is not None and category not in RETURN_CATEGORIES:
        raise HTTPException(400, "return_category 只能是：%s" % " / ".join(RETURN_CATEGORIES))
    # 同一轮只许一条留痕。此前可以反复提交：一次复核算成两次，队列的
    # manual=none/done 口径也会被重复行冲乱。
    dup = db.query_one("SELECT decision, decided_at FROM manual_review WHERE run_id=%s",
                       (run_id,))
    if dup:
        raise HTTPException(409, "该轮已有复核留痕（%s，%s），不重复记录"
                            % (dup["decision"], dup["decided_at"]))
    # 认领锁（2026-10-26）：已被他人认领的单不可提交复核——两个审核岗同时
    # 开着同一张单时，后提交的那个在这里被拦下，而不是两边都成功后
    # 对着同 run 的 409 猜「谁先审的」。未认领的单可直接审，提交时自动补认领。
    act = _active_claim(run["bill_no"])
    if act and act["assignee"] != u["account"]:
        raise HTTPException(409, "该单据已被其他审核岗认领，正在其待办中，不可代审")
    disagree = [x for x in (body.get("disagree_with") or []) if isinstance(x, str)]
    # 逐条文字意见（A-P0-1，2026-10-27）：审核岗勾「不认可」旁的就地输入。
    # 形态 = {rule_id: 意见}。只保留本次勾选的规则——勾了没写的规则不带 note 键
    # （收件箱按「有 note 则显示」渲染，历史行天然兼容）；没勾却塞了意见的丢弃，
    # 不许出现「意见跟着没被勾的规则回流」的错挂。
    raw_notes = body.get("disagree_notes") or {}
    notes = {str(k): str(v).strip()[:100]
             for k, v in raw_notes.items()
             if isinstance(raw_notes, dict) and isinstance(v, str)
             and k in disagree and v.strip()}
    # 分项核实（P1-5，2026-10-27）：对偶于 disagree 的正向勾选「已人工核实」。
    # 刻意**并入 agree_with 落库**而不是新开第二套勾选列——agree_with 的既有
    # 语义就是「明确认可的命中」，verified 是它的人工核实子集，两套「认可」
    # 口径并存迟早打架（与「署名单一来源」同一纪律）。对外字段名保持
    # verified（前端语义），出口在 bill_manuals 里合并返回 agree_with。
    agree = [x for x in (body.get("agree_with") or []) if isinstance(x, str)]
    verified = [x for x in (body.get("verified") or []) if isinstance(x, str)]
    agree_all = list(dict.fromkeys(agree + verified))  # 去重保序
    import json
    with rec.phase("A"):
        with db.tx() as conn:
            rec.guard_write("manual_review")
            # 自动补认领：未认领就提交复核 = 隐式认领（审了就是我的单），
            # 认领留痕于是覆盖「谁审的」而不只是「谁领的」。
            if not act:
                conn.cursor().execute(
                    "INSERT INTO review_assignment (bill_no, assignee) VALUES (%s, %s)",
                    (run["bill_no"], u["account"]))
            conn.cursor().execute("""INSERT INTO manual_review
                (bill_no, run_id, reviewer, decision, return_reason, return_category,
                 disagree_with, agree_with)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run["bill_no"], run_id, body.get("reviewer") or "unknown", decision,
                 body.get("return_reason"),
                 category if decision == "return" else None,
                 json.dumps(disagree, ensure_ascii=False),
                 json.dumps(agree_all, ensure_ascii=False)))
            # 「不认可这条命中」逐条回流 feedback_log。此前接口 note 一直**承诺**回流
            # 但全仓没人写——承诺了就是欠着：偏差页（finding_vs_human/false_positive）
            # 因此从来不含这些勾选。cycle_id/agent_id 取自本轮 review_run——
            # 被评判的正是那个 Agent 的产出。feedback_log.cycle_id NOT NULL，
            # 老 run 没有 cycle 时只能跳过，并在返回里如实报数，不假报成功。
            written = 0
            written_notes = 0
            for rid in disagree:
                if not run["cycle_id"]:
                    break
                # note 并入 actual JSON（A-P0-1）：disagree_with 保持数组形态不动
                # （复核记录显示口径不变）；规则管理员侧收件箱点开异议时
                # 「审核员为什么说不认可」就来自这里。没有意见的历史行无 note 键。
                actual = {"rule_id": rid, "run_id": run_id}
                if rid in notes:
                    actual["note"] = notes[rid]
                    written_notes += 1
                rec.record_feedback(conn, run["cycle_id"], run["agent_id"], run["bill_no"],
                                    "finding_vs_human",
                                    {"human": "不认可该命中"},
                                    actual,
                                    1.0, "false_positive", "human_review")
                written += 1
            # 整单退回 vs AI 结论（2.11-E，2026-10-27）：前端退回弹窗一直承诺
            # 「原因将进入偏差统计」，但此前整单 return 从不写 feedback_log——
            # 偏差页的 false_negative（AI 放行/待复核，人工退回）因此永远缺这批。
            # 退回时同步写一条 conclusion_vs_human 候选；AI 结论为 pass/review
            # 而人退回 → false_negative 候选。agree 不写（AI 通过人通过=一致，
            # null 偏差没有统计价值；needs_review_only 的口径由偏差页作业归类）。
            fn_written = 0
            if decision == "return" and run["cycle_id"]:
                run_concl = db.query_one(
                    "SELECT conclusion FROM review_run WHERE run_id=%s", (run_id,))
                ai_concl = run_concl["conclusion"] if run_concl else None
                if ai_concl in ("pass", "review"):
                    rec.record_feedback(
                        conn, run["cycle_id"], run["agent_id"], run["bill_no"],
                        "conclusion_vs_human",
                        {"expected": ai_concl},
                        {"human": "return", "reason": (body.get("return_reason") or "")[:200],
                         "run_id": run_id},
                        1.0, "false_negative", "human_review")
                    fn_written = 1
    # 动作必留痕（P0-10）：复核提交是签字类动作，管理员的操作台账必须可见。
    op_log.log_by_request(request, "review", "复核提交", target=run["bill_no"],
                          detail="结论：%s%s%s%s" % (
                              {"agree": "复核通过", "agree_with_note": "通过并备注",
                               "return": "退回报账人"}[decision],
                              "（%s）" % category if decision == "return" and category else "",
                              "；人工核实 %d 项" % len(verified) if verified else "",
                              "；不认可 %d 条" % len(disagree) if disagree else ""))
    return {"ok": True,
            "feedback_written": written,
            "false_negative_written": fn_written,
            "note": "人工结论已记录。%d 条不认可已逐条回流 feedback_log"
                    "(finding_vs_human/false_positive)%s%s。" % (
                        written,
                        "；其中 %d 条附文字意见" % written_notes if written_notes else "",
                        "；整单退回已记偏差候选（conclusion_vs_human）" if fn_written else "")}


# ── 单据附件 ────────────────────────────────────────────────────────────────
# 附件是**真实发票与出差申请单的扫描图**，里面有姓名、身份证号片段、金额。
# 两条安全口径：
#
# 1. **只按 id 取图，不接受外来路径。** 第一版很容易写成
#    `GET /file?path=...`，那等于把整个服务器磁盘开给前端。
#    这里拿 id 查库取 stored_path，再强制校验它确实落在 ATTACH_DIR 之下——
#    库里的值理论上是我们自己写的，但「理论上可信的输入」正是最容易出事的那种。
#
# 2. **图片出口不过脱敏中间件。** MaskMiddleware 只处理 JSON 响应，二进制直接穿过去。
#    这是**有意的**：审核岗必须看到发票原件才能复核，把发票打上马赛克等于
#    让这套系统没法用。但它意味着**这个接口会把客户的个人信息原样发给浏览器**，
#    所以它只在内网 POC 里成立；对外提供必须先有登录与权限。这一条写在这里，
#    不是写在某份文档里——下一个人改这个接口时看得到。
from fastapi.responses import FileResponse   # noqa: E402
from .. import config                        # noqa: E402


def _under(path, base):
    """path 是否落在 base 之下。→ bool

    **刻意不用 os.path.commonpath。** 实测（2026-10-21）：Windows 上两个路径
    在不同盘符时它抛 `ValueError: Paths don't have the same drive`，
    于是这道闸是以 500 崩掉的方式「拦住」的——文件确实没发出去，
    但接口回的是「服务器故障」而不是「这个路径不许读」，
    而一个会抛异常的安全检查，下一个人很容易用 try/except 包起来然后 pass 掉。

    normcase 也是必须的：Windows 路径大小写不敏感，
    `d:\\...\\var\\attachments` 和 `D:\\...\\var\\Attachments` 是同一个目录，
    用区分大小写的比较会把合法路径判成越界。
    """
    p = os.path.normcase(os.path.abspath(path))
    b = os.path.normcase(os.path.abspath(base)).rstrip("\\/")
    return p == b or p.startswith(b + os.sep)


@router.get("/attachments/{att_id}/file")
def attachment_file(att_id: int):
    """取一张附件原图。"""
    row = must_exist(db.query_one(
        "SELECT bill_no, file_name, stored_path, content_type, source_kind "
        "FROM attachment WHERE id=%s", (att_id,)), "附件 %d" % att_id)
    # stored_path 有两种历史形态（新相对值 / 旧绝对值，跨环境搬来的也在内），
    # 统一先解析成本环境路径，见 attachment_extract.resolve_stored_path 的注释。
    # 解析不了的交回 _under / 404 的既有分支——那两处文案会说清发生了什么。
    from ..perception.attachment_extract import resolve_stored_path
    path = resolve_stored_path(row["stored_path"])
    if not path:
        raise HTTPException(404, detail=(
            "这条附件记录没有原图。source_kind=%s —— **演示单的附件是合成的**，"
            "只有类型和文件名，没有图片。" % row["source_kind"]))
    # 规范化之后必须仍在 ATTACH_DIR 之下，否则一律拒绝
    if not _under(path, config.ATTACH_DIR):
        raise HTTPException(400, detail=(
            "这条附件记录的路径不在附件存储区（%s）内，拒绝读取。"
            "附件只能从附件区读——库里的值理论上是我们自己写的，"
            "但「理论上可信的输入」正是最容易出事的那种" % config.ATTACH_DIR))
    full = os.path.abspath(path)
    if not os.path.isfile(full):
        raise HTTPException(404, detail=(
            "登记里有这条附件，但磁盘上找不到文件：%s。"
            "**不自动删登记**——登记在而文件不在，是需要人去查的状态，"
            "悄悄清掉等于把问题藏起来" % row["file_name"]))
    return FileResponse(full, media_type=row["content_type"] or "image/png",
                        filename=row["file_name"])


@router.get("/attachments/status")
def attachment_status():
    """抽了多少、和申报张数差多少、还差什么才能开 TR-D6-01。"""
    from ..perception import attachment_extract as ax
    return ax.status()


# ── 附件读图判类型 ───────────────────────────────────────────────────────────
# **真出网、真计费**，而且发出去的是真实发票与出差申请单（含姓名、身份证号片段、
# 金额）。单独一组接口、单独一个按钮——点「附件分类」之前，使用者必须先看到
# 「这一下要读几张图、花多少钱」，不能和不计费的抽取动作混在一个按钮里。
from ..perception import attachment_classify as acx   # noqa: E402


@router.get("/attachments/classify/pending")
def attachment_classify_pending(limit: int = Query(500, ge=1, le=2000)):
    """还没判类型的真实附件。**点「开始分类」之前先看这份清单。**"""
    rows = acx.pending(limit)
    return {"items": rows, "total": len(rows),
            "note": "只列从 xlsx 抽出来的真实附件（source_kind=xlsx内嵌）；"
                    "演示数据的类型是生成器直接给的，不进这份清单。"}


@router.post("/attachments/classify")
def attachment_classify(body: dict = Body(None)):
    """批量读图判类型。**这一下会真出网、真计费。**

    一次最多 limit 条（上限 100），不传 attachment_ids 就按「还没判类型的」顺序挑。
    传 attachment_ids=[] 等于明确说「这次不处理任何一条」，不是「全部」。
    """
    b = body or {}
    ids = b.get("attachment_ids")
    try:
        return acx.run(attachment_ids=ids, limit=int(b.get("limit") or 20),
                       config_id=b.get("config_id"))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.get("/attachments/classify/status")
def attachment_classify_status():
    """分类进度、费用留痕（累计 token）、类型分布。"""
    return acx.status()


# ── 附件字段抽取 ────────────────────────────────────────────────────────────
# **真出网、真计费**，是判类型之后的第二步。单独一组接口——和分类一样，
# 点「开始抽取」之前使用者要先看到「这一下要读几张图」。
# 2026-10-22 用户决策：这一次只抽文字/日期/布尔字段，金额税额不落明文。
from ..perception import attachment_extract_fields as aex  # noqa: E402


@router.get("/attachments/extract/pending")
def attachment_extract_pending(limit: int = Query(500, ge=1, le=2000)):
    """已判过类型、在抽取范围内（A01/A03/A04/A05）、但还没抽字段的附件。"""
    rows = aex.pending(limit)
    return {"items": rows, "total": len(rows)}


@router.post("/attachments/extract")
def attachment_extract(body: dict = Body(None)):
    """批量抽字段。**这一下会真出网、真计费。** 只问文字/日期/布尔字段，不问金额税额。

    一次最多 limit 条（上限 100），不传 attachment_ids 就按「还没抽的」顺序挑。
    """
    b = body or {}
    ids = b.get("attachment_ids")
    try:
        return aex.run(attachment_ids=ids, limit=int(b.get("limit") or 20),
                       config_id=b.get("config_id"))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.get("/attachments/extract/status")
def attachment_extract_status():
    """抽取进度、费用留痕。"""
    return aex.status()


@router.get("/attachments/perception")
def attachment_perception(failures: int = Query(50, ge=0, le=500)):
    """附件感知链路一次看全：判类型 + 抽字段的进度、成本、失败。

    **做成一个合并接口而不是让前端打四次**：这四个数必须同时出现在一屏上
    才有意义。分四次拉，中间任何一次失败都会让页面显示一个「进度 219/219
    但成本空白」的半截状态，而那看起来像「没花钱」。

    失败明细**默认带上**（最近 50 条）。读图是真出网真计费的动作，
    把失败藏在另一个要点一下才展开的接口里，等于默认不给看——
    实测 49 次失败在改这一版之前页面上一个字都没有。
    """
    from .. import perception
    clf = acx.status()
    ext = aex.status()
    clf_f = perception.ledger_failures("attachment_vision", failures)
    ext_f = perception.ledger_failures("attachment_extract", failures)
    return {
        "classify": clf,
        "extract": ext,
        "classify_failures": clf_f,
        "extract_failures": ext_f,
        # 链路口径：两步是**串行依赖**，而且第二步的范围比第一步小。
        # 不写这一句，看到「判类型 219 / 抽字段 197」会以为少了 22 张。
        "chain_note": (
            "两步串行：先判类型（全部 %d 张真实附件），再按类型抽字段"
            "（只有 A01/A03/A04/A05 四类进抽取范围，共 %d 张）。"
            "**抽字段的分母比判类型小不是漏了**——出差审批单、交通费凭证、"
            "住宿费发票、其他差旅费发票之外的类型这一轮不抽字段。"
            % (clf.get("total") or 0, ext.get("eligible") or 0)),
        "cost_note": (
            "token 是累计口径（两张台账全量，含失败的调用）。"
            "**失败的调用也花钱**，所以成本要算在一起看；"
            "但失败次数不等于待办，见各步的失败说明。"),
    }


@router.get("/deviation")
def deviation():
    """误报与漏报**分开**统计，不合成准确率。"""
    rows = db.query("""SELECT deviation_kind, count(*) n FROM feedback_log
                       WHERE metric IN ('conclusion_vs_human','finding_vs_human')
                       GROUP BY 1 ORDER BY 2 DESC""")
    by_rule = db.query("""SELECT actual->>'rule_id' AS rule_id, count(*) n
                          FROM feedback_log
                          WHERE metric='finding_vs_human' AND deviation_kind='false_positive'
                            AND actual->>'rule_id' IS NOT NULL
                          GROUP BY 1 ORDER BY 2 DESC LIMIT 20""")
    return {"by_kind": rows, "false_positive_by_rule": by_rule,
            "kinds": {
                "false_positive": "AI 命中了规则，人工却放行 —— 真误报，代价最高",
                "false_negative": "AI 放行，人工退回 —— 漏报",
                "needs_review_only": "AI 只是有待人工项，人工放行 —— **不是误报**，是没把握",
                "null": "一致",
            },
            "note": "漏报放过风险；误报把正常单据判成问题件，审核岗很快就不再信任这套系统。"
                    "合成一个「准确率」会把误报的代价藏起来，所以这里不给那个数。"
                    "「判不了」单列，不混进误报——混进去会虚报误报率。"}


# ─────────────────────────────────────────────────────────────────────────────
# 审计接口 —— **旁路出口，必须过同一套脱敏**
# ─────────────────────────────────────────────────────────────────────────────
audit_router = APIRouter(prefix="/api/audit", tags=["审计"])


@audit_router.get("/snapshots")
def snapshots(subject_id: str = None, page: int = 1, size: int = 20):
    w, p = ("WHERE subject_id=%s", (subject_id,)) if subject_id else ("", ())
    return paginate("SELECT count(*) FROM data_pool_snapshot " + w,
                    """SELECT snapshot_id, agent_id, subject_id, taken_at, payload_hash,
                       masked, source_tables, row_counts, payload
                       FROM data_pool_snapshot """ + w + " ORDER BY taken_at DESC", p,
                    page, size)


@audit_router.get("/cycles")
def cycles(subject_id: str = None, status: str = None, page: int = 1, size: int = 20):
    where, params = [], []
    if subject_id:
        where.append("subject_id=%s")
        params.append(subject_id)
    if status:
        where.append("status=%s")
        params.append(status)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return paginate("SELECT count(*) FROM cycle_log" + w,
                    """SELECT cycle_id, agent_id, agent_version, rule_set_version,
                       ontology_version, subject_id, snapshot_id, trigger_kind,
                       trigger_reason, rule_candidates, rule_executed, rule_excluded,
                       rule_hits, decision, phase_timings_ms, status, error,
                       started_at, finished_at
                       FROM cycle_log""" + w + " ORDER BY started_at DESC",
                    tuple(params), page, size)


@audit_router.get("/trace/{subject_id}")
def trace(subject_id: str):
    """一张单的全过程：S 看到什么 → D 判了什么 → A 写了什么 → F 偏差多少 → I 调了什么。

    五张表分开存的价值就在这个接口上：合成一张「日志表」之后，
    「模型看到的输入」和「模型造成的写入」就混在同一批行里，这里没法分开追。
    """
    return {
        "subject_id": subject_id,
        "S_snapshots": db.query("""SELECT snapshot_id, taken_at, payload_hash, masked,
                                   source_tables, row_counts FROM data_pool_snapshot
                                   WHERE subject_id=%s ORDER BY taken_at""", (subject_id,)),
        "D_cycles": db.query("""SELECT cycle_id, trigger_kind, rule_candidates, rule_executed,
                                decision, phase_timings_ms, status, started_at
                                FROM cycle_log WHERE subject_id=%s ORDER BY started_at""",
                             (subject_id,)),
        "A_actions": db.query("""SELECT l.emitted_at, l.action, l.target, l.written_tables,
                                 l.idempotency_key FROM agent_signal_log l
                                 JOIN cycle_log c ON c.cycle_id=l.cycle_id
                                 WHERE c.subject_id=%s ORDER BY l.emitted_at""", (subject_id,)),
        "F_feedback": db.query("""SELECT observed_at, metric, expected, actual,
                                  deviation, deviation_kind, source FROM feedback_log
                                  WHERE subject_id=%s ORDER BY observed_at""", (subject_id,)),
        # I 阶段按**批次**调参，不是按单。所以 improve_log.cycle_id 通常为空，
        # 按 cycle_id 关联永远查不到——第一版就是这么写的，结果这一节永远是空的，
        # 看着像「从来没调过参」。
        # 正确做法：取这张单所涉 Agent 的近期调参，并标明它是批次级的。
        "I_improvements": db.query("""SELECT i.applied_at, i.agent_id, i.param,
                                      i.old_value, i.new_value, i.rejected,
                                      i.rejected_why, i.reason, i.bounded_by,
                                      (i.cycle_id IS NOT NULL) AS per_cycle
                                      FROM improve_log i
                                      WHERE i.agent_id IN (
                                        SELECT DISTINCT agent_id FROM cycle_log
                                        WHERE subject_id = %s)
                                      ORDER BY i.applied_at DESC LIMIT 20""",
                                   (subject_id,)),
        "I_scope_note": "I 阶段按**批次**调参，不是按单——单个周期的数字噪声太大，"
                        "按它调会让参数抖来抖去。所以这一节列的是本单所涉 Agent 的"
                        "近期调参记录，per_cycle=false 表示该条是批次级的。",
    }


@audit_router.get("/consistency")
def consistency():
    ok, info = rec.audit_consistency()
    return {"ok": ok, **info}


@audit_router.get("/quarantine")
def quarantine():
    """隔离区不为空就是一个**要处理的事实**，不是日志噪音。

    里面每一行都代表一笔业务数据当前不被任何规则审核。
    """
    rows = db.query("""SELECT id, loaded_at, source, target, reason, gap_id, row_data,
                       resolved FROM load_quarantine ORDER BY loaded_at DESC""")
    return {"items": rows, "unresolved": sum(1 for r in rows if not r["resolved"]),
            "note": "覆盖面声明里必须把它算进去——"
                    "「本批 40 张单全部审核完毕」和「40 张单 + 8 行游离明细未审」是两句话。"}


# ─────────────────────────────────────────────────────────────────────────────
# 调试接口 —— **另一个旁路出口**
# ─────────────────────────────────────────────────────────────────────────────
debug_router = APIRouter(prefix="/api/debug", tags=["调试"])


@debug_router.get("/signals")
def signals():
    return {"items": db.query("""SELECT signal_id, channel, kind, subject_id, status,
                                 attempts, max_attempts, last_error, payload,
                                 claimed_by, created_at FROM agent_signal
                                 ORDER BY id DESC LIMIT 100"""),
            "by_status": db.query("SELECT status, count(*) n FROM agent_signal GROUP BY 1")}


@debug_router.get("/agent-state")
def agent_state():
    return {"items": db.query("SELECT * FROM agent_state ORDER BY agent_id")}


@debug_router.get("/fact/{bill_no}")
def debug_fact(bill_no: str):
    """直接看 S 阶段会捞到什么。**这是最容易漏脱敏的一条路**——
    它返回的是原始事实对象，不经过报告生成。"""
    from ..perception import reader
    fact, meta = reader.snapshot_fact(bill_no)
    if fact is None:
        raise HTTPException(404, "单据 %s 不存在" % bill_no)
    payload, masked, leaks = reader.masked_payload(fact)
    return {"meta": meta, "masked": masked, "leaks": leaks, "payload": payload}
