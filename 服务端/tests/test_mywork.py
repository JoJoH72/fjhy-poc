# -*- coding: utf-8 -*-
"""个人工作台 / 工单认领（2026-10-26 客户待办#1）：抢单池、认领锁定、单位范围。

**跑在真库上**，纪律与 test_iam 相同：
  · 只碰演示单（is_demo=TRUE、FJHY-DEMO- 前缀），用完把认领/复核/绑定行清干净
  · 唯一一次真实 manual 提交不带 disagree_with（不产生 feedback_log 审计行），
    manual_review 行在 finally 里删掉，队列口径还原
  · autouse 兜底闸：真实单（非演示）上的认领行数前后必须分毫不差
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
    """真实单上的认领必须纹丝不动——测试只能动演示单。"""
    def snap():
        return db.scalar(
            "SELECT count(*) FROM review_assignment a "
            "JOIN expense_bill b ON b.bill_no=a.bill_no WHERE b.is_demo = FALSE")
    before = snap()
    yield
    after = snap()
    assert before == after, f"测试在真实单上留下了认领行：{before} → {after}"


def _pick_demo_bill() -> str:
    """挑一张可以下手的演示单：有审核轮次、无人工结论、无有效认领。"""
    row = db.query_one(
        """SELECT b.bill_no FROM expense_bill b
            WHERE b.is_demo = TRUE
              AND EXISTS (SELECT 1 FROM review_run r WHERE r.bill_no=b.bill_no)
              AND NOT EXISTS (SELECT 1 FROM manual_review m WHERE m.bill_no=b.bill_no)
              AND NOT EXISTS (SELECT 1 FROM review_assignment a
                              WHERE a.bill_no=b.bill_no AND a.released_at IS NULL)
            ORDER BY b.bill_no LIMIT 1""")
    assert row, "库里没有可用的演示单（全部已有结论或被认领）——请先跑演示数据生成"
    return row["bill_no"]


def _cleanup_bill(bill_no: str):
    db.execute("DELETE FROM manual_review WHERE bill_no=%s AND reviewer LIKE %s",
               (bill_no, "测试%"))
    db.execute("DELETE FROM review_assignment WHERE bill_no=%s", (bill_no,))


def _release_all(bill_no: str):
    db.execute("UPDATE review_assignment SET released_at=now() "
               "WHERE bill_no=%s AND released_at IS NULL", (bill_no,))


def test_认领即锁定_他人不可认领不可代审():
    no = _pick_demo_bill()
    lin, chen = login("linjia"), login("chenfang")
    try:
        # 认领成功，出口带认领人真名（claim_label，不是会被打星的 reviewer）
        r = lin.post(f"/api/bills/{no}/claim")
        assert r.status_code == 200, r.text
        assert r.json()["claim_label"] == "林嘉"

        # 第二人认领 → 409。错误文案不带姓名（会被闭集合脱敏，与列表真名打架），
        # 认领人真名看列表的 claim_label。
        r2 = chen.post(f"/api/bills/{no}/claim")
        assert r2.status_code == 409 and "已被认领" in r2.json()["detail"]

        # 列表口径：mine 有它、none 没有
        mine = lin.get("/api/bills", params={"claim": "mine", "q": no}).json()["items"]
        pool = lin.get("/api/bills", params={"claim": "none", "q": no}).json()["items"]
        assert [x["bill_no"] for x in mine] == [no]
        assert pool == []
        assert mine[0]["claim_label"] == "林嘉"

        # 他人代审 → 409（这是锁定语义的闸，先于任何写入）
        run = db.query_one("SELECT run_id FROM review_run WHERE bill_no=%s "
                           "ORDER BY started_at DESC LIMIT 1", (no,))
        r3 = chen.post(f"/api/reviews/{run['run_id']}/manual",
                       json={"decision": "agree", "reviewer": "测试陈芳"})
        assert r3.status_code == 409 and "不可代审" in r3.json()["detail"]
    finally:
        _release_all(no)


def test_释放只认认领人():
    no = _pick_demo_bill()
    lin, chen = login("linjia"), login("chenfang")
    try:
        assert lin.post(f"/api/bills/{no}/claim").status_code == 200
        # 没认领的人释放 → 403；释放后再释放 → 409
        assert chen.post(f"/api/bills/{no}/release").status_code == 403
        assert lin.post(f"/api/bills/{no}/release").status_code == 200
        assert lin.post(f"/api/bills/{no}/release").status_code == 409
        # 释放后回到池子里，别人可以认领
        assert chen.post(f"/api/bills/{no}/claim").status_code == 200
    finally:
        _release_all(no)


def test_认领是审核岗动作_工程师403():
    no = _pick_demo_bill()
    eng = login("hetech")
    try:
        r = eng.post(f"/api/bills/{no}/claim")
        assert r.status_code == 403
    finally:
        _release_all(no)


def test_未认领提交复核自动补认领():
    """审了就是我的单：manual 提交在没有认领时自动写入认领留痕。"""
    no = _pick_demo_bill()
    lin = login("linjia")
    run = db.query_one("SELECT run_id FROM review_run WHERE bill_no=%s "
                       "ORDER BY started_at DESC LIMIT 1", (no,))
    try:
        # reviewer 用「测试」前缀署名——finally 只清这类行，真实留痕不碰
        r = lin.post(f"/api/reviews/{run['run_id']}/manual",
                     json={"decision": "agree", "reviewer": "测试林嘉"})
        assert r.status_code == 200, r.text
        act = db.query_one(
            "SELECT assignee FROM review_assignment "
            "WHERE bill_no=%s AND released_at IS NULL", (no,))
        assert act and act["assignee"] == "linjia", "提交复核应自动补认领"
    finally:
        _cleanup_bill(no)


def test_个人工作台三段与统计():
    lin, eng = login("linjia"), login("hetech")
    assert eng.get("/api/my/work").status_code == 403
    d = lin.get("/api/my/work").json()
    for k in ("me", "todo", "pool", "history", "stats"):
        assert k in d, f"缺 {k}"
    # 陈芳登录时看不到林嘉的待办——这就是「按账号匹配」
    chen = login("chenfang")
    d2 = chen.get("/api/my/work").json()
    assert d2["me"]["account"] == "chenfang"
    mine_nos = {x["bill_no"] for x in d["todo"]["items"]}
    chen_nos = {x["bill_no"] for x in d2["todo"]["items"]}
    assert not (mine_nos & chen_nos), "两个账号的待办不该重叠（认领唯一）"


def test_单位范围过滤():
    """账号挂了单位 → 工单与个人工作台只看该单位；解绑回全池。"""
    lin = login("linjia")
    admin = login("admin")
    target = db.query_one(
        "SELECT b.org_id FROM expense_bill b JOIN org o ON o.org_id=b.org_id "
        "WHERE b.is_demo=TRUE GROUP BY b.org_id HAVING count(*) > 0 "
        "ORDER BY count(*) LIMIT 1")
    try:
        r = admin.put("/api/iam/users/linjia",
                      json={"orgs": [target["org_id"]]})
        assert r.status_code == 200, r.text
        assert [o["id"] for o in r.json()["orgs"]] == [target["org_id"]]

        bills = lin.get("/api/bills", params={"size": 500}).json()
        orgs = {x["org_id"] for x in bills["items"]}
        assert orgs == {target["org_id"]}, f"应只见 {target['org_id']}，实际 {orgs}"
        assert "scope_note" in bills

        w = lin.get("/api/my/work").json()
        assert w["me"]["orgs"] == [target["org_id"]]
        assert w["me"]["account"] == "linjia"
    finally:
        # 解绑恢复全池（空列表=清空，不是 None=None 不改）
        admin.put("/api/iam/users/linjia", json={"orgs": []})
        assert db.scalar("SELECT count(*) FROM sys_user_org WHERE account='linjia'") == 0


def test_单位id不存在直接400():
    admin = login("admin")
    r = admin.put("/api/iam/users/linjia", json={"orgs": ["ORG-NOT-EXIST"]})
    assert r.status_code == 400
    assert db.scalar("SELECT count(*) FROM sys_user_org WHERE account='linjia'") == 0


def test_审核岗角色已带个人工作台菜单():
    roles = TestClient(app).get("/api/iam/roles").json()["items"]
    auditor = [r for r in roles if r["key"] == "auditor"][0]
    assert "mywork" in auditor["perms"]
    assert auditor["home"] == "/review/my"
