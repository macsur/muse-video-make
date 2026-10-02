# 接班记录（Muse 视频工作台 · 本地版）

> 接班时间：2026-10-03
> 对应交班记录：[`HANDOVER-OUT.md`](./HANDOVER-OUT.md)
> 状态：**代码通读完成，零改动，等指令开工**

---

## 1. 接班确认清单

逐条打勾确认，确认完再动代码：

- [ ] 我已读完 `docs/HANDOVER-OUT.md`，理解了"CDP 操控真实网页"这个核心机制
- [ ] 我知道浏览器只有 1 个、chat/image/video 必须串行，改动不能绕过 `SCHED`
- [ ] 我知道**长视频功能从未成功生成过一段**（3 条任务全部 0 段失败），根因是提示词而非逻辑（§4）
- [ ] 我知道**本地魔改 `backend_src/*.py` 会被「一键在线升级」静默覆盖**（P0-B），而且现在本地魔改 = 整个长视频模块
- [ ] 我知道 `/admin/repo/push` 只需 Bearer Key 就能把代码推到 GitHub（P0-C）
- [ ] 我知道 **API Key 明文写在 `web/index.html:218`**，而 web 服务绑 `0.0.0.0`（P0-D）
- [ ] 我知道密钥有两份且不一致：`data/local_config.json`（`m2a_1dcf48`）vs `backend_src/.env`（`m2a_2cd0fa`）
- [ ] 我知道数据落在**两个地方**：`data/`（密钥、profile）和 `backend_src/data/`（账号、任务、媒体）
- [ ] 我确认了本次任务范围：**该程序代码需要大量修改完善**（不是修一个 bug）
- [ ] 我知道跑生成任务会**消耗 muse.ai 额度**（当前账号 use_count 已 54），未经用户批准不冒烟

---

## 2. 阅读顺序

前两步必读：

| 顺序 | 文件 | 为什么 |
|---|---|---|
| 1️⃣ | `docs/HANDOVER-OUT.md` §3 运行原理 | 不懂 CDP 那套机制，改 engine 必翻车 |
| 2️⃣ | `docs/HANDOVER-OUT.md` §4 长视频失败分析 | **本次最重要**，不读会重复踩同一个坑 |
| 3️⃣ | `run_local.py`（491 行） | 本地化的全部逻辑都在这，改启动行为只动它 |
| 4️⃣ | `backend_src/longvideo.py`（699 行） | 长视频分切/基底/合成。**纯函数，有 54 项离线断言保护，改起来最安全** |
| 5️⃣ | `backend_src/engine.py`（1200 行） | 生成逻辑主体，改这里要极度小心 |
| 6️⃣ | `backend_src/app.py`（3005 行） | 路由 + 提示词构造 + 管理接口 |
| 7️⃣ | `web/index.html`（504 行） | 前端工作台，功能需求常要前后端一起改 |
| 8️⃣ | `backend_src/scheduler.py` | 队列语义，改并发/超时必读 |

**可跳过**：`install.sh`（104KB）、`test-install.sh`、`Dockerfile`、`docker-compose.yml`、`deploy/` —— 本地版完全不经过（§2.3）。

---

## 3. 开工前必须先做的两件事

### 3.1 备份（现在有 git 了，但保险起见）

**上一版记录说"本地目录不是 git 仓库"——这已过时。** 现在有 7 个 commit，工作区 clean：

```
c3ff151 fix(长视频): 修复纯文本备用号重试导致直接退出，以及中文化界面的 quota 识别
0fb9d65 docs: 交班记录补 §10 变更说明，接班记录勾掉 B2/B6
bf4b04a feat(长视频): 前端分段展示、ffmpeg 探测，修 MUSE2API_REPO 错地址
5049c19 feat(长视频): 接入分段编排，修复超时报错掩盖真实原因
8178fd3 feat(长视频): 新增长视频自动分段模块与离线回归测试
b10626b chore: 建立本地版基线快照（改动前的原始状态）
```

所以**回滚点有了**，不必再手工 `cp -a` 备份。建议开工前先 `git tag baseline-2026-10-03` 打一个标签。

