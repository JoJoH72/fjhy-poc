# -*- coding: utf-8 -*-
"""冲突对比与裁决（2026-10-27 客户点名）：compare 组装 / resolve 留痕 / 发布第 4 闸。

客户投诉原话：「冲突标注点不开，看不到两条规则的对比原文」。
这组测试钉三件事：

1. **compare 必须把两条口径都给出来**——左侧规则+来源条款原文，
   右侧对方（条款全文或如实标「未入库」）。反查不到时给 note 备份的
   their_text 兜底，绝不编造原文。
2. **裁决必须留痕**：rule_relation.note 追加 resolved 段（原 note 一字不动）、
   needs_confirm 置 false、sys_op_log 落一条 category=rule。
   没写结论 / 没署名的裁决必须 400。
3. **发布第 4 闸**：有待裁决冲突时 freeze 被 gates 挡住——
   「暂定口径」不许升级成「生产口径」。

测试数据用真实库里的两条 conflicts_with（TR-D3-12 / TR-D4-16，装载播种），
裁决动作做完**还原 needs_confirm=true**，不把演示库留在被裁决状态。
"""
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                    # noqa: E402
from app.main import app                               # noqa: E402
from app.api import asset as asset_api                 # noqa: E402
from app import release_pack as rp                     # noqa: E402

c = TestClient(app)

# 装载播种的两条真实冲突关系（_data_seed 若库里没有则跳过——不造假数据）
def _rel(rule_id):
    return db.query_one("""SELECT id, note, needs_confirm FROM rule_relation
                           WHERE rule_id=%s AND rel_type='conflicts_with' AND version=1""",
                        (rule_id,))


@pytest.fixture()
def restore_d312():
    """裁决测试动的是真实播种行，用完把**全部** conflicts_with 行还原成
    needs_confirm=true 且 note 里无 resolved 段——不把演示库留在被裁决状态。"""
    yield
    for row in db.query("""SELECT id, note FROM rule_relation
                           WHERE rel_type='conflicts_with'"""):
        note = json.loads(row["note"]) if row["note"] else {}
        note.pop("resolved", None)
        db.execute("""UPDATE rule_relation SET needs_confirm=true, note=%s
                      WHERE id=%s""",
                   (json.dumps(note, ensure_ascii=False), row["id"]))


# ─────────────────────────────────────────────────────────────────────────────
# 一、compare：两条口径都要摆出来
# ─────────────────────────────────────────────────────────────────────────────
def test_左侧是规则本体加来源条款():
    if not _rel("TR-D3-12"):
        pytest.skip("库里没有 TR-D3-12 冲突关系（装载基线变了）")
    r = c.get("/api/asset/rules/TR-D3-12/relations/compare",
              params={"other": "制度 第十条 / 要点 2.1.20"})
    assert r.status_code == 200
    j = r.json()
    left = j["left"]
    assert left["rule_id"] == "TR-D3-12"
    assert left["rule_name"], "规则名没给——名字都看不到谈不上对比"
    assert left["control_type"]
    sc = left["source_clause_full"]
    assert sc and sc["body"], "来源条款原文没给出来"


def test_右侧条款出处能反查到全文():
    """「要点 2.1.20」必须在 doclib_clause 里反查出全文——their_text 的原文佐证。"""
    if not _rel("TR-D3-12"):
        pytest.skip("库里没有 TR-D3-12 冲突关系")
    r = c.get("/api/asset/rules/TR-D3-12/relations/compare",
              params={"other": "制度 第十条 / 要点 2.1.20"})
    j = r.json()
    assert j["right"]["kind"] == "clause"
    segs = {s["clause_no"]: s for s in j["right"]["sources"]}
    assert segs["2.1.20"]["found"], "要点 2.1.20 反查不到——这条实测是能命中的"
    assert "交通意外保险" in (segs["2.1.20"]["clause"]["body"] or "")
    # 「制度 第十条」那份文件没切条款（实测 0 条条款）——如实标没找到
    assert not segs["第十条"]["found"]


def test_反查不到时不编原文():
    """「解答 解答4」库里没有这份条款。note 里也没备份 their_text（只有数值）。
    这时 right.sources 的 clause 必须是 None——宁缺毋造。"""
    if not _rel("TR-D4-16"):
        pytest.skip("库里没有 TR-D4-16 冲突关系")
    r = c.get("/api/asset/rules/TR-D4-16/relations/compare",
              params={"other": "解答 解答4"})
    j = r.json()
    seg = j["right"]["sources"][0]
    assert not seg["found"] and seg["clause"] is None
    # 但数值分歧（40 vs 50）在 note 里，抽屉靠它展示差异
    assert j["note"].get("their_value") == "40 元"
    assert j["note"].get("our_value") == "50 元"


def test_没有冲突关系的规则404():
    r = c.get("/api/asset/rules/TR-D1-01/relations/compare",
              params={"other": "任何条款"})
    assert r.status_code == 404


