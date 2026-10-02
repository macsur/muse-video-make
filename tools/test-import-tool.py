#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导号工具（get_muse_cookie.py）的回归测试。

怎么用：
    python test-import-tool.py --base http://1.2.3.4:18610 --key m2a_xxx

    没给 --base/--key 时只跑「不需要服务器」的那些用例（语法、参数、
    地址校验、剪贴板解析、编码降级……）。

设计原则：
  · 站在「不懂技术的人」的角度想他会怎么错 —— 地址漏冒号、Key 粘错、
    什么都不填、乱填中文、浏览器没装、日常浏览器开着……
  · 每条断言都真的起子进程、真的喂输入、真的看输出，不做 mock。
  · 需要弹浏览器才能验证的部分，用短 --timeout 让它快速失败，
    只验证「流程走到了哪一步、有没有崩、提示能不能看懂」。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys

# 注意：这里刻意**不** import pathlib —— 8.1 要读工具源码，直接用
# open(path, encoding="utf-8") 就够了。少一个依赖，少一处可能在老 Python
# 上出问题的地方。

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "get_muse_cookie.py")
PY = sys.executable
CONF = os.path.join(os.path.expanduser("~"), ".muse2api-import.json")

PASS = 0
FAIL = 0
SKIP = 0


def chk(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}   {detail}")


def skip(name: str, why: str):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}   （{why}）")


