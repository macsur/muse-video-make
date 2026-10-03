#!/usr/bin/env python3
"""长视频一键跑：提交分镜剧本 → 轮询到终态 → 打印结果。

    python tools/run_long_video.py --plan-only          # 只看分段计划，不消耗额度
    python tools/run_long_video.py                      # 真跑（默认 240 秒）
    python tools/run_long_video.py --duration 120       # 换个目标时长

设计要点：
* **先 plan 再跑**。分段是纯函数、零额度，跑之前一定先把「会分成几段、每段
  请求多少秒、提示词多长」打出来 —— 这���步不花任何额度，事后才发现分错了
  就太亏了。`--plan-only` 就是干这个的。
* **后台可中断**。任务本身由服务端驱动，本脚本只是个会轮询的客户端，
  Ctrl-C 只是停止观察，**不会**取消服务端的任务。想停要去管理页面。
* 真实耗时参考：单段 30 秒视频实测约 371 秒，10 段 ≈ 62 分钟（不含重试）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend_src"))

DEFAULT_SCRIPT = ROOT / "素材库" / "唐进生视频剧本" / "唐先生的中场人生三部曲_240秒分镜剧本.md"
if not DEFAULT_SCRIPT.is_file():  # 发布包里带的是素材库那份样本
    DEFAULT_SCRIPT = ROOT / "docs" / "scripts" / "tang-240s.md"
TERMINAL = {"done", "failed", "succeeded", "success", "error", "stalled"}


def load_cfg() -> tuple[str, int]:
    """从 data/local_config.json 读 api_key / api_port（密钥不落盘到脚本里）。"""
    cfg_path = ROOT / "data" / "local_config.json"
    d = json.loads(cfg_path.read_text(encoding="utf-8"))
    return d["api_key"], int(d.get("api_port", 18610))


def _req(url: str, key: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data,
                               headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"},
                               method="POST" if data else "GET")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def show_plan(p, seconds: int, src: str) -> None:
    import longvideo as L
    print("=" * 68)
    print("分段计划（纯本地推演，零额度）")
    print("=" * 68)
    print("角色基底：%d 字" % len(p.base))
    print("段数 %d ｜ 覆盖 %d 秒 ｜ 目标 %d 秒" % (len(p.segments), p.covered_seconds, seconds))
    native = sum(s.request_duration for s in p.segments)
    print("muse.ai 原生请求合计 %d 秒 -> 合成时裁回 %d 秒"
          % (native, p.covered_seconds))
    if native != p.covered_seconds:
        print("（原生档位只有 5/10/30 秒，向上取整必然超；超出的部分在合成时裁掉）")
    print()
    for s in p.segments:
        tags = re.findall(r"镜头\s*\d+|段落\s*[A-Z]", s.text)
        sp = L.build_segment_prompt(p, s)
        print("  第%2d段 %3d-%3ds  剧本%2ds  请求%2ds  提示词%4d字  %s"
              % (s.index, s.t_start, s.t_end, s.seconds,
                 s.request_duration, len(sp), tags))
    if p.notes:
        print()
        for nt in p.notes:
            print("  ⚠ " + nt)
    print()

    # 覆盖率体检：镜头/段落有没有丢
    joined = "\n".join(s.text for s in p.segments)
    want = re.findall(r"镜头\s*\d+|段落\s*[A-Z]", src)
    got = re.findall(r"镜头\s*\d+|段落\s*[A-Z]", joined)
    missing = [t for t in want if want.count(t) > got.count(t)]
    if missing:
        print("  ⚠ 剧本里有 %d 个镜头/段落没有进入分段：%s"
              % (len(missing), missing))
    else:
        print("  ✓ 剧本里 %d 个镜头/段落全部进入分段，无丢失" % len(set(want)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=str(DEFAULT_SCRIPT))
    ap.add_argument("--duration", type=int, default=240)
    ap.add_argument("--plan-only", action="store_true",
                    help="只打印分段计划，不提交任务、不消耗额度")
    ap.add_argument("--poll", type=int, default=15, help="轮询间隔秒")
    args = ap.parse_args()

    src = Path(args.script).read_text(encoding="utf-8")

    import longvideo as L
    try:  # 用真实配置推演，免得预览和服务端实际分段不一致
        from config import CFG
        cap, maxseg = CFG.video_seg_cap or L.MUSE_MAX_SINGLE_SECONDS, CFG.video_max_segments
    except Exception:  # noqa: BLE001 —— 没装依赖时也能看计划
        cap, maxseg = L.MUSE_MAX_SINGLE_SECONDS, 48
    plan = L.plan_segments(src, args.duration, cap=cap, max_segments=maxseg)
    show_plan(plan, args.duration, src)

    if args.plan_only:
        return 0

    if not plan.segments:
        print("没有解析出任何分段，放弃提交。")
        return 1

    key, port = load_cfg()
    base = "http://127.0.0.1:%d" % port
    print("提交任务到 %s …（%d 段，预计 %.0f 分钟）"
          % (base, len(plan.segments),
             len(plan.segments) * 371 / 60.0))
    task = _req(base + "/v1/videos", key, {"prompt": src, "duration": args.duration})
    tid = task.get("task_id") or task.get("id")
    print("任务已受理：%s" % tid)
    print("（Ctrl-C 只停止观察，不会取消服务端任务）\n")

    t0 = time.time()
    last = None
    while True:
        try:
            t = _req("%s/v1/videos/%s" % (base, tid), key)
        except urllib.error.URLError as e:
            print("轮询失败（服务可能重启中）：%s，3 秒后重试" % e)
            time.sleep(3)
            continue

        st = t.get("status")
        seg = "%d/%d" % (t.get("segment_done") or 0, t.get("segment_total") or 0)
        prog = t.get("progress") or 0
        line = "[%5.1f分] %-8s %-9s %s 段%s %3d%%" % (
            (time.time() - t0) / 60.0, st, t.get("stage") or "-",
            "进度" if st == "running" else "    ", seg, prog)
        if line != last:
            print(line, flush=True)
            last = line

        if st in TERMINAL:
            print()
            print("=" * 68)
            print("终态：%s ｜ 用时 %.1f 分钟" % (st, (time.time() - t0) / 60.0))
            print("=" * 68)
            if t.get("notes"):
                for nt in t["notes"]:
                    print("  ⚠ " + nt)
            if t.get("error"):
                print("错误：%s" % t["error"])
            segs = t.get("segments") or []
            if segs:
                print("\n分段：")
                for s in segs:
                    print("  第%2d段 %3d-%3ds  %6.1fs  %s"
                          % (s["index"], s["t_start"], s["t_end"],
                             s.get("elapsed") or 0, s.get("filename")))
            res = t.get("result") or {}
            mg = res.get("merge") or {}
            if res.get("url"):
                print("\n成片：%s" % res["url"])
                print("  时长 %s 秒 ｜ 合成方式 %s ｜ 已裁剪 %s"
                      % (round(mg.get("seconds") or 0, 2), mg.get("mode"),
                         mg.get("trimmed")))
            elif st != "failed":
                print("\n未产出成片，分段文件保留在 segments 里（可自行 ffmpeg 拼接）。")
            Path(ROOT / "data" / "last_long_video.json").write_text(
                json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
            print("\n完整任务记录：data/last_long_video.json")
            return 0 if st in ("done", "succeeded", "success") else 1

        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已停止观察（服务端任务仍在跑）。")
        raise SystemExit(130)