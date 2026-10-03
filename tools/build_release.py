#!/usr/bin/env python3
"""构建发布压缩包：把工作台打成一份「下载解压就能跑」的 zip。

    python3 tools/build_release.py --version 1.1.0

为什么要单独写这个脚本，而不是 `zip -r`：
打包这个项目最大的风险不是打不出来，是**打进去不该打的东西**——
`backend_src/data/` 里有 145 MB 的成片、任务记录，还有浏览器 profile
（里面是 muse.ai 账号的 cookie）；`data/local_config.json` 里是本地 API Key。
这些东西一旦进了 zip，公开仓库的 Release 就等于把账号发出去。

所以这里用**白名单**而不是黑名单：只列明确要带的文件，其余一律不带。
新增文件不会「顺手」被带进包里，要带就得先在这里显式登记。
"""

import argparse
import os
import re
import stat
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

# ---- 要打包的文件（相对仓库根目录）------------------------------------
# 顺序即 zip 内的顺序，刻意让 README 和启动脚本排在最前面：
# 用户双击解开后，第一眼看到的就是「解压完怎么跑」。
FILES = [
    "README.md",
    "LICENSE",
    "start_local.sh",
    "start_local.bat",
    "run_local.py",
    "backend_src/app.py",
    "backend_src/cdp.py",
    "backend_src/config.py",
    "backend_src/engine.py",
    "backend_src/longvideo.py",
    "backend_src/scheduler.py",
    "backend_src/store.py",
    "backend_src/admin.html",
    "backend_src/requirements.txt",
    "backend_src/version.json",
    "backend_src/LICENSE",
    "backend_src/tools/get_muse_cookie.py",
    "backend_src/extension/manifest.json",
    "backend_src/extension/popup.html",
    "backend_src/extension/popup.js",
    "backend_src/extension/README.md",
    "backend_src/extension/安装说明.txt",
    "web/index.html",
    "tools/get_muse_cookie.py",
    "tools/run_long_video.py",
]

# 整目录带进来（只带匹配到的文件，不带 __pycache__ 等）。
DIRS = [
    ("素材库/唐进生视频剧本", None),
]

# ---- 绝对不能出现的东西 ------------------------------------------------
# 命中就中止打包，而不是打个警告继续。这些是「泄密」的形状。
BANNED_PATH = re.compile(
    r"(^|/)(data|profiles|venv|\.venv_local|__pycache__|\.git)(/|$)"
    r"|\.log$|\.pyc$|\.DS_Store$|\.env$"
)
BANNED_CONTENT = [
    # 真密钥形如 m2a_1dcf48ae…：必须含数字，且不能是文档里的占位符 m2a_xxxxxxxxxxxx
    (re.compile(rb"m2a_(?!x{4,}\b)[A-Za-z0-9]{8,}(?=[A-Za-z0-9]*\d)"), "本地 API Key"),
    (re.compile(rb"muse_token|MUSE_COOKIE\s*=\s*[\"'][^\"']{20,}"), "账号 cookie"),
]


def collect():
    """按白名单收集文件，返回 [(绝对路径, zip 内相对路径)]，找不到的记警告。"""
    items, missing = [], []
    for rel in FILES:
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            items.append((src, rel))
        else:
            missing.append(rel)

    for rel_dir, pattern in DIRS:
        base = os.path.join(ROOT, rel_dir)
        if not os.path.isdir(base):
            missing.append(rel_dir + "/")
            continue
        for name in sorted(os.listdir(base)):
            src = os.path.join(base, name)
            if not os.path.isfile(src):
                continue
            if pattern and not re.match(pattern, name):
                continue
            items.append((src, f"{rel_dir}/{name}"))
    return items, missing


def audit(items):
    """打包前的泄露自检。返回问题列表，空列表才算通过。"""
    problems = []
    for src, arc in items:
        if BANNED_PATH.search(arc):
            problems.append(f"路径不该进包: {arc}")
            continue
        with open(src, "rb") as fh:
            blob = fh.read()
        for pat, what in BANNED_CONTENT:
            if pat.search(blob):
                problems.append(f"{arc}: 疑似{what}")
    return problems


def add(zf, src, arc):
    """写进 zip，并保留可执行位（macOS/Linux 解压后 .sh 还得能直接跑）。"""
    info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
    mode = 0o755 if arc.endswith((".sh", ".py")) else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as fh:
        zf.writestr(info, fh.read())


def human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description="构建 Muse 视频工作台发布压缩包")
    ap.add_argument("--version", default="1.1.0", help="版本号（默认 1.1.0）")
    ap.add_argument("--out", default=DIST, help="输出目录（默认 dist/）")
    args = ap.parse_args()

    items, missing = collect()
    if missing:
        print("⚠️  以下条目不存在，已跳过：")
        for m in missing:
            print("   -", m)

    problems = audit(items)
    if problems:
        print("❌ 泄露自检未通过，已中止打包：")
        for p in problems:
            print("   -", p)
        return 1

    name = f"muse-video-make-v{args.version}.zip"
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, name)
    top = f"muse-video-make-v{args.version}"

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in items:
            add(zf, src, f"{top}/{arc}")

    size = os.path.getsize(dest)
    print(f"\n✅ 已生成 {dest}")
    print(f"   {len(items)} 个文件 · {human(size)}")
    print("\n包内清单：")
    for _, arc in items:
        print("   ", arc)
    return 0


if __name__ == "__main__":
    sys.exit(main())