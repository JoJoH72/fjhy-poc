# -*- coding: utf-8 -*-
"""视觉模型转文字：扫描件 → 多模态模型逐页转写 → doc_lib_ocr。

2026-10-26 用户需求：图片型材料（扫描 PDF 等）上传后应全自动进入规则抽取，
不再人工转文字。设计三条纪律：

1. **原件永远不动，文本是派生物。** 磁盘上只有客户传的那份原件；转写文本按页
   写进 doc_lib_ocr——和本机 tesseract **同一张表、同一条下游管道**（切条款/
   特征/候选/AI 草稿全部复用），另留每页渲好的 PNG 供人眼复核。
   不是再造一份「文字版 PDF 文件」，是「原件 + 页图 + 页文本」三层，
   文件清单里不会多出第二个文件，去重与溯源都不受影响。

2. **页级判定，混合材料自动分流。** 每页先抽原生文字层：非空直接用（零成本、
   零失真），空的页才发视觉模型。纯图件＝全部页走视觉；文字夹杂图的件＝
   文字页取原生、图片页转写，每页来源如实落库（原生文本 / 视觉转写）。

3. **页间并行、不开思考。** 页与页无依赖，按 llm_config.concurrency 并行
   （4 页＝4 路同时读）；thinking 关闭走配置既有约定。**每一页的出网调用
   都落 doc_page_vision 审计（prompt_kind=text，与读图同枚举）**——哪页图离开过
   本系统、发给了哪个型号、多少字节，事后必须能回答（读图纪律，同 chat_vision
   的文档要求）。
"""
import datetime
import io
import os
from concurrent.futures import ThreadPoolExecutor

from . import config, db
from .llm import client
from .ocr import detect_table_image, out_dir

SYSTEM = ("你是文档转写器。逐字转写图片里的全部文字，保持原有的段落与条目顺序；"
          "表格一行一行转写（单元格之间用「；」分隔）；"
          "只输出原文，不解释、不总结、不修正、不添加任何原文没有的内容。")

# 一页最多可能多少输出 token：公文一页约 400~900 字，密集表格页可到 1500 字，
# 给 3000 封顶。模型侧思考已按配置关闭。
PAGE_MAX_TOKENS = 3000


def _cfg(config_id=None):
    """取读图配置：复用 vision_read 用途（本系统唯一「发图给模型」的口径，
    型号、计费、审计都只有一份答案）。"""
    if config_id:
        return db.query_one("SELECT * FROM llm_config WHERE config_id=%s", (config_id,))
    row = db.query_one("""SELECT * FROM llm_config
                          WHERE enabled AND purpose='vision_read'
                          ORDER BY is_default DESC, updated_at DESC LIMIT 1""")
    if not row:
        raise RuntimeError("没有启用的「读票据/行程单图」用途模型。"
                           "到「大模型配置」页给 vision_read 配一条并启用。")
    return row


def _read_page(cfg, file_id, page_no, img_path):
    """一页图 → 视觉模型转写文本。审计先行（出网的页必须留痕）。→ dict"""
    attempt = (db.scalar("""SELECT max(attempt) FROM doc_page_vision
                             WHERE file_id=%s AND page_no=%s
                               AND prompt_kind='text'""",
                         (file_id, page_no)) or 0) + 1
    res, content, use = client.chat_vision(
        cfg, SYSTEM, "请转写这一页的全部文字。", img_path, max_tokens=PAGE_MAX_TOKENS)
    db.execute("""INSERT INTO doc_page_vision(
                    file_id, page_no, attempt, config_id, model_name, model_echo,
                    prompt_kind, image_path, image_bytes, ok, err_kind, message)
                  VALUES (%s,%s,%s,%s,%s,%s,'text',%s,%s,%s,%s,%s)""",
               (file_id, page_no, attempt, cfg["config_id"], cfg["model_name"],
                res.model_echo, img_path,
                os.path.getsize(img_path) if os.path.exists(img_path) else None,
                res.ok, None if res.ok else res.err_kind,
                None if res.ok else (res.message or "")[:300]))
    return {"page_no": page_no, "ok": res.ok,
            "text": (content or "").strip() if res.ok else "",
            "err": None if res.ok else (res.message or "")[:160],
            "latency_ms": res.latency_ms}


