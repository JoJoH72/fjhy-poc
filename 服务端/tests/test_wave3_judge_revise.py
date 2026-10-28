# -*- coding: utf-8 -*-
"""Wave 3 四条的后端闭环测试（2026-10-28）：

  B-P0-2 附件识别结果人工订正 —— reclassify（人工定类/改判/幂等/400/403/
         台账只增）+ fields（GET 并列视图/POST 订正/清除还原/闭集合/403）
  B-P1-D 草稿重复检测 —— dup_check 信号口径（来源条款门控 / 名称相似 /
         阈值重合 / 无命中不误报）
  B-P1-E 规则修订与版本历史 —— revise（理由必填/v2 新行旧行不动/409 防重/
         compare 声明落 logic/403）+ versions（含 released_in 口径）
  B-P1-F 规则试算 —— same_set 零差异提前返回 + 修订后差异（变严/按规则
         ±命中/逐单可跳报告）+ 只读红线（业务表行数前后不变）+ op_log 留痕

纪律与 test_feedback_inbox 相同：跑真库、只碰可还原的数据、用完还原；
真实单据上的行数前后必须分毫不差。改判/修订/试算全部要能回答留痕三问。
"""
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app import db  # noqa: E402
from app import doclib_extract as dx  # noqa: E402


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUTH_MODE", "on")


def login(account, pwd="demo-1234") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"account": account, "password": pwd})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(autouse=True)
def _restore():
    """真库还原口径：记下要动的对象，测试后逐一还原。
    JSONB 列 db 层已自动解成 dict——回写要重新 dumps。"""
    touched = {"attachment": [], "rule_v2": [], "op_log": [], "drafts": []}
    yield touched
    for att_id in touched["attachment"]:
        row = db.query_one("SELECT type_source, fields_override FROM attachment WHERE id=%s",
                           (att_id,))
        db.execute("DELETE FROM attachment_judge_log WHERE attachment_id=%s", (att_id,))
        db.execute("""UPDATE attachment SET type_source=%s, fields_override=%s,
                      typed_at=NULL, typed_by=NULL WHERE id=%s""",
                   (row["type_source"],
                    json.dumps(row["fields_override"], ensure_ascii=False)
                    if isinstance(row["fields_override"], (dict, list))
                    else row["fields_override"], att_id))
    for rid in touched["rule_v2"]:
        db.execute("DELETE FROM rule WHERE rule_id=%s AND version > 1", (rid,))
    for target in touched["op_log"]:
        db.execute("DELETE FROM sys_op_log WHERE target=%s", (target,))
    for did in touched["drafts"]:
        db.execute("UPDATE doclib_draft SET dup_of=NULL, dup_signals=NULL WHERE draft_id=%s",
                   (did,))


def _pick_real_untyped() -> int:
    """挑一张未判类型的真实附件（还原时只动 type_source/override，attach_type
    本来就是 NULL）。"""
    row = db.query_one("""SELECT id FROM attachment
                          WHERE source_kind='xlsx内嵌' AND attach_type IS NULL
                          ORDER BY id LIMIT 1""")
    assert row, "库里没有未判类型的真实附件"
    return row["id"]


# ── B-P0-2：附件人工订正 ─────────────────────────────────────────────────────

def test_人工定类_未判类型_留痕(_restore):
    touched = _restore
    aud = login("chenfang")
    att_id = _pick_real_untyped()
    touched["attachment"].append(att_id)
    r = aud.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A04"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attach_type"] == "住宿费发票"
    assert body["old_value"] in (None, "未判定")
    # 界面并列口径：type_source=人工指定，AI 台账（attachment_vision）一行未动
    row = db.query_one("SELECT attach_type, type_source, typed_by FROM attachment WHERE id=%s",
                       (att_id,))
    assert row["attach_type"] == "住宿费发票"
    assert row["type_source"] == "人工指定"
    assert row["typed_by"] is None          # 人工定类不挂读图台账
    # 台账只增：一行 attach_type 记录，前后值都在
    log = db.query_one("""SELECT * FROM attachment_judge_log
                          WHERE attachment_id=%s AND field='attach_type'""", (att_id,))
    assert log and log["old_value"] is None and log["new_value"] == "住宿费发票"
    assert log["judged_by"] == "chenfang" and log["judged_label"]
    # op_log 留痕（review 类别，target 精确键）
    op = db.query_one("""SELECT * FROM sys_op_log WHERE category='review'
                         AND action='订正附件类型' AND target=%s""",
                      ("attachment:%d" % att_id,))
    assert op and "住宿费发票" in op["detail"] and op["actor"] == "chenfang"
    touched["op_log"].append("attachment:%d" % att_id)


