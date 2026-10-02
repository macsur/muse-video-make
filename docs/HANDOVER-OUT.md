# 交班记录（Muse 视频工作台 · 本地版）

> 记录时间：2026-10-02
> 记录人：Claude（已完成全部代码通读，未做任何代码改动）
> 交接对象：接手继续修改完善的人 / 会话

---

## 0. 一句话概括

把 **muse.ai 网页免费账号** 的对话 / 生图 / 生视频额度，用 CDP 操控真实 Chrome 反代成 **OpenAI 兼容 API**，并配一个本地网页工作台。服务器版（Docker/systemd）被改造成了"下载即用"的本地版。

---

## 1. 血缘关系（先搞清楚，别搞混）

| 角色 | 仓库 | 说明 |
|---|---|---|
| **本目录来源** | `yys9253462-gif/muse-video-installer` | 服务器一键安装器（本目录就是它，`install.sh` 104KB、`test-install.sh` 30KB 都在） |
| **真正的服务端** | `czg86389-hub/muse2api` | `backend_src/` 的内容，`install.sh` 就是把它装到服务器上的 |

⚠️ **`run_local.py:37` 写错了仓库地址**（**已于 2026-10-02 修复**）：

```python
MUSE2API_REPO = "yys9253462-gif/muse2api"   # ← 这个仓库不存在
```

正确应为 `czg86389-hub/muse2api`。原先因为 `backend_src/app.py` 已存在，`ensure_backend_source()` 直接 return，所以没暴露；但删掉 `backend_src/` 重新拉源码就会失败。

---

## 2. 目录地图

### 2.1 本地化新增层（改代码主要动这里）

| 文件 | 行数 | 职责 |
|---|---|---|
| `run_local.py` | 474 | **主启动器**。建 venv、探浏览器、拉源码、注入前端配置、起 web + 后端双服务、`--import-account` |
| `start_local.sh` / `.bat` | 22 / 14 | 挑一个能用的 Python 3 再 exec `run_local.py` |
| `web/index.html` | 431 | 文生视频工作台（提示词 / 时长 / 画幅 / 首帧图 → 轮询任务 → 播视频） |
| `README_LOCAL.md` | 74 | 本地使用说明 |
| `data/local_config.json` | — | **本地密钥与端口的唯一事实来源** |
| `data/profiles/generate/` | 122M | 无头 Chrome 的 user-data-dir |
| `tools/get_muse_cookie.py` | 1364 | 导号工具（纯标准库，弹独立浏览器登录后抓 Cookie 上传） |
| `tools/test-import-tool.py` | 305 | 导号工具的回归测试 |

### 2.2 上游原样层（`backend_src/`，几乎未改）

| 文件 | 行数 | 职责 |
|---|---|---|
| `app.py` | 2763 | FastAPI 全部路由、OpenAI 兼容层、提示词构造、参数校验、账号池管理、**GitHub 在线升级** |
| `engine.py` | 1197 | **CDP 驱动 muse.ai 网页**生图/生视频/对话 |
| `scheduler.py` | 331 | 单 worker FIFO 生成队列（替代"抢裸锁"） |
| `cdp.py` | 181 | 极简 CDP 客户端（单读循环线程，防死锁） |
| `store.py` | 302 | 账号池 / 任务 JSON 原子落盘 |
| `config.py` | 145 | 全部配置走 `MUSE2API_*` 环境变量 + `.env` |
| `admin.html` | 1475 | 账号池管理面板（挂在 `GET /` 和 `/admin`） |
| `extension/` | — | Chrome 扩展（点一下把 Cookie 同步到本地服务） |
| `Dockerfile` / `docker-compose.yml` / `deploy/` | — | 服务器版残留，本地版**用不到** |
| `data/` | — | `accounts.json` / `tasks.json` / `media/` / `scripts/` / `chromium.log` |

---

## 3. 运行原理（改代码前必须理解的部分）

### 3.1 核心机制：**不是调 API，是操控网页**

