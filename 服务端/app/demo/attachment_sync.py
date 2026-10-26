# -*- coding: utf-8 -*-
"""启动链的附件同步入口：抽 xlsx 内嵌真实附件 + 修复 stored_path。幂等。

用法（与 seed.py 同一种脚本模式）：

  python -X utf8 -I app/demo/attachment_sync.py           # 抽取 + 修复 + 体检
  python -X utf8 -I app/demo/attachment_sync.py --status  # 只打印体检

为什么需要这个独立入口（2026-10-25，待办①的病根之一）：

  部署/entrypoint.sh 一直在跑抽取，scripts/start.ps1 却从来没有——
  同一套代码，docker 环境里 40 张真实单带着 219 张附件图，本地环境却是
  「40 张单全都没有附件」（实测 /api/attachments/status：磁盘 219、库里 0、
  孤儿 219）。因为 seed.py 的 clear() 会整表清掉 attachment，而磁盘上的图
  不跟着删；不补抽一次，页面上就和已被推翻的缺口 G-29 长得一模一样。

  attachment_extract.py 自己跑不了脚本模式：它模块级是相对导入
  （`from .. import config, db`），`python -I app/perception/….py` 时
  __package__ is None，第一行就 ImportError。所以照 seed.py 的样子，
  给启动链一个绝对导入的小入口。
"""
import argparse
import json
import os
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import config                                   # noqa: E402
from app.perception import attachment_extract as ax      # noqa: E402
from app.perception.attachment_extract import ExtractError  # noqa: E402

MATERIAL = os.path.join(config.PROJECT_ROOT, "材料", "单据明细表与审批意见")


def main():
    ap = argparse.ArgumentParser(description="xlsx 内嵌附件抽取 + stored_path 修复（全部幂等）")
    ap.add_argument("--status", action="store_true", help="只打印附件体检，不抽取不修复")
    args = ap.parse_args()

    st = ax.status()
    if args.status:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if not os.path.isdir(MATERIAL):
        print("  !! 材料/单据明细表与审批意见 不在（%s），没法抽取" % MATERIAL)
        return 1

    files = sorted(f for f in os.listdir(MATERIAL)
                   if f.endswith(".xlsx") and not f.startswith("~$"))
    saved = already = 0
    for f in files:
        # 单份失败不停整个同步：材料目录里将来多出一份没有「报销附件」表的
        # xlsx 是完全可能的，它的附件本来就不该抽。
        try:
            r = ax.extract_xlsx(os.path.join(MATERIAL, f))
        except ExtractError as e:
            print("  %s：跳过（%s）" % (f, e))
            continue
        saved += r["saved"]
        already += r["already"]
        print("  %s：新登记 %d · 已在 %d" % (f, r["saved"], r["already"]))
        for w in r["warnings"]:
            print("  !! " + w)

    rep = ax.repair_paths()
    print("  stored_path 修复：改写 %d · 已规范 %d · 定位不到 %d（保留登记，不删）"
          % (rep["fixed"], rep["kept"], rep["missing"]))

    st = ax.status()
    print("  体检：真实单 %d 张 · 带附件 %d · 库里 %d 条 · 磁盘 %d 个 · 孤儿 %d"
          % (st["real_bills"], st["real_bills_with_attachment"],
             st["extracted_total"], st["files_on_disk"], st["orphan_files"]))
    if st["orphan_files"]:
        print("  !! " + st["orphan_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