def test_改判幂等与重复改判(_restore):
    touched = _restore
    aud = login("chenfang")
    att_id = _pick_real_untyped()
    touched["attachment"].append(att_id)
    aud.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A04"})
    # 同值幂等
    r2 = aud.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A04"})
    assert r2.status_code == 200 and r2.json()["unchanged"] is True
    # 再改 A03：old_value 取上一人工值
    r3 = aud.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A03"})
    assert r3.status_code == 200
    assert r3.json()["attach_type"] == "交通费报销凭证"
    assert r3.json()["old_value"] == "住宿费发票"
    logs = db.query("""SELECT new_value FROM attachment_judge_log
                       WHERE attachment_id=%s AND field='attach_type' ORDER BY id""",
                    (att_id,))
    assert [x["new_value"] for x in logs] == ["住宿费发票", "交通费报销凭证"]
    touched["op_log"].append("attachment:%d" % att_id)


def test_订正的参数与权限闸(_restore):
    touched = _restore
    aud = login("chenfang")
    eng = login("hetech")
    lead = login("leader") if db.query_one(
        "SELECT 1 FROM sys_user WHERE account='leader'") else None
    att_id = _pick_real_untyped()
    touched["attachment"].append(att_id)
    # 非法细类编码
    r = aud.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "X99"})
    assert r.status_code == 400 and "细类编码" in r.json()["detail"]
    # 不存在的附件
    r = aud.post("/api/attachments/99999999/reclassify", json={"type_code": "A04"})
    assert r.status_code == 404
    # engineer 越权（后端双闸，藏按钮不算拦截）
    r = eng.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A04"})
    assert r.status_code == 403
    r = eng.post(f"/api/attachments/{att_id}/fields",
                 json={"updates": [{"field": "title", "value": "x"}]})
    assert r.status_code == 403
    if lead:
        r = lead.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A04"})
        assert r.status_code == 403


def test_字段订正_并列与还原(_restore):
    touched = _restore
    aud = login("chenfang")
    att_id = _pick_real_untyped()
    touched["attachment"].append(att_id)
    aud.post(f"/api/attachments/{att_id}/reclassify", json={"type_code": "A04"})
    # 抽屉数据源：AI 值与人工值并列、字段名闭集合给 spec_fields
    g = aud.get(f"/api/attachments/{att_id}/fields")
    assert g.status_code == 200
    d = g.json()
    keys = {f["field"] for f in d["fields"]}
    assert {"title", "check_in", "check_out"} <= keys, "A04 的 spec 字段应可补录"
    # 订正两个字段
    r = aud.post(f"/api/attachments/{att_id}/fields", json={"updates": [
        {"field": "title", "value": "福建省港口集团有限责任公司"},
        {"field": "check_in", "value": "2025-12-14"},
    ]})
    assert r.status_code == 200 and r.json()["updated"] == 2
    g2 = aud.get(f"/api/attachments/{att_id}/fields").json()
    by = {f["field"]: f for f in g2["fields"]}
    assert by["title"]["manual_value"] == "福建省港口集团有限责任公司"
    assert by["title"]["judged"] is True
    # AI 台账零行（本来就空）——人工值不伪造 AI 值
    assert by["title"]["ai_value"] is None
    # 清除人工值 = 还原 AI 口径（overlay 删键）
    r = aud.post(f"/api/attachments/{att_id}/fields",
                 json={"updates": [{"field": "title", "value": None}]})
    assert r.status_code == 200
    assert "title" not in (r.json()["fields_override"] or {})
    # 闭集合：不收自由键
    r = aud.post(f"/api/attachments/{att_id}/fields",
                 json={"updates": [{"field": "hack", "value": "x"}]})
    assert r.status_code == 400 and "补录不是新造字段" in r.json()["detail"]
    # 台账与 op_log
    n = db.scalar("""SELECT count(*) FROM attachment_judge_log
                     WHERE attachment_id=%s""", (att_id,))
    assert n >= 4          # 1 attach_type + 2 字段 + 1 清除
    touched["op_log"].append("attachment:%d" % att_id)


