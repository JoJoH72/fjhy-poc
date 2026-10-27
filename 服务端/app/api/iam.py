# -*- coding: utf-8 -*-
"""系统 · 用户与角色（IAM）+ 登录会话。

**脱敏口径（这一组接口为什么单独说明）**
  账号名、角色名、部门属于**系统配置**，不是单据里的个人信息。
  通用脱敏中间件会把闭集合里的中文名（林嘉、陈芳…）一律替成「林*」，
  账号目录就没法用了，所以 main.py 对 /api/iam/*、/api/auth/* 跳过 scrub。
  但两条底线不变：
    · 手机号仍在这里按 M03 脱敏（接口只回 138****6021）
    · 密码摘要、会话 token 的完整值**永远不出接口**（token 只在 Cookie 里下发）

**服务端护栏（前端拦一遍不算数，真正的判断在这里）**
  1. 不能删除或停用当前登录账号——否则操作者当场把自己踢出去
  2. 至少保留一个启用状态的系统管理员，否则没人能再进这一页
  3. 内置角色不可删；角色还有成员不可删
  4. 系统管理员的权限矩阵只读（防止把「用户与角色」自己关掉）
"""
import re

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import db, iam_auth, mask
from . import op_log

router = APIRouter(prefix="/api", tags=["系统 · 用户与角色"])

SEED_ACCOUNTS = ["linjia", "chenfang", "hetech", "wangzong", "admin"]
DEMO_PWD_HASH = ("pbkdf2:60000:fjhy-demo-salt:"
                 "33b7da85b0e405ce7a3fc13a751483d6b7b128f62a3b3f8f2c6041e1a628a577")
SEED_ROLES = [
    # 2026-10-26 IA 重排（0044）：四角色按真实工作重排菜单，本体退出业务界面
    ("auditor", "审核岗", "复核 AI 结论、处置单据", "all",
     ["mywork", "ai-review", "workbench", "report", "trace"], True, "/review/my"),
    ("engineer", "规则管理员", "维护制度文件与审核规则：上传、审定、发布", "all",
     ["doc-lib", "rules", "publish", "trace"], True, "/asset/docs"),
    ("admin", "系统管理员", "平台、模型、账号与系统配置", "all",
     ["users", "llm", "ops", "startup", "knowstruct", "oplog"], True, "/sys/users"),
    ("leader", "领导查询", "只读查看整体质量与偏差", "all",
     ["dash", "workbench", "trace", "deviation"], True, "/dash"),
]
# 登录页角色列表的显示顺序：与最初一致（审核 → 知识工程师 → 系统管理员 → 领导查询）。
# 数据库列是 sys_role.sort_no，这里只用于「恢复默认」时写回。
SEED_ORDER = {"auditor": 1, "engineer": 2, "admin": 3, "leader": 4}

SEED_USERS = [
    ("linjia", "林嘉", "13800006021", "单据审核部", "auditor"),
    ("chenfang", "陈芳", "13900003357", "单据审核部", "auditor"),
    ("hetech", "何工", "13700008846", "知识运营部", "engineer"),
    ("wangzong", "王总", "13600001129", "集团领导", "leader"),
    ("admin", "系统管理", "13500000001", "信息中心", "admin"),
]

ACC_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{2,19}$")
SCOPES = ("all", "dept", "self")


# ── 输出整形 ───────────────────────────────────────────────────────────────
def _role_out(r: dict, members: int) -> dict:
    return {
        "key": r["role_key"], "name": r["role_name"], "desc": r["description"],
        "builtin": r["builtin"], "perms": r["perms"], "scope": r["data_scope"],
        "home": r["home"], "members": members,
    }


def _user_out(u: dict) -> dict:
    return {
        "id": u["account"], "name": u["user_name"], "account": u["account"],
        # 手机号按 M03 脱敏后再出接口；编辑时如果还是带 * 的掩码，服务端会保留原值
        "phone": mask.mask_mobile(u["phone_no"]) if u["phone_no"] else "",
        "dept": u["dept"], "roleKey": u["role_key"], "enabled": u["enabled"],
        "createdAt": u["created_at"].strftime("%Y-%m-%d"),
        "lastLogin": u["last_login_at"].strftime("%Y-%m-%d %H:%M") if u["last_login_at"] else None,
    }