### 3.2 清场（否则测出来的结果不可信）

```bash
# 当前现场（2026-10-03 实测）
lsof -nP -iTCP -sTCP:LISTEN | grep -E "18610|8090|19210"
ps -eo pid,ppid,lstart,command | grep -E "run_local|uvicorn|19210" | grep -v grep
```

- ✅ 上一版的双后端实例问题**已消失**，当前只有一套：`run_local.py`(69398) → `uvicorn`(69403)
- ⚠️ **Chrome 21773 仍是 PPID=1 的孤儿**（16:07 起，没人持有）。当前无害（engine 复用它），要清就 `pkill -f "remote-debugging-port=19210"`
- ⚠️ **web 服务绑在 `*:8090`（0.0.0.0）不是 127.0.0.1**，局域网可访问。测试时注意别在公共 WiFi 下裸奔

---

## 4. 改造清单

> 每项动手前先确认用户是否要，别自作主张。

### 🔴 第一批：安全护栏（**建议先做这批**，成本低、风险高）

> 这批不改任何功能，只是把"能毁掉全部工作"的口子堵上。**在动长视频之前做完**，否则一次误点就白干。

| # | 事项 | 落点 | 验收标准 |
|---|---|---|---|
| A1 | 禁用一键升级 / repo push | `app.py:2916` `2926`、`2934` 加 `local_mode` 开关（读 `CFG` 或环境变量）；`admin.html` 隐藏按钮 | 本地启动时管理页**看不到**这两个按钮；直接 POST 返回明确错误 |
| A2 | 单实例保护 | `run_local.py` 加 pidfile + 文件锁 + 启动复检端口 | 已有实例在跑时再次 `./start_local.sh`，**复用并提示**，不静默起第二个 |
| A3 | Key 不再明文进 HTML | `run_local.py:278 prepare_web_index()` 改成启动时通过 `/admin/apikey` 动态下发 | `web/index.html` 里 `grep m2a_` 无结果；目录被复制不泄露 Key |
| A4 | Key 单一事实来源 | 统一到 `data/local_config.json`；`.env` 降级只读 | `/admin/apikey/rotate` 后**重启仍在生效** |
| A5 | web 绑 127.0.0.1 + CORS 收紧 | `run_local.py:320` 改 `127.0.0.1`；`app.py:75` 的 `allow_origins=["*"]` 改用已算好的 `_origins` | 局域网其他机器无法访问；`CFG.cors_origins` 配了真的生效 |
| A6 | `/v1/media` 鉴权 | `app.py:2054` | 无 Key 取媒体返回 401；前端播放路径同步适配 |

### 🟠 第二批：长视频主线（**用户真正想要的功能，现在 0 段成功**）

这是本次的核心工程。根因分析见 `HANDOVER-OUT.md` §4.3，简单说：**`build_segment_prompt` 的段头"【长视频第 2/10 段…】必须与其它段保持一致"是对话语义，模型以为在跟人协作，于是回一段"我打算怎么拍"的文本。**

| # | 事项 | 落点 | 验收标准 |
|---|---|---|---|
| B7 | **重写段头措辞**（最高优先） | `longvideo.py:522 build_segment_prompt()` | 段头不再出现"第 N/M 段""与其它段保持一致"这类跨段指代；改成"这是一段独立的视频生成请求"式表述。**先做这个，再谈别的** |
| B8 | 纯文本拒答要能自动纠正 | `app.py:1576` 附近，`_run_generation` 重试路径 | 模型返回纯文本时，自动用"更强硬的生成指令"重试 1 次（而不是直接换号或失败）。**这条可能是 B7 的兜底** |
| B9 | 单段失败可续传 | `app.py:1536` 循环 | 长视频失败后，已完成段能保留并**从断点续跑**，不用从头 10 段重来（现在重启就全丢） |
| B10 | 段级重试与降级 | `app.py:1536-1585` | 某段连挂 2 次时，可选「跳过该段继续」或「整片失败」，而不是无脑中断 |
| B11 | 输入长度上限 | `app.py:1648` `plan_segments` 是同步调用 + O(n²) | 超长输入不会卡死事件循环；加 `asyncio.to_thread` 或长度上限 |
| B12 | 段体截断要留痕 | `longvideo.py:539-541` | 截断时写进 `plan.notes`，前端能看到"本段剧本已截断" |
| B13 | `budget` 检查前移 | `app.py:1530` | 提交段任务**前**就检查 deadline 剩余时间够不够 `queue_timeout` |

