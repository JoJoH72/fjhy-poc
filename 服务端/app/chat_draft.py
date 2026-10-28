# -*- coding: utf-8 -*-
"""对话式起草（客户 #6，打磨规划 B-P1-A / v0.1 §2.4，2026-10-28）。

用户在抽屉里用自然语言描述业务要求 → 模型追问关键要素 → 产八要素草稿 →
用户确认 → **进既有专家审定链**（ExtractReview 同款审定页签）。

三条设计判断，都不许在实施中松动：

1. **对话只是草稿的第五来源，不是规则的直通道。** 产物落 doclib_draft
   （review_state='待审'），与制度抽取草稿走完全相同的审定/采纳/发布闸门。
   「对话不出审定直接入库」是红线——降低的是起草门槛，不是审定门槛。

2. **防幻觉如实降级。** 对话没有制度原文兜底：数值核对只对**用户原话**
   做（用户说了 200 元、模型写 250 元 → 红标；用户没提数字模型编了一个 →
   红标），审定页仍要挂「无制度原文佐证」黄条。核对不通过照样落库照常
   打标——删掉它等于掩盖模型的问题（与 check_numbers 同一条纪律）。

3. **每一笔可交代。** 模型调用逐次落 llm_call_log（purpose='chat_draft'，
   成败都落）；转待审落 op_log（category=rule）；用户原话永久留在
   doclib_clause.body——审定人看到的「条款原文」就是起草人当时怎么说的。

多轮上下文由前端持有、随请求带回（服务端无状态）：会话是草稿的孕育过程，
落库的是「转待审」那一刻的结论，过程不占库表。
"""
import datetime
import json
import re
import uuid

from . import db, mask
from .llm import client as llmc

# 对话用途取 rule_judge 配置（glm-5.3，走内网网关）——对话式起草要的是
# 「跟人多轮掰扯清楚要素」的能力，与规则研判同一档；extract 那档
# （glm-5.3-flash）是批量抽条款的性价比档。不悄悄顶替：没有配置就明确报错。
CHAT_PURPOSE = "rule_judge"


def chat_cfg():
    """取对话起草用的模型配置。没有就**明确报错**（与 _extract_cfg 同款纪律）。"""
    cfg = db.query_one("""SELECT * FROM llm_config
                           WHERE enabled AND is_default AND purpose = %s
                           LIMIT 1""", (CHAT_PURPOSE,))
    if not cfg:
        cfg = db.query_one("""SELECT * FROM llm_config
                               WHERE enabled AND purpose = %s
                               ORDER BY updated_at DESC LIMIT 1""", (CHAT_PURPOSE,))
    if not cfg:
        raise RuntimeError(
            "没有启用的「规则研判」（%s）用途模型配置，对话起草需要它。"
            "去「大模型配置」页给该用途选一条配置并设为默认。"
            "**不自动改用别的用途的模型**：对话起草与读图/抽条款的要求不一样，"
            "悄悄顶替会让结果质量无从解释。" % CHAT_PURPOSE)
    return cfg


# 八要素字段（与 doclib_extract.SYSTEM 的 JSON 结构对齐——审定页按同一套
# 字段名渲染，对话草稿不该让审定页多写一行兼容）。
DRAFT_FIELDS = ("规则名称", "审核维度", "规则类型", "风险等级", "适用范围",
                "判定逻辑", "例外情形", "处置动作")
# 对话模式额外带的说明字段（不进八要素渲染，进缺口栏）
EXTRA_FIELDS = ("缺口",)

SYSTEM = """你是差旅费报销规则的起草助手。用户会用自然语言描述一条业务要求，
你帮他把要求落成可被规则引擎执行的「规则八要素」草稿。

工作方式（每一轮都遵守）：
1. 用户说得不够具体时**先追问再起草**：适用范围（哪些人/哪些费用）、
   阈值或金额、控制类型（拦截=禁止 / 提醒=警告）这三样缺一样就问，
   一次最多问两样，别把人问烦。
2. 要素齐了就出草稿；用户继续补充时**增量修订**同一张草稿，不是重起一张。
3. **不得编造用户没有给出的数字**。用户说「超标 200 元以上拦截」，
   草稿里就只许出现 200；顺手写个「单次上限 500 元」是幻觉，会被核对标红。
4. 用户原话给不出的字段填 null，并在 缺口 里写明缺什么、需要谁补。
5. 只输出 JSON，不要任何解释文字。

JSON 结构：
{"reply": "给用户看的一句话（追问或确认，60 字内）",
 "ready": true/false,
 "draft": {"可规则化": true, "原因": "", "规则名称": "", "审核维度": "",
           "规则类型": "硬|混合|软", "适用范围": "", "判定逻辑": "",
           "例外情形": "", "风险等级": "高|中|低", "处置动作": "禁止|警告",
           "缺口": ""}}

ready=false 表示还在追问；draft 可以为空对象。ready=true 时 draft 必须齐。"""

