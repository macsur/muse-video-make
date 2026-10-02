"""muse2api — 把 Muse(muse.ai) 网页免费账号的对话/生图/生视频额度反代成 API。

OpenAI 兼容:
  POST /v1/chat/completions          （文本 / 代码对话，支持 stream，可接 Codex）
  POST /v1/images/generations
  POST /v1/videos  +  GET /v1/videos/{task_id}
  GET  /v1/models
  GET  /v1/media/{name}

管理（前端账号池管理页面在 GET /）:
  GET    /admin/status
  GET    /admin/accounts
  POST   /admin/accounts            （单条 / 批量文本 / 批量数组）
  PATCH  /admin/accounts/{id}       （改标签、启用/禁用）
  DELETE /admin/accounts/{id}
  POST   /admin/accounts/{id}/test  （真实打开 muse.ai 验证会话是否有效）
  POST   /admin/accounts/{id}/relogin
  GET    /admin/tasks               （任务记录）
  DELETE /admin/tasks/{id}  |  POST /admin/tasks/clear
  GET    /admin/media               （媒体库）
  GET    /admin/extension           （浏览器扩展 zip，用来取 cookie）
  GET    /admin/cookie-helper       （命令行取 cookie 脚本，进阶）
"""
from __future__ import annotations

from typing import Any

import asyncio
import base64
import io
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
import zipfile

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel, Field

from config import CFG
from engine import ESSENTIAL_COOKIES, MuseAuthError, MuseEngine, MuseGenerationError
from longvideo import (MUSE_MAX_SINGLE_SECONDS, MUSE_SINGLE_DURATIONS,
                       build_segment_prompt, merge_segments, plan_segments)
from scheduler import ST_DONE, ST_FAILED, ST_QUEUED, ST_RUNNING, ST_TIMEOUT, Scheduler
from store import Store, account_expiry, min_expiry

import sys
log = logging.getLogger("muse2api")
log.setLevel(logging.INFO)
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_h)

CFG.ensure_dirs()
app = FastAPI(title="muse2api", version="1.5.3")

# Cookie 助手脚本从 muse.ai 页面发起导入请求，需要放行该来源；
# 浏览器扩展从 chrome-extension:// 发起，也一并放行。
#
# 上游原本在这里硬编码 allow_origins=["*"]，理由是「本服务用 Bearer Key 鉴权、
# 不依赖 Cookie，放行来源不会带来越权风险」，同时兼容 Open WebUI / LobeChat 等
# 各类 Web 客户端。这个理由在服务器部署下成立。
#
# 但本地版不成立，有两个叠加因素：
#   1. run_local 过去把前端静态服务绑在 0.0.0.0，同一局域网内谁都能打开前端；
#   2. API Key 被 prepare_web_index 明文注入 web/index.html。
# 在「0.0.0.0 + 明文 Key」的前提下放开所有来源，等于把账号额度暴露给整个局域网，
# 所以本地模式改为白名单：显式列出允许的前端来源（含本地 web 端口）。
#
# 注意：本地前端 (127.0.0.1:8090) 与 API (127.0.0.1:18610) 是**不同端口**，
# 属于跨源请求，所以本地 web 源必须显式放行，否则会把自己给拦住。
_origins = [o.strip() for o in (CFG.cors_origins or "").split(",") if o.strip()]
if CFG.local_mode:
    # 本地前端由 run_local 起在另一个端口（默认 8090），与 API 跨源，
    # 必须显式放行，否则会把自己的前端也拦掉。run_local 注入 MUSE2API_WEB_PORT。
    try:
        _web_port = int(os.environ.get("MUSE2API_WEB_PORT", "0")) or None
    except ValueError:
        _web_port = None
    _local_origins = {"http://127.0.0.1:%d" % CFG.port, "http://localhost:%d" % CFG.port}
    if _web_port:
        _local_origins.add("http://127.0.0.1:%d" % _web_port)
        _local_origins.add("http://localhost:%d" % _web_port)
    _origins = sorted(set(_origins) | _local_origins)
else:
    _origins = ["*"]

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(CORSMiddleware,
                   allow_origins=_origins,
                   allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["*"],
                   expose_headers=["*"],
                   max_age=600)

store = Store(CFG)
engine = MuseEngine(CFG)

# 浏览器只有 1 个实例，所有生成路径必须串行。
# GEN_LOCK 是那把真正的互斥锁；SCHED 是架在它上面的 FIFO 队列调度器。
# 详见 scheduler.py 顶部注释（修缺陷 3 / 4 / 10）。
GEN_LOCK = threading.Lock()
# 图片任务准入与幂等判定的专用锁：只保护 store 里图片任务的「查重 + 建任务」
# 这段临界区，与浏览器串行无关，因此独立于 GEN_LOCK。
IMAGE_TASK_LOCK = threading.Lock()
# run_timeout：单任务执行看门狗（缺陷 4 兜底）。
# 语义是「engine 自己的超时（video 默认 600s）应先生效」；看门狗只处理
# engine 连自己的超时都没走完就卡死的情况，所以取 video_timeout + 300s 余量。
# 之前设成 *2（1200s）过长 —— 真出问题时用户要等 20 分钟才看到失败。
SCHED = Scheduler(GEN_LOCK, max_queue=100, queue_timeout=900,
                  run_timeout=CFG.video_timeout + 300)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _on_sched_run_timeout(job):
    """调度器看门狗触发：强杀浏览器，打断卡死的生成，释放锁。"""
    log.error("【看门狗】强制重启浏览器以打断卡死任务: %s", getattr(job, "label", "?"))
    try:
        engine.stop()
    except Exception as exc:  # noqa: BLE001
        log.warning("【看门狗】engine.stop() 异常: %s", exc)


SCHED.on_run_timeout = _on_sched_run_timeout


# ------------------------- OpenAI 风格的错误响应 -------------------------
# 各类智能体基本都按 OpenAI 的 {"error": {"message": ...}} 取错误信息；
# FastAPI 默认返回的是 {"detail": ...}，客户端会读不到原因、只显示"未知错误"。
# 所以 /v1/* 统一转成 OpenAI 格式，管理接口保持原样（前端依赖 detail）。
def _err_type(status: int) -> str:
    if status == 404:
        return "not_found_error"
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "server_error"
    return "invalid_request_error"


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": str(exc.detail),
                               "type": _err_type(exc.status_code),
                               "param": None, "code": exc.status_code}},
            headers=getattr(exc, "headers", None))
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/"):
        return JSONResponse(status_code=422, content={"error": {
            "message": "请求参数校验失败：" + str(exc.errors())[:400],
            "type": "invalid_request_error", "param": None, "code": 422}})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# ------------------------- 405 鉴权旁路修复（缺陷 11） -------------------------
# 问题：Starlette 的 405 Method Not Allowed 是在「路由匹配阶段」直接返回的，
# 早于 FastAPI 的依赖注入 —— 也就是说 `Depends(auth)` 根本没跑。
# 结果：未授权的人只要用错方法（比如对 /v1/videos 发 GET），
# 就能根据 405 / 200 / 404 的差异枚举出服务到底有哪些端点。
#
# 修法：加一层最外层中间件，凡是命中 /v1/* 且最终返回 405 的请求，
# 先做一次鉴权；鉴权不过直接返回 401，不再泄露「这个方法不行」的信号。
@app.middleware("http")
async def _guard_method_not_allowed(request: Request, call_next):
    response = await call_next(request)
    if (response.status_code == 405
            and request.url.path.startswith("/v1/")):
        auth_err = _check_bearer(request.headers.get("authorization"))
        if auth_err is not None:
            return JSONResponse(
                status_code=401,
                content={"error": {"message": auth_err,
                                   "type": "invalid_request_error",
                                   "param": None, "code": 401}})
    return response


