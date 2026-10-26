# -*- coding: utf-8 -*-
"""用户与角色（IAM）：登录、会话、账号与角色的增删改查、以及四条服务端护栏。

**这些测试跑在真库上**（和其余测试一样），所以：
  · 只创建带 `t_` 前缀的临时账号，用完在 finally 里删掉
  · 不调用 /api/iam/reset —— 它是演示复位，会把自建账号一并清掉
  · 不修改内置角色（admin 的权限本来就只读，审核岗改了会影响演示）

重点不在「增删改查能跑通」，而在**这几条护栏不能只活在前端**：
  未登录 401 / 非管理员 403 / 不能删自己 / 内置角色不可删 / 超管权限不可改。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

TMP_ACC = "t_iam_probe"
TMP_PWD = "probe-1234"


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    """本模块要验的就是鉴权本身，所以把 conftest 关掉的开关打回 on。"""
    from app import config
    monkeypatch.setattr(config, "AUTH_MODE", "on")


@pytest.fixture()
def anon():
    """未登录的客户端。Cookie 每个用例独立，避免互相污染。"""
    return TestClient(app)


def login(client, account="admin", pwd="demo-1234"):
    r = client.post("/api/auth/login", json={"account": account, "password": pwd})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture()
def admin():
    c = TestClient(app)
    login(c)
    yield c
    c.post("/api/auth/logout")


def _cleanup():
    """清掉本次用例建的临时账号/角色。

    注意 LIKE 里的 % 必须走参数绑定：直接写在 SQL 里会被 psycopg2 当成占位符，
    报的是 "tuple index out of range"，看着像数据库坏了。
    """
    from app import db
    db.execute("DELETE FROM sys_user WHERE account LIKE %s", ("t\\_%",))
    db.execute("DELETE FROM sys_role WHERE role_key LIKE %s", ("t\\_%",))


def test_未登录一律401(anon):
    for path in ("/api/iam/users", "/api/auth/me", "/api/agents"):
        r = anon.get(path)
        assert r.status_code == 401, f"{path} 应当 401，实际 {r.status_code}"


def test_角色清单是公开的_登录页要用(anon):
    r = anon.get("/api/iam/roles")
    assert r.status_code == 200
    keys = [x["key"] for x in r.json()["items"]]
    assert "admin" in keys and "auditor" in keys
    # 登录页点角色要能自动填账号
    assert any(x.get("sample_account") for x in r.json()["items"])


def test_角色清单按固定顺序返回(anon):
    """顺序是产品要求：审核 → 知识工程师 → 系统管理员 → 领导查询。

    2026-10-25 出过一次：四个角色同事务插入，created_at 完全相同，
    排序退化成 role_key 字母序，admin（系统管理员）冲到第一，
    登录页的顺序就变了。现在按 sys_role.sort_no 排，这条用例钉住它。
    """
    keys = [x["key"] for x in anon.get("/api/iam/roles").json()["items"]]
    assert keys[:4] == ["auditor", "engineer", "admin", "leader"], keys


def test_密码错误不放行(anon):
    r = anon.post("/api/auth/login", json={"account": "admin", "password": "wrong-pwd"})
    assert r.status_code == 401
    # 账号不存在与密码错误必须是同一句话，否则等于送人一个账号枚举接口
    r2 = anon.post("/api/auth/login", json={"account": "no_such_user", "password": "x"})
    assert r2.status_code == 401
    assert r.json()["detail"] == r2.json()["detail"]


def test_登录成功下发HttpOnly会话(anon):
    r = login(anon)
    assert r.json()["roleKey"] == "admin"
    raw = r.headers.get("set-cookie", "")
    assert "fjhy_sid=" in raw and "HttpOnly" in raw
    me = anon.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["account"] == "admin"


def test_账号清单不返回完整手机号(admin):
    items = admin.get("/api/iam/users").json()["items"]
    assert items
    for u in items:
        assert "*" not in u["name"], "账号名不该被脱敏成星号（IAM 接口跳过字段脱敏）"
        if u["phone"]:
            assert "*" in u["phone"], "手机号必须按 M03 脱敏后才出接口"
    admin_row = next(u for u in items if u["account"] == "admin")
    assert admin_row["phone"].startswith("135") and admin_row["phone"].endswith("0001")


def test_建号_改号_删号(admin):
    _cleanup()
    try:
        r = admin.post("/api/iam/users", json={
            "name": "探针", "account": TMP_ACC, "phone": "13900002222",
            "dept": "测试部", "roleKey": "auditor", "enabled": True, "password": TMP_PWD,
        })
        assert r.status_code == 200, r.text

        # 重复账号要被拒
        dup = admin.post("/api/iam/users", json={
            "name": "探针2", "account": TMP_ACC, "roleKey": "auditor", "password": TMP_PWD})
        assert dup.status_code == 400

        # 改姓名与角色
        up = admin.put(f"/api/iam/users/{TMP_ACC}",
                       json={"name": "探针改", "roleKey": "leader"})
        assert up.status_code == 200 and up.json()["name"] == "探针改"
        assert up.json()["roleKey"] == "leader"

        # 掩码手机号原样回传 = 不改动
        keep = admin.put(f"/api/iam/users/{TMP_ACC}", json={"phone": "139****2222"})
        assert keep.status_code == 200 and keep.json()["phone"].endswith("2222")

        # 新账号能用初始密码登录
        c2 = TestClient(app)
        assert c2.post("/api/auth/login",
                       json={"account": TMP_ACC, "password": TMP_PWD}).status_code == 200

        assert admin.delete(f"/api/iam/users/{TMP_ACC}").status_code == 200
        assert admin.get("/api/iam/users").json()["items"]
    finally:
        _cleanup()


def test_不能删自己也不能停用自己(admin):
    r = admin.delete("/api/iam/users/admin")
    assert r.status_code == 400 and "自己" in r.json()["detail"]
    r2 = admin.put("/api/iam/users/admin", json={"enabled": False})
    assert r2.status_code == 400


def test_非管理员不能碰用户角色(anon):
    c = TestClient(app)
    login(c, "linjia")
    assert c.get("/api/iam/users").status_code == 403
    assert c.post("/api/iam/users", json={"name": "x", "account": "xx1",
                                         "roleKey": "auditor"}).status_code == 403
    c.post("/api/auth/logout")


def test_内置角色不可删_超管权限不可改(admin):
    assert admin.delete("/api/iam/roles/admin").status_code == 400
    r = admin.put("/api/iam/roles/admin", json={"perms": []})
    assert r.status_code == 400 and "超管" in r.json()["detail"]


def test_新建角色后能删_有成员时拒删(admin):
    _cleanup()
    try:
        r = admin.post("/api/iam/roles", json={"name": "t_临时角色", "desc": "测试",
                                              "scope": "self", "perms": ["dash"]})
        assert r.status_code == 200, r.text
        key = r.json()["key"]
        assert key.startswith("t_") or key.startswith("r")
        # 挂一个成员进去
        admin.post("/api/iam/users", json={
            "name": "角色探针", "account": TMP_ACC, "roleKey": key, "password": TMP_PWD})
        blocked = admin.delete(f"/api/iam/roles/{key}")
        assert blocked.status_code == 400 and "账号属于该角色" in blocked.json()["detail"]
        admin.delete(f"/api/iam/users/{TMP_ACC}")
        assert admin.delete(f"/api/iam/roles/{key}").status_code == 200
    finally:
        _cleanup()


def test_登出后会话立刻失效(anon):
    login(anon)
    assert anon.get("/api/iam/users").status_code == 200
    anon.post("/api/auth/logout")
    assert anon.get("/api/iam/users").status_code == 401


def test_停用账号后已发出的会话立刻失效(admin):
    """停用要立即生效，不能等 Cookie 自然过期（12 小时）。"""
    _cleanup()
    try:
        admin.post("/api/iam/users", json={
            "name": "停用探针", "account": TMP_ACC, "roleKey": "auditor", "password": TMP_PWD})
        c2 = TestClient(app)
        login(c2, TMP_ACC, TMP_PWD)
        assert c2.get("/api/iam/roles").status_code == 200
        admin.put(f"/api/iam/users/{TMP_ACC}", json={"enabled": False})
        # 角色清单是公开接口，换成需要登录的接口来验会话
        assert c2.get("/api/iam/users").status_code == 401
    finally:
        _cleanup()
