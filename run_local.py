#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Muse 视频工作台 · 本地免 Docker 一键运行器
-------------------------------------------
无需云服务器，无需 Docker，无需配置 Linux 服务。
自动管理 Python 虚拟环境与依赖、本地浏览器驱动、API 后端服务与 Web 前端界面。
"""

from __future__ import annotations

import argparse
import atexit
import http.server
import json
import os
import platform
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(BASE_DIR, "backend_src")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_FILE = os.path.join(DATA_DIR, "local_config.json")
VENV_DIR = os.path.join(BASE_DIR, ".venv_local")
WEB_DIR = os.path.join(BASE_DIR, "web")

# 真正的服务端仓库。原先写的是 yys9253462-gif/muse2api（不存在），
# yys9253462-gif/muse-video-installer 是本安装器自己的仓库，别搞混。
# 地址写错的后果平时看不出来（ensure_backend_source 见 backend_src 已存在就
# 直接 return），但一旦删掉 backend_src/ 想重新拉源码就会失败。
MUSE2API_REPO = "czg86389-hub/muse2api"
MUSE2API_REF = "main"

DEFAULT_API_PORT = 18610
DEFAULT_WEB_PORT = 8090
DEFAULT_CDP_PORT = 19210

# 前端静态服务默认只绑回环地址。API Key 会被注入前端页面（见 prepare_web_index），
# 绑 0.0.0.0 等于把账号额度暴露给整个局域网。需要局域网访问时用 --web-host 指定。
WEB_BIND_HOST = "127.0.0.1"

# ----------------- 终端颜色 -----------------
C_RED = "\033[0;31m" if sys.stdout.isatty() else ""
C_GRN = "\033[0;32m" if sys.stdout.isatty() else ""
C_YEL = "\033[0;33m" if sys.stdout.isatty() else ""
C_CYN = "\033[0;36m" if sys.stdout.isatty() else ""
C_BLD = "\033[1m" if sys.stdout.isatty() else ""
C_DIM = "\033[2m" if sys.stdout.isatty() else ""
C_OFF = "\033[0m" if sys.stdout.isatty() else ""

def log_info(msg: str):
    print(f"  {C_CYN}ℹ{C_OFF} {msg}")

def log_ok(msg: str):
    print(f"  {C_GRN}✓{C_OFF} {msg}")

def log_warn(msg: str):
    print(f"  {C_YEL}!{C_OFF} {msg}")

def log_err(msg: str):
    print(f"  {C_RED}✗{C_OFF} {msg}")

def log_step(msg: str):
    print(f"\n{C_BLD}▸ {msg}{C_OFF}")


# ----------------- 浏览器探测 -----------------
def detect_system_browser() -> str:
    """自动探测系统安装的 Chrome / Edge / Chromium。"""
    custom = os.environ.get("MUSE2API_CHROMIUM", "").strip()
    if custom and (os.path.isfile(custom) or shutil.which(custom)):
        return custom

    candidates = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
    ]
    for c in candidates:
        if os.path.isfile(c) or shutil.which(c):
            return c
    return ""


# ----------------- 端口可用性探测 -----------------
def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0

def find_available_port(start_port: int) -> int:
    port = start_port
    while is_port_in_use(port):
        port += 1
    return port


# ----------------- 配置读取与保存 -----------------
def load_or_init_config(api_port: int | None = None, web_port: int | None = None) -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    cfg = {}
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    if not cfg.get("api_key"):
        cfg["api_key"] = f"m2a_{secrets.token_hex(16)}"

    if api_port:
        cfg["api_port"] = api_port
    elif not cfg.get("api_port"):
        cfg["api_port"] = DEFAULT_API_PORT if not is_port_in_use(DEFAULT_API_PORT) else find_available_port(DEFAULT_API_PORT)

    if web_port:
        cfg["web_port"] = web_port
    elif not cfg.get("web_port"):
        cfg["web_port"] = DEFAULT_WEB_PORT if not is_port_in_use(DEFAULT_WEB_PORT) else find_available_port(DEFAULT_WEB_PORT)

    cfg["cdp_port"] = DEFAULT_CDP_PORT

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    return cfg


# ----------------- Python 解释器与虚拟环境 -----------------
def find_working_python() -> str:
    """寻找支持创建带 pip 的完整 venv 的 Python 3 解释器。"""
    # 优先检测 Homebrew / 系统中 pip 完好的稳定 Python 版本 (3.11 / 3.10)
    priority_candidates = [
        "/opt/homebrew/bin/python3.11",
        "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3",
        "/opt/homebrew/bin/python3.10",
        "/usr/local/bin/python3.11",
        "/usr/local/bin/python3.10",
    ]
    other_cmds = ["python3.11", "python3.10", "python3.12", "python3", "python"]
    candidates = [p for p in priority_candidates if os.path.isfile(p)]
    for cmd in other_cmds:
        path = shutil.which(cmd)
        if path and path not in candidates:
            candidates.append(path)
    if sys.executable not in candidates:
        candidates.append(sys.executable)

    import tempfile
    for py in candidates:
        try:
            res = subprocess.run(
                [py, "-c", "import sys; exit(0 if sys.version_info >= (3, 8) else 1)"],
                capture_output=True,
                timeout=5,
            )
            if res.returncode == 0:
                # 真实测试该 Python 的 ensurepip / venv 是否正常（避免 3.12/3.14 Homebrew pyexpat 冲突）
                test_dir = tempfile.mkdtemp(prefix="test_venv_")
                try:
                    v_res = subprocess.run(
                        [py, "-m", "venv", test_dir],
                        capture_output=True,
                        timeout=15,
                    )
                    if v_res.returncode == 0:
                        # 确保里面生成了 pip
                        is_win = platform.system() == "Windows"
                        test_pip = os.path.join(test_dir, "Scripts" if is_win else "bin", "pip.exe" if is_win else "pip")
                        if os.path.isfile(test_pip):
                            return py
                finally:
                    shutil.rmtree(test_dir, ignore_errors=True)
        except Exception:
            continue

    return sys.executable

def ensure_venv() -> str:
    """确保虚拟环境建立并返回虚拟环境中的 python 路径。"""
    is_win = platform.system() == "Windows"
    venv_py = os.path.join(VENV_DIR, "Scripts" if is_win else "bin", "python.exe" if is_win else "python")
    venv_pip = os.path.join(VENV_DIR, "Scripts" if is_win else "bin", "pip.exe" if is_win else "pip")

    if not os.path.isfile(venv_py) or not os.path.isfile(venv_pip):
        if os.path.exists(VENV_DIR):
            shutil.rmtree(VENV_DIR, ignore_errors=True)
        log_step("创建隔离 Python 虚拟环境 (.venv_local)")
        base_py = find_working_python()
        log_info(f"使用底层 Python: {base_py}")
        try:
            subprocess.run([base_py, "-m", "venv", VENV_DIR], check=True)
            log_ok("虚拟环境创建完成")
        except Exception as e:
            log_err(f"创建虚拟环境失败: {e}")
            log_warn("将尝试使用当前环境执行...")
            return sys.executable

    # 检查核心依赖
    test_cmd = [venv_py, "-c", "import fastapi, uvicorn, pydantic, requests, websocket; print('OK')"]
    check = subprocess.run(test_cmd, capture_output=True)
    if check.returncode != 0:
        log_step("安装所需运行依赖 (FastAPI, Uvicorn, Requests, Websocket 等)")
        pip_cmd = [
            venv_pip, "install", "--upgrade",
            "fastapi>=0.115",
            "uvicorn[standard]>=0.32",
            "pydantic>=2.9",
            "requests>=2.32",
            "websocket-client>=1.8",
            "python-multipart>=0.0.9",
        ]
        log_info("正在执行 pip 安装，请稍候...")
        p_res = subprocess.run(pip_cmd)
        if p_res.returncode == 0:
            log_ok("依赖安装成功！")
        else:
            log_err("依赖安装遇到问题，请检查网络后重试。")
            sys.exit(1)

    return venv_py


# ----------------- 源码同步 -----------------
def ensure_backend_source():
    """下载或解压 muse2api 源码。"""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    core_file = os.path.join(RUNTIME_DIR, "app.py")
    if os.path.isfile(core_file):
        return

    log_step("获取 muse2api 核心服务端源码")
    tar_url = f"https://codeload.github.com/{MUSE2API_REPO}/tar.gz/refs/heads/{MUSE2API_REF}"
    log_info(f"正在从 GitHub 下载后端代码 ({tar_url}) ...")

    import io
    import tarfile
    req = urllib.request.Request(tar_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            # 找到顶层目录并解压
            members = tar.getmembers()
            top_prefix = members[0].name.split("/")[0] + "/"
            for m in members:
                if m.name.startswith(top_prefix):
                    m.name = m.name[len(top_prefix):]
                    if m.name:
                        tar.extract(m, RUNTIME_DIR)
        log_ok("后端源码部署就绪！")
    except Exception as e:
        log_err(f"源码下载解压失败: {e}")
        log_warn("如果直连 GitHub 不畅，请开启终端代理后再试，或将 muse2api 源码直接解压至 backend_src 目录。")
        sys.exit(1)


# ----------------- Web 界面更新注入 -----------------
def prepare_web_index(api_port: int, api_key: str):
    """确保 web 目录存在，并把配置改成**运行时**拉取，不再把 Key 写进文件。

    旧实现把 API Key 明文注入 index.html（`[Local Auto-Init]` 块），有两个问题：
      1. Key 永久留在磁盘上 —— 目录被复制/打包/分享就跟着泄露；
      2. 注入只做一次（有标记保护），Key 轮换后前端永远拿不到新值。

    现在改为：HTML 里只留一个 `<script src="/local-config.js">` 引用，
    Key 由本地 web 服务在运行时生成（见 start_web_server 的 do_GET）。
    文件里不再有任何 Key，grep m2a_ 应该是空的。
    """
    os.makedirs(WEB_DIR, exist_ok=True)
    index_path = os.path.join(WEB_DIR, "index.html")
    if not os.path.isfile(index_path):
        log_err("未找到 web/index.html 文件！")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 1) 清掉历史遗留的明文 Key 注入块（可能已在磁盘上）。
    #    磁盘上有两种历史形态，都见过：
    #      a) 裸的顶层语句（旧版 prepare_web_index 注入的原始形态）
    #      b) 包在 IIFE 里的（后来手改过）
    #    所以结尾只允许匹配「可有可无的 IIFE 闭合」，不能强制要求它存在。
    #    反过来也不能只删到 setItem 那行 —— 那样会留下残缺的 "}" / "})();"
    #    让页面直接 SyntaxError。
    if "[Local Auto-Init]" in html:
        import re as _re
        html = _re.sub(
            r"[ \t]*// \[Local Auto-Init\][\s\S]*?"
            r"localStorage\.setItem\(cfgKey, JSON\.stringify\(cur\)\);"
            r"(?:\s*\}\s*)?(?:\s*\)\(\)\s*;?)?",
            "", html)
        if "[Local Auto-Init]" in html:
            log_warn("未能完整清除历史 Key 注入块，请检查 web/index.html")
        html = html.replace("\n\n\n", "\n\n")

    # 2) 确保引了 /local-config.js（Key 由此运行时下发）
    if "/local-config.js" not in html:
        html = html.replace(
            "<script>",
            '<script src="/local-config.js"></script>\n<script>', 1)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)


# ----------------- 静态 Web 服务 -----------------
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

def start_web_server(port: int, directory: str, host: str = WEB_BIND_HOST,
                     api_port: int = 18610, api_key: str = ""):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)
        def log_message(self, format, *args):
            pass # 抑制普通请求日志，保持终端整洁

        def do_GET(self):
            # 动态下发本地配置。Key 不写进 index.html —— 之前 prepare_web_index
            # 会把明文 Key 永久注入 HTML 文件，目录一旦被复制/分享就跟着泄露，
            # 而且注入只做一次，Key 轮换了前端也不更新。这里改为运行时生成，
            # 文件里永远没有 Key。
            if self.path.split("?")[0] == "/local-config.js":
                body = ("window.__LOCAL_CFG__ = %s;\n" % json.dumps(
                    {"base": f"http://127.0.0.1:{api_port}", "key": api_key},
                    ensure_ascii=False)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    # 只绑回环地址：API Key 曾明文注入前端，绑 0.0.0.0 等于把额度暴露给整个局域网。
    # 需要局域网访问时用 --web-host 显式指定。
    httpd = ThreadedHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


# ----------------- 启动状态监控与打印 -----------------
# ----------------- 单实例保护 -----------------
PIDFILE = os.path.join(DATA_DIR, "run_local.pid")


def _read_pidfile() -> int:
    """读出 pidfile 里的 PID；文件不存在/损坏返回 0。"""
    try:
        with open(PIDFILE, encoding="utf-8") as f:
            return int((f.read().strip() or "0").split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def _pid_alive(pid: int) -> bool:
    """进程是否还活着。os.kill(pid, 0) 只做权限/存在性检查，不真发信号。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不属于当前用户


