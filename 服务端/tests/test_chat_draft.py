# -*- coding: utf-8 -*-
"""对话式起草（B-P1-A，2026-10-28 Wave 4）。

测试全部确定性：模型调用 mock 掉（不出网、不花钱），只验服务端自己的行为——
角色闸 / 会话校验 / JSON 解析 / 数值核对（对用户原话） / 转待审落库四件套 /
审定链零改动（落库后 list_drafts 能按既有结构读到）/ 留痕（llm_call_log +
sys_op_log）。真机演示路径的 1~2 次真调用在无头实测里验，不进 pytest。
"""
import json
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from app.main import app                      # noqa: E402
from app import db                            # noqa: E402
from app import chat_draft as cd              # noqa: E402


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUTH_MODE", "on")


def login(account, pwd="demo-1234") -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"account": account, "password": pwd})
    assert r.status_code == 200, r.text
    return c


FAKE_CFG = {"config_id": None, "model_name": "mock-glm",
            "max_tokens": 4000, "concurrency": 1}


def _real_cfg_id():
    """llm_call_log.config_id 有真外键——mock 配置也要借一条真配置的 id。"""
    global FAKE_CFG
    if not FAKE_CFG["config_id"]:
        FAKE_CFG["config_id"] = db.scalar(
            "SELECT config_id FROM llm_config WHERE enabled LIMIT 1")
    return FAKE_CFG["config_id"]


class _Res:
    def __init__(self, ok=True, content="", model_echo="mock-glm"):
        self.ok = ok
        self.message = "" if ok else "mock 失败"
        self.err_kind = None if ok else "timeout"
        self.latency_ms = 5
        self.model_echo = model_echo
        self.prompt_tokens = 100
        self.completion_tokens = 50


def _mock_chat(monkeypatch, reply_payload):
    """把 llmc.chat_json 换成确定性假实现。reply_payload 是模型应回的 JSON 串。

    cd.chat_turn 里对 llmc.chat_json 的调用走的是 `from .llm import client as
    llmc` 的模块引用——patch client 模块本身即可，两处引用同一模块对象。
    """
    calls = []

    def fake(cfg, system, user, max_tokens=1500):
        calls.append({"system": system, "user": user})
        return _Res(), reply_payload

    from app.llm import client as llmc
    monkeypatch.setattr(llmc, "chat_json", fake)
    return calls


@pytest.fixture(autouse=True)
def _fake_cfg(monkeypatch):
    _real_cfg_id()
    monkeypatch.setattr(cd, "chat_cfg", lambda: dict(FAKE_CFG))


def _cleanup(task_ids, draft_ids=()):
    """清掉测试落的四件套 + 两条留痕。台账行（llm_call_log/sys_op_log）
    是审计表，生产不可删——但这两行是测试自己写的 mock 数据，不删会把
    演示库的计费/操作记录污染成假数字，留着反而违反「别把验证动作的产物
    留在别人的演示环境里」。"""
    for tid in task_ids:
        rows = db.query("SELECT draft_id, clause_id FROM doclib_draft WHERE task_id=%s",
                        (tid,))
        for r in rows:
            db.execute("DELETE FROM doclib_draft WHERE draft_id=%s", (r["draft_id"],))
            db.execute("DELETE FROM doclib_clause WHERE clause_id=%s", (r["clause_id"],))
        db.execute("DELETE FROM doc_lib_file WHERE fingerprint=%s", ("chat:%s" % tid,))
        db.execute("DELETE FROM doclib_extract_task WHERE task_id=%s", (tid,))
    for did in draft_ids:
        db.execute("DELETE FROM sys_op_log WHERE category='rule' AND target=%s", (did,))
    db.execute("DELETE FROM llm_call_log WHERE purpose='chat_draft' AND config_id=%s",
               (FAKE_CFG["config_id"],))


# ── 角色闸 ────────────────────────────────────────────────────────────────────