def _check_bearer(authorization: str | None) -> str | None:
    """返回 None 表示鉴权通过；否则返回错误信息字符串。

    必须与 `auth()` 的判定逻辑保持一致，否则会出现
    「405 路径放行了但真实请求仍被拒」的不一致。
    """
    if not CFG.api_key:
        return None
    if not authorization or not authorization.lower().startswith("bearer "):
        return "缺少 Authorization: Bearer <key>"
    parts = authorization.split(None, 1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if not token or token != CFG.api_key:
        return "API key 无效"
    return None

MODELS = [
    {"id": "muse-spark", "object": "model", "owned_by": "muse",
     "description": "Muse Spark —— 文本 / 代码对话（网页免费额度，支持流式）"},
    {"id": "muse-image", "object": "model", "owned_by": "muse",
     "description": "Muse Image —— 文生图 / 图像编辑（网页免费额度）"},
    {"id": "muse-video", "object": "model", "owned_by": "muse",
     "description": "Muse Video —— 文生视频 / 图生视频（网页免费额度）"},
]

# 下游（Codex / Cline / 各种客户端）习惯按 OpenAI、Anthropic 的名字传模型，
# 这里统一映射到 muse 的真实能力上。
#
# 说明：muse.ai 网页是自动路由的 agent，对外**没有可枚举的模型清单**，
# 能稳定调用的就是三条真实能力 —— Muse Spark（语言/代码）、
# Muse Image（生图）、Muse Video（生视频）。别名只是让下游不用改配置。
MODEL_ALIASES = {
    # ---- 文本 / 代码 → muse-spark ----
    "muse-text": "muse-spark", "muse-chat": "muse-spark", "muse-llm": "muse-spark",
    "koda": "muse-spark",
    "gpt-3.5-turbo": "muse-spark", "gpt-4": "muse-spark", "gpt-4-turbo": "muse-spark",
    "gpt-4o": "muse-spark", "gpt-4o-mini": "muse-spark", "gpt-4.1": "muse-spark",
    "gpt-4.1-mini": "muse-spark", "gpt-5": "muse-spark", "gpt-5-codex": "muse-spark",
    "o1": "muse-spark", "o1-mini": "muse-spark", "o3": "muse-spark",
    "o3-mini": "muse-spark", "o4-mini": "muse-spark",
    "codex": "muse-spark", "codex-mini-latest": "muse-spark",
    "claude-3-5-sonnet": "muse-spark", "claude-3-5-sonnet-latest": "muse-spark",
    "claude-3-7-sonnet": "muse-spark", "claude-sonnet-4": "muse-spark",
    "claude-opus-4": "muse-spark", "claude-3-opus": "muse-spark",
    "claude-3-haiku": "muse-spark",
    "deepseek-chat": "muse-spark", "deepseek-coder": "muse-spark",
    "deepseek-reasoner": "muse-spark", "qwen-coder": "muse-spark",
    "gemini-2.5-pro": "muse-spark", "gemini-2.5-flash": "muse-spark",
    # ---- 生图 → muse-image ----
    "muse-img": "muse-image", "dall-e": "muse-image", "dall-e-3": "muse-image",
    "gpt-image-1": "muse-image", "flux": "muse-image", "midjourney": "muse-image",
    # ---- 生视频 → muse-video ----
    "muse-vid": "muse-video", "muse-videos": "muse-video",
    "sora": "muse-video", "sora-2": "muse-video", "veo": "muse-video",
    "veo-3": "muse-video", "kling": "muse-video", "runway": "muse-video",
}


def resolve_model(name: str | None, default: str = "muse-image") -> str:
    n = (name or "").strip().lower()
    return MODEL_ALIASES.get(n, n or default)


# ------------------------- 鉴权 -------------------------
def auth(authorization: str | None = Header(default=None)):
    if not CFG.api_key:
        return True
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 Authorization: Bearer <key>")
    parts = authorization.split(None, 1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if not token or token != CFG.api_key:
        raise HTTPException(401, "API key 无效")
    return True


# ------------------------- 请求模型 -------------------------

def _renew_and_persist(acc_id: str, wake_vm: bool = True, force: bool = False) -> dict | None:
    """调用 /api/session 续签账号 cookie 并写回 store，返回最新账号 dict。
    近期（10分钟内）已续签且状态正常的账号直接复用，避免每次请求阻塞 2~3 秒 HTTP 往返。"""
    acc = store.get_account(acc_id)
    if not acc or not acc.get("cookies"):
        return acc
    now = time.time()
    last_sync = max(
        int(acc.get("synced_at") or 0),
        int(acc.get("last_keepalive") or 0),
        int(engine._last_http_renew.get(acc_id, 0)),
    )
    if not force and acc.get("ok") is True and (now - last_sync) < 600:
        engine._last_http_renew[acc_id] = last_sync
        return acc
    try:
        res = engine.renew_session_http(acc["cookies"], acc.get("cookies_exp"), wake_vm=wake_vm)
        engine._last_http_renew[acc_id] = now
        if res.get("cookies"):
            store.update_account(acc_id, cookies=res["cookies"],
                                 cookies_exp=res.get("cookies_exp"),
                                 ok=True if res.get("ok") else acc.get("ok"),
                                 synced_at=int(now))
            store.touch_keepalive(acc_id, True, f"会话正常 (VM: {res.get('vm_state') or 'RUNNING'})")
    except MuseAuthError as exc:
        store.mark(acc_id, False, str(exc))
        raise
    except Exception as exc:
        log.warning("HTTP 预续签账号 %s 异常: %s", acc_id, exc)
    return store.get_account(acc_id)


def safe_chat_stream(cookies: dict, prompt: str, expires: dict | None,
                     timeout: int, account_id: str | None):
    """在独立线程中执行 chat_stream，通过 Queue 往外吐增量文本。

    **锁的获取方式已改**：不再由本函数内的线程直接 `with GEN_LOCK`
    抢占，而是把「整段生成」作为一个 job 提交给 SCHED（FIFO 单 worker），
    由 worker 在持锁状态下执行。这样 chat / image / video 三条路径
    都走同一条公平队列，先来先得（修缺陷 3 / 10）。

    无论下游客户端何时断连、异常或超时，stop_event + finally 块保证
    100% 立即释放锁并让出 worker，绝不死锁。
    若首字前遇到单账号 VM 卡死或会话异常，自动切换下一个健康账号重试一次。
    """
    import queue
    q = queue.Queue(maxsize=100)
    stop_event = threading.Event()

    def job():
        """由调度器 worker 在持锁状态下执行；负责跑完整个生成过程。"""
        cur_id = account_id
        cur_cookies = cookies
        cur_exp = expires
        last_exc = None
        try:
            for attempt in range(2):
                if stop_event.is_set():
                    return
                if attempt > 0:
                    alt = store.pick_account(rotate=True, force_rotate=True, exclude_id=cur_id)
                    if not alt or alt["id"] == cur_id:
                        break
                    cur_id = alt["id"]
                    cur_cookies = alt["cookies"]
                    cur_exp = alt.get("cookies_exp")
                    log.info("【对话自动切号】切换到备用账号 %s (%s) 重试...", alt.get("label"), cur_id)
                yielded = False
                try:
                    if cur_id:
                        refreshed = _renew_and_persist(cur_id, wake_vm=True, force=(attempt > 0))
                        if refreshed:
                            cur_cookies = refreshed["cookies"]
                            cur_exp = refreshed.get("cookies_exp")
                    if stop_event.is_set():
                        return
                    engine.start()
                    for chunk in engine.chat_stream(
                        cur_cookies, prompt, cur_exp, timeout,
                        account_id=cur_id, stop_event=stop_event
                    ):
                        yielded = True
                        q.put(("data", chunk))
                        if stop_event.is_set():
                            return
                    if cur_id:
                        store.mark(cur_id, True, "")
                        _sync_cookies(cur_id)
                    return
                except MuseAuthError as exc:
                    last_exc = exc
                    if cur_id:
                        store.mark(cur_id, False, str(exc))
                    if yielded:
                        break
                except Exception as exc:
                    last_exc = exc
                    try:
                        engine.reset_thread()
                    except Exception:
                        pass
                    if yielded:
                        break
            if last_exc is not None:
                q.put(("error", last_exc))
        finally:
            q.put(("done", None))

    try:
        SCHED.submit(job, label="chat")
    except Exception:  # noqa: BLE001  # 队列满
        raise MuseGenerationError("生成队列已满，请稍后重试")

    try:
        while True:
            kind, val = q.get()
            if kind == "data":
                yield val
            elif kind == "error":
                raise val
            else:
                break
    finally:
        stop_event.set()


# ------------------------- 参数校验（修缺陷 1 / 2 / 8） -------------------------
# 实测背景：
#   * 缺陷 1 —— `duration` 原为裸 int，传 999 / 0 / -1 都被静默接受，
#     最终拼进提示词交给 muse.ai，产出与预期严重不符且无任何报错。
#   * 缺陷 2 —— `size` 原为裸 str，传任意字符串都被原样写进提示词。
#   * 缺陷 8 —— `reference_image` / `image` 传非法内容（不是图片、URL 打不开、
#     base64 乱码）时，`_normalize_image` 失败后**静默返回空**，任务照样往下跑，
#     白等 1.5~3 倍时间才失败（实测 194.7s）。
# 这里统一做「入口即校验」，把非法输入在提交阶段就打回去。
# muse.ai 单次**真正**支持的时长档位。原来的 {5,6,10,30,60,120,240,480} 是
# 本 app 自己编的白名单：ffprobe 扫过全部历史产出，最长 30.0 秒，清一色
# 720x1280。放行 240 秒的结果是 9858 字剧本原样发过去、agent 卡 600 秒零产出
# （task_5aec863f8e1a4764b4ae）。
_MUSE_SINGLE_DURATIONS = set(MUSE_SINGLE_DURATIONS)          # {5, 10, 30}
_MAX_VIDEO_SECONDS = 480                                    # 更长则自动分段
_VALID_IMAGE_MIME = {"image/png", "image/jpeg", "image/jpg", "image/webp",
                     "image/gif", "image/bmp"}
_MAX_REF_BYTES = 20 * 1024 * 1024      # 参考图上限 20MB


def validate_video_duration(d: int | None) -> int:
    """校验并归一视频时长。缺陷 1。

    双档放行：
    * ``5 / 10 / 30`` —— muse.ai 原生档位，一次生成出一条
    * ``31 ~ 480`` —— 走长视频自动分段，拆成多个 <=30s 的短片依次生成后合成
    """
    if d is None:
        return 6
    try:
        di = int(d)
    except (TypeError, ValueError):
        raise HTTPException(400, f"duration 必须是整数，收到: {d!r}")
    if di in _MUSE_SINGLE_DURATIONS:
        return di
    if MUSE_MAX_SINGLE_SECONDS < di <= _MAX_VIDEO_SECONDS:
        return di
    raise HTTPException(
        400,
        f"不支持的 duration={di}。可填 muse.ai 原生档位 {sorted(_MUSE_SINGLE_DURATIONS)}"
        f"（一次出一条），或 {MUSE_MAX_SINGLE_SECONDS + 1}~{_MAX_VIDEO_SECONDS}"
        f"（自动拆成多个 {MUSE_MAX_SINGLE_SECONDS} 秒短片后合成）")


def validate_size(size: str | None, aspect_ratio: str | None) -> None:
    """校验尺寸/比例字符串。缺陷 2。"""
    s = (size or "").strip().lower()
    if not s:
        return
    # 允许 "auto" / "WxH" / 常见比例 "16:9"
    if s == "auto":
        return
    if re.fullmatch(r"\d{2,5}x\d{2,5}", s):
        w, h = (int(x) for x in s.split("x"))
        if not (64 <= w <= 8192 and 64 <= h <= 8192):
            raise HTTPException(400, f"size 尺寸超出范围(64~8192): {size!r}")
        return
    if re.fullmatch(r"\d{1,2}[:/]\d{1,2}", s):
        return
    if any(k in s for k in ("portrait", "landscape", "square", "竖屏", "横屏", "正方形")):
        return
    raise HTTPException(400, f"无法识别的 size/aspect_ratio: {size!r}")


def validate_reference_image(ref: str | None) -> str | None:
    """校验参考图输入。缺陷 8。

    只做「轻量校验」——不下载远程图、不解码大 base64，避免在请求线程里
    产生新的长阻塞；真正无法使用的输入会在生成阶段被 MuseGenerationError
    明确抛出，而不是像以前那样静默忽略继续跑。
    返回归一后的字符串（可能带 400 抛错）。
    """
    if ref is None:
        return None
    if not isinstance(ref, str):
        raise HTTPException(400, "reference_image 必须是字符串（URL / data URI / base64）")
    s = ref.strip()
    if not s:
        return None
    if len(s) > _MAX_REF_BYTES * 2:   # base64 膨胀约 4/3，留余量
        raise HTTPException(400, f"参考图数据过大（>{_MAX_REF_BYTES // 1024 // 1024}MB）")
    if s.startswith("data:"):
        head = s.split(",", 1)[0]
        mime = head.split(";")[0].replace("data:", "").strip().lower()
        if mime and mime not in _VALID_IMAGE_MIME:
            raise HTTPException(400, f"参考图 MIME 不受支持: {mime}")
        if "," not in s or not s.split(",", 1)[1].strip():
            raise HTTPException(400, "data URI 参考图内容为空")
        return s
    if s.startswith(("http://", "https://")):
        return s
    # 纯 base64（无 data: 前缀）：粗略校验字符集与长度
    if re.fullmatch(r"[A-Za-z0-9+/=\s]+", s):
        if len(s) < 32:
            raise HTTPException(400, "base64 参考图数据过短，疑似非法")
        return s
    raise HTTPException(
        400, "reference_image 无法识别，仅支持 http(s) URL / data URI / base64")


class ImageRequest(BaseModel):
    prompt: str
    model: str = "muse-image"
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    response_format: str = "url"      # url | b64_json
    timeout: int | None = Field(default=None, ge=1, le=600)
    extra: str | None = None
    image: Any = None
    images: list | None = None
    reference_image: str | None = None
    async_: bool = Field(False, alias="async")


class VideoRequest(BaseModel):
    prompt: str
    model: str = "muse-video"
    duration: int | None = None
    size: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    timeout: int | None = None
    extra: str | None = None
    image: Any = None
    image_url: Any = None
    reference_image: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list | None = None


class ChatRequest(BaseModel):
    """OpenAI Chat Completions 请求。

    对第三方客户端要尽量宽松：不认识字段一律忽略（Pydantic 默认行为），
    只挑我们真正用得上的读 —— 否则各种智能体各传各的参数就会 422。
    """
    model: str = "muse-spark"
    messages: list[ChatMessage] = []
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    prompt: str | None = None       # 兼容把 prompt 直接放顶层的客户端
    # 下面这些声明出来只是为了「能读到」，muse.ai 端不做对应处理
    tools: list | None = None
    tool_choice: object | None = None
    response_format: object | None = None
    stream_options: object | None = None


class ResponsesRequest(BaseModel):
    """OpenAI Responses API（新版 Codex 默认走这个）。"""
    model: str = "muse-spark"
    input: str | list | None = None
    instructions: str | None = None
    stream: bool = False
    max_output_tokens: int | None = None
    timeout: int | None = None
    tools: list | None = None
    store: bool | None = None


class AccountRequest(BaseModel):
    label: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    cookie_header: str | None = None
    batch: str | None = None          # 多行文本，每行一个账号
    expires: dict[str, int] = Field(default_factory=dict)   # cookie 名 -> 过期时间戳


class AccountPatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None


# ------------------------- 提示词构造 -------------------------
def build_image_prompt(r: ImageRequest) -> str:
    has_ref = bool(r.reference_image or r.image or r.images)
    if has_ref:
        parts = [f"基于我本次上传附带的参考图片进行生图/编辑：{r.prompt.strip()}"]
    else:
        parts = [f"全新文生图创作（当前未提供任何参考图，请勿查找历史相册或向用户索要原图，直接根据文字描述从零绘制生成一张全新图片）：{r.prompt.strip()}"]
    ar = (r.aspect_ratio or "").strip().lower()
    sz = (r.size or "").strip().lower()

    if any(k in ar or k in sz for k in ("9:16", "9/16", "portrait", "竖屏", "720x1280", "1080x1920")):
        parts.append("【画面构图与比例要求】：严格 9:16 竖屏满屏画幅（9:16 vertical portrait aspect ratio，高大于宽的手机全屏竖版画面），绝对不要生成横屏，保持垂直构图")
    elif any(k in ar or k in sz for k in ("16:9", "16/9", "landscape", "横屏", "1280x720", "1920x1080")):
        parts.append("【画面构图与比例要求】：16:9 宽屏横屏画幅（16:9 widescreen landscape aspect ratio）")
    elif any(k in ar or k in sz for k in ("1:1", "square", "正方形", "1024x1024")):
        parts.append("【画面构图与比例要求】：1:1 正方形画幅（1:1 square aspect ratio）")
    elif any(k in ar or k in sz for k in ("4:3", "4/3")):
        parts.append("【画面构图与比例要求】：4:3 比例画幅")
    elif any(k in ar or k in sz for k in ("3:4", "3/4")):
        parts.append("【画面构图与比例要求】：3:4 竖向画幅")
    elif r.aspect_ratio:
        parts.append(f"【画面构图与比例要求】：{r.aspect_ratio} 画面比例")
    elif r.size:
        parts.append(f"尺寸/比例：{r.size}")

    if has_ref:
        parts.append("【纯净画面要求】：彻底清除并去除参考图中的所有文字、水印、签名、角标及Logo标记（clean image without any watermark, text, or logo），输出绝对纯净无字画面")

    if r.extra:
        parts.append(r.extra)
    return "，".join(parts)


def _content_text(content) -> str:
    """把 OpenAI 的 content 归一成纯文本（兼容多模态 list 形式）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t in (None, "text", "input_text", "output_text"):
                    parts.append(str(item.get("text") or ""))
                elif t == "image_url":
                    parts.append("[图片]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(content)


def build_chat_prompt(messages: list[ChatMessage]) -> str:
    """把 messages 拼成发给 muse.ai 的一段提示词。

    muse.ai 网页本身是个带上下文的会话，但本 API 是无状态的（每次可能落到
    不同账号/页面），所以把历史拼进 prompt 最可控 —— 这正好匹配 Codex 这类
    「每轮都带全量历史」的客户端。
    """
    system, turns = [], []
    for m in messages:
        role = (m.role or "").strip().lower()
        text = _content_text(m.content).strip()
        if role == "tool":
            # 工具执行结果 → 当成"用户提供的信息"发过去
            if text:
                turns.append(("user", "【工具执行结果】\n" + text))
            continue
        if not text:
            # 部分客户端的 assistant 消息只带 tool_calls、没有正文
            if role == "assistant" and m.tool_calls:
                turns.append(("assistant", "【请求调用工具】" + json.dumps(
                    m.tool_calls, ensure_ascii=False)[:600]))
            continue
        if role in ("system", "developer"):
            system.append(text)
        else:
            turns.append((role, text))

    # 单轮且无系统指令 → 直接发原文，最贴近自然对话
    if len(turns) == 1 and not system and turns[0][0] == "user":
        return turns[0][1]

    parts = []
    if system:
        sys_text = "\n\n".join(system)
        sys_text = sys_text.replace("danger-full-access", "standard-workspace-access")
        parts.append(f"背景与任务设定：\n{sys_text}")
    for role, text in turns:
        label = "助手" if role == "assistant" else "用户"
        parts.append(f"{label}：\n{text}")
    return "\n\n".join(parts)


# ------------------------- 工具调用（function calling）适配 -------------------------
# muse.ai 的网页模型**不会**返回结构化的 tool_calls，所以这里做一层协议适配：
#   1) 请求带 tools 时，把工具定义翻译成提示词里的【工具调用协议】；
#   2) 模型按协议输出 ```json {"tool": "...", "arguments": {...}} ```；
#   3) 我们把这段解析回 OpenAI 的 tool_calls 交给下游 agent。
#
# 注意：这是"尽力适配"而非保证 —— 目标模型是通用对话模型，没有针对
# function calling 做微调，遵守协议的程度需要实测观察。
_TOOL_PROTOCOL_HEAD = """你可以根据需要调用以下工具来协助用户完成任务。
若需调用工具，请直接输出如下格式的 JSON 代码块（不要包含其他多余解释）：
```json
{"name": "<工具名>", "arguments": {<参数>}}
```
如果需要调用多个工具，请输出包含多个对象的 JSON 数组。
如果无需调用工具，请直接用自然语言回答。

可用工具列表：
"""


def _describe_params(params) -> str:
    """把 JSON Schema 的参数描述成易读的多行文本。"""
    if not isinstance(params, dict):
        return "      （无参数）"
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    if not props:
        return "      （无参数）"
    lines = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        lines.append("      - %s (%s, %s)%s" % (
            name, spec.get("type") or "any",
            "必填" if name in required else "可选",
            (" " + spec["description"]) if spec.get("description") else ""))
    return "\n".join(lines)


def build_tools_prompt(tools: list | None) -> str:
    """把 tools 定义翻译成提示词片段（没有工具时返回空串）。

    同时兼容 Chat Completions 的 `{"type":"function","function":{...}}`
    和 Responses API 的 `{"type":"function","name":...,"parameters":...}`。
    """
    if not tools:
        return ""
    items = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        if not fn.get("name"):
            continue
        desc = fn.get("description") or ""
        items.append("%d. %s%s\n   参数：\n%s" % (
            len(items) + 1, fn["name"], (" — " + desc) if desc else "",
            _describe_params(fn.get("parameters"))))
    if not items:
        return ""
    return _TOOL_PROTOCOL_HEAD + "\n".join(items) + "\n"


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")


def _as_tool_calls(obj) -> list[dict] | None:
    """把解析出的 JSON 转成 OpenAI tool_calls；不像工具调用就返回 None。"""
    raw = obj if isinstance(obj, list) else [obj]
    if not raw:
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        name = item.get("tool") or item.get("name") or item.get("function")
        if not isinstance(name, str) or not name:
            return None
        args = item.get("arguments")
        if args is None:
            args = item.get("parameters") or item.get("args") or {}
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        out.append({"id": "call_" + uuid.uuid4().hex[:20], "type": "function",
                    "function": {"name": name, "arguments": args}})
    return out or None


def parse_tool_calls(text: str) -> tuple[list[dict] | None, str]:
    """从模型输出里抽出工具调用。

    返回 (tool_calls, 剩余文本)；抽不到就返回 (None, 原文)。
    支持多种形态：
    1) ```json ... ``` 代码块；
    2) 裸 JSON（或带有前导 json/JSON 关键字）；
    3) 文本中内嵌的完整 JSON 对象或数组。
    """
    if not text:
        return None, text
    for m in reversed(list(_FENCE_RE.finditer(text))):
        try:
            calls = _as_tool_calls(json.loads(m.group(1).strip()))
        except Exception:
            continue
        if calls:
            return calls, (text[:m.start()] + text[m.end():]).strip()

    stripped = text.strip()
    clean_stripped = re.sub(r"^(?:```)?(?:json|JSON)?\s*", "", stripped).rstrip("`").strip()
    if clean_stripped.startswith(("{", "[")):
        try:
            calls = _as_tool_calls(json.loads(clean_stripped))
            if calls:
                return calls, ""
        except Exception:
            pass

    m_json = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if m_json:
        try:
            calls = _as_tool_calls(json.loads(m_json.group(1).strip()))
            if calls:
                rest = (text[:m_json.start()] + text[m_json.end():]).strip()
                if re.sub(r"^(?:```)?(?:json|JSON)?\s*", "", rest).strip("`").strip() == "":
                    rest = ""
                return calls, rest
        except Exception:
            pass

    return None, text


_SCRIPT_BACKUP_STORE: dict[str, str] = {}  # prompt_hash -> full original script


def _scripts_dir() -> str:
    return os.path.join(CFG.data_dir, "scripts")


def _script_path(key: str) -> str:
    return os.path.join(_scripts_dir(), f"{key}.json")


def _load_script_backups():
    """启动时从磁盘加载已有剧本备份到内存索引。"""
    d = _scripts_dir()
    if not os.path.isdir(d):
        return
    for fname in os.listdir(d):
        if not fname.endswith(".json"):
            continue
        key = fname[:-5]
        if key in _SCRIPT_BACKUP_STORE:
            continue
        try:
            with open(os.path.join(d, fname), encoding="utf-8") as f:
                obj = json.load(f)
            _SCRIPT_BACKUP_STORE[key] = obj.get("script", "")
        except Exception:
            pass


def _save_script_backup(prompt: str) -> str:
    """保存完整剧本原文到内存 + 磁盘，返回 hash key 供后续索引。"""
    key = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    _SCRIPT_BACKUP_STORE[key] = prompt
    try:
        os.makedirs(_scripts_dir(), exist_ok=True)
        tmp = _script_path(key) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "key": key,
                "length": len(prompt),
                "saved_at": int(time.time()),
                "script": prompt,
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _script_path(key))
    except Exception as exc:
        log.warning("剧本备份写盘失败（不影响生成）: %s", exc)
    return key


def _condense_video_script(prompt: str, dur: int) -> str:
    """把多镜头剧本类提示词压缩成视频模型可执行的单段视觉指令。

    - 自动检测剧本格式（含【镜头X】标题 或 超过 800 字符多行文本）
    - 按目标时长决定取几个场景：
        ≤10s  → 只取镜头一（单场景精华）
        ≤30s  → 取镜头一 + 镜头二，拼成连贯短片描述
        ≤60s  → 取前三个镜头
        >60s  → 取前五个镜头
    - 完整剧本原文自动备份到磁盘 data/scripts/（持久化，重启后保留）
    - 最终执行指令控制在 700 字以内
    """
    has_scene_markers = bool(re.search(
        r'【镜头[一二三四五六七八九十\d]|【0:\d|【场景|【第[一二三四五六七八九十\d]', prompt))
    is_long_script = len(prompt) > 800 and ('\n' in prompt)
    if not (has_scene_markers or is_long_script):
        return prompt

    # 保存原始剧本备份
    _save_script_backup(prompt)

    # 30 秒及以上：直接返回原始剧本（不压缩），让模型有足够内容填满时长。
    # 实测：压缩版提示词内容太少，模型只输出最短的 10 秒；完整剧本才能驱动 30s+。
    if dur >= 30:
        _save_script_backup(prompt)
        return prompt

    # 10 秒以下：只取第一个场景精华
    if dur <= 10:
        max_scenes = 1
    else:
        max_scenes = 2

    lines = prompt.split('\n')

    char_lines: list[str] = []
    neg_lines: list[str] = []
    photo_lines: list[str] = []
    light_lines: list[str] = []
    scenes: list[list[str]] = []   # 每个元素是一个场景的行列表
    cur_scene: list[str] = []

    in_neg = in_photo = in_light = in_scene = False

    scene_pattern = re.compile(r'【镜头[一二三四五六七八九十\d]|【0:\d|【第[一二三四五六七八九十]')
    neg_pattern   = re.compile(r'【.*?(负面提示|全片人物负面|禁止)')
    photo_pattern = re.compile(r'【.*?(统一摄影|摄影规则|镜头规则)')
    light_pattern = re.compile(r'【.*?(统一光线|光线规则)')
    section_end   = re.compile(r'【')  # 任何新的【开头标题都意味着当前块结束

    for line in lines:
        s = line.strip()
        if not s or s == '⸻' or s.startswith('=='):
            continue

        if neg_pattern.search(s):
            if in_scene and cur_scene:
                scenes.append(cur_scene); cur_scene = []
            in_neg = True; in_photo = in_light = in_scene = False
            continue
        if photo_pattern.search(s):
            if in_scene and cur_scene:
                scenes.append(cur_scene); cur_scene = []
            in_photo = True; in_neg = in_light = in_scene = False
            continue
        if light_pattern.search(s):
            if in_scene and cur_scene:
                scenes.append(cur_scene); cur_scene = []
            in_light = True; in_neg = in_photo = in_scene = False
            continue
        if scene_pattern.search(s):
            if in_scene and cur_scene:
                scenes.append(cur_scene); cur_scene = []
            in_scene = True; in_neg = in_photo = in_light = False
            continue
        # 其他【标题（结构段）：结束当前收集
        if s.startswith('【') and section_end.match(s):
            if in_scene and cur_scene:
                scenes.append(cur_scene); cur_scene = []
            in_neg = in_photo = in_light = in_scene = False
            continue

        if in_neg:
            if len(s) > 1:
                neg_lines.append(s.lstrip('不要').rstrip('。').strip())
            continue
        if in_photo:
            if len(s) > 1:
                photo_lines.append(s)
            continue
        if in_light:
            if len(s) > 1:
                light_lines.append(s)
            continue
        if in_scene:
            if len(s) > 1:
                cur_scene.append(s)
            continue

        # 人物描述（剧本最前面、还没进任何块之前的段落）
        if not any([in_neg, in_photo, in_light, in_scene]) and len(scenes) == 0:
            if len(s) > 2 and not s.startswith('【') and not s.startswith('#'):
                if len(char_lines) < 20:
                    char_lines.append(s)

    if in_scene and cur_scene:
        scenes.append(cur_scene)

    # 组装执行指令
    parts_out: list[str] = []

    # 人物核心视觉
    char_desc = '，'.join(char_lines[:12])
    if char_desc:
        parts_out.append(f"人物：{char_desc}")

    # 选取指定数量的场景
    selected = scenes[:max_scenes]
    for i, sc in enumerate(selected, 1):
        sc_text = '，'.join(sc[:10])
        label = f"镜头{i}" if max_scenes > 1 else "场景"
        parts_out.append(f"{label}：{sc_text}")

    # 摄影规则摘要（最多 5 条）
    if photo_lines:
        parts_out.append(f"摄影：{'，'.join(photo_lines[:5])}")

    # 光线规则摘要（最多 3 条）
    if light_lines:
        parts_out.append(f"光线：{'，'.join(light_lines[:3])}")

    # 负面提示（核心禁用词）
    neg_key = ['老人脸', '衰老', '松弛', '肥胖', '驼背', '秃顶', '夸张肌肉', '网红脸', '磨皮']
    neg_filtered = [n for n in neg_lines if any(k in n for k in neg_key)]
    if neg_filtered:
        parts_out.append(f"禁止：{'，'.join(neg_filtered[:6])}")

    result = '。'.join(parts_out)
    if len(result) > 700:
        result = result[:700]

    return result if result.strip() else prompt[:600]


def build_video_prompt(r: VideoRequest, executable_prompt: str | None = None) -> str:
    """构造下发给 muse.ai 的视频提示词。

    ``executable_prompt`` 非 None 时**跳过** ``_condense_video_script``。
    长视频分段由 ``longvideo`` 负责切分和角色基底注入，段体已经是可执行的
    单段指令；再走一遍压缩会把切好的段体二次裁剪，也会给每段都写一份
    剧本备份（10 段 = 10 次落盘）。
    """
    user_prompt = r.prompt.strip()
    ar = (r.aspect_ratio or "").strip().lower()
    sz = (r.size or "").strip().lower()
    dur = r.duration or 6
    has_ref = bool(r.reference_image or r.image or r.image_url)

    is_vertical = any(k in ar or k in sz for k in ("9:16", "9/16", "portrait", "竖屏", "720x1280", "1080x1920"))

    # 剧本类超长提示词自动压缩为模型可执行的单段视觉指令
    if executable_prompt is None:
        executable_prompt = _condense_video_script(user_prompt, dur)

    parts = []
    if is_vertical:
        if has_ref:
            parts.append(f"基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）：生成一个严格9:16竖屏手机满屏的动态图生视频（9:16 vertical portrait video，720x1280，高大于宽的手机全屏竖版画面，严格以附带的参考图为起始第一帧延续动作，严禁生成横屏或黑边，时长严格为 {dur} 秒）：{executable_prompt}")
        else:
            parts.append(f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）：生成一个严格9:16竖屏手机满屏视频（9:16 vertical portrait video，720x1280，高大于宽的手机全屏竖版画面，严禁生成横屏或带有左右黑边，保持垂直构图，时长严格为 {dur} 秒）：{executable_prompt}")
    elif any(k in ar or k in sz for k in ("16:9", "16/9", "landscape", "横屏", "1280x720", "1920x1080")):
        if has_ref:
            parts.append(f"基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）：生成一个16:9宽屏横屏图生视频（16:9 widescreen landscape video，严格以附带的参考图为起始第一帧延续动作，时长严格为 {dur} 秒）：{executable_prompt}")
        else:
            parts.append(f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）：生成一个16:9横屏宽屏视频（16:9 widescreen landscape video，时长严格为 {dur} 秒）：{executable_prompt}")
    else:
        if has_ref:
            parts.append(f"基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）：生成动态图生视频（严格以附带的参考图为起始第一帧延续动作，时长严格为 {dur} 秒）：{executable_prompt}")
        else:
            parts.append(f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）：生成一个视频（时长严格为 {dur} 秒）：{executable_prompt}")

    if r.resolution:
        parts.append(f"画质规格：{r.resolution}")
    if r.extra:
        parts.append(r.extra)
    return "，".join(parts)


def media_url(name: str) -> str:
    """媒体地址。

    配了 public_base 就返回**绝对 URL** —— OpenAI 兼容客户端（以及各类智能体
    平台）拿到 data[].url 后一般会直接渲染或下载，相对路径会被解析到客户端
    自己的域名上，导致 404。public_base 为空时退回相对路径。

    本地模式下附带 `?key=`：媒体接口是要鉴权的，而浏览器 `<video src>` /
    `<img src>` 无法附加 Authorization 头，只能走查询参数。不带的话前端一播放
    就是 401。服务端部署（local_mode 关闭）时不带 key，由客户端自己用 Bearer。
    """
    base = _public_base()
    url = f"{base}/v1/media/{name}" if base else f"/v1/media/{name}"
    if CFG.local_mode and CFG.api_key:
        url += f"?key={CFG.api_key}"
    return url


# ------------------------- cookie 解析 -------------------------
def parse_cookie_text(text: str) -> dict[str, str]:
    """把 `a=1; b=2` / JSON / Set-Cookie 行解析成 dict。"""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        import json
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return {str(k): str(v) for k, v in obj.items()}
        except Exception:  # noqa: BLE001
            pass
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def parse_batch(text: str) -> list[tuple[str, dict]]:
    """批量导入：每行 `标签 | cookie串`，标签可省略。"""
    out: list[tuple[str, dict]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = ""
        body = line
        if "|" in line:
            head, tail = line.split("|", 1)
            if "=" not in head:          # `|` 出现在 cookie 里时不算分隔符
                label, body = head.strip(), tail.strip()
        cookies = parse_cookie_text(body)
        if cookies:
            out.append((label, cookies))
    return out


# ------------------------- 核心生成 -------------------------
def _sync_cookies(acc_id: str) -> dict:
    """生成完从浏览器读回 cookie 并写回账号池。

    实测结论：**核心 cookie 的 expires 不会被使用行为续期**（hatch_vml 固定
    约 2 天就到期），这里同步回来的主要是那些每次访问都会重新下发的非核心
    cookie（_fbp / wd / dpr 等）以及可能新增的条目，避免账号信息比实际更旧。

    注意：这个函数**绝不能把生成结果搞失败** —— 生成已经成功了，
    同步只是锦上添花，出任何问题都只记一条日志。
    """
    try:
        live = engine.read_cookies()
        if not live:
            return {"synced": 0}
        acc = store.get_account(acc_id)
        if not acc:
            return {"synced": 0}
        cur = dict(acc.get("cookies") or {})
        new_vals = {k: v["value"] for k, v in live.items() if v.get("value")}
        # 有效期要「合并」而不是「覆盖」：会话 cookie 读回的 expires 是 -1，
        # 若整体覆盖会把原有有效期记录清空（实测踩过这个坑）。
        exps = dict(acc.get("cookies_exp") or {})
        exps.update({k: v["expires"] for k, v in live.items()
                     if _pos(v.get("expires"))})
        changed = sum(1 for k, v in new_vals.items() if cur.get(k) != v)
        store.update_account(acc_id, cookies={**cur, **new_vals},
                             cookies_exp=exps, synced_at=int(time.time()))
        updated = store.get_account(acc_id) or {}
        return {"synced": len(new_vals), "changed": changed,
                "expires_at": updated.get("expires_at")}
    except Exception as exc:  # noqa: BLE001
        log.warning("cookie 同步失败（不影响本次生成）: %s", exc)
        return {"synced": 0, "error": str(exc)[:200]}


def _pos(v) -> bool:
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def _deadline_error(last_exc) -> MuseGenerationError:
    """超时时**带上最后一次真实错误**。

    原来这两个 deadline 守卫直接抛「任务总等待时限已到，停止重试」，把
    ``last_exc`` 丢掉了。用户看到的是误导性的原因，真实错误（实测绝大多数是
    「等待生成超时，未出现新的生成结果」）被吞光，排查时完全看不出 muse.ai
    到底干了什么 —— task_5aec863f8e1a4764b4ae 就是这么查了半天的。
    """
    if last_exc:
        return MuseGenerationError(f"任务总等待时限已到，停止重试；最后一次错误：{last_exc}")
    return MuseGenerationError("任务总等待时限已到，停止重试")


def _run_generation(prompt: str, kind: str, timeout: int,
                    account_id: str | None = None, on_progress=None,
                    reference_image: str | None = None) -> tuple[dict, str | None]:
    # 锁契约（重要）：本函数假定调用方已持有 GEN_LOCK，自身不再抢锁。
    # 正确调用方式是经由 `_sched_run(...)` / `SCHED.submit(...)` 提交成 job，
    # 由调度器 worker 在持锁状态下调用。若直接在线程里裸调，
    # 会与其它生成任务并发争抢同一个浏览器，导致串话。
    #
    # 上游原先在此处 `GEN_LOCK.acquire(timeout=...)`；因 GEN_LOCK 不可重入，
    # 调度器 worker 已持锁后再抢会永久自锁，故抢锁职责上移至调度器。
    deadline = time.monotonic() + max(1, timeout)
    return _run_generation_locked(prompt, kind, timeout, account_id,
                                  on_progress, reference_image, deadline=deadline)


def _run_generation_locked(prompt: str, kind: str, timeout: int,
                    account_id: str | None = None, on_progress=None,
                    reference_image: str | None = None, deadline=None) -> tuple[dict, str | None]:
    acc = store.get_account(account_id) if account_id else None
    if acc and not acc.get("enabled", True):
        acc = None
    if not acc:
        acc = store.pick_account(rotate=True, preferred_id=getattr(engine, "current_acc_id", None))
    if not acc:
        raise MuseAuthError("没有可用账号，请先在管理页面导入 cookie")

    last_exc = None
    cur_acc = acc
    for attempt in range(2):
        if deadline is not None and time.monotonic() >= deadline:
            raise _deadline_error(last_exc)
        if attempt > 0:
            # 原来这里第一句是 `if "未产出媒体附件，仅返回了文本回复" in str(last_exc): break`
            # —— 那个字符串全仓库已经不存在了（engine 早改成「模型未生成媒体，仅返回文本」），
            # 所以这句判断是死代码，它想表达的「纯文本回复就别试了」从来没生效过。
            #
            # 真正的问题是下面那个 break：备用账号拿不到就整个放弃。本机账号池只有
            # 1 个号，于是**任何瞬时失败都是一次就死**。而 muse.ai 的生成 agent
            # 本来就有相当比例的抖动 —— 它会改成聊天口径，编一句
            # 「成品实测：720×1280、严格 30.000000 秒」然后在页面上留下一个
            # aria-label="找不到视频" 的占位（thread 5274edb7 现场）。
            # 实测同一时刻紧接着再发一次就正常出片（task_de396808040f47d99c54，5s，71.9s）。
            # 所以：没备用号就用同一个号重试一次，别直接放弃。
            alt = store.pick_account(rotate=True, force_rotate=True, exclude_id=cur_acc["id"])
            if alt and alt["id"] != cur_acc["id"]:
                cur_acc = alt
                log.info("【生图/视频自动切号】切换到备用账号 %s (%s) 重试...", cur_acc.get("label"), cur_acc["id"])
            else:
                log.warning("【生成重试】账号池无备用账号，用当前账号 %s 重试一次（原因：%s）",
                            cur_acc.get("label"), str(last_exc)[:120])
                time.sleep(3)
        try:
            refreshed = _renew_and_persist(cur_acc["id"], wake_vm=True, force=(attempt > 0))
            if refreshed:
                cur_acc = refreshed
            engine.start()
            remaining = int(deadline - time.monotonic()) if deadline is not None else timeout
            if remaining <= 0:
                raise _deadline_error(last_exc)
            res = engine.generate(cur_acc["cookies"], prompt, expect=kind,
                                  timeout=remaining, expires=cur_acc.get("cookies_exp"),
                                  account_id=cur_acc["id"], on_progress=on_progress,
                                  reference_image=reference_image)
            store.mark(cur_acc["id"], True, "")
            _sync_cookies(cur_acc["id"])
            return res, cur_acc["id"]
        except MuseAuthError as exc:
            last_exc = exc
            store.mark(cur_acc["id"], False, str(exc))
            engine.stop()
        except MuseGenerationError as exc:
            last_exc = exc
            store.mark(cur_acc["id"], True, f"任务异常: {str(exc)[:60]}")
            try:
                engine.reset_thread()
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            engine.stop()
            last_exc = MuseGenerationError(f"生成失败: {exc}")
    raise last_exc


# ------------------------- 基础接口 -------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": int(time.time())}


@app.get("/readyz")
def readyz():
    accs = [a for a in store.list_accounts() if a.get("enabled", True)]
    return {"status": "ready" if accs else "no_account",
            "accounts": len(accs),
            "browser_running": bool(engine.proc and engine.proc.poll() is None)}


@app.get("/v1/models")
def models(_=Depends(auth)):
    return {"object": "list", "data": MODELS}


# ------------------------- 生图 -------------------------
def _image_response(req: ImageRequest, res: dict) -> dict:
    item = {"revised_prompt": req.prompt, "url": media_url(res["filename"]),
            "size": req.size or "auto", "kind": res["kind"], "bytes": res["size"]}
    if req.response_format == "b64_json":
        fpath = res.get("path") or os.path.join(CFG.media_dir, res["filename"])
        with open(fpath, "rb") as f:
            item["b64_json"] = base64.b64encode(f.read()).decode()
        item.pop("url", None)
    return {"created": int(time.time()), "data": [item]}


def _queue_image(req: ImageRequest, prompt: str, reference_image: str | None,
                 idempotency_key: str | None = None):
    """Opt-in polling avoids reverse-proxy timeouts; no generation is repeated by polling."""
    if idempotency_key and len(idempotency_key) > 256:
        raise HTTPException(400, "Idempotency-Key too long")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
    request_hash = hashlib.sha256(json.dumps(
        {"request": req.model_dump(by_alias=True), "reference": reference_image},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with IMAGE_TASK_LOCK:
        tasks = list(store.tasks.values())
        if key_hash:
            for existing in tasks:
                if existing.get("image_request_key") == key_hash:
                    if existing.get("image_request_hash") != request_hash:
                        raise HTTPException(409, "Idempotency-Key reused with different request")
                    return JSONResponse(status_code=202, content={
                        "id": existing["id"], "task_id": existing["id"],
                        "object": "image.task", "status": existing["status"],
                        "progress": existing.get("progress", 0),
                        "created_at": existing["created_at"]})
        # ponytail: one Chromium worker; cap admission instead of adding a broker.
        if sum(t.get("kind") == "image" and t.get("status") in ("queued", "processing")
               for t in tasks) >= 8:
            raise HTTPException(429, "Image queue is full; retry later")
        task = store.create_task("image", req.prompt)
        tid = task["id"]
        store.update_task(tid, api_prompt=prompt, size=req.size,
                          response_format=req.response_format, progress=0,
                          image_request_key=key_hash, image_request_hash=request_hash)

    def worker():
        # 经 FIFO 调度器排队执行（与 chat/video 共用一条队列，单 worker 持锁）。
        # 不可直接裸调 _run_generation —— 该函数假定调用方已持锁。
        # 注意 progress=5 的「已开始处理」要等真正拿到执行权后再打，
        # 否则排队期间就显示 processing 会误导用户（缺陷 9 同类问题）。
        t0 = time.time()
        try:
            def _do():
                store.update_task(tid, status="processing", progress=5)
                return _run_generation(
                    prompt, "image", req.timeout or CFG.image_timeout,
                    reference_image=reference_image,
                    on_progress=lambda p: store.update_task(tid, progress=p))

            res, acc_id = _run_generation_sched(
                _do, label="image:%s" % tid[-8:])
            # Store only media metadata, not large base64 payloads or reference credentials.
            store.update_task(tid, status="completed", progress=100, account=acc_id,
                              elapsed=round(time.time() - t0, 1),
                              url=media_url(res["filename"]),
                              result={**{k: res[k] for k in ("filename", "size", "kind")},
                                      "url": media_url(res["filename"])})
        except Exception as exc:  # noqa: BLE001
            store.update_task(tid, status="failed", error=str(exc),
                              elapsed=round(time.time() - t0, 1))

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={
        "id": tid, "task_id": tid, "object": "image.task", "status": "queued",
        "progress": 0, "created_at": task["created_at"]})


@app.post("/v1/images/tasks")
def create_image_task(req: ImageRequest,
                      idempotency_key: str | None = Header(default=None), _=Depends(auth)):
    ref_img = req.reference_image or req.image
    if isinstance(ref_img, dict):
        ref_img = ref_img.get("url") or ref_img.get("b64_json")
    if not ref_img and req.images:
        first = req.images[0]
        ref_img = first.get("image_url") or first.get("url") if isinstance(first, dict) else first
    return _queue_image(req, build_image_prompt(req), ref_img, idempotency_key)


@app.get("/v1/images/tasks/{task_id}")
def get_image_task(task_id: str, _=Depends(auth)):
    task = store.get_task(task_id)
    if not task or task.get("kind") != "image":
        raise HTTPException(404, "image task 不存在")
    out = dict(task)
    if out.get("status") == "completed":
        req = ImageRequest(prompt=out["prompt"], size=out.get("size"),
                           response_format=out.get("response_format") or "url")
        out.update(_image_response(req, out["result"]))
    return out

def _run_generation_sched(fn, label: str = "gen", timeout: float | None = None):
    """同步提交给 FIFO 调度器并阻塞等待结果。

    供**在独立线程中、无法 await** 的调用方使用（如 _queue_image 的 worker）。
    与 async 版 `_sched_run` 语义一致：进入 SCHED 排队 → 由单 worker 持
    GEN_LOCK 执行 → 返回结果。这样图片与 chat/video 共用同一条队列，
    不会绕过调度器裸抢浏览器。
    """
    return SCHED.run_sync(fn, label=label, timeout=timeout)


async def _sched_run(fn, label: str):
    """把「需要占浏览器的同步生成函数」提交给 FIFO 调度器并 await 结果。

    这样生图请求不再用裸锁抢，而是与 chat/video 一起排队（修缺陷 3 / 10）。
    并行请求会按提交顺序执行，且不会出现线程饥饿。
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def wrapped():
        if fut.cancelled():
            return
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(_safe_set_exc, fut, exc)
        else:
            loop.call_soon_threadsafe(_safe_set_result, fut, result)

    SCHED.submit(wrapped, label=label)
    return await fut


