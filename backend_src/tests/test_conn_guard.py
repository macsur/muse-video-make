"""连接故障守卫的阈值回归测试。零额度、零网络、零浏览器。

    python backend_src/tests/test_conn_guard.py

回归的是长视频全军覆没的那次：``_wait_attachment`` 里判断「页面卡在正在连接」
的守卫阈值写死 45 秒，可实测单段生成要 226~371 秒。这几十秒里页面显示
「正在连接」、附件数为 0、助手气泡也没长 —— 三个条件在**正常生成期间同时成立**，
于是 45 秒一到就抛「连接故障」，长视频第 1 段 100% 死在 45 秒处
（task_cb3315b46cb1427c9202、task_e27794ec41b64bedbad6）。

注意既有的 test_vm_wait.py 抓不到这个 bug：它的桩里附件 20 秒就出现，
比 45 秒的阈值早，守卫根本没来得及触发。这里补上**符合真实耗时**的时序。
"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("cand_engine", ROOT / "engine.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def drive(appear_at, timeout, tail="Connecting..."):
    """跑一遍 _wait_attachment，附件在 appear_at 秒出现（None = 永不出现）。"""
    clock = [0.0]
    m.time = SimpleNamespace(time=lambda: clock[0],
                             sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    eng = object.__new__(m.MuseEngine)
    eng._scroll_bottom = lambda: None
    eng.page = SimpleNamespace(js=lambda _: json.dumps(
        {"tail": tail, "cnt": 0, "txt": "", "stop": True}))
    eng.attachments = (lambda: []
                       if (appear_at is None or clock[0] < appear_at)
                       else [{"src": "generated.mp4", "tid": "video",
                              "w": 1280, "h": 720, "hasVideo": True,
                              "vSrc": "blob:generated"}])
    try:
        res = eng._wait_attachment("", timeout, "video")
        return ("ok", res, clock[0])
    except m.MuseGenerationError as e:
        return ("raised", str(e), clock[0])


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok  %s" % msg)


def test_slow_generation_not_killed():
    """实测最慢的一档（245.8 秒出片）绝不能被守卫砍掉。"""
    print("test_slow_generation_not_killed")
    kind, res, el = drive(appear_at=245.8, timeout=600)
    check(kind == "ok", "245.8 秒出片的慢生成没有被误杀（实际 %s）" % kind)
    check(res.get("vSrc") == "blob:generated", "正确取到视频源")


def test_typical_generation_not_killed():
    """更常见的 226~246 秒区间同样要放过。"""
    print("test_typical_generation_not_killed")
    for t in (226.0, 245.8, 371.0):
        kind, res, _ = drive(appear_at=t, timeout=600)
        check(kind == "ok", "%s 秒出片未被误杀" % t)


def test_real_stuck_still_reported():
    """真卡死时守卫仍要报错 —— 修的是阈值，不是把这个守卫删掉。"""
    print("test_real_stuck_still_reported")
    kind, err, el = drive(appear_at=None, timeout=600)
    check(kind == "raised", "连接真卡死时仍会抛错")
    check("正在连接" in err, "抛的是连接故障而非「模型未生成」")
    check(el < 600, "在 600 秒预算耗尽前就收工（实际 %.0fs）" % el)


def test_small_timeout_still_safe():
    """timeout 调小时也不能退化成早杀（45 秒那种）。"""
    print("test_small_timeout_still_safe")
    kind, res, _ = drive(appear_at=25.0, timeout=300)
    check(kind == "ok", "timeout=300 时 25 秒出片未被误杀")


if __name__ == "__main__":
    for fn in (test_slow_generation_not_killed, test_typical_generation_not_killed,
               test_real_stuck_still_reported, test_small_timeout_still_safe):
        fn()
    print("\n全部通过")