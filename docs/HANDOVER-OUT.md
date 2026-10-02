# 交班记录（Muse 视频工作台 · 本地版）

> 记录时间：2026-10-03
> 记录人：Claude Code（代码通读 + 实测核实，**未修改任何代码**）
> 交接对象：继续修改完善的人 / 会话
> 上一版：2026-10-02（旧版要点归档在 §12）

---

## 0. 一句话概括

把 **muse.ai 网页免费账号** 的对话 / 生图 / 生视频额度，用 CDP 操控真实 Chrome 反代成 **OpenAI 兼容 API**，配一个本地网页工作台。服务器版（Docker/systemd）被改造成"下载即用"的本地版。

**当前真实状态：短视频可用；长视频功能代码已写完，但从未成功生成过一段。** 这是本项目当前最重要的一句话，详见 §4。

---

## 1. 血缘关系

| 角色 | 仓库 | 说明 |
|---|---|---|
| **本目录来源** | `yys9253462-gif/muse-video-installer` | 服务器一键安装器（`install.sh` 104KB、`test-install.sh` 30KB） |
| **真正的服务端** | `czg86389-hub/muse2api` | `backend_src/` 的内容，也是**在线升级的数据源** |

`run_local.py:37` 的 `MUSE2API_REPO` 已于 2026-10-02 修正为 `czg86389-hub/muse2api`。

⚠️ **注意方向不对称**：本地版是从 installer 改的，但**在线升级拉的是 `muse2api`**，两者不是同一个仓库。升级只覆盖 `backend_src/`，永远碰不到 `run_local.py` / `web/`。

---

## 2. 目录地图

### 2.1 本地化新增层（改代码主要动这里）

| 文件 | 行数 | 职责 |
|---|---|---|
| `run_local.py` | 491 | **主启动器**。建 venv、探浏览器、拉源码、注入前端配置、起 web + 后端双服务、`--import-account` |
| `web/index.html` | 504 | 文生视频工作台（提示词/时长/画幅/首帧图 → 轮询任务 → 播视频 + 分段列表） |
| `start_local.sh` / `.bat` | 22 / 14 | 挑一个能用的 Python 3 再 exec `run_local.py` |
| `README_LOCAL.md` | 74 | 本地使用说明 |
| `data/local_config.json` | — | 本地密钥与端口（**但见 §5.1，它不是唯一事实来源**） |
| `data/profiles/generate/` | 122M | 无头 Chrome 的 user-data-dir |
| `docs/` | — | 本文件 + `HANDOVER-IN.md` |

### 2.2 上游原样层（`backend_src/`）

| 文件 | 行数 | 职责 |
|---|---|---|
| `app.py` | 3005 | FastAPI 全部路由、OpenAI 兼容层、提示词构造、账号池、**GitHub 在线升级 + git push** |
| `engine.py` | 1200 | CDP 驱动 muse.ai 网页生图/生视频/对话 + 续签保活 |
| `longvideo.py` | 699 | 长视频分切（基底提取/三级解析/DP 装箱）+ ffmpeg 合成。**纯逻辑，零网络** |
| `scheduler.py` | 331 | 单 worker FIFO 生成队列 |
| `cdp.py` | 181 | 极简 CDP 客户端（单读循环线程防死锁） |
| `store.py` | 302 | 账号池 / 任务 JSON 原子落盘 |
| `config.py` | 170 | 配置走 `MUSE2API_*` 环境变量 + `.env` |
| `admin.html` | 1475 | 账号池管理面板 |
| `tests/` | 6 个 | 4 个上游自带 + 2 个长视频离线回归 |
| `extension/` | — | Chrome 扩展（点一下把 Cookie 同步到本地服务） |

### 2.3 纯死代码（本地版不经过）

`install.sh`、`test-install.sh`、`Dockerfile`、`docker-compose.yml`、`deploy/` —— `run_local.py` 完全不引用它们。
留着只是因为它们是"血缘证据"，且**升级功能会以「上游」名义覆盖 `backend_src/`**，删了不影响本地运行。

---

## 3. 运行原理（改代码前必须理解）

