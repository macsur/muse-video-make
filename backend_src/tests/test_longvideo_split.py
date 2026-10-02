"""长视频分段的离线回归测试。纯文本处理，零额度、零网络、零浏览器。

    python backend_src/tests/test_longvideo_split.py

样本内联在下面：**不能**去读 backend_src/data/scripts/*.json —— 那个目录被
.gitignore 的 data/ 规则排除，新克隆的仓库里根本不存在（当初那份 9858 字的
失败剧本就是这样消失的）。这里内嵌一份精简但结构完整的样本：角色基底段 +
时间码块 + 镜头行 + 段落 A/B/C，与真实剧本同构。
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import longvideo as L

# --------------------------------------------------------------------------
# 内联样本（结构与真实失败剧本同构，内容为压缩版）
# --------------------------------------------------------------------------
SAMPLE = """### 一、 核心资产设定：角色基底与负向提示词 (Master Consistency Prompt)

在每一个提示词段落中嵌入一致性基底，确保全片不换脸、不衰老。

> **角色基底描述 (Character Base Profile)**
> **[Chinese]**: 唐进生，50多岁成熟英俊中国男性，高大健壮，宽肩厚背。
> **[English]**: A handsome and robust 50-year-old Chinese man.
> **负向提示词 (Negative Prompt)**: 多余手指，塑料皮肤，鱼眼畸变，换脸。

---

### 二、 240秒分段生成描述提示词（分镜提示词表）

#### 【0:00—1:10｜上半场】

* **镜头 01 (0:00—0:18) 青年练拳**
* **画面与机位**：清晨薄雾中的旧城区院落，年轻时的唐进生在击打悬挂沙袋。
* **English Prompt**: `Cinematic 50mm shot, dawn mist, old courtyard.`

* **镜头 02 (0:18—0:40) 中年接手**
* **画面与机位**：多年后，同样院落，他独自站着看沙袋。

* **镜头 03 (0:40—1:10) 疏离的餐桌**
* **画面与机位**：室内暖光，长桌只有一人，碗筷整齐。

#### 【1:10—2:20｜下半场】

* **镜头 04 (1:10—1:40) 雨夜对峙**
* **画面与机位**：暴雨中的码头，两道身影隔着雨帘。

* **镜头 05 (1:40—2:00) 转身**
* **画面与机位**：他转身走进雨里，背影渐远。