```
注入 Cookie → 导航 muse.ai/thread/new → 等 React 水合 + WebSocket 就绪
   → 真实鼠标点击聚焦 textarea → React 原型 setter 灌提示词
   → 等 Send 按钮渲染出来再点（按钮在 = React 真收到输入了）
   → 轮询 [data-testid^="hatch-chat-attachment-presentation-"] 抓 img/video 的 blob src
   → 在页面里 fetch(blobUrl) → ArrayBuffer → base64 → 回传 Python 落盘
   → 失败则退化为点"下载"按钮，从 downloads/ 目录捞文件
```

关键函数坐标（`backend_src/engine.py`）：

- `_apply_cookies()` :212 — 先 `Network.clearBrowserCookies` 清空保证账号隔离
- `_wait_ws_ready()` :267 — 等 `readyState=complete` + 有 textarea + hydration=hydrated + 无 "Connecting..."
- `_send()` :575 — **双条件校验**：文字真进了输入框 **且** Send 按钮真被 React 渲染出来
- `_ATT_JS` :520 — 附件抓取 JS（视频封面 vs 视频源要严格区分，防误取封面图）
- `_wait_attachment()` :682 — 轮询 + 进度 `min(95, 25+elapsed*0.6)`，**绝不伪造进度**
- `_EXTRACT_JS` :775 — 页面内 fetch + btoa 取字节
- `_download_fallback()` :961 — 只点**选中结果**的下载按钮，绝不点全局按钮
- `renew_session_http()` :147 — GET `/api/session` 续签 + POST `/api/hatch/vm/wake` 唤醒 VM
- `generate()` :1110 — 主流程编排

### 3.2 账号存活模型

- 决定生死的 cookie 是 **`hatch_vml`**（约 2 天寿命，不因使用而延长）
- 保活 = 直接打 `/api/session` 触发 Meta 网关重新签发 + 唤醒云端 VM
- **实测观察**：每 15 分钟保活一次，`expires_at` 每次只 +900s（不是 +48h）。也就是账号实际寿命绑在"保活不能停超过 15 分钟"上。`store.py:29 VML_TTL = 2*86400` 是拿不到 expires 时的兜底估算。

### 3.3 并发模型（`scheduler.py`）

**只有 1 个浏览器**，所以 chat / image / video 三条路径**必须串行**。

原设计（已废弃）：每请求起线程抢 `GEN_LOCK`（裸 `threading.Lock`）→ 不保证 FIFO、会饿死、会死等。

现设计：
- `queue.Queue` + **唯一 worker 线程** → 天然 FIFO
- 出队前查 `deadline`，排队超 900s 直接判 `timeout`，不浪费浏览器时间
- `run_timeout = video_timeout + 300` 的看门狗 → 超时则 `engine.stop()` 强杀浏览器打断
- 三个入口：`submit`（异步）/ `run_sync`（已在独立线程里）/ `run`（管理页旁路探活，会插队）

### 3.4 数据落盘位置（**分散在两处，容易踩坑**）

| 内容 | 路径 | 写入方 |
|---|---|---|
| 本地密钥 / 端口 | `data/local_config.json` | `run_local.py` |
| 浏览器 profile | `data/profiles/generate/` | Chrome |
| 账号池 | `backend_src/data/accounts.json` | `store.py` |
| 任务记录 | `backend_src/data/tasks.json` | `store.py` |
| 生成的视频 | `backend_src/data/media/` (51M) | `engine.py` |
| 压缩前的长剧本备份 | `backend_src/data/scripts/` | `app.py:_save_script_backup` |
| 服务端 Key（**另一份**） | `backend_src/.env` | `app.py:_persist_env` |

---

## 4. 关键不变量（改代码时不能破坏）

