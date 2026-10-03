# Muse 视频工作台 · 一键安装

[![Release](https://img.shields.io/github/v/release/macsur/muse-video-make?label=%E6%9C%80%E6%96%B0%E7%89%88)](https://github.com/macsur/muse-video-make/releases/latest)
[![License](https://img.shields.io/github/license/yys9253462-gif/muse-video-installer)](LICENSE)

> 在**你自己的服务器**上，一条命令装好一个「输入文字就能生成视频」的网页工具。
> 装完后：浏览器打开网址 → 写一句话 → 出视频。还能让别的软件（Cherry Studio、NextChat 等）连上它调用接口。

**不需要懂 Linux，不需要懂 Docker。** 脚本会自己把该装的都装好。

---

## ▶ 精选成片 · 车展现场 30 秒

[![车展现场 30 秒](素材库/生成视频/30秒车模_封面图.jpg)](素材库/生成视频/30秒车模.mp4)

**1280×720 · 24fps · 30.0 秒 · H.264 · 13.9 MB**
**[▶ 点击播放 / 下载原视频](素材库/生成视频/30秒车模.mp4)**

> 由本项目生成：写一段中文场景描述，单次出片，无需本地显卡。

更多成片：

| 成片 | 规格 | 大小 |
|---|---|---|
| [5 秒柴犬](素材库/生成视频/5秒柴犬.mp4) | 720×1280 竖屏 · 5.0 秒 | 3.5 MB |
| [Gemini 10 秒年少](素材库/生成视频/gemini_10秒年少.mp4) | 1280×720 · 10.0 秒 | 3.6 MB |
| [《唐先生的中场人生》30 秒封面](https://github.com/macsur/muse-video-make/releases/download/v1.0.0/cover-30s.mp4) | 1280×720 · 30.0 秒 | 8.3 MB |

> 📦 大体积成片放在 [Releases 页面](https://github.com/macsur/muse-video-make/releases/latest) 下载，不进仓库树，
> 保持 clone 轻快。该片由本项目生成：把一份带角色基底的唐进生分镜提示词丢进去，单次生成耗时 **245.8 秒**。
> 剧本原文见 [素材库/唐进生视频剧本](素材库/唐进生视频剧本/)。

---

## 致谢

本仓库建立在以下开源项目之上，作者们完成了绝大部分底层工作。**没有他们就没有这个项目。**

| 项目 | 作者 | 链接 | 协议 |
|---|---|---|---|
| **muse2api**（上游） | [czg86389-hub](https://github.com/czg86389-hub) | [czg86389-hub/muse2api](https://github.com/czg86389-hub/muse2api) | MIT |
| **muse2api**（程序本体 + 稳定性修复） | [yys9253462-gif](https://github.com/yys9253462-gif) | [yys9253462-gif/muse2api](https://github.com/yys9253462-gif/muse2api) | MIT |
| **muse-video-installer**（安装脚本、工作台） | [yys9253462-gif](https://github.com/yys9253462-gif) | [yys9253462-gif/muse-video-installer](https://github.com/yys9253462-gif/muse-video-installer) | MIT |

依赖关系自下而上：

```
czg86389-hub/muse2api          ← 上游原型
        ↓
yys9253462-gif/muse2api        ← 程序本体（叠加 11 项稳定性与安全修复）
        ↓
yys9253462-gif/muse-video-installer  ← 一键安装脚本 + 网页工作台
        ↓
本仓库                            ← 长视频分段管线、安全加固、剧本压缩修复
```

- 协议：上游为 **MIT**，本仓库同样以 MIT 发布（见 [LICENSE](LICENSE) 与
  [`backend_src/LICENSE`](backend_src/LICENSE)）。MIT 要求保留原始版权声明，
  两个 LICENSE 文件均已原样保留。
- 本仓库作者对上游的**任何改动都无条件贡献回去**；上游合并
  [PR #5](https://github.com/czg86389-hub/muse2api/pull/5) 后可直接切回官方版本。
- 若上游作者认为本仓库的署名或表述需要调整，请提 issue 或 PR，我们无条件配合更正。

> 📌 另需感谢 muse.ai（Meta）提供视频生成能力。本项目是**非官方**的第三方封装，
> 与 Meta、muse.ai 无隶属或背书关系。

---

## 目录

- [▶ 精选成片 · 车展现场 30 秒](#-精选成片--车展现场-30-秒)
- [致谢](#致谢)
- [先搞清楚：这里说的「服务器」是什么](#先搞清楚这里说的服务器是什么)
- [装之前要准备什么](#装之前要准备什么)
- [30 秒开始安装](#30-秒开始安装)
- [安装过程会问你什么](#安装过程会问你什么)
- [装完之后怎么用](#装完之后怎么用)
- [常用命令](#常用命令)
- [怎么卸载](#怎么卸载)
- [遇到问题怎么办](#遇到问题怎么办)
- [进阶：绑域名 + HTTPS](#进阶绑域名--https)
- [全部参数一览](#全部参数一览)

---

## 先搞清楚：这里说的「服务器」是什么

如果你从没碰过服务器，先看这三句话：

1. **它是一台 24 小时开着的电脑**，放在机房的，不是你桌上那台。你的网页工具就装在它上面。
2. **要租，不是买断。** 阿里云 / 腾讯云 / 华为云 / AWS / Oracle 这些云服务商都有，
   搜「云服务器」或「VPS」，最低配一个月十几到几十块。选 **2 核 4G 内存、系统选 Ubuntu 或 Debian**。
3. **租完你会拿到三样东西**：一个 **IP 地址**（形如 `43.201.63.145`）、一个 **root 密码**、
   以及控制台里的**「安全组 / 防火墙」设置页**（装完要在这里放行两个端口，见下面）。

> 💡 **怎么连上它？** 你租服务器的那个控制台里，一般有个「登录 / 远程连接」按钮，点开就是命令行，
> 不用额外装软件。如果你习惯用自己电脑的终端，也可以 `ssh root@你的IP`（会提示输密码）。
> 连上之后，把下面「30 秒开始安装」那段命令整段复制粘贴进去，回车即可。

> ⚠️ **没有服务器怎么办？** 那这个工具用不了 —— 它必须装在一台常开的机器上（你自己的电脑关机它就停了）。
> 不想租的话，可以先跳过，等你有了机器再来。

---

## 装之前要准备什么

| 需要 | 说明 |
|---|---|
| **一台服务器** | Linux（Debian / Ubuntu / CentOS / RHEL / Alpine 都行），能 SSH 登录，有 root 权限 |
| **至少 2 核 4G** | 因为要跑一个无头浏览器。内存小了会卡 |
| **至少 10G 空闲磁盘** | 程序本体 + 浏览器大约几百 MB |
| **能访问外网** | 第一次安装要从 GitHub 下载程序，网速慢会久一点 |
| **两个没被占用的端口** | 默认用 `18610`（接口）和 `8090`（网页）。被占了脚本会自动换 |
| **一个 muse.ai 账号** | 这是"燃料"。装完必须导入一个账号才能生成视频（见下文第 4 步） |

> ⚠️ **端口一定要在云服务商控制台放行**
> 阿里云 / 腾讯云 / AWS / Oracle 等都有"安全组"或"防火墙规则"。
> 装完如果网页打不开，九成是这里没放行 `8090` 和 `18610`。

---

## 30 秒开始安装

SSH 登录到你的服务器，然后：

```bash
# 1. 下载安装脚本
curl -fsSL -o install.sh https://raw.githubusercontent.com/yys9253462-gif/muse-video-installer/main/install.sh

# 2. 跑起来（会问你 1-2 个问题）
sudo bash install.sh
```

> 📌 **脚本下载不下来？** 三种办法任选：
> 1. 用 `scp` 把 `install.sh` 传到服务器：`scp install.sh root@你的服务器IP:/root/`
> 2. 在浏览器里打开上面的链接，复制全部内容，在服务器上新建文件粘贴进去
> 3. 装了 `git` 的话：`git clone https://github.com/yys9253462-gif/muse-video-installer.git`
>
> ✅ 装完之后**你下载的那份 `install.sh` 删掉也没关系** —— 脚本会把自己
> 另存一份到安装目录里（如 `/opt/mvw/install.sh`），以后 `--status` /
> `--upgrade` / `--uninstall` 都用那一份就行。安装结束时也会把这条命令
> 明确打印给你。

> 💡 想装**固定版本**（而不是随时可能变动的最新版）？
> 去 [Releases 页面](https://github.com/yys9253462-gif/muse-video-installer/releases/latest) 下载 `muse-video-installer-<版本>.tar.gz`，
> 传到服务器上 `tar -xzf` 解压，再 `sudo bash install.sh`。解压出来的包里连导号工具一起都有。

想**全自动、不问任何问题**：

```bash
sudo bash install.sh --yes
```

想**先看看它会做什么、但不动系统**：

```bash
sudo bash install.sh --dry-run
```

---

## 安装过程会问你什么

脚本只会问你 **1-2 个问题**，拿不准的直接按 **回车** 用默认值就行。

| 问题 | 默认 | 说明 |
|---|---|---|
| 装到哪个目录？ | `/opt/mvw` | 一般不用改 |
| 用哪个端口？ | 自动挑 | 会先看 `18610` / `8090` 有没有被占，占了就往后找 |

然后它就自己干活了，大概 **2-5 分钟**：

```
▸ 开始检查环境
  ✓ 系统：debian 12（用 apt 装东西）
▸ 检查并安装 git / docker / docker compose
  ✓ docker 已就绪
▸ 准备程序文件
  ✓ 程序本体下载完成
▸ 配置密钥
  ✓ 已生成一把随机密钥（只显示在最后，请留意）
▸ 写入配置
  ✓ 接口端口 18610，网页端口 8090
▸ 启动服务
  ✓ 容器已启动
▸ 等待接口就绪
  ✓ 接口已响应（HTTP 401 属正常，说明在跑）
▸ 配置网页服务
  ✓ 网页服务已启动
```

最后会打印一份**"下一步"清单**，照着做就行。

---

## 装完之后怎么用

安装结束时会给你这些信息，**请截图保存**（尤其是 API Key）：

```
网页地址：      http://你的服务器IP:8090/
接口地址：      http://你的服务器IP:18610/v1
API Key：       m2a_xxxxxxxxxxxxxxxxxxxx
安装目录：      /opt/mvw
账号池面板：    http://你的服务器IP:18610/admin?key=你的Key
```

### 第 1 步：打开网页看看

浏览器访问 `http://你的服务器IP:8090/`。
看到左边有「生成视频」按钮、右上角状态灯是**绿色**，就说明装好了。

### 第 2 步：导入你的 muse.ai 账号（⚠️ 必须做）

视频工作台是靠 **muse.ai 的账号**来出片的，所以得先给它一个账号。
你的服务器上**没有浏览器**，所以这一步要在**你自己的电脑**上做。

给你两条路，**任选其一**：

| | 方式 | 适合谁 | 要装什么 |
|---|---|---|---|
| **🅰** | 导号小工具（推荐） | 想省事的 | Python |
| **🅱** | 网页手动粘贴 | 不想装任何东西的 | 无 |

> 💡 **装了 Python 就选 🅰** —— 敲一条命令，剩下的它全问你。
> **没 Python / 不想装** 就选 🅱，代价是要自己从浏览器里抄 4 串字符。

---

## 🅰 导号小工具（推荐）

整个流程**只需要敲一条命令**，剩下的它都会问你。

#### 准备：确认电脑上有 Python 和浏览器（各看一次就行）

**Python**：打开命令行敲 `python --version`，能显示版本号（如 `Python 3.12.1`）就跳过。
没装的话去 [python.org](https://www.python.org/downloads/) 下载，安装时**务必勾选
"Add Python to PATH"**（这一步漏了后面会报「不是内部或外部命令」）。

**浏览器**：Windows 自带的 **Edge** 就行，有 **Chrome** 更好。不用额外装。

#### 第 1 步：下载导号小工具

下载这个文件，**存到桌面**（文件名叫 `get_muse_cookie.py`，别改）：

```
https://raw.githubusercontent.com/yys9253462-gif/muse-video-installer/main/tools/get_muse_cookie.py
```

> 它是一个普通的 Python 脚本，你可以用记事本打开看内容，**不会**在你电脑上装任何东西。

---

#### 第 2 步：敲一条命令

在桌面打开命令行：Windows `Win+R` → 输入 `cmd` → 回车，然后：

```bash
cd Desktop
python get_muse_cookie.py
```

它会问你两件事（直接从下面的输出里复制过去）：

| 它问什么 | 你填什么 |
|---|---|
| 服务器地址 | `你的服务器IP:18610` |
| API Key | 安装结束时显示的 `m2a_` 开头那串（随时可以用 `sudo bash install.sh --status` 查回来） |

填完它会**自动弹出一个浏览器窗口**。

> 💡 地址和 Key 它会**记住**。第二次起，直接连敲两个回车就行。

---

#### 第 3 步：在弹出的窗口里登录 muse.ai

用你平时的方式登录即可。**登录成功、能看到聊天界面后**，脚本会自己把账号传上服务器，
你会看到：

```
  ✓ 导入成功！
      账号标签：你的邮箱
      账号 ID： 160c1f16435f
      cookie： 10 条
```

然后它会问你「**还要再导入一个 muse.ai 账号吗？**」

- 只加一个 → 直接回车
- **想加多个** → 按 `y`，会弹出一个干净的浏览器窗口，登录**另一个** muse.ai 账号即可。
  想加几个就加几个，一路 `y` 下去，加完回车收工。

最后它会打印账号池的现状，方便你核对。

---

#### 第 4 步：核对

```bash
sudo bash install.sh --status
```

看到「账号池： **N** 个账号」就对上了。也可以打开面板看：
`http://你的服务器IP:18610/admin?key=你的Key`

---

#### 常见卡点

| 现象 | 怎么办 |
|---|---|
| `python 不是内部或外部命令` | Python 装了但没勾 "Add to PATH"。重装一次并勾上，或把 Python 的安装目录手动加到 PATH |
| 「没找到可用的浏览器」 | 装个 Chrome（<https://www.google.cn/chrome/>），或者用 `--chrome "D:\某处\chrome.exe"` 指定路径 |
| 「连不上服务器」 | ① 地址漏了冒号（要写 `1.2.3.4:18610`）② 云服务商的**安全组**没放行 18610 端口 |
| 「API Key 不对」 | Key 复制少了字符。跑 `sudo bash install.sh --status` 看回来重新复制 |
| 等待登录超时 | 重跑一次，这次登录快一点；或者加 `--timeout 600` 给足 10 分钟 |
| 浏览器起不来（提示「你自己日常用的浏览器正在运行」） | Chrome 系浏览器不允许同时开两个带调试端口的实例。把你的浏览器**完全退出**再重跑；或者改用 Edge：`--chrome "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"` |
| 完全不想装浏览器/Python | 用下面的 🅱 方案 |

---

## 🅱 网页手动粘贴（不装任何东西）

服务端的面板**自带导入框**，不想装 Python 就用这条路。

#### 第 1 步：从浏览器里把 4 串 cookie 抄出来

1. 在你自己的浏览器里**登录 muse.ai**（要先登录成功）
2. 按 `F12` 打开开发者工具 → 切到 **Application**（中文版叫「应用」）标签页
3. 左侧找到 **Cookies** → 点开 `https://muse.ai`
4. 在右侧列表里依次找到这 **4 个名字**，双击它的 **Value** 列复制：

```
hatch_sess
hatch_gw
hatch_vml
hatch_native_auth_device
```

> ⚠️ 这 4 个的值都很长（几十到几百字符），**要完整复制**，少一个字符就不行。
> 它们都标着 `HttpOnly ✓` —— 这正是为什么不能直接在控制台敲
> `document.cookie` 读它们，必须到 Application 面板里看。

#### 第 2 步：拼成一行，粘进面板

把这 4 个值拼成**一行**（分号 + 空格分隔）：

```
hatch_sess=你复制的值; hatch_gw=你复制的值; hatch_vml=你复制的值; hatch_native_auth_device=你复制的值
```

然后打开面板 → 点「**添加账号**」→ 把上面那行粘进输入框 → 保存：

```
http://你的服务器IP:18610/admin?key=你的Key
```

#### 想一次加多个？

面板支持批量，**每行一个账号**，行首可以带标签：

```
acc-01 | hatch_sess=...; hatch_gw=...; hatch_vml=...; hatch_native_auth_device=...
acc-02 | hatch_sess=...; hatch_gw=...; hatch_vml=...; hatch_native_auth_device=...
```

> 💡 如果你装了 Python，用 🅰 的 `--from-clipboard` 也行 ——
> 它读的就是上面这种格式，你不用自己拼接。

---

### 第 3 步：回到网页，测试生成

接口地址和 Key 脚本已经**自动填好了**。直接写一段描述，点「生成视频」，
等 **1-2 分钟**出片。

### 第 4 步（可选）：让别的软件连上它

任何支持 **OpenAI 兼容接口**的客户端都能连：

| 填什么 | 填什么值 |
|---|---|
| API 地址 / Base URL | `http://你的服务器IP:18610/v1` |
| API Key | `m2a_xxxxxxxxxxxx` |
| 模型名 | 见账号池面板里列出的模型 |

> ⚠️ **注意地址末尾只有一个 `/v1`，不要写成 `//v1`**（多一个斜杠会连不上）。

---

## 常用命令

脚本支持"记住上次的配置"，所以重跑这些命令**不用再带参数**：

```bash
sudo bash install.sh --status      # 看运行状态 + 找回地址和 API Key
sudo bash install.sh --upgrade     # 升级到最新版（Key 不变）
sudo bash install.sh --uninstall   # 卸载（默认保留数据）
sudo bash install.sh --help        # 看全部用法
```

> 💡 **找不到安装脚本了？** 脚本在安装时会把自己存一份到安装目录，
> 所以下面这条**永远可用**（把 `/opt/mvw` 换成你的安装目录）：
>
> ```bash
> sudo bash /opt/mvw/install.sh --status
> ```
>
> 安装结束时，最后打印的「常用命令」就是这条完整路径，直接复制即可。
>
> 📌 `--status` 是**只读**的，不用加 `sudo` 也能看（当前用户没有 docker
> 权限时会提示你加 `sudo`）。

### 再跑一次 `install.sh` 会怎样？

直接 `sudo bash install.sh` 会**接上已有安装**，不会重头来：

| 东西 | 行为 |
|---|---|
| 端口 | 沿用上次的（除非你显式给新端口） |
| **API Key** | **沿用上次的**（客户端不用重配）✅ |
| 账号数据 | 保留 |
| 程序本体 | 拉取最新代码 |
| 容器 | 原地重建 |

所以"想改点东西再装一遍"是安全的。

### 加账号 / 看账号池（在你自己的电脑上跑）

导号小工具除了导号，还能直接管账号池，不用打开网页：

```bash
python get_muse_cookie.py                    # 导一个；它会问「还要再来一个吗」
python get_muse_cookie.py --count 3          # 一口气导 3 个（全自动，不问）
python get_muse_cookie.py --list             # 看账号池现在有哪些账号
python get_muse_cookie.py --remove acc-02    # 删掉某个账号（按 ID 或标签）
```

> 不记得地址和 Key 也没关系 —— 敲 `python get_muse_cookie.py` 它会问你，
> 而且**记住上次填的**，第二次起连敲两个回车就行。
>
> 忘了 Key：在服务器上跑 `sudo bash install.sh --status` 就能看回来。

### 直接管容器（进阶）

```bash
# 看日志
cd /opt/mvw && docker compose logs -f

# 重启
cd /opt/mvw && docker compose restart

# 看网页服务日志
journalctl -u mvw-web.service -f
```

---

## 怎么卸载

```bash
sudo bash install.sh --uninstall
```

默认行为：

| 东西 | 卸载后 |
|---|---|
| 容器（muse-video） | ✅ 删除 |
| 网页 systemd 服务 | ✅ 停止并删除 |
| **安装目录 `/opt/mvw`（含账号数据）** | **保留**（怕你误删） |

想**连数据一起删干净**，卸载后手动执行：

```bash
sudo rm -rf /opt/mvw
```

---

## 遇到问题怎么办

### ❌ 网页打不开 / 转圈

1. **先查云服务商安全组** —— 90% 是这个原因。放行 TCP `8090` 和 `18610`。
2. 在服务器上本地试一下：
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/
   ```
   返回 `200` 说明服务没问题，就是防火墙/安全组的事。
3. 看网页服务状态：
   ```bash
   sudo bash install.sh --status
   ```

### ❌ 客户端报「连接失败」

**99% 是地址写错了。** 检查：

- 末尾是不是**只有一个** `/v1`（`http://IP:18610//v1` ← 错，多了一个斜杠）
- 端口对不对（默认 `18610`，装的时候可能自动换过）
- 服务器安全组放行了没

### ❌ 我忘了 API Key 是多少

不用重装，直接查：

```bash
sudo bash install.sh --status
```

输出里有一段「**连接信息**」，会把网页地址、接口地址、API Key、账号池面板链接全列出来。

### ❌ 重跑一遍安装，会不会把 Key 换掉？

**不会。** 脚本会沿用上次的 Key，并在输出里告诉你「沿用上次的密钥（客户端不用重配）」。
所以你可以放心重跑（改端口、改目录都可以），已配置好的客户端不受影响。

> 如果你**确实想换一把新 Key**：手动删掉安装目录里的 `install.conf`，
> 或者改掉 `docker-compose.yml` 里的 `MUSE2API_KEY`，再重跑。

### ❌ 网页能开，但生成视频报错 / 一直转

账号没导入或掉线了。先看 `--status`，如果提示「账号池是空的」：

```
http://你的服务器IP:18610/admin?key=你的Key
```

账号数量是 `0` → 重跑[第 2 步](#第-2-步导入你的-museai-账号-必须做)导号。
有账号但报错 → 重新导一次（账号会过期，脚本会每 48 小时自动续期，但偶尔会失效）。

想确认账号还在不在，不用开网页：

```bash
python get_muse_cookie.py --list
```

### ❌ 安装时报「端口已被占用」

```bash
# 看谁占了 8090
sudo ss -lntp | grep 8090
```

要么停掉那个服务，要么换个端口：

```bash
sudo bash install.sh --api-port 19000 --web-port 9000
```

### ❌ 自定义端口后，本地通了但远程连不上

这是**上游程序的一个坑**（它的 Dockerfile 把端口写死成 18610）。
**本脚本已经处理好了**（用 `command` 显式覆盖端口）。
如果你是自己手动部署遇到这个，需要在 `docker-compose.yml` 里加：

```yaml
command: ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port 你的端口"]
```

### ❌ 内存不够 / 容器被 OOM 杀掉

无头浏览器吃内存。2G 内存的机器建议升到 4G，或者在云控制台加 swap。

### ❌ 安装卡在「下载程序本体」

服务器访问 GitHub 慢。可以：
- 换个时间再试
- 或者在你本地下载好，用 `scp` 传上去，再跑安装

---

## 进阶：绑域名 + HTTPS

如果你的域名已经解析到了这台服务器，装的时候加一个参数就自动配好 HTTPS：

```bash
sudo bash install.sh --domain video.你的域名.com
```

脚本会自动：
- 用 Caddy 起一个反代
- 自动申请 Let's Encrypt 免费证书
- 装完直接 `https://video.你的域名.com/` 访问

> ⚠️ 前提：域名**必须**已经解析到这台服务器的公网 IP，且 **80 / 443 端口没被别的程序占用**。
> 如果这台机器上已经有 Caddy/Nginx 在跑，脚本会提示你，需要手动加一段配置。

不要域名（默认）：

```bash
sudo bash install.sh --no-domain
```

---

## 全部参数一览

```
用法：sudo bash install.sh [选项]

常用：
  --yes, -y            全自动安装，所有问题用默认值（适合脚本/CI）
  --dry-run            只显示会做什么，不实际改动系统
  --status             看当前运行状态
  --upgrade            升级到最新版
  --uninstall          卸载
  --help, -h           显示帮助

进阶：
  --dir <路径>         安装到哪个目录（默认 /opt/mvw）
  --api-port <端口>    接口服务端口（默认自动挑，常用 18610）
  --web-port <端口>    网页端口（默认自动挑，常用 8090）
  --domain <域名>      给网页绑域名并自动配 HTTPS
  --no-domain          不要域名（默认就是不要）
  --no-deps            不自动装依赖，缺什么只告诉你
```

**示例：**

```bash
# 全自动，指定网页端口
sudo bash install.sh --yes --web-port 8090

# 装到自定义目录，用自定义端口
sudo bash install.sh --dir /data/video --api-port 19000 --web-port 9000

# 绑域名
sudo bash install.sh --domain video.example.com
```

---

## 同一台机器装多份？

**可以。** 脚本会**从安装目录自动派生容器名和服务名**，所以：

```bash
sudo bash install.sh --dir /opt/mvw-2 --api-port 18620 --web-port 8100
```

两份互不干扰，各自独立端口、独立账号池。

> ⚠️ 如果派生出来的名字**正好和机器上已有的服务重名**（且不是本脚本装的），
> 脚本会**拒绝执行并提示你换个目录** —— 这是故意设计的，免得把别人的服务覆盖掉。

---

## 会和机器上已有的东西冲突吗？

脚本做了三层保护，**不会**覆盖别人的东西：

| 保护 | 行为 |
|---|---|
| **目录** | 目标目录不可写 → 提前报错，不会下载到一半才失败 |
| **端口** | 被别人的程序占用 → 报错并给出 `ss -lntp` 排查命令；<br>被**自己上次的**容器占用 → 放行，原地重建 |
| **服务名 / 容器名** | 已存在同名但**不是本脚本装的** → 拒绝执行，提示换目录 |

默认安装目录是 `/opt/mvw`，默认容器名 `mvw`，默认服务名 `mvw-web.service` ——
刻意选了不常见的名字，就是为了降低撞名概率。

**目录名是中文也没问题**（比如 `/opt/视频工作台`）。脚本会给 compose 显式指定一个
ASCII 项目名，不会踩到 Docker「项目名不能为空」那个坑。而且不同目录会派生出不同名字，
所以你在两个目录各装一份也不会互相覆盖。

---

## 装完之后常见的几个小疑问

| 情况 | 怎么办 |
|---|---|
| **装完发现网页打不开** | 先确认云服务商**安全组/防火墙**放行了那两个端口（控制台里加），再用 `sudo bash install.sh --status` 核对地址 |
| **给了 `--domain` 但域名打不开** | 多半是 DNS 还没解析到这台机器。装完最后的提示会告诉你：先用 `IP:端口` 访问，等 DNS 生效后重跑 `sudo bash install.sh --domain 你的域名` 就会自动配上 HTTPS |
| **嫌下载太慢，按了 Ctrl+C** | 不要紧。重跑同一条命令就会接着来（脚本可以安全重复运行），想先看进度用 `--status` |
| **反复装了很多次，突然新装起不来** | Docker 的网段可能被分光了（报 `address pools have been fully subnetted`）。跑 `docker network prune -f` 清掉没人用的网络再重试 |
| **想改端口 / 改目录重装** | 直接重跑，加新的 `--api-port` / `--web-port` 即可。**API Key 会沿用**，已配置的客户端不用改 |

---

## 卸载 / 排障速查

| 我想… | 命令 |
|---|---|
| 看状态 | `sudo bash install.sh --status` |
| 升级 | `sudo bash install.sh --upgrade` |
| 卸载 | `sudo bash install.sh --uninstall` |
| 看容器日志 | `cd /opt/mvw && docker compose -p mvw logs -f` |
| 看网页日志 | `journalctl -u mvw-web.service -f` |
| 看谁占端口 | `sudo ss -lntp \| grep <端口>` |
| 清理没人用的 Docker 网络 | `docker network prune -f` |

---

## 自测（可选）

仓库自带两套回归测试，改完代码可以自己跑：

```bash
# 安装脚本：58 项（语法、参数校验、dry-run、真实安装、幂等重跑、
#           端口冲突、卸载、以及五轮实测踩出来的缺陷回归）
sudo bash test-install.sh

# 导号工具：61 项（地址校验、cookie 解析、错误路径、真实服务器交互）
python3 tools/test-import-tool.py --base http://你的IP:18610 --key m2a_xxx
```

---

## 关于

- 程序本体：[yys9253462-gif/muse2api](https://github.com/yys9253462-gif/muse2api)
  —— 基于上游 [czg86389-hub/muse2api](https://github.com/czg86389-hub/muse2api)（MIT 协议），
  并叠加了 11 项稳定性与安全修复（并发调度、参数校验、鉴权加固等）。
  上游合并 [PR #5](https://github.com/czg86389-hub/muse2api/pull/5) 后可切回上游。
- 本安装脚本：把部署、导号、网页工作台串成一条命令，面向不懂 Linux 的用户
- 安装脚本版本：`1.1.1`

### 装完会自检代码（v1.1.1 新增）

脚本固定安装 `main` 分支（`MUSE2API_REF="main"`），装完会**在容器里**核对三项关键修复：
FIFO 队列调度（`scheduler.py`）、405 鉴权守卫（`_guard_method_not_allowed`）、
视频时长校验（`validate_video_duration`）。三项都在才打印「代码自检：关键修复都在」；
任一缺失会明确报错，而不是假装安装成功。随时可复查：

```bash
sudo bash install.sh --status   # 会顺带跑一次代码自检
```

### 文件说明

| 文件 | 用途 |
|---|---|
| `install.sh` | **主脚本**，你要用的就是它 |
| `README.md` | 本说明文档 |
| `test-install.sh` | 回归测试套件（开发者用，会自动装一份到 `/opt/muse-regress` 再卸载） |

### 跑回归测试（可选）

```bash
sudo cp install.sh /root/install-muse-video.sh
sudo bash test-install.sh
```

会依次验证：语法、参数校验、dry-run 无副作用、真实安装、结果核验、
`--status`、幂等重跑、端口冲突拦截、卸载、卸载后复检、清理。

> ⚠️ 测试会在 `/opt/muse-regress` 和端口 `28710/28711` 上操作。
> 如果这台机器上有**正式服务也叫 `muse-regress-*`**，请先改测试脚本里的变量名。

> 💡 **免责声明**：本脚本只是部署工具，视频生成能力来自 muse.ai 的账号。
> 请遵守 muse.ai 的服务条款，不要滥用。