def test_演示合成附件拒订正():
    aud = login("chenfang")
    row = db.query_one("""SELECT a.id FROM attachment a
                          JOIN expense_bill b ON b.bill_no=a.bill_no
                          WHERE a.source_kind='演示合成' AND b.is_demo LIMIT 1""")
    if not row:
        pytest.skip("库里没有演示合成附件")
    r = aud.post(f"/api/attachments/{row['id']}/reclassify", json={"type_code": "A04"})
    assert r.status_code == 400 and "合成" in r.json()["detail"]


# ── B-P1-D：草稿重复检测 ─────────────────────────────────────────────────────

def test_重复检测_信号口径():
    """确定性比对三信号：同文件同条款/名称相似/阈值重合；跨文件同条款号不触发。"""
    # ① 规则 source_clause.file 是类别代号（制度）时，同条款号不许触发
    #    「来源条款相同」——41 条「制度 第X条」跟任何真实文件的「第X条」都会撞。
    draft = {"规则名称": "完全无关的名称", "可规则化": True, "判定逻辑": "看心情"}
    clause = {"clause_no": "第七条", "file_name": "某真实制度文件.docx", "body": ""}
    hits = dx.dup_check(draft, clause)
    if hits:
        for m in hits["matches"]:
            assert "来源条款相同" not in m["signals_desc"], "类别代号不触发条款信号"
    # ② 名称相似（≥0.6 才独立算信号）
    draft = {"规则名称": "伙食补助费标准测试", "可规则化": True, "判定逻辑": ""}
    hits = dx.dup_check(draft, {"clause_no": "第一条", "file_name": "x.docx", "body": ""})
    assert hits and hits["matches"][0]["rule_id"] == "TR-D4-04"
    assert hits["matches"][0]["name_ratio"] >= 0.6
    assert any("名称相似" in m["signals_desc"] for m in hits["matches"])
    # ③ 无信号的草稿不误报
    hits = dx.dup_check({"规则名称": "量子纠缠差旅补贴", "可规则化": True, "判定逻辑": ""},
                        {"clause_no": "第两百条", "file_name": "z.docx", "body": ""})
    assert hits is None


def test_存量待审草稿_出口带回写(_restore):
    """list_drafts 出口对存量草稿现算回写 dup_of（不改 review_state）。"""
    rows = dx.list_drafts(state="待审", limit=30)
    assert rows
    for r in rows:
        if r.get("dup_of"):
            d = r["dup_of"] if isinstance(r["dup_of"], dict) else json.loads(r["dup_of"])
            assert d["matches"] and all(
                m["strength"] in ("高", "低") for m in d["matches"])
            assert d["signals_desc"]
            break


# ── B-P1-E：修订 + 版本历史 ─────────────────────────────────────────────────