1. **浏览器只有一个** —— 任何绕过 `SCHED` 直接 `with GEN_LOCK` 的新代码路径都是隐患。`app.py:1101` 的注释写得很清楚：`_run_generation()` 假定调用方已持锁。
2. **`GEN_LOCK` 不可重入** —— 调度器 worker 已持锁后再抢会永久自锁（历史上真踩过）。
3. **进度不许伪造** —— `get_video()` 已删掉旧的 `min(92, 20+elapsed*1.1)`，改为返回真实 `progress` + `idle_seconds`，>120s 无进展标 `stalled`。别改回去。
4. **cookie 有效期只信实测 expires 或导入锚点** —— 不用"检测成功"虚推 48h（`store.py:188` 有注释说明）。
5. **`_sync_cookies()` 绝不能让生成失败** —— 生成已经成功了，同步只是锦上添花（`app.py:1063`）。
6. **账号切换重试最多 2 次** —— 且已经 yield 出内容后不再重试（避免重复输出）。
7. **`CDP.send()` 绝不无限阻塞** —— 业务线程只 `wait` 结果，读循环线程独占 `recv()`。

---

## 5. 已知问题清单（全部经过实测确认）

### 🔴 P0-1：当前有**两个后端实例**抢同一个端口

实测证据：

```
PID 39690  uvicorn --host 0.0.0.0   --port 18610   16:57 起 · 孤儿(PPID=1) · 写 backend.log · Key 走 .env
PID 68241  run_local.py --open-browser              18:17 起 · PPID 15980(你的终端)
PID 68247  uvicorn --host 127.0.0.1 --port 18610   18:17 起 · run_local 的子进程 · Key 走 local_config
PID 21773  Chrome --headless=new --remote-debugging-port=19210  16:07 起 · PPID=1 · profile=data/profiles/generate
```

- macOS 允许 `0.0.0.0:P` 与 `127.0.0.1:P` 同时绑定，`run_local.py` 启动时**没有任何端口占用检测**，直接起来了
- 两份 Key 不一致：`local_config` = `m2a_1dcf48…`，`.env` = `m2a_2cd0fa…`
  - 实测：连 `127.0.0.1:18610` 命中的是 run_local 那个 → 用 local_config 的 Key 能通（200）
  - `backend.log` 里满屏 401，就是另一个进程拿着错的 Key 在探活
- **两个进程共享同一个 `backend_src/data/*.json`**，`store.py` 的 `_LOCK` 是进程内锁，跨进程无保护 → 账号/任务互相覆盖
- **两个进程共享同一个 Chrome**（`engine.start()` 检测到 19210 已有 CDP 就直接复用）→ `admin/status` 里 `browser_running: false` 就是复用结果。两边都能下发点击，**会串话**
- **保活守护跑了两份**，每 15 分钟各打一次 `/api/session`
- Chrome 的 PPID=1，**没人持有它**，会一直常驻

### 🔴 P0-2：没有单实例保护

`run_local.py` 只在**首次生成配置**时用 `is_port_in_use` 找空闲端口；配置已存在就直接复用 `local_config.json` 里的端口，**启动时不复检**。也没有 pidfile / 文件锁。后果就是 P0-1。

### 🟠 P1-1：**一键在线升级会覆盖本地魔改**

`admin.html:229` 有「⚡ 一键在线升级并重启」按钮 → `POST /admin/update/upgrade` → `app.py:2618 _upgrade_from_github_sync()` **直接把 GitHub tarball 覆盖 `backend_src/` 下所有文件**（只保护 `.env` / `data/accounts.json` / `data/tasks.json` 三个文件）。

**本地版对用户改过的 `backend_src/*.py` 是毁灭性的，且无任何确认弹窗。**

### 🟠 P1-2：`/admin/repo/push` 能把本地代码推到 GitHub

`app.py:2706`，维护者专用接口，本地版不应暴露。

### 🟠 P1-3：Key 轮换会静默回退

`/admin/apikey/rotate` 把新 Key 写进 `backend_src/.env` 并立即生效，但 `run_local.py:405` 每次启动都用环境变量 `MUSE2API_KEY=<local_config 的旧 Key>` 覆盖它 → **重启后 Key 悄悄回退到旧值**。

### 🟠 P1-4：Key 明文写进 HTML 文件