MAX_MSGS = 30            # 会话上限：防把聊天记录当免费存储无限堆
MAX_CHARS = 6000         # 单条消息上限（脱敏后）：防把整份制度贴进来当对话


def _fmt_turns(messages):
    """把多轮对话拼成模型输入。用户内容先脱敏（纪律：送进模型前就要脱敏）。"""
    lines = []
    for m in messages[-MAX_MSGS:]:
        role = "用户" if m.get("role") == "user" else "助手"
        text = str(m.get("content") or "").strip()[:MAX_CHARS]
        if not text:
            continue
        if role == "用户":
            text = mask.mask_free_text(text)
        lines.append("%s：%s" % (role, text))
    return "\n".join(lines)


def _clean_draft(draft):
    """模型给的草稿瘦身：只留契约字段，字符串字段裁长度（防把整段对话塞进草稿）。"""
    d = draft if isinstance(draft, dict) else {}
    out = {}
    for f in DRAFT_FIELDS + EXTRA_FIELDS + ("可规则化", "原因"):
        v = d.get(f)
        if isinstance(v, str):
            v = v.strip()[:500]
        out[f] = v
    out["可规则化"] = bool(out.get("可规则化", True))
    return out


def chat_turn(messages, session_hint=""):
    """一轮对话。messages=[{role:'user'|'assistant', content}]，返回给前端的
    {reply, ready, draft, model, latency_ms, note}。**真调模型、真计费。**

    会话上下文整个随请求带回：服务端不留会话表——对话是过程，落库的是
    「转待审」那一刻的结论（draft_from_chat），过程不占库表。
    """
    msgs = [m for m in (messages or [])
            if isinstance(m, dict) and str(m.get("content") or "").strip()]
    if not msgs:
        raise ValueError("没有可发送的对话内容")
    user_turns = [m for m in msgs if m.get("role") == "user"]
    if not user_turns:
        raise ValueError("会话里没有用户发言——起草从你的描述开始")
    if len(msgs) > MAX_MSGS:
        raise ValueError("会话超过 %d 轮，请新开一次对话起草（历史草稿已转待审的不受影响）"
                         % MAX_MSGS)

    cfg = chat_cfg()
    user = "会话主题：%s\n\n对话记录：\n%s" % (
        (session_hint or "规则起草").strip()[:80], _fmt_turns(msgs))

    res, content = llmc.chat_json(
        cfg, SYSTEM, user,
        max_tokens=int(cfg.get("max_tokens") or 4000))

    # 每一次调用都落 llm_call_log，成败都落（与 ai_review 同一口径）。
    # prompt_masked 恒 TRUE：用户内容在 _fmt_turns 里已过 mask_free_text。
    db.execute("""INSERT INTO llm_call_log
                  (config_id, purpose, prompt_tokens, completion_tokens,
                   latency_ms, status, error, prompt_masked)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)""",
               (cfg.get("config_id"), "chat_draft",
                res.prompt_tokens, res.completion_tokens, res.latency_ms,
                "ok" if res.ok else (res.err_kind or "error"),
                None if res.ok else (res.message or "")[:500]))
    if not res.ok:
        return {"reply": res.message or "模型调用失败", "ready": False,
                "draft": None, "model": cfg.get("model_name"),
                "latency_ms": res.latency_ms, "ok": False,
                "err_kind": res.err_kind,
                "note": "失败也留了痕（llm_call_log，purpose=chat_draft）。"
                        "可稍后重试；重试不重复计已失败那次的费。"
                        if res.err_kind in ("http", "connect", "timeout", "tls")
                        else "配置或配额问题：去「大模型配置」页检查该用途配置。"}

    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    try:
        obj = json.loads(raw)
    except ValueError:
        return {"reply": "模型返回的不是约定的 JSON，这一轮作废。可直接重发上一句。",
                "ready": False, "draft": None,
                "model": cfg.get("model_name"), "latency_ms": res.latency_ms,
                "ok": False, "err_kind": "parse",
                "note": "原文前 200 字：" + raw[:200]}
    draft = _clean_draft(obj.get("draft") or {})
    ready = bool(obj.get("ready")) and draft.get("可规则化") is not False
    return {
        "reply": str(obj.get("reply") or "").strip()[:300] or (
            "草稿已更新" if ready else "请补充以上信息"),
        "ready": ready,
        "draft": draft if ready else None,
        "model": res.model_echo or cfg.get("model_name"),
        "latency_ms": res.latency_ms, "ok": True,
        "note": "草稿数值未与任何制度原文核对过（对话起草没有制度出处），"
                "采纳前须人工确认每个数字。" if ready else "",
    }