def _safe_set_result(fut: asyncio.Future, value):
    if not fut.done():
        fut.set_result(value)


def _safe_set_exc(fut: asyncio.Future, exc: BaseException):
    if not fut.done():
        fut.set_exception(exc)


@app.post("/v1/images/generations")
async def images_generations(req: ImageRequest, _=Depends(auth)):
    ref_img = req.reference_image or req.image
    if isinstance(ref_img, dict):
        ref_img = ref_img.get("url") or ref_img.get("b64_json")
    if not ref_img and req.images and isinstance(req.images, list):
        first = req.images[0]
        ref_img = first.get("image_url") or first.get("url") if isinstance(first, dict) else first

    validate_size(req.size, req.aspect_ratio)     # 缺陷 2
    ref_img = validate_reference_image(ref_img)   # 缺陷 8

    prompt = build_image_prompt(req)
    timeout = req.timeout or CFG.image_timeout
    if req.async_:
        return _queue_image(req, prompt, ref_img)
    try:
        res, _acc = await _sched_run(
            lambda: _run_generation(prompt, "image", timeout, reference_image=ref_img),
            label="image")
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _image_response(req, res)


@app.post("/v1/images/edits")
async def images_edits(request: Request, _=Depends(auth)):
    """OpenAI 兼容的图生图/图像编辑接口，兼容 multipart/form-data 与 application/json。"""
    content_type = request.headers.get("content-type", "").lower()
    prompt = ""
    model = "muse-image"
    size = None
    aspect_ratio = None
    response_format = "url"
    timeout = None
    ref_image_data = None
    async_mode = False

    if "multipart/form-data" in content_type:
        form = await request.form()
        async_mode = form.get("async", False)
        prompt = form.get("prompt") or ""
        model = form.get("model") or "muse-image"
        size = form.get("size")
        aspect_ratio = form.get("aspect_ratio")
        response_format = form.get("response_format") or "url"
        timeout_val = form.get("timeout")
        if timeout_val:
            try:
                timeout = int(timeout_val)
            except ValueError:
                pass
        img_field = form.get("image")
        if img_field and hasattr(img_field, "read"):
            content = await img_field.read()
            ref_mime = getattr(img_field, "content_type", "image/png") or "image/png"
            ref_image_data = f"data:{ref_mime};base64,{base64.b64encode(content).decode('ascii')}"
        elif isinstance(img_field, str):
            ref_image_data = img_field
    else:
        body = await request.json()
        async_mode = body.get("async", False)
        prompt = body.get("prompt") or ""
        model = body.get("model") or "muse-image"
        size = body.get("size")
        aspect_ratio = body.get("aspect_ratio")
        response_format = body.get("response_format") or "url"
        timeout = body.get("timeout")
        img_val = body.get("image")
        if isinstance(img_val, dict):
            ref_image_data = img_val.get("url") or img_val.get("b64_json")
        elif isinstance(img_val, str):
            ref_image_data = img_val
        if not ref_image_data and body.get("images") and isinstance(body.get("images"), list):
            first = body["images"][0]
            if isinstance(first, dict):
                ref_image_data = first.get("image_url") or first.get("url")
            elif isinstance(first, str):
                ref_image_data = first
        if not ref_image_data:
            ref_image_data = body.get("reference_image") or body.get("image_url")

    if not prompt:
        prompt = "参考此图片并进行生图创作"

    req_obj = ImageRequest(
        prompt=prompt,
        model=model,
        size=size,
        aspect_ratio=aspect_ratio,
        response_format=response_format,
        timeout=timeout,
        reference_image=ref_image_data,
        **{"async": async_mode}
    )
    full_prompt = build_image_prompt(req_obj)
    gen_timeout = timeout or CFG.image_timeout
    if req_obj.async_:
        return _queue_image(req_obj, full_prompt, ref_image_data)
    try:
        res, _acc = await _sched_run(
            lambda: _run_generation(full_prompt, "image", gen_timeout,
                                    reference_image=ref_image_data),
            label="image")
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc

    return _image_response(req_obj, res)


