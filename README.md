# Muse 视频工作台 · 本地版

**在你自己的电脑上跑一个「写一句话就出视频」的网页工具。**

下载 → 解压 → 终端里敲一条命令 → 浏览器自动打开 → 写描述 → 出视频。

不用买服务器，不用装 Docker，不用配域名证书。

---

## ▶ 看看它能做出什么

仓库首页有一支 30 秒成片：

[![精选成片](https://raw.githubusercontent.com/macsur/muse-video-make/main/素材库/生成视频/30秒车模_封面图.jpg)](https://github.com/macsur/muse-video-make/blob/main/素材库/生成视频/30秒车模.mp4)

更多成片见 [Releases 页面](https://github.com/macsur/muse-video-make/releases/latest)。

---

## 一分钟跑起来

### 第 1 步：下载

从 [Releases](https://github.com/macsur/muse-video-make/releases/latest) 下载 `muse-video-make-v1.1.0.zip`，解压。

### 第 2 步：启动

**Mac / Linux** —— 在解压出来的目录里打开终端，运行：

```bash
./start_local.sh --open-browser
```

**Windows** —— 双击 `start_local.bat`。

`--open-browser` 的作用是服务起来后自动帮你打开网页；不加也行，手动访问 <http://127.0.0.1:8090/> 一样。

### 第 3 步：导入 muse.ai 账号

**另开一个终端窗口**，在同一个目录里运行：

```bash
python3 run_local.py --import-account
```

它会弹出一个独立浏览器窗口，在那里正常登录你的 muse.ai 账号。登录完终端显示 `✓ 导入成功` 即可，支持连续导入多个账号轮转。

### 第 4 步：出片

浏览器打开 <http://127.0.0.1:8090/>，右上角状态灯变绿，输入画面描述，提交。单次出片通常需要 **3~6 分钟**。

---

## 运行前你需要什么

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | **3.8 或更高** | 只用来起服务，不用装任何第三方库 |
| 浏览器 | **Chrome 或 Edge** | 实际干活的渲染内核，必须有 |

两项都会在启动时自动检测，缺哪个终端会直接告诉你。首次启动会自动建一个隔离虚拟环境 `.venv_local` 并装依赖（FastAPI、Uvicorn、requests 等），之后启动就不装了。

> 视频是在 muse.ai 那边生成的，本工具只是把浏览器操作自动化。**出片速度取决于对方服务**，跟你电脑快慢无关。

---

## 常用操作

| 想做的事 | 命令 |
|---|---|
| 启动并自动打开网页 | `./start_local.sh --open-browser` |
| 换端口 | `./start_local.sh --web-port 8080 --api-port 18600` |
| 指定浏览器内核 | `./start_local.sh --browser "/Applications/Google Chrome.app/..."` |
| 导入 / 管理账号 | `python3 run_local.py --import-account` |
| 停止服务 | 在跑服务的终端里按 `Ctrl + C` |
| 重置密钥 | 删掉 `data/local_config.json`，下次启动会重新生成 |

**两个端口的分工：**

- `http://127.0.0.1:8090/` —— 前端创作界面，给人用
- `http://127.0.0.1:18610/` —— 后端 API 和账号面板，给程序用

---

## 接入 Cherry Studio / NextChat 等客户端

后端兼容 OpenAI API 规范，可以直接当视频/对话/图像接口用：

- **Base URL**：`http://127.0.0.1:18610/v1`
- **API Key**：启动时终端打印的 `m2a_xxxxxxxxxxxx`（在 `data/local_config.json` 里）
- **端点**：`POST /v1/videos`、`POST /v1/chat/completions`、`POST /v1/images/generations`

---

## 长视频（分段生成）

超过 30 秒的成片走分段管线：把分镜剧本切成镜头，逐段生成再合成。包里的 `素材库/唐进生视频剧本/` 有一份完整的 240 秒剧本样本可以直接改。

```bash
# 长视频合成需要 ffmpeg / ffprobe，先装好（brew install ffmpeg / apt install ffmpeg）
# 服务要先跑着，这个脚本是往已在跑的服务提交任务、然后轮询到出片

python3 tools/run_long_video.py --plan-only          # 只看分段计划，不提交、不消耗额度
python3 tools/run_long_video.py --script "路径/你的剧本.md"   # 真跑（默认 240 秒）
```

改剧本直接编辑 markdown 就行，格式照着样本写（`【镜头一｜标题】` 分镜 + `**镜头 01 (0:00—0:08)**` 时间码）。**跑之前先用 `--plan-only` 看一遍**——分段是纯函数、零成本，分错了再跑就太亏了。

一个 240 秒的片子大约 24 段、参考耗时 **62 分钟**（不含重试）。`Ctrl-C` 只停止观察，不会取消服务端的任务。

---

## 出问题了

| 现象 | 怎么办 |
|---|---|
| 提示未找到 Chrome / Edge | 装一个 Chrome 或 Edge，或用 `--browser` 指定路径 |
| 提示未检测到 Python 3.8+ | 去 <https://www.python.org/downloads/> 装一个再试 |
| 网页打不开 | 确认终端窗口还开着；关掉终端服务就停了 |
| 端口被占用 | 用 `--web-port` / `--api-port` 换一个端口 |
| 任务卡在「生成中」很久 | 单次出片本来就要 3~6 分钟，超过 10 分钟多半是对方服务出问题 |
| 账号灯是红的 | 重新 `python3 run_local.py --import-account` 导一次 |
| 想彻底重来 | 删掉 `data/` 和 `.venv_local/` 两个目录 |

日志在 `data/run_local.log`。

---

## 目录结构

```
muse-video-make-v1.1.0/
├── start_local.sh          ← Mac/Linux 启动脚本
├── start_local.bat         ← Windows 启动脚本
├── run_local.py            ← 启动器本体（起服务、建环境、管端口）
├── web/index.html          ← 前端创作界面
├── backend_src/            ← 后端服务
├── tools/                  ← 账号导入、长视频等工具
├── 素材库/                  ← 剧本样本与成片
├── data/                   ← 首次启动自动生成：密钥、账号、任务、成片
└── .venv_local/            ← 首次启动自动生成的隔离 Python 环境
```

**`data/` 和 `.venv_local/` 是运行后自动生成的，不要手动改，也不要分享给别人** —— 里面是你的 API Key 和 muse.ai 账号登录态。

---

## 致谢

本项目建立在以下开源项目之上，作者们完成了绝大部分底层工作。**没有他们就没有这个项目。**

| 项目 | 作者 | 链接 | 协议 |
|---|---|---|---|
| **muse2api**（上游） | [czg86389-hub](https://github.com/czg86389-hub) | [czg86389-hub/muse2api](https://github.com/czg86389-hub/muse2api) | MIT |
| **muse2api**（程序本体 + 稳定性修复） | [yys9253462-gif](https://github.com/yys9253462-gif) | [yys9253462-gif/muse2api](https://github.com/yys9253462-gif/muse2api) | MIT |
| **muse-video-installer**（一键安装脚本、工作台） | [yys9253462-gif](https://github.com/yys9253462-gif) | [yys9253462-gif/muse-video-installer](https://github.com/yys9253462-gif/muse-video-installer) | MIT |

依赖关系自下而上：

```
czg86389-hub/muse2api          ← 上游原型
        ↓
yys9253462-gif/muse2api        ← 程序本体（叠加 11 项稳定性与安全修复）
        ↓
yys9253462-gif/muse-video-installer  ← 一键安装脚本 + 网页工作台
        ↓
本仓库                            ← 本地版打包、长视频分段管线、安全加固、剧本压缩修复
```

- 协议：上游为 **MIT**，本仓库同样以 MIT 发布（见 `LICENSE` 与 `backend_src/LICENSE`）。
  MIT 要求保留原始版权声明，两个 LICENSE 文件均已原样保留。
- 本仓库作者对上游的**任何改动都无条件贡献回去**。
- 若上游作者认为本仓库的署名或表述需要调整，请提 issue 或 PR，我们无条件配合更正。

> 📌 另需感谢 muse.ai（Meta）提供视频生成能力。本项目是**非官方**的第三方封装，
> 与 Meta、muse.ai 无隶属或背书关系。请遵守 muse.ai 的服务条款。

---

## 许可

MIT，见 [LICENSE](LICENSE)。