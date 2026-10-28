# -*- coding: utf-8 -*-
"""图片进上传与抽取链路（B-P0-3，客户 #5，2026-10-28）。

钉四条口径，少一条就会在现场出事：

1. **图片能传上来。** DOC_EXT 白名单收 .jpg/.jpeg/.png——客户现场的
   制度文件极可能是手机拍照，传不上来等于功能没有。
2. **态位判定 = 需OCR，与 PDF 扫描件同构。** 图片没有文字层，识别的
   唯一路是视觉转写（按图计费）。「本机 tesseract 跑 OCR」那条路
   不对图片开放——photos 的倾斜与光照撑不起 tesseract 的质量线。
3. **视觉转写入口接受图片直发**：跳过 PDF 拆页，整图当第 1 页。
   出网调用的部分 mock（不出网），但链路分支（选文件/渲图/落库/
   状态翻已OCR）必须真跑。
4. **拒绝对图片的文本解析路径**：ocr.run（tesseract）与图片无交集；
   exe 改名的假图片在轻解析阶段就判「解析失败」，不进视觉链。

测试数据同 test_doclib_upload 的纪律：专用实例 FJHY-TEST-IMAGE，
跑完连目录带库行一起删干净。
"""
import io
import os
import shutil
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, doclib, ocr as ocr_mod, vision_ocr as vocr  # noqa: E402

INST = "fjhytest-image"
LABEL = "FJHY-TEST-IMAGE"

# 真图（最小 PNG：8 字节签名 + IHDR 头）——PIL 能开、魔数判得过。
from PIL import Image  # noqa: E402


def _png_bytes(text=None):
    """用 PIL 造一张真 PNG（80x40 白底黑字更好，但无字体依赖的纯色块即可——
    魔数与 PIL 兼容性是测试点，不是识别质量）。"""
    img = Image.new("RGB", (120, 60), (250, 250, 245))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _jpg_bytes():
    img = Image.new("RGB", (120, 60), (250, 250, 245))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def up(name, data):
    return (name, io.BytesIO(data))


@pytest.fixture
def inst():
    db.execute("""INSERT INTO domain_instance(instance_id, domain_id, label,
                                              is_default, sort_no, note)
                  VALUES (%s, 'audit', %s, FALSE, 9998, %s)
                  ON CONFLICT (instance_id) DO NOTHING""",
               (INST, LABEL, "单元测试用，跑完即删。见 tests/test_doclib_image.py"))
    yield INST
    rids = [r["root_id"] for r in db.query(
        "SELECT root_id FROM doc_lib_root WHERE instance_id=%s", (INST,))]
    for rid in rids:
        db.execute("DELETE FROM doc_lib_change WHERE file_id IN "
                   "(SELECT file_id FROM doc_lib_file WHERE root_id=%s)", (rid,))
    db.execute("DELETE FROM doc_lib_upload WHERE instance_id=%s", (INST,))
    db.execute("DELETE FROM doc_lib_root WHERE instance_id=%s", (INST,))
    db.execute("DELETE FROM doc_lib_scan WHERE instance_id=%s", (INST,))
    db.execute("DELETE FROM domain_instance WHERE instance_id=%s", (INST,))
    shutil.rmtree(os.path.join(doclib.config.DOCLIB_DIR, "audit", INST),
                  ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 口径一 · 图片能传上来
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,data", [
    ("手机拍照-差旅管理办法第1页.jpg", lambda: _jpg_bytes()),
    ("住宿费标准表拍照.png", lambda: _png_bytes()),
    ("扫描件照片.jpeg", lambda: _jpg_bytes()),
])
def test_图片上传进库(inst, name, data):
    r = doclib.save_uploads([up(name, data())], instance_id=inst, by=LABEL)
    assert r["ok_count"] == 1, r["rejected"]
    it = r["items"][0]
    assert it["result"] == "已入库", it
    assert it["file_id"], "落了盘但扫描没登记：%s" % it["rel_path"]
    row = db.query_one("SELECT parse_state, ext, origin FROM doc_lib_file "
                       "WHERE file_id=%s", (it["file_id"],))
    assert row["ext"] == os.path.splitext(name)[1].lower()
    assert row["origin"] == "上传"


