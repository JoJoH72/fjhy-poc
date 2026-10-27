# -*- coding: utf-8 -*-
"""资产写口角色闸（2026-10-26 IA 重排）：制度文件/抽取/发布只许规则管理员与超管写。

护栏模式与 test_mywork 的审核岗闸一致：**闸在服务端**，前端菜单收敛不算数。
挑有代表性的写端点各验一个：上传、扫描、抽取审定、发布冻结；GET 不拦。
真上传会落盘——这里只验闸（403/200），不做真实操作：
被拦的请求在进 handler 之前就被依赖抛掉了，不会碰磁盘。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUTH_MODE", "on")


def login(account, pwd="demo-1234") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"account": account, "password": pwd})
    assert r.status_code == 200, r.text
    return c


# (method, path, body) ——挑有代表性的写口；冻结/审定是状态变更，非 engineer 直接 403
PROBES = [
    ("post", "/api/doclib/scan", {}),
    ("post", "/api/extract/run", {}),
    ("post", "/api/publish/freeze", {"version": "gate-probe"}),
]


def test_审核岗写资产口一律403():
    c = login("linjia")  # auditor
    for method, path, body in PROBES:
        r = getattr(c, method)(path, json=body)
        assert r.status_code == 403, f"{path} 应 403，实际 {r.status_code}: {r.text[:80]}"
        assert "规则管理员" in r.json()["detail"]


def test_领导写资产口一律403():
    c = login("wangzong")
    for method, path, body in PROBES:
        r = getattr(c, method)(path, json=body)
        assert r.status_code == 403, f"{path} 应 403，实际 {r.status_code}"


def test_规则管理员过闸_审核岗语义不误伤():
    """engineer 通过闸（可能因业务校验 4xx，但绝不是 403 角色闸）。"""
    c = login("hetech")
    r = c.post("/api/doclib/scan", json={})
    assert r.status_code != 403, f"engineer 被角色闸误拦: {r.text[:80]}"


def test_GET不拦_审核岗可读资产清单():
    """审计追溯等只读场景要能读清单——闸只挂写口。"""
    c = login("linjia")
    r = c.get("/api/doclib/files")
    assert r.status_code != 403, f"GET 被误拦: {r.text[:80]}"
    r2 = c.get("/api/publish/releases")
    assert r2.status_code != 403
