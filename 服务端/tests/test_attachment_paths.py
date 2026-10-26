# -*- coding: utf-8 -*-
"""stored_path 的形态纪律：写相对、读兼容、修复幂等（2026-10-25 演示待办①）。

背景：stored_path 原来存当次运行环境的**绝对路径**——数据库跟着环境走
（pg_dump 搬库、卷重建）之后全体失效，/api/attachments/{id}/file 的
_under 闸门会正确拒绝，但用户看到的是「附件全打不开」，和「没给附件」
长得一模一样。演示待办「数据导入新环境后部分附件缺失」就是这个形态问题。

这个文件钉四条：

1. **新写入的值必须是相对 ATTACH_DIR 的相对路径**（`/` 分隔）。
2. **读取端三种历史形态都认**：相对值 / 本环境绝对值 / 别的环境搬来的
   绝对值（按 /attachments/ 段重定位，候选文件存在才采信）。
3. **repair 幂等**：跑到不动点为止；两边都没文件的行保留登记不删
   ——登记在而文件不在，是要人查的状态，不是可以静默清掉的状态。
4. **解析出来的路径必须能过 API 的 _under 闸门**——重定位若产出越界
   路径，等于把安全检查的输入换成了绕过它的输入。

测试数据一律建在 pytest 的 tmp_path 与 FJHY-TEST- 前缀下，跑完删干净。
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, db  # noqa: E402
from app.perception import attachment_extract as ax  # noqa: E402

PREFIX = "FJHY-TEST-ATT-"


@pytest.fixture
def bill():
    """一张测试单 + 一张真实落盘的附件图。跑完连带附件（库与盘）一起删。

    **盘上的文件也要删**：status() 的孤儿检测数的是磁盘文件数对库里登记数，
    测试残留的文件会让下一次真实体检报「差 N 条」——一条把人引向
    「重跑过 seed」这个完全错误方向的假警。"""
    no = PREFIX + "0009"
    db.execute("""INSERT INTO expense_bill(bill_no, doc_type, biz_type, is_demo)
                  VALUES (%s, '差旅费报账单', '国内差旅费', FALSE)
                  ON CONFLICT (bill_no) DO NOTHING""", (no,))
    yield no
    db.execute("DELETE FROM attachment WHERE bill_no=%s", (no,))
    db.execute("DELETE FROM expense_bill WHERE bill_no=%s", (no,))
    shutil.rmtree(os.path.join(config.ATTACH_DIR, "audit", "fjhy", no),
                  ignore_errors=True)


def _insert_att(bill_no, stored_path, seq=1):
    """直接插一行附件（不经过抽取），用来构造三种历史形态。"""
    db.execute("""INSERT INTO attachment(bill_no, file_name, stored_path, seq,
                                         sha256, byte_size, content_type,
                                         source_kind, source_ref)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,'xlsx内嵌','测试')
                  ON CONFLICT DO NOTHING""",
               (bill_no, "%02d_test.png" % seq, stored_path, seq,
                "testsha%03d" % seq, 100, "image/png"))


# ── 1 · 新写入的值是相对值 ────────────────────────────────────────────────────

def test_抽取落库的是相对路径(tmp_path, bill):
    """extract_xlsx 写入的 stored_path 必须是相对 ATTACH_DIR、`/` 分隔。
    绝对值跨环境即失效——这是整个形态约定的出发点。"""
    from tests.test_attachment_extract import _make_xlsx, _png
    x = os.path.join(tmp_path, "t.xlsx")
    _make_xlsx(x, [bill], [(2, 1, _png((200, 10, 10)))])
    ax.extract_xlsx(x)
    rows = db.query("SELECT stored_path FROM attachment WHERE bill_no=%s", (bill,))
    assert rows, "抽取没有落任何附件行"
    for r in rows:
        p = r["stored_path"]
        assert p, "stored_path 为空"
        assert not os.path.isabs(p), "落库的还是绝对路径：%s" % p
        assert "\\" not in p, "相对值里混了反斜杠：%s" % p
        assert os.path.isfile(os.path.join(config.ATTACH_DIR, *p.split("/"))), \
            "相对值拼回 ATTACH_DIR 后文件不存在：%s" % p


# ── 2 · 读取端三种历史形态 ────────────────────────────────────────────────────

def test_解析_相对值(tmp_path, bill):
    rel = "audit/fjhy/%s/01_test.png" % bill
    p = ax.resolve_stored_path(rel)
    assert os.path.isabs(p)
    assert p == os.path.join(config.ATTACH_DIR, *rel.split("/"))


def test_解析_本环境绝对值原样(tmp_path, bill):
    full = os.path.join(config.ATTACH_DIR, "audit", "fjhy", bill, "01_test.png")
    assert ax.resolve_stored_path(full) == os.path.abspath(full)


def test_解析_跨环境绝对值按attachments段重定位(tmp_path, bill):
    """库里存着别的环境的绝对路径（另一台机器的盘符 / 容器内 /app/…）。
    按 /attachments/ 段重定位到本环境，**候选文件存在才采信**。"""
    rel = "audit/fjhy/%s/01_test.png" % bill
    real = os.path.join(config.ATTACH_DIR, *rel.split("/"))
    os.makedirs(os.path.dirname(real), exist_ok=True)
    with open(real, "wb") as f:
        f.write(b"png")
    for foreign in ("/app/服务端/var/attachments/" + rel,
                    "D:\\别的环境\\服务端\\var\\attachments\\"
                    + rel.replace("/", "\\")):
        got = ax.resolve_stored_path(foreign)
        assert got == os.path.abspath(real), "没重定位到本环境：%s → %s" % (foreign, got)


def test_解析_重定位候选不存在时交回原值(bill):
    """候选不存在时**不许猜**：把原值交回去，走 _under/404 的既有分支，
    那两处文案会告诉人发生了什么。"""
    ghost = "/app/other/var/attachments/audit/fjhy/%s/99_ghost.png" % bill
    assert ax.resolve_stored_path(ghost) == ghost
    assert ax.resolve_stored_path("") is None
    assert ax.resolve_stored_path(None) is None


def test_解析结果必须过API的_under闸门(bill):
    """重定位是「善意猜测」，猜出来的路径必须仍落在 ATTACH_DIR 之内
    ——否则等于把安全检查的输入换成了绕过它的输入。"""
    from app.api.review import _under
    rel = "audit/fjhy/%s/01_test.png" % bill
    real = os.path.join(config.ATTACH_DIR, *rel.split("/"))
    os.makedirs(os.path.dirname(real), exist_ok=True)
    with open(real, "wb") as f:
        f.write(b"png")
    for raw in ("/app/x/var/attachments/" + rel,
                rel,
                os.path.join(config.ATTACH_DIR, *rel.split("/"))):
        assert _under(ax.resolve_stored_path(raw), config.ATTACH_DIR), \
            "解析结果越界：%s" % raw


# ── 3 · repair 幂等、缺文件不删登记 ──────────────────────────────────────────

def test_repair幂等且缺文件保留登记(tmp_path, bill):
    # 一行「文件在、值是旧绝对形态」→ 该被改写成相对值
    rel = "audit/fjhy/%s/02_test.png" % bill
    real = os.path.join(config.ATTACH_DIR, *rel.split("/"))
    os.makedirs(os.path.dirname(real), exist_ok=True)
    with open(real, "wb") as f:
        f.write(b"png")
    legacy = "E:\\老环境\\服务端\\var\\attachments\\" + rel.replace("/", "\\")
    _insert_att(bill, legacy, seq=2)
    # 一行「两边都没有文件」→ 登记保留，不许被删
    ghost = "/app/old/var/attachments/audit/fjhy/%s/03_gone.png" % bill
    _insert_att(bill, ghost, seq=3)

    ax.repair_paths()                     # 第一遍：改写 legacy 行
    row2 = db.query_one("SELECT stored_path FROM attachment "
                        "WHERE bill_no=%s AND seq=2", (bill,))
    assert row2["stored_path"] == rel, "旧绝对值没被改写成相对值"

    r2 = ax.repair_paths()                # 第二遍：不动点
    assert r2["fixed"] == 0, "repair 不幂等，第二遍还在改写"

    row3 = db.query_one("SELECT stored_path FROM attachment "
                        "WHERE bill_no=%s AND seq=3", (bill,))
    assert row3 and row3["stored_path"] == ghost, \
        "两边都没文件的行被改了或被删了——登记在而文件不在是要人查的状态"