### 3.1 核心机制：不是调 API，是操控网页

```
注入 Cookie → 导航 muse.ai/thread/new → 等 React 水合 + WebSocket 就绪
   → 真实鼠标点击聚焦 textarea → React 原型 setter 灌提示词
   → 等 Send 按钮渲染出来再点（按钮在 = React 真收到输入了）
   → 轮询 [data-testid^="hatch-chat-attachment-presentation-"] 抓 img/video 的 blob src
   → 在页面里 fetch(blobUrl) → ArrayBuffer → base64 → 回传 Python 落盘
   → 失败则退化为点"下载"按钮，从 downloads/ 目录捞文件
```

关键函数坐标（`backend_src/engine.py`）：

| 函数 | 行 | 作用 |
|---|---|---|
| `_apply_cookies()` | 212 | 先 `Network.clearBrowserCookies` 清空保证账号隔离 |
| `reset_thread()` | 288 | 每次生成前确保干净会话（见下方说明） |
| `_wait_ws_ready()` | 267 | 等 `readyState=complete` + 有 textarea + hydration=hydrated + 无 "Connecting..." |
| `_ATT_JS` | 520 | 附件抓取 JS（**视频封面 vs 视频源严格区分**，防误取封面图） |
| `_send()` | 575 | **双条件校验**：文字真进了输入框 **且** Send 按钮真被 React 渲染出来 |
| `_wait_attachment()` | 682 | 轮询 + 进度 `min(95, 25+elapsed*0.6)`，**绝不伪造进度** |
| `_EXTRACT_JS` | 775 | 页面内 fetch + btoa 取字节 |
| `_download_fallback()` | 961 | 只点**选中结果**的下载按钮，绝不点全局按钮 |
| `renew_session_http()` | 147 | GET `/api/session` 续签 + POST `/api/hatch/vm/wake` 唤醒 VM |
| `generate()` | 1110 | 主流程编排 |

**`reset_thread` 值得单独说**（`engine.py:288-345`）：它会关弹窗、统计**主对话区**（不含左侧 thread 列表）的气泡数，若 `hasStop || hasStuck || hasAtts` 或（视频路径）`pathname !== '/thread/new' || bubbleCount > 0` 就强制 `Page.navigate` 到干净的 `/thread/new`，10s 内没回来就抛错让上层切号重试。
即：**视频生成每次都在干净会话里跑，上下文不会跨段累积**。这一点很重要——它排除了"模型看到历史对话"这个解释（见 §4）。

### 3.2 账号存活模型

- 决定生死的 cookie 是 **`hatch_vml`**（约 2 天寿命，不因使用而延长）
- 保活 = 直接打 `/api/session` 触发 Meta 网关重新签发 + 唤醒云端 VM
- **实测**：每 15 分钟保活一次，`expires_at` 每次只 +900s（不是 +48h）。
  也就是账号实际寿命绑在"保活不能停超过 15 分钟"上。`store.py:29 VML_TTL = 2*86400` 是拿不到 expires 时的兜底估算。

### 3.3 并发模型（`scheduler.py`）

**只有 1 个浏览器**，所以 chat / image / video 三条路径**必须串行**。

原设计（已废弃）：每请求起线程抢 `GEN_LOCK`（裸 `threading.Lock`）→ 不保证 FIFO、会饿死、会死等。

现设计：
- `queue.Queue` + **唯一 worker 线程** → 天然 FIFO
- 出队前查 `deadline`，排队超 `queue_timeout` 直接判 `timeout`，不浪费浏览器时间
- `run_timeout = video_timeout + 300` 的看门狗 → 超时则 `engine.stop()` 强杀浏览器打断
- 三个入口：`submit`（异步）/ `run_sync`（已在独立线程里）/ `run`（管理页旁路探活，会插队）

### 3.4 数据落盘位置（**分散在两处**）

