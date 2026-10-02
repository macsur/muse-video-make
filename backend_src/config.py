"""muse2api 配置。"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

_DEFAULT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """自动加载项目根目录下的 .env 文件（不覆盖已存在的系统环境变量）。"""
    base = os.environ.get("MUSE2API_HOME") or _DEFAULT_BASE_DIR
    env_path = os.path.join(base, ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv()


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _detect_chromium() -> str:
    """优先使用 MUSE2API_CHROMIUM；若未设置或路径不存在，则自动探测系统已安装的 Chromium/Chrome。"""
    configured = os.environ.get("MUSE2API_CHROMIUM", "").strip()
    if configured and (os.path.isfile(configured) or shutil.which(configured)):
        return configured
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if os.path.isfile(c) or shutil.which(c):
            return c
    return configured or "chromium"


@dataclass
class Config:
    base_dir: str = field(default_factory=lambda: _env("MUSE2API_HOME", _DEFAULT_BASE_DIR))
    host: str = field(default_factory=lambda: _env("MUSE2API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("MUSE2API_PORT", "18610")))

    # 浏览器
    chromium: str = field(default_factory=_detect_chromium)
    cdp_port: int = field(default_factory=lambda: int(_env("MUSE2API_CDP_PORT", "19210")))
    home_dir: str = field(default_factory=lambda: _env("MUSE2API_HOME_DIR", os.path.expanduser("~")))
    extra_path: str = field(default_factory=lambda: _env("MUSE2API_EXTRA_PATH", "/snap/bin"))
    site_url: str = field(default_factory=lambda: _env("MUSE2API_SITE", "https://muse.ai/"))
    login_wait: int = field(default_factory=lambda: int(_env("MUSE2API_LOGIN_WAIT", "60")))

    # 浏览器 profile 根目录。
    # 踩过的坑：snap 版 chromium 受 AppArmor 约束，只能读写 $HOME 下的路径。
    # 传 --user-data-dir=/opt/... 或 /tmp/... 不会报错，但会被静默忽略，
    # 浏览器回落到 ~/snap/chromium/common/chromium。
    # 所以必须落在 $HOME 下（不要用点开头的隐藏目录，snap 的 home 接口排除隐藏文件）。
    profile_root: str = field(
        default_factory=lambda: _env("MUSE2API_PROFILE_ROOT",
                                     os.path.join(os.path.expanduser("~"),
                                                  "muse2api-profiles")))

    # 鉴权
    api_key: str = field(default_factory=lambda: _env("MUSE2API_KEY", ""))

    # 对外地址（管理页面显示「接入地址」用；留空则页面用当前域名兜底）
    public_base: str = field(
        default_factory=lambda: _env("MUSE2API_PUBLIC_BASE", ""))

    # 允许跨域调用导入接口的来源（Cookie 助手脚本从 muse.ai 页面提交时用）
    cors_origins: str = field(
        default_factory=lambda: _env("MUSE2API_CORS_ORIGINS",
                                     "https://muse.ai,https://www.muse.ai"))

    # 本地模式（run_local.py 启动时置 1）。
    #
    # 本地版对 backend_src/ 做了大量魔改（长视频分段等），而
    # /admin/update/upgrade 会用 GitHub 上游 tarball 直接覆盖 backend_src/ 下
    # 所有文件（只保护 .env / data/accounts.json / data/tasks.json 三个），
    # /admin/repo/push 则会把本地代码推回上游 main。两者对本地版都是毁灭性的，
    # 所以本地模式下一律禁用。
    local_mode: bool = field(
        default_factory=lambda: _env("MUSE2API_LOCAL_MODE", "0") not in ("0", "", "false", "False"))

    # 生成
    image_timeout: int = field(default_factory=lambda: int(_env("MUSE2API_IMAGE_TIMEOUT", "240")))
    video_timeout: int = field(default_factory=lambda: int(_env("MUSE2API_VIDEO_TIMEOUT", "600")))
    chat_timeout: int = field(default_factory=lambda: int(_env("MUSE2API_CHAT_TIMEOUT", "300")))

    # 工具调用（function calling）协议适配开关，**默认关闭**。
    # 实测 muse.ai 的助手会明确拒绝输出"伪工具调用"（原话：「我不会按这种 JSON
    # 格式输出'伪工具调用'」），强行注入协议反而会污染正常回答。
    # 所以默认只做「参数兼容」（客户端传 tools 不会 422），不注入、不解析。
    # 若目标模型行为有变，可设 MUSE2API_TOOL_PROTOCOL=1 打开这层适配再试。
    tool_protocol: bool = field(
        default_factory=lambda: _env("MUSE2API_TOOL_PROTOCOL", "0").strip() != "0")

    # ---- 长视频自动分段 ----
    # muse.ai 网页端单次最多只能出 30 秒（实测：全部历史产出都是 5/10/30 秒、
    # 720x1280 h264 yuv420p 24fps）。超过 30 秒的请求会被自动拆段依次生成，
    # 最后用 ffmpeg 合成。段内容上限默认取实测物理上限，不要往上调。

    # 单段内容的秒数上限（硬约束，不建议超过 30）
    video_seg_cap: int = field(default_factory=lambda: int(_env("MUSE2API_VIDEO_SEG_CAP", "30")))
    # 段数上限：480 秒 / 30 秒 = 16 段。超出则截断并在任务里明确写明丢了多少内容。
    video_max_segments: int = field(
        default_factory=lambda: int(_env("MUSE2API_VIDEO_MAX_SEGMENTS", "16")))
    # 长视频整体预算（秒）。单个 30 秒视频实测 371 秒，16 段约 99 分钟，留余量取 2 小时。
    long_video_budget: int = field(
        default_factory=lambda: int(_env("MUSE2API_LONG_VIDEO_BUDGET", "7200")))
    # 长视频每段的排队上限。分段任务逐段提交，段与段之间要释放浏览器，
    # 所以不能用单段视频的 video_timeout 当排队上限。
    seg_queue_timeout: int = field(
        default_factory=lambda: int(_env("MUSE2API_SEG_QUEUE_TIMEOUT", "1800")))
    # 是否用 ffmpeg 把分段合成一条。关掉则只返回分段列表。
    video_merge: bool = field(
        default_factory=lambda: _env("MUSE2API_VIDEO_MERGE", "1").strip() != "0")
    # ffmpeg / ffprobe 路径。留空则用 shutil.which 探测；探测不到时降级为
    # 「只返回分段列表」，绝不因为缺 ffmpeg 把已完成的任务变成失败。
    ffmpeg: str = field(default_factory=lambda: _env("MUSE2API_FFMPEG", ""))
    ffprobe: str = field(default_factory=lambda: _env("MUSE2API_FFPROBE", ""))

    @property
    def data_dir(self) -> str:
        return os.path.join(self.base_dir, "data")

    @property
    def media_dir(self) -> str:
        return os.path.join(self.data_dir, "media")

    @property
    def download_dir(self) -> str:
        return os.path.join(self.data_dir, "downloads")

    @property
    def profile_dir(self) -> str:
        """生成用浏览器 profile。"""
        return os.path.join(self.profile_root, "generate")

    @property
    def accounts_file(self) -> str:
        return os.path.join(self.data_dir, "accounts.json")

    @property
    def tasks_file(self) -> str:
        return os.path.join(self.data_dir, "tasks.json")

    def ensure_dirs(self):
        for d in (self.base_dir, self.data_dir, self.media_dir, self.download_dir,
                  self.profile_dir):
            os.makedirs(d, exist_ok=True)


CFG = Config()