def test_数值提取按单位分桶():
    """前端 <mark> 高亮用的 nums 列表：只要「数值+单位」形态的。"""
    nums = asset_api._clause_nums({"body": "标准 450 元，超 10 人按三分之一，白酒 500 毫升不超 500 元"})
    got = {(n["value"], n["unit"]) for n in nums}
    assert ("450", "元") in got
    assert ("10", "人") in got


# ─────────────────────────────────────────────────────────────────────────────
# 二、resolve：拍板必须留痕
# ─────────────────────────────────────────────────────────────────────────────
def test_裁决落note并清needs_confirm(restore_d312):
    if not _rel("TR-D3-12"):
        pytest.skip("库里没有 TR-D3-12 冲突关系")
    from app import config
    config.AUTH_MODE = "on"
    try:
        cc = TestClient(app)
        assert cc.post("/api/auth/login",
                       json={"account": "hetech", "password": "demo-1234"}).status_code == 200
        r = cc.post("/api/asset/rules/TR-D3-12/relations/resolve",
                    json={"decision": "left",
                          "resolution": "取制度口径：允许每人次一份保险",
                          "by": "测试裁决人"})
        assert r.status_code == 200, r.text
    finally:
        config.AUTH_MODE = "off"
    row = _rel("TR-D3-12")
    assert row["needs_confirm"] is False
    note = json.loads(row["note"])
    # 原 note 的裁决依据一字不动（why/with 都在），resolved 是追加段
    assert note["why"], "裁决把原有依据覆盖掉了"
    assert note["resolved"]["decision"] == "left"
    assert note["resolved"]["resolved_by"] == "测试裁决人"
    # 操作日志：谁、何时、对什么、做了什么
    log = db.query_one("""SELECT * FROM sys_op_log WHERE category='rule'
                          AND action='裁决规则冲突' ORDER BY id DESC LIMIT 1""")
    assert log and log["actor"] == "hetech" and "TR-D3-12" in log["target"]


def test_没写结论的裁决被拒(restore_d312):
    r = c.post("/api/asset/rules/TR-D3-12/relations/resolve",
               json={"decision": "left", "resolution": "  ", "by": "某人"})
    assert r.status_code == 400
    assert "结论" in r.json()["detail"]


def test_没署名的裁决被拒():
    r = c.post("/api/asset/rules/TR-D3-12/relations/resolve",
               json={"decision": "merge", "resolution": "合并", "by": ""})
    assert r.status_code == 400


def test_非法decision被拒():
    r = c.post("/api/asset/rules/TR-D3-12/relations/resolve",
               json={"decision": "别的", "resolution": "x", "by": "y"})
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 三、发布第 4 闸：待裁决冲突挡住冻结
# ─────────────────────────────────────────────────────────────────────────────
def test_有待裁决冲突时第4闸挡住():
    n = db.scalar("SELECT count(*) FROM rule_relation WHERE needs_confirm AND version=1") \
        + db.scalar("SELECT count(*) FROM doclib_conflict WHERE state='待裁决'")
    gates = {g["gate"]: g for g in rp.gates(None)}
    g4 = gates.get("无待裁决冲突")
    assert g4, "第 4 闸没挂上"
    if n:
        assert g4["ok"] is False
        assert g4["how"], "闸必须给「怎么过」"


def test_全部裁决后第4闸放行(restore_d312):
    """裁决掉唯一的 needs_confirm 后（连同 doclib_conflict 必须为空），闸要能变绿。"""
    from app import config
    config.AUTH_MODE = "on"
    try:
        cc = TestClient(app)
        cc.post("/api/auth/login", json={"account": "hetech", "password": "demo-1234"})
        rows = db.query("""SELECT rule_id FROM rule_relation
                           WHERE needs_confirm AND version=1""")
        for row in rows:
            r = cc.post("/api/asset/rules/%s/relations/resolve" % row["rule_id"],
                        json={"decision": "left", "resolution": "测试放行", "by": "测试"})
            assert r.status_code == 200, r.text
        n = db.scalar("SELECT count(*) FROM rule_relation WHERE needs_confirm AND version=1")
        assert n == 0
        if db.scalar("SELECT count(*) FROM doclib_conflict WHERE state='待裁决'") == 0:
            gates = {g["gate"]: g for g in rp.gates(None)}
            assert gates["无待裁决冲突"]["ok"] is True
    finally:
        config.AUTH_MODE = "off"
        # fixture restore_d312 会做完整还原（needs_confirm + note）


def test_op_log_rule类别已登记():
    """rule 类别加进 CATEGORIES——台账查询页的分类下拉靠它。"""
    from app.api import op_log
    assert "rule" in op_log.CATEGORIES
    assert op_log.CATEGORY_CN["rule"] == "规则"