| 内容 | 路径 | 写入方 |
|---|---|---|
| 本地密钥 / 端口 | `data/local_config.json` | `run_local.py` |
| 浏览器 profile | `data/profiles/generate/` | Chrome |
| 账号池 | `backend_src/data/accounts.json` | `store.py` |
| 任务记录 | `backend_src/data/tasks.json` | `store.py` |
| 生成的视频 | `backend_src/data/media/` | `engine.py` |
| 压缩前的长剧本备份 | `backend_src/data/scripts/` | `app.py` |
| 服务端 Key（**另一份**） | `backend_src/.env` | `app.py:_persist_env` |

`tasks.json` 顶层是 `{task_id: {...}}` 的 dict，**不是** `{"tasks": [...]}`。统计脚本别踩这个。

---

## 4. 🔴 当前最重要的发现：长视频从未成功过

这是本次通读+实测最有价值的结论，**优先级高于下面所有问题**。

### 4.1 数据快照（2026-10-03 实测 `backend_src/data/tasks.json`）

29 个任务，全部 video：**12 completed / 17 failed**。

其中带 `segment_total` 字段的长视频任务共 **3 个，全部 failed，且 `segments` 长度为 0**：

| task id | segment_total | 完成段数 | 失败原因 |
|---|---|---|---|
| `task_5f7f1ab230ea49bf904c` | 10 | **0** | `模型未生成媒体，仅返回文本: 素材在同一目录的三个原始片段里，需要微调某一镜再告诉我。第 2/10 段的内容发我我就接着拍。` |
| `task_6738a6184df4456699cc` | 1 | **0** | 服务重启中断（已完成 0/1 段） |
| `task_60fd28306c9147fd8751` | 2 | **0** | 服务重启中断（已完成 0/2 段） |

### 4.2 失败率高的真正原因

17 个 failed 里，**至少 6 个是同一个错误**：`模型未生成媒体，仅返回文本: ...`

典型内容：
- `规格实测：720×1280 严格 9:16 竖屏，无黑边，时长正好 30 秒，带全程音频。结构是三段连起来的...`
- `我已经把活派给后台任务：28 段全部纯文本全新生成...`
- `成品规格：720×1280 竖屏、时长正好 30.000000 秒。分段结构：0–10s｜...`

**这不是超时，不是 bug，是 muse.ai 那边把它当成了聊天对话在回答。**
模型输出的是"我打算怎么拍"的长文本计划，而不是一个视频。

### 4.3 根因分析（已排除一个错误假设）

我最初怀疑是"上下文累积导致模型以为在做多轮协作"——因为错误文本里出现了"第 2/10 段的内容发我"。

**这个假设被排除了**：`engine.reset_thread()`（`engine.py:288`）对视频路径每次都强制导航到干净的 `/thread/new`，上下文不会跨段累积。

真正的根因是**提示词本身**。`longvideo.py:522 build_segment_prompt()` 的段头长这样：

```
【长视频第 2/10 段 · 本段约 24 秒 · 全片约 240 秒】
这是同一部长片的一段，必须与其它段保持人物、服装、场景、光线与画风完全一致；
请只演绎本段内容，不要试图一次拍完全片。
```

问题有三层：
1. **"第 2/10 段"这个说法本身就是对话语义**。模型读到"第 2 段"，合理推断是"第 1 段已经聊过了"，于是反问"第 2 段的内容发我"。
2. **"必须与其它段保持一致"隐含"我看过其它段"**，但干净会话里它根本没看过其它段，产生认知矛盾。
3. **长段头 + 角色基底 + 长段体**拼在一起非常像"项目需求文档"，muse.ai 判定为"这是一次内容策划请求"而非"生成视频"。

短视频不触发这个问题，是因为短视频提示词（`app.py:979 build_video_prompt`）没有段号、没有"其它段"这种跨段指代。

### 4.4 结论

**长视频分段的离线逻辑（分切/装箱/合成）经过 54 项断言验证是对的；出问题的是"怎么跟 muse.ai 说话"。**
这条主线值得单独立项，详见 `HANDOVER-IN.md` §4 第三批 B7。

---

## 5. 已核实的问题清单

> 下面每一条我都在代码里复核过行号和逻辑，不是推测。
> 上一版记录里的 P0-1（两个后端实例抢同一端口）**本次实测已不存在**，见 §8。

### 🔴 P0-A：长视频从未成功生成（见 §4）