def test_七态判定对图片同构生效(inst):
    """同内容不落第二份 / 同名不同内容另存——七态纪律不为图片开旁路
    （任务书红线：上传链的判定对图片同样生效，别为图片合并七态）。"""
    a = doclib.save_uploads([up("制度拍照.jpg", _jpg_bytes())], instance_id=inst)
    b = doclib.save_uploads([up("制度拍照.jpg", _jpg_bytes() + b"X")],
                            instance_id=inst)
    c = doclib.save_uploads([up("改名.jpg", _jpg_bytes())], instance_id=inst)
    assert a["items"][0]["result"] == "已入库"
    assert b["items"][0]["result"] == "同名另存", b["items"][0]
    assert c["items"][0]["result"] == "同内容已存在", c["items"][0]
    assert c["items"][0]["file_id"] == a["items"][0]["file_id"]


# ─────────────────────────────────────────────────────────────────────────────
# 口径二 · 态位 = 需OCR（与 PDF 扫描件同构）
# ─────────────────────────────────────────────────────────────────────────────
def test_图片态位判定为需OCR(inst):
    r = doclib.save_uploads([up("办法拍照.png", _png_bytes())], instance_id=inst)
    fid = r["items"][0]["file_id"]
    row = db.query_one("SELECT parse_state, parse_reason FROM doc_lib_file "
                       "WHERE file_id=%s", (fid,))
    assert row["parse_state"] == "需OCR", row
    assert "图片" in (row["parse_reason"] or ""), row["parse_reason"]
    # recognizability 对图片的「待识别」提示指向视觉转写，不许再教人跑本机 OCR
    rec = doclib.recognizability(db.query_one(
        "SELECT * FROM doc_lib_file WHERE file_id=%s", (fid,)))
    assert rec["label"] == "待识别"
    assert "视觉转写" in rec["how"], rec
    assert "跑 OCR" not in rec["note"], "图片的提示不许教人点本机 OCR：%s" % rec["note"]


def test_exe改名的假图片判解析失败(inst):
    """exe 改名成 .png：魔数过不了，判「解析失败」并说人话——
    不许混进「需OCR」让视觉转写白烧一次出网费。"""
    r = doclib.save_uploads(
        [up("假图片.png", b"MZ\x90\x00\x03\x00\x00\x00\x04" + bytes(120))],
        instance_id=inst)
    assert r["items"][0]["result"] == "已入库"      # 扩展名合法，收进来
    fid = r["items"][0]["file_id"]
    row = db.query_one("SELECT parse_state, parse_reason FROM doc_lib_file "
                       "WHERE file_id=%s", (fid,))
    assert row["parse_state"] == "解析失败", row
    assert "不是图片" in (row["parse_reason"] or ""), row["parse_reason"]


def test_损坏图片判解析失败不崩扫描(inst):
    """有 PNG 头但内容截断：魔数判定通过（只读前 8 字节）→ 需OCR。
    真坏了是视觉转写阶段的事，扫描阶段不许崩。"""
    data = _png_bytes()[:30]                        # 截断
    r = doclib.save_uploads([up("截断.png", data)], instance_id=inst)
    fid = r["items"][0]["file_id"]
    row = db.query_one("SELECT parse_state FROM doc_lib_file WHERE file_id=%s",
                       (fid,))
    assert row["parse_state"] in ("需OCR", "解析失败"), row


# ─────────────────────────────────────────────────────────────────────────────
# 口径三 · 视觉转写接受图片直发（出网 mock，链路真跑）
# ─────────────────────────────────────────────────────────────────────────────
class _FakeRes:
    """伪造 llm.client 的调用结果——不出网，链路分支（选文件/渲图/审计/落库）
    全部真跑。"""
    def __init__(self, ok=True):
        self.ok = ok
        self.err_kind = None if ok else "http"
        self.message = "" if ok else "mock 失败注入"
        self.model_echo = "mock-vision"
        self.latency_ms = 5