**段落 A (2:00—2:20) 片尾**
落幅：空荡的院落与静止的沙袋，全片结束。
"""

SAMPLE_SECONDS = 140

# 纯散文兜底样本：没有时间码块也没有镜头行，只能按字数切。
# 必须够长才会切出多段：30 秒 × 40 字/秒 = 1200 字。
PLAIN = (
    "清晨的旧城区还没有醒来，唐进生站在院中，风穿过树梢，吹动晾衣绳上的白衬衫。"
    "他低头看着自己的双手，那双手比二十年前宽了一圈，也厚了一圈。"
    "远处传来孩子的笑声，尖锐而短促，像一只不小心碰翻的铃铛。"
    "他没有回头，只是把挂在肩上的毛巾取下来，慢慢叠好，放在石凳上。"
    "沙袋还在晃，绳子在风里发出细微的摩擦声，一荡，一荡，节奏很慢。"
    "他伸手扶住沙袋，等它彻底停下来，才松开手，指节上留下一道白印。"
    "门槛上的漆又掉了一块，露出底下发灰的木纹，那是很多年前就有的伤口。"
    "他停在门槛上，没有跨出去，好像在等什么，又好像已经忘了在等什么。"
    "光线从昏黄慢慢变成惨白，是那种阴天特有的、没有方向的光。"
    "屋里的碗筷整整齐齐地摆着，一双筷子架在碗上，像是随时会有人回来吃饭。"
    "他伸手把那双筷子摆正了一点，然后收回手，在裤子上擦了擦。"
    "钟表在墙上走着，声音不大，但每一秒都落得很清楚。"
    "他坐在那里，听了整整一分钟，然后站起来，把椅子推回原位。"
    "雨开始下了，先是几滴打在地上，溅起很小的水花，然后就密了。"
    "整条街很快被打湿，屋檐下挂起一道水帘，把远处的灯拉成一条橙色的线。"
    "他站在雨里，仰头看天，云层很低，压得很实，看不见月亮。"
    "一道闪电把他的脸照亮，只有半秒钟，然后一切重新暗下去。"
    "他低头看了看脚下的积水，水面上有他的倒影，也在闪。"
    "有人从巷子那头跑过来，脚步声很急，跑到一半又停住了。"
    "他们隔着雨帘对视，谁都没有先开口，谁也没有先转身。"
    "他忽然觉得这些年所有的坚持都很轻，轻得抵不过一场雨。"
    "于是他松开一直攥着的那只手，雨水立刻灌进指缝，凉得发疼。"
    "他慢慢把手放下，垂在身体两侧，什么也没有拿。"
    "最后他转过身，走进雨里，背影被水汽一点点吃掉，先是肩膀，然后是脚。"
    "院子重新安静下来，只剩沙袋还在慢慢地晃，幅度越来越小。"
    "天亮的时候雨停了，地上积着一层浅水，映着灰白的天。"
    "没有人来把那双手套的沙袋重新挂好，它就那样歪着，一夜没动。"
    "镜头缓缓拉远，把整条空街收进画面，最后停在没有关严的那扇门上。"
    "门缝里透出一线光，细得几乎看不见，但确实还在。"
    "画面淡出，全片结束，只剩下雨滴落在屋檐上的声音，一下，又一下。"
    "他想起很多年前的一个清晨，也是这样的雨，也是这样的门槛。"
    "那时候他还会笑，笑起来眼角有很深的纹，笑完就用手背去擦。"
    "现在他不笑了，也不再擦任何东西，只是把手插进口袋里站着。"
    "口袋里的手机震了一下，屏幕亮起来，是一个没有存名字的号码。"
    "他没有接，震动声在雨里显得很小，小到像是别人身上的声音。"
    "第二次震动来的时候，他把手机取出来，看了一眼，然后关机。"
    "屏幕黑下去之前，他看见自己的倒影被那道光切成两半。"
    "他忽然明白，有些话一旦说出口就会变轻，轻到再也接不回来。"
    "所以他选择不说，选择走开，选择让这场雨替他把所有话都说完。"
    "沙袋的绳子终于完全静止了，垂在原地，像一个不再问问题的哑巴。"
    "院子里的积水映着天，云的影子在水面上一动不动地被拉长。"
    "远处的第一班公交驶过桥面，车灯在雨雾里晕成两团昏黄。"
    "他站在原地看了一会儿，然后终于迈过门槛，走进了雨里。"
)

_failures = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _failures.append(name)


# --------------------------------------------------------------------------
# A 分段正确性
# --------------------------------------------------------------------------
def test_split():
    print("A 分段正确性")
    p = L.plan_segments(SAMPLE, SAMPLE_SECONDS)
    check("解析出多段", len(p.segments) >= 3, "实际 %d 段" % len(p.segments))
    check("每段 <= 30 秒", all(s.seconds <= 30 for s in p.segments),
          str([s.seconds for s in p.segments]))
    check("时间轴不回退", all(p.segments[i].t_start == p.segments[i - 1].t_end
                          for i in range(1, len(p.segments))))
    check("时间轴从 0 开始", p.segments[0].t_start == 0)
    check("索引连续", [s.index for s in p.segments] == list(range(1, len(p.segments) + 1)))
    check("request_duration 合法",
          all(s.request_duration in L.MUSE_SINGLE_DURATIONS for s in p.segments),
          str([s.request_duration for s in p.segments]))
    check("覆盖约等于请求时长", abs(p.covered_seconds - SAMPLE_SECONDS) <= 2,
          "覆盖 %d / 请求 %d" % (p.covered_seconds, SAMPLE_SECONDS))

    # 镜头编号无丢失无重复：把 5 个镜头 + 1 个段落全找回来
    joined = "\n".join(s.text for s in p.segments)
    for tag in ("镜头 01", "镜头 02", "镜头 03", "镜头 04", "镜头 05", "段落 A"):
        check("包含 %s" % tag, joined.count(tag) == 1,
              "出现 %d 次" % joined.count(tag))
    check("段体不含基底标题", "核心资产设定" not in joined and "Master Consistency" not in joined)


# --------------------------------------------------------------------------
# B 基底提取
# --------------------------------------------------------------------------
def test_base():
    print("B 基底提取")
    base, lo, hi = L.extract_consistency_base(SAMPLE)
    check("抽到基底", len(base) > 60, "只有 %d 字" % len(base))
    check("含中文角色描述", "唐进生" in base)
    check("含英文角色描述", "handsome and robust" in base)
    check("含负向提示词", "多余手指" in base)
    check("无 markdown 残留", "**" not in base and "> " not in base and "###" not in base,
          repr(base[:80]))
    check("偏移量能定位回原文", 0 <= lo < hi <= len(SAMPLE))

    blocks, spans = L._base_spans(SAMPLE)
    check("逐段返回区间", len(spans) == len(blocks) and len(spans) >= 1)
    stripped = L._strip_spans(SAMPLE, spans)
    check("挖除后基底消失", "Master Consistency" not in stripped)
    check("挖除后镜头还在", all(("镜头 %02d" % i) in stripped for i in range(1, 6)))


# --------------------------------------------------------------------------
# C 段提示词
# --------------------------------------------------------------------------
def test_prompt():
    print("C 段提示词")
    p = L.plan_segments(SAMPLE, SAMPLE_SECONDS)
    sp = L.build_segment_prompt(p, p.segments[0])
    # 段号/跨段指代会诱使 muse.ai 进入「协作对话」模式、回一段文字方案而不是
    # 生成视频（2026-10-03 实测 3 条长视频任务全部 0 段失败）。详见
    # longvideo.build_segment_prompt 的 docstring。
    check("不含段号措辞", "长视频第" not in sp and "第 1/%d 段" % len(p.segments) not in sp)
    check("不含跨段指代", "其它段" not in sp and "其他段" not in sp)
    check("以生成指令开头", sp.startswith("全新文生视频创作"))
    check("声明禁止参考历史上下文", "严禁参考任何历史" in sp)
    check("写死本段时长", "时长严格为 %d 秒" % p.segments[0].seconds in sp)
    check("结尾要求直接生成", "不要输出文字方案" in sp)
    check("带基底", p.base[:20] in sp)
    check("未超字数上限", len(sp) <= L.BASE_MAX_CHARS, "%d 字" % len(sp))
    check("每段都带基底", all(p.base[:20] in L.build_segment_prompt(p, s)
                          for s in p.segments))
    check("每段都无段号", all("长视频第" not in L.build_segment_prompt(p, s)
                          for s in p.segments))

    # 超长剧本也必须压回上限
    big = L.plan_segments(SAMPLE * 40, SAMPLE_SECONDS)
    longest = max(len(L.build_segment_prompt(big, s)) for s in big.segments)
    check("超长剧本也压回上限", longest <= L.BASE_MAX_CHARS, "%d 字" % longest)
    # 截断必须留痕，不能静默丢剧情
    check("截断时写入 notes", any("截断" in n for n in big.notes),
          str(big.notes[:2]))


# --------------------------------------------------------------------------
# D 无标记兜底
# --------------------------------------------------------------------------
def test_plain():
    print("D 无标记兜底")
    p = L.plan_segments(PLAIN, 60)
    check("按字数切出多段", len(p.segments) >= 2, "%d 段" % len(p.segments))
    check("每段 <= 30 秒", all(s.seconds <= 30 for s in p.segments))
    check("字数守恒无丢失",
          sum(len(s.text) for s in p.segments) == len(PLAIN.strip()),
          "%d vs %d" % (sum(len(s.text) for s in p.segments), len(PLAIN.strip())))


def test_short_script():
    print("D2 剧本撑不起请求时长")
    p = L.plan_segments("一只猫在草地上跑。", 240)
    check("不伪造时长", p.covered_seconds <= 30, str(p.covered_seconds))
    check("明确告知内容不足", any("短于请求" in n for n in p.notes), str(p.notes))

    p = L.plan_segments(SAMPLE, SAMPLE_SECONDS)
    check("长度吻合时不多嘴", not any("短于请求" in n or "长于请求" in n for n in p.notes),
          str(p.notes))


# --------------------------------------------------------------------------
# E 边界与确定性
# --------------------------------------------------------------------------
def test_edges():
    print("E 边界与确定性")
    check("空串不炸", L.plan_segments("", 240).segments == [])
    check("纯空白不炸", L.plan_segments("   \n  ", 240).segments == [])
    check("短剧本直通单段", len(L.plan_segments("一只猫在草地上跑。", 5).segments) == 1)

    # 单个 unit 超过 cap 时要能再切
    long_unit = "**镜头 01 (0:00—2:00)**\n" + ("他站在雨里。\n" * 400)
    p = L.plan_segments(long_unit, 120)
    check("超长单元被再切", len(p.segments) >= 3, "%d 段" % len(p.segments))
    check("再切后仍 <= 30 秒", all(s.seconds <= 30 for s in p.segments),
          str([s.seconds for s in p.segments]))

    # max_segments 生效且明确告知
    p = L.plan_segments(SAMPLE * 20, SAMPLE_SECONDS, max_segments=3)
    check("max_segments 生效", len(p.segments) == 3, "%d 段" % len(p.segments))
    check("截断有明确 note", any("截断" in n for n in p.notes), str(p.notes))

    # 确定性：同输入两次结果完全一致
    a = L.plan_segments(SAMPLE, SAMPLE_SECONDS)
    b = L.plan_segments(SAMPLE, SAMPLE_SECONDS)
    check("确定性",
          [(s.t_start, s.t_end, s.text, s.request_duration) for s in a.segments]
          == [(s.t_start, s.t_end, s.text, s.request_duration) for s in b.segments])


# --------------------------------------------------------------------------
# F 装箱
# --------------------------------------------------------------------------
def test_pack():
    print("F 装箱")
    bins = L._pack_min_bins([10, 10, 10, 10], 30)
    check("DP 比贪心少一段（贪心 3 段）", len(bins) == 2, str(bins))
    check("保序不交叉", all(b == sorted(b) for b in bins))
    flat = [i for b in bins for i in b]
    check("每个元素恰好一次", flat == [0, 1, 2, 3], str(flat))
    check("每箱 <= cap", all(sum([10, 10, 10, 10][i] for i in b) <= 30 for b in bins))
    check("空输入不炸", L._pack_min_bins([], 30) == [])

    durs = [28, 28, 29, 20, 13, 30, 20, 17, 25, 30]
    bins = L._pack_min_bins(durs, 30)
    check("真实样本 10 段装箱到 10 段", len(bins) == 10, str(bins))


# --------------------------------------------------------------------------
# G ffmpeg 降级
# --------------------------------------------------------------------------
def test_merge_degrade():
    print("G ffmpeg 降级")
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "a.mp4")
        b = os.path.join(td, "b.mp4")
        for f in (a, b):
            with open(f, "wb") as fh:
                fh.write(b"not a real mp4")

        r = L.merge_segments([a], out_path=os.path.join(td, "m.mp4"))
        check("单段返回 ok=False", r.get("ok") is False and "reason" in r, str(r))

        r = L.merge_segments([a, b], out_path=os.path.join(td, "m.mp4"),
                             ffmpeg="/nonexistent/ffmpeg", ffprobe="/nonexistent/ffprobe")
        check("缺 ffmpeg 优雅降级", r.get("ok") is False and "ffmpeg" in r["reason"], str(r))

        # 有 ffmpeg 但输入是垃圾文件：必须返回 ok=False，不能抛异常
        ff = L._which("ffmpeg")
        if ff:
            r = L.merge_segments([a, b], out_path=os.path.join(td, "m.mp4"))
            check("垃圾输入不抛异常", isinstance(r, dict) and "ok" in r, str(r))

        # 临时目录用完即删，不留垃圾
        check("不残留临时文件",
              not [x for x in os.listdir(tempfile.gettempdir()) if x.startswith("merge_")][:1])


def test_merge_trim():
    """按剧本秒数逐段裁剪：muse.ai 只给 5/10/30 秒，不裁成片就会膨胀。

    真实样本（唐先生 240 秒分镜）：10 段原生输出合计 300 秒，
    剧本分配合计 240 秒 —— 差的那 60 秒必须裁掉，否则节奏全乱。
    """
    print("G2 合成裁剪")
    if not (L._which("ffmpeg") and L._which("ffprobe")):
        print("  SKIP 未找到 ffmpeg/ffprobe")
        return
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        # 3 段，每段原生 10 秒（模拟向上取整到原生档位）
        paths = []
        for i in range(3):
            p = os.path.join(td, "s%d.mp4" % i)
            rc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                 "testsrc=size=320x568:rate=24", "-t", "10",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
                 "yuv420p", p]).returncode
            if rc != 0 or not os.path.isfile(p):
                check("生成测试片段", False, "ffmpeg 失败")
                return
            paths.append(p)

        total_in = sum(L._probe(p, "ffprobe")["duration"] for p in paths)
        check("输入合计 30 秒", abs(total_in - 30) < 0.5, "%.2f" % total_in)

        wants = [8, 7, 5]
        out = os.path.join(td, "m.mp4")
        r = L.merge_segments(paths, out_path=out, durations=wants)
        check("裁剪合成成功", r.get("ok") is True, str(r.get("reason")))
        if r.get("ok"):
            got = L._probe(out, "ffprobe")["duration"]
            check("成片裁到剧本总长 20 秒", abs(got - 20) < 1.0, "%.2f" % got)
            check("标记走了裁剪", r.get("trimmed") is True, str(r.get("trimmed")))

        # durations 与实际一致 → 不该触发裁剪，快路径保持 -c copy
        same = [10, 10, 10]
        r2 = L.merge_segments(paths, out_path=os.path.join(td, "m2.mp4"),
                              durations=same)
        check("时长已吻合时不裁剪", r2.get("ok") and r2.get("trimmed") is False,
              str(r2.get("trimmed")))
        check("时长已吻合时走 copy 快路径", r2.get("mode") == "copy", str(r2.get("mode")))

        # durations 长度对不上 → 忽略裁剪，绝不串位
        r3 = L.merge_segments(paths, out_path=os.path.join(td, "m3.mp4"),
                              durations=[8, 7])
        check("durations 数量不符时安全忽略",
              r3.get("ok") and r3.get("trimmed") is False, str(r3.get("reason")))


def test_strip_text():
    """剔除「要在画面上渲染文字」的句子。

    2026-10-03 实测：只要提示词里出现字卡要求，muse.ai 就切进规划/对话模式
    （反问「要把镜头04 的字卡换成中文…吗？」），整段 0 产出。同一份分镜里
    唯一没有字卡的那一段一次就出片了。所以这不是优化，是能不能跑的区别。
    """
    print("C2 去文字指令")
    s = L.strip_text_overlays(
        '底部干净居中淡出字卡：“人生有上半场”。\n'
        '* **画面与机位**：城市冷色夜景街头，唐进生挺直后背孤身缓步向前。\n'
        '* **English Prompt**: `Minimalist elegant white subtitle appears: '
        '"Life has a first half".`\n'
        'A man standing amid passing cars and soft bokeh.')
    check("删掉中文字卡句", "字卡" not in s, repr(s[:60]))
    check("删掉英文字幕句", "subtitle" not in s.lower(), repr(s[-60:]))
    check("保留画面描述", "夜景街头" in s and "孤身缓步向前" in s)
    check("保留英文画面描述", "soft bokeh" in s)

    # 片尾：LaTeX 块、引用行、书名号、以及 "Mr." 缩写后的那个点
    tail = ('$$\\text{《唐先生的中场人生三部曲》}$$\n'
            '* **排版内容**：\n'
            '> 人生走到中场，\n'
            '> 故事才真正开始。\n'
            '* **English Prompt**: `Minimalist cinematic film typography gently '
            'fades in at center: "Mr. Tang\'s Midlife Trilogy".`\n'
            'The room is empty and the floor is wet.')
    t = L.strip_text_overlays(tail)
    check("删掉 LaTeX 块", "$$" not in t and "\\text" not in t, repr(t[:60]))
    check("删掉引用行（宣传语）", "人生走到中场" not in t, repr(t[:80]))
    check("删掉片名", "唐先生" not in t)
    check("Mr. 缩写不断句，半句不外泄",
          "Midlife Trilogy" not in t, repr(t[:120]))
    check("无关描述仍在", "The room is empty and the floor is wet" in t)

    # 整段都是字卡时，交给 build_segment_prompt 走黑场兜底
    p = L.plan_segments(SAMPLE, SAMPLE_SECONDS)
    sps = [L.build_segment_prompt(p, s) for s in p.segments]
    check("每段提示词都不含字卡要求",
          all(not re.search(r"字卡|排版内容|宣传语",
                            L.strip_text_overlays(s.text)) for s in p.segments))
    check("每段都声明画面无文字",
          all("画面中不要出现任何文字" in x for x in sps))


def main():
    for fn in (test_split, test_base, test_prompt, test_plain, test_short_script,
               test_edges, test_pack, test_merge_degrade, test_merge_trim,
               test_strip_text):
        fn()
    print()
    if _failures:
        print("FAILED %d 项：%s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("PASS 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
