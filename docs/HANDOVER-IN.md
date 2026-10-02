# 接班记录（Muse 视频工作台 · 本地版）

> 接班时间：2026-10-02
> 对应交班记录：[`HANDOVER-OUT.md`](./HANDOVER-OUT.md)
> 状态：**代码通读完成，零改动，等指令开工**

---

## 1. 接班确认清单

接手后请逐条打勾确认，确认完再动代码：

- [ ] 我已读完 `docs/HANDOVER-OUT.md`，理解了"CDP 操控真实网页"这个核心机制
- [ ] 我知道浏览器只有 1 个、chat/image/video 必须串行，改动不能绕过 `SCHED`
- [ ] 我知道**本地魔改 `backend_src/*.py` 会被「一键在线升级」静默覆盖**（P1-1）
- [ ] 我知道当前有**两个后端实例**在跑（P0-1），任何测试结果都可能被污染
- [ ] 我知道密钥有两份且不一致：`data/local_config.json` vs `backend_src/.env`
- [ ] 我知道数据落在**两个地方**：`data/`（密钥、profile）和 `backend_src/data/`（账号、任务、媒体）
- [ ] 我确认了本次任务范围：**该程序代码需要大量修改完善**（不是修一个 bug）

---

## 2. 建议的阅读顺序

想改哪里就读哪里，但**前两步必读**：

| 顺序 | 文件 | 为什么 |
|---|---|---|
| 1️⃣ | `run_local.py`（474 行） | 本地化的全部逻辑都在这，改启动行为只动它 |
| 2️⃣ | `docs/HANDOVER-OUT.md` §3 运行原理 | 不懂 CDP 那套机制，改 engine 必翻车 |
| 3️⃣ | `backend_src/engine.py`（1197 行） | 生成逻辑主体，80% 的功能需求落在这里 |
| 4️⃣ | `backend_src/app.py`（2763 行） | 路由 + 提示词构造 + 参数校验 + 管理接口 |
| 5️⃣ | `backend_src/scheduler.py` | 队列语义，改并发/超时必读 |
| 6️⃣ | `web/index.html`（431 行） | 前端工作台，功能需求常常要前后端一起改 |

**可跳过**：`install.sh`（104KB 服务器安装器）、`test-install.sh`、`Dockerfile`、`docker-compose.yml`、`deploy/` —— 本地版完全不经过它们。

---

## 3. 开工前必须先做的两件事

### 3.1 备份（改代码前）

本地目录**不是 git 仓库**（`git rev-parse` 会失败，`backend_src/.git` 不存在），改坏了没有回滚点。

```bash
cd /Users/ttnk/Desktop/muse-video-installer
cp -a backend_src backend_src.bak.$(date +%Y%m%d-%H%M)
cp -a run_local.py run_local.py.bak
cp -a web web.bak
```

### 3.2 清场（否则测出来的结果不可信）

```bash
# 看清楚都有谁
lsof -nP -iTCP -sTCP:LISTEN | grep -E "18610|8090|19210"
ps -o pid,ppid,lstart,command -p 39690 -p 68241 -p 68247

# 停掉孤儿后端 39690（绑 0.0.0.0:18610 + 用 .env 的旧 Key）
kill 39690

# 停掉没人持有的 Chrome 21773（PPID=1，早于两个后端启动）
pkill -f "remote-debugging-port=19210"

# 只留一套：回到终端里重新 ./start_local.sh
```

清理后应当只剩：`run_local.py` → 一个 uvicorn → 一个 Chrome。

---

## 4. 改造清单（按建议顺序，含验收标准）

> 这份清单来自交班时的实测结论。**每项动手前先确认用户是否要**，别自作主张。

### 🟠 第一批：让本地版"敢改"（改代码的前提）

| # | 事项 | 落点 | 验收标准 |
|---|---|---|---|
| A1 | 禁用本地版的一键升级 / repo push | `app.py` 加开关（读 `CFG.local_mode` 或环境变量），`admin.html` 隐藏按钮 | 本地启动时管理页**看不到**「一键在线升级」；`POST /admin/update/upgrade` 返回明确错误 |
| A2 | 单实例保护 | `run_local.py` 加 pidfile + 文件锁 + 启动复检端口 | 已有实例在跑时再次 `./start_local.sh`，**复用并提示**，不静默起第二个 |
| A3 | Key 单一事实来源 | 统一到 `data/local_config.json`；`.env` 降级为只读或删除；`run_local.py:405` 不再无条件覆盖 | `/admin/apikey/rotate` 后**重启仍在生效**，前后端都拿到新 Key |
| A4 | Key 不再明文写进 HTML | 改成启动时通过 `/admin/apikey` 或 `config.js` 动态下发 | `web/index.html` 里搜不到 `m2a_`；目录被复制不泄露 Key |
| A5 | 修 `MUSE2API_REPO` 地址 | `run_local.py:37` | 删掉 `backend_src/` 后能成功重新拉取源码 |

### 🟡 第二批：正确性与健壮性

| # | 事项 | 落点 | 验收标准 |
|---|---|---|---|
| B1 | CORS 收紧 | `app.py:72` 真正使用 `CFG.cors_origins`，或按 `public_base` 推导 | 局域网其他机器无法访问管理接口 |
| B2 | ~~排队超时与长视频~~ | ✅ **已完成**（2026-10-02）：`duration>30` 改走 `longvideo.plan_segments` 自动分段，逐段独立排队 | 见 `HANDOVER-OUT.md` §10 |
| B3 | `/v1/media` 鉴权 | `app.py:1824` | 无 Key 取媒体返回 401；前端播放路径同步适配 |
| B4 | 启动健康检查增强 | `run_local.py:431` | 浏览器起不来时启动横幅能明确报错，而不是只有"后端就绪" |
| B5 | CDP 端口可配 | `run_local.py:42` + CLI 参数 | `--cdp-port 19220` 生效 |
| B6 | ~~修 `MUSE2API_REPO` 地址~~ | ✅ **已完成**（2026-10-02，原 A5） | 已改为 `czg86389-hub/muse2api` |

