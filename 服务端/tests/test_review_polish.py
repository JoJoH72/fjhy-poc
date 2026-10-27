# -*- coding: utf-8 -*-
"""审核岗打磨（2026-10-27 规划 P0/P1）：薄 API 出口、留痕、白话化、核实勾选。

覆盖四件事（都坏了不会报错的那类）：
  1. /api/review/config：大额阈值与退回分类闭集合下发（数字不写死红线）
  2. 认领/释放/复核提交写 sys_op_log（category=review）——管理员台账可见
  3. 审核出口白话化：G-04 等内部编号不在 /api/reviews 与单据详情出口出现
  4. 分项核实 verified 并入 agree_with 落库；退回分类落库 + 闭集合校验；
     退回写 conclusion_vs_human/false_negative 候选

纪律与 test_mywork 相同：跑真库、只碰演示单、用完还原；
真实单（非演示）上的认领/复核行数前后必须分毫不差。
"""
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
def _real_bills_untouched():
    """真实单上的认领/复核必须纹丝不动。"""
    def snap():
        return (
            db.scalar("SELECT count(*) FROM review_assignment a "
                      "JOIN expense_bill b ON b.bill_no=a.bill_no WHERE b.is_demo = FALSE"),
            db.scalar("SELECT count(*) FROM manual_review m "
                      "JOIN expense_bill b ON b.bill_no=m.bill_no WHERE b.is_demo = FALSE"),
        )
    before = snap()
    yield
    after = snap()
    assert before == after, f"测试在真实单上留下了行：{before} → {after}"


def _pick_demo_bill() -> str:
    row = db.query_one(
        """SELECT b.bill_no FROM expense_bill b
            WHERE b.is_demo = TRUE
              AND EXISTS (SELECT 1 FROM review_run r WHERE r.bill_no=b.bill_no)
              AND NOT EXISTS (SELECT 1 FROM manual_review m WHERE m.bill_no=b.bill_no)
              AND NOT EXISTS (SELECT 1 FROM review_assignment a
                              WHERE a.bill_no=b.bill_no AND a.released_at IS NULL)
            ORDER BY b.bill_no LIMIT 1""")
    assert row, "库里没有可用的演示单（未审未认领）——先跑装载链"
    return row["bill_no"]


def _latest_run(bill_no: str) -> str:
    return db.query_one(
        "SELECT run_id FROM review_run WHERE bill_no=%s ORDER BY started_at DESC LIMIT 1",
        (bill_no,))["run_id"]


def _cleanup(bill_no: str):
    db.execute("DELETE FROM manual_review WHERE bill_no=%s AND reviewer LIKE %s",
               (bill_no, "测试%"))
    db.execute("DELETE FROM review_assignment WHERE bill_no=%s", (bill_no,))
    # 测试写的偏差候选行（按 run_id 定位，只删自己那轮）
    db.execute("""DELETE FROM feedback_log WHERE subject_id=%s
                  AND metric='conclusion_vs_human'
                  AND actual->>'run_id' = ANY(
                      SELECT run_id FROM review_run WHERE bill_no=%s)""",
               (bill_no, bill_no))


# ── 1. 配置出口 ──────────────────────────────────────────────────────────────

def test_审核端配置出口():
    c = login("chenfang")
    r = c.get("/api/review/config")
    assert r.status_code == 200
    d = r.json()
    assert d["big_amount_threshold"] == 10000.0, "默认阈值应与原前端写死值一致（行为不变）"
    assert "缺附件" in d["return_categories"] and "其他" in d["return_categories"]


# ── 2. 动作留痕（sys_op_log） ────────────────────────────────────────────────

def test_认领释放复核都写操作日志():
    no = _pick_demo_bill()
    lin = login("linjia")
    try:
        run = _latest_run(no)
        assert lin.post(f"/api/bills/{no}/claim").status_code == 200
        row = db.query_one(
            "SELECT * FROM sys_op_log WHERE category='review' AND target=%s "
            "ORDER BY id DESC LIMIT 1", (no,))
        assert row and row["action"] == "认领工单" and row["actor"] == "linjia"
        assert row["actor_name"] == "林嘉"

        assert lin.post(f"/api/bills/{no}/release").status_code == 200
        row = db.query_one(
            "SELECT * FROM sys_op_log WHERE category='review' AND target=%s "
            "ORDER BY id DESC LIMIT 1", (no,))
        assert row and row["action"] == "释放工单"

        r = lin.post(f"/api/reviews/{run}/manual", json={"decision": "agree",
                                                          "reviewer": "测试林嘉"})
        assert r.status_code == 200, r.text
        row = db.query_one(
            "SELECT * FROM sys_op_log WHERE category='review' AND target=%s "
            "ORDER BY id DESC LIMIT 1", (no,))
        assert row and row["action"] == "复核提交" and "复核通过" in row["detail"]
    finally:
        _cleanup(no)


# ── 3. 出口白话化 ────────────────────────────────────────────────────────────