`run_local.py:278 prepare_web_index()` 把 API Key 永久写进 `web/index.html:218`。且注入只做一次（有 `[Local Auto-Init]` 标记保护），Key 换了前端不更新。目录一旦被复制/分享，Key 一起泄露。

### 🟡 P2 其它

| # | 问题 | 位置 |
|---|---|---|
| P2-1 | `MUSE2API_REPO` 仓库地址写错 | `run_local.py:37` |
| P2-2 | `allow_origins=["*"]` 硬编码，`CFG.cors_origins` 形同虚设；孤儿实例又绑 `0.0.0.0`，管理页对局域网裸奔 | `app.py:72` |
| P2-3 | `/v1/media/{name}` 无鉴权（本地可接受，但 Key 泄露时视频全裸） | `app.py:1824` |
| P2-4 | ~~长视频（120s/240s/480s）容易撞 `queue_timeout=900s` 排队超时~~ **已修**：改走自动分段，逐段独立排队 | `app.py:93` |
| P2-5 | ~~`_condense_video_script` 在 `dur>=30` 时直接返回原文不压缩~~ **已修**：长剧本先由 `longvideo.plan_segments` 切段，每段独立构造提示词 | `app.py:842` |
| P2-6 | `run_local.py` 无 `--cdp-port` 参数，CDP 端口硬编码 19210 | `run_local.py:42` |
| P2-7 | 启动健康检查只探 `/v1/models`，探不出"浏览器起不来" | `run_local.py:431` |
| P2-8 | `media/Backs/` 里有手工救援的素材（`rescued_video.mp4`、`extracted_frame0.jpg`、`tang_xiansheng_30s.mp4`），是人工调试痕迹，会被 `/admin/media` 列出来 | `backend_src/data/media/Backs/` |
| P2-9 | `data/` 被 `.gitignore` 忽略，但 `backend_src/data/` 不在其管辖范围内的完整说明缺失 | `.gitignore` |

---

## 6. 🔴 危险操作红线

| 操作 | 后果 |
|---|---|
| 在管理页点「一键在线升级并重启」 | **本地改过的 `backend_src/*.py` 被 GitHub 版本静默覆盖** |
| 调用 `/admin/repo/push` | 把本地代码推到 GitHub 远端 |
| 删除 `backend_src/data/accounts.json` | 账号池清空，要重新导号 |
| 删除 `data/profiles/generate/` | Chrome 会话 profile 丢失（导的号还在，但需重新预热） |
| 同时起两个 `run_local.py` | 见 P0-1，直接造成数据互相覆盖 |
| 删掉 `backend_src/` 想让它重拉 | 会因为 P2-1 拉失败（仓库地址错） |

---

## 7. 已验证可用的操作方法

```bash
cd /Users/ttnk/Desktop/muse-video-installer

./start_local.sh                      # 启动（双服务）
./start_local.sh --open-browser       # 启动并自动开网页
./start_local.sh --web-port 8080 --api-port 18600   # 换端口
python3 run_local.py --import-account # 导 muse.ai 账号

# 前端 http://127.0.0.1:8090/     管理页 http://127.0.0.1:18610/admin
# API   http://127.0.0.1:18610/v1  Key 见 data/local_config.json
```

停止：在运行 `run_local.py` 的终端按 `Ctrl + C`（会 terminate 子进程 + 关 web 服务）。
**注意：这只管得到自己起的子进程，管不到 P0-1 里那个孤儿 39690。**

---

## 8. 当前数据快照（2026-10-02 19:03）

**账号池** `backend_src/data/accounts.json` — 1 个账号

| 字段 | 值 |
|---|---|
| id / label | `8e80811045dd` |
| cookie 条数 | 9 |
| ok | `true` |
| note | `会话有效 · 自动保活 (VM: RUNNING)` |
| use_count | 14 |
| expires_at | 1791111510（≈ 2026-10-04） |

**任务** `backend_src/data/tasks.json` — 14 条，全部 video：8 completed / 6 failed

**媒体** `backend_src/data/media/` — 3 个 mp4（3.3M / 4.7M / 11M）+ `Backs/` 目录

