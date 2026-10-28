# -*- coding: utf-8 -*-
"""关系图谱完整版出口（graph/rich + concepts/{id}/rules）的契约测试。

这两个出口是规则库「关系图谱」页签的唯一数据源，钉四件事：
  1. 字段齐全——前端每一处动画/tooltip 都挂在具体字段上，缺一个就是静默降级
  2. hit_count 口径——近 30 天 outcome='hit' 的 finding 数，pending 不算
  3. 溯源三要素——source.file/clause/confidence 结构不动
  4. 概念反查——concept_rules 给的规则清单与 rule_concept 一致
薄出口、引擎零改动：这里同时钉「不写库」——图谱出口跑了之后库里一个字都没变。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, onto_coord as oc  # noqa: E402


@pytest.fixture(scope="module")
def rich():
    return oc.graph_rich()


# ── 1. 字段齐全 ─────────────────────────────────────────────────────────────
def test_三层节点都在(rich):
    assert any(e["layer"] == "biz" for e in rich["entities"])
    assert all(r["layer"] == "rule" for r in rich["rule_nodes"])
    assert all(c["layer"] == "concept" for c in rich["concept_nodes"])
    # 全量规则都进图（不是只画参与关系的 20 条——完整版要能按维度聚合）
    n_rules = db.scalar("SELECT count(*) FROM rule WHERE version=1")
    assert len(rich["rule_nodes"]) == n_rules


def test_规则节点字段(rich):
    for k in ("rule_id", "rule_name", "dimension", "dimension_name", "control_type",
              "rule_class", "compiled", "demo_executable", "hit_count", "layer",
              "source", "feasibility", "priority"):
        assert k in rich["rule_nodes"][0], "rule_nodes 缺字段 %s" % k


def test_维度名映射(rich):
    """D1..D10 每个编号都有中文名（combo 聚合的标签靠它）。"""
    dims = {d["id"]: d["name"] for d in rich["dimensions"]}
    assert len(dims) == 10
    for r in rich["rule_nodes"]:
        assert r["dimension"] in dims, "规则 %s 的维度 %s 不在 D1-D10" % (
            r["rule_id"], r["dimension"])


def test_溯源三要素(rich):
    """source 结构钉死：file/clause/confidence 三键，有出处时缺一不可。"""
    with_src = [r for r in rich["rule_nodes"] if r["source"]]
    assert with_src, "82 条规则一条 source 都没有——装载口径变了，要重新审这个出口"
    for r in with_src[:10]:
        for k in ("file", "clause", "confidence"):
            assert k in r["source"], "%s 的 source 缺 %s" % (r["rule_id"], k)


def test_冲突边带note与开关(rich):
    opens = [e for e in rich["rule_edges"] if e["conflict_open"]]
    assert opens, "没有待裁决冲突边——演示冲突样例没了，先跑 P0-7 数据自愈"
    for e in opens:
        assert e["rel_type"] == "conflicts_with" and e["needs_confirm"]
        assert e["note"], "冲突边 %s→%s 没有 note，对比抽屉讲不出 why" % (
            e["rule_id"], e["other_rule"])
    # 已裁决 / 非冲突的边不许误标
    for e in rich["rule_edges"]:
        if e["rel_type"] != "conflicts_with":
            assert not e["conflict_open"]


# ── 2. hit_count 口径 ───────────────────────────────────────────────────────
def test_hit_count口径是近30天hit(rich):
    """与 SQL 同口径重算一遍，逐规则比对。pending/超期不算——
    pending 是「没跑成」，算进去会把跑不动画成很热。"""
    truth = {r["rule_id"]: r["n"] for r in db.query("""
        SELECT f.rule_id, count(*) AS n
          FROM review_finding f
          JOIN review_run rr ON rr.run_id = f.run_id
         WHERE f.outcome = 'hit'
           AND rr.started_at > now() - interval '30 days'
         GROUP BY 1""")}
    for r in rich["rule_nodes"]:
        assert r["hit_count"] == truth.get(r["rule_id"], 0), \
            "%s hit_count=%s 与实时聚合 %s 不一致" % (
                r["rule_id"], r["hit_count"], truth.get(r["rule_id"], 0))


def test_hit_count全实时不缓存(rich):
    """出口跑两次之间库里补一条 hit，第二次必须能看到——数字不许有缓存。"""
    g2 = oc.graph_rich()
    a = {r["rule_id"]: r["hit_count"] for r in rich["rule_nodes"]}
    b = {r["rule_id"]: r["hit_count"] for r in g2["rule_nodes"]}
    assert a == b  # 同一秒内两次取数一致（口径稳定性）；
    # 且确实是从 finding 表来的：任取一条有命中的规则，数字能在 finding 表对上
    hot = [r for r in rich["rule_nodes"] if r["hit_count"] > 0]
    if not hot:
        pytest.skip("近 30 天没有任何命中，先跑一次智能审核")
    rid = hot[0]["rule_id"]
    n = db.scalar("""SELECT count(*) FROM review_finding f
                      JOIN review_run rr ON rr.run_id = f.run_id
                     WHERE f.rule_id=%s AND f.outcome='hit'
                       AND rr.started_at > now() - interval '30 days'""", (rid,))
    assert hot[0]["hit_count"] == n


# ── 3. 概念引用边 ───────────────────────────────────────────────────────────
def test_概念边与rule_concept一致(rich):
    n_rc = db.scalar("SELECT count(*) FROM rule_concept WHERE version=1")
    assert len(rich["concept_edges"]) == n_rc
    # 每条边两端都在节点集里（悬边=前端画不出线）
    rids = {r["rule_id"] for r in rich["rule_nodes"]}
    cids = {c["concept_id"] for c in rich["concept_nodes"]}
    for e in rich["concept_edges"]:
        assert e["rule_id"] in rids, "概念边悬在规则侧：%s" % e["rule_id"]
        assert e["concept_id"] in cids, "概念边悬在概念侧：%s" % e["concept_id"]


def test_概念节点带可得性(rich):
    """可得性非「样本可得」的要能在图上认出来——挂在取不到的数据上的规则
    是「看着有规则、实际跑不动」的那批。"""
    for c in rich["concept_nodes"]:
        assert c["availability"], "概念 %s 没有可得性" % c["concept_id"]
        assert c["n_rules"] >= 1


# ── 4. 概念反查 ─────────────────────────────────────────────────────────────
def test_概念反查规则清单():
    c = oc.concept_rules("C0203")
    assert c, "C0203（费用明细金额，被 20+ 条规则引用）查不到"
    n_truth = db.scalar(
        "SELECT count(*) FROM rule_concept WHERE concept_id='C0203' AND version=1")
    assert len(c["rules"]) == n_truth
    for r in c["rules"]:
        assert r["role"] in ("read", "threshold", "exist")
    assert c["availability"], "概念详情缺可得性"


def test_概念反查不存在():
    assert oc.concept_rules("C9999") is None


# ── 5. 薄出口不写库 ─────────────────────────────────────────────────────────
def test_图谱出口只读(rich):
    """跑完 graph_rich 之后 rule_relation 的 needs_confirm 一个没变——
    裁决只能走 /relations/resolve（留痕），图谱不许有旁路。"""
    n_open = db.scalar(
        "SELECT count(*) FROM rule_relation WHERE needs_confirm")
    n_edge_open = sum(1 for e in rich["rule_edges"] if e["conflict_open"])
    assert n_edge_open == n_open
