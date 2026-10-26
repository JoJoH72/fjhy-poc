# -*- coding: utf-8 -*-
"""制度文件预置的幂等性（2026-10-25 演示待办③）。

preset_doclib.py 挂在两条启动链上、每次启动都跑：它要不重不漏，
「重跑不重复入库」必须是钉死的性质而不是碰巧。它走 save_uploads()
的真实路径（暂存→sha256 去重→原子改名→自动扫描），所以这里顺带
钉住的也是那条链的去重性质在「整目录预置」场景下的表现。

另一条被钉住的纪律：**现场演示素材不许被预置烧掉**。
「差旅费审核规则.xlsx」是 10-26 现场上传动线的素材，预置脚本必须跳过它
——预置了它，现场增量就是 0，「上传→抽取→审定」的动线就断了。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.demo import preset_doclib  # noqa: E402

SRC = preset_doclib.SRC

pytestmark = pytest.mark.skipif(
    not os.path.isdir(SRC), reason="材料/差旅审核制度规则 不在，预置无从谈起")


def _lib_count():
    return db.scalar(
        "SELECT count(*) FROM doc_lib_file WHERE instance_id='fjhy' AND NOT missing") or 0


def _expected_names():
    return sorted(f for f in os.listdir(SRC)
                  if not f.startswith("~$")
                  and f not in preset_doclib.LIVE_DEMO_SKIP
                  and os.path.isfile(os.path.join(SRC, f)))


def test_预置幂等_重跑不重复入库():
    assert preset_doclib.main() == 0
    n1 = _lib_count()
    # 再跑一遍：库里文件数一个不许变（同内容 sha256 去重，被拒的也留台账行）
    assert preset_doclib.main() == 0
    assert _lib_count() == n1, "重跑后库里的文件数变了——预置不幂等"
    # 预置过的每一份都要能在库里按名字找到
    names = {os.path.basename(r["rel_path"]) for r in db.query(
        "SELECT rel_path FROM doc_lib_file WHERE instance_id='fjhy' AND NOT missing")}
    missing = [n for n in _expected_names() if n not in names]
    assert not missing, "预置说成功，但库里找不到：%s" % missing


def test_预置后文件库可见且带真实台账():
    """演示口径：客户在文件库页要能看到已上传材料清单。"""
    rows = db.query("""
        SELECT f.rel_path, u.uploaded_by
          FROM doc_lib_file f
          LEFT JOIN doc_lib_upload u ON u.file_id = f.file_id
         WHERE f.instance_id='fjhy' AND NOT f.missing""")
    assert rows, "文件库是空的——演示时客户看不到已上传材料"
    assert any(r["uploaded_by"] for r in rows), "台账里没有上传人——预置没走真实上传路径"


def test_现场演示素材绝不允许被预置():
    """「差旅费审核规则.xlsx」留给现场上传。库里若有它，动线就断了。"""
    hit = db.query_one(
        "SELECT file_id FROM doc_lib_file WHERE instance_id='fjhy'"
        " AND rel_path LIKE %s AND NOT missing",
        ("%差旅费审核规则.xlsx%",))
    assert not hit, "现场素材被预置进库了——把它删掉，预置脚本的排除表失效了"