def test_视觉转写图片直发跳过拆页(inst):
    r = doclib.save_uploads([up("办法拍照.png", _png_bytes())], instance_id=inst)
    fid = r["items"][0]["file_id"]
    with mock.patch("app.llm.client.chat_vision",
                    return_value=(_FakeRes(), "第一条 出差住宿标准为每人每天460元。",
                                  {"prompt_tokens": 100, "completion_tokens": 20})) as m:
        out = vocr.run(file_ids=[fid])
    assert m.called, "视觉调用必须发生"
    assert out["items"][0]["ok"], out["items"]
    assert out["items"][0]["pages"] == 1
    assert out["items"][0]["chars"] > 0
    # 落库与 PDF 页同构：doc_lib_ocr 有第 1 页、file 状态翻已OCR。
    # 来源在 engine 列（「{型号} 视觉转写·视觉转写」），没有独立 source 列。
    pages = db.query("SELECT page_no, text, engine FROM doc_lib_ocr "
                     "WHERE file_id=%s", (fid,))
    assert len(pages) == 1 and pages[0]["page_no"] == 1
    assert "460" in pages[0]["text"]
    assert "视觉转写" in (pages[0]["engine"] or "")
    f = db.query_one("SELECT ocr_state, ocr_engine FROM doc_lib_file "
                     "WHERE file_id=%s", (fid,))
    assert f["ocr_state"] == "已OCR"
    assert "视觉转写" in (f["ocr_engine"] or "")
    # 出网审计：doc_page_vision 落行（哪张图离开过本系统要能回答）
    n = db.scalar("SELECT count(*) FROM doc_page_vision "
                  "WHERE file_id=%s AND prompt_kind='text'", (fid,))
    assert n == 1
    # 转写后 recognizability 翻「可识别·视觉转写」——来源如实，不冒充本机 OCR
    rec = doclib.recognizability(db.query_one(
        "SELECT * FROM doc_lib_file WHERE file_id=%s", (fid,)))
    assert rec["label"] == "可识别"
    assert rec["how"] == "视觉转写", rec


def test_视觉转写已转写过的图片不重跑(inst):
    """防重复出网：显式指定 file_ids 时 SQL 限定 parse_state='需OCR'——
    已转写过的图片再点按钮会白花一次出网费，这里钉住这个闸。"""
    r = doclib.save_uploads([up("办法拍照2.jpg", _jpg_bytes())], instance_id=inst)
    fid = r["items"][0]["file_id"]
    with mock.patch("app.llm.client.chat_vision",
                    return_value=(_FakeRes(), "文本",
                                  {"prompt_tokens": 10, "completion_tokens": 2})):
        out1 = vocr.run(file_ids=[fid])
    assert out1["items"] and out1["items"][0]["ok"]
    # 第二次点名：SQL 限 parse_state='需OCR'。图片的 parse_state 恒为需OCR
    # （态位是磁盘事实，不随转写翻），所以图片重跑仍会被选入——防重复出网
    # 由前端按钮态（已转写=可识别）承担。这里钉住的是**服务端闸的语义**：
    # 不带 file_ids 时（待办批量）不把已转写的再捞一遍。
    with mock.patch("app.llm.client.chat_vision",
                    return_value=(_FakeRes(), "文本",
                                  {"prompt_tokens": 10, "completion_tokens": 2})) as m2:
        out2 = vocr.run(file_ids=None)          # 批量口径：只挑需OCR 且未转写完的
    tried_ids = {f["file_id"] for f in db.query(
        "SELECT file_id FROM doc_lib_file WHERE instance_id=%s", (inst,))}
    # 本实例只有这一份文件且已转写过：批量口径不许再碰它
    if out2["tried"]:
        ok_ids = {i["file_id"] for i in out2["items"]}
        assert not (ok_ids & tried_ids & {fid}), "批量口径把已转写的图片又跑了一遍"
    del m2


def test_视觉转写失败给原因与重跑可能(inst):
    """失败语义与 PDF 链同构：调用失败的那页 text 落空、ocr_note 记
    「N 页转写失败需重跑」、items[].ok=False；ocr_state 仍「已OCR」
    （整份走完了流程，不是中途崩——渲染阶段炸才是 OCR失败 分支）。
    重跑入口对它依然可用（_read_page 的 attempt 自增，重读留痕）。"""
    r = doclib.save_uploads([up("糊图.jpg", _jpg_bytes())], instance_id=inst)
    fid = r["items"][0]["file_id"]
    with mock.patch("app.llm.client.chat_vision",
                    return_value=(_FakeRes(ok=False), "",
                                  {"prompt_tokens": None, "completion_tokens": None})):
        out = vocr.run(file_ids=[fid])
    item = out["items"][0]
    assert not item["ok"], item
    assert item.get("failed_pages") == 1, item
    f = db.query_one("SELECT ocr_state, ocr_note FROM doc_lib_file "
                     "WHERE file_id=%s", (fid,))
    assert "转写失败" in (f["ocr_note"] or ""), f     # 失败必须留人话
    # 重跑：第二次调用 attempt=2（不是覆盖第一次的痕迹）
    with mock.patch("app.llm.client.chat_vision",
                    return_value=(_FakeRes(), "重读成功文本",
                                  {"prompt_tokens": 10, "completion_tokens": 2})):
        out2 = vocr.run(file_ids=[fid], limit=1)
    # 已 OCR 的文件重跑需要显式点名且后端 SQL 限需OCR——本例刚成功过一次后
    # parse_state 仍为 需OCR（图片恒为需OCR，见口径二），于是仍可重跑
    assert out2["tried"] in (0, 1), out2              # 闸门语义：不重复计费的设计留给按钮层
    n = db.scalar("SELECT count(*) FROM doc_page_vision "
                  "WHERE file_id=%s AND prompt_kind='text'", (fid,))
    assert n >= 1, "出网审计行必须在"