# ------------------------- 生视频（异步任务） -------------------------

# 进度分配：生成占 0~95，最后 5% 留给 ffmpeg 合成。
_SEG_RENDER_TOP = 95


def _seg_overall(done: int, total: int, seg_pct: float = 0.0,
                 top: int = _SEG_RENDER_TOP) -> int:
    """把「第 done/total 段 + 段内 seg_pct%」映射成整体进度。

    进度必须**单调不减**：前端轮询时看到 95% 之后又跳回 30% 会直接崩掉用户
    对系统的信任（缺陷 9 的教训 —— 宁可慢，不可倒退）。
    """
    if total <= 0:
        return 0
    span = max(1.0, float(top))
    pct = (done + max(0.0, min(100.0, seg_pct)) / 100.0) * span / total
    return max(0, min(int(top), int(round(pct))))


def _is_quota_fault(exc: Exception) -> bool:
    """判断是不是「额度用完」类错误 —— 这类重试没有意义，必须立刻停。"""
    msg = str(exc)
    return bool(re.search(
        r"额度|配额|quota|limit reached|weekly limit|"
        r"reset|过期|expired|没有可用账号",
        msg, re.I))


def _run_segment_with_retry(prompt: str, prog_cb, ref_img, seg, n: int,
                            task_id: str, deadline: float, notes: list):
    """跑一段视频，失败后按 config 重试；全部失败才抛出异常。

    为什么要这一层：``_run_generation_locked`` 自带 2 次尝试，但那一层一失败
    整段就作废，而长视频任一段挂掉整个任务就前功尽弃。10 段的任务对
    「每段成功率」是指数关系，多一次重试的收益远大于多花的几分钟。
    """
    tries = max(1, int(getattr(CFG, "long_video_segment_retries", 1)) + 1)
    last_exc = None

    for k in range(tries):
        if k:
            # 刚失败就立刻重发容易撞上同一个坏状态，隔几秒再试。
            time.sleep(min(20, 5 * k))
            log.warning("【长视频 %s】第 %d/%d 段第 %d 次重试（原因：%s）",
                        task_id[-8:], seg.index, n, k, str(last_exc)[:160])
            # 重试期间进度回退到本段起点，避免前端一直显示上一段的百分比。
            store.update_task(task_id, segment_index=seg.index,
                              segment_done=seg.index - 1, stage="rendering")

        try:
            res, acc_id = SCHED.run_sync(
                lambda p=prompt: _run_generation(
                    p, "video", CFG.video_timeout, on_progress=prog_cb,
                    reference_image=ref_img),
                label=f"video:{task_id[-8:]}:seg{seg.index}:try{k + 1}",
                queue_timeout=CFG.seg_queue_timeout)
            if k:
                note = f"第 {seg.index} 段重试 {k} 次后才成功"
                if note not in notes:
                    notes.append(note)
            return res, acc_id
        except Exception as exc:  # noqa: BLE001 —— 交给上层统一记失败
            last_exc = exc
            if _is_quota_fault(exc):
                log.error("【长视频 %s】第 %d 段遇到额度/账号故障，不再重试：%s",
                          task_id[-8:], seg.index, str(exc)[:160])
                break
            # 预算快用完了就别再试，直接如实报错。
            if k + 1 < tries and time.time() > deadline - 30:
                log.error("【长视频 %s】第 %d 段失败且预算将尽，放弃重试：%s",
                          task_id[-8:], seg.index, str(exc)[:160])
                break

    raise last_exc


