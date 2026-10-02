#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 publish/ 打成一个可复现的发布包（Windows 上必须这样做）。

为什么不能用 `git archive` / `tar czf`：
  · Windows 上的 git 是 core.filemode=false，`git archive` 打出的 .sh 全是 664，
    用户解包后不能 `./install.sh`，必须 `bash install.sh` —— 违背直觉。
  · 直接 `tar czf` 会把当前时间/uid/gid 写进去，同样的内容两次打包 SHA256 不同，
    没法做校验。

所以这里用 Python 的 tarfile **手工构造**：
  · 显式指定每个文件的 mode（脚本 0o755，其余 0o644）；
  · 固定 mtime / uid / gid / uname / gname → 可复现，同内容同哈希。

用法：
    python make_release.py 1.0.1
产物：
    release-build/muse-video-installer-<版本>.tar.gz
"""
import os
import sys
import hashlib
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 本脚本在 publish/tools/maintainer/ → 仓库内容根就是上两级
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = ROOT
# 产物写到仓库外面（别把发布包提交进仓库）
OUT_DIR = os.environ.get("RELEASE_OUT_DIR", os.path.join(os.path.dirname(ROOT), "release-build"))

# 打进包里的文件（相对仓库根的路径）→ 是否可执行
# ⚠️ 这里是**面向用户**的发布包内容 —— 维护者自己的脚本（如本文件、
#    publish.sh）刻意不包含在内：它们对用户没用，还会让包装着更重的
#    「内部实现」味道。仓库里另有维护者工具目录就够了。
FILES = {
    ".gitattributes": False,
    ".gitignore": False,
    "LICENSE": False,
    "README.md": False,
    "install.sh": True,
    "test-install.sh": True,
    "tools/get_muse_cookie.py": True,
    "tools/test-import-tool.py": True,
}

# 固定元数据 —— 让打包结果可复现
FIXED_MTIME = 1759100000  # 2025-09-29 附近，固定值即可


def build(version: str) -> str:
    top = f"muse-video-installer-{version}"
    out = os.path.join(OUT_DIR, f"{top}.tar.gz")
    os.makedirs(OUT_DIR, exist_ok=True)

    def add(tf, name, data, mode, isdir=False):
        ti = tarfile.TarInfo(name)
        ti.mtime = FIXED_MTIME
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        if isdir:
            ti.type = tarfile.DIRTYPE
            ti.mode = mode
            tf.addfile(ti)
        else:
            ti.type = tarfile.REGTYPE
            ti.mode = mode
            ti.size = len(data)
            import io
            tf.addfile(ti, io.BytesIO(data))

    # ⚠️ 用 gzip.GzipFile 手工构造、并把 mtime 固定为 0，才能做到真正的
    #    可复现。tarfile.open("w:gz") 会自己建 gzip 头并写入**当前时间**，
    #    于是同样内容每次打包 SHA256 都不同（实测复现：连打三次三个哈希）。
    #    mtime=0 是 gzip 的约定写法，表示"无时间戳"。
    import gzip
    import io

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        add(tf, top, b"", 0o755, isdir=True)
        add(tf, f"{top}/tools", b"", 0o755, isdir=True)
        for rel in sorted(FILES):
            path = os.path.join(SRC, rel)
            with open(path, "rb") as f:
                data = f.read()
            add(tf, f"{top}/{rel}", data, 0o755 if FILES[rel] else 0o644)

    with open(out, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
            gz.write(raw.getvalue())
    return out


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "0.0.0-dev"
    out = build(version)
    h = hashlib.sha256(open(out, "rb").read()).hexdigest()
    size = os.path.getsize(out)
    print(f"产物：{out}")
    print(f"大小：{size} 字节（{size/1024:.1f} KB）")
    print(f"SHA256：{h}")
    # 顺手打印内容清单，便于核对
    print("内容：")
    with tarfile.open(out) as tf:
        for m in tf.getmembers():
            kind = "dir " if m.isdir() else "file"
            print(f"  {kind} {oct(m.mode)}  {m.size:>8}  {m.name}")


if __name__ == "__main__":
    main()