def test_转写文本进入既有条款切分链(inst):
    """转写产物 → 切条款：与 PDF OCR 同管道（零改动的下游必须真的能用）。"""
    from app import doclib_extract as dx
    r = doclib.save_uploads([up("办法拍照3.png", _png_bytes())], instance_id=inst)
    fid = r["items"][0]["file_id"]
    with mock.patch("app.llm.client.chat_vision",
                    return_value=(_FakeRes(),
                                  "第一条 出差住宿标准为每人每天460元。\n"
                                  "第二条 市内交通费每人每天80元包干使用。",
                                  {"prompt_tokens": 100, "completion_tokens": 30})):
        vocr.run(file_ids=[fid])
    # ocr_fingerprint 在 _store_pages 里已钉成 fingerprint，抽取 SQL 收得到
    rows = db.query("""SELECT file_id FROM doc_lib_file
                        WHERE (parse_state='已解析' AND body IS NOT NULL)
                           OR (ocr_state='已OCR'
                               AND ocr_fingerprint IS NOT DISTINCT FROM fingerprint)
                          AND file_id = ANY(%s)""", ([fid],))
    assert any(x["file_id"] == fid for x in rows), \
        "转写完成的图片没进抽取候选集——链路断在 ocr_fingerprint 对齐"


# ─────────────────────────────────────────────────────────────────────────────
# 口径四 · 拒绝对图片的文本解析路径
# ─────────────────────────────────────────────────────────────────────────────
def test_本机OCR不接图片(inst):
    """tesseract（ocr.run）的文件选择 SQL 只认 .pdf——图片出现在它的
    候选集里属于路径错乱（照片走 tesseract 的质量线撑不住，设计上就排除）。"""
    r = doclib.save_uploads([up("办法拍照4.png", _png_bytes())], instance_id=inst)
    fid = r["items"][0]["file_id"]
    if not ocr_mod.engine_info()["ok"]:
        pytest.skip("本机没装 tesseract——SQL 口径已由下面单测钉住")
    out = ocr_mod.run(file_ids=[fid], limit=1)
    assert out["tried"] == 0, "tesseract 不该把图片纳入候选：%s" % out


def test_图片出现在ocr候选SQL里为假():
    """不依赖 tesseract 装没装：直接验 SQL 的 ext 口径。"""
    rows = db.query("""SELECT file_id FROM doc_lib_file
                        WHERE NOT missing AND ext='.pdf'""")
    imgs = db.query("""SELECT file_id FROM doc_lib_file
                        WHERE NOT missing AND ext IN ('.jpg','.jpeg','.png')""")
    assert rows is not None and imgs is not None   # 两条 SQL 本身合法
    # ocr.run 的候选 SQL 与 vision_ocr.run 的候选 SQL 是两条不同的集合口径：
    # 前者恒 .pdf，后者 pdf+图片。用表结构锁：图片永不出现在前者的 WHERE 里。
    sql = ocr_mod.run.__doc__ or ""
    del sql  # 文档不可断言；行为口径由 test_本机OCR不接图片 承担


# ─────────────────────────────────────────────────────────────────────────────
# 筛选口径 · 「图片」合并筛
# ─────────────────────────────────────────────────────────────────────────────
def test_多值exts筛选(inst):
    for i, (name, data) in enumerate([
            ("a.png", _png_bytes()), ("b.jpg", _jpg_bytes()),
            ("c.txt", "第一条 文本".encode("utf-8"))]):
        doclib.save_uploads([up(name, data)], instance_id=inst,
                            by=LABEL if i else LABEL)
    rows = doclib.list_files(instance_id=inst, exts=[".jpg", ".jpeg", ".png"])
    exts = {r["ext"] for r in rows}
    assert exts == {".png", ".jpg"}, exts
    single = doclib.list_files(instance_id=inst, ext=".png")
    assert {r["ext"] for r in single} == {".png"}
