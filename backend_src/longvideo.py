"""长视频自动分段：把超过 30 秒的剧本拆成多个 <=30 秒的短片依次生成，再用 ffmpeg 合成。

为什么需要它
------------
muse.ai 网页端实测**最多只能产出 30 秒竖屏视频**（ffprobe 扫过全部历史产出：
5s / 10s / 30s，清一色 720x1280 h264 yuv420p 24fps）。但 app.py 原先的
``_VIDEO_DURATIONS = {5,6,10,30,60,120,240,480}`` 是这个 app **自己编的白名单**，
不是 muse.ai 的能力上限。240 秒的请求被放行后，9858 字的剧本被原样发过去
（``_condense_video_script`` 在 ``dur>=30`` 时直接返回原文），agent 卡在
"要拍一部 4 分钟电影"上，600 秒零产出。

这里用「自动分段 + ffmpeg 合成」来兑现用户对长时长的合理诉求：用户照常提交
``duration: 240`` 和长剧本，服务端自己拆成若干段 <=30s 依次生成。

本模块是**纯逻辑**：不 import app / store / engine，不碰网络，可以离线跑测试
（``backend_src/tests/test_longvideo_split.py``），零额度消耗。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# muse.ai 网页端单次真正支持的时长（实测上限，不是偏好）
MUSE_SINGLE_DURATIONS = (5, 10, 30)
MUSE_MAX_SINGLE_SECONDS = 30

# 兜底估算：没有时间码时，按多少字算一秒。真实样本 9858 字 / 240 秒 ≈ 41 字/秒，
# 取 40 略偏保守（宁可多切一刀，也不要让某段内容超过 30 秒）。
CHARS_PER_SECOND = 40

# 注入角色基底后，单段提示词的总字数上限。
# 实测 6488 字能一次生成成功，所以 4000 留足余量。
BASE_MAX_CHARS = 4000

# 单个角色基底段最多保留多少字，防止剧本里有个超长"附录"把每段都撑爆
_BASE_MAX_LEN = 2000

# ffmpeg 合成是纯 CPU 活，串行化避免多个长视频任务互相抢核
_MERGE_LOCK = threading.Semaphore(1)

_TS = "(\\d{1,2}:\\d{2}(?::\\d{2})?)"
_SEP = "[—–\\-~～至到]"
_TC_PAIR = _TS + r"\s*" + _SEP + r"\s*" + _TS


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Segment:
    """一段待生成的短片。"""

    index: int  # 1 起
    t_start: int  # 在全片时间轴上的起点（秒）
    t_end: int  # 在全片时间轴上的终点（秒）
    text: str  # 段体（**不含**角色基底，基底由 build_segment_prompt 统一注入）
    request_duration: int  # 实际向 muse.ai 请求的时长，必须 ∈ MUSE_SINGLE_DURATIONS

    @property
    def seconds(self) -> int:
        return max(1, self.t_end - self.t_start)


@dataclass
class SegmentPlan:
    """一次长视频的分段方案。"""

    segments: List[Segment] = field(default_factory=list)
    base: str = ""  # 角色/风格基底，注入每一段
    total_seconds: int = 0  # 全片目标时长（用户请求的）
    notes: List[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.segments)

    @property
    def covered_seconds(self) -> int:
        return sum(s.seconds for s in self.segments)


# --------------------------------------------------------------------------
# 角色基底提取
# --------------------------------------------------------------------------

_BASE_KEY = re.compile(
    r"(核心资产设定|角色基底|角色设定|人物设定|角色一致性|全局风格|风格基底|"
    r"负向提示词|负面提示词|禁止内容|"
    r"master\s+consistency|character\s+base|negative\s+prompt|global\s+style)",
    re.I,
)

# 基底段吃到下一个"结构性标题"为止
_BASE_STOP = re.compile(r"^\s*(?:#{1,6}[ \t]|\*\*[^\n]{0,24}(?:镜头|段落|场景|画面)|【)")


def _is_base_head(line: str) -> bool:
    """判断一行是不是基底段的标题。

    真实剧本里的写法五花八门（实测样本）：
        ``### 一、 核心资产设定：角色基底与负向提示词 (Master Consistency Prompt)``
        ``> **角色基底描述 (Character Base Profile)**``
    所以不能要求"整行就是关键词"。判定用三条：

    1. 含基底关键词，且整行不超过 120 字；
    2. 关键词**前面**很短（<=20 字）—— 标题才这么短，正文句子会有一大截前缀；
    3. 行尾**不是**句读 —— 标题不以 ``。！？，、；`` 收尾，正文句子会。
    """
    s = line.strip()
    if len(s) > 120:
        return False
    if s[-1:] in "。．.！!？?，,、；;：:":
        return False
    m = _BASE_KEY.search(s)
    if not m:
        return False
    return len(s[:m.start()].strip().lstrip("#>*_ \t")) <= 20


def _clean_block(s: str) -> str:
    """清掉 markdown 残留，让基底读起来像自然语言提示词而不是原文。"""
    s = re.sub(r"^[ \t]*>[ \t]?", "", s, flags=re.M)  # 引用块
    s = re.sub(r"^[ \t]*[-+][ \t]+", "", s, flags=re.M)  # 无序列表
    s = re.sub(r"^[ \t]*\*[ \t]+", "", s, flags=re.M)  # 无序列表（单星号）
    s = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", s, flags=re.M)  # 标题井号
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _base_spans(script: str) -> Tuple[List[str], List[Tuple[int, int]]]:
    """扫描出所有基底段，返回 ``(清洗后的段落文本, [(起, 止), ...])``。

    **逐段返回区间，不合并成一个 [lo, hi)**：基底小节之间可能夹着真正的镜头内容
    （实测样本里基底在最前面，但剧本没有格式保证），合并会把中间的镜头一起删掉。
    """
    if not script:
        return [], []

    lines = script.splitlines(keepends=True)
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln)

    blocks: List[str] = []
    spans: List[Tuple[int, int]] = []
    used = [False] * len(lines)

    for i, ln in enumerate(lines):
        if used[i] or not _is_base_head(ln):
            continue
        j, body = i + 1, []
        while j < len(lines):
            if _BASE_STOP.match(lines[j]) or _is_base_head(lines[j]):
                break
            body.append(lines[j])
            used[j] = True
            j += 1
        text = _clean_block("".join(body))
        if not text:
            continue
        used[i] = True
        blocks.append(text)
        spans.append((starts[i], starts[j] if j < len(lines) else len(script)))

    return blocks, spans


def _strip_spans(script: str, spans: List[Tuple[int, int]]) -> str:
    out, at = [], 0
    for s, e in sorted(spans):
        if s < at:
            continue
        out.append(script[at:s])
        at = e
    out.append(script[at:])
    return "".join(out)


def _clean_lead(lead: str) -> str:
    """滤掉前导文字里的结构性行，只留下真正的全局设定。"""
    out = []
    for ln in (lead or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or set(s) <= set("-*_= ") or s.startswith("【"):
            continue
        out.append(ln)
    return _clean_block("\n".join(out))


def extract_consistency_base(script: str) -> Tuple[str, int, int]:
    """抽出「角色基底 / 负向提示词」等全局设定段。

    返回 ``(基底文本, 最早起始下标, 最晚结束下标)``，下标供只读场景定位；
    需要精确挖除时请用 :func:`_base_spans`（合并区间会误删中间内容）。
    找不到时返回 ``("", 0, 0)``。
    """
    blocks, spans = _base_spans(script)
    if not blocks:
        return "", 0, 0
    base = _clean_block("\n\n".join(blocks))
    if len(base) > _BASE_MAX_LEN:
        base = base[:_BASE_MAX_LEN].rstrip() + "\n…（基底过长已截断）"
    return base, spans[0][0], max(e for _s, e in spans)


# --------------------------------------------------------------------------
# 剧本解析：把长剧本切成带时间码的「单元」
# --------------------------------------------------------------------------


@dataclass
class _Unit:
    """解析出的一个最小叙事单元（一个镜头 / 一个时间码块）。"""

    text: str
    seconds: int
    t_start: Optional[int] = None  # 绝对时间码；兜底切分时为 None


def _tc_to_sec(s: str) -> Optional[int]:
    parts = s.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except (ValueError, AttributeError):
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def _est_seconds(text: str) -> int:
    """没有时间码时按字数估时长。"""
    return max(3, int(round(len(text) / float(CHARS_PER_SECOND))))


def _chunk_plain(text: str, cap: int) -> List[_Unit]:
    """Tier3 兜底：既没有时间码块也没有镜头行，就按字数把纯散文均分。"""
    text = (text or "").strip()
    if not text:
        return []
    per = max(1, cap * CHARS_PER_SECOND)
    parts = [text[i:i + per] for i in range(0, len(text), per)]
    out, at = [], 0
    for p in parts:
        sec = _est_seconds(p)
        out.append(_Unit(text=p, seconds=sec, t_start=at))
        at += sec
    return out


def _parse_units(script: str, cap: int) -> Tuple[List[_Unit], str]:
    """解析剧本。返回 ``(单元列表, 前导文字)``。

    三级策略，从细到粗，命中哪个用哪个：

    * Tier1  时间码块 ``【0:00—0:36｜...】``
    * Tier2  镜头行 ``**镜头 01 (0:00—0:08)**`` / ``**段落 A (3:48—3:52)**``
    * Tier3  两者都没有 → 按字数均分

    Tier1 / Tier2 可能同时存在于同一份剧本里（幕标题 + 幕内镜头）。
    **取单元数多的那一档**（更细 → 装箱更自由），被淘汰的那档标题行
    不丢弃，作为前缀挂到下一个单元上，信息不丢。
    """
    lines = (script or "").splitlines()
    if not lines:
        return [], ""

    # 扫描所有带时间码的行，打上档位标记
    markers: List[Tuple[int, int, int, int]] = []  # (行号, 起始秒, 结束秒, 档位)
    for i, ln in enumerate(lines):
        m = re.search(_TC_PAIR, ln)
        if not m:
            continue
        a, b = _tc_to_sec(m.group(1)), _tc_to_sec(m.group(2))
        if a is None or b is None or b < a:
            continue
        tier = 1 if "【" in ln else 2
        markers.append((i, a, b, tier))

    n1 = sum(1 for m in markers if m[3] == 1)
    n2 = len(markers) - n1
    if max(n1, n2) < 2:
        return _chunk_plain(script, cap), ""

    tier = 1 if n1 > n2 else 2
    chosen = [m for m in markers if m[3] == tier]
    structural = {m[0] for m in markers if m[3] != tier}

    units: List[_Unit] = []
    for k, (ln_no, a, b, _t) in enumerate(chosen):
        end_no = chosen[k + 1][0] if k + 1 < len(chosen) else len(lines)
        # 本单元的行范围 = [ln_no, end_no)，把没被选中的结构性标题行插到最前面
        head = [lines[i] for i in sorted(structural & set(range(ln_no, end_no)))]
        body = lines[ln_no:end_no]
        text = "\n".join(x for x in (head + body) if x.strip()).strip()
        sec = b - a
        if sec <= 0:
            sec = _est_seconds(text)
        units.append(_Unit(text=text, seconds=sec, t_start=a))

    lead = "\n".join(lines[: chosen[0][0]]).strip()
    return units, lead


# --------------------------------------------------------------------------
# 切分与装箱
# --------------------------------------------------------------------------


def _split_oversize(text: str, seconds: int, cap: int) -> List[Tuple[str, int]]:
    """单个单元超过 cap 秒时再切一刀，返回 ``[(文本, 秒数), ...]``，秒数和恰为 ``seconds``。"""
    if seconds <= cap:
        return [(text, seconds)]

    parts = [p for p in re.split(r"(?<=[。！？!?；;\n])", text) if p and p.strip()]
    if len(parts) < 2:
        step = max(200, cap * CHARS_PER_SECOND // 2)
        parts = [text[i:i + step] for i in range(0, len(text), step)]
    if len(parts) < 2:
        return [(text, min(seconds, cap))]

    total = sum(len(p) for p in parts) or 1
    # 先按字数比例分配，四舍五入到整数秒
    durs = [max(1, int(round(seconds * len(p) / float(total)))) for p in parts]
    # 再把误差摊回去，保证总和精确等于 seconds
    diff = seconds - sum(durs)
    step = 1 if diff > 0 else -1
    k = 0
    guard = 0
    while diff != 0 and guard < len(durs) * abs(diff) + 16:
        i = k % len(durs)
        if step > 0 or durs[i] > 1:
            durs[i] += step
            diff -= step
        k += 1
        guard += 1

    out, buf, at = [], [], 0
    for p, d in zip(parts, durs):
        buf.append(p)
        at += d
        if at >= cap:
            out.append(("".join(buf), at))
            buf, at = [], 0
    if buf:
        out.append(("".join(buf), max(1, seconds - sum(d for _t, d in out))))
    return out


def _pack_min_bins(durs: List[int], cap: int, max_units: int = 0) -> List[List[int]]:
    """保序装箱（DP），使段数最少，且每段总时长 <= cap。返回下标分组。

    贪心（能塞就塞）在这里不最优：dur=[10,10,10,10], cap=30 贪心会得到 3 段
    （10+10+10 / 10），DP 得到 2 段。段数直接等于用户要等的生成轮次。

    ``max_units`` > 0 时限制每段最多装几个 unit（= 镜头）。
    **默认必须传 1**，理由见 plan_segments 的实测记录：一次给 muse.ai 多个
    镜头，它有相当比例会切进「我先规划/分头开工，稍后交付」的对话模式，
    一个附件都不出。段数变多换来的是每一段都真的能拍出来。
    """
    n = len(durs)
    if n == 0:
        return []
    if max_units and max_units < 1:
        max_units = 1

    INF = float("inf")
    f = [0] + [None] * n  # f[i] = 覆盖前 i 个元素所需的最少段数
    prev = [-1] * (n + 1)
    for i in range(1, n + 1):
        total, best, bj = 0, None, -1
        for j in range(i - 1, -1, -1):
            if max_units and (i - j) > max_units:
                break
            total += durs[j]
            if total > cap:
                break
            if f[j] is None:
                continue
            cand = f[j] + 1
            if best is None or cand < best:
                best, bj = cand, j
        if best is None:  # 单个元素就超 cap（理论上 _split_oversize 已避免）
            f[i], prev[i] = INF, -1
        else:
            f[i], prev[i] = best, bj

    if f[n] is None or f[n] == INF:
        # 兜底贪心，宁可多几段也不能不切
        bins, cur, total = [], [], 0
        for i, d in enumerate(durs):
            if cur and (total + d > cap or (max_units and len(cur) >= max_units)):
                bins.append(cur)
                cur, total = [], 0
            cur.append(i)
            total += d
        if cur:
            bins.append(cur)
        return bins

    bins, i = [], n
    while i > 0:
        j = prev[i]
        bins.append(list(range(j, i)))
        i = j
    bins.reverse()
    return bins


def _snap_duration(seconds: int) -> int:
    """把段内容时长向上取整到 muse.ai 真正支持的档位。"""
    s = max(1, int(seconds))
    for d in MUSE_SINGLE_DURATIONS:
        if s <= d:
            return d
    return MUSE_MAX_SINGLE_SECONDS


def plan_segments(prompt: str, requested_duration: int,
                  cap: int = MUSE_MAX_SINGLE_SECONDS,
                  max_segments: int = 16,
                  max_units_per_segment: int = 1) -> SegmentPlan:
    """把长剧本拆成若干 <= ``cap`` 秒的段。

    纯函数：同一输入必然得到同一输出（测试依赖这一点）。

    ``max_units_per_segment`` 默认 **1**，即一段只放一个镜头。实测依据
    （2026-10-03，唐先生 240 秒分镜）：一段塞 2~3 个镜头时，muse.ai 有相当
    比例会切进规划/对话模式 ——「镜头07…→镜头08…→镜头09…三段纯文本生成，
    原片都在 clips/ 里」「三个片段正在并行生成中，全部完成后我再拼接成
    28 秒成片交付」—— 结果一个附件都不出，错误统一是
    「模型未生成媒体，仅返回文本」。段数从 10 涨到 24 是有代价的
    （等待时间翻倍），但每段只讲一件事、没有「分头开工再拼接」的可乘之机，
    才真的拍得出来。
    """
    prompt = (prompt or "").strip()
    plan = SegmentPlan(total_seconds=int(requested_duration or 0))
    if not prompt:
        plan.notes.append("提示词为空，未生成分段")
        return plan

    cap = max(1, int(cap))
    max_segments = max(1, int(max_segments))
    max_units_per_segment = max(1, int(max_units_per_segment or 1))

    base_blocks, base_spans = _base_spans(prompt)
    units, lead = _parse_units(prompt, cap)
    if not units:
        plan.notes.append("未能从剧本中解析出任何片段")
        return plan

    # 基底原文不再进段体，避免同一段话既做基底又在每段里重复
    if base_spans:
        units, lead2 = _parse_units(_strip_spans(prompt, base_spans).strip(), cap)
        if units:
            lead = lead2
        else:  # 挖基底把结构挖坏了，用原解析结果，宁可重复也不要丢内容
            plan.notes.append("角色基底与正文难以分离，基底可能重复出现")

    base = _clean_block("\n\n".join(base_blocks))
    if base and len(base) > _BASE_MAX_LEN:
        base = base[:_BASE_MAX_LEN].rstrip() + "\n…（基底过长已截断）"

    # 前导文字是全局设定（片名、风格、规格），并进基底注入每一段。
    # 但要滤掉结构性行：目录标题、分隔线、时间码块标题——它们不是风格指令，
    # 留在基底里反而会让模型以为每段都要处理 0:00—0:36 这种时间范围。
    lead = _clean_lead(lead)
    if lead and lead not in base:
        base = (lead + "\n\n" + base).strip() if base else lead
    if base and len(base) > _BASE_MAX_LEN:
        base = base[:_BASE_MAX_LEN].rstrip() + "\n…（基底过长已截断）"
    plan.base = base

    # 1) 超过 cap 的单元先再切一刀
    flat: List[Tuple[str, int, Optional[int]]] = []
    for u in units:
        if u.seconds <= cap:
            flat.append((u.text, u.seconds, u.t_start))
            continue
        for txt, sec in _split_oversize(u.text, u.seconds, cap):
            flat.append((txt, sec, None))

    # 2) 保序装箱
    bins = _pack_min_bins([f[1] for f in flat], cap,
                          max_units=max_units_per_segment)

    # 3) 截断保护：段数太多就砍掉后面的，并明确告知
    truncated = 0
    if len(bins) > max_segments:
        kept_items = sum(len(b) for b in bins[:max_segments])
        truncated = len(bins) - max_segments
        dropped = sum(flat[j][1] for j in range(kept_items, len(flat)))
        bins = bins[:max_segments]
        plan.notes.append("段数超过上限 %d，已截断最后 %d 段（约 %d 秒内容未生成）"
                          % (max_segments, truncated, dropped))
        plan.notes.append("实际覆盖约 %d 秒（用户请求 %d 秒）"
                          % (sum(flat[j][1] for j in range(kept_items)),
                             plan.total_seconds))

    at = 0
    for i, b in enumerate(bins, 1):
        txt = "\n".join(flat[j][0] for j in b).strip()
        secs = sum(flat[j][1] for j in b)
        seg = Segment(index=i, t_start=at, t_end=at + secs, text=txt,
                      request_duration=_snap_duration(secs))
        at += secs
        plan.segments.append(seg)

    # 脚本本身撑不起用户要的时长时必须说清楚。用户提交 duration: 240 + 一段
    # 只有 800 字的散文，产物只能是 22 秒；不提示的话前端会显示"240 秒视频"
    # 却拿到一个 22 秒文件，比报错更难排查。
    if not truncated and plan.total_seconds and plan.covered_seconds:
        if plan.covered_seconds < plan.total_seconds * 0.9:
            plan.notes.append(
                "剧本内容约 %d 秒，短于请求的 %d 秒；已按内容实际长度分段，"
                "如需更长请补充剧本内容" % (plan.covered_seconds, plan.total_seconds))
        elif plan.covered_seconds > plan.total_seconds * 1.1:
            plan.notes.append(
                "剧本内容约 %d 秒，长于请求的 %d 秒；已全部纳入分段"
                % (plan.covered_seconds, plan.total_seconds))

    return plan


# 需要从提示词里剔除的「渲染文字」类句子。
# 2026-10-03 实测：只要提示词里出现「底部淡出字卡：…"人生有上半场"」这类要求，
# muse.ai 就会切进**规划/对话**模式 —— 它反问「要把镜头04 的字卡换成中文…吗？」、
# 「三个片段正在并行生成中，全部完成后我再拼接成 28 秒成片交付」，
# 于是整段 0 产出，错误统一是「模型未生成媒体，仅返回文本」。
# 同一份分镜里唯一没有字卡的那一段（第 1 段）一次就出片了，其余带字卡的段全灭。
#
# 所以：文字一律不进提示词，改由后期用 ffmpeg 压上去（见 plan_notes）。
# 顺带的好处是视频模型本来就烧不好中文字幕，压字我们自己控制更准。
_TEXT_OVERLAY_CJK = (r"字卡|字幕|标题文字|压字|文字排版|排版内容|片名|宣传语|"
                     r"淡入文字|字体|文案|叠字|《|》")
_TEXT_OVERLAY_EN = (r"subtitle|text overlay|typography|lettering|caption|"
                    r"title fades|tagline|typed text|on-screen text|"
                    r"midlife trilogy|holds for \d|clean kerning")
# 句子里这些缩写后面的点**不是**句号，不能在那里断句
# （"Mr. Tang's Midlife Trilogy" 会在 "Mr." 处被切开，剩半个句子漏出去）。
_ABBREV = r"(?:Mr|Mrs|Ms|Dr|Prof|St|Mt|No|vs|etc|e\.g|i\.e|Jr|Sr)"


def _is_text_sentence(s: str) -> bool:
    return bool(re.search(_TEXT_OVERLAY_CJK, s)
                or re.search(_TEXT_OVERLAY_EN, s, re.I))


def strip_text_overlays(text: str) -> str:
    """剔除提示词里所有「要在画面上渲染文字」的内容，只按句子删。

    只按句子删，不动其余描述。删完如果整段几乎不剩内容（片尾那种
    纯字卡段就是），交给调用方按黑场处理。
    """
    text = text or ""
    # 片尾的排版块整体拿掉：LaTeX 公式、引用行、书名号标题。
    # 这些不是「句子」，句子切分切不掉，必须先按块清掉。
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.S)
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    text = re.sub(r"《[^》]*》", " ", text)
    # 句末标点前是缩写时，先把点换成标记，免得在那里断句
    text = re.sub(r"\b(%s)\." % _ABBREV, r"\1@@KEEP@@", text)
    out = []
    for sent in re.split(r"(?<=[。！？!?])\s*|\n|(?<=[a-z])\.\s+", text):
        s = sent.replace("@@KEEP@@", ".").strip()
        if not s or _is_text_sentence(s):
            continue
        out.append(s)
    return "\n".join(out)


# 一段里去掉文字要求后，至少要还剩这么多字才值得让 muse.ai 去拍，
# 否则这一段其实是个纯字卡段（片尾），让它拍纯黑/空镜即可。
_MIN_VISUAL_CHARS = 80


# build_segment_prompt 产出的段头。调用方若还要把段体交给 build_video_prompt
# 再包一层画幅/时长声明，**必须先剥掉它**，否则提示词里会同时出现两个互相
# 矛盾的时长：
#     外层「时长严格为 10 秒」（= seg.request_duration，向 muse.ai 请求的档位）
#     内层「时长严格为  8 秒」（= seg.seconds，剧本时间码算出来的）
# 8 秒的剧本镜头向上取档成 10 秒是设计如此（muse.ai 只支持 5/10/30），
# 但模型读到的就是一条自相矛盾的指令。
SEG_HEAD_RE = re.compile(
    r"^\s*全新文生视频创作[^\n]*?时长严格为\s*\d+\s*秒\s*[）)\s]*[。．.]?\s*"
)


def build_segment_prompt(plan: SegmentPlan, seg: Segment) -> str:
    """组装单段的实际提示词：生成指令 + 角色基底 + 段体。

    措辞是踩过坑换来的，别再改回"第 N/M 段"那种写法。

    背景（2026-10-03 实测）：最早段头写成
        【长视频第 2/10 段 · 本段约 24 秒 · 全片约 240 秒】
        这是同一部长片的一段，必须与其它段保持人物、服装、场景、光线与画风完全一致；
    结果 3 条长视频任务全部 0 段失败，错误统一是「模型未生成媒体，仅返回文本」，
    模型的回复是「第 2/10 段的内容发我我就接着拍」这种**协作对话**语气。

    两个原因叠加：
      1. 段号是**对话语义**。模型读到"第 2 段"，合理推断第 1 段已经聊过了，
         于是反问要内容 —— 它在跟你对话，不是在生成视频。
      2. "必须与其它段保持一致"**主动提示了还有别的段**，让模型认为这是个
         多轮协作任务；而干净会话里它根本没看过其它段，产生认知矛盾。

    engine.reset_thread() 每次生成都会导航到干净的 /thread/new，上下文不会跨段
    累积 —— 所以问题出在提示词本身，不是会话历史。已确认。

    现在的写法对齐短视频的 build_video_prompt（app.py）：以生成指令开头、
    明确声明"全新生成、禁止参考历史上下文"、时长写死、段体放最后。
    跨段一致性改由角色基底承载 —— 基底本身就是"全片统一设定"，
    它是**规格**而不是**对话指代**，模型会当作渲染依据而非上下文引用。
    """
    secs = seg.seconds
    # 不要出现「第 N 段」「共 M 段」「与其它段保持一致」这类措辞。
    head = ("全新文生视频创作（纯文本全新生成，严禁参考任何历史图片、"
            "历史对话或上下文）：生成一个视频（时长严格为 %d 秒）。" % secs)
    chunks = [head]
    if plan.base:
        chunks.append("【本片统一的角色与画风设定】\n" + plan.base)

    # 文字一律不进提示词（见 strip_text_overlays 的实测记录）。
    body = strip_text_overlays(seg.text)
    if len(re.sub(r"[\s#*`—\-]", "", body)) < _MIN_VISUAL_CHARS:
        # 片尾那种纯字卡段：没有可拍的内容，就让它拍一段干净黑场，
        # 文字后期再压。绝不能把「渲染这段文字」原样交给模型。
        body = ("纯黑场画面，缓慢平稳地淡入到深黑并保持，无任何物体、"
                "无人物、无光影变化、无文字。")
    chunks.append(body)
    chunks.append("画面中不要出现任何文字、字幕、字卡、标题、台标或水印。"
                  "以上是本段的完整创作需求，请直接开始生成这个视频，"
                  "不要输出文字方案、分镜说明或制作计划。")

    out = "\n\n".join(c for c in chunks if c and c.strip()).strip()
    if len(out) > BASE_MAX_CHARS:
        # 段体优先保留（它才是这一段要拍的东西），基底其次被压缩。
        # 段尾被截掉是**静默丢剧情**，必须像基底那样留痕（notes 会带到前端），
        # 否则用户看到的成品缺了一段却毫无提示，比报错更难排查。
        tail = ("\n\n以上是本段的完整创作需求，请直接开始生成这个视频，"
                "不要输出文字方案、分镜说明或制作计划。")
        keep = BASE_MAX_CHARS - len(head) - len(tail) - 32
        b_len = min(len(plan.base), max(0, keep // 3))
        out = (head + "\n\n【本片统一的角色与画风设定】\n" + plan.base[:b_len]
               + "\n\n" + body[: max(0, keep - b_len)] + tail)
        dropped = len(body) - max(0, keep - b_len)
        if dropped > 0:
            note = "第 %d 段剧本超出单段长度上限，已截断约 %d 字，该段剧情可能不完整" % (
                seg.index, dropped)
            if note not in plan.notes:
                plan.notes.append(note)
            if len(plan.base) > b_len:
                bnote = "第 %d 段的角色基底已压缩（原 %d 字 → %d 字），一致性可能下降" % (
                    seg.index, len(plan.base), b_len)
                if bnote not in plan.notes:
                    plan.notes.append(bnote)
    return out


# --------------------------------------------------------------------------
# ffmpeg 合成
# --------------------------------------------------------------------------


def _which(name: str, override: str = "") -> str:
    if override:
        return override if (os.path.isfile(override) or shutil.which(override)) else ""
    return shutil.which(name) or ""


def _run(cmd: List[str], timeout: int = 900) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "ffmpeg 超时"
    except OSError as e:
        return 127, str(e)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace")


def _probe(path: str, ffprobe: str) -> Optional[dict]:
    rc, out = _run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                    "-of", "json", path], timeout=120)
    if rc != 0:
        return None
    try:
        info = json.loads(out)
    except ValueError:
        return None
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return None
    dur = info.get("format", {}).get("duration") or v.get("duration") or "0"
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "codec": v.get("codec_name"),
        "w": v.get("width"),
        "h": v.get("height"),
        "pix": v.get("pix_fmt"),
        "fps": v.get("r_frame_rate"),
        "duration": dur,
    }


def _same_stream(a: Optional[dict], b: Optional[dict]) -> bool:
    if not a or not b:
        return False
    return all(a.get(k) == b.get(k) for k in ("codec", "w", "h", "pix", "fps"))


def _listfile(path: str, paths: List[str]) -> None:
    """写 ffconcat 列表文件。

    **必须写文件，不能走 stdin。** 本机 ffmpeg 9.0.1 实测：`-i -` 会被解析成 `fd:`
    协议（`Protocol 'fd' not on whitelist`），`pipe:0` 同样被协议白名单拦，
    加 `-protocol_whitelist file,pipe,crypto,data` 也不行。
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for p in paths:
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))