### 🔴 P0-B：一键升级会静默覆盖本地长视频增量

`app.py:2870-2887` `_upgrade_from_github_sync()` 从 `codeload.github.com/czg86389-hub/muse2api` 拉 tarball，解包覆盖 `backend_src/` 下**所有文件**，只保护 3 个：

```python
protected_files = {".env", "data/accounts.json", "data/tasks.json"}
```

**`longvideo.py`（699 行，本次全部长视频功能）和 `app.py` 的分段编排都不在保护名单里，点一次按钮全没了。**
无确认弹窗（`admin.html` 的按钮直接 POST）。上一版记录说"只保护 3 个文件"是对的，但没意识到它会杀掉整个长视频模块。

### 🔴 P0-C：`/admin/repo/push` 能把本地代码推到 GitHub

`app.py:2934`，`TRACKED_REPO_PATHS` 包含 `app.py` / `engine.py` 等核心代码，`git push origin HEAD:main` 直推 main 分支。
**它和 `/admin/update/upgrade` 共用同一个 `Depends(auth)`**——也就是说，只要知道 Bearer Key 就能推代码，没有维护者校验。

### 🔴 P0-D：API Key 明文硬编码在 HTML 里

`web/index.html:218`：`if (!cur.key) { cur.key = 'm2a_1dcf48ae...'; }`
`run_local.py:278 prepare_web_index()` 把它永久写进文件，且注入只做一次（有 `[Local Auto-Init]` 标记保护），Key 换了前端不更新。
叠加 `run_local.py:320` 的 `ThreadedHTTPServer(("0.0.0.0", port))` —— **web 服务绑 0.0.0.0，局域网内知道 Key 的机器可以直接用你的额度**。

### 🟠 P1-A：没有单实例保护

`run_local.py` 只在**首次生成配置**时用 `is_port_in_use` 找空闲端口（`run_local.py:121-149`）；配置已存在就直接复用 `local_config.json` 里的端口，**启动时不复检**。没有 pidfile、没有文件锁。
后果：macOS 允许 `0.0.0.0:P` 与 `127.0.0.1:P` 同时绑定，第二个实例会**静默起来**，两个进程共享同一份 `tasks.json`（`store.py` 的锁是进程内的）和同一个 Chrome → 数据互相覆盖 + 浏览器串话。

### 🟠 P1-B：Key 轮换会静默回退

`/admin/apikey/rotate` 把新 Key 写进 `backend_src/.env` 并立即生效，但 `run_local.py` 每次启动都用环境变量 `MUSE2API_KEY=<local_config 的旧 Key>` 覆盖它 → **重启后 Key 悄悄回退**。
实测两份 Key 确实不一致：`local_config.json` = `m2a_1dcf48…`，`backend_src/.env` = `m2a_2cd0fa…`。

### 🟠 P1-C：CORS 形同虚设

`app.py:71` 算出了 `_origins`，`app.py:75` 却硬编码 `allow_origins=["*"]`。`CFG.cors_origins` 配了也没用。
叠加 P0-D 的 0.0.0.0 绑定和明文 Key，管理页对局域网裸奔。

### 🟠 P1-D：`/v1/media/{name}` 无鉴权

`app.py:2054 get_media`。本地可接受，但配合 P0-D，Key 一泄露全部视频裸奔。

### 🟠 P1-E：`engine._log` 文件句柄泄漏

`engine.py:95` `open(..., "ab", buffering=0)` 永不关闭；`engine.py:110 engine.stop()` 只关 CDP 和 proc，不关 log。
每次 stop+start 泄漏一个 fd。看门狗超时和切号都会触发 stop。

### 🟠 P1-F：`plan_segments` 在事件循环里同步跑

`app.py:1648` 是 `async def` 但直接调阻塞的 `plan_segments`。`longvideo.py:381 _pack_min_bins` 是 O(n²)。
正常剧本（9858 字 → 8 个单元）无所谓，但**超长输入会卡死整个事件循环**。建议加输入长度上限或 `asyncio.to_thread`。

### 🟡 P2 级（已核实）

