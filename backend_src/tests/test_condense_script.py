"""剧本压缩（_condense_video_script）的离线回归测试。纯文本，零额度零网络。

    python backend_src/tests/test_condense_script.py

回归的是 task_5717af4ebbed41d9b9cc：一份 1330 字的 markdown 体剧本（带
Master Consistency Prompt / Master Negative Prompt 引用块）按 5 秒单段下发，
压缩后被 `result[:700]` 从单词中间切断，下发给 muse.ai 的是

    ...> **通用统一负向提示词 (Master Negative Prompt)**，> `old age frail,
    wrinkled skin, sagging face, bald, receding h

反引号都没闭合。muse.ai 对残缺指令的反应不是报错而是**静默不生成**，一直
拖到 video_timeout 耗尽（603 秒）才报「等待生成超时」。

样本内联，不读 data/scripts/*.json —— 那个目录被 .gitignore 排除。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import _CONDENSE_MAX, _clip_text, _condense_video_script

# --------------------------------------------------------------------------
# 样本 A：markdown 引用体（这次的失败形态）
#
# 长度必须 > 800，否则 app.py:873 的 is_long_script 门槛不成立，函数会在
# 第 874 行原样返回 prompt，压缩逻辑压根不会执行 —— 测试就成了空跑。
# 真实的 task_5717af4ebbed41d9b9cc 是 1330 字。
# --------------------------------------------------------------------------
MARKDOWN_SCRIPT = """### 一、 核心资产设定：角色基底与负向提示词 (Master Consistency Prompt)

在每一个提示词段落中嵌入一致性基底，确保全片不换脸、不衰老。以下基底描述
与负向提示词必须逐段嵌入，任何一个镜头都不得偏离。

> **角色基底描述 (Character Base Profile)**
> **[Chinese]**: 唐进生，30多岁成熟英俊中国男性，高大健壮，宽肩厚背，腰腹紧实，姿态挺拔。五官端正，深沉眼神，眉骨突出，挺直鼻梁，下颌线极其清晰分明。干净利落的深色短发，鬓角带有少量自然黑色发丝，皮肤呈现真实的细微自然纹理。神态沉稳内敛，动作从容克制，绝无驼背或疲态老态。
> **[English]**: A handsome and robust 30-year-old Chinese man named Tang Jinsheng, tall and well-built with broad shoulders, strong posture, well-defined sharp jawline, prominent brow bone, straight nose, and calm, deep eyes.

> **通用统一负向提示词 (Master Negative Prompt)**
> `old age frail, wrinkled skin, sagging face, bald, receding hairline, hunched back, slouching, weak body, sickly, youthful idol, anime, cartoon, greasy expression`

* **镜头 10 (1:25—1:35) 戴老板的目光**
* **画面与机位**：吧台或沙发区，打扮优雅成熟的女性端着酒杯，目光原本漫不经心，在掠过唐进生时微微停顿，眼神中闪过一丝专注与好奇。景深极浅，人物面部清晰，背景吧台化为柔和光斑。
* **English Prompt**: `Over-the-shoulder medium close-up of an elegant mature businesswoman holding a drink, her gaze lingering briefly, shallow depth of field.`

* **镜头 11 (1:35—1:45) 无言的对峙**
* **画面与机位**：两人隔着吧台对峙，空气凝滞。侧逆光勾出唐进生的下颌线与眉骨轮廓，戴老板的表情从好奇转为审慎。
* **English Prompt**: `Two-shot across a bar counter, tense silence, rim light carving the jawline, restrained body language.`
"""

# --------------------------------------------------------------------------
# 样本 B：【】方括号体（必须保持原有行为，不能被 markdown 改动带坏）
# 含【镜头 X】标记，走 has_scene_markers 分支，与样本 A 是两条不同的入口。
# --------------------------------------------------------------------------
BRACKET_SCRIPT = """【统一摄影】全片使用中长焦，避免广角变形；人物镜头保持胸口高度

【镜头一｜第一次出场】清晨薄雾中的旧城区院落
唐进生在击打悬挂沙袋，出拳连贯，汗水顺着下颌线滑落。
摄影机缓慢横移，雾气在晨光中形成柔和光柱。
地面有积水，脚步溅起细小水花。