def test_审核岗不许对话起草():
    aud = login("linjia")
    r = aud.post("/api/extract/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 403
    r2 = aud.post("/api/extract/draft-from-chat",
                  json={"draft": {}, "messages": []})
    assert r2.status_code == 403


# ── 会话校验 ──────────────────────────────────────────────────────────────────

def test_空会话与无用户发言被拒():
    eng = login("hetech")
    r = eng.post("/api/extract/chat", json={"messages": []})
    assert r.status_code == 400
    r2 = eng.post("/api/extract/chat", json={
        "messages": [{"role": "assistant", "content": "你好"}]})
    assert r2.status_code == 400 and "用户" in r2.json()["detail"]


# ── 对话轮：解析 / 追问 / 草稿 / 失败留痕 ────────────────────────────────────

def test_追问轮不回草稿(monkeypatch):
    eng = login("hetech")
    payload = json.dumps({"reply": "适用哪些人员？", "ready": False, "draft": {}},
                         ensure_ascii=False)
    calls = _mock_chat(monkeypatch, payload)
    r = eng.post("/api/extract/chat", json={
        "messages": [{"role": "user", "content": "住宿费超标要拦截"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] is False and body["draft"] is None
    assert "适用" in body["reply"]
    # 用户内容真的发给了模型（脱敏在更下层，这里只验拼装）
    assert "住宿费超标要拦截" in calls[0]["user"]


def test_成稿轮回八要素草稿(monkeypatch):
    eng = login("hetech")
    draft = {"可规则化": True, "规则名称": "住宿费超标拦截", "审核维度": "住宿费",
             "规则类型": "硬", "适用范围": "全体员工", "判定逻辑": "住宿费超标准200元以上",
             "例外情形": None, "风险等级": "中", "处置动作": "禁止", "缺口": ""}
    payload = json.dumps({"reply": "草稿已生成", "ready": True, "draft": draft},
                         ensure_ascii=False)
    _mock_chat(monkeypatch, payload)
    r = eng.post("/api/extract/chat", json={
        "messages": [{"role": "user", "content": "住宿费超标准200元以上要拦截"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["draft"]["规则名称"] == "住宿费超标拦截"
    assert "制度原文" in body["note"]


def test_模型失败也落llm_call_log(monkeypatch):
    from app.llm import client as llmc

    def bad(cfg, system, user, max_tokens=1500):
        return _Res(ok=False), ""

    monkeypatch.setattr(llmc, "chat_json", bad)
    eng = login("hetech")
    r = eng.post("/api/extract/chat", json={
        "messages": [{"role": "user", "content": "测试失败留痕"}]})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    row = db.query_one(
        "SELECT status FROM llm_call_log WHERE purpose='chat_draft' "
        "ORDER BY id DESC LIMIT 1")
    assert row and row["status"] == "timeout"
    db.execute("DELETE FROM llm_call_log WHERE purpose='chat_draft'")


# ── 数值核对（对话版）────────────────────────────────────────────────────────

def test_用户没说的数字被标红():
    draft = {"判定逻辑": "住宿费超标准250元以上拦截"}
    chk = cd.check_numbers_vs_user(draft, ["住宿费超标准200元以上要拦截"])
    vals = [s["value"] for s in chk["suspect"]]
    assert any("250" in v for v in vals), "模型编的 250 必须被标出：%s" % vals


def test_用户说过的数字不误报():
    draft = {"判定逻辑": "住宿费超标准200元以上拦截"}
    chk = cd.check_numbers_vs_user(draft, ["住宿费超标准200元以上要拦截"])
    assert chk["ok"] is True and chk["suspect"] == []


def test_列表序号不算阈值():
    draft = {"判定逻辑": "1. 超标准200元拦截"}
    chk = cd.check_numbers_vs_user(draft, ["超标准200元要拦截"])
    assert chk["ok"] is True


# ── 转待审：落库四件套 + 审定链零改动 ────────────────────────────────────────

GOOD_DRAFT = {"可规则化": True, "规则名称": "住宿费超标拦截", "审核维度": "住宿费",
              "规则类型": "硬", "适用范围": "全体员工",
              "判定逻辑": "住宿费超标准200元以上拦截", "例外情形": None,
              "风险等级": "中", "处置动作": "禁止", "缺口": ""}
MSGS = [{"role": "user", "content": "住宿费超标准200元以上要拦截"}]


def test_转待审落库四件套():
    eng = login("hetech")
    r = eng.post("/api/extract/draft-from-chat",
                 json={"draft": GOOD_DRAFT, "messages": MSGS, "session_hint": "住宿费"})
    assert r.status_code == 200, r.text
    out = r.json()
    tid = out["task_id"]
    try:
        # 1. task：mode='对话'
        t = db.query_one("SELECT mode, scope, status FROM doclib_extract_task "
                         "WHERE task_id=%s", (tid,))
        assert t["mode"] == "对话" and t["status"] == "done"
        # 2. file：解析失败占位（run_split 不会碰它）+ doc_kind='其他'
        f = db.query_one("""SELECT f.parse_state, f.doc_kind FROM doclib_draft d
                             JOIN doclib_clause c ON c.clause_id=d.clause_id
                             JOIN doc_lib_file f ON f.file_id=c.file_id
                            WHERE d.draft_id=%s""", (out["draft_id"],))
        assert f["parse_state"] == "解析失败" and f["doc_kind"] == "其他"
        # 3. clause：用户原话进 body，text_source='对话'
        c = db.query_one("""SELECT c.clause_no, c.body, c.text_source
                             FROM doclib_draft d JOIN doclib_clause c
                               ON c.clause_id=d.clause_id WHERE d.draft_id=%s""",
                         (out["draft_id"],))
        assert c["clause_no"] == "对话" and c["text_source"] == "对话"
        assert "住宿费超标准200元以上" in c["body"]
        # 4. draft：待审，审定链既有查询能读到
        d = db.query_one("SELECT review_state, rulable, draft FROM doclib_draft "
                         "WHERE draft_id=%s", (out["draft_id"],))
        assert d["review_state"] == "待审" and d["rulable"] is True
        rows = db.query("""SELECT d.draft_id FROM doclib_draft d
                             JOIN doclib_clause c ON c.clause_id=d.clause_id
                             JOIN doc_lib_file f ON f.file_id=c.file_id
                            WHERE d.draft_id=%s""", (out["draft_id"],))
        assert rows, "list_drafts 同款三表 JOIN 必须能读到（审定链零改动的前提）"
        # op_log：对话起草动作
        op = db.query_one(
            "SELECT action, actor FROM sys_op_log WHERE category='rule' "
            "AND target=%s", (out["draft_id"],))
        assert op and op["action"] == "对话起草" and op["actor"] == "hetech"
    finally:
        _cleanup([tid], (out["draft_id"],))


def test_转待审走既有审定接口可驳回():
    """红线验证：对话草稿进了同一条审定链——驳回它走的是既有 review 端点。"""
    eng = login("hetech")
    r = eng.post("/api/extract/draft-from-chat",
                 json={"draft": GOOD_DRAFT, "messages": MSGS})
    out = r.json()
    tid = out["task_id"]
    try:
        r2 = eng.post("/api/extract/drafts/%s/review" % out["draft_id"],
                      json={"state": "驳回", "reviewer": "测试何工", "reason": "要素不全"})
        assert r2.status_code == 200, r2.text
        d = db.query_one("SELECT review_state, reject_reason FROM doclib_draft "
                         "WHERE draft_id=%s", (out["draft_id"],))
        assert d["review_state"] == "驳回" and d["reject_reason"] == "要素不全"
    finally:
        _cleanup([tid], (out["draft_id"],))


def test_缺规则名称或判定逻辑拒转():
    eng = login("hetech")
    # session_hint 用测试专属词：库里可能有演示存量（同规则名的对话草稿常驻），
    # 拒转断言按 hint 定位本测试该落未落的行，不与存量互扰
    hint = "拒转测试-不应落库"
    d1 = dict(GOOD_DRAFT); d1["规则名称"] = ""
    r = eng.post("/api/extract/draft-from-chat",
                 json={"draft": d1, "messages": MSGS, "session_hint": hint})
    assert r.status_code == 400 and "规则名称" in r.json()["detail"]
    d2 = dict(GOOD_DRAFT); d2["判定逻辑"] = None
    r2 = eng.post("/api/extract/draft-from-chat",
                  json={"draft": d2, "messages": MSGS, "session_hint": hint})
    assert r2.status_code == 400 and "判定逻辑" in r2.json()["detail"]
    assert db.scalar("SELECT count(*) FROM doclib_extract_task WHERE mode='对话' "
                     "AND scope=%s", (hint,)) == 0, "拒绝时不得落半截"


def test_数值待核进草稿留档():
    eng = login("hetech")
    d = dict(GOOD_DRAFT); d["判定逻辑"] = "住宿费超标准250元以上拦截"
    r = eng.post("/api/extract/draft-from-chat",
                 json={"draft": d, "messages": MSGS})
    out = r.json()
    assert any("250" in s["value"] for s in out["number_check"]["suspect"])
    row = db.query_one("SELECT number_check FROM doclib_draft WHERE draft_id=%s",
                       (out["draft_id"],))
    chk = row["number_check"]
    if isinstance(chk, str):
        chk = json.loads(chk)
    assert any("250" in s["value"] for s in chk["suspect"]), "落库版必须同样带红标"
    _cleanup([out["task_id"]], (out["draft_id"],))