def _users() -> list[dict]:
    return db.query(
        "SELECT account, user_name, phone_no, dept, role_key, enabled, created_at, last_login_at "
        "FROM sys_user ORDER BY created_at, account"
    )


def _roles() -> list[dict]:
    """角色清单。**顺序只看 sort_no**。

    原来按 `created_at, role_key` 排：四个预置角色是同一个事务插的，
    created_at 一模一样，排序退化成 role_key 字母序（admin 排第一），
    登录页的角色顺序就变成「系统管理员、审核岗、知识工程师、领导查询」。
    顺序这种事必须有一列显式的值，不能靠两个"恰好"的字段兜底。
    """
    return db.query(
        "SELECT r.*, (SELECT count(*) FROM sys_user u WHERE u.role_key = r.role_key) AS members "
        "FROM sys_role r ORDER BY r.sort_no, r.created_at, r.role_key"
    )


def _me_out(u: dict) -> dict:
    return {
        "account": u["account"], "userName": u["user_name"], "dept": u["dept"],
        "roleKey": u["role_key"], "roleName": u["role_name"], "home": u["home"],
        "scope": u["data_scope"],
    }


# ── 会话 ───────────────────────────────────────────────────────────────────
@router.post("/auth/login")
def login(body: dict = Body(...)):
    account = str(body.get("account") or "").strip()
    pwd = str(body.get("password") or "")
    if not account or not pwd:
        raise HTTPException(status_code=400, detail="请输入账号和密码")
    u = db.query_one("SELECT * FROM sys_user WHERE account = %s", (account,))
    # **账号不存在 / 停用 / 密码错误，统一回「账号或密码不正确」**：
    # 分开说就能枚举账号是否存在，等于把半个登录口送出去。
    if not u or not u["enabled"] or not iam_auth.verify_password(pwd, u["pwd_hash"]):
        op_log.log("login", "登录", actor=account,
                   actor_name=(u or {}).get("user_name", ""), ok=False,
                   detail="失败（账号/停用/密码错，统一口径不留哪个错）")
        raise HTTPException(status_code=401, detail="账号或密码不正确")

    iam_auth.destroy_expired()
    token = iam_auth.create_session(account)
    db.execute("UPDATE sys_user SET last_login_at = now() WHERE account = %s", (account,))
    full = db.query_one(
        "SELECT u.*, r.role_name, r.home, r.data_scope FROM sys_user u "
        "JOIN sys_role r ON r.role_key = u.role_key WHERE u.account = %s", (account,))
    op_log.log("login", "登录", actor=account, actor_name=full["user_name"],
               role_key=full["role_key"], detail=f"进入{full['role_name']}工作台")
    resp = JSONResponse(_me_out(full))
    resp.set_cookie(iam_auth.COOKIE_NAME, token, httponly=True, samesite="lax",
                    path="/", max_age=iam_auth.SESSION_TTL_HOURS * 3600)
    return resp