def _render_file(path, file_id, dpi=150, max_pages=60):
    """渲染阶段：逐页判原生文字层，图片页渲 PNG。→ (native, jobs, n_pages)

    native = {页码: 页dict}（原生文字页，零成本零失真）
    jobs   = [(页码, 图路径, 是否表格页)]（需要发视觉模型的页）

    2026-10-28（B-P0-3，客户 #5）：path 是 .jpg/.jpeg/.png 时**整图直发**——
    跳过 PDF 拆页那一层，图片自己就是「第 1 页」。计费口径不变：一张图片
    = 一次视觉调用 = 一页。落库（doc_lib_ocr 的 image_path、doc_page_vision
    的审计行）与 PDF 拆出来的页完全同构，下游切条款/草稿零改动。
    """
    from PIL import Image
    from .doclib import IMAGE_EXT

    img_dir = os.path.join(out_dir(), file_id)
    os.makedirs(img_dir, exist_ok=True)

    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        # 图片直发：p001.jpg 是发模型的最终产物（JPEG q85），
        # 原件是 PNG 时也压一道——大 PNG 在网关侧预填充慢（PDF 链实测）。
        try:
            img = Image.open(path).convert("RGB")
        except Exception:                                      # noqa: BLE001
            raise
        img_path = os.path.join(img_dir, "p001.jpg")
        img.save(img_path, "JPEG", quality=85)
        try:
            px_table, _, _ = detect_table_image(img)
        except Exception:                                      # noqa: BLE001
            px_table = False
        return {}, [(1, img_path, px_table)], 1

    import fitz
    doc = fitz.open(path)
    try:
        native, jobs = {}, []
        n_pages = doc.page_count
        for i, page in enumerate(doc, 1):
            if i > max_pages:
                break
            text = (page.get_text() or "").strip()
            if text:
                native[i] = text
                continue
            pix = page.get_pixmap(dpi=dpi)
            png = pix.tobytes("png")
            # **发模型前压成 JPEG**：150dpi 的 PNG 红头页实测 877KB，大图在
            # 网关侧预填充要 20~30 秒（并发救不了单页大图）；JPEG q85 只有
            # 1/5~1/8 体积、识别质量几乎无损。PNG 不发，JPEG 同时留作人眼复核。
            img = Image.open(io.BytesIO(png)).convert("RGB")
            img_path = os.path.join(img_dir, "p%03d.jpg" % i)
            img.save(img_path, "JPEG", quality=85)
            try:
                px_table, _, _ = detect_table_image(Image.open(io.BytesIO(png)))
            except Exception:                                  # noqa: BLE001
                px_table = False
            jobs.append((i, img_path, px_table))
        return native, jobs, n_pages
    finally:
        doc.close()


