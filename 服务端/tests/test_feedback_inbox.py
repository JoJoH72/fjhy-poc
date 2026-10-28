# -*- coding: utf-8 -*-
"""异议收件箱（B-P0-1 + A-P0-1，2026-10-27 规划 Wave 1）。

覆盖两件事的闭环：
  1. 产出侧（A-P0-1）：复核提交带 disagree_with + disagree_notes →
     feedback_log.actual.note 逐条落库（不认可数组形态不变，历史行无 note 兼容）
  2. 消费侧（B-P0-1）：聚合列表 / 明细 / 处置动作（keep 必填说明 / revise 预填 /
     409 防重 / 审核岗 403）+ 处置留痕 sys_op_log + resolved 三列落库

纪律与 test_mywork 相同：跑真库、只碰演示单、用完还原；
真实单（非演示）上的复核/feedback/认领行数前后必须分毫不差。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db


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
def _real_rows_untouched():
    """真实单上的复核与 feedback 行必须纹丝不动。"""
    def snap():
        return (
            db.scalar("SELECT count(*) FROM manual_review m "
                      "JOIN expense_bill b ON b.bill_no=m.bill_no WHERE b.is_demo = FALSE"),
            db.scalar("""SELECT count(*) FROM feedback_log f
                         JOIN expense_bill b ON b.bill_no=f.subject_id
                         WHERE b.is_demo = FALSE
                           AND f.metric='finding_vs_human'"""),
        )
    before = snap()
    yield
    after = snap()
    assert before == after, f"测试在真实单上留下了行：{before} → {after}"


def _pick_demo_bill_with_hit() -> tuple[str, str, str]:
    """挑一张可下手的演示单：有审核轮次、有 hit、无人工结论、无有效认领。
    返回 (bill_no, run_id, 一条 hit 的 rule_id)。

    池子空时自举补审（与 test_mywork 同源）：字母序靠前的测试调
    generator.generate() 会清掉审核轮次、而补偿只在 session 收尾跑——
    这里自己补跑一次规则链路（不调大模型），不依赖测试顺序。"""
    sql = """SELECT b.bill_no, r.run_id,
                    (SELECT rf.rule_id FROM review_finding rf
                      WHERE rf.run_id=r.run_id AND rf.outcome='hit'
                      ORDER BY rf.rule_id LIMIT 1) AS rid
            FROM expense_bill b
            JOIN LATERAL (SELECT * FROM review_run rr WHERE rr.bill_no=b.bill_no
                          ORDER BY rr.started_at DESC LIMIT 1) r ON TRUE
            WHERE b.is_demo = TRUE AND r.hit_count > 0 AND r.cycle_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM manual_review m WHERE m.bill_no=b.bill_no)
              AND NOT EXISTS (SELECT 1 FROM review_assignment a
                              WHERE a.bill_no=b.bill_no AND a.released_at IS NULL)
            ORDER BY b.bill_no LIMIT 1"""
    row = db.query_one(sql)
    if not row:
        from app.api import ai_review as ar
        res = ar.audit_demo_bills(reason="test_feedback_inbox 池子自举（前序测试清了审核轮次）")
        assert any(r.status == "ok" for r in res), "演示单补审全部失败：%s" % [
            (r.bill_no, r.status) for r in res[:3]]
        row = db.query_one(sql)
        assert row and row["rid"], "补审后仍无可用的带 hit 演示单"
    return row["bill_no"], row["run_id"], row["rid"]


def _cleanup(bill_no: str, run_id: str):
    db.execute("DELETE FROM manual_review WHERE run_id=%s", (run_id,))
    db.execute("DELETE FROM review_assignment WHERE bill_no=%s", (bill_no,))
    db.execute("DELETE FROM feedback_log WHERE metric='finding_vs_human' "
               "AND actual->>'run_id' = %s", (run_id,))


# ── 1. 产出侧：note 逐条回流（A-P0-1）────────────────────────────────────────

def test_不认可带意见逐条回流():
    no, run, rid = _pick_demo_bill_with_hit()
    lin = login("linjia")
    try:
        r = lin.post(f"/api/reviews/{run}/manual", json={
            "decision": "agree", "reviewer": "测试林嘉",
            "disagree_with": [rid],
            "disagree_notes": {rid: "住宿标准档位与职级不匹配，判定有误"},
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["feedback_written"] == 1
        row = db.query_one(
            """SELECT actual FROM feedback_log
               WHERE metric='finding_vs_human' AND actual->>'run_id'=%s""", (run,))
        assert row, "feedback_log 未落行"
        actual = row["actual"]
        if isinstance(actual, str):
            actual = json.loads(actual)
        assert isinstance(actual, dict), f"actual 应为 dict（db 层已解 JSON）：{type(actual)}"
        assert actual.get("rule_id") == rid
        assert actual.get("note") == "住宿标准档位与职级不匹配，判定有误"
        # disagree_with 落库形态不变（数组）——复核记录显示口径不动
        mr = db.query_one("SELECT disagree_with FROM manual_review WHERE run_id=%s", (run,))
        dw = mr["disagree_with"]
        if isinstance(dw, str):
            dw = json.loads(dw or "[]")
        assert dw == [rid]
    finally:
        _cleanup(no, run)


def test_没勾的规则塞意见被丢弃():
    """意见只跟勾选走：disagree_notes 里塞了没勾的规则 → 不回流那条。"""
    no, run, rid = _pick_demo_bill_with_hit()
    lin = login("linjia")
    try:
        r = lin.post(f"/api/reviews/{run}/manual", json={
            "decision": "agree", "reviewer": "测试林嘉",
            "disagree_with": [rid],
            "disagree_notes": {rid: "这条有意见", "TR-XX-00": "没勾的意见"},
        })
        assert r.status_code == 200, r.text
        rows = db.query(
            """SELECT actual FROM feedback_log
               WHERE metric='finding_vs_human' AND actual->>'run_id'=%s""", (run,))
        rids = []
        for x in rows:
            a = x["actual"]
            rids.append((json.loads(a) if isinstance(a, str) else a).get("rule_id"))
        assert "TR-XX-00" not in rids, "没勾的规则不该回流"
    finally:
        _cleanup(no, run)


# ── 2. 消费侧：聚合 / 明细 / 处置（B-P0-1）──────────────────────────────────

def _make_one_feedback() -> tuple:
    """造一条真异议（走真实提交流程，不 SQL 造假）。
    返回 (fid, bill_no, run_id, rule_id)。"""
    no, run, rid = _pick_demo_bill_with_hit()
    lin = login("linjia")
    r = lin.post(f"/api/reviews/{run}/manual", json={
        "decision": "agree", "reviewer": "测试林嘉",
        "disagree_with": [rid], "disagree_notes": {rid: "档位映射有误，请核对职级"}})
    assert r.status_code == 200, r.text
    row = db.query_one(
        """SELECT id FROM feedback_log
           WHERE metric='finding_vs_human' AND actual->>'run_id'=%s""", (run,))
    assert row, "回流行未落"
    return row["id"], no, run, rid


def test_聚合列表与明细含意见():
    fid, no, run, rid = _make_one_feedback()
    eng = login("hetech")
    try:
        d = eng.get("/api/asset/feedbacks").json()
        # 定位到**本测试那条异议**的聚合行（库里可能有演示存量——TR-D4-02
        # 两条演示异议常驻，不能拿「第一行 pending」当自己）
        row = [x for x in d["items"] if x["rule_id"] == rid][0]
        assert row["n"] >= 1 and row["note_n"] >= 1, "计数应含带意见条数"
        assert row["rule_name"], "聚合行应带规则名"
        assert row["state"] == "pending"
        # state=pending 过滤口径
        d2 = eng.get("/api/asset/feedbacks", params={"state": "pending"}).json()
        assert all(x["state"] == "pending" for x in d2["items"])
        assert any(x["rule_id"] == rid for x in d2["items"])
        # 明细：意见原话 + 当时判定原文 + 审核员署名（user_label 口径）
        det = eng.get(f"/api/asset/feedbacks/{rid}").json()
        mine = [x for x in det["items"] if x["id"] == fid][0]
        assert mine["note"] == "档位映射有误，请核对职级"
        assert mine["bill_no"] == no and mine["run_id"] == run
        assert mine["user_label"] == "测试林嘉"
        assert mine["finding_message"], "当时判定原文应给出（标注可下钻）"
    finally:
        db.execute("DELETE FROM manual_review WHERE run_id=%s", (run,))
        db.execute("DELETE FROM review_assignment WHERE bill_no=%s", (no,))
        db.execute("DELETE FROM feedback_log WHERE id=%s", (fid,))
        db.execute("DELETE FROM sys_op_log WHERE category='rule' AND target=%s",
                   ("feedback:%d" % fid,))


def test_处置维持_必填说明_留痕():
    fid, no, run, rid = _make_one_feedback()
    eng = login("hetech")
    aud = login("chenfang")
    try:
        # 审核岗不许自处置
        r0 = aud.post(f"/api/asset/feedbacks/{fid}/resolve",
                      json={"decision": "keep", "note": "x"})
        assert r0.status_code == 403
        # keep 不带说明 → 400
        r1 = eng.post(f"/api/asset/feedbacks/{fid}/resolve",
                      json={"decision": "keep", "note": " "})
        assert r1.status_code == 400 and "维持" in r1.json()["detail"]
        # 正常维持
        r2 = eng.post(f"/api/asset/feedbacks/{fid}/resolve",
                      json={"decision": "keep", "note": "规则口径无误，系单据职级填写错误"})
        assert r2.status_code == 200, r2.text
        row = db.query_one("SELECT resolved_by, resolved_note, resolved_at "
                           "FROM feedback_log WHERE id=%s", (fid,))
        assert row["resolved_by"] == "hetech"
        assert row["resolved_note"] == "规则口径无误，系单据职级填写错误"
        assert row["resolved_at"]
        # 留痕：sys_op_log rule 类别 + 精确键 target
        op = db.query_one(
            "SELECT * FROM sys_op_log WHERE category='rule' AND target=%s "
            "ORDER BY id DESC LIMIT 1", ("feedback:%d" % fid,))
        assert op and op["action"] == "处置异议" and "维持规则" in op["detail"]
        assert op["actor"] == "hetech"
        # 重复处置 → 409
        r3 = eng.post(f"/api/asset/feedbacks/{fid}/resolve",
                      json={"decision": "keep", "note": "再来一次"})
        assert r3.status_code == 409
        # 聚合态变已维持：定位本测试那条规则的聚合行（同规则可能有演示存量
        # 异议常驻——只断言本条处置后的状态口径，不断言全表）
        d = eng.get("/api/asset/feedbacks").json()
        row = [x for x in d["items"] if x["rule_id"] == rid][0]
        if row["pending_n"] == 0:
            assert row["state"] == "keep", f"处置后该规则应已维持，实际 {row['state']}"
        else:
            # 同规则还有别的待处置异议（演示存量）→ 行仍待处置，但 pending_n
            # 已减一——这正是聚合口径的正确行为
            assert row["state"] == "pending"
    finally:
        db.execute("DELETE FROM manual_review WHERE run_id=%s", (run,))
        db.execute("DELETE FROM review_assignment WHERE bill_no=%s", (no,))
        db.execute("DELETE FROM feedback_log WHERE id=%s", (fid,))
        db.execute("DELETE FROM sys_op_log WHERE category='rule' AND target=%s",
                   ("feedback:%d" % fid,))


def test_处置确认为规则问题_带预填():
    fid, no, run, rid = _make_one_feedback()
    eng = login("hetech")
    try:
        r = eng.post(f"/api/asset/feedbacks/{fid}/resolve",
                     json={"decision": "revise"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prefill"] and "异议" in body["prefill"] and "档位映射有误" in body["prefill"], \
            "修订预填应带最近一条意见摘要"
        op = db.query_one(
            "SELECT detail FROM sys_op_log WHERE category='rule' AND target=%s "
            "ORDER BY id DESC LIMIT 1", ("feedback:%d" % fid,))
        assert "确认为规则问题" in op["detail"]
    finally:
        db.execute("DELETE FROM manual_review WHERE run_id=%s", (run,))
        db.execute("DELETE FROM review_assignment WHERE bill_no=%s", (no,))
        db.execute("DELETE FROM feedback_log WHERE id=%s", (fid,))
        db.execute("DELETE FROM sys_op_log WHERE category='rule' AND target=%s",
                   ("feedback:%d" % fid,))


def test_处置不存在的异议404():
    eng = login("hetech")
    r = eng.post("/api/asset/feedbacks/99999999/resolve",
                 json={"decision": "keep", "note": "x"})
    assert r.status_code == 404