### 🟡 第三批：正确性

| # | 事项 | 落点 | 验收标准 |
|---|---|---|---|
| C1 | 前端漏判 `stalled` | `web/index.html:398/405`、`288-290` | 任务 stalled 时前端**停轮询并明确告警**，不再永远显示"生成中 X%" |
| C2 | quota 中文解析补全 | `engine.py:505/517/480` | 中文界面下能读出 `weekly_reset` / `extra_expires` / `weekly_used_pct`，且不白等 7.2 秒 |
| C3 | 账号 `ok` 语义 | `app.py:1207` + `store.py:167` | 配额耗尽的账号能被排除，不会无限次重选（每次浪费 371s） |
| C4 | `engine._log` fd 泄漏 | `engine.py:95/110` | `stop()` 关掉 log；反复 stop/start 后 fd 数不增长 |
| C5 | `VideoRequest.timeout` 约束 | `app.py:498` | 加 `Field(ge=1, le=...)`，与 `ImageRequest:483` 一致 |
| C6 | `exp_val` 阈值收紧 | `engine.py:224` | 1 小时内过期的 cookie 不被改写成 7 天后 |
| C7 | 媒体 GC | `config.py:144` | `media/` 有清理策略（保留最近 N 个 / N MB） |
| C8 | 清理死代码 | `cdp.pump`、`cdp.set_event_handler`、`scheduler.get_scheduler`、`scheduler._Job.__post_init__` | 删掉或接上，别留误导性空壳 |
| C9 | `merge_segments` 默认片名撞名 | `longvideo.py:641` | 同一秒两个长视频不互相覆盖 |
| C10 | `debug-*.json` 不轮转 | `engine.py:1137/1146` | 加轮转或数量上限 |
| C11 | `media/Backs/` 人工素材 | `backend_src/data/media/Backs/` | 里面有 `rescued_video.mp4` / `extracted_frame0.jpg` 等手工救援素材，会被 `/admin/media` 列出来。**先问用户这些要不要留** |

### 🟢 第四批：功能（**请用户提需求**）

交接时未收集到具体功能需求。方向建议（供讨论，非承诺）：

- [ ] 批量视频任务 / 任务队列可视化
- [ ] **图生视频首帧自动提取/裁剪**（`media/Backs/extracted_frame0.jpg` 说明有人手工做过）
- [ ] 剧本模板系统（`data/scripts/` 已有备份机制，可往上搭）
- [ ] 多账号池配额看板
- [ ] 生成结果推送到网盘/对象存储
- [ ] 前端：任务历史跨浏览器同步（现在只在 localStorage）
- [ ] 前端：图片/对话模型入口（现在前端只做视频）

---

## 5. 验证方法

### 5.1 长视频专项（离线零额度，**本次已全部实跑通过**）

```bash
cd /Users/ttnk/Desktop/muse-video-installer
.venv_local/bin/python backend_src/tests/test_longvideo_split.py   # 54 项：分切/基底/装箱/合成降级
.venv_local/bin/python backend_src/tests/test_longvideo_flow.py    # 54 项：driver 编排，猴补 _run_generation
node web/tests/showtask.test.js                                     # 19 项：前端渲染，需 node
```

⚠️ 这 3 个**不是 pytest 风格**（直接 `python` 跑、自己 assert），`python -m pytest tests/` 收集不到它们。

### 5.2 上游自带的 pytest

```bash
cd /Users/ttnk/Desktop/muse-video-installer/backend_src
../.venv_local/bin/python -m pytest tests/ -v
# 现有：test_vm_wait / test_async_images / test_session_health / test_media_selection
```

