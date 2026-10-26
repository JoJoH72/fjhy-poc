# -*- coding: utf-8 -*-
"""把 材料/差旅审核制度规则 预置进制度文件库（幂等，走真实上传路径）。

用法（与 seed.py 同一种脚本模式）：

  python -X utf8 -I app/demo/preset_doclib.py

为什么需要（2026-10-25，演示待办③）：

  文件库的上传/登记/解析链路是全的，但演示环境从来没传过材料——
  托管目录空、库里无登记，客户在页面上「看不到已上传材料」。
  预置走 doclib.save_uploads() 同一条路（暂存 → sha256 去重 → 原子改名 →
  自动扫描登记），上传台账里会留下真实记录（uploaded_by=预置），
  不是绕过链路直接塞表——那样台账对不上，页面上的数字就成了假话。

  **刻意排除「差旅费审核规则.xlsx」**：它是 10-26 现场演示「上传 → 增量感知 →
  四步抽取 → 专家审定」动线的素材（表格行 → 确定性列映射，最适合现场展示）。
  预置了它，现场增量就是 0，动线就断了。彩排也不要用它——
  sha256 按内容去重，素材烧一次少一次（重传同内容会被拒，改名也没用）。
"""
import os
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import config                        # noqa: E402
from app import doclib                        # noqa: E402

SRC = os.path.join(config.PROJECT_ROOT, "材料", "差旅审核制度规则")

# 现场演示的上传素材，预置时跳过（见模块注释）。
LIVE_DEMO_SKIP = {"差旅费审核规则.xlsx"}


def main():
    if not os.path.isdir(SRC):
        print("  !! 材料/差旅审核制度规则 不在（%s），没法预置" % SRC)
        return 1

    names = sorted(f for f in os.listdir(SRC)
                   if not f.startswith("~$")           # Office 打开时的临时文件
                   and f not in LIVE_DEMO_SKIP)
    items = []
    for n in names:
        p = os.path.join(SRC, n)
        if not os.path.isfile(p):
            continue
        items.append((n, open(p, "rb")))
    if not items:
        print("  !! 目录里没有可预置的文件")
        return 1

    # 显式指定实例：预置不依赖「当前实例」是谁——万一有人切到别的实例，
    # 预置也不该跟着跑偏。
    res = doclib.save_uploads(items, instance_id="fjhy", by="预置")
    seen = sum(1 for r in res["items"] if r["result"] == "同内容已存在")
    for r in res["rejected"]:
        if r["result"] != "同内容已存在":
            print("  %s：%s（%s）" % (r["file_name"], r["result"], r["reason"]))
    print("  预置 %d 份：新入 %d · 已在 %d —— 幂等，重跑不重复入库"
          % (len(res["items"]), res["ok_count"], seen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
