# -*- coding: utf-8 -*-
"""
打包体积实测。

量什么：
    out/          electron-vite 的构建产物 —— 这是要交出去、要装到用户机器上的东西
    node_modules/ 依赖树 —— 它**不进交付包**（打包时只把 renderer 里真正 import 到的
                  模块打进去），列出来是为了回答"为什么装一次要几百 MB"

为什么 out/ 要拆到子目录 + 拆 live2d：
    out/ 一共才几 MB，其中 Live2D 的官方样例模型（Haru / Shizuku）占了大头，
    而它们按《素材授权调研》的结论是**开发期验证链路用的，不进最终交付物**。
    不拆出来的话，评审看到"桌宠 7MB"会以为本体积就该这么大，
    拆出来才能说清"删掉样例模型之后真实交付体积是多少"。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_common import DESKTOP_DIR, PROJECT_DIR, RAW_DIR, ensure_dirs, run_utf8, write_csv, write_json  # noqa: E402

MB = 1024 * 1024


def tree_size(path):
    """返回 (总字节, 文件数)。"""
    total, files = 0, 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            fp = os.path.join(root, n)
            try:
                total += os.path.getsize(fp)
                files += 1
            except OSError:
                pass
    return total, files


def fmt(b):
    return round(b / MB, 2)


def main():
    run_utf8()
    ensure_dirs()

    out_dir = os.path.join(DESKTOP_DIR, "out")
    nm_dir = os.path.join(DESKTOP_DIR, "node_modules")
    src_dir = os.path.join(DESKTOP_DIR, "src")

    rows = []

    def add(group, name, path):
        if not os.path.exists(path):
            return
        b, n = tree_size(path)
        rows.append({"group": group, "item": name, "size_mb": fmt(b), "size_bytes": b, "files": n})

    # ── out/ 拆分 ──
    add("out", "out/ 合计", out_dir)
    add("out", "  out/main（主进程）", os.path.join(out_dir, "main"))
    add("out", "  out/preload（预加载桥）", os.path.join(out_dir, "preload"))
    add("out", "  out/renderer（渲染进程）", os.path.join(out_dir, "renderer"))

    l2d = os.path.join(out_dir, "renderer", "live2d")
    if not os.path.exists(l2d):
        # 打包产物里 public/ 是原样拷过来的，可能被 vite 放到根下
        alt = os.path.join(out_dir, "renderer", "public", "live2d")
        l2d = alt if os.path.exists(alt) else l2d
    add("out-breaking", "  ↳ 其中 Live2D 样例模型（开发期用，不进交付）", l2d)

    out_total = next((r["size_bytes"] for r in rows if r["item"] == "out/ 合计"), 0)
    l2d_size = next((r["size_bytes"] for r in rows if "Live2D" in r["item"]), 0)
    rows.append(
        {
            "group": "out-breaking",
            "item": "  ↳ 扣除 Live2D 样例模型后的交付体积",
            "size_mb": fmt(out_total - l2d_size),
            "size_bytes": out_total - l2d_size,
            "files": 0,
        }
    )

    # ── node_modules 拆分（只列前 12 大，全列没意义）──
    add("node_modules", "node_modules/ 合计（不进交付包）", nm_dir)
    if os.path.isdir(nm_dir):
        subs = []
        for name in os.listdir(nm_dir):
            p = os.path.join(nm_dir, name)
            if not os.path.isdir(p):
                continue
            if name.startswith("@"):
                for sub in os.listdir(p):
                    sp = os.path.join(p, sub)
                    if os.path.isdir(sp):
                        b, n = tree_size(sp)
                        subs.append((b, f"    @{name[1:]}/{sub}", n))
            else:
                b, n = tree_size(p)
                subs.append((b, "    " + name, n))
        subs.sort(reverse=True)
        for b, name, n in subs[:12]:
            rows.append({"group": "node_modules-top", "item": name, "size_mb": fmt(b), "size_bytes": b, "files": n})

    # ── 源码 ──
    add("source", "src/ 源码合计", src_dir)

    write_csv(os.path.join(RAW_DIR, "bundle_size.csv"), rows, ["group", "item", "size_mb", "size_bytes", "files"])
    write_json(
        os.path.join(RAW_DIR, "bundle_size.json"),
        {
            "out_total_mb": fmt(out_total),
            "live2d_samples_mb": fmt(l2d_size),
            "out_without_live2d_mb": fmt(out_total - l2d_size),
            "node_modules_total_mb": next((r["size_mb"] for r in rows if r["item"].startswith("node_modules/ 合计")), None),
            "rows": rows,
        },
    )

    print(f"{'项目':<46}{'大小(MB)':>10}{'文件数':>9}")
    for r in rows:
        print(f"{r['item']:<46}{r['size_mb']:>10}{r['files']:>9}")
    print("\n已写出 raw/bundle_size.csv 与 raw/bundle_size.json")


if __name__ == "__main__":
    main()