# ── 数值核对（对话版）──────────────────────────────────────────────────────
# 复用 doclib_extract 的阈值正则与「完整数」判定：误报和漏报同样致命的结论
# 在对话场景一样成立。唯一的不同是「原文」= 用户原话的拼接。
NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:元|天|日|人|次|公里|%|％)")
NUM_BIG = re.compile(r"(?<![\d.])\d{3,}(?:\.\d+)?(?![\d.])")
LIST_MARK = re.compile(
    r"(^|[\s；;。，,、（(【\[])\s*[（(]?\d{1,2}(?:[）)、]|[.．](?!\d))", re.M)


def check_numbers_vs_user(draft, user_texts):
    """草稿阈值 vs 用户原话：用户没说过的数字就是幻觉嫌疑。

    与 doclib_extract.check_numbers 同一套正则与豁免（列表序号不算、
    三位以上裸数也算、子串不算命中），只是比对源从条款原文换成用户原话——
    「用户没提这个数」比「原文没这个数」在对话场景下是同一个意思。
    """
    src = "".join(str(t or "") for t in user_texts)
    src = re.sub(r"[\s　,，]", "", src)
    bad = []
    for f in ("适用范围", "判定逻辑", "例外情形", "处置动作", "缺口"):
        v = draft.get(f)
        if not isinstance(v, str):
            continue
        text = LIST_MARK.sub(" ", v)
        seen = set()
        for rx in (NUM_UNIT, NUM_BIG):
            for m in rx.finditer(text):
                t = re.sub(r"[\s　,，]", "", m.group(0))
                digits = re.sub(r"[^\d.]", "", t)
                if not digits or digits in seen:
                    continue
                if not re.search(r"(?<![\d.])" + re.escape(digits) + r"(?![\d.])", src):
                    seen.add(digits)
                    bad.append({"field": f, "value": m.group(0).strip()})
    return {"ok": not bad, "suspect": bad[:8],
            "note": "对话起草没有制度原文可比，这里对的是**你的原话**："
                    "标红的数值在你的描述里没出现过，是模型补的——采纳前必须核对。"}