@router.post("/auth/logout")
def logout(request: Request):
    iam_auth.destroy_session(iam_auth.cookie_token(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(iam_auth.COOKIE_NAME, path="/")
    return resp


@router.get("/auth/me")
def me(request: Request):
    return _me_out(iam_auth.current_user(request))


# ── 角色 ───────────────────────────────────────────────────────────────────
@router.get("/iam/roles")
def list_roles():
    """角色清单（含成员数）。登录页也要用它渲染角色选项，所以不设鉴权。

    额外带一个 `sample_account`（该角色下第一个启用账号）：
    登录页点角色要能自动填账号，否则演示时每次都要记 5 个账号名。
    """
    # DISTINCT ON 取每个角色按账号排序的第一条，只要一次查询
    samples = {
        x["role_key"]: x
        for x in db.query(
            "SELECT DISTINCT ON (role_key) role_key, account, user_name FROM sys_user "
            "WHERE enabled ORDER BY role_key, account")
    }
    out = []
    for r in _roles():
        item = _role_out(r, r["members"])
        sm = samples.get(r["role_key"])
        item["sample_account"] = sm["account"] if sm else ""
        item["sample_user"] = sm["user_name"] if sm else ""
        out.append(item)
    return {"items": out}


@router.post("/iam/roles")
def create_role(request: Request, body: dict = Body(...)):
    iam_auth.current_admin(request)
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写角色名称")
    if db.scalar("SELECT 1 FROM sys_role WHERE role_name = %s", (name,)):
        raise HTTPException(status_code=400, detail=f"角色名「{name}」已存在")
    scope = body.get("scope") if body.get("scope") in SCOPES else "self"
    key = "r" + __import__("uuid").uuid4().hex[:12]
    perms = body.get("perms") or []
    db.execute(
        "INSERT INTO sys_role (role_key, role_name, description, data_scope, perms, builtin, home, sort_no) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, FALSE, %s, "
        "        (SELECT COALESCE(MAX(sort_no), 0) + 1 FROM sys_role))",
        (key, name, str(body.get("desc") or "").strip(), scope,
         __import__("json").dumps(perms, ensure_ascii=False), _first_home(perms)))
    r = db.query_one("SELECT * FROM sys_role WHERE role_key = %s", (key,))
    op_log.log_by_request(request, "iam", "新增角色", target=name,
                          detail=f"菜单 {len(perms)} 项 · 数据范围 {scope}")
    return _role_out(r, 0)


@router.put("/iam/roles/{role_key}")
def update_role(role_key: str, request: Request, body: dict = Body(...)):
    iam_auth.current_admin(request)
    if role_key == "admin":
        raise HTTPException(status_code=400, detail="系统管理员为内置超管，权限不可修改")
    r = db.query_one("SELECT * FROM sys_role WHERE role_key = %s", (role_key,))
    if not r:
        raise HTTPException(status_code=404, detail="角色不存在")
    name = str(body.get("name") or r["role_name"]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写角色名称")
    if db.scalar("SELECT 1 FROM sys_role WHERE role_name = %s AND role_key <> %s", (name, role_key)):
        raise HTTPException(status_code=400, detail=f"角色名「{name}」已存在")
    scope = body.get("scope") if body.get("scope") in SCOPES else r["data_scope"]
    perms = body.get("perms")
    if perms is None:
        perms = r["perms"]
    db.execute(
        "UPDATE sys_role SET role_name = %s, description = %s, data_scope = %s, perms = %s::jsonb "
        "WHERE role_key = %s",
        (name, str(body.get("desc") or "").strip(), scope,
         __import__("json").dumps(perms, ensure_ascii=False), role_key))
    op_log.log_by_request(request, "iam", "修改角色", target=name,
                          detail=f"菜单 {len(perms)} 项 · 数据范围 {scope}")
    return _role_out(db.query_one("SELECT * FROM sys_role WHERE role_key = %s", (role_key,)),
                     db.scalar("SELECT count(*) FROM sys_user WHERE role_key = %s", (role_key,)))


@router.delete("/iam/roles/{role_key}")
def delete_role(role_key: str, request: Request):
    iam_auth.current_admin(request)
    r = db.query_one("SELECT * FROM sys_role WHERE role_key = %s", (role_key,))
    if not r:
        raise HTTPException(status_code=404, detail="角色不存在")
    if r["builtin"]:
        raise HTTPException(status_code=400, detail="内置角色不可删除")
    n = db.scalar("SELECT count(*) FROM sys_user WHERE role_key = %s", (role_key,))
    if n:
        raise HTTPException(status_code=400, detail=f"还有 {n} 个账号属于该角色，先调整他们的角色")
    db.execute("DELETE FROM sys_role WHERE role_key = %s", (role_key,))
    op_log.log_by_request(request, "iam", "删除角色", target=r["role_name"])
    return {"ok": True, "deleted": role_key}


def _first_home(perms: list[str]) -> str:
    """新角色的落地页 = 它可见菜单里「左侧第一个」。

    **顺序必须与前端 navTree 完全一致**（总览 → 领域知识资产 → 报账单审核 → 系统）。
    原来这份手写顺序是「先审核组」，和侧栏顺序对不上，于是新角色登录会落到
    第 3 个菜单上——用户看到的就是「为什么总不是第一个」。
    """
    order = ["dash",
             "mywork", "ai-review", "workbench", "report", "trace", "deviation",
             "doc-lib", "rules", "publish",
             "users", "llm", "ops", "startup", "knowstruct", "oplog"]
    paths = {"mywork": "/review/my",
             "ai-review": "/review/ai-review", "workbench": "/review/bills",
             "report": "/review/report", "trace": "/review/trace", "deviation": "/review/deviation",
             "doc-lib": "/asset/docs", "rules": "/asset/rules-lib", "publish": "/asset/releases",
             "users": "/sys/users", "llm": "/sys/llm", "ops": "/sys/agents",
             "startup": "/sys/startup", "knowstruct": "/sys/knowstruct", "dash": "/dash",
             "oplog": "/sys/oplog"}
    for k in order:
        if k in perms:
            return paths[k]
    return "/dash"


# ── 账号 ───────────────────────────────────────────────────────────────────
def _orgs_of(accounts: list[str]) -> dict[str, list[dict]]:
    """批量取账号的单位绑定（审核专业组）。一次查询，别在循环里 N+1。"""
    if not accounts:
        return {}
    rows = db.query(
        """SELECT uo.account, o.org_id, o.name
             FROM sys_user_org uo JOIN org o ON o.org_id = uo.org_id
            WHERE uo.account = ANY(%s) ORDER BY o.org_id""", (accounts,))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["account"], []).append({"id": r["org_id"], "name": r["name"]})
    return out


def _sync_user_orgs(account: str, org_ids) -> None:
    """整组替换账号的单位绑定。org_ids=None = 本次不改（编辑时没带这个字段）；
    空列表 = 清空（解除单位限制，回全池）。org_id 不存在直接 400，不静默丢。"""
    if org_ids is None:
        return
    ids = [str(x) for x in (org_ids or [])]
    for oid in ids:
        if not db.scalar("SELECT 1 FROM org WHERE org_id = %s", (oid,)):
            raise HTTPException(400, detail="单位不存在：%s" % oid)
    with db.tx() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sys_user_org WHERE account = %s", (account,))
        for oid in dict.fromkeys(ids):   # 去重保序，防重复提交写两行
            cur.execute("INSERT INTO sys_user_org (account, org_id) VALUES (%s, %s)",
                        (account, oid))


@router.get("/iam/orgs")
def list_orgs(request: Request):
    """核算主体清单（用户编辑器的单位多选用）。org 表里演示单位带【演示】前缀，
    名字自说明，不在接口层再过滤——过滤口径写在名字里比藏在代码里诚实。"""
    iam_auth.current_admin(request)
    return {"items": db.query(
        "SELECT org_id AS id, name FROM org ORDER BY org_id")}


@router.get("/iam/users")
def list_users(request: Request):
    iam_auth.current_admin(request)
    items = [_user_out(u) for u in _users()]
    org_map = _orgs_of([it["account"] for it in items])
    for it in items:
        it["orgs"] = org_map.get(it["account"], [])
    return {"items": items}


def _check_account_guard(current: dict, account: str, enabled: bool | None = None) -> None:
    """删号 / 停用的两条护栏。**必须放在服务端**，前端的拦截只能算提示。"""
    if account == current["account"]:
        raise HTTPException(status_code=400, detail="不能对自己执行这个操作")
    if enabled is False:
        # 目标如果是最后一个启用的管理员，停掉就没人能再进这一页
        n = db.scalar(
            "SELECT count(*) FROM sys_user WHERE role_key = 'admin' AND enabled AND account <> %s",
            (account,))
        target_is_admin = db.scalar(
            "SELECT 1 FROM sys_user WHERE account = %s AND role_key = 'admin' AND enabled", (account,))
        if target_is_admin and n == 0:
            raise HTTPException(status_code=400, detail="至少要保留一个启用的系统管理员账号")


@router.post("/iam/users")
def create_user(request: Request, body: dict = Body(...)):
    iam_auth.current_admin(request)
    name = str(body.get("name") or "").strip()
    account = str(body.get("account") or "").strip()
    pwd = str(body.get("password") or "")
    if not name:
        raise HTTPException(status_code=400, detail="请填写姓名")
    if not ACC_RE.match(account):
        raise HTTPException(status_code=400, detail="账号需以字母开头，3–20 位字母/数字/._-")
    role_key = str(body.get("roleKey") or "")
    if not db.scalar("SELECT 1 FROM sys_role WHERE role_key = %s", (role_key,)):
        raise HTTPException(status_code=400, detail="请选择有效角色")
    if len(pwd) < 6:
        raise HTTPException(status_code=400, detail="初始密码至少 6 位")
    if db.scalar("SELECT 1 FROM sys_user WHERE account = %s", (account,)):
        raise HTTPException(status_code=400, detail=f"账号 {account} 已存在")
    phone = str(body.get("phone") or "").replace(" ", "")
    if phone and not re.fullmatch(r"\d{6,15}", phone):
        raise HTTPException(status_code=400, detail="手机号格式不对")
    db.execute(
        "INSERT INTO sys_user (account, user_name, phone_no, dept, role_key, enabled, pwd_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (account, name, phone, str(body.get("dept") or "").strip(), role_key,
         bool(body.get("enabled", True)), iam_auth.hash_password(pwd)))
    _sync_user_orgs(account, body.get("orgs"))
    op_log.log_by_request(request, "iam", "新增账号", target=f"{name}（{account}）",
                          detail=f"角色 {db.scalar('SELECT role_name FROM sys_role WHERE role_key = %s', (role_key,))}")
    out = _user_out(db.query_one("SELECT * FROM sys_user WHERE account = %s", (account,)))
    out["orgs"] = _orgs_of([account]).get(account, [])
    return out


@router.put("/iam/users/{account}")
def update_user(account: str, request: Request, body: dict = Body(...)):
    me = iam_auth.current_admin(request)
    u = db.query_one("SELECT * FROM sys_user WHERE account = %s", (account,))
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    enabled = body.get("enabled")
    if enabled is False and u["enabled"]:
        _check_account_guard(me, account, enabled=False)

    name = str(body.get("name") or u["user_name"]).strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写姓名")
    role_key = str(body.get("roleKey") or u["role_key"])
    if not db.scalar("SELECT 1 FROM sys_role WHERE role_key = %s", (role_key,)):
        raise HTTPException(status_code=400, detail="请选择有效角色")

    # 带 * 的掩码原样回传 = 没改手机号。否则就会把「138****6021」当成新号码存进去。
    phone = body.get("phone")
    if phone is None or "*" in str(phone):
        phone = u["phone_no"]
    else:
        phone = str(phone).replace(" ", "")
        if phone and not re.fullmatch(r"\d{6,15}", phone):
            raise HTTPException(status_code=400, detail="手机号格式不对")

    # dept 与 name/phone 同款回退：没带 = 不改，空串 = 清空。
    # 原来这里没带也会清空——「只想改单位绑定」的局部更新会把部门悄悄抹掉。
    dept = body.get("dept")
    dept = u["dept"] if dept is None else str(dept).strip()

    db.execute(
        "UPDATE sys_user SET user_name = %s, phone_no = %s, dept = %s, role_key = %s, enabled = %s "
        "WHERE account = %s",
        (name, phone, dept, role_key,
         u["enabled"] if enabled is None else bool(enabled), account))
    _sync_user_orgs(account, body.get("orgs"))
    # 停用/启用与信息修改分开口径——管理员翻日志时关心的是「谁被停了」
    if enabled is not None and bool(enabled) != u["enabled"]:
        op_log.log_by_request(request, "iam", "启用账号" if enabled else "停用账号",
                              target=f"{name}（{account}）")
    else:
        op_log.log_by_request(request, "iam", "修改账号", target=f"{name}（{account}）",
                              detail=f"角色 {role_key}")
    out = _user_out(db.query_one("SELECT * FROM sys_user WHERE account = %s", (account,)))
    out["orgs"] = _orgs_of([account]).get(account, [])
    return out


@router.delete("/iam/users/{account}")
def delete_user(account: str, request: Request):
    me = iam_auth.current_admin(request)
    u = db.query_one("SELECT * FROM sys_user WHERE account = %s", (account,))
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    _check_account_guard(me, account, enabled=False if u["role_key"] == "admin" and u["enabled"] else None)
    db.execute("DELETE FROM sys_user WHERE account = %s", (account,))
    op_log.log_by_request(request, "iam", "删除账号",
                          target=f"{u['user_name']}（{account}）")
    return {"ok": True, "deleted": account}


@router.post("/iam/users/{account}/password")
def reset_password(account: str, request: Request, body: dict = Body(...)):
    iam_auth.current_admin(request)
    pwd = str(body.get("password") or "")
    if len(pwd) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if not db.scalar("SELECT 1 FROM sys_user WHERE account = %s", (account,)):
        raise HTTPException(status_code=404, detail="用户不存在")
    db.execute("UPDATE sys_user SET pwd_hash = %s WHERE account = %s",
               (iam_auth.hash_password(pwd), account))
    # 改了密码就把这个账号的旧会话全部踢下线，否则旧 Cookie 还能用 12 小时
    db.execute("DELETE FROM sys_session WHERE account = %s", (account,))
    op_log.log_by_request(request, "iam", "重置密码", target=account,
                          detail="旧会话已全部下线")
    return {"ok": True}


# ── 恢复默认（演示专用）────────────────────────────────────────────────────
@router.post("/iam/reset")
def reset(request: Request):
    iam_auth.current_admin(request)
    """把角色与账号恢复成出厂状态。自建角色、自建账号会丢；这是**演示复位**，
    不是生产功能——生产里不会有「一键把所有人密码重置成 demo-1234」这种操作。"""
    import json
    with db.tx() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sys_user WHERE NOT (account = ANY(%s))", (SEED_ACCOUNTS,))
        cur.execute("DELETE FROM sys_role WHERE builtin = FALSE")
        for key, name, desc, scope, perms, builtin, home in SEED_ROLES:
            cur.execute(
                "INSERT INTO sys_role (role_key, role_name, description, data_scope, perms, builtin, home, sort_no) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
                "ON CONFLICT (role_key) DO UPDATE SET role_name = EXCLUDED.role_name, "
                "description = EXCLUDED.description, data_scope = EXCLUDED.data_scope, "
                "perms = EXCLUDED.perms, home = EXCLUDED.home, sort_no = EXCLUDED.sort_no",
                (key, name, desc, scope, json.dumps(perms), builtin, home,
                 SEED_ORDER.get(key, 100)))
        for account, name, phone, dept, role_key in SEED_USERS:
            cur.execute(
                "INSERT INTO sys_user (account, user_name, phone_no, dept, role_key, enabled, pwd_hash) "
                "VALUES (%s, %s, %s, %s, %s, TRUE, %s) "
                "ON CONFLICT (account) DO UPDATE SET user_name = EXCLUDED.user_name, "
                "phone_no = EXCLUDED.phone_no, dept = EXCLUDED.dept, role_key = EXCLUDED.role_key, "
                "enabled = TRUE, pwd_hash = EXCLUDED.pwd_hash",
                (account, name, phone, dept, role_key, DEMO_PWD_HASH))
    op_log.log_by_request(request, "iam", "恢复默认", target="角色与账号",
                          detail="演示复位：自建角色与账号清空，内置五账号复原")
    return {"ok": True, "users": len(SEED_USERS), "roles": len(SEED_ROLES)}