def ensure_single_instance(cfg: dict):
    """已有实例在跑就复用它，不要静默起第二个。

    为什么必须要有这个：macOS 允许 0.0.0.0:P 和 127.0.0.1:P 同时绑定，所以
    第二个 run_local.py 不会报「端口被占用」，而是直接起来。然后：

      * 两个进程共享 backend_src/data/*.json，而 store.py 的锁是**进程内**的，
        跨进程毫无保护 → 账号池和任务记录互相覆盖；
      * 两个进程共享同一个 Chrome（engine 检测到 CDP 端口已被占用就直接复用），
        两边都能下发点击，会串话；
      * 保活守护跑两份，每 15 分钟各打一次 /api/session。

    之前只在**首次生成配置**时探测端口，配置已存在就直接复用，所以完全拦不住。
    """
    # 1) pidfile 命中且进程活着 → 复用
    old_pid = _read_pidfile()
    if _pid_alive(old_pid):
        print(f"\n{C_YEL}检测到已有实例在运行 (PID {old_pid})。{C_OFF}")
        log_info(f"前端: http://127.0.0.1:{cfg['web_port']}/")
        log_info(f"管理面板: http://127.0.0.1:{cfg['api_port']}/admin?key={cfg['api_key']}")
        log_info("本次启动已取消。要强制重启请先在旧终端按 Ctrl+C，或结束该进程。")
        sys.exit(0)

    # 2) pidfile 过期（进程已死）→ 清掉重写
    if old_pid:
        log_info(f"清理残留 pidfile（PID {old_pid} 已不存在）")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()}\n")

    # 3) 没有 pidfile 但端口已被占 —— 多半是别人手动起了后端，或 pidfile 被删了。
    #    这里必须挡住，否则就是上面那三种串话。
    for label, port in (("API", cfg["api_port"]), ("Web", cfg["web_port"])):
        if is_port_in_use(port):
            log_err(f"{label} 端口 {port} 已被占用，但找不到本程序的 pidfile。")
            log_warn("可能有另一个 run_local.py 在跑，或后端被手动启动过。")
            log_info(f"先关掉占用者（lsof -nP -iTCP:{port} -sTCP:LISTEN）再重试；")
            log_info("若确认无人占用，可删除 " + PIDFILE + " 后重试。")
            sys.exit(1)