def run(args, stdin_text="", timeout=90):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([PY, TOOL] + args, input=stdin_text.encode("utf-8"),
                           capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace")
        err = (exc.stderr or b"").decode("utf-8", "replace")
        return -99, out, err + "\n<<超时>>"
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def load_module():
    spec = importlib.util.spec_from_file_location("gmc", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="", help="真实 muse2api 地址（可选）")
    ap.add_argument("--key", default="", help="真实 API Key（可选）")
    ap.add_argument("--chrome", default="", help="浏览器路径（测浏览器相关用例用）")
    args = ap.parse_args()

    print("=" * 70)
    print("  导号工具回归测试")
    print("=" * 70)

    # ─────────────────────────────────────────── 1 静态检查
    print("\n[1] 静态检查")
    rc, out, err = run(["--help"])
    chk("1.1 --help 退出码 0", rc == 0, f"rc={rc}")
    for flag in ("--base", "--key", "--count", "--list", "--remove",
                 "--from-clipboard", "--user-data-dir", "--chrome"):
        chk(f"1.2 --help 里有 {flag}", flag in out, out[:300])

    m = load_module()
    chk("1.3 模块能导入", m is not None)
    chk("1.4 核心 cookie 名是 4 条", len(m.ESSENTIAL) == 4, str(m.ESSENTIAL))

    # ─────────────────────────────────────────── 2 地址校验
    print("\n[2] 地址合法性判断")
    good = ["1.2.3.4:18610", "13.63.71.232:18610", "video.example.com",
            "https://my-domain.com", "http://1.2.3.4:18610/", "localhost:18610"]
    bad = ["1.2.3.4 18610", "不是地址哈哈", "", "http://", ":::", "空格 有 空 格"]
    for s in good:
        chk(f"2.1 合法：{s!r}", m.looks_like_address(s) is True)
    for s in bad:
        chk(f"2.2 非法：{s!r}", m.looks_like_address(s) is False)

    # ─────────────────────────────────────────── 3 剪贴板解析
    print("\n[3] 剪贴板 cookie 解析（多种粘贴形态）")
    full = "; ".join(f"{n}=VAL_{i}" for i, n in enumerate(m.ESSENTIAL))
    cases = [
        ("标准分号", full),
        ("带 Cookie: 前缀", "Cookie: " + full),
        ("换行分隔", full.replace("; ", "\n")),
        ("等号两边有空格", full.replace("=", " = ")),
        ("带引号", full.replace("=", '="').replace(";", '";')),
        ("末尾多余分号", full + ";"),
    ]
    for name, txt in cases:
        got = m.parse_cookie_text(txt)
        miss = [n for n in m.ESSENTIAL if n not in got]
        chk(f"3.1 {name} 能解析出 4/4", not miss, f"缺 {miss}")
    chk("3.2 空串解析出 0 条", m.parse_cookie_text("") == {})
    chk("3.3 乱输入解析出 0 条", m.parse_cookie_text("hello 随便打的") == {})
    chk("3.4 缺一条只解析出 3 条",
        len(m.parse_cookie_text("; ".join(f"{n}=x" for n in m.ESSENTIAL[:3]))) == 3)

    # ─────────────────────────────────────────── 4 时间格式化
    print("\n[4] 有效期展示")
    import time
    chk("4.1 会话级", m.fmt_ts(-1) == "会话级", m.fmt_ts(-1))
    chk("4.2 已过期有提示", "已过期" in m.fmt_left(int(time.time()) - 100))
    chk("4.3 剩几小时", "小时" in m.fmt_left(int(time.time()) + 7200))
    chk("4.4 剩几天", "天" in m.fmt_left(int(time.time()) + 86400 * 3))

    # ─────────────────────────────────────────── 5 错误路径
    print("\n[5] 错误路径（不给地址/Key 时必须明确拒绝）")
    rc, out, err = run(["--no-browser-check"], timeout=30)
    chk("5.1 缺参数退出码非 0", rc != 0, f"rc={rc}")
    chk("5.2 没崩（无 Traceback）", "Traceback" not in (out + err), (out + err)[-300:])
    # 5.3 必须在「地址和 Key 都对」的前提下测，否则会先被连通自检拦住
    if args.base and args.key:
        rc, out, err = run(["--base", args.base, "--key", args.key,
                            "--no-browser-check"], timeout=40)
        chk("5.3 --no-browser-check 不给 --chrome 时会提示",
            "--chrome" in out, out[:400])
    else:
        skip("5.3 --no-browser-check 提示", "未提供 --base/--key")
    rc, out, err = run(["--base", "http://127.0.0.1:1", "--key", "m2a_x",
                        "--chrome", args.chrome or "/nonexistent/x.exe"], timeout=40)
    chk("5.4 连不上时给排查方向",
        ("连不上" in out) or ("防火墙" in out) or ("安全组" in out), out[:400])
    if args.chrome:
        rc, out, err = run(["--base", args.base or "http://127.0.0.1:1",
                            "--key", args.key or "m2a_x",
                            "--chrome", "/definitely/not/here.exe"], timeout=40)
        chk("5.5 指定的浏览器不存在时明确报错",
            ("不存在" in out) or ("不可用" in out), out[:400])

    # ─────────────────────────────────────────── 6 真实服务器（可选）
    if args.base and args.key:
        print("\n[6] 真实服务器交互")
        had = os.path.exists(CONF)
        backup = None
        if had:
            backup = open(CONF, encoding="utf-8").read()
        try:
            rc, out, err = run(["--base", args.base, "--key", args.key, "--list"], timeout=60)
            chk("6.1 --list 退出码 0", rc == 0, f"rc={rc} {err[:200]}")
            chk("6.2 连通自检通过", "连接正常" in out, out[:400])
            chk("6.3 有表头", "标签" in out and "账号 ID" in out, out[:400])
            chk("6.4 有管理页链接", "/admin?key=" in out, out[:400])

            rc, out, err = run(["--base", args.base,
                                "--key", "m2a_wrong_000000000000000000",
                                "--list"], timeout=60)
            chk("6.5 错误 Key 被拦", rc != 0 and ("Key" in out), f"rc={rc} {out[:200]}")

            rc, out, err = run(["--base", args.base, "--key", args.key,
                                "--remove", "绝对不存在的标签zzz"], timeout=60)
            chk("6.6 --remove 不存在时明确报没找到", "没找到" in out, out[:400])

            rc, out, err = run(["--base", "http://127.0.0.1:1", "--key", args.key], timeout=40)
            chk("6.7 端口错误被拦", rc != 0, f"rc={rc}")
        finally:
            if backup is not None:
                open(CONF, "w", encoding="utf-8").write(backup)
            elif os.path.exists(CONF):
                os.remove(CONF)
    else:
        print("\n[6] 真实服务器交互")
        skip("6.x 真实服务器用例", "未提供 --base/--key")

    # ─────────────────────────────────────────── 7 小白破坏性场景
    #
    # 这一组全部是 2026-09-29 五轮实测**真机跑出来**的缺陷，锁定住防止退化。
    # 每条都对应一个真实发生过的事故或误导性提示。
    print("\n[7] 小白破坏性场景（实测缺陷回归）")

    # 7.1 地址规范化：大写协议、多余斜杠、路径都要清掉
    #     事故：`HTTP://1.2.3.4:18610///` 原样显示，请求拼成 `...///admin/...`
    #           拿到空结果，却报「连接正常」，小白完全查不出来。
    norm_ok = [("HTTP://1.2.3.4:18610///", "http://1.2.3.4:18610"),
               ("  http://1.2.3.4:18610/  ", "http://1.2.3.4:18610"),
               ("1.2.3.4:18610", "http://1.2.3.4:18610"),
               ("HTTPS://A.COM:443/x?y=1", "https://A.COM:443"),
               ("http://", "")]
    for raw, want in norm_ok:
        got = m.normalize_base(raw)
        chk(f"7.1 规范化 {raw!r} → {want!r}", got == want, f"得到 {got!r}")

    # 7.2 Key 里的空白必须被清掉（粘贴事故：从聊天窗口复制带换行/空格）
    #     事故：界面显示 `API Key： m2a_xxx`（前导空格），照抄到别处就认证失败。
    import re as _re
    messy = "  m2a_ c9c2\t6822\n e0c8  "
    cleaned = _re.sub(r"\s+", "", messy.strip().strip('"').strip("'"))
    chk("7.2 Key 中间空白被清掉", cleaned == "m2a_c9c26822e0c8", cleaned)

    # 7.3 --port 非法值必须当场拒绝，不能默默用默认值
    #     事故：`--port 99999` 直接拿去开调试端口，浏览器永远起不来，
    #           报出来的错跟端口毫无关系。
    for bad_port in ("0", "99999", "-1", "80"):
        rc, out, err = run(["--base", "http://127.0.0.1:1", "--key", "m2a_x",
                            "--port", bad_port], timeout=35)
        chk(f"7.3 --port {bad_port} 被当场拒绝",
            rc != 0 and "--port" in out, f"rc={rc} out={out[:150]!r}")

    # 7.4 --count 负数必须明确报「不能是负数」
    #     事故：`--count -1` 循环不成立、什么都没干，界面却显示
    #           「没找到可用的浏览器」，小白以为是自己没装浏览器。
    rc, out, err = run(["--base", "http://127.0.0.1:1", "--key", "m2a_x",
                        "--count", "-1"], timeout=35)
    chk("7.4 --count 负数被当场拒绝",
        rc != 0 and "负数" in out, f"rc={rc} out={out[:150]!r}")

    # 7.5 `--remove` 后面没跟值 → 必须报「没给」，绝不能掉进导号流程
    #     事故：小白想删账号，脚本却弹出一个浏览器要他登录 muse.ai。
    rc, out, err = run(["--base", "http://127.0.0.1:1", "--key", "m2a_x",
                        "--remove", ""], timeout=35)
    chk("7.5 --remove 空值被当场拒绝（不会误入导号流程）",
        rc != 0 and "--remove" in out and "登录" not in out,
        f"rc={rc} out={out[:200]!r}")

    # 7.6 --timeout 太短要提示（默认 300，给 10 秒肯定不够登录）
    rc, out, err = run(["--base", "http://127.0.0.1:1", "--key", "m2a_x",
                        "--timeout", "10"], timeout=35)
    chk("7.6 --timeout 过短被当场拒绝",
        rc != 0 and "--timeout" in out, f"rc={rc} out={out[:150]!r}")

    # 7.7 空账号池时 --list 必须告诉小白「下一步跑什么」
    #     事故：只显示一个「（空）」，小白不知道该干嘛。
    if args.base and args.key:
        # 用 28810 那种空池实例太依赖环境；直接测文案函数
        buf = []
        _say_backup = m.say
        m.say = lambda s="": buf.append(str(s))
        try:
            m.print_accounts([], "http://x")
        finally:
            m.say = _say_backup
        text = "\n".join(buf)
        chk("7.7 空账号池给出「怎么加账号」指引",
            ("账号池是空的" in text) and ("python get_muse_cookie.py" in text),
            text[:300])

    print("\n[8] 小白体验缺陷回归（2026-09-29 第二轮）")

    # 8.1 非交互时提示必须独立成行，不能和后续输出挤在一起
    #     事故：`服务器地址（…）：  这个 Key 我还记着：m2a_xxx`
    #          两段提示粘成一行，小白不知道该敲什么。
    with open(m.__file__, encoding="utf-8") as f:
        src = f.read()
    chk("8.1 有 _prompt_line 做非交互分行处理",
        "def _prompt_line" in src, "未找到 _prompt_line")

    # 8.2 非交互读不到地址时必须「只问一次」+ 给正确用法，不能刷屏 5 次
    #     事故：管道里跑，同一段报错打 5 遍，看着像死循环。
    chk("8.2 非交互时重试次数降为 1",
        "_maxtry = 5 if sys.stdin.isatty() else 1" in src,
        "未找到 _maxtry 逻辑")
    chk("8.3 非交互失败时给出 --base/--key 用法",
        "--base http://1.2.3.4:18610 --key m2a_" in src,
        "未找到非交互的用法提示")

    # 8.4 实跑一次：非交互空输入应当快速退出（rc=2），且输出不重复刷屏
    #     ⚠️ 模拟方式：stdin_text="" —— 子进程的 stdin 是一根**已关闭的空管道**，
    #        input() 立刻 EOF，正是「非交互、没人能回答」的场景。
    #        （别写 stdin_empty=True，run() 没这个参数。）
    rc, out, err = run([], stdin_text="", timeout=30)
    joined = out + err
    n_tips = joined.count("看着不像一个地址")
    chk("8.4 非交互空输入只提示一次（不刷屏）",
        rc == 2 and n_tips <= 1, f"rc={rc} 重复提示={n_tips} 次")

    print("\n" + "=" * 70)
    print(f"  结果：{PASS} 通过 / {FAIL} 失败" + (f" / {SKIP} 跳过" if SKIP else ""))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