# ── 转待审：落库进既有审定链 ───────────────────────────────────────────────
def draft_from_chat(draft, messages, by=None, session_hint=""):
    """确认草稿 → 落 doclib_draft（待审）。**不写 rule 表、不发布。**

    落库四件套（一次事务性写入，任一步失败整体不落半截）：
      1. doclib_extract_task（mode='对话'）
      2. doc_lib_file 占位行（parse_state='解析失败'——run_split 的 WHERE
         只挑「已解析」，占位行永远不会被当制度文件重切，条款与草稿
         不会被级联清掉；doc_kind='其他'，不进制度统计）
      3. doclib_clause 一行：body=用户原话（脱敏后）。审定页「条款原文」
         栏显示的就是起草人当时怎么说——这就是对话场景的证据链。
      4. doclib_draft 一行：review_state='待审'，与制度抽取草稿同结构。
    """
    d = _clean_draft(draft)
    if not d.get("规则名称"):
        raise ValueError("草稿缺「规则名称」，不能转待审——回到对话里让模型补齐")
    if not d.get("判定逻辑"):
        raise ValueError("草稿缺「判定逻辑」，不能转待审——这是八要素里最不能空的一项")
    msgs = [m for m in (messages or [])
            if isinstance(m, dict) and str(m.get("content") or "").strip()]
    user_texts = [m.get("content") for m in msgs if m.get("role") == "user"]
    if not user_texts:
        raise ValueError("会话没有用户发言，落库后审定人将看不到起草依据，拒绝")

    chk = check_numbers_vs_user(d, user_texts)

    now = datetime.datetime.now().replace(microsecond=0)
    who = (by or "ui").strip()[:64] or "ui"
    topic = (session_hint or "").strip()[:60] or (d.get("规则名称") or "")[:60]
    task_id = "dlx-" + uuid.uuid4().hex[:12]
    stamp = now.strftime("%Y%m%d-%H%M%S")
    file_id = "dlf-" + uuid.uuid4().hex[:12]
    clause_id = "dlc-" + uuid.uuid4().hex[:12]
    draft_id = "dld-" + uuid.uuid4().hex[:12]

    # 用户原话（脱敏）——同时是发给过模型的与会话留档正文
    user_body = "\n\n".join("（用户）%s" % mask.mask_free_text(
        str(t).strip()[:MAX_CHARS]) for t in user_texts)

    # 归属实例与托管根：按当前实例取（与上传同一口径），没有托管根就挂默认实例的
    inst_row = db.query_one("""SELECT i.instance_id, i.label AS instance_label
                                 FROM domain_instance i ORDER BY i.is_default DESC, i.sort_no
                                 LIMIT 1""")
    iid = inst_row["instance_id"] if inst_row else None
    root = db.query_one("SELECT root_id FROM doc_lib_root "
                        "WHERE kind='托管' AND instance_id = %s", (iid,))
    if not root:
        root = db.query_one("SELECT root_id FROM doc_lib_root "
                            "WHERE kind='托管' ORDER BY root_id LIMIT 1")

    db.execute("""INSERT INTO doclib_extract_task(task_id, mode, scope, llm_config_id)
                  VALUES (%s,'对话',%s,NULL)""", (task_id, topic or "规则起草"))
    db.execute("""
        INSERT INTO doc_lib_file(file_id, root_id, abs_path, rel_path, file_name,
            ext, file_size, mtime, fingerprint, parse_state, parse_reason,
            text_chars, body, doc_kind, first_seen_at, last_seen_at, missing,
            last_change, changed_at, extracted_at, extract_fingerprint,
            instance_id, origin, uploaded_by, uploaded_at)
        VALUES (%s,%s,%s,%s,%s,'.md',%s,%s,%s,'解析失败',%s,0,NULL,'其他',
                %s,%s,FALSE,'新增',%s,%s,%s,%s,'上传',%s,%s)""",
               (file_id,                                   # file_id
                root["root_id"] if root else None,         # root_id
                # abs_path 全库唯一：每次会话一条（占位串带批次号防撞唯一键）
                "（对话起草会话 %s，无磁盘文件）" % task_id,
                "对话起草/%s-%s.md" % (topic[:20] or "会话", stamp),   # rel_path
                "对话起草-%s.md" % (topic[:24] or stamp),   # file_name
                len(user_body.encode("utf-8")),            # file_size
                now,                                       # mtime
                "chat:%s" % task_id,                       # fingerprint
                "对话起草会话记录，不是制度文件，不参与条款切分；"
                "草稿在「抽取与审定」页签审定",              # parse_reason
                now,                                       # first_seen_at
                now,                                       # last_seen_at
                now,                                       # changed_at
                now,                                       # extracted_at
                "chat:%s" % task_id,                       # extract_fingerprint
                iid,                                       # instance_id
                who,                                       # uploaded_by
                now))                                      # uploaded_at
    db.execute("""
        INSERT INTO doclib_clause(clause_id, file_id, seq, clause_no, title, body,
            char_pos, features, numbers, doc_kind, covered_by, file_fingerprint,
            text_source, split_strategy, extracted_at)
        VALUES (%s,%s,1,'对话',%s,%s,0,'{}','{}','其他','{}',%s,'对话','对话',%s)""",
               (clause_id, file_id, topic or None, user_body,
                "chat:%s" % task_id, now))
    db.execute("""
        INSERT INTO doclib_draft(draft_id, task_id, clause_id, rulable, draft,
            number_check, sent_chars, truncated, model, latency_ms, attempt)
        VALUES (%s,%s,%s,TRUE,%s,%s,%s,FALSE,%s,NULL,1)""",
               (draft_id, task_id, clause_id,
                json.dumps(d, ensure_ascii=False),
                json.dumps(chk, ensure_ascii=False),
                len(user_body), "对话起草"))
    db.execute("""UPDATE doclib_extract_task SET status='done', finished_at=now(),
                         clause_cnt=1, draft_cnt=1, note=%s WHERE task_id=%s""",
               ("对话起草转待审（草稿不是规则，审定后才可能进规则库）。"
                "数值核对不通过 %d 处，已在草稿上打标。" % len(chk["suspect"]),
                task_id))
    return {"draft_id": draft_id, "task_id": task_id, "clause_id": clause_id,
            "number_check": chk,
            "note": "已转待审。去「制度文件 → 抽取与审定」页签审定；"
                    "对话产物与制度抽取产物走同一条审定链。"}