def cleanup_pidfile():
    """退出时清掉自己的 pidfile（只删属于自己的那份）。"""
    if _read_pidfile() == os.getpid():
        try:
            os.remove(PIDFILE)
        except OSError:
            pass


def print_status_banner(cfg: dict, browser_path: str):
    api_port = cfg["api_port"]
    web_port = cfg["web_port"]
    api_key = cfg["api_key"]

    print("\n" + "=" * 60)
    print(f" {C_BLD}{C_GRN}Muse 视频工作台 · 本地免 Docker 服务已就绪{C_OFF}")
    print("=" * 60)
    print(f"  {C_BLD}网页地址 (创作控制台):{C_OFF}  {C_CYN}http://127.0.0.1:{web_port}/{C_OFF}")
    print(f"  {C_BLD}接口地址 (API Base):{C_OFF}     http://127.0.0.1:{api_port}/v1")
    print(f"  {C_BLD}账号池管理面板:{C_OFF}         http://127.0.0.1:{api_port}/admin?key={api_key}")
    print(f"  {C_BLD}API 鉴权密钥 (API Key):{C_OFF}  {C_YEL}{api_key}{C_OFF}")
    print(f"  {C_BLD}本地浏览器内核:{C_OFF}         {browser_path}")
    print("-" * 60)
    print("  使用指南：")
    print(f"  1. 浏览器打开 {C_CYN}http://127.0.0.1:{web_port}/{C_OFF} 即可体验文生视频。")
    print(f"  2. 如需导入 muse.ai 账号，可运行：")
    print(f"     {C_BLD}python3 tools/get_muse_cookie.py --base http://127.0.0.1:{api_port} --key {api_key}{C_OFF}")
    print(f"  3. 按 {C_RED}Ctrl + C{C_OFF} 即可随时优雅退出本地服务。")
    print("=" * 60 + "\n")


