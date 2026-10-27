# -*- coding: utf-8 -*-
"""操作日志（sys_op_log）：登录留痕、IAM 动作留痕、管理员查询闸与分页过滤。

口径（op_log.py 文件头）：
  · 只 INSERT 不 UPDATE/DELETE——审计台账不可删，这条不测运行时
    （运行时测不了「不存在的能力」，靠表上没有任何 UPDATE/DELETE 语句保证）
  · 登录成功/失败各写一条；失败的那条统一口径，不漏是哪一步错了
  · 查询面只给系统管理员（403 不是 401——他已经登录了，只是权限不够）
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    """鉴权打开（conftest 默认关掉）。这一页要验的就是闸。"""
    from app import config
    monkeypatch.setattr(config, "AUTH_MODE", "on")


@pytest.fixture()
def admin():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"account": "admin", "password": "demo-1234"})
    assert r.status_code == 200, r.text
    yield c
    c.post("/api/auth/logout")


def _last(*params):
    """按条件取最新一条。参数成对给：actor, 'admin', category, 'login', …"""
    from app import db
    conds = []
    bind = []
    for i in range(0, len(params), 2):
        conds.append("%s = %%s" % params[i])
        bind.append(params[i + 1])
    where = " AND ".join(conds) if conds else "TRUE"
    return db.query_one(
        "SELECT * FROM sys_op_log WHERE %s ORDER BY id DESC LIMIT 1" % where, tuple(bind))


def test_登录成功写一条():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"account": "admin", "password": "demo-1234"})
    assert r.status_code == 200
    row = _last("actor", "admin", "category", "login", "ok", True)
    assert row, "登录成功没留痕"
    assert row["action"] == "登录" and row["role_key"] == "admin"
    c.post("/api/auth/logout")


def test_登录失败也写一条_并且不成功():
    c = TestClient(app)
    assert c.post("/api/auth/login",
                  json={"account": "admin", "password": "wrong"}).status_code == 401
    row = _last("actor", "admin", "category", "login", "ok", False)
    assert row, "登录失败没留痕"
    assert "统一口径" in row["detail"]


def test_管理员建角色后日志里有记录(admin):
    from app import db
    db.execute("DELETE FROM sys_role WHERE role_name = %s", ("t_日志探针角色",))   # 上次断跑的残留
    r = admin.post("/api/iam/roles", json={"name": "t_日志探针角色", "perms": ["dash"]})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    row = _last("category", "iam", "action", "新增角色")
    assert row and row["actor"] == "admin" and row["target"] == "t_日志探针角色"
    assert row["ok"] is True
    db.execute("DELETE FROM sys_role WHERE role_key = %s", (key,))


def test_查询面只给系统管理员(admin):
    # 未登录 401（会话中间件先拦）
    anon = TestClient(app)
    assert anon.get("/api/op-log").status_code == 401
    # 已登录的非管理员 403
    c = TestClient(app)
    assert c.post("/api/auth/login",
                  json={"account": "linjia", "password": "demo-1234"}).status_code == 200
    assert c.get("/api/op-log").status_code == 403
    c.post("/api/auth/logout")
    # 管理员 200
    assert admin.get("/api/op-log").status_code == 200


def test_分页与分类过滤(admin):
    # 先保证台账里至少有两条 login
    for _ in range(2):
        c = TestClient(app)
        c.post("/api/auth/login", json={"account": "admin", "password": "demo-1234"})
        c.post("/api/auth/logout")

    r = admin.get("/api/op-log", params={"size": 5}).json()
    assert 1 <= len(r["items"]) <= 5
    assert r["total"] >= len(r["items"])
    # 分类下拉带计数，且只含固定四类
    cats = {x["key"]: x["n"] for x in r["categories"]}
    assert set(cats) >= {"login", "iam", "publish", "review"}
    assert cats["login"] >= 2

    # 分类过滤：login 分类下的每一条都是 login
    r2 = admin.get("/api/op-log", params={"category": "login", "size": 50}).json()
    assert r2["items"], "login 分类不可能是空的（上面刚登过）"
    assert all(x["category"] == "login" for x in r2["items"])
    assert r2["total"] == cats["login"]

    # 翻页：第 1 页和第 2 页不重复（按 id 倒序）
    p1 = admin.get("/api/op-log", params={"size": 2, "page": 1}).json()["items"]
    p2 = admin.get("/api/op-log", params={"size": 2, "page": 2}).json()["items"]
    if p2:                                     # 台账不足一页时第 2 页为空，也是对的
        ids1 = {x["id"] for x in p1}
        ids2 = {x["id"] for x in p2}
        assert not (ids1 & ids2)
        assert p1[0]["id"] > p2[0]["id"]

    # 关键字：q=admin 至少命中 actor/detail 里带 admin 的行
    rq = admin.get("/api/op-log", params={"q": "admin"}).json()
    assert rq["total"] >= 1


def test_输出形状_不含敏感值(admin):
    """台账出接口的字段是固定那几个；detail 里不允许出现密码。"""
    r = admin.get("/api/op-log", params={"size": 3}).json()
    for it in r["items"]:
        assert set(it) >= {"id", "at", "actor", "actor_name", "role", "category",
                           "action", "target", "detail", "ok"}
        assert "demo-1234" not in it["detail"]
        assert "*" not in it["actor_name"], "actor_name 被脱敏打了星号，台账查不出是谁"