【镜头二｜夜色登场】夜晚城市街道
唐进生从远处走来，肩膀自然打开，背部挺直，步幅适中。
城市灯光在他身后形成柔和散景，车流光轨划过。
他在一处橱窗前停下，整理衣领。

【镜头三｜书房独处】室内书房
唐进生坐在书桌前翻阅文件，眉头微蹙，台灯光线从侧面打亮面部。
桌面上散落着几份摊开的图纸和一支钢笔。

【负面提示】
不要出现老人脸，不要衰老松弛，不要驼背秃顶，不要网红脸
"""


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok  %s" % msg)


def test_no_mid_sentence_cut():
    """核心回归：任何输出都不得从单词/句子中间截断。"""
    print("test_no_mid_sentence_cut")
    out = _condense_video_script(MARKDOWN_SCRIPT, 5)
    check(len(out) <= _CONDENSE_MAX, "输出不超上限 (%d <= %d)" % (len(out), _CONDENSE_MAX))
    check(not out.endswith(("h", "th", "…old age frail,")), "结尾不是半个单词")
    # markdown 记号不该泄漏到喂给模型的正文里
    check("`" not in out, "没有残留反引号")
    check("**" not in out, "没有残留加粗标记")
    check(not out.rstrip().endswith("，"), "结尾不是悬空逗号")


def test_keeps_character_and_negative():
    """角色基底和负向提示是优先级最高的两段，绝不能被裁掉。"""
    print("test_keeps_character_and_negative")
    out = _condense_video_script(MARKDOWN_SCRIPT, 5)
    check("唐进生" in out, "保留了角色基底")
    check("old age frail" in out, "保留了英文负向提示词")
    check("禁止：" in out, "负向提示词进了「禁止」段")
    check("hunched back" in out, "负向提示词没被腰斩")


def test_no_regression_bracket_script():
    """【】体剧本的原有行为不能被 markdown 归一化带坏。"""
    print("test_no_regression_bracket_script")
    out = _condense_video_script(BRACKET_SCRIPT, 15)
    check(len(out) <= _CONDENSE_MAX, "输出不超上限 (%d)" % len(out))
    check("唐进生" in out, "保留了场景内容")
    check("禁止：" in out, "【负面提示】块被正确收进「禁止」段")
    check("老人脸" in out, "中文禁用词未被关键词过滤丢掉")


def test_clip_text_boundaries():
    print("test_clip_text_boundaries")
    check(_clip_text("短句", 100) == "短句", "不超限则原样返回")

    # 契约：切点必须落在句读边界上，且切掉的分隔符由「…」替代
    # （而不是补成「句子。。…」这种重复标点）。
    for limit in (20, 35, 50, 80, 140):
        src = "这是一个很长的句子需要被裁剪。" * 20
        clipped = _clip_text(src, limit)
        check(len(clipped) <= limit + 1, "limit=%d 输出不超限 (%d)" % (limit, len(clipped)))
        check(clipped.endswith("…"), "limit=%d 超限时补省略号" % limit)
        cut = len(clipped) - 1                      # 省略号之前的内容长度
        check(src[:cut] == clipped[:-1], "limit=%d 裁剪结果是原文前缀" % limit)
        check(src[cut] in "。；;，,、 ", "limit=%d 切点落在句读边界上" % limit)

    # 英文（无句读分隔符时退化为按空格断词，仍不能切碎半个单词）
    en = "over the shoulder medium close up of an elegant mature businesswoman " * 6
    clipped = _clip_text(en, 50)
    check(clipped.endswith("…"), "英文裁剪也补省略号")
    check(clipped[:-1].split()[-1] in
          {"over", "the", "shoulder", "medium", "close", "up", "of", "an",
           "elegant", "mature", "businesswoman"}, "英文按词断而非断在词中")


if __name__ == "__main__":
    for fn in (test_no_mid_sentence_cut, test_keeps_character_and_negative,
               test_no_regression_bracket_script, test_clip_text_boundaries):
        fn()
    print("\n全部通过")