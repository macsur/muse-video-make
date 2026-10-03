"""长视频分段的**编排层**回归测试：app.py 里的 driver 行为。

    python backend_src/tests/test_longvideo_flow.py

只 import app 模块并猴补 ``_run_generation`` / ``merge_segments``：
**不启动浏览器、不联网、不消耗 muse.ai 额度**。要验的是编排逻辑 ——
分段驱动走的是 SCHED.run_sync（而不是裸调或整段独占 GEN_LOCK）、
第 N 段失败时保留前 N-1 段、进度单调不减、合成降级不把任务变成 failed。

分切算法本身由 test_longvideo_split.py 覆盖，这里直接复用它的样本。
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_longvideo_split import SAMPLE, SAMPLE_SECONDS  # noqa: E402  只取样本常量

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


def _load(home):
    os.environ.update(MUSE2API_HOME=home, MUSE2API_PROFILE_ROOT=home,
                      MUSE2API_KEY="test-only", MUSE2API_PUBLIC_BASE="")
    spec = importlib.util.spec_from_file_location(
        "lv_flow_target", SRC / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.SCHED.start()
    return module


def _wait_terminal(module, task_id, limit=30.0):
    """轮询到终态，返回 (任务, 记录下来的进度序列)。"""
    terminal = {module.ST_DONE, module.ST_FAILED, "stalled"}
    seen, last, t0 = [], 0.0, time.time()
    while time.time() - t0 < limit:
        t = module.get_video(task_id, None)
        p = t.get("progress") or 0
        if p != last:
            seen.append(p)
            last = p
        if t.get("status") in terminal:
            return t, seen
        time.sleep(0.01)
    return module.get_video(task_id, None), seen


def _monotonic(seq):
    return all(b >= a for a, b in zip(seq, seq[1:]))


def _fixture_media(module, n):
    """造 n 个假媒体文件，返回 [(filename, path, size), ...]"""
    d = Path(module.CFG.media_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = d / ("seg%02d.mp4" % i)
        p.write_bytes(b"fixture" * 64)
        out.append({"filename": p.name, "path": str(p), "size": p.stat().st_size,
                    "kind": "video"})
    return out


def scenario_ok(module):
    """全部段落成功 → 合成成功 → 任务 done。"""
    print("场景 1：10 段全部成功")
    media = _fixture_media(module, 12)
    calls, run_sync_calls = [], []

    def generation(prompt, kind, timeout, **kw):
        calls.append(prompt)
        return media[len(calls) - 1], "fixture-account"

    real_run_sync = module.SCHED.run_sync

    def counting_run_sync(fn, label="", **kw):
        run_sync_calls.append(label)
        return real_run_sync(fn, label=label, **kw)

    module._run_generation = generation
    module.SCHED.run_sync = counting_run_sync
    module.merge_segments = lambda paths, **kw: {
        "ok": True, "path": os.path.join(module.CFG.media_dir, "merged_test.mp4"),
        "mode": "copy", "seconds": SAMPLE_SECONDS}
    # 合成结果文件必须真的存在，driver 要 stat 它
    (Path(module.CFG.media_dir) / "merged_test.mp4").write_bytes(b"x" * 128)

    req = module.VideoRequest(prompt=SAMPLE, duration=SAMPLE_SECONDS)
    r = asyncio.run(module.create_video(req, None))
    check("立即返回 202 风格的任务对象", r.get("id") and r.get("task_id"), str(r))
    check("返回里带 segment_total", r.get("segment_total", 0) >= 3, str(r))
    check("返回里带 notes", "notes" in r, str(r))

    t, seen = _wait_terminal(module, r["id"])
    check("任务成功", t.get("status") == module.ST_DONE, "%s / %s" % (t.get("status"), t.get("error")))
    check("每段各调一次生成", len(calls) == t.get("segment_total"),
          "%d 次 vs %d 段" % (len(calls), t.get("segment_total")))
    check("分段全部记录", len(t.get("segments") or []) == len(calls))
    check("走的是 SCHED.run_sync", len(run_sync_calls) == len(calls),
          "%d 次" % len(run_sync_calls))
    check("标签含段号", any(":seg1" in x for x in run_sync_calls), str(run_sync_calls[:2]))
    check("进度单调不减", _monotonic(seen), str(seen))
    check("最终 100", seen and seen[-1] == 100, str(seen[-3:]))
    check("result.url 指向合成片",
          (t.get("result") or {}).get("url", "").endswith("merged_test.mp4"),
          str((t.get("result") or {}).get("url")))
    check("合成信息写进 result", (t.get("result") or {}).get("merge", {}).get("ok") is True)
    check("每段请求时长都是原生档位",
          all(s["requested_duration"] in (5, 10, 30) for s in t["segments"]))
    # 段号措辞会诱使 muse.ai 进入对话模式（见 longvideo.build_segment_prompt
    # 的 docstring 与 HANDOVER-OUT §4.3），所以这里反过来断言**不含**段号。
    check("每段提示词都不含段号措辞",
          all("长视频第" not in p for p in calls))
    check("每段提示词都是生成指令",
          all(p.startswith("全新文生视频创作") for p in calls))
    check("合成文件名不含分隔符", "/" not in (t["result"]["filename"]))
    module.SCHED.run_sync = real_run_sync


def scenario_fail(module):
    """第 3 段**持续**失败（重试也没救回来）→ 任务 failed，但前 2 段必须保留。

    注意 fixture 要让第 3 段的每一次尝试都失败：如果只失败一次，
    段级重试会把它救回来，任务反而成功，就测不到「中断并保留」这条路径了。
    """
    print("场景 2：第 3 段持续失败")
    media = _fixture_media(module, 12)
    calls = []

    def generation(prompt, kind, timeout, **kw):
        calls.append(prompt)
        if len(calls) in (3, 4):  # 第 3 段的首次尝试 + 1 次重试
            raise module.MuseGenerationError("fixture: 等待生成超时，未出现新的生成结果")
        return media[len(calls) - 1], "fixture-account"

    module._run_generation = generation
    module.merge_segments = lambda paths, **kw: {"ok": True, "path": "/tmp/x.mp4"}

    req = module.VideoRequest(prompt=SAMPLE, duration=SAMPLE_SECONDS)
    r = asyncio.run(module.create_video(req, None))
    t, seen = _wait_terminal(module, r["id"])
    check("任务失败", t.get("status") == module.ST_FAILED, str(t.get("status")))
    check("错误里带真实原因", "未出现新的生成结果" in (t.get("error") or ""),
          str(t.get("error")))
    check("错误里说明保留了几段", "已完成 2/" in (t.get("error") or ""), str(t.get("error")))
    check("已完成分段被保留", len(t.get("segments") or []) == 2,
          "%d 段" % len(t.get("segments") or []))
    check("重试 1 次后仍失败即中断（共 4 次）", len(calls) == 4, "%d 次" % len(calls))
    check("失败时不合成（result 为空）", not (t.get("result") or {}).get("merge"),
          str(t.get("result")))
    check("进度单调不减", _monotonic(seen), str(seen))


def scenario_retry(module):
    """瞬时失败（只失败一次）→ 段级重试救回来，任务照常成功。

    这是 10 段长视频能「自动一次跑完」的关键：muse.ai 的生成 agent 本来就有
    相当比例的抖动，2026-10-03 实测同一时刻紧接着再发一次就正常出片。
    """
    print("场景 2b：瞬时失败被重试救回")
    media = _fixture_media(module, 12)
    calls = []

    def generation(prompt, kind, timeout, **kw):
        calls.append(prompt)
        if len(calls) == 3:  # 只有第 3 段首次尝试失败
            raise module.MuseGenerationError("fixture: 瞬时抖动")
        return media[min(len(calls), len(media)) - 1], "fixture-account"

    module._run_generation = generation
    merge_path = os.path.join(module.CFG.media_dir, "merged_retry.mp4")
    module.merge_segments = lambda paths, **kw: {
        "ok": True, "path": merge_path, "mode": "copy", "seconds": SAMPLE_SECONDS}
    Path(merge_path).write_bytes(b"x" * 128)

    req = module.VideoRequest(prompt=SAMPLE, duration=SAMPLE_SECONDS)
    r = asyncio.run(module.create_video(req, None))
    t, _ = _wait_terminal(module, r["id"])
    check("重试后任务成功", t.get("status") == module.ST_DONE, str(t.get("error")))
    check("总调用次数 = 段数 + 1 次重试", len(calls) == t.get("segment_total") + 1,
          "%d 次 vs %d 段" % (len(calls), t.get("segment_total")))
    check("分段数不受重试影响", len(t.get("segments") or []) == t.get("segment_total"))
    check("notes 里留了重试痕迹",
          any("重试" in n for n in (t.get("notes") or [])), str(t.get("notes")))


def scenario_merge_degrade(module):
    """合成失败不算任务失败：任务仍 done，前端拿分段列表。"""
    print("场景 3：合成失败降级")
    media = _fixture_media(module, 12)
    calls = []

    def generation(prompt, kind, timeout, **kw):
        calls.append(prompt)
        return media[len(calls) - 1], "fixture-account"

    module._run_generation = generation
    module.merge_segments = lambda paths, **kw: {
        "ok": False, "reason": "未找到 ffmpeg，长视频只返回分段列表，不合成"}

    req = module.VideoRequest(prompt=SAMPLE, duration=SAMPLE_SECONDS)
    r = asyncio.run(module.create_video(req, None))
    t, _ = _wait_terminal(module, r["id"])
    check("段落全成功时任务仍为 done", t.get("status") == module.ST_DONE, str(t.get("status")))
    check("result.url 为空", (t.get("result") or {}).get("url") is None)
    check("降级原因写进 result", "ffmpeg" in (t.get("result") or {}).get("merge", {}).get("reason", ""))
    check("分段列表照样返回", len((t.get("result") or {}).get("segments") or []) == len(calls))


def scenario_short(module):
    """<=30s 仍走原来的单段路径，不受分段逻辑影响。"""
    print("场景 4：短时长回归")
    media = _fixture_media(module, 2)
    calls = []

    def generation(prompt, kind, timeout, **kw):
        calls.append(prompt)
        return media[0], "fixture-account"

    module._run_generation = generation
    req = module.VideoRequest(prompt="一只猫在草地上跑", duration=5)
    r = asyncio.run(module.create_video(req, None))
    t, _ = _wait_terminal(module, r["id"])
    check("短时长不带 segment_total", not t.get("segment_total"), str(t.get("segment_total")))
    check("短时长成功", t.get("status") == module.ST_DONE, str(t.get("error")))
    check("短时长只调一次生成", len(calls) == 1, "%d 次" % len(calls))
    check("短时长有 result.url", bool((t.get("result") or {}).get("url")))


def scenario_validate(module):
    print("场景 5：时长校验")
    # (输入, 期望归一值)；期望为 None 表示应当在入口被拒
    cases = ((5, 5), (10, 10), (30, 30), (60, 60), (240, 240), (480, 480),
             (31, 31), (31.5, 31), ("120", 120),
             (4, None), (0, None), (1, None), (481, None), (999, None),
             ("abc", None), (None, 6))
    for d, want in cases:
        try:
            got = module.validate_video_duration(d)
            check("duration=%r -> %r" % (d, got), want is not None and got == want,
                  "" if want is not None else "却放行了")
        except Exception:
            check("duration=%r 被拒" % d, want is None, "却抛异常")


def scenario_deadline_error(module):
    print("场景 6：超时报错不再吞掉真实原因")
    msg = str(module._deadline_error(module.MuseGenerationError("等待生成超时，未出现新的生成结果")))
    check("带上最后一次错误", "等待生成超时" in msg, msg)
    check("仍保留原提示", "任务总等待时限已到" in msg, msg)
    bare = str(module._deadline_error(None))
    check("无 last_exc 时不炸", "任务总等待时限已到" in bare, bare)


def scenario_startup(module):
    print("场景 7：重启清理残留任务")
    t1 = module.store.create_task("video", "残留的普通视频")
    module.store.update_task(t1["id"], status="processing")
    t2 = module.store.create_task("video", "残留的长视频")
    module.store.update_task(t2["id"], status="processing", segment_total=10,
                             segment_done=4, segments=[{"index": 1}, {"index": 2}])
    asyncio.run(module._startup())
    check("普通视频残留被标失败",
          module.store.get_task(t1["id"])["status"] == "failed")
    check("长视频残留被标失败",
          module.store.get_task(t2["id"])["status"] == "failed")
    err = module.store.get_task(t2["id"])["error"]
    check("长视频错误里带已完成段数", "4/10" in err, err)
    check("长视频分段仍保留",
          len(module.store.get_task(t2["id"])["segments"]) == 2)


def scenario_resume(module):
    """断点续传：模拟前 2 段已生成并落盘，后续重试仅生成第 3~6 段，最终合成。"""
    print("场景 8：长视频断点续传")
    media = _fixture_media(module, 12)
    calls = []

    def generation(prompt, kind, timeout, **kw):
        calls.append(prompt)
        return media[len(calls) - 1], "fixture-account"

    module._run_generation = generation
    merge_path = os.path.join(module.CFG.media_dir, "merged_resume.mp4")
    module.merge_segments = lambda paths, **kw: {
        "ok": True, "path": merge_path, "mode": "copy", "seconds": SAMPLE_SECONDS}
    Path(merge_path).write_bytes(b"x" * 128)

    # 1. 模拟一个失败的长视频任务，前 2 段文件完好落盘
    t = module.store.create_task("video", SAMPLE)
    seg1_file = Path(module.CFG.media_dir) / "seg_exist_01.mp4"
    seg1_file.write_bytes(b"data" * 512)
    seg2_file = Path(module.CFG.media_dir) / "seg_exist_02.mp4"
    seg2_file.write_bytes(b"data" * 512)

    fake_segments = [
        {"index": 1, "t_start": 0, "t_end": 18, "seconds": 18, "requested_duration": 30,
         "filename": seg1_file.name, "path": str(seg1_file), "bytes": 2048, "kind": "video"},
        {"index": 2, "t_start": 18, "t_end": 40, "seconds": 22, "requested_duration": 30,
         "filename": seg2_file.name, "path": str(seg2_file), "bytes": 2048, "kind": "video"},
    ]
    module.store.update_task(
        t["id"], status="failed", stage="failed", duration=SAMPLE_SECONDS,
        segment_total=6, segment_done=2, segments=fake_segments,
        error="模拟在第 3 段发生异常"
    )

    # 2. 调用 resume 接口
    res = asyncio.run(module.resume_video_task(t["id"], None))
    check("resume 接口响应成功", res.get("status") == module.ST_QUEUED, str(res))
    check("resume 记录复用了 2 段", res.get("existing_segments_count") == 2)

    # 3. 等待终态
    finished, _ = _wait_terminal(module, t["id"])
    check("断点续传后任务最终成功", finished.get("status") == module.ST_DONE, str(finished.get("error")))
    # 样本一共 6 段，前 2 段复用，应该只产生 4 次生成调用（第 3、4、5、6 段）
    check("仅生成剩余的 4 个分段", len(calls) == 4, f"实际生成调用了 {len(calls)} 次")
    check("分段总数仍为 6 段", len(finished.get("segments") or []) == 6)
    check("最终生成合成视频", bool((finished.get("result") or {}).get("url")))


def main():
    with tempfile.TemporaryDirectory(prefix="muse-lv-flow-") as home:
        module = _load(home)
        try:
            scenario_validate(module)
            scenario_deadline_error(module)
            scenario_ok(module)
            scenario_fail(module)
            scenario_retry(module)
            scenario_merge_degrade(module)
            scenario_short(module)
            scenario_startup(module)
            scenario_resume(module)
        finally:
            module.SCHED.stop()
    print()
    if _failures:
        print("FAILED %d 项：%s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("PASS 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
