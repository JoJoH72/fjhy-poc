# -*- coding: utf-8 -*-
"""登录密码与会话（IAM 的基础设施）。

**三件这里刻意不做的事**
  1. 不自己发明摘要：不用 sha256（快哈希，离线撞库成本太低）。
  2. 不把 token 放内存：后端重启全员掉线、多进程对不上号。
  3. 不在登录接口里返回密码摘要或完整手机号：
     摘要是**单向的**，回显就等于把撞库材料送出去。

密码格式：`pbkdf2:<iterations>:<salt>:<hex>`
会话：HttpOnly Cookie `fjhy_sid`，服务端 sys_session 表，12 小时过期。
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import config, db

SESSION_TTL_HOURS = 12
COOKIE_NAME = "fjhy_sid"
_ITERATIONS = 60000


def hash_password(pwd: str, salt: str | None = None) -> str:
    """生成摘要。盐不传就现生成——新密码不许复用旧盐，否则同密码同摘要。"""
    s = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), s.encode(), _ITERATIONS)
    return "pbkdf2:%d:%s:%s" % (_ITERATIONS, s, h.hex())


def verify_password(pwd: str, stored: str) -> bool:
    """校验。**结构不合法的一律算失败**，不许因为解析异常把异常当通过。"""
    try:
        scheme, iters, salt, expect = stored.split(":", 3)
        if scheme != "pbkdf2":
            return False
        h = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode(), int(iters))
        # 常量时间比较：逐位比对会泄露摘要前缀对不对（时序侧信道）
        return secrets.compare_digest(h.hex(), expect)
    except (ValueError, AttributeError):
        return False


def create_session(account: str) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    db.execute("INSERT INTO sys_session (token, account) VALUES (%s, %s)", (token, account))
    return token


def destroy_session(token: str) -> None:
    if token:
        db.execute("DELETE FROM sys_session WHERE token = %s", (token,))


def destroy_expired() -> None:
    db.execute("DELETE FROM sys_session WHERE created_at < now() - interval '%s hours'" % SESSION_TTL_HOURS)


def cookie_token(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME) or ""


def user_by_token(token: str) -> dict | None:
    """token → 用户。**过期、停用、角色被删，任何一种都算没登录。**
    查一次库而不是信任 Cookie 里的账号：账号被停用后，
    已发出的 Cookie 必须立刻失效，不能等 12 小时自然过期。
    """
    if not token:
        return None
    # **不能在这里用 % 拼 SQL**：语句里已经有给 psycopg2 的 %s 占位符，
    # 再用 % 运算符会把它一起吃掉（实测报 "not enough arguments for format string"）。
    # 过期小时数是内部常量，用 f-string 拼进去，占位符留给驱动。
    row = db.query_one(
        f"""
        SELECT u.account, u.user_name, u.dept, u.phone_no, u.role_key, u.enabled,
               u.last_login_at, r.role_name, r.home, r.data_scope, r.perms
          FROM sys_session s
          JOIN sys_user u ON u.account = s.account
          JOIN sys_role r ON r.role_key = u.role_key
         WHERE s.token = %s AND s.created_at > now() - interval '{SESSION_TTL_HOURS} hours'
        """,
        (token,),
    )
    if not row or not row["enabled"]:
        return None
    return row


def current_user(request: Request) -> dict:
    """给业务 handler 用的依赖：没登录一律 401，而不是匿名放行。"""
    u = user_by_token(cookie_token(request))
    if not u:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return u


def current_admin(request: Request) -> dict:
    """只有系统管理员能改用户与角色。普通角色拿到 403，不是 401——
    401 会把人弹去登录页，而他已经登录了，只是权限不够。"""
    u = current_user(request)
    if u["role_key"] != "admin":
        raise HTTPException(status_code=403, detail="需要系统管理员权限")
    return u


class SessionMiddleware:
    """登录会话中间件：/api/* 一律要登录，白名单除外。

    为什么放在中间件而不是给每个接口加依赖：
      handler 会越写越多，总有人忘；忘的那个接口就是「不登录也能读业务数据」。
      白名单只有三个：登录、登出、角色选项（登录页要用它渲染角色列表）。

    401 的响应体固定，不回显路径与参数——按路径试探接口的行为不该被鼓励。
    """

    PUBLIC = ("/api/auth/login", "/api/auth/logout", "/api/iam/roles")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if config.AUTH_MODE != "on":
            await self.app(scope, receive, send)
            return
        if not path.startswith("/api/") or path in self.PUBLIC:
            await self.app(scope, receive, send)
            return
        if not user_by_token(_token_from_scope(scope)):
            from starlette.responses import JSONResponse
            resp = JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _token_from_scope(scope) -> str:
    for k, v in scope.get("headers") or []:
        if k.lower() != b"cookie":
            continue
        for part in v.decode("latin-1").split(";"):
            name, _, val = part.strip().partition("=")
            if name == COOKIE_NAME:
                return val
    return ""