# ----------------- 导号工具快捷调用 -----------------
def run_import_account(cfg: dict):
    tool_path = os.path.join(BASE_DIR, "tools", "get_muse_cookie.py")
    if not os.path.isfile(tool_path):
        log_err("未找到 tools/get_muse_cookie.py 工具")
        return
    cmd = [
        sys.executable,
        tool_path,
        "--base", f"http://127.0.0.1:{cfg['api_port']}",
        "--key", cfg["api_key"],
    ]
    print(f"\n{C_BLD}▸ 启动账号导入工具...{C_OFF}")
    subprocess.run(cmd)


# ----------------- 主入口 -----------------
def main():
    parser = argparse.ArgumentParser(description="Muse 视频工作台 · 本地免 Docker 运行器")
    parser.add_argument("--api-port", type=int, help="后端 API 端口 (默认 18610)")
    parser.add_argument("--web-port", type=int, help="前端 Web 端口 (默认 8090)")
    parser.add_argument("--import-account", action="store_true", help="启动账号导入工具")
    parser.add_argument("--browser", type=str, help="指定 Chrome/Chromium 浏览器路径")
    parser.add_argument("--open-browser", action="store_true", help="服务启动后自动打开网页")
    parser.add_argument("--web-host", type=str, default=WEB_BIND_HOST,
                        help="前端 Web 服务绑定地址 (默认 127.0.0.1；填 0.0.0.0 会暴露给局域网，不推荐)")
    args = parser.parse_args()

    cfg = load_or_init_config(args.api_port, args.web_port)

    if args.import_account:
        run_import_account(cfg)
        return

    # 单实例保护：必须在起任何服务之前挡掉，否则第二个实例已经造成串话了。
    ensure_single_instance(cfg)
    atexit.register(cleanup_pidfile)

    print(f"\n{C_BLD}Muse 视频工作台 —— 正在启动本地环境...{C_OFF}")

    # 1. 检查浏览器
    browser = args.browser or detect_system_browser()
    if not browser:
        log_err("未找到 Chrome / Edge / Chromium 浏览器！")
        log_warn("本工作台在本地运行需要 Chrome 或 Edge 浏览器作为无头内核。")
        log_info("请安装 Google Chrome (https://www.google.cn/chrome/) 后重试。")
        sys.exit(1)
    log_ok(f"检测到浏览器内核: {browser}")

    # 2. 检查并准备 Python 依赖
    venv_py = ensure_venv()

    # 3. 准备后端代码
    ensure_backend_source()

    # 4. 准备前端代码
    prepare_web_index(cfg["api_port"], cfg["api_key"])

    # 5. 启动前端 Web 服务
    log_step(f"启动前端 Web 服务 (端口 {cfg['web_port']})")
    web_server = start_web_server(cfg["web_port"], WEB_DIR, args.web_host,
                                  cfg["api_port"], cfg["api_key"])
    log_ok("前端服务已启动")

    # 6. 配置后端环境变量并启动后端进程
    log_step(f"启动后端 API 服务 (端口 {cfg['api_port']})")
    env = os.environ.copy()
    env["MUSE2API_KEY"] = cfg["api_key"]
    env["MUSE2API_HOST"] = "127.0.0.1"
    env["MUSE2API_PORT"] = str(cfg["api_port"])
    env["MUSE2API_CHROMIUM"] = browser
    env["MUSE2API_CDP_PORT"] = str(cfg["cdp_port"])
    env["MUSE2API_HOME"] = RUNTIME_DIR
    env["MUSE2API_HOME_DIR"] = BASE_DIR
    env["MUSE2API_PROFILE_ROOT"] = os.path.join(DATA_DIR, "profiles")
    env["MUSE2API_PUBLIC_BASE"] = f"http://127.0.0.1:{cfg['api_port']}"
    # 本地模式标记：后端据此禁用「一键在线升级」和「代码推送到上游」
    # （前者会用 GitHub tarball 覆盖 backend_src/ 下除 3 个白名单文件外的所有内容，
    #  本地版的长视频分段等改动会全部丢失；后者会把本地代码推到上游 main）。
    env["MUSE2API_LOCAL_MODE"] = "1"
    # 前端端口：后端本地模式下用它把本地前端来源加进 CORS 白名单
    # （前端 8090 与 API 18610 是不同端口，属于跨源）。
    env["MUSE2API_WEB_PORT"] = str(cfg["web_port"])

    # ffmpeg：长视频分段后要靠它合成。探测不到不算致命 —— 后端会降级成
    # 「只返回分段列表」，但要提前告诉用户，否则他等 40 分钟才发现拿不到合成片。
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        env["MUSE2API_FFMPEG"] = ffmpeg
        env["MUSE2API_FFPROBE"] = ffprobe
        log_ok(f"ffmpeg 已就绪，长视频将自动分段并合成 ({ffmpeg})")
    else:
        missing = "ffmpeg" if not ffmpeg else "ffprobe"
        log_warn(f"未检测到 {missing}：长视频仍会自动分段，但只会返回分段列表，"
                 f"不会合成成一条。macOS 可执行 brew install ffmpeg")

    cmd = [
        venv_py,
        "-m", "uvicorn", "app:app",
        "--host", "127.0.0.1",
        "--port", str(cfg["api_port"]),
        "--log-level", "info",
    ]

    backend_proc = subprocess.Popen(
        cmd,
        cwd=RUNTIME_DIR,
        env=env,
    )

    # 7. 等待后端健康检查
    ready = False
    models_url = f"http://127.0.0.1:{cfg['api_port']}/v1/models"
    for _ in range(30):
        try:
            req = urllib.request.Request(models_url, headers={"Authorization": f"Bearer {cfg['api_key']}"})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            pass
        if backend_proc.poll() is not None:
            break
        time.sleep(1)

    if not ready and backend_proc.poll() is not None:
        log_err("后端服务异常退出，请查看上方日志。")
        web_server.shutdown()
        sys.exit(1)

    log_ok("后端服务就绪！")

    print_status_banner(cfg, browser)

    if args.open_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{cfg['web_port']}/")
        except Exception:
            pass

    # 循环挂起，监听终止信号
    try:
        backend_proc.wait()
    except KeyboardInterrupt:
        print(f"\n{C_YEL}正在安全关闭本地服务...{C_OFF}")
        backend_proc.terminate()
        try:
            backend_proc.wait(timeout=5)
        except Exception:
            backend_proc.kill()
        web_server.shutdown()
        log_ok("服务已全部退出，欢迎再次使用！")
    finally:
        # 无论正常退出、异常还是 Ctrl+C，都别把 pidfile 留在那里，
        # 否则下次启动会以为「已有实例」而拒绝运行。
        cleanup_pidfile()

if __name__ == "__main__":
    main()