def run(file_ids=None, limit=3, config_id=None):
    """对「需OCR」的文件跑视觉转写并落库（doc_lib_ocr，与 tesseract 同管道）。

    2026-10-26 提速版：**所有文件的所有图片页进一个全局线程池**——份与份
    不再串行。演示场景是"一次传几份扫描件"，串行时 3 份×30 秒=90 秒没人
    受得了；全局池按页为粒度调度，墙钟 ≈ 总页数 ÷ 并行度的单页时延。
    落库仍按文件聚合、串行写（psycopg2 连接不跨线程共享）。

    2026-10-28（B-P0-3）：ext 白名单加图片（.jpg/.jpeg/.png）——图片恒走
    视觉链（与扫描 PDF 同构，只是没有拆页这一步）。注意：仍只收
    parse_state='需OCR' 的——.exe 改名的假图片在 scan 阶段已判「解析失败」，
    这里不许再把它们捞进来白烧一次出网费。
    """
    cfg = _cfg(config_id)
    sql = ["""SELECT file_id, abs_path, file_name, fingerprint, parse_state, ext
                FROM doc_lib_file
               WHERE NOT missing AND (ext='.pdf' OR ext IN ('.jpg','.jpeg','.png'))"""]
    args = []
    if file_ids:
        # 显式指定时**仍限定 parse_state='需OCR'**：已转写过的再点一遍按钮
        # 会白花一整份的出网费（PDF 与图片同口径）。
        sql.append("AND file_id = ANY(%s) AND parse_state='需OCR'")
        args.append(list(file_ids))
    else:
        sql.append("AND parse_state='需OCR'")
    sql.append("ORDER BY rel_path LIMIT %s")
    args.append(max(1, min(20, int(limit))))
    files = db.query(" ".join(sql), tuple(args))

    engine = "%s 视觉转写" % cfg["model_name"]
    t0 = datetime.datetime.now()
    items = []
    plans = []        # (file_row, native, jobs, n_pages)
    for f in files:
        try:
            native, jobs, total = _render_file(f["abs_path"], f["file_id"])
            plans.append((f, native, jobs, total))
        except Exception as e:                              # noqa: BLE001
            db.execute("""UPDATE doc_lib_file SET ocr_state='OCR失败', ocr_at=now(),
                                 ocr_note=%s WHERE file_id=%s""",
                       ("视觉转写 %s: %s" % (type(e).__name__, e), f["file_id"]))
            items.append({"file_id": f["file_id"], "file_name": f["file_name"],
                          "ok": False, "error": str(e)})

    # 全局池：页为粒度，上限 2×配置并发、封顶 8（网关实测 6 并发稳定）
    all_jobs = [(f, j) for f, _, js, _ in plans for j in js]
    if all_jobs:
        workers = min(max(int(cfg.get("concurrency") or 3) * 2, 4), len(all_jobs), 8)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            got = list(pool.map(
                lambda t: _read_page(cfg, t[0]["file_id"], t[1][0], t[1][1]),
                all_jobs))
        # 键必须带 file_id：多文件并行时页码会撞车（A 的第 1 页 ≠ B 的第 1 页）
        by_key = {}
        for (fj, _jj), g in zip(all_jobs, got):
            by_key[(fj["file_id"], g["page_no"])] = g
        for f, native, jobs, total in plans:
            pages = []
            for no, text in native.items():
                pages.append({"page_no": no, "text": text, "chars": len(text),
                              "source": "原生文本", "has_table": False, "ok": True})
            for no, img_path, px_table in jobs:
                g = by_key.get((f["file_id"], no)) or {}
                pages.append({"page_no": no, "text": g.get("text") or "",
                              "chars": len(g.get("text") or ""),
                              "source": "视觉转写", "has_table": px_table,
                              "ok": g.get("ok", False)})
            pages.sort(key=lambda p: p["page_no"])
            _store_pages(f, pages, total, engine)
            failed_pages = sum(1 for p in pages if not p["ok"])
            chars = sum(p["chars"] for p in pages)
            items.append({"file_id": f["file_id"], "file_name": f["file_name"],
                          "ok": not failed_pages, "pages": len(pages),
                          "total_pages": total, "chars": chars,
                          "vision_pages": len(jobs), "native_pages": len(native),
                          "failed_pages": failed_pages})
    else:
        for f, native, jobs, total in plans:
            pages = sorted(
                [{"page_no": no, "text": t, "chars": len(t), "source": "原生文本",
                  "has_table": False, "ok": True} for no, t in native.items()],
                key=lambda p: p["page_no"])
            _store_pages(f, pages, total, engine)
            items.append({"file_id": f["file_id"], "file_name": f["file_name"],
                          "ok": True, "pages": len(pages), "total_pages": total,
                          "chars": sum(p["chars"] for p in pages),
                          "vision_pages": 0, "native_pages": len(native),
                          "failed_pages": 0})

    secs = round((datetime.datetime.now() - t0).total_seconds(), 1)
    done = sum(1 for i in items if i.get("ok"))
    return {"engine": engine, "tried": len(files), "done": done,
            "failed": len(files) - done, "elapsed_s": secs, "items": items,
            "note": "视觉转写**真出网、真计费**（每页一次调用，全局按页并行）。"
                    "转写文本与 tesseract OCR 同管道存放，可信度比原生文本低一档："
                    "数字一律人工复核，不直接进硬规则阈值。"}


def _store_pages(f, pages, total, engine):
    """一个文件的页文本 → doc_lib_ocr + doc_lib_file 状态（与 tesseract 同口径）。"""
    db.execute("DELETE FROM doc_lib_ocr WHERE file_id=%s", (f["file_id"],))
    chars = 0
    tbl = 0
    failed_pages = 0
    for p in pages:
        db.execute("""INSERT INTO doc_lib_ocr(
                file_id, page_no, text, chars, mean_conf, low_conf_words,
                word_cnt, has_table, table_note, engine, dpi, image_path)
              VALUES (%s,%s,%s,%s,NULL,0,%s,%s,%s,%s,150,%s)""",
            (f["file_id"], p["page_no"], p["text"], p["chars"], p["chars"] // 2,
             p["has_table"],
             ("检测到表格（像素线条）。**转写文本的行列对应关系不保证**，"
              "数字请对照页图人工核对") if p["has_table"] else None,
             "%s·%s" % (engine, p["source"]),
             os.path.join(out_dir(), f["file_id"], "p%03d.jpg" % p["page_no"])
             if p["source"] == "视觉转写" else None))
        chars += p["chars"]
        if p["has_table"]:
            tbl += 1
        if not p["ok"]:
            failed_pages += 1
    db.execute("""UPDATE doc_lib_file
                     SET ocr_state='已OCR', ocr_at=now(), ocr_chars=%s, ocr_pages=%s,
                         ocr_table_pages=%s, ocr_mean_conf=NULL, ocr_engine=%s,
                         ocr_fingerprint=fingerprint, ocr_note=%s
                   WHERE file_id=%s""",
               (chars, len(pages), tbl, engine,
                ("视觉转写：文本来自多模态模型，可信度比原生文本低一档，"
                 "数字一律人工复核" +
                 ("；%d 页转写失败需重跑" % failed_pages if failed_pages else "")),
                f["file_id"]))