def _drive_long_video(task_id: str, plan, req: VideoRequest,
                      ref_img: str | None, budget: int) -> None:
    """长视频驱动：逐段提交生成，段间释放浏览器，最后尝试合成。

    **必须是普通线程，不能是 SCHED job，也不能写成 await 链**：
    * 作为 SCHED job 提交的话，整个生成期间会一直持有 GEN_LOCK —— 单段实测
      371 秒，10 段就是 33~62 分钟，期间 chat / image / 短 video 全部被堵死。
      这里改成普通线程，**逐段** `SCHED.run_sync`，段与段之间浏览器让给别人。
    * 写成 await 链的话，`run_sync` 的阻塞等待会卡死 FastAPI 事件循环。

    失败策略（用户已确认）：任一段失败即中断整个任务，**保留已完成分段**，
    不合成残缺版 —— 合成残缺片会让客户端拿到"看起来完整"的短片，
    静默丢剧情比直接报错更难排查。
    """
    n = len(plan.segments)
    t0 = time.time()
    deadline = t0 + max(60, int(budget))
    done: list[dict] = []
    acc_id = None
    notes = list(plan.notes)

    try:
        for seg in plan.segments:
            if time.time() > deadline:
                raise MuseGenerationError(
                    f"长视频总预算 {int(budget)}s 已用尽，停在第 {seg.index}/{n} 段"
                    f"（已完成 {len(done)} 段，剩余 {n - len(done)} 段未生成）")

            body = build_segment_prompt(plan, seg)
            # 每段按内容时长向上取档（5/10/30），请求里不能还写 240
            seg_req = req.model_copy(update={"duration": seg.request_duration,
                                             "timeout": CFG.video_timeout})
            prompt = build_video_prompt(seg_req, executable_prompt=body)
            log.info("【长视频 %s】第 %d/%d 段，%.0fs（请求 %ds，%d 字）",
                     task_id[-8:], seg.index, n, seg.seconds,
                     seg.request_duration, len(prompt))

            store.update_task(task_id, status=ST_RUNNING, stage="rendering",
                              segment_index=seg.index, segment_total=n,
                              segment_done=len(done),
                              progress=_seg_overall(len(done), n))

            seg_t0 = time.time()

            def prog_cb(p, _i=seg.index, _d=len(done)):
                store.update_task(task_id, progress=_seg_overall(_d, n, p),
                                  segment_index=_i, stage="rendering")

            res, acc_id = _run_segment_with_retry(
                prompt, prog_cb, ref_img, seg, n, task_id, deadline, notes)

            entry = {"index": seg.index, "t_start": seg.t_start, "t_end": seg.t_end,
                     "seconds": seg.seconds, "requested_duration": seg.request_duration,
                     "url": media_url(res["filename"]), "filename": res["filename"],
                     "path": res.get("path") or os.path.join(CFG.media_dir, res["filename"]),
                     "bytes": res["size"], "kind": res["kind"],
                     "elapsed": round(time.time() - seg_t0, 1)}
            done.append(entry)
            store.update_task(task_id, segments=done, segment_done=len(done),
                              progress=_seg_overall(len(done), n), account=acc_id)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if done:
            msg = f"{msg}（已完成 {len(done)}/{n} 段，分段文件已保留在 segments 里）"
        store.update_task(task_id, status=ST_FAILED, stage="failed", error=msg,
                          segments=done, segment_done=len(done),
                          elapsed=round(time.time() - t0, 1), notes=notes)
        log.error("【长视频 %s】失败于第 %d/%d 段（已完成 %d 段）: %s",
                  task_id[-8:], len(done) + 1, n, len(done), exc)
        return

    # ---- 全部段落完成，尝试合成 ----
    store.update_task(task_id, stage="merging", progress=96, segments=done,
                      segment_done=len(done))
    if not CFG.video_merge:
        merge = {"ok": False, "reason": "已通过 MUSE2API_VIDEO_MERGE=0 关闭合成"}
    elif len(done) < 2:
        merge = {"ok": False, "reason": "只有 1 段，无需合成"}
    else:
        merge = merge_segments(
            [e["path"] for e in done],
            out_path=os.path.join(CFG.media_dir, "merged_%s.mp4" % task_id),
            ffmpeg=CFG.ffmpeg, ffprobe=CFG.ffprobe,
            # 按剧本分配的秒数逐段裁回：muse.ai 只有 5/10/30 秒原生档位，
            # 不裁的话成片会从 240 秒膨胀到 300 秒，节奏全乱。
            durations=[e["seconds"] for e in done])

    elapsed = round(time.time() - t0, 1)
    if merge.get("ok"):
        name = os.path.basename(merge["path"])
        vurl = media_url(name)
        store.update_task(task_id, status=ST_DONE, progress=100, stage="done",
                          account=acc_id, elapsed=elapsed, url=vurl,
                          video={"url": vurl}, segments=done,
                          segment_done=len(done), notes=notes,
                          result={"url": vurl, "filename": name,
                                  "bytes": os.path.getsize(merge["path"]),
                                  "kind": "video",
                                  "merge": {"ok": True, "mode": merge.get("mode"),
                                            "trimmed": merge.get("trimmed"),
                                            "seconds": merge.get("seconds")},
                                  "segments": done, "notes": notes})
    else:
        # 降级：段落都生成成功了，**不算失败**。只把合成原因写清楚，
        # 让前端渲染分段列表而不是显示"生成中 95%"。
        log.warning("【长视频 %s】合成未完成，返回分段列表: %s", task_id[-8:], merge.get("reason"))
        store.update_task(task_id, status=ST_DONE, progress=100, stage="done",
                          account=acc_id, elapsed=elapsed, segments=done,
                          segment_done=len(done), notes=notes,
                          result={"url": None, "kind": "video",
                                  "merge": {"ok": False, "reason": merge.get("reason")},
                                  "segments": done, "notes": notes})


@app.post("/v1/videos")
@app.post("/v1/videos/generations")
async def create_video(req: VideoRequest, _=Depends(auth)):
    # ---- 入口校验（修缺陷 1 / 2 / 8）----
    duration = validate_video_duration(req.duration)   # 缺陷 1：时长白名单
    validate_size(req.size, req.aspect_ratio)           # 缺陷 2：尺寸/比例校验
    ref_img = None
    if req.reference_image:
        ref_img = req.reference_image
    elif req.image_url:
        ref_img = req.image_url if isinstance(req.image_url, str) else (req.image_url.get("url") if isinstance(req.image_url, dict) else None)
    elif req.image:
        ref_img = req.image.get("url") if isinstance(req.image, dict) else req.image

    ref_img = validate_reference_image(ref_img)   # 缺陷 8：非法参考图入口即拒

    task = store.create_task("video", req.prompt)

    # ---- 长视频分支：>30s 自动拆段 ----
    if duration > MUSE_MAX_SINGLE_SECONDS:
        plan = plan_segments(req.prompt, duration,
                             cap=CFG.video_seg_cap or MUSE_MAX_SINGLE_SECONDS,
                             max_segments=CFG.video_max_segments)
        if not plan.segments:
            store.update_task(task["id"], status=ST_FAILED, stage="failed",
                              error="长视频分段失败：" + "；".join(plan.notes))
            raise HTTPException(400, "长视频分段失败：" + "；".join(plan.notes))
        # 整份长剧本只备份一次（分段各自再走一遍 _condense 会写 N 份）
        _save_script_backup(req.prompt)
        store.update_task(task["id"], status=ST_QUEUED, progress=0, stage="queued",
                          api_prompt="[长视频 %d 秒 → %d 段] %s" % (
                              duration, len(plan.segments),
                              build_segment_prompt(plan, plan.segments[0])[:200]),
                          segment_total=len(plan.segments), segment_index=0,
                          segment_done=0, segments=[], notes=plan.notes,
                          duration=duration)
        threading.Thread(target=_drive_long_video,
                         args=(task["id"], plan, req, ref_img, CFG.long_video_budget),
                         name="longvideo-%s" % task["id"][-8:], daemon=True).start()
        return {"id": task["id"], "task_id": task["id"], "object": "video.task",
                "status": ST_QUEUED, "progress": 0, "duration": duration,
                "segment_total": len(plan.segments), "notes": plan.notes,
                "queue_size": SCHED.queue_size,
                "created_at": task["created_at"]}

    # ---- 常规分支：<=30s，一次出一条 ----
    prompt = build_video_prompt(req)
    timeout = req.timeout or CFG.video_timeout
    store.update_task(task["id"], api_prompt=prompt,
                      status=ST_QUEUED, progress=0, stage="queued")

    def job():
        """由调度器 worker 在持锁状态下执行。"""
        store.update_task(task["id"], status=ST_RUNNING, progress=10,
                          stage="rendering")
        t0 = time.time()
        try:
            def prog_cb(p):
                store.update_task(task["id"], progress=p, stage="rendering")
            res, acc_id = _run_generation(prompt, "video", timeout,
                                          on_progress=prog_cb, reference_image=ref_img)
            vurl = media_url(res["filename"])
            store.update_task(task["id"], status=ST_DONE, progress=100,
                              stage="done",
                              account=acc_id,
                              elapsed=round(time.time() - t0, 1),
                              url=vurl,
                              video={"url": vurl},
                              result={"url": vurl,
                                      "filename": res["filename"],
                                      "bytes": res["size"], "kind": res["kind"]})
        except Exception as exc:  # noqa: BLE001
            store.update_task(task["id"], status=ST_FAILED, stage="failed",
                              elapsed=round(time.time() - t0, 1), error=str(exc))

    try:
        SCHED.submit(job, label=f"video:{task['id'][-8:]}")
    except Exception as exc:  # noqa: BLE001  # 队列满
        store.update_task(task["id"], status=ST_FAILED, stage="rejected",
                          error=f"服务繁忙，生成队列已满: {exc}")
        raise HTTPException(503, "生成队列已满，请稍后重试") from exc

    return {"id": task["id"], "task_id": task["id"], "object": "video.task",
            "status": ST_QUEUED, "progress": 0,
            "queue_size": SCHED.queue_size,
            "created_at": task["created_at"]}


@app.get("/v1/videos/{task_id}")
@app.get("/v1/videos/generations/{task_id}")
def get_video(task_id: str, _=Depends(auth)):
    t = store.get_task(task_id)
    if not t:
        raise HTTPException(404, "task 不存在")
    out = dict(t)
    status = out.get("status")
    if status in ("succeeded", "success", "done"):
        out["status"] = ST_DONE
    out.setdefault("stage", out.get("status"))

    if out.get("status") == ST_DONE:
        out["progress"] = 100
    elif out.get("status") == ST_RUNNING:
        # 注意：**不再伪造进度**。旧实现在这里按 elapsed 编造
        # `min(92, 20 + elapsed*1.1)`，导致一个早就卡死的僵尸任务
        # 对外永远显示 92%「快要好了」，客户端被无限期欺骗（缺陷 9）。
        # 现在只回传 worker 真实写入的 progress；若长时间无推进，
        # 明确标记 stalled，让调用方能识别并放弃。
        last = out.get("updated_at") or out.get("created_at") or time.time()
        idle = time.time() - last
        out["idle_seconds"] = round(idle, 1)
        # 长视频的单段实测就要 371 秒，120 秒阈值会把每一段都误判成卡死。
        # 合成阶段（stage=merging）纯 CPU，也要给足时间。
        seg_total = out.get("segment_total")
        if out.get("stage") == "merging":
            stall_limit = 900
        elif seg_total:
            stall_limit = max(120, CFG.seg_queue_timeout + 180)
        else:
            stall_limit = 120
        out["stall_limit_seconds"] = stall_limit
        if idle > stall_limit:
            out["status"] = "stalled"
            out["stage"] = "stalled"
            if seg_total:
                out["error"] = (f"第 {out.get('segment_index')} 段已 {int(idle)}s "
                                f"无任何进展（共 {seg_total} 段），疑似浏览器会话卡死")
            else:
                out["error"] = f"任务已 {int(idle)}s 无任何进展，疑似浏览器会话卡死"
    # 分段信息平铺出去，前端不必去翻 result
    if out.get("segment_total"):
        out.setdefault("segment_done", len(out.get("segments") or []))
    vurl = out.get("url")
    if not vurl and isinstance(out.get("result"), dict):
        vurl = out["result"].get("url")
    if vurl:
        out["url"] = vurl
        if "video" not in out:
            out["video"] = {"url": vurl}
    return out


