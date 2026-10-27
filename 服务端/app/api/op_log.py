# -*- coding: utf-8 -*-
"""系统 · 操作日志（2026-10-27 M2 管理员新功能）。

动作必留痕（谁、何时、对什么做了什么）的查询面 + 写入助手。
数据落 sys_op_log（0045）：只 INSERT 不删改——审计性质，
业务数据可清理而台账不可删（与 agent_signal_log 同一口径）。

写谁：登录/登出、用户与角色的增删改停、密码重置、恢复默认、
发布（冻结/签字/切当前/回滚）、规则域（裁决/审定/采纳）。
这些都是「改系统行为」的动作；
查询类接口不记（记了只会把真动作淹没）。

detail 只放人话摘要，不放敏感值：密码、Key、手机号明文一律不进日志。
"""
from fastapi import APIRouter, Request

from .. import db, iam_auth

router = APIRouter(prefix="/api", tags=["系统 · 操作日志"])

CATEGORIES = ("login", "iam", "publish", "review", "rule")


def log(category: str, action: str, *, actor: str = "", actor_name: str = "",
        role_key: str = "", target: str = "", detail: str = "",
        ok: bool = True, ip: str = "") -> None:
    """写一条操作日志。失败不抛——日志写挂不能把业务动作一起拖死。"""
    try:
        db.execute(
            "INSERT INTO sys_op_log (actor, actor_name, role_key, category, action, target, detail, ok, ip) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (actor, actor_name, role_key, category, action, target, detail[:500], ok, ip[:45]))
    except Exception:   # noqa: BLE001
        pass


def log_by_request(request: Request, category: str, action: str, **kw) -> None:
    """从会话补 actor/角色/IP 的便捷入口。未登录（如登录失败）时 actor 由调用方给。"""
    u = None
    try:
        u = iam_auth.current_user(request)
    except Exception:   # noqa: BLE001
        pass
    if u:
        kw.setdefault("actor", u["account"])
        kw.setdefault("actor_name", u.get("user_name", ""))
        kw.setdefault("role_key", u.get("role_key", ""))
    kw.setdefault("ip", request.client.host if request.client else "")
    log(category, action, **kw)


def _out(r: dict) -> dict:
    return {
        "id": r["id"],
        "at": r["acted_at"].strftime("%Y-%m-%d %H:%M:%S") if r["acted_at"] else "",
        "actor": r["actor"],
        "actor_name": r["actor_name"] or r["actor"],
        "role": r["role_key"] or "",
        "category": r["category"],
        "action": r["action"],
        "target": r["target"],
        "detail": r["detail"],
        "ok": r["ok"],
        "ip": r["ip"],
    }


CATEGORY_CN = {"login": "登录", "iam": "用户与角色", "publish": "发布",
               "review": "复核", "rule": "规则"}


@router.get("/op-log")
def list_op_log(request: Request, category: str = "", actor: str = "",
                q: str = "", page: int = 1, size: int = 20):
    """操作日志（仅系统管理员）。服务端分页——台账只增不减，
    全量拉前端分页迟早拖死（E5 扛规模判据）。"""
    iam_auth.current_admin(request)
    conds, params = ["1=1"], []
    if category in CATEGORIES:
        conds.append("category = %s"); params.append(category)
    if actor:
        conds.append("actor = %s"); params.append(actor)
    if q:
        conds.append("(actor ILIKE %s OR action ILIKE %s OR target ILIKE %s OR detail ILIKE %s)")
        params += [f"%{q}%"] * 4
    where = " AND ".join(conds)
    total = db.scalar(f"SELECT count(*) FROM sys_op_log WHERE {where}", tuple(params)) or 0
    size = min(max(int(size), 1), 100)
    page = max(int(page), 1)
    rows = db.query(
        f"SELECT * FROM sys_op_log WHERE {where} ORDER BY id DESC LIMIT %s OFFSET %s",
        tuple(params) + (size, (page - 1) * size))
    # 分类下拉的选项（带计数）
    by_cat = {r["category"]: r["n"] for r in
              db.query("SELECT category, count(*) AS n FROM sys_op_log GROUP BY category")}
    return {
        "items": [_out(r) for r in rows],
        "total": total,
        "categories": [{"key": k, "label": CATEGORY_CN.get(k, k), "n": by_cat.get(k, 0)}
                       for k in CATEGORIES],
    }