| # | 问题 | 位置 |
|---|---|---|
| P2-1 | 前端漏判 `stalled` 状态。`index.html:398/405` 只认 completed/failed，后端 `app.py:1750` 会产出 `stalled` → **既不停轮询也不报错，永远显示"生成中 X%"** | `web/index.html` |
| P2-2 | `renderHist`(`index.html:288-290`) 同样漏 `stalled`，徽章显示「生成中」 | `web/index.html` |
| P2-3 | quota 解析只认英文。`engine.py:505` 只匹配 `Weekly limit resets on`，`engine.py:517` 只认 `Never expires`；`engine.py:480` 等待条件是 `"Usage" in txt or "used" in txt`。**中文界面下 `found` 恒为 False，且每次白等 7.2 秒**。上个 commit `c3ff151` 只改了按钮中文化，解析层没跟上 | `engine.py` |
| P2-4 | `build_segment_prompt` 截断时静默丢段尾（`longvideo.py:539-541`），不像基底那样标 `…（已截断）` | `longvideo.py` |
| P2-5 | 账号 `ok` 语义混乱。生成失败也 `store.mark(ok=True)`（`app.py:1207`），而 `store.py:167` 用 `ok is not False` 过滤 → **配额耗尽的账号会无限次被重选**，每次浪费一整轮生成时间（实测 371s/段） | `app.py` / `store.py` |
| P2-6 | 账号 `use_count` 已到 54，quota 实际已很低，但没有任何降级/告警机制 | `store.py` |
| P2-7 | `_drive_long_video` 的 `budget` 形同虚设（`app.py:1530`）：只在段循环**开头**检查，单段最长可耗 `queue_timeout+video_timeout`=2400s，最坏超支到 9600s | `app.py` |
| P2-8 | `_wait_attachment` 的纯文本拒答判定 `txt_stable>=15`（≈9s 连续不变）偏长，模型"思考"中途停顿可能被误杀 | `engine.py:761-770` |
| P2-9 | base64 走 CDP 传视频有 OOM/卡顿风险（`engine.py:778-797`），30 秒视频 base64 后 3~7MB 一次性塞进 `Runtime.evaluate` | `engine.py` |
| P2-10 | `merge_segments` 的 `-c copy` 快路径不校验起始 PTS（`longvideo.py:657`），上游改编码会静默劣化 | `longvideo.py` |
| P2-11 | `engine.py:224` `exp_val` 阈值 `> now + 3600` 太宽松：1 小时内过期的 cookie 会被改写成 7 天后，**用必然失效的会话却不报错** | `engine.py` |
| P2-12 | `media/` 只增不减无 GC。长视频场景 10 段 × 重试，每次几十 MB | `config.py` |
| P2-13 | `VideoRequest.timeout` 无 Field 约束（`app.py:498`，`ImageRequest` 在 483 有），可传 999999 使 deadline 永远不触发 | `app.py` |
| P2-14 | 视频请求头写 30s 上限，但 muse.ai 网页端实测**最多只能出 30 秒**（ffprobe 扫过全部历史产出：5/10/30s，720×1280）。app 自己的白名单是猜的 | `app.py` |
| P2-15 | 死代码：`cdp.pump()`（参数全摆设，无调用点）、`cdp.set_event_handler`（无调用者）、`scheduler.get_scheduler()`（`app.py:95` 直接实例化）、`scheduler._Job.__post_init__`（空函数体）、`scheduler` 的 `order=True`（从不排序） | 多处 |

---

## 6. 关键不变量（改代码时不能破坏）

