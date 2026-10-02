# Muse 视频工作台 · 本地免 Docker 极速运行指南

本指南面向需要在**自己的本地电脑（Mac / Windows / Linux）**直接运行 Muse 视频工作台的用户。
**无需购买云服务器、无需 Docker、无需 Root 权限、不折腾系统服务！**

---

## 🚀 30 秒在本地电脑运行

### 1. 启动服务

- **Mac / Linux 用户**：
  在终端中打开本目录，运行：
  ```bash
  ./start_local.sh
  ```
  *(或者直接执行 `python3 run_local.py`)*

- **Windows 用户**：
  双击运行 `start_local.bat`。

---

### 2. 脚本会自动为你做好的事情：
1. **自动探测本地 Chrome / Edge 浏览器**作为无头渲染内核；
2. **自动创建独立的 Python 隔离虚拟环境 (`.venv_local`)**，并自动安装所有必要依赖包；
3. **自动下载并部署核心服务端代码 (`backend_src`)**；
4. **生成并记住专属于你本地的安全 API Key**；
5. **双服务一体化启动**：
   - **前端 Web 创作界面**：`http://127.0.0.1:8090/`
   - **后端 API 与账号管理面板**：`http://127.0.0.1:18610/`

---

## 🔑 首次使用：导入 muse.ai 账号

启动后，在你的本地电脑上另开一个终端窗口，运行内置的导入命令：

```bash
python3 run_local.py --import-account
```
或者手动执行：
```bash
python3 tools/get_muse_cookie.py --base http://127.0.0.1:18610 --key <你的API_KEY>
```

- 该命令会自动弹出一个独立的浏览器窗口；
- 正常登录你的 `muse.ai` 账号；
- 登录完成后终端会显示 `✓ 导入成功`，支持连续导入多个账号轮转；
- 回到浏览器打开 `http://127.0.0.1:8090/`，右上角灯变绿，输入画面描述即可出视频！

---

## 常用操作说明

| 目标 | 操作 |
|---|---|
| **启动服务并自动打开网页** | `./start_local.sh --open-browser` |
| **自定义端口** | `./start_local.sh --web-port 8080 --api-port 18600` |
| **导入或管理账号** | `python3 run_local.py --import-account` |
| **停止服务** | 在运行终端中按下 `Ctrl + C`，服务会自动安全退出 |
| **重置或重新配置** | 删除 `data/local_config.json` 即可重新生成密钥 |

---

## 接入 Cherry Studio / NextChat / 其它客户端

本服务完全兼容 OpenAI API 规范：
- **API 接口地址 (Base URL)**：`http://127.0.0.1:18610/v1`
- **API Key**：启动控制台打印的 `m2a_xxxxxxxxxxxx`
- **支持端点**：
  - 视频生成：`POST /v1/videos`
  - 对话流式：`POST /v1/chat/completions`
  - 图像生成：`POST /v1/images/generations`