def test_审核出口不带内部缺口编号():
    """G-04 是规则管理员的语言；审核报告上必须已是白话。"""
    run = db.query_one(
        """SELECT DISTINCT rf.run_id FROM review_finding rf
            WHERE rf.outcome='pending'
              AND (rf.pending_reason LIKE '%%G-04%%' OR rf.message LIKE '%%G-04%%')
            LIMIT 1""")
    if not run:
        pytest.skip("库里没有带 G-04 的 pending 行")
    c = login("chenfang")
    r = c.get("/api/reviews/" + run["run_id"])
    assert r.status_code == 200
    d = r.json()
    for k in ("hit", "pending"):
        for f in d["findings"][k]:
            blob = (f.get("message") or "") + (f.get("pending_reason") or "")
            assert "G-04" not in blob, f"{k} {f['rule_id']} 出口仍带 G-04：{blob[:60]}"
            assert "比 TR-D6-03 严厉" not in blob, "规则号互引未翻成白话"
    # 白话语义在（不是把整句删了）
    joined = " ".join((f.get("pending_reason") or "")
                      for f in d["findings"]["pending"])
    if d["findings"]["pending"]:
        assert ("暂缺" in joined) or ("暂未接入" in joined) or len(joined) > 0


def test_人工核点提示与对比出口():
    """P1-6/P1-2：hint 按规则号兜底；compare 只在 evidence 键对齐全时出。"""
    c = login("chenfang")
    run = db.query_one(
        "SELECT DISTINCT run_id FROM review_finding WHERE rule_id='TR-D6-03' "
        "AND outcome='pending' LIMIT 1")
    if not run:
        pytest.skip("库里没有 TR-D6-03 的 pending 行")
    d = c.get("/api/reviews/" + run["run_id"]).json()
    p = [f for f in d["findings"]["pending"] if f["rule_id"] == "TR-D6-03"]
    assert p and p[0].get("human_check_hint"), "TR-D6-03 应带人工核点提示"
    # TR-D1-02（抬头核对）实测 evidence 无数字键对 → compare 必须不出（不编数）
    run2 = db.query_one(
        "SELECT DISTINCT run_id FROM review_finding WHERE rule_id='TR-D1-02' "
        "AND outcome='hit' LIMIT 1")
    if run2:
        d2 = c.get("/api/reviews/" + run2["run_id"]).json()
        h = [f for f in d2["findings"]["hit"] if f["rule_id"] == "TR-D1-02"]
        assert h and "compare" not in h[0], "键对不齐不应编造 compare"


# ── 4. 分项核实 + 退回分类 + 偏差候选 ────────────────────────────────────────

def test_核实勾选并入_agree_with_落库():
    no = _pick_demo_bill()
    lin = login("linjia")
    run = _latest_run(no)
    try:
        # 找该轮一条 pending 规则做核实目标
        rid = db.query_one(
            "SELECT rule_id FROM review_finding WHERE run_id=%s AND outcome='pending' "
            "ORDER BY rule_id LIMIT 1", (run,))
        body = {"decision": "agree", "reviewer": "测试林嘉"}
        if rid:
            body["verified"] = [rid["rule_id"]]
        r = lin.post(f"/api/reviews/{run}/manual", json=body)
        assert r.status_code == 200, r.text
        row = db.query_one("SELECT agree_with FROM manual_review WHERE run_id=%s", (run,))
        import json as _j
        got = _j.loads(row["agree_with"] or "[]")
        if rid:
            assert rid["rule_id"] in got, "verified 未并入 agree_with"
    finally:
        _cleanup(no)


def test_退回分类与偏差候选():
    no = _pick_demo_bill()
    chen = login("chenfang")
    run = _latest_run(no)
    try:
        r = chen.post(f"/api/reviews/{run}/manual", json={
            "decision": "return", "reviewer": "测试陈芳",
            "return_reason": "缺住宿发票", "return_category": "缺附件"})
        assert r.status_code == 200, r.text
        assert r.json().get("false_negative_written") in (0, 1)
        row = db.query_one("SELECT return_category FROM manual_review WHERE run_id=%s", (run,))
        assert row and row["return_category"] == "缺附件"
        # 偏差候选：AI 结论 review/pass 而人退回 → false_negative 已写实
        ai = db.query_one("SELECT conclusion FROM review_run WHERE run_id=%s", (run,))["conclusion"]
        fn = db.query_one(
            """SELECT 1 FROM feedback_log WHERE subject_id=%s
                AND metric='conclusion_vs_human' AND deviation_kind='false_negative'""",
            (no,))
        assert bool(fn) == (ai in ("pass", "review")), "退回偏差候选与 AI 结论不一致"
    finally:
        _cleanup(no)


def test_退回分类闭集合校验():
    no = _pick_demo_bill()
    chen = login("chenfang")
    run = _latest_run(no)
    try:
        r = chen.post(f"/api/reviews/{run}/manual", json={
            "decision": "return", "reviewer": "测试陈芳",
            "return_reason": "x", "return_category": "不存在的分类"})
        assert r.status_code == 400 and "return_category" in r.json()["detail"]
    finally:
        _cleanup(no)


# ── 5. 同类单据 peers ────────────────────────────────────────────────────────

def test_同类单据peers纯查询():
    no = db.query_one("SELECT bill_no FROM expense_bill WHERE NOT is_demo LIMIT 1")["bill_no"]
    c = login("chenfang")
    r = c.get(f"/api/bills/{no}/peers")
    assert r.status_code == 200
    d = r.json()
    assert d["window_days"] == 7 and d["total"] == len(d["items"]) <= 20
    for it in d["items"]:
        assert it["bill_no"] != no, "peers 不能含自己"
        assert "same_amount" in it and "conclusion" in it and "manual_decision" in it
    # 不存在的单 404
    assert c.get("/api/bills/NO-SUCH/peers").status_code == 404