1. **浏览器只有一个** —— 任何绕过 `SCHED` 直接抢 `GEN_LOCK` 的新代码路径都是隐患。`app.py` 的 `_run_generation()` 假定调用方已持锁。
2. **`GEN_LOCK` 不可重入** —— 调度器 worker 已持锁后再抢会永久自锁（历史上真踩过）。
3. **进度不许伪造** —— 已删掉旧的 `min(92, 20+elapsed*1.1)`，改为返回真实 `progress` + `idle_seconds`，>120s 无进展标 `stalled`。别改回去。
4. **cookie 有效期只信实测 expires 或导入锚点** —— 不用"检测成功"虚推 48h（`store.py:188` 有注释说明）。
5. **`_sync_cookies()` 绝不能让生成失败** —— 生成已经成功了，同步只是锦上添花。
6. **账号切换重试最多 2 次** —— 且已经 yield 出内容后不再重试（避免重复输出）。
7. **`CDP.send()` 绝不无限阻塞** —— 业务线程只 `wait` 结果，读循环线程独占 `recv()`。这个单读循环设计真正解决了两个具体死锁，是本项目最扎实的部分。
8. **driver 绝不能作为 SCHED job 提交**。`_drive_long_video` 用 `threading.Thread` + 逐段 `SCHED.run_sync`，段间释放浏览器。整条长视频占一个 job 的话，10 段会独占锁 33~62 分钟。
9. **driver 也不能写成 await 链**。`run_sync` 是阻塞的，写成 await 会卡死 FastAPI 事件循环。
10. **ffmpeg 列表必须写临时文件**。本机 ffmpeg 9.0.1 的 `-i -` 会被解析成 `fd:` 协议，`pipe:0` 被协议白名单拦，加 `-protocol_whitelist` 也不行。
11. **合成片名不能含 `/`**。`app.get_media` 显式拒绝含分隔符的名字。
12. **合成失败不算任务失败**。段落都生成成功了就是成功，只把原因写进 `result.merge.reason`。

---

## 7. 🔴 危险操作红线

| 操作 | 后果 |
|---|---|
| 在管理页点「一键在线升级并重启」 | **长视频模块（`longvideo.py` 699 行 + app.py 分段编排）被 GitHub 版本静默覆盖** |
| 调用 `/admin/repo/push` | 把本地代码推到 GitHub main 分支（只需 Bearer Key） |
| 删除 `backend_src/data/accounts.json` | 账号池清空，重新导号要人工登录 muse.ai |
| 删除 `data/profiles/generate/` | Chrome profile 丢失（导的号还在，但要重新预热） |
| 同时起两个 `run_local.py` | 见 P1-A，数据互相覆盖 + 浏览器串话，**且不报错** |
| 删掉 `backend_src/` 想让它重拉 | 会因仓库地址问题拉失败（已修 A5，但删之前仍要确认） |
| 未告知用户就消耗额度做冒烟测试 | 视频生成直接吃 muse.ai 账号配额（当前账号 use_count 已 54） |

---

## 8. 当前运行现场（2026-10-03 实测）

**已干净**（上一版的 P0-1 双实例问题已消失）：

| PID | 角色 |
|---|---|
| 69398 | `run_local.py --open-browser`（PPID 15980 = 你的终端） |
| 69403 | uvicorn `--host 127.0.0.1 --port 18610`（run_local 的子进程） |
| 21773 | Chrome `--headless=new --remote-debugging-port=19210`，**PPID=1，16:07 起，无人持有** |

- web 服务 `*:8090` —— ⚠️ **绑的是 0.0.0.0，不是 127.0.0.1**，局域网可访问（见 P0-D）
- ffmpeg 9.0.1 在 `/opt/homebrew/bin/ffmpeg`
- git 工作区 clean，7 个 commit（基线 `b10626b` → 最新 `c3ff151`）

**Chrome 21773 仍是 PPID=1 的孤儿**（早于两个后端启动）。当前无害（engine 会复用），但"没人持有"这点没变。

### 数据快照

- **账号池** 1 个：`8e80811045dd`，ok=true，**use_count=54**，cookies=9，expires_at=1791127042（≈ 2026-10-05）
- **任务** 29 个：12 completed / 17 failed，全部 video
- **磁盘**：`backend_src/data/media` 51M，`data/profiles` 122M

---

## 9. 已验证可用的操作方法

```bash
cd /Users/ttnk/Desktop/muse-video-installer

./start_local.sh                      # 启动（双服务）
./start_local.sh --open-browser       # 启动并自动开网页
./start_local.sh --web-port 8080 --api-port 18600   # 换端口
python3 run_local.py --import-account # 导 muse.ai 账号

# 前端 http://127.0.0.1:8090/     管理页 http://127.0.0.1:18610/admin
# API   http://127.0.0.1:18610/v1
```