# 合成时判定「需要裁剪」的容差（秒）。muse.ai 的原生档位只有 5/10/30，
# 而剧本分配的段长是任意整数，两者对不上的地方要裁回来。
_TRIM_TOL = 0.75


def merge_segments(paths: List[str], out_path: str = "",
                   ffmpeg: str = "", ffprobe: str = "",
                   work_dir: str = "", durations: Optional[List[float]] = None
                   ) -> dict:
    """把多段视频合成一条。

    **任何失败都只返回 ``{"ok": False, "reason": ...}``，绝不抛异常** ——
    上游的生成任务已经成功了，不能因为合成失败把整个任务变成 failed。

    两级策略：
    * 快路径：编码参数一致 → ``-c copy``（实测历史产出 100% 是 720x1280 h264
      yuv420p 24fps，必然命中）
    * 稳路径：逐段 ``scale+pad+setsar=1+fps+format`` 归一化后再 concat

    ``durations`` 是剧本给每段分配的秒数（与 ``paths`` 一一对应）。
    **muse.ai 只会输出 5/10/30 秒**，而剧本分配的段长是任意整数，``_snap_duration``
    只能向上取整 —— 不裁剪的话，13 秒的镜头会拿到 30 秒画面，成片从 240 秒
    膨胀到 300 秒，节奏全乱。所以这里按剧本时长逐段 ``-t`` 裁回去。
    """
    paths = [p for p in (paths or []) if p and os.path.isfile(p)]
    if len(paths) < 2:
        return {"ok": False, "reason": "可合并的分段不足 2 个（找到 %d 个）" % len(paths)}

    ff = _which("ffmpeg", ffmpeg)
    fp = _which("ffprobe", ffprobe) or _which("ffprobe")
    if not ff or not fp:
        missing = "ffmpeg" if not ff else "ffprobe"
        return {"ok": False, "reason": "未找到 %s，长视频只返回分段列表，不合成" % missing}

    if not out_path:
        from config import CFG  # 延迟导入：让本模块可离线单测
        os.makedirs(CFG.media_dir, exist_ok=True)
        # 必须落在 media_dir **根下**、文件名里不能带 "/"：app.get_media 显式
        # 拒绝含分隔符的名字（防目录穿越），写进 merged/ 子目录前端就取不到。
        out_path = os.path.join(CFG.media_dir, "merged_%d.mp4" % int(time.time()))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    tmp = work_dir or tempfile.mkdtemp(prefix="merge_")
    os.makedirs(tmp, exist_ok=True)
    own_tmp = not work_dir

    try:
        with _MERGE_LOCK:
            infos = [_probe(p, fp) for p in paths]
            usable = [i for i in infos if i]
            if not usable:
                return {"ok": False, "reason": "分段文件无法被 ffprobe 解析"}
            total_in = sum(i["duration"] for i in usable)

            # ---- 剧本分配的每段秒数 → 需要裁剪的段 ----
            # 按下标对齐 paths，这样某段 ffprobe 失败时也不会串位。
            want_by_idx = {}
            if durations and len(durations) == len(paths):
                for i, w in enumerate(durations):
                    try:
                        w = float(w)
                    except (TypeError, ValueError):
                        continue
                    if w > 0:
                        want_by_idx[i] = w

            def _trim_to(i):
                """该段需要的裁剪时长（秒）；不需要裁剪返回 None。"""
                w = want_by_idx.get(i)
                if w is None or infos[i] is None:
                    return None
                return w if abs(infos[i]["duration"] - w) > _TRIM_TOL else None

            need_trim = any(_trim_to(i) is not None for i in range(len(paths)))

            # ---- 快路径：参数一致，直接拼 ----
            # 需要裁剪时不能走 -c copy：裁过的段参数已经和原始段不一致了。
            if not need_trim \
                    and len(usable) == len(paths) \
                    and all(_same_stream(usable[0], i) for i in usable) \
                    and all(p.lower().endswith((".mp4", ".mov", ".m4v")) for p in paths):
                lf = os.path.join(tmp, "fast.txt")
                _listfile(lf, paths)
                rc, log = _run([ff, "-y", "-f", "concat", "-safe", "0", "-i", lf,
                                "-c", "copy", "-movflags", "+faststart", out_path])
                if rc == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                    final = _probe(out_path, fp)
                    return {"ok": True, "path": out_path, "mode": "copy",
                            "trimmed": False,
                            "seconds": (final or {}).get("duration", total_in)}

            # ---- 稳路径：先归一化，再拼 ----
            ref = usable[0]
            norm = []
            for i, p in enumerate(paths):
                dst = os.path.join(tmp, "n%03d.mp4" % i)
                vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                      "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p"
                      % (ref["w"], ref["h"], ref["w"], ref["h"]))
                args = [ff, "-y", "-i", p, "-vf", vf]
                w = _trim_to(i)
                if w is not None:
                    # -t 是输出选项：放在输入之后、输出文件之前，只裁这一段。
                    args += ["-t", "%.3f" % w]
                args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                         "-pix_fmt", "yuv420p", "-an", dst]
                rc, log = _run(args, timeout=1800)
                if rc != 0 or not os.path.isfile(dst):
                    return {"ok": False, "reason": "第 %d 段归一化失败：%s"
                            % (i + 1, log.strip().splitlines()[-1] if log.strip() else "未知错误")}
                norm.append(dst)

            lf = os.path.join(tmp, "norm.txt")
            _listfile(lf, norm)
            rc, log = _run([ff, "-y", "-f", "concat", "-safe", "0", "-i", lf,
                            "-c", "copy", "-movflags", "+faststart", out_path])
            if rc != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
                return {"ok": False, "reason": "concat 失败：%s"
                        % (log.strip().splitlines()[-1] if log.strip() else "未知错误")}

            final = _probe(out_path, fp)
            return {"ok": True, "path": out_path, "mode": "normalize",
                    "trimmed": need_trim,
                    "seconds": (final or {}).get("duration", total_in)}
    except Exception as e:  # noqa: BLE001 —— 合成失败绝不能打断已完成的任务
        return {"ok": False, "reason": "合成异常：%s" % e}
    finally:
        if own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