def test_修订闭环_新行旧行不动(_restore):
    touched = _restore
    eng = login("hetech")
    rid = "TR-D9-02"
    touched["rule_v2"].append(rid)
    touched["op_log"].append(rid)
    v1 = db.query_one("SELECT * FROM rule WHERE rule_id=%s AND version=1", (rid,))
    # 理由必填
    r = eng.post(f"/api/asset/rules/{rid}/revise", json={"reason": " "})
    assert r.status_code == 400 and "理由" in r.json()["detail"]
    # 修订：例外与优先级
    r = eng.post(f"/api/asset/rules/{rid}/revise", json={
        "reason": "测试修订：例外补一条（回退）",
        "fields": {"exception": "测试例外条款", "priority": 90},
    })
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert set(r.json()["changed"]) == {"exception", "priority"}
    # 旧行原样不动（天然留痕）
    v1b = db.query_one("SELECT * FROM rule WHERE rule_id=%s AND version=1", (rid,))
    for k in ("exception", "priority", "logic", "scope", "revised_by"):
        assert v1[k] == v1b[k], f"v1 行被动了：{k}"
    # 新行带修订元数据 + 不进执行集（未发布）
    v2 = db.query_one("SELECT * FROM rule WHERE rule_id=%s AND version=2", (rid,))
    assert v2["exception"] == "测试例外条款" and v2["priority"] == 90
    assert v2["revised_by"] == "hetech" and v2["revised_label"]
    assert v2["revised_from"] == 1 and "测试修订" in v2["revised_reason"]
    assert v2["demo_executable"] is False and v2["status"] == "draft"
    assert v2["created_by"] == "revise:hetech"
    # 重复修订 → 409（先处置上一版）
    r = eng.post(f"/api/asset/rules/{rid}/revise", json={"reason": "再来"})
    assert r.status_code == 409 and "已有修订版" in r.json()["detail"]
    # 版本历史
    v = eng.get(f"/api/asset/rules/{rid}/versions").json()
    assert v["total"] == 2 and v["revised_count"] == 1
    top = v["items"][0]
    assert top["version"] == 2 and top["revised_reason"].startswith("测试修订")
    assert top["released_in"] is None            # 未随版发布
    # op_log
    op = db.query_one("""SELECT * FROM sys_op_log WHERE category='rule'
                         AND action='修订规则' AND target=%s""", (rid,))
    assert op and "v1 → v2" in op["detail"] and "hetech" in op["actor"]


def test_修订_compare声明落_logic(_restore):
    """A-P1-1 契约：compare 四字段声明随修订进 logic.compare（编辑面唯一入口）。"""
    touched = _restore
    eng = login("hetech")
    rid = "TR-D4-06"
    touched["rule_v2"].append(rid)
    touched["op_log"].append(rid)
    cmp = [{"label": "住宿费日均", "actual_key": "日均", "limit_key": "区间上界", "unit": "元"}]
    r = eng.post(f"/api/asset/rules/{rid}/revise", json={
        "reason": "补对比声明（测试回退）", "compare": cmp})
    assert r.status_code == 200, r.text
    v2 = db.query_one("SELECT logic FROM rule WHERE rule_id=%s AND version=2", (rid,))
    lg = v2["logic"] if isinstance(v2["logic"], dict) else json.loads(v2["logic"])
    assert lg.get("compare") == cmp
    # 声明形状闸：缺 limit_key → 400
    db.execute("DELETE FROM rule WHERE rule_id=%s AND version>1", (rid,))
    r = eng.post(f"/api/asset/rules/{rid}/revise", json={
        "reason": "x", "compare": [{"label": "a", "actual_key": "b"}]})
    assert r.status_code == 400 and "limit_key" in r.json()["detail"]


def test_修订权限与不存在():
    aud = login("chenfang")
    r = aud.post("/api/asset/rules/TR-D4-02/revise", json={"reason": "越权"})
    assert r.status_code == 403
    eng = login("hetech")
    r = eng.post("/api/asset/rules/TR-XX-99/revise", json={"reason": "x"})
    assert r.status_code == 404


def test_种子compare声明_声明优先于推断():
    """A-P1-1：出口三级——库内声明 > 出口种子 > 键对推断。用真实 hit 的
    evidence 直接调出口函数验证。"""
    from app.api.review import _attach_compare, _COMPARE_SEEDS
    row = db.query_one("""SELECT rf.rule_id, rf.evidence FROM review_finding rf
                          WHERE rf.rule_id='TR-D4-02' AND rf.outcome='hit' LIMIT 1""")
    if not row:
        pytest.skip("库里没有 TR-D4-02 的 hit（先跑一轮审核）")
    logic_row = db.query_one("SELECT logic FROM rule WHERE rule_id='TR-D4-02' AND version=1")
    logic = logic_row["logic"] if logic_row else {}
    # ① 库内声明优先
    f = {"rule_id": "TR-D4-02", "evidence": row["evidence"],
         "_rule_logic": {**logic, "compare": [
             {"label": "库内声明", "actual_key": "日均", "limit_key": "区间上界"}]}}
    _attach_compare(f)
    assert f["compare"][0]["label"] == "库内声明"
    # ② 无库内声明 → 出口种子接管（装载器重装 rule 表也不会丢）
    f2 = {"rule_id": "TR-D4-02", "evidence": row["evidence"], "_rule_logic": logic or {}}
    _attach_compare(f2)
    assert f2.get("compare"), "种子层必须兜住高频规则的对比块"
    assert f2["compare"][0]["label"] == "住宿费日均"
    assert isinstance(f2["compare"][0]["limit"], float)
    assert "TR-D4-02" in _COMPARE_SEEDS and "TR-D10-06" in _COMPARE_SEEDS