# ------------------------- 对话（OpenAI 兼容） -------------------------
def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _chat_chunk(cid: str, created: int, model: str, delta: dict,
                finish: str | None = None) -> dict:
    return {"id": cid, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _want_usage(stream_options) -> bool:
    """下游（LangChain、部分 SDK / 智能体框架）会带
    `stream_options.include_usage=true`，要求在 [DONE] 之前补一个
    `choices: []` + `usage` 的分片。不发的话少数框架会一直等 usage 而卡住。"""
    if isinstance(stream_options, dict):
        return bool(stream_options.get("include_usage"))
    return False


_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, _=Depends(auth)):
    """OpenAI 兼容的对话接口 —— 各类智能体客户端都能接。

    说明：muse.ai 网页是**自动路由**的 agent，界面上没有模型选择器，所有请求
    都会落到同一个网页助手（自称 Koda，底层是 Muse 系列语言模型）。所以
    `model` 字段只用于兼容下游，不影响路由结果。

    请求带 `tools` 时，会注入【工具调用协议】并把模型输出的 JSON 解析回
    `tool_calls`（muse.ai 没有原生 function calling，这层属于协议适配）。

    对话与生图/生视频共用同一个浏览器实例，靠 `GEN_LOCK` 串行。
    """
    prompt = build_chat_prompt(req.messages) or (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "messages 为空")

    # 工具调用协议默认不注入 —— 实测 muse.ai 的助手会明确拒绝输出"伪工具调用"，
    # 注入反而污染正常回答；详见 config.py 的 tool_protocol 注释。
    tool_note = build_tools_prompt(req.tools) if CFG.tool_protocol else ""
    if tool_note:
        # 放在末尾：开头是 agent 自己的 system 提示，夹在中间容易被忽略；
        # 紧贴用户消息之前，模型对末尾指令的遵守度明显更高。
        prompt = prompt + "\n\n" + tool_note

    model = resolve_model(req.model, default="muse-spark")
    timeout = int(req.timeout or CFG.chat_timeout)
    acc = store.pick_account(rotate=True, preferred_id=getattr(engine, "current_acc_id", None))
    if not acc:
        raise HTTPException(400, "没有可用账号，请先在管理页面导入 cookie")

    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    acc_id = acc["id"]
    cookies = acc["cookies"]
    expires = acc.get("cookies_exp")

    def tool_call_deltas(calls: list[dict]) -> list[list[dict]]:
        """按 OpenAI 习惯分两片发：先 id/name，再 arguments 全文。"""
        head = [{"index": i, "id": c["id"], "type": "function",
                 "function": {"name": c["function"]["name"], "arguments": ""}}
                for i, c in enumerate(calls)]
        body = [{"index": i, "function": {"arguments": c["function"]["arguments"]}}
                for i, c in enumerate(calls)]
        return [head, body]

    if req.stream:
        def sync_stream():
            try:
                stream_gen = safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id)
                if not tool_note:
                    for chunk in stream_gen:
                        yield _sse(_chat_chunk(cid, created, model, {"content": chunk}))
                    yield _sse(_chat_chunk(cid, created, model, {}, finish="stop"))
                else:
                    buf, mode = "", None
                    for chunk in stream_gen:
                        if mode == "text":
                            yield _sse(_chat_chunk(cid, created, model, {"content": chunk}))
                            continue
                        buf += chunk
                        if mode is None:
                            head = buf.lstrip()
                            if len(head) >= 2:
                                if head[0] in "{[" or head.startswith("```"):
                                    mode = "maybe_tool"
                                else:
                                    mode = "text"
                                    yield _sse(_chat_chunk(cid, created, model, {"content": buf}))
                                    buf = ""
                    if mode == "text":
                        yield _sse(_chat_chunk(cid, created, model, {}, finish="stop"))
                    else:
                        calls, rest = parse_tool_calls(buf)
                        if calls:
                            if rest:
                                yield _sse(_chat_chunk(cid, created, model, {"content": rest}))
                            for piece in tool_call_deltas(calls):
                                yield _sse(_chat_chunk(cid, created, model, {"tool_calls": piece}))
                            yield _sse(_chat_chunk(cid, created, model, {}, finish="tool_calls"))
                        else:
                            if buf:
                                yield _sse(_chat_chunk(cid, created, model, {"content": buf}))
                            yield _sse(_chat_chunk(cid, created, model, {}, finish="stop"))
                if _want_usage(req.stream_options):
                    yield _sse({"id": cid, "object": "chat.completion.chunk",
                                "created": created, "model": model,
                                "choices": [],
                                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
                yield "data: [DONE]\n\n"
                store.mark(acc_id, True, "")
            except MuseAuthError as exc:
                store.mark(acc_id, False, str(exc))
                yield _sse({"error": {"message": str(exc), "type": "auth_error", "code": 401}})
            except MuseGenerationError as exc:
                store.mark(acc_id, True, f"助手超时: {str(exc)[:60]}")
                try:
                    engine.reset_thread()
                except Exception:
                    pass
                yield _sse({"error": {"message": str(exc), "type": "server_error", "code": 502}})
            except Exception as exc:
                yield _sse({"error": {"message": f"内部错误: {exc}", "type": "server_error", "code": 500}})
        return StreamingResponse(sync_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

    def run() -> str:
        return "".join(safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id))

    try:
        text = await asyncio.to_thread(run)
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc))
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc))

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if tool_note:
        calls, rest = parse_tool_calls(text)
        if calls:
            return {"id": cid, "object": "chat.completion", "created": created,
                    "model": model,
                    "choices": [{"index": 0, "finish_reason": "tool_calls",
                                 "message": {"role": "assistant",
                                             "content": rest or None,
                                             "tool_calls": calls}}],
                    "usage": usage}
    return {"id": cid, "object": "chat.completion", "created": created,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": usage}


def _responses_messages(req: ResponsesRequest) -> list[ChatMessage]:
    """把 Responses API 的 instructions / input 归一成 messages。"""
    msgs: list[ChatMessage] = []
    if req.instructions:
        msgs.append(ChatMessage(role="system", content=req.instructions))
    inp = req.input
    if isinstance(inp, str):
        msgs.append(ChatMessage(role="user", content=inp))
        return msgs
    if isinstance(inp, list):
        for item in inp:
            if isinstance(item, str):
                msgs.append(ChatMessage(role="user", content=item))
                continue
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if item.get("role"):                      # message 形态
                msgs.append(ChatMessage(role=item["role"],
                                        content=item.get("content")))
            elif itype == "input_text":
                msgs.append(ChatMessage(role="user", content=item.get("text")))
            elif itype == "function_call_output":
                msgs.append(ChatMessage(role="user", content="【工具执行结果】\n"
                                        + str(item.get("output") or "")))
            elif itype == "function_call":
                msgs.append(ChatMessage(role="assistant", content="【请求调用工具】"
                                        + str(item.get("name") or "")))
    return msgs


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/v1/responses")
async def responses_api(req: ResponsesRequest, _=Depends(auth)):
    """OpenAI Responses API —— 新版 Codex 默认走这个端点。

    只实现 Codex 实际用到的子集：文本进 → 文本出（含流式事件）。
    muse.ai 不会返回结构化的 tool_calls，关于工具调用的边界见 README。
    """
    prompt = build_chat_prompt(_responses_messages(req))
    if not prompt:
        raise HTTPException(400, "input 为空")

    model = resolve_model(req.model, default="muse-spark")
    timeout = int(req.timeout or CFG.chat_timeout)
    acc = store.pick_account(rotate=True, preferred_id=getattr(engine, "current_acc_id", None))
    if not acc:
        raise HTTPException(400, "没有可用账号，请先在管理页面导入 cookie")

    rid = "resp_" + uuid.uuid4().hex[:24]
    mid = "msg_" + uuid.uuid4().hex[:24]
    created = int(time.time())
    acc_id, cookies, expires = acc["id"], acc["cookies"], acc.get("cookies_exp")

    def envelope(status: str, text: str = "") -> dict:
        done = status == "completed"
        return {"id": rid, "object": "response", "created_at": created,
                "status": status, "model": model,
                "output": [{"type": "message", "id": mid, "role": "assistant",
                            "status": "completed" if done else "in_progress",
                            "content": ([{"type": "output_text", "text": text,
                                          "annotations": []}] if text else [])}],
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "total_tokens": 0}}

    if req.stream:
        def sync_stream():
            try:
                yield _sse_event("response.created", {
                    "type": "response.created",
                    "response": envelope("in_progress")})
                yield _sse_event("response.output_item.added", {
                    "type": "response.output_item.added", "output_index": 0,
                    "item": {"id": mid, "type": "message", "role": "assistant",
                             "status": "in_progress", "content": []}})
                yield _sse_event("response.content_part.added", {
                    "type": "response.content_part.added", "item_id": mid,
                    "output_index": 0, "content_index": 0,
                    "part": {"type": "output_text", "text": "",
                             "annotations": []}})
                full = ""
                for chunk in safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id):
                    full += chunk
                    yield _sse_event("response.output_text.delta", {
                        "type": "response.output_text.delta", "item_id": mid,
                        "output_index": 0, "content_index": 0, "delta": chunk})
                yield _sse_event("response.output_text.done", {
                    "type": "response.output_text.done", "item_id": mid,
                    "output_index": 0, "content_index": 0, "text": full})
                yield _sse_event("response.output_item.done", {
                    "type": "response.output_item.done", "output_index": 0,
                    "item": {"id": mid, "type": "message", "role": "assistant",
                             "status": "completed",
                             "content": [{"type": "output_text", "text": full,
                                          "annotations": []}]}})
                yield _sse_event("response.completed", {
                    "type": "response.completed",
                    "response": envelope("completed", full)})
            except Exception as exc:  # noqa: BLE001
                log.warning("responses 流式失败: %s", exc)
                yield _sse_event("response.failed", {
                    "type": "response.failed",
                    "response": envelope("failed")})
        return StreamingResponse(sync_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

    def run() -> str:
        return "".join(safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id))

    try:
        text = await asyncio.to_thread(run)
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc))
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc))

    return envelope("completed", text)


@app.get("/v1/media/{name}")
def get_media(name: str, key: str | None = Query(default=None),
              authorization: str | None = Header(default=None)):
    """取已生成的媒体文件。

    需要鉴权：媒体目录里放的是账号额度换来的成品，Key 一旦泄露就等于全部裸奔。

    两种带凭证的方式：
      * 标准做法 —— `Authorization: Bearer <key>`（OpenAI 兼容客户端用这个）
      * 兜底 —— `?key=<key>` 查询参数。浏览器的 `<video src=...>` / `<img src=...>`
        没法附加请求头，只能走查询参数。前端播放路径依赖这个。
    """
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    # 复用统一鉴权逻辑，但把查询参数里的 key 视作 Bearer 凭证
    if key and not (authorization or "").lower().startswith("bearer "):
        authorization = f"Bearer {key}"
    auth(authorization)
    p = os.path.join(CFG.media_dir, name)
    if not os.path.isfile(p):
        raise HTTPException(404, "文件不存在")
    return FileResponse(p)


# ------------------------- 管理：总览 -------------------------
@app.get("/admin/status")
def admin_status(_=Depends(auth)):
    base = _public_base()
    return {
        "accounts": store.list_accounts(),
        "stats": store.stats(),
        "tasks": store.list_tasks(20),
        "media_count": len(os.listdir(CFG.media_dir))
        if os.path.isdir(CFG.media_dir) else 0,
        "browser_running": bool(engine.proc and engine.proc.poll() is None),
        "essential_cookies": list(ESSENTIAL_COOKIES),
        "scheduler": SCHED.stats(),
        "base_url": f"{base}/v1",
        "config": {"site": CFG.site_url, "cdp_port": CFG.cdp_port,
                   "image_timeout": CFG.image_timeout,
                   "video_timeout": CFG.video_timeout,
                   "host": CFG.host, "port": CFG.port,
                   "public_base": base},
    }


# ------------------------- 管理：账号池 -------------------------
@app.get("/admin/scheduler")
def admin_scheduler(_=Depends(auth)):
    """生成调度器观测：队列长度、正在跑的任务、累计指标（修缺陷 4 可观测性）。"""
    return SCHED.stats()


@app.get("/admin/accounts")
def list_accounts(_=Depends(auth)):
    return {"accounts": store.list_accounts(), "stats": store.stats()}


@app.post("/admin/accounts")
def add_account(req: AccountRequest, _=Depends(auth)):
    added: list[dict] = []
    seen: list[dict] = []          # 本次导入的所有 cookie，用来检查核心项是否齐全

    if req.batch:
        for label, cookies in parse_batch(req.batch):
            acc = store.add_account(cookies, label)
            seen.append(cookies)
            added.append({"id": acc["id"], "label": acc["label"],
                          "cookie_count": len(cookies),
                          "expires_at": acc.get("expires_at")})

    cookies = dict(req.cookies)
    if req.cookie_header:
        cookies.update(parse_cookie_text(req.cookie_header))
    if cookies:
        acc = store.add_account(cookies, req.label, cookies_exp=req.expires)
        seen.append(cookies)
        added.append({"id": acc["id"], "label": acc["label"],
                      "cookie_count": len(cookies),
                      "expires_at": acc.get("expires_at")})

    if not added:
        raise HTTPException(400, "未解析到任何 cookie，请检查格式")

    # 只要有一个账号把核心 cookie 凑齐就算通过（批量时按整体判断）
    missing = [n for n in ESSENTIAL_COOKIES
               if not any(n in c for c in seen)]
    return {"added": added, "count": len(added),
            "essential_missing": missing,
            "warning": (f"缺少核心 cookie：{', '.join(missing)}，该账号可能无法生成"
                        if missing else "")}


@app.patch("/admin/accounts/{aid}")
def patch_account(aid: str, req: AccountPatch, _=Depends(auth)):
    acc = store.update_account(aid, label=req.label, enabled=req.enabled)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return {k: v for k, v in acc.items() if k != "cookies"} | {
        "cookie_count": len(acc.get("cookies", {}))}


@app.delete("/admin/accounts/{aid}")
def del_account(aid: str, _=Depends(auth)):
    ok = store.delete_account(aid)
    if not ok:
        raise HTTPException(404, "账号不存在")
    return {"deleted": True, "id": aid}


@app.post("/admin/accounts/{aid}/test")
async def test_account(aid: str, _=Depends(auth)):
    """真实打开 muse.ai 验证该账号 cookie 是否仍可登录。"""
    acc = store.get_account(aid)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if not acc.get("cookies"):
        raise HTTPException(400, "该账号没有 cookie")

    def _probe():
        with GEN_LOCK:
            try:
                engine.start()
                engine.refresh(acc["cookies"], acc.get("cookies_exp"))
                synced = _sync_cookies(aid)
                quota = None
                try:  # 顺带刷新额度；读不到不影响测试结论
                    quota = engine.quota(acc["cookies"],
                                         acc.get("cookies_exp"))
                    quota["checked_at"] = int(time.time())
                    store.update_account(aid, quota=quota)
                except Exception:  # noqa: BLE001
                    quota = None
                store.mark(aid, True, "会话有效")
                return {"ok": True, "message": "会话有效，可正常生成",
                        "synced": synced, "quota": quota}
            except MuseAuthError as exc:
                store.mark(aid, False, str(exc)[:200])
                return {"ok": False, "message": str(exc)[:200]}
            except Exception as exc:  # noqa: BLE001
                store.touch_keepalive(aid, None, f"测试未确认（保留账号状态）: {str(exc)[:200]}")
                return {"ok": False, "message": str(exc)[:200]}

    res = await asyncio.to_thread(_probe)
    if not res["ok"]:
        return JSONResponse(res, status_code=200)
    return res


@app.post("/admin/accounts/{aid}/relogin")
async def relogin_account(aid: str, _=Depends(auth)):
    return await test_account(aid, _)