### 5.3 语法自检（改完先跑这个）

见 `HANDOVER-OUT.md` §9。

### 5.4 冒烟（**会消耗额度，先问用户**）

```bash
K=$(python3 -c "import json;print(json.load(open('data/local_config.json'))['api_key'])")

curl -s -H "Authorization: Bearer $K" http://127.0.0.1:18610/healthz
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:18610/readyz

# 5 秒短视频
TID=$(curl -s -X POST http://127.0.0.1:18610/v1/videos \
  -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"prompt":"一只猫在草地上跑","duration":5,"size":"16:9"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -s -H "Authorization: Bearer $K" http://127.0.0.1:18610/v1/videos/$TID | python3 -m json.tool | head -30
```

**长视频端到端更贵**：240 秒 = 10 段，实测单段就要 371 秒，预计 33~62 分钟。**必须用户明确授权才做。**

### 5.5 查历史失败原因（本次定位根因就是这么做的）

```bash
python3 -c "
import json,collections
d=json.load(open('backend_src/data/tasks.json'))
ts=list(d.values())   # 注意：顶层是 {task_id: {...}}，不是 {'tasks':[...]}
errs=[(t.get('error') or '(none)') for t in ts if t.get('status')=='failed']
for e,c in collections.Counter(errs).most_common(12): print('%2d× %s'%(c,e[:180]))"
```

---

## 6. 红线（再次强调）

| ⛔ 不要做 | 原因 |
|---|---|
| 在管理页点「一键在线升级并重启」 | 长视频模块（`longvideo.py` 699 行 + 分段编排）被 GitHub 版本静默覆盖 |
| 调 `/admin/repo/push` | 只需 Bearer Key 就能把本地代码推到 GitHub main |
| 删除 `backend_src/data/accounts.json` | 账号池清空，重新导号要人工登录 muse.ai |
| 删除 `data/profiles/generate/` | Chrome profile 丢失，要重新预热 |
| 同时起两个 `run_local.py` | 数据互相覆盖 + 浏览器串话，**且 macOS 不会报错** |
| 删掉 `backend_src/` | 源码拉取会失败 |
| 未告知用户就消耗额度做冒烟/长视频测试 | 直接吃账号配额（当前 use_count 已 54） |
| 清理 `media/Backs/` 里的手工素材 | 可能是用户手工救援的成果，**先问** |

---

## 7. 需要用户确认的问题

**最优先的两个**（不回答没法开工）：

1. **长视频的角色基底/分镜表**：用户在 2026-10-03 的对话中提供了一份 240 秒长视频的资产设定（唐进生，中英双版角色基底 + 通用负向提示词）和分镜提示词表，**但只贴到「镜头 01（青年时代练拳）」就中断了**。
   - 剩下的镜头能补全吗？
   - 还是希望代码自动从自然语言剧本里分镜（`plan_segments` 的三级解析）？
   - 好消息：这份基底的格式正好能被 `longvideo.py:102 _BASE_RE`（匹配 `character base` / `negative prompt` / `master consistency`）识别，是可直接复用的输入格式。

2. **第一批安全护栏要不要现在做**？我建议做——B7 重写段头是几行改动，但在 A1~A6 没堵住之前，一次误点升级按钮全部白干。

**其次**：

3. **要不要真跑一次长视频端到端？** 需要用户授权额度 + 接受 33~62 分钟耗时。
4. **上游同步策略**：以后还跟不跟 `czg86389-hub/muse2api` 的更新？跟的话本地改动怎么保留？（A1 落地后这个问题就变成"要不要留个手动同步流程"）
5. **`media/Backs/` 里的人工素材**要不要保留/清理？
6. **CDP 端口要不要可配**？现在硬编码 19210（`run_local.py:42`）。

---

## 8. 接班人签名

```
接班人：________________
时间：  ________________
已读 HANDOVER-OUT.md：   ☐
已打 baseline tag：      ☐
已确认 §7 待答问题：     ☐
已确认第一批是否开工：   ☐
```