# ── B-P1-F：试算 ─────────────────────────────────────────────────────────────

def test_试算_无修订零差异提前返回():
    eng = login("hetech")
    has_revise = db.query_one("SELECT 1 FROM rule WHERE version > 1 AND revised_by IS NOT NULL")
    if has_revise:
        pytest.skip("库里有未回退的修订（其它用例的中间态），跳过零差异分支")
    r = eng.post("/api/publish/impact-preview", json={"scope": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["same_set"] is True and body["flips"] == []


def test_试算_修订后出差异并只读(_restore):
    touched = _restore
    eng = login("hetech")
    rid = "TR-D4-04"
    touched["rule_v2"].append(rid)
    # 只读红线基线：回放不许写任何业务表
    base = {
        "review_run": db.scalar("SELECT count(*) FROM review_run"),
        "review_finding": db.scalar("SELECT count(*) FROM review_finding"),
        "cycle_log": db.scalar("SELECT count(*) FROM cycle_log"),
        "manual_review": db.scalar("SELECT count(*) FROM manual_review"),
    }
    # 修订：伙食补助档压到 1（演示单必打出一批新命中）
    lg = db.query_one("SELECT logic FROM rule WHERE rule_id=%s AND version=1", (rid,))["logic"]
    deg = dict((lg if isinstance(lg, dict) else json.loads(lg)).get("degraded") or {})
    deg["lower"], deg["upper"] = 1, 1
    r = eng.post(f"/api/asset/rules/{rid}/revise", json={
        "reason": "试算联调：档位压到 1（回退）",
        "fields": {"logic": {"degraded": deg}},
    })
    assert r.status_code == 200, r.text
    ip = eng.post("/api/publish/impact-preview", json={"scope": "demo"})
    assert ip.status_code == 200, ip.text
    body = ip.json()
    assert body["same_set"] is False
    assert body["summary"]["changed"] > 0 and body["summary"]["stricter"] > 0
    assert any(x["rule_id"] == "TR-D4-04" and x["only_b"] > 0 for x in body["by_rule"])
    f0 = body["flips"][0]
    assert f0["new_hits"] and f0["run_id"], "逐单差异要能跳报告页（标注可下钻）"
    assert f0["direction"] in ("stricter", "looser", "flat")
    assert "不代表未来" in body["note"]
    # 只读红线：四张业务表一行未动
    after = {
        "review_run": db.scalar("SELECT count(*) FROM review_run"),
        "review_finding": db.scalar("SELECT count(*) FROM review_finding"),
        "cycle_log": db.scalar("SELECT count(*) FROM cycle_log"),
        "manual_review": db.scalar("SELECT count(*) FROM manual_review"),
    }
    assert base == after, f"试算写了业务表：{base} → {after}"
    # op_log 留痕（签字发布的配套证据）
    op = db.query_one("""SELECT * FROM sys_op_log WHERE category='publish'
                         AND action='试算差异' ORDER BY id DESC LIMIT 1""")
    assert op and "回放" in op["detail"] and op["actor"] == "hetech"
    touched["op_log"].append(rid)


def test_试算权限闸():
    aud = login("chenfang")
    r = aud.post("/api/publish/impact-preview", json={})
    assert r.status_code == 403


def test_试算非法scope():
    eng = login("hetech")
    r = eng.post("/api/publish/impact-preview", json={"scope": "mars"})
    assert r.status_code == 400