@app.post("/admin/accounts/{aid}/cookies")
def update_cookies(aid: str, payload: dict = Body(...), _=Depends(auth)):
    """更新某个账号的 cookie（用于会话过期后补新 cookie）。"""
    cookies = dict(payload.get("cookies") or {})
    if payload.get("cookie_header"):
        cookies.update(parse_cookie_text(payload["cookie_header"]))
    if not cookies:
        raise HTTPException(400, "未解析到 cookie")
    exp = {k: int(v) for k, v in (payload.get("expires") or {}).items()
           if _pos(v)}
    # 补入的是「全新会话」的 cookie，有效期估算锚点必须重置到当下，
    # 否则会沿用旧会话的锚点，把剩余天数算少。
    acc = store.update_account(aid, cookies=cookies, ok=None,
                               note="已更新 cookie",
                               expiry_anchor=int(time.time()),
                               cookies_exp=exp or None)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return {"ok": True, "id": aid, "cookie_count": len(cookies),
            "expires_at": acc.get("expires_at")}


@app.post("/admin/relogin")
def relogin(_=Depends(auth)):
    acc = store.pick_account(rotate=True)
    if not acc:
        raise HTTPException(400, "没有可用账号")
    try:
        engine.start()
        engine.refresh(acc["cookies"], acc.get("cookies_exp"))
        return {"ok": True, "account": acc["id"]}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


# ------------------------- 管理：额度 -------------------------
@app.post("/admin/accounts/{aid}/quota")
async def query_quota(aid: str, _=Depends(auth)):
    """打开 muse.ai 的 Settings 面板读该账号的额度（实时），并缓存到账号记录。

    返回例：{"plan":"Free plan","weekly_reset":"Sep 30",
             "weekly_used_pct":1,"extra_left":"2B tokens left",
             "extra_used_pct":0,"extra_expires":"never"}
    """
    acc = store.get_account(aid)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if not acc.get("cookies"):
        raise HTTPException(400, "该账号没有 cookie")

    def _probe():
        with GEN_LOCK:
            engine.start()
            q = engine.quota(acc["cookies"], acc.get("cookies_exp"))
            q["checked_at"] = int(time.time())
            store.update_account(aid, quota=q)
            return q

    try:
        return await asyncio.to_thread(_probe)
    except MuseAuthError as exc:
        store.mark(aid, False, str(exc)[:200])
        raise HTTPException(401, str(exc)) from exc
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"额度查询失败: {exc}") from exc


@app.post("/admin/quota")
async def query_any_quota(_=Depends(auth)):
    """用当前最久未用的可用账号查一次额度（同池账号共享同一 muse.ai 计划的
    通常只有一人使用时够用；多账号时建议按账号查）。"""
    acc = store.pick_account(rotate=True)
    if not acc:
        raise HTTPException(400, "没有可用账号")
    return await query_quota(acc["id"], _)


# ------------------------- 管理：接入信息 / API Key -------------------------
def _public_base() -> str:
    return (CFG.public_base or "").rstrip("/")


@app.get("/admin/apikey")
def get_apikey(_=Depends(auth)):
    base = _public_base()
    base_url = f"{base}/v1" if base else ""
    return {"api_key": CFG.api_key, "base_url": base_url,
            "models_url": f"{base}/v1/models" if base else "",
            "media_url": f"{base}/v1/media/{{name}}" if base else "/v1/media/{name}"}


@app.post("/admin/apikey/rotate")
def rotate_apikey(_=Depends(auth)):
    """生成新的 API Key，写入 .env 并立即生效（不用重启）。

    本地模式下还要写回 data/local_config.json —— 那是 run_local.py 每次启动时
    读取并用环境变量 MUSE2API_KEY 注入的来源。只写 .env 的话，重启后 Key 会被
    local_config 里的旧值覆盖回去（这就是「轮换后重启静默回退」的成因）。
    """
    import secrets
    new_key = "m2a_" + secrets.token_hex(24)
    old = CFG.api_key
    CFG.api_key = new_key
    _persist_env("MUSE2API_KEY", new_key)
    if CFG.local_mode:
        _persist_local_config_key(new_key)
    return {"ok": True, "api_key": new_key, "previous": old,
            "message": "已生成新 Key 并立即生效；旧 Key 已失效，请更新下游项目"}


def _persist_local_config_key(new_key: str) -> bool:
    """把新 Key 写回 data/local_config.json（本地模式的 Key 唯一事实来源）。

    local_config.json 在 backend_src/ 的上一级 data/ 下（run_local.py 以
    BASE_DIR/data 为固定路径）。写失败不致命 —— CFG.api_key 已经生效了，
    只是下次重启会回退，所以这里如实回报。
    """
    path = os.path.join(CFG.base_dir, os.pardir, "data", "local_config.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["api_key"] = new_key
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError) as e:
        log.warning("写回 local_config.json 失败，重启后 Key 会回退: %s", e)
        return False


def _persist_env(key: str, value: str):
    """把配置写回 .env（保留其它行，原子替换）。"""
    path = os.path.join(CFG.base_dir, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    os.replace(tmp, path)


# ------------------------- 管理：Cookie 获取 -------------------------
@app.get("/admin/cookie-helper")
def cookie_helper(download: int = 0):
    """返回本机取 cookie 的助手脚本（进阶方式，需要装 Python）。"""
    p = os.path.join(BASE_DIR, "tools", "get_muse_cookie.py")
    if not os.path.isfile(p):
        raise HTTPException(404, "助手脚本缺失")
    headers = {}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="get_muse_cookie.py"'
    return FileResponse(p, media_type="text/x-python", headers=headers)


@app.get("/admin/extension")
def extension_zip():
    """把浏览器扩展打包成 zip 返回（推荐方式，零命令行）。

    用户下载后解压 → chrome://extensions 开发者模式加载 → 点一下就导入 cookie。
    """
    src = os.path.join(BASE_DIR, "extension")
    if not os.path.isdir(src):
        raise HTTPException(404, "扩展目录缺失")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(src)):
            p = os.path.join(src, name)
            if os.path.isfile(p):
                z.write(p, os.path.join("muse2api-extension", name))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 'attachment; filename="muse2api-extension.zip"',
                 "Cache-Control": "no-store"})


@app.get("/admin/extension/files")
def extension_files():
    """列出扩展目录内容（前端展示用）。"""
    src = os.path.join(BASE_DIR, "extension")
    if not os.path.isdir(src):
        raise HTTPException(404, "扩展目录缺失")
    return {"files": sorted(f for f in os.listdir(src)
                            if os.path.isfile(os.path.join(src, f)))}


# ------------------------- 管理：剧本备份 -------------------------
@app.get("/admin/scripts")
def list_script_backups(_=Depends(auth)):
    """列出所有剧本备份（持久化到 data/scripts/，重启后保留）。"""
    d = _scripts_dir()
    items = []
    for key, script in _SCRIPT_BACKUP_STORE.items():
        saved_at = None
        try:
            p = _script_path(key)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    meta = json.load(f)
                saved_at = meta.get("saved_at")
        except Exception:
            pass
        items.append({
            "key": key,
            "length": len(script),
            "saved_at": saved_at,
            "preview": script[:80] + "…" if len(script) > 80 else script,
        })
    items.sort(key=lambda x: x.get("saved_at") or 0, reverse=True)
    return {"count": len(items), "scripts": items}


@app.get("/admin/scripts/{key}")
def get_script_backup(key: str, _=Depends(auth)):
    """取回指定 key 的完整原始剧本。"""
    if key not in _SCRIPT_BACKUP_STORE:
        raise HTTPException(404, "剧本备份不存在")
    saved_at = None
    try:
        with open(_script_path(key), encoding="utf-8") as f:
            meta = json.load(f)
        saved_at = meta.get("saved_at")
    except Exception:
        pass
    script = _SCRIPT_BACKUP_STORE[key]
    return {"key": key, "length": len(script), "saved_at": saved_at, "script": script}


@app.delete("/admin/scripts/{key}")
def delete_script_backup(key: str, _=Depends(auth)):
    """删除指定 key 的剧本备份（内存 + 磁盘）。"""
    if key not in _SCRIPT_BACKUP_STORE:
        raise HTTPException(404, "剧本备份不存在")
    _SCRIPT_BACKUP_STORE.pop(key, None)
    try:
        p = _script_path(key)
        if os.path.isfile(p):
            os.unlink(p)
    except Exception as exc:
        log.warning("删除剧本备份文件失败: %s", exc)
    return {"deleted": True, "key": key}


# ------------------------- 管理：任务 / 媒体 -------------------------
@app.get("/admin/tasks")
def admin_tasks(limit: int = 50, _=Depends(auth)):
    return {"tasks": store.list_tasks(limit)}


@app.delete("/admin/tasks/{tid}")
def del_task(tid: str, _=Depends(auth)):
    if not store.delete_task(tid):
        raise HTTPException(404, "任务不存在")
    return {"deleted": True}


@app.post("/admin/tasks/clear")
def clear_tasks(payload: dict = Body(default={}), _=Depends(auth)):
    return {"removed": store.clear_tasks(int(payload.get("keep") or 0))}


@app.get("/admin/media")
def admin_media(_=Depends(auth)):
    d = CFG.media_dir
    items = []
    if os.path.isdir(d):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(name)[1].lower()
            items.append({"name": name, "url": media_url(name), "bytes": os.path.getsize(p),
                          "mtime": int(os.path.getmtime(p)),
                          "kind": "video" if ext in (".mp4", ".webm", ".mov") else "image"})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"media": items, "count": len(items)}


# ------------------------- 前端页面 -------------------------
def _admin_html() -> str:
    p = os.path.join(BASE_DIR, "admin.html")
    try:
        with open(p, encoding="utf-8") as f:
            content = f.read()
            if CFG.api_key:
                # 自动为本地用户注入 API Key，防止打开时需要手动复制粘贴
                inject_js = f"""<script>
try {{
  if (!localStorage.getItem('muse2api_key')) {{
    localStorage.setItem('muse2api_key', '{CFG.api_key}');
  }}
}} catch(e) {{}}
</script>"""
                content = content.replace("</head>", f"{inject_js}\n</head>", 1)
            return content
    except OSError:
        return ("<!doctype html><meta charset=utf-8><body style='background:#0b0f19;"
                "color:#e6e8ee;font-family:system-ui;padding:40px'>"
                "<h2>muse2api</h2><p>管理页面文件 admin.html 缺失。</p>"
                "<p>接口可用：<code>/v1/images/generations</code>、"
                "<code>/v1/videos</code></p></body>")


@app.get("/", response_class=HTMLResponse)
def index():
    return _admin_html()


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return _admin_html()



# ------------------------- 账号自动保活与静默续期 -------------------------
KEEPALIVE_LOCK = asyncio.Lock()
KEEPALIVE_STATE = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "next_run": None,
}