**磁盘**：`backend_src/data/media` 51M · `data/profiles` 122M

---

## 9. 交接时未做的事（明确声明）

- **没有修改任何代码**
- **没有运行过任何生成任务**（只读了数据文件和日志）
- **没有清理 P0-1 的孤儿进程**（未获授权，未动现场）
- `web/index.html` 里的 `[Local Auto-Init]` 注入块是历史遗留产物，本记录未改动它

---

## 10. 后续变更（2026-10-02 晚，接手后已完成）

交班后第一件事是修「长视频必然失败」这条主线，三个 commit：

| commit | 内容 |
|---|---|
| `8178fd3` | 新增 `backend_src/longvideo.py`（分切 + 合成）与 `tests/test_longvideo_split.py` |
| `5049c19` | `app.py` 接入分段编排；修 deadline 守卫吞掉真实错误；`config.py` 加 7 个可调项 |
| `bf4b04a` | 前端分段展示 + `admin.html` 徽章 + ffmpeg 探测 + 修 `MUSE2API_REPO` |

**核心结论：muse.ai 网页端单次最多只能出 30 秒**（ffprobe 扫过全部历史产出：
5/10/30 秒，720x1280）。原来的 `_VIDEO_DURATIONS` 白名单是 app 自己编的。
现在 `duration > 30` 会自动拆成多个 <=30s 的短片依次生成，再用 ffmpeg 合成。

### 新增文件

| 文件 | 作用 |
|---|---|
| `backend_src/longvideo.py` | 分切（角色基底提取 / 三级解析 / DP 装箱）+ ffmpeg 合成。**纯逻辑，不 import app/store/engine** |
| `backend_src/tests/test_longvideo_split.py` | 54 项断言，离线零额度 |
| `backend_src/tests/test_longvideo_flow.py` | 54 项断言，编排层，猴补 `_run_generation` |
| `web/tests/showtask.test.js` | 19 项断言，前端渲染（需 node，零依赖） |

### 改动的既有文件

`app.py`（时长白名单双档化 / `create_video` 分段分支 / `_drive_long_video` /
`get_video` stalled 阈值 / `_startup` 清理 video 残留 / `build_video_prompt` 新参数）、
`config.py`（7 个 `MUSE2API_*`）、`web/index.html`、`backend_src/admin.html`、
`backend_src/Dockerfile`、`run_local.py`。

### 接手前必读的新增不变量

1. **driver 绝不能作为 SCHED job 提交**。`_drive_long_video` 用
   `threading.Thread` + 逐段 `SCHED.run_sync`，段间释放浏览器。
   若整条长视频占着一个 job，10 段会独占 `GEN_LOCK` 33~62 分钟，
   把 chat/image/短视频全堵死。
2. **driver 也不能写成 await 链**。`run_sync` 是阻塞的，写成 await 会卡死
   FastAPI 事件循环。
3. **ffmpeg 列表必须写临时文件**。本机 ffmpeg 9.0.1 的 `-i -` 会被解析成 `fd:`
   协议，`pipe:0` 被协议白名单拦，加 `-protocol_whitelist` 也不行。
4. **合成片名不能含 `/`**。`app.get_media` 显式拒绝含分隔符的名字。
5. **合成失败不算任务失败**。段落都生成成功了就是成功，只把原因写进
   `result.merge.reason`，由前端渲染分段列表。
6. `get_video` 的 stalled 阈值对长视频是 `seg_queue_timeout + 180`，
   不是 120 秒 —— 单段实测就要 371 秒。

### 尚未验证的部分

**只做了离线验证，一次都没真的调用过 muse.ai 分段生成。**
`test_longvideo_flow.py` 猴补掉了 `_run_generation`，ffmpeg 合成只用历史
视频验证过（10s+10s→20s 快路径、1280x720+720x1280→720x1280 稳路径）。
真实端到端（duration=240 跑完 10 段）**会消耗大量额度**，
且预计耗时 33~62 分钟，需要用户授权后再做。