停止：在运行 `run_local.py` 的终端按 `Ctrl + C`。

### 离线测试（全部零额度，**本次已全部实跑通过**）

```bash
cd /Users/ttnk/Desktop/muse-video-installer
.venv_local/bin/python backend_src/tests/test_longvideo_split.py   # PASS
.venv_local/bin/python backend_src/tests/test_longvideo_flow.py    # PASS
node web/tests/showtask.test.js                                     # PASS
```

> 注意：这 3 个脚本在 `backend_src/tests/` 下**不是 pytest 风格**（直接 `python` 跑、自己 assert、自己打印 PASS），
> 所以 `python -m pytest tests/` 会收集不到它们。上游那 4 个（`test_vm_wait` / `test_async_images` /
> `test_session_health` / `test_media_selection`）才是 pytest 风格，需 `pip install pytest`。

### 语法自检

```bash
.venv_local/bin/python -c "import ast
for f in ['run_local.py','backend_src/app.py','backend_src/engine.py','backend_src/scheduler.py','backend_src/store.py','backend_src/cdp.py','backend_src/config.py','backend_src/longvideo.py']:
    ast.parse(open(f,encoding='utf-8').read()); print('OK',f)"
```

### 日志位置

- 本地版后端日志在**启动 `run_local.py` 的那个终端**（未重定向）
- `backend.log` 是**旧孤儿进程**的日志，跟当前实例无关
- Chrome 自身日志：`backend_src/data/chromium.log`
- 生成失败现场快照：`backend_src/data/debug-*.json`（**不轮转，会无限累积**）

---

## 10. 交接时明确声明：没做的事

- **没有修改任何代码**（本次只读 + 跑离线测试 + 读数据文件）
- **没有运行任何生成任务**，因此**没有消耗任何额度**
- **没有点过升级/推送按钮**，没有动现场进程
- 长视频**端到端从未验证过**（§4），240 秒跑完 10 段预计耗时 33~62 分钟且大量消耗额度，需用户授权
- 用户在本轮提供了一份 240 秒长视频的角色基底（唐进生，中英双版 + 通用负向提示词）和分镜提示词表，**但只贴到「镜头 01」就中断了，剩余镜头未提供**。这份基底正好能被 `longvideo.py:102` 的 `_BASE_RE`（匹配 `character base` / `negative prompt` / `master consistency`）识别，是可直接复用的输入格式

---

## 11. 与上一版（2026-10-02）记录的差异

| 项 | 上一版 | 本次实测 |
|---|---|---|
| 双后端实例（P0-1） | 🔴 存在（孤儿 39690） | ✅ **已消失**，只剩一套 |
| git 版本控制 | 说"不是 git 仓库" | ✅ **已有 git**，7 个 commit，工作区 clean |
| 长视频 | 刚写完，只做过离线验证 | ⚠️ **有 3 条真实失败任务，0 段成功**，根因已定位到提示词（§4.3） |
| Chrome 孤儿 | PPID=1 | 仍是 PPID=1（21773） |
| Key 两份不一致 | 🔴 存在 | 🔴 **仍存在**（`m2a_1dcf48` vs `m2a_2cd0fa`） |
| 升级覆盖风险 | 🟠 P1-1「覆盖本地魔改」 | 🔴 升为 **P0-B**，因为现在本地魔改=整个长视频模块 |
| quota 中文解析 | 未提及 | 🟡 **P2-3**，上个 commit 只修了一半 |

---

## 12. 下一棒建议从这里开始

1. **先做安全护栏**（P0-B/C/D + P1-A/B/C）——在动长视频之前把"一键毁掉所有改动"的口子堵上。成本很低。
2. **再攻长视频主线**（§4）——这是用户真正想要的功能，而它现在 0 段成功。核心是重写 `build_segment_prompt` 的段头措辞。
3. 用户那份唐进生基底可以拿来当**第一条真实验证用例**（但需要授权额度）。

具体清单和验收标准见 `HANDOVER-IN.md`。