def _probe_account_sync(aid: str, check_quota: bool = False) -> dict:
    """通过 muse.ai/api/session 触发 Meta 网关签发新 hatch_vml (+48h) / hatch_sess (+30d) 并唤醒云端 VM。"""
    acc = store.get_account(aid)
    if not acc or not acc.get("cookies"):
        return {"ok": False, "id": aid, "label": (acc or {}).get("label", aid), "error": "账号无有效 cookie"}
    try:
        res = engine.renew_session_http(acc["cookies"], acc.get("cookies_exp"), wake_vm=True)
        store.update_account(
            aid,
            cookies=res["cookies"],
            cookies_exp=res["cookies_exp"],
            ok=True if res.get("ok") else False,
            synced_at=int(time.time()),
        )
        vm_state = res.get("vm_state") or "RUNNING"
        store.touch_keepalive(aid, True, f"会话有效 · 自动保活 (VM: {vm_state})")
        quota = acc.get("quota")
        if check_quota and GEN_LOCK.acquire(blocking=False):
            try:
                engine.start()
                quota = engine.quota(res["cookies"], res["cookies_exp"])
                quota["checked_at"] = int(time.time())
                store.update_account(aid, quota=quota)
            except Exception as qe:  # noqa: BLE001
                log.warning("读取账号 %s 额度失败: %s", aid, qe)
            finally:
                GEN_LOCK.release()
        updated = store.get_account(aid) or {}
        return {
            "ok": True,
            "id": aid,
            "label": acc.get("label", aid),
            "vm_id": res.get("vm_id"),
            "vm_state": vm_state,
            "wake_ok": res.get("wake_ok"),
            "expires_at": updated.get("expires_at"),
            "quota": quota,
        }
    except MuseAuthError as exc:
        store.touch_keepalive(aid, False, f"保活认证失败: {str(exc)[:200]}")
        return {"ok": False, "id": aid, "label": acc.get("label", aid), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        store.touch_keepalive(aid, None, f"保活未确认（保留账号状态）: {str(exc)[:200]}")
        return {"ok": False, "id": aid, "label": acc.get("label", aid), "error": str(exc)}


async def run_keepalive_all(force: bool = False) -> dict:
    """执行账号保活：force=True 时强制刷新所有启用账号，否则刷新临期/未检账号并保持 VM 热备。"""
    async with KEEPALIVE_LOCK:
        now = int(time.time())
        KEEPALIVE_STATE["running"] = True
        KEEPALIVE_STATE["last_run"] = now
        results = []
        skipped = []

        try:
            accounts = [a for a in store.list_accounts() if a.get("enabled", True)]
            for a in accounts:
                aid = a["id"]
                label = a.get("label", aid)
                exp_at = a.get("expires_at")
                last_ka = a.get("last_keepalive") or 0

                needs_run = (
                    force
                    or not exp_at
                    or (exp_at - now < 36 * 3600)
                    or ((now - last_ka) > 15 * 60)
                    or (a.get("ok") is not True)
                )
                if not needs_run:
                    skipped.append({"id": aid, "label": label, "reason": "会话充足且近期已保活"})
                    continue

                log.info("【自动保活】正在为账号 %s (%s) 执行静默续期与 VM 唤醒...", label, aid)
                res = await asyncio.to_thread(_probe_account_sync, aid, False)
                results.append(res)
                log.info("【自动保活】账号 %s 执行结果: ok=%s expires_at=%s", label, res.get("ok"), res.get("expires_at"))
                await asyncio.sleep(0.5)

            summary = {
                "checked_at": now,
                "refreshed_count": len(results),
                "refreshed": results,
                "skipped_count": len(skipped),
                "skipped": skipped,
            }
            KEEPALIVE_STATE["last_result"] = summary
            KEEPALIVE_STATE["next_run"] = now + 900
            return summary
        finally:
            KEEPALIVE_STATE["running"] = False


def _warmup_browser_sync():
    """后台静默预热浏览器与首个可用账号的 WebSocket 隧道，使重启后首条请求也秒回。"""
    if getattr(engine, "current_acc_id", None) and engine.page is not None:
        return
    acc = store.pick_account(rotate=False)
    if not acc or not acc.get("cookies"):
        return
    if not GEN_LOCK.acquire(blocking=False):
        return
    try:
        log.info("【浏览器预热】正在后台预热账号 %s (%s) 的热备标签页...", acc.get("label"), acc["id"])
        refreshed = _renew_and_persist(acc["id"], wake_vm=True, force=False) or acc
        engine.start()
        engine.ensure_page(refreshed["cookies"], refreshed.get("cookies_exp"), account_id=acc["id"])
        log.info("【浏览器预热】账号 %s (%s) 热备标签页与 WebSocket 已就绪", acc.get("label"), acc["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("【浏览器预热】预热异常: %s", exc)
    finally:
        GEN_LOCK.release()


async def _keepalive_loop():
    """后台常驻守护任务：每 15 分钟轮询一次账号健康状态并保持 VM 热备。"""
    log.info("【自动保活守护进程】已启动，检测周期: 15 分钟")
    await asyncio.sleep(1)
    try:
        await asyncio.to_thread(_warmup_browser_sync)
    except Exception as e:  # noqa: BLE001
        log.warning("【浏览器预热】异常: %s", e)
    while True:
        try:
            await run_keepalive_all(force=False)
            if not getattr(engine, "current_acc_id", None) or engine.page is None:
                await asyncio.to_thread(_warmup_browser_sync)
        except Exception as e:  # noqa: BLE001
            log.error("【自动保活守护进程】轮询异常: %s", e)
        await asyncio.sleep(900)


@app.post("/admin/accounts/keepalive")
async def trigger_keepalive_all(force: bool = True, _=Depends(auth)):
    """管理员手动触发一次全账号保活续期。"""
    if KEEPALIVE_STATE["running"]:
        return {"status": "busy", "message": "保活任务正在执行中，请稍候"}
    return await run_keepalive_all(force=force)


@app.post("/admin/accounts/{aid}/keepalive")
async def trigger_keepalive_single(aid: str, _=Depends(auth)):
    """手动针对单个账号执行保活续期。"""
    return await asyncio.to_thread(_probe_account_sync, aid, False)


@app.get("/admin/keepalive/status")
def get_keepalive_status(_=Depends(auth)):
    """获取保活守护协程状态。"""
    return KEEPALIVE_STATE


# ------------------------- 仓库实时更新检测、通知与一键在线升级 -------------------------
REPO_URL = "https://github.com/czg86389-hub/muse2api"
TRACKED_REPO_PATHS = [
    "app.py", "engine.py", "store.py", "cdp.py", "config.py",
    "admin.html", "README.md", "version.json", "requirements.txt",
    "Dockerfile", "docker-compose.yml", ".env.example", ".gitignore",
    "LICENSE", "extension", "deploy", "tools",
]
_UPDATE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def _read_env_key(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    path = os.path.join(CFG.base_dir, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f.read().splitlines():
                s = ln.strip()
                if s.startswith(key + "="):
                    return s.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _read_local_version() -> dict:
    p = os.path.join(BASE_DIR, "version.json")
    try:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
            if isinstance(obj, dict):
                return obj
    except Exception:
        pass
    return {"version": "1.5.0", "highlights": []}


def _installed_sha_file() -> str:
    return os.path.join(CFG.data_dir, ".installed_sha")


def _git(args: list[str], timeout: int = 30):
    import subprocess
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-c", f"safe.directory={BASE_DIR}", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _ensure_git_repo(token: str = ""):
    """确保 BASE_DIR 已初始化为绑定 czg86389-hub/muse2api 的 Git 仓库。"""
    git_dir = os.path.join(BASE_DIR, ".git")
    remote_url = f"https://x-access-token:{token}@github.com/czg86389-hub/muse2api.git" if token else f"{REPO_URL}.git"
    if not os.path.isdir(git_dir):
        _git(["init", "-b", "main"])
        _git(["remote", "add", "origin", remote_url])
        _git(["fetch", "origin", "main"], timeout=45)
        _git(["reset", "--mixed", "origin/main"])
    else:
        _git(["remote", "set-url", "origin", remote_url])
    _git(["config", "user.name", "czg86389-hub"])
    _git(["config", "user.email", "czg86389-hub@users.noreply.github.com"])


def _reject_upgrade_in_local_mode():
    """本地模式下拒绝「一键升级」和「推送到上游」。

    这两个接口对服务器部署是有用的，但对本地版是毁灭性的：

      * /admin/update/upgrade 用上游 tarball 覆盖 backend_src/ 下**所有**文件，
        只保护 .env / data/accounts.json / data/tasks.json 三个。本地版对
        app.py、longvideo.py 等做了大量魔改（长视频分段等），一次点击全没了，
        而且 admin.html 上那个按钮没有确认弹窗。
      * /admin/repo/push 会 git push 到上游 main 分支。本地版不是上游的延续，
        推上去等于污染别人的仓库。

    两者原本都只用 Depends(auth)（即 Bearer Key）保护，而 Key 明文写在
    web/index.html 里，所以任何一个知道 Key 的人都能触发。这里在本地模式下
    直接堵死；要恢复得改 MUSE2API_LOCAL_MODE 并自行评估风险。
    """
    if CFG.local_mode:
        raise HTTPException(
            403,
            "本地模式已禁用在线升级与代码推送：它们会覆盖/污染本地对 backend_src/ 的修改。"
            "如需升级请手动 git 操作。")


def _check_update_sync(force: bool = False) -> dict:
    """检测 GitHub 官方仓库 (czg86389-hub/muse2api) 是否有新版本或新提交。
    默认缓存 90 秒，防止频繁刷新触发 GitHub API 速率限制。"""
    now = time.time()
    if not force and _UPDATE_CACHE["data"] and (now - _UPDATE_CACHE["ts"]) < 90:
        return _UPDATE_CACHE["data"]

    import requests
    token = _read_env_key("GITHUB_TOKEN")
    local_ver_obj = _read_local_version()
    local_version = str(local_ver_obj.get("version") or "1.5.0")

    has_git = os.path.isdir(os.path.join(BASE_DIR, ".git"))
    local_sha, local_msg, local_ts = "", "", 0
    if has_git:
        try:
            r_sha = _git(["rev-parse", "--short", "HEAD"])
            if r_sha.returncode == 0:
                local_sha = r_sha.stdout.strip()[:7]
            r_log = _git(["log", "-1", "--format=%s||%ct"])
            if r_log.returncode == 0 and "||" in r_log.stdout:
                parts = r_log.stdout.strip().split("||", 1)
                local_msg = parts[0]
                local_ts = int(parts[1])
        except Exception:
            pass

    if not local_sha and os.path.isfile(_installed_sha_file()):
        try:
            with open(_installed_sha_file(), encoding="utf-8") as f:
                local_sha = f.read().strip()[:7]
        except OSError:
            pass

    remote_version = local_version
    highlights = list(local_ver_obj.get("highlights") or [])
    try:
        rv = requests.get(
            f"https://raw.githubusercontent.com/czg86389-hub/muse2api/main/version.json?t={int(now)}",
            timeout=6,
        )
        if rv.status_code == 200:
            rvj = rv.json()
            if isinstance(rvj, dict):
                remote_version = str(rvj.get("version") or remote_version)
                if rvj.get("highlights"):
                    highlights = list(rvj["highlights"])
    except Exception:
        pass

    remote_sha, remote_msg, remote_time = "", "", ""
    recent_commits = []
    try:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "muse2api-updater"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(
            "https://api.github.com/repos/czg86389-hub/muse2api/commits?sha=main&per_page=5",
            headers=headers,
            timeout=6,
        )
        if resp.status_code == 200 and isinstance(resp.json(), list):
            commits = resp.json()
            for idx, c in enumerate(commits):
                sha7 = (c.get("sha") or "")[:7]
                c_msg = ((c.get("commit") or {}).get("message") or "").splitlines()[0]
                c_date = (((c.get("commit") or {}).get("committer") or {}).get("date") or "")
                c_url = c.get("html_url") or f"{REPO_URL}/commit/{sha7}"
                if idx == 0:
                    remote_sha = sha7
                    remote_msg = c_msg
                    remote_time = c_date
                recent_commits.append({
                    "sha": sha7,
                    "message": c_msg,
                    "date": c_date,
                    "url": c_url,
                })
    except Exception:
        pass

    # 若用户通过 Docker/ZIP 部署（无 .git）且首次运行版本一致，记录初始基准 SHA
    if not local_sha and remote_sha and local_version == remote_version:
        local_sha = remote_sha
        try:
            with open(_installed_sha_file(), "w", encoding="utf-8") as f:
                f.write(remote_sha)
        except OSError:
            pass

    has_update = False
    if remote_sha and local_sha and remote_sha != local_sha:
        has_update = True
    elif remote_version and local_version and remote_version != local_version:
        has_update = True

    data = {
        "repo_url": REPO_URL,
        "has_git": has_git,
        "local_version": local_version,
        "remote_version": remote_version,
        "local_sha": local_sha,
        "local_msg": local_msg,
        "local_ts": local_ts,
        "remote_sha": remote_sha,
        "remote_msg": remote_msg,
        "remote_time": remote_time,
        "has_update": has_update,
        "up_to_date": not has_update and bool(remote_sha or remote_version),
        "highlights": highlights,
        "recent_commits": recent_commits,
        "checked_at": int(now),
        # 本地模式下管理页据此隐藏「一键升级」横幅与按钮（后端也会拒绝该接口）。
        "local_mode": bool(CFG.local_mode),
    }
    _UPDATE_CACHE["ts"] = now
    _UPDATE_CACHE["data"] = data
    return data


def _upgrade_from_github_sync() -> dict:
    """从 GitHub 拉取最新代码覆盖核心文件（兼容 Git 与无 Git 的 Docker/ZIP 环境），绝不触碰 .env 与 data/。"""
    import requests
    import tarfile

    token = _read_env_key("GITHUB_TOKEN")
    upgraded_via = ""
    try:
        _ensure_git_repo(token)
        f_res = _git(["fetch", "origin", "main"], timeout=45)
        if f_res.returncode == 0:
            _git(["checkout", "-f", "origin/main", "--", "."])
            _git(["reset", "--mixed", "origin/main"])
            upgraded_via = "git"
    except Exception as e:
        log.warning("Git 拉取更新失败，将使用 Tarball 方式更新: %s", e)

    if not upgraded_via:
        resp = requests.get(
            "https://codeload.github.com/czg86389-hub/muse2api/tar.gz/refs/heads/main",
            timeout=60,
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"下载 GitHub 更新包失败 (HTTP {resp.status_code})")
        protected_files = {".env", "data/accounts.json", "data/tasks.json"}
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel = parts[1].replace("\\", "/")
                if ".." in rel or rel in protected_files:
                    continue
                target_path = os.path.join(BASE_DIR, rel)
                if member.isdir():
                    os.makedirs(target_path, exist_ok=True)
                elif member.isfile():
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    fobj = tar.extractfile(member)
                    if fobj is not None:
                        with open(target_path, "wb") as out_f:
                            out_f.write(fobj.read())
        upgraded_via = "tarball"

    _UPDATE_CACHE["ts"] = 0.0
    status = _check_update_sync(force=True)
    if status.get("remote_sha"):
        try:
            with open(_installed_sha_file(), "w", encoding="utf-8") as f:
                f.write(status["remote_sha"])
            status["local_sha"] = status["remote_sha"]
            status["has_update"] = False
            status["up_to_date"] = True
        except OSError:
            pass
    return {
        "ok": True,
        "via": upgraded_via,
        "message": f"已成功更新至最新版本 {status.get('remote_version')} ({status.get('remote_sha')})",
        "status": status,
    }


@app.get("/admin/update/check")
@app.get("/admin/repo/status")
async def admin_check_update(force: bool = False):
    """供所有已部署节点实时检测 GitHub 官方仓库是否有新版本更新。"""
    return await asyncio.to_thread(_check_update_sync, force)


@app.post("/admin/update/upgrade")
@app.post("/admin/repo/pull")
async def admin_upgrade_now(payload: dict = Body(default={}), _=Depends(auth)):
    """一键从 GitHub 官方仓库拉取最新更新并自动平滑重启服务。"""
    _reject_upgrade_in_local_mode()
    res = await asyncio.to_thread(_upgrade_from_github_sync)
    restart = payload.get("restart", True) if isinstance(payload, dict) else True
    if restart:
        def _delayed_restart():
            time.sleep(0.5)
            try:
                engine.stop()
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=_delayed_restart, daemon=True).start()
    return res


@app.post("/admin/repo/push")
async def admin_repo_push(payload: dict = Body(default={}), _=Depends(auth)):
    """维护者专用：将当前节点核心代码推送到 GitHub 仓库（自动过滤 .env 与 data 目录）。"""
    _reject_upgrade_in_local_mode()
    msg = (payload.get("message") or "").strip() or f"chore: sync update ({time.strftime('%Y-%m-%d %H:%M:%S')})"
    new_token = (payload.get("github_token") or "").strip()
    if new_token:
        _persist_env("GITHUB_TOKEN", new_token)
        os.environ["GITHUB_TOKEN"] = new_token
    token = new_token or _read_env_key("GITHUB_TOKEN")

    def _do_push():
        _ensure_git_repo(token)
        existing_paths = [p for p in TRACKED_REPO_PATHS if os.path.exists(os.path.join(BASE_DIR, p))]
        _git(["add", "--", *existing_paths])
        st = _git(["status", "--porcelain", "--", *existing_paths])
        committed = False
        if st.stdout.strip():
            c_res = _git(["commit", "-m", msg])
            if c_res.returncode != 0:
                raise HTTPException(500, f"Git commit 失败: {c_res.stderr or c_res.stdout}")
            committed = True
        p_res = _git(["push", "origin", "HEAD:main"], timeout=60)
        if p_res.returncode != 0:
            err = (p_res.stderr or p_res.stdout or "").strip()
            raise HTTPException(500, f"Git push 失败: {err[:300]}")
        _UPDATE_CACHE["ts"] = 0.0
        status = _check_update_sync(force=True)
        return {
            "ok": True,
            "committed": committed,
            "message": "已成功提交并推送到 GitHub 仓库",
            "status": status,
        }

    return await asyncio.to_thread(_do_push)


@app.on_event("startup")
async def _startup():
    for task in list(store.tasks.values()):
        if task.get("status") not in ("queued", "processing", "running"):
            continue
        # 长视频重启后会丢在 processing 上：driver 是进程内线程，进程一死就没了。
        # 不标 failed 的话它会永远卡在 processing，前端 120 秒后开始显示
        # "疑似浏览器会话卡死"，用户完全不知道真实原因是重启。
        if task.get("segment_total"):
            done = task.get("segment_done") or len(task.get("segments") or [])
            store.update_task(
                task["id"], status="failed", stage="failed",
                error=f"服务重启中断了长视频任务（已完成 {done}/{task['segment_total']} 段，"
                      f"分段文件已保留），请重新提交")
        elif task.get("kind") == "image":
            store.update_task(task["id"], status="failed", error="服务重启中断了任务，请重新提交")
        else:
            store.update_task(task["id"], status="failed", stage="failed",
                              error="服务重启中断了任务，请重新提交")
    if not CFG.api_key:
        import secrets
        new_key = "m2a_" + secrets.token_hex(24)
        CFG.api_key = new_key
        _persist_env("MUSE2API_KEY", new_key)
        log.info("🔑 未检测到 MUSE2API_KEY，已自动生成初始密钥: %s", new_key)
    _load_script_backups()
    log.info("📄 已从磁盘加载 %d 个剧本备份", len(_SCRIPT_BACKUP_STORE))
    SCHED.start()
    asyncio.create_task(_keepalive_loop())


@app.on_event("shutdown")
def _shutdown():
    SCHED.stop()
    engine.stop()