### 🟢 第三批：功能（**这一批请用户提需求**）

交班时**未收集到具体功能需求**，只知道"需要大量修改完善"。建议方向（供讨论，非承诺）：

- [ ] 批量视频任务 / 任务队列可视化
- [ ] 视频时长与画幅的真实映射（目前只是把话术塞进 prompt，模型不一定照做）
- [ ] 图生视频首帧的提取/裁剪（`media/Backs/extracted_frame0.jpg` 说明有人手工做过这事）
- [ ] 剧本模板系统（`data/scripts/` 已有备份机制，可往上搭）
- [ ] 多账号池的配额看板
- [ ] 生成结果直接推送到网盘/对象存储
- [ ] 前端：任务历史跨浏览器同步（现在只在 localStorage）
- [ ] 前端：图片/对话模型入口（现在前端只做视频，图片和对话要走 API）

---

## 5. 验证方法（改完怎么确认没改坏）

### 5.1 语法与导入

```bash
cd /Users/ttnk/Desktop/muse-video-installer
.venv_local/bin/python -c "import ast,sys
for f in ['run_local.py','backend_src/app.py','backend_src/engine.py','backend_src/scheduler.py','backend_src/store.py','backend_src/cdp.py','backend_src/config.py','backend_src/longvideo.py']:
    ast.parse(open(f,encoding='utf-8').read()); print('OK',f)"
```

### 5.1b 长视频专项（全部离线，零额度）

```bash
cd /Users/ttnk/Desktop/muse-video-installer
.venv_local/bin/python backend_src/tests/test_longvideo_split.py   # 54 项：分切/基底/装箱/合成降级
.venv_local/bin/python backend_src/tests/test_longvideo_flow.py    # 54 项：driver 编排，猴补 _run_generation
node web/tests/showtask.test.js                                     # 19 项：前端渲染，需 node
```

### 5.2 上游已有的测试

```bash
cd /Users/ttnk/Desktop/muse-video-installer/backend_src
ls tests/
../.venv_local/bin/python -m pytest tests/ -v      # 需要 pytest，没有就 ../.venv_local/bin/pip install pytest
```

现有测试：`test_vm_wait.py` / `test_async_images.py` / `test_session_health.py` / `test_media_selection.py`

### 5.3 冒烟（会消耗 muse.ai 额度，先问用户）

```bash
K=$(python3 -c "import json;print(json.load(open('data/local_config.json'))['api_key'])")

curl -s -H "Authorization: Bearer $K" http://127.0.0.1:18610/healthz
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:18610/readyz

# 提交一个 5 秒短视频（异步任务）
TID=$(curl -s -X POST http://127.0.0.1:18610/v1/videos \
  -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"prompt":"一只猫在草地上跑","duration":5,"size":"16:9"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "task=$TID"

# 轮询
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:18610/v1/videos/$TID \
  | python3 -m json.tool | head -30
```

### 5.4 看日志

- 本地版后端日志在**启动 `run_local.py` 的那个终端**（未重定向）
- `backend.log` 是**孤儿进程 39690** 的日志，跟本地版无关
- Chrome 自身的日志：`backend_src/data/chromium.log`
- 生成失败时的现场快照：`backend_src/data/debug-no-attachment.json`、`debug-extract-fail.json`

---

## 6. 红线（交班时再次强调）

| ⛔ 不要做 | 原因 |
|---|---|
| 在管理页点「一键在线升级并重启」 | 会覆盖本地对 `backend_src/*.py` 的所有修改 |
| 调 `/admin/repo/push` | 会把本地代码推到 GitHub |
| 删除 `backend_src/data/accounts.json` | 账号池清空，需重新导号（导号要人工登录 muse.ai） |
| 同时起两个 `run_local.py` | 数据互相覆盖、Chrome 串话 |
| 删掉 `backend_src/` 而不先修 A5 | 源码拉取会失败 |
| 未告知用户就消耗额度做冒烟测试 | 视频生成直接吃 muse.ai 账号配额 |

---

## 7. 遗留问题确认单（请用户逐条回答）

接手后请先跟用户确认这些，回答完再开工：

1. **要改什么？** 是修 bug、加功能、还是重写某些模块？具体到哪一块？
2. **改哪一层？** 只改本地化层（`run_local.py` + `web/`），还是要动 `backend_src/`？（动后者就有被"一键升级"覆盖的风险，需要 A1 先落地）
3. **要不要 init git？** 目前无版本控制，改动无法回滚。建议先 `git init` + 首次提交现状作为基线。
4. **孤儿进程 39690 和 Chrome 21773 怎么处理？** 是清掉，还是你正在用？
5. **上游同步策略？** 以后还要不要跟 `czg86389-hub/muse2api` 的更新？跟的话需要保留哪些本地改动？
6. **密钥以哪份为准？** `local_config.json` 还是 `.env`？

---

## 8. 接班人签名

```
接班人：________________
时间：  ________________
已读 HANDOVER-OUT.md：☐
已完成备份：            ☐
已清理孤儿进程：        ☐
待办清单已确认：        ☐
```
