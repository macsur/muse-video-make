#!/usr/bin/env bash
# ============================================================================
#  Muse 视频工作台 —— 一键安装脚本
#
#  装好之后你能：
#    · 在浏览器里打开一个网页，输入一句话就生成视频
#    · 让别的软件（Cherry Studio、NextChat 等）连上它调用接口
#
#  用法（下面统一写作 install.sh，实际就是你下载时的文件名）：
#    交互安装：   sudo bash install.sh
#    全自动安装： sudo bash install.sh --yes
#    看会做什么： sudo bash install.sh --dry-run
#    其他：       sudo bash install.sh --status | --uninstall | --help
#
#  支持 Debian/Ubuntu（apt）、CentOS/RHEL（yum/dnf）、Alpine（apk）
# ============================================================================

set -uo pipefail

# 脚本自身的名字 —— 提示里一律用它，这样不管用户下载后叫 install.sh、
# setup.sh 还是别的，复制粘贴出来的命令都是对的。
#
# ⚠️ 但**管道运行**时必须特判（实测踩过）：
#    `curl ... | bash` / `cat install.sh | bash` 时 $0 是解释器名（bash / sh），
#    basename 得到 "bash" —— 于是收尾里会印出
#        sudo bash bash --status
#    这种 nonsense，小白照抄必报错。这正是「一条命令安装」最常见的用法。
#    判定：$0 是常见 shell 名 → 认为是从管道/标准输入来的，改用固定名 install.sh。
SELF="$(basename "${0:-}")"
# 记住「脚本文件在哪」。管道运行时这里为空 —— 后面安装流程会**把脚本自己
# 抄一份进安装目录**，让 --status/--upgrade 这些生命周期命令永远有个可执行的副本，
# 不再依赖「用户当初把脚本下到哪了」。这一步是小白最需要的兜底：
# 他很可能装完就把脚本删了 / 换成手机看，回头想升级时两手空空。
SELF_PATH=""
# 判定很简单：$0 指向一个**真实存在的文件**，就认为「知道自己在哪」。
# ⚠️ 早先写成匹配 /*、./*、../* 白名单，漏掉了最常见的一种调用：
#    `cd /tmp/dir && bash install.sh`（相对名、不带 ./）—— 那是小白
#    解压完之后的典型动作，结果自存功能直接失效。改成「存在即认」。
if [ -f "${0:-}" ]; then
  SELF_PATH="$0"
fi
case "$SELF" in
  bash|sh|dash|ash|zsh|ksh|"") SELF="install.sh"; SELF_PATH="" ;;
esac

# ── 自我定位「我是从哪个安装目录跑起来的」──────────────────────────────
#
# 我们把脚本副本存进安装目录（见 save_state），就是为了让用户随时能
# `bash /opt/xxx/install.sh --status`。但副本里的 DEFAULT_DIR 还是 /opt/mvw ——
# 装到别的目录时副本会认不出自己是谁（实测：跑副本报「还没装过 /opt/mvw」）。
# 解法：脚本启动时看看自己躺在哪里 —— 如果**旁边就有 install.conf**，
# 说明「我就在某个安装目录里」，那就把那个目录当作默认安装目录。
INSTALL_DIR_SELF=""
if [ -n "$SELF_PATH" ] && [ -f "$SELF_PATH" ]; then
  _self_dir="$(cd "$(dirname "$SELF_PATH")" 2>/dev/null && pwd)"
  if [ -n "$_self_dir" ] && [ -f "$_self_dir/install.conf" ]; then
    INSTALL_DIR_SELF="$_self_dir"
  fi
fi

SCRIPT_VERSION="1.1.1"
# 前缀统一用 mvw-（Muse Video Workbench），避免和用户已有的 muse-video / muse2api
# 等同名服务撞车 —— 曾因默认名与既有服务的 unit 重名，把别人的服务覆盖掉。
APP_NAME="mvw"
APP_LABEL="Muse 视频工作台"
DEFAULT_DIR="/opt/mvw"
DEFAULT_API_PORT=18610
DEFAULT_WEB_PORT=8090
# 装哪一份 muse2api？
#   指向自己的 fork —— 它基于上游 v1.5.2，并叠加了 11 项缺陷修复
#   （FIFO 队列调度 / CDP 单读循环 / 参数校验 / 405 鉴权绕过等）。
#   ⚠️ 上游原仓库 czg86389-hub/muse2api 当前**不含**这些修复，
#   因此这里不能用上游地址，否则装出来的版本会缺修复。
#   待上游合并 PR（czg86389-hub/muse2api#5）后，可考虑切回上游。
MUSE2API_REPO="yys9253462-gif/muse2api"
# 装仓库里的哪个分支/标签。
# ⚠️ 必须显式写死，不能让 git 用「默认分支」——这是个踩过的坑：
#   `git clone --depth 1 <url>` 不加 -b 时**只拉默认分支**。曾经修复代码放在
#   其它分支、默认分支还是旧版，脚本却提示「安装成功」，用户装完才发现功能是坏的，
#   排查了很久。显式 -b 让「装哪一版」变成脚本里看得见的一行，而不是仓库设置里的隐藏状态。
#   同时下游还有 verify_installed_fixes() 自检兜底，双保险。
MUSE2API_REF="main"
# 本脚本自己所在的仓库 —— 导号小工具托管在这里，别指向上游
# （上游的 tools/get_muse_cookie.py 是原版，不能多账号、也没有连通自检）。
SELF_REPO="yys9253462-gif/muse-video-installer"

# ── 颜色（注意：变量名不与业务变量冲突） ──────────────────────────────
C_RED=''; C_GRN=''; C_YEL=''; C_CYN=''; C_BLD=''; C_DIM=''; C_OFF=''
if [ -t 1 ]; then
  C_RED=$'\033[0;31m'; C_GRN=$'\033[0;32m'; C_YEL=$'\033[0;33m'
  C_CYN=$'\033[0;36m'; C_BLD=$'\033[1m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
fi

# ── 输出函数 ─────────────────────────────────────────────────────────
# ⚠️ 铁律：**所有**面向人的输出一律走 stderr（>&2），
#    stdout 只留给「函数返回值」（如 pick_port / used_ports / gen_key）。
#    曾经因为 warn() 写 stdout，导致 $(pick_port) 的命令替换结果里
#    混进了「端口 18610 已被占用，换一个」这句告警文本，把 compose
#    写成语法错误的垃圾 —— 这个坑踩过一次，绝不能再犯。
say()  { printf '%s\n' "$*" >&2; }
ok()   { printf '  %s✓%s %s\n' "$C_GRN" "$C_OFF" "$*" >&2; }
warn() { printf '  %s!%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
err()  { printf '  %s×%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
step() { printf '\n%s▸ %s%s\n' "$C_BLD" "$*" "$C_OFF" >&2; }
dim()  { printf '%s%s%s\n' "$C_DIM" "$*" "$C_OFF" >&2; }

# 菜单/提示一律走 stderr，stdout 只留给返回值
info() { printf '%s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

# ── 参数与状态 ────────────────────────────────────────────────────────
ASSUME_YES=0
DRY_RUN=0
NO_DEPS=0
DO_STATUS=0
DO_UNINSTALL=0
DO_UPGRADE=0
DO_HELP=0
INSTALL_DIR="${INSTALL_DIR_SELF:-$DEFAULT_DIR}"
API_PORT=""
WEB_PORT=""
DOMAIN=""
NO_DOMAIN=0
ADV_GIVEN=0
# 用户要了域名、但 DNS 没生效导致这次没配上的话，记在这里，验收清单里再提一次
DOMAIN_SKIPPED=""

RUN_LOG=""

usage() {
  cat <<EOF
${APP_LABEL} 一键安装脚本 v${SCRIPT_VERSION}

用法：sudo bash ${SELF} [选项]

常用：
  --yes, -y            全自动安装，所有问题用默认值（适合脚本/CI）
  --dry-run            只显示会做什么，不实际改动系统
  --status             看当前运行状态（也能找回地址和 API Key）
  --upgrade            升级到最新版
  --uninstall          卸载
  --help, -h           显示本帮助

进阶：
  --dir <路径>         安装到哪个目录（默认 ${DEFAULT_DIR}）
  --api-port <端口>    接口服务端口（默认自动挑，常用 ${DEFAULT_API_PORT}）
  --web-port <端口>    网页端口（默认自动挑，常用 ${DEFAULT_WEB_PORT}）
  --domain <域名>      给网页绑个域名并自动配 HTTPS（需要域名已解析到本机）
  --no-domain          不要域名（默认就是不要）
  --no-deps            不自动安装依赖，缺什么只告诉你

示例：
  sudo bash ${SELF}
  sudo bash ${SELF} --yes --web-port 8090
  sudo bash ${SELF} --domain video.example.com
EOF
}

# ── 「你是不是想打……」：给打错的参数一个最接近的建议 ────────────────
#
# ⚠️ 为什么值得单写一个函数（真人实测）：
#    小白打错 --uninstall 的概率极高，而最常见的错法是**少一个字母**
#    （--unstall / --uninstal）—— 光看两行字他根本发现不了差在哪。
#    更常见的是**不带你以为的横杠**：直接敲 `uninstall` / `status`。
#    这时只回一句「不认识的参数，用 --help 看用法」，等于把人晾在原地。
#    下面用最朴素的「编辑距离」找最近的合法选项，明确告诉他打哪个。
_levdist() {
  # 极简 Levenshtein 距离（纯 bash，字符串都很短，性能无所谓）
  local a="$1" b="$2" i j
  local la=${#a} lb=${#b}
  local prev cur
  local -a row
  for ((j = 0; j <= lb; j++)); do row[j]=$j; done
  for ((i = 1; i <= la; i++)); do
    prev=${row[0]}; row[0]=$i
    for ((j = 1; j <= lb; j++)); do
      cur=${row[j]}
      if [ "${a:i-1:1}" = "${b:j-1:1}" ]; then
        row[j]=$prev
      else
        local m=$prev
        [ "${row[j]}" -lt "$m" ] && m=${row[j]}
        [ "${row[j-1]}" -lt "$m" ] && m=${row[j-1]}
        row[j]=$((m + 1))
      fi
      prev=$cur
    done
  done
  printf '%s' "${row[lb]}"
}

suggest_arg() {
  local bad="$1"
  # 归一化：去掉前导横杠，转小写 —— 这样 `uninstall`/`-Uninstall` 都能对上
  local key="${bad#--}"; key="${key#-}"
  local had_dash=0
  case "$bad" in -*) had_dash=1 ;; esac
  key="$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]')"
  local best="" bestd=99 cand d
  for cand in yes dry-run no-deps status uninstall upgrade help dir api-port web-port domain no-domain; do
    d="$(_levdist "$key" "$cand")"
    if [ "$d" -lt "$bestd" ]; then bestd=$d; best=$cand; fi
  done
  # 冒号后面这段是「完全对上、但没写横杠」的情况 —— **小白最常犯的错**：
  # 直接把 `uninstall` / `status` 当子命令敲。这时距离是 0，必须也给出建议，
  # 否则最该帮的那一类人反而得不到提示。
  if [ "$bestd" = 0 ] && [ "$had_dash" = 0 ] && [ -n "$best" ]; then
    printf '%s' "--$best"; return 0
  fi
  # 距离阈值：宁可多猜一次，也别让小白卡住 —— 这个提示只是**建议**，
  # 猜错了顶多浪费一眼；猜对了就省掉他反复试错。实测常见错法：
  #   unstall→uninstall(2) / stauts→status(2,字母调位) / updat→upgrade(4,缩写)
  # 但也不能太松，否则乱敲个 `xyz` 都会被"建议"成 --yes。规则：
  #   · 太短（<=4 字符）→ 只容忍 2，够抓 stauts/updat 这类，
  #     又不至于把 `xyz`(→yes,3) 硬凑上；
  #   · 中等（5-7）→ 容忍 4；
  #   · 长词（>=8）→ 容忍 5。
  local lim=4
  if [ "${#key}" -le 4 ]; then lim=2
  elif [ "${#key}" -ge 8 ]; then lim=5
  fi
  if [ -n "$best" ] && [ "$bestd" -le "$lim" ] && [ "$bestd" -gt 0 ]; then
    printf '%s' "--$best"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)        ASSUME_YES=1 ;;
    --dry-run)       DRY_RUN=1 ;;
    --no-deps)       NO_DEPS=1 ;;
    --status)        DO_STATUS=1 ;;
    --uninstall)     DO_UNINSTALL=1 ;;
    --upgrade)       DO_UPGRADE=1 ;;
    --help|-h)       DO_HELP=1 ;;
    --dir)           INSTALL_DIR="${2:-}"; shift; [ -n "${INSTALL_DIR:-}" ] || die "--dir 后面要跟一个路径" ;;
    --dir=*)         INSTALL_DIR="${1#*=}"; [ -n "$INSTALL_DIR" ] || die "--dir= 后面要跟一个路径" ;;
    --api-port)      API_PORT="${2:-}"; shift; [ -n "${API_PORT:-}" ] || die "--api-port 后面要跟一个端口号（比如 --api-port 18610）"; ADV_GIVEN=1 ;;
    --api-port=*)    API_PORT="${1#*=}"; [ -n "$API_PORT" ] || die "--api-port= 后面要跟一个端口号"; ADV_GIVEN=1 ;;
    --web-port)      WEB_PORT="${2:-}"; shift; [ -n "${WEB_PORT:-}" ] || die "--web-port 后面要跟一个端口号（比如 --web-port 8090）"; ADV_GIVEN=1 ;;
    --web-port=*)    WEB_PORT="${1#*=}"; [ -n "$WEB_PORT" ] || die "--web-port= 后面要跟一个端口号"; ADV_GIVEN=1 ;;
    --domain)        DOMAIN="${2:-}"; shift; [ -n "${DOMAIN:-}" ] || die "--domain 后面要跟一个域名（比如 --domain video.example.com）"; ADV_GIVEN=1 ;;
    --domain=*)      DOMAIN="${1#*=}"; [ -n "$DOMAIN" ] || die "--domain= 后面要跟一个域名"; ADV_GIVEN=1 ;;
    --no-domain)     NO_DOMAIN=1; ADV_GIVEN=1 ;;
    *)
      # 打错的参数：先猜一个最像的，明确告诉他该敲哪个；猜不到再退回看帮助。
      _sug="$(suggest_arg "$1")"
      if [ -n "$_sug" ]; then
        die "不认识的参数：$1
       你是不是想打：$_sug ？
       全部用法：bash ${SELF} --help"
      fi
      die "不认识的参数：$1（用 --help 看用法）" ;;
  esac
  shift
done

if [ "$DO_HELP" = 1 ]; then usage; exit 0; fi

# 端口合法性
validate_port() {
  local p="$1" what="$2"
  [ -n "$p" ] || return 0
  case "$p" in *[!0-9]*) die "$what 必须是数字，你给的是「$p」" ;; esac
  if [ "$p" -lt 1024 ] || [ "$p" -gt 65535 ]; then
    die "$what 要在 1024–65535 之间，你给的是 $p"
  fi
}
validate_port "$API_PORT" "接口端口"
validate_port "$WEB_PORT" "网页端口"

# 端口别撞车
if [ -n "$API_PORT" ] && [ -n "$WEB_PORT" ] && [ "$API_PORT" = "$WEB_PORT" ]; then
  die "接口端口和网页端口不能是同一个（都是 $API_PORT）。给它们各分一个。"
fi

# 子命令互斥检查：--status / --uninstall / --upgrade 只能给一个
_SUB_CNT=0
_SUB_LIST=""
for _v in status uninstall upgrade; do
  eval "_cur=\$DO_$(printf '%s' "$_v" | tr 'a-z' 'A-Z')"
  if [ "$_cur" = 1 ]; then
    _SUB_CNT=$((_SUB_CNT + 1))
    _SUB_LIST="${_SUB_LIST:+$_SUB_LIST 和 }--$_v"
  fi
done
if [ "$_SUB_CNT" -gt 1 ]; then
  die "这几个参数一次只能给一个：$_SUB_LIST。
       你想干什么就留哪个，比如只看状态：sudo bash ${SELF} --status"
fi
unset _SUB_CNT _SUB_LIST _cur _v

# 域名清洗：用户常粘 https:// 和结尾斜杠
if [ -n "$DOMAIN" ]; then
  DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"
  DOMAIN="${DOMAIN%%/*}"; DOMAIN="${DOMAIN%%:*}"
fi

# ── 运行包装器（dry-run 只打印） ──────────────────────────────────────
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s %s\n' "$C_CYN" "$C_OFF" "$*"
    return 0
  fi
  printf '    %s$%s %s\n' "$C_DIM" "$C_OFF" "$*"
  "$@"
}

runsh() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s %s\n' "$C_CYN" "$C_OFF" "$*"
    return 0
  fi
  printf '    %s$%s %s\n' "$C_DIM" "$C_OFF" "$*"
  bash -c "$*"
}

# ── 交互函数（非终端 / --yes 时用默认值） ─────────────────────────────
ask() {
  local prompt="$1" def="$2" ans=""
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then printf '%s' "$def"; return 0; fi
  printf '  %s [%s]: ' "$prompt" "$def" >&2
  if ! read -r ans; then printf '%s' "$def"; return 0; fi   # EOF → 默认值
  printf '%s' "${ans:-$def}"
}

ask_yn() {
  local prompt="$1" def="$2" ans=""        # def: y 或 n
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then
    [ "$def" = y ] && return 0 || return 1
  fi
  while :; do
    printf '  %s [%s/%s]: ' "$prompt" \
      "$( [ "$def" = y ] && echo 'Y' || echo 'y')" \
      "$( [ "$def" = y ] && echo 'n' || echo 'N')" >&2
    if ! read -r ans; then          # EOF：确认类一律按「否」，不能默认放行
      [ "$def" = y ] && return 0 || return 1
    fi
    ans="$(printf '%s' "$ans" | tr -d '[:space:]')"
    [ -z "$ans" ] && { [ "$def" = y ] && return 0 || return 1; }
    case "$ans" in
      y|Y|yes|YES|Yes|true|1|是|是的|好|好的|对|要|嗯|可以|行|确定|確認) return 0 ;;
      n|N|no|NO|No|false|0|否|不|不要|不用|不是|取消|取消吧) return 1 ;;
      *) printf '  %s没看懂「%s」—— 请回答 y（是）或 n（否），直接回车用默认值%s\n' \
           "$C_YEL" "$ans" "$C_OFF" >&2 ;;
    esac
  done
}

# ── 环境探测 ──────────────────────────────────────────────────────────
OS_ID=""; OS_VER=""; PKG=""
detect_os() {
  if [ -f /etc/os-release ]; then
    # 在子 shell 里读，避免 os-release 里的 ID/VERSION/NAME 等通用变量名
    # 污染本脚本的同名变量（实测 VERSION 被覆盖成 "12 (bookworm)"）
    OS_ID="$( (. /etc/os-release 2>/dev/null; printf '%s' "${ID:-unknown}") )"
    OS_VER="$( (. /etc/os-release 2>/dev/null; printf '%s' "${VERSION_ID:-}") )"
  fi
  if   command -v apt-get >/dev/null 2>&1; then PKG=apt
  elif command -v dnf     >/dev/null 2>&1; then PKG=dnf
  elif command -v yum     >/dev/null 2>&1; then PKG=yum
  elif command -v apk     >/dev/null 2>&1; then PKG=apk
  else PKG=""
  fi
}

IS_ROOT=0
check_root() {
  [ "$(id -u)" = 0 ] && IS_ROOT=1
  if [ "$IS_ROOT" != 1 ]; then
    if command -v sudo >/dev/null 2>&1; then
      die "这个脚本要用管理员权限跑。请这样运行：
       sudo bash ${SELF} $*"
    fi
    die "这个脚本要用管理员权限跑，但这台机器上没有 sudo。
       请先切换到 root 再运行（执行：su -  然后重新跑本脚本）"
  fi
}

# 已用端口（宿主监听 + docker 已发布）
USED_PORTS_CACHE=""
used_ports() {
  if [ -z "$USED_PORTS_CACHE" ]; then
    USED_PORTS_CACHE="$(
      { ss -ltnH 2>/dev/null | awk '{print $4}' | sed 's/.*://'
        docker ps --format '{{.Ports}}' 2>/dev/null | tr ',' '\n' \
          | sed -n 's/.*:\([0-9][0-9]*\)->.*/\1/p'
      } | grep -E '^[0-9]+$' | sort -nu | tr '\n' ' '
    )"
  fi
  printf '%s' "$USED_PORTS_CACHE"
}

port_free() {
  local p="$1"
  case " $(used_ports) " in *" $p "*) return 1 ;; esac
  return 0
}

# 这个端口是不是「我们自己上次安装的」占着的？
# 重跑安装/改配置时，端口被自己的旧容器/旧网页服务占着是**正常且预期**的，
# 不该像被外人占用那样直接报错退出（否则幂等重跑会撞墙）。
#
# 有两种「自己人」：
#   1) 接口端口 —— Docker 容器 $CONTAINER_NAME 发布的端口
#   2) 网页端口 —— systemd 服务 $WEB_UNIT 跑的静态服务器
# 两类都要认，否则重跑时会卡在网页端口上（实测踩过）。
port_owned_by_us() {
  local p="$1"

  # ① Docker 容器发布的端口
  if command -v docker >/dev/null 2>&1; then
    local ports
    ports="$(docker inspect "$CONTAINER_NAME" \
               --format '{{range $p, $conf := .NetworkSettings.Ports}}{{$p}} {{end}}' \
               2>/dev/null)"
    case " $ports " in *":${p}/tcp "*) return 0 ;; esac
    ports="$(docker port "$CONTAINER_NAME" 2>/dev/null | tr -d ' ' | tr '\n' ' ')"
    case " $ports " in *"0.0.0.0:${p} "*) return 0 ;; esac
  fi

  # ② 我们自己的网页 systemd 服务占的端口
  #    判据：该单元 active，且它的命令行里出现了这个端口
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet "$WEB_UNIT" 2>/dev/null; then
      local cmdline
      cmdline="$(systemctl show "$WEB_UNIT" -p ExecStart --value 2>/dev/null)"
      case "$cmdline" in
        *":${p} "|*":${p}\""|*" ${p} "|*" ${p}\""|*":${p}'"'"'*) return 0 ;;
        *) case "$cmdline" in *"${p}"*) return 0 ;; esac ;;
      esac
    fi
  fi

  return 1
}

pick_port() {
  local p="$1"
  local moved=0
  while ! port_free "$p"; do
    # 是自家旧容器占的 → 不换端口，直接沿用（它会原地重建）
    if port_owned_by_us "$p"; then
      printf '%s' "$p"
      return 0
    fi
    # ⚠️ 文案分两种：
    #    真实安装时说「已被占用，换一个」是对的（脚本真的会自动往上找）；
    #    但在 dry-run 里这么说会让小白**误以为必须自己手动换端口** ——
    #    实测时 dry-run 打出「端口 18610 已被占用，换一个」，
    #    而 18610 只是这台机器上别的服务占的，脚本本来就会自动避开。
    #    dry-run 要说清楚"这是自动的，不用管"。
    if [ "$DRY_RUN" = 1 ]; then
      if [ "$moved" = 0 ]; then
        say "    [dry-run] 端口 $p 被占了，脚本会自动往上找空闲端口（不用管）"
      fi
    else
      warn "端口 $p 已被占用，换一个"
    fi
    moved=1
    p=$((p + 1))
    if [ "$p" -gt 65535 ]; then die "找不到空闲端口了，请用 --web-port 手动指定"; fi
  done
  printf '%s' "$p"
}

# 显式指定端口时的检查：自家容器占着 = 放行；外人占着 = 报错
require_port_usable() {
  local p="$1" what="$2"
  if port_free "$p"; then return 0; fi
  port_owned_by_us "$p" && return 0
  die "${what}端口 $p 已被别的程序占用。换个端口，或先停掉占它的服务。
       查是谁占的：sudo ss -lntp | grep :$p"
}

# 安装目录能否创建/写入？提前拦，避免下载完几百 MB 才失败。
check_install_dir_writable() {
  # 已有目录：校验写权限
  if [ -d "$INSTALL_DIR" ]; then
    if [ ! -w "$INSTALL_DIR" ]; then
      die "目录 $INSTALL_DIR 已存在但没有写权限。换个 --dir，或 sudo chown 一下。"
    fi
    if [ "$DRY_RUN" = 1 ]; then
      say "    [dry-run] 安装目录已存在且可写：$INSTALL_DIR"
    else
      ok "安装目录可用（已存在）：$INSTALL_DIR"
    fi
    return 0
  fi
  # 目录不存在：往上一层找已存在且可写的祖先
  local probe="$INSTALL_DIR"
  while [ ! -d "$probe" ]; do
    local parent; parent="$(dirname "$probe")"
    [ "$parent" = "$probe" ] && break
    probe="$parent"
  done
  if [ -w "$probe" ]; then
    if [ "$DRY_RUN" = 1 ]; then
      say "    [dry-run] 安装目录将创建：$INSTALL_DIR（上级 $probe 可写）"
    else
      ok "安装目录将创建：$INSTALL_DIR"
    fi
    return 0
  fi
  die "创建不了目录 $INSTALL_DIR —— 它的上级 $probe 没有写权限。
       换个位置：sudo bash ${SELF} --dir /你的/可写/路径"
}

# 冲突保护：目标 systemd 单元 / 容器名如果已被「别人的服务」占着，就停下别动。
# 背景：本脚本按安装目录派生 unit 名（如 /opt/mvw → mvw-web.service）。
# 万一派生出的名字和机器上既有服务重名，直接写会把人家的服务覆盖掉
# （开发期就真发生过：默认名与既有 muse-video-web.service 撞名，把正式服务删了）。
assert_no_unit_conflict() {
  [ "$DRY_RUN" = 1 ] && return 0
  local unit="/etc/systemd/system/${WEB_UNIT}"
  [ -f "$unit" ] || return 0
  # 是我们的（内容里带我们的安装目录，或带我们的 Description）→ 放行，会原地重建
  if grep -q -- "$INSTALL_DIR" "$unit" 2>/dev/null; then return 0; fi
  if grep -q -- "$APP_LABEL (static site)" "$unit" 2>/dev/null; then return 0; fi
  die "服务名冲突：$unit 已经存在，而且不是本脚本装的（指向别的目录）。
       为避免覆盖别人的服务，已停止。
       换个安装目录即可自动换个服务名：sudo bash ${SELF} --dir /opt/别的名字"
}

assert_no_container_conflict() {
  [ "$DRY_RUN" = 1 ] && return 0
  command -v docker >/dev/null 2>&1 || return 0
  docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 || return 0

  # 认领判定（任一命中即认为是我们的，放行原地重建）：
  #   ① docker compose 打的工程标签（最可靠）
  #   ② 镜像 tag 是 <容器名> 开头（本脚本 build 时就这么打）
  #   ③ 挂载点指向我们的安装目录
  #   ④ 容器是我们 compose 文件里定义的服务名
  local proj img mounts
  proj="$(docker inspect "$CONTAINER_NAME" \
            --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)"
  [ -n "$proj" ] && [ "$proj" != "<no value>" ] && return 0

  img="$(docker inspect "$CONTAINER_NAME" --format '{{.Config.Image}}' 2>/dev/null)"
  case "$img" in "${CONTAINER_NAME}"|"${CONTAINER_NAME}:"*|*"/${CONTAINER_NAME}:"*) return 0 ;; esac

  mounts="$(docker inspect "$CONTAINER_NAME" \
              --format '{{range .Mounts}}{{.Source}} {{end}}' 2>/dev/null)"
  case " $mounts " in *" $INSTALL_DIR "*) return 0 ;; esac
  case "$mounts" in *"$INSTALL_DIR"*) return 0 ;; esac

  # 只有「容器在跑，且上面四个信号一个都对不上」才认为是别人的
  die "容器名冲突：已有一个叫 $CONTAINER_NAME 的容器，而且不是本脚本装的。
       为避免误删别人的容器，已停止。
       换个安装目录即可自动换个容器名：sudo bash ${SELF} --dir /opt/别的名字"
}

# ── 依赖自动安装 ──────────────────────────────────────────────────────
pkg_install() {
  local pkgs="$1"
  [ "$NO_DEPS" = 1 ] && return 1
  case "$PKG" in
    apt) run apt-get update -qq && run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $pkgs ;;
    dnf) run dnf install -y -q $pkgs ;;
    yum) run yum install -y -q $pkgs ;;
    apk) run apk add --no-cache $pkgs ;;
    *)   return 1 ;;
  esac
}

docker_ok() { command -v docker >/dev/null 2>&1; }

compose_ok() { docker compose version >/dev/null 2>&1; }

# 统一的 compose 调用入口。
#
# ⚠️ 为什么必须显式带 -p（项目名）：
#    docker compose 默认拿**目录名**当项目名。目录名如果全是中文（国内太常见了，
#    比如 /opt/视频工作台），推导出来的项目名字符全被过滤掉，只剩空串，
#    compose 直接报 `project name must not be empty`，安装当场失败 ——
#    而报错信息里完全看不出是"目录名是中文"引起的，小白绝对查不出来。
#    显式给一个 ASCII 的项目名（和容器名一致）就彻底绕开这个坑，
#    顺带保证「不同目录 → 不同项目」，多份安装互不干扰。
compose() {
  docker compose -p "$CONTAINER_NAME" "$@"
}

ensure_docker() {
  if docker_ok; then
    ok "docker 已就绪（$(docker --version 2>/dev/null | sed 's/Docker version //;s/,.*//')）"
  else
    if [ "$NO_DEPS" = 1 ]; then
      die "这台机器上还没装 docker，而你用了 --no-deps。
       想让它自动装就去掉 --no-deps，或自己执行：
       curl -fsSL https://get.docker.com | sh"
    fi
    step "这台机器上还没有 docker，正在自动安装"
    dim "  （会从官方源下载，网慢就久一点，别急）"
    local installed=0
    # 方式 1：官方一键脚本
    if [ "$DRY_RUN" = 1 ]; then
      runsh "curl -fsSL https://get.docker.com | sh"
      installed=1
    else
      local tmp; tmp="$(mktemp)"
      if curl -fsSL --max-time 120 https://get.docker.com -o "$tmp" 2>/dev/null; then
        sh "$tmp" >/tmp/muse-docker-install.log 2>&1 || true
        docker_ok && installed=1     # 关键：不信退出码，验命令真的可用
      fi
      rm -f "$tmp"
      # 方式 2：发行版仓库
      if [ "$installed" != 1 ]; then
        warn "官方脚本没能装上，改用系统自带仓库"
        pkg_install "docker.io" || true
        docker_ok && installed=1
      fi
      # 方式 3：重装（包在、二进制没了的情况）
      if [ "$installed" != 1 ] && [ "$PKG" = apt ]; then
        warn "再试一次：重新安装 docker 包"
        run env DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall docker.io || true
        docker_ok && installed=1
      fi
    fi
    [ "$installed" = 1 ] || die "docker 自动安装失败。
       可以看一眼日志：/tmp/muse-docker-install.log
       或手动装：curl -fsSL https://get.docker.com | sh"
    run systemctl enable --now docker 2>/dev/null || true
    ok "docker 装好了"
  fi

  # compose 插件
  if compose_ok; then
    ok "docker compose 可用"
    return 0
  fi
  step "缺 docker compose，正在自动安装"
  if [ "$DRY_RUN" = 1 ]; then
    runsh "install compose plugin"
    return 0
  fi
  pkg_install "docker-compose-plugin" || true
  if ! compose_ok; then
    # 兜底：直接下插件二进制
    local ver; ver="$(curl -fsSL --max-time 20 \
      https://api.github.com/repos/docker/compose/releases/latest 2>/dev/null \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
    [ -n "$ver" ] || ver="v2.29.7"
    run mkdir -p /usr/local/lib/docker/cli-plugins
    local url="https://github.com/docker/compose/releases/download/${ver}/docker-compose-linux-x86_64"
    run curl -fsSL --max-time 300 "$url" -o /usr/local/lib/docker/cli-plugins/docker-compose
    run chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  fi
  compose_ok && ok "docker compose 装好了" || warn "docker compose 仍不可用，后续可能出错"
}

ensure_git() {
  if command -v git >/dev/null 2>&1; then ok "git 已就绪"; return 0; fi
  step "缺 git，正在自动安装"
  pkg_install "git" || true
  command -v git >/dev/null 2>&1 && ok "git 装好了" || warn "git 装不上，将改用下载压缩包的方式"
}

# 公网 IP（云主机上 ip route 拿到的是内网 IP，必须走外部服务）
public_ip() {
  local ip=""
  for u in https://api.ipify.org https://ifconfig.me/ip https://ipv4.icanhazip.com; do
    ip="$(curl -s --max-time 8 "$u" 2>/dev/null | tr -d '[:space:]')"
    case "$ip" in *[!0-9.]*|"") ip="" ;; *) break ;; esac
  done
  printf '%s' "$ip"
}

# ── 状态文件（不 source！自己解析 + 键白名单） ────────────────────────
STATE_FILE=""
load_state() {
  STATE_FILE="$INSTALL_DIR/install.conf"
  [ -f "$STATE_FILE" ] || return 1
  local got=0
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    local k="${line%%=*}" v="${line#*=}"
    case "$k" in
      API_PORT) [ -z "$API_PORT" ] && API_PORT="$v"; got=1 ;;
      WEB_PORT) [ -z "$WEB_PORT" ] && WEB_PORT="$v"; got=1 ;;
      DOMAIN)   [ "$NO_DOMAIN" != 1 ] && [ -z "$DOMAIN" ] && DOMAIN="$v"; got=1 ;;
      # 把上次的 Key 读回来，重跑时复用它 —— 否则会生成新 Key，
      # 导致所有已配置的客户端和导号脚本全部失效（实测踩过）。
      API_KEY)  [ -z "$API_KEY" ] && API_KEY="$v"; got=1 ;;
      INSTALL_DIR) got=1 ;;
    esac
  done < "$STATE_FILE"
  [ "$got" = 1 ] && return 0
  # 有内容但一个可用键都没有 → 文件可能被改坏
  if [ -s "$STATE_FILE" ]; then
    warn "状态文件看起来被改坏了（$STATE_FILE），将使用默认值"
  fi
  return 1
}

save_state() {
  [ "$DRY_RUN" = 1 ] && return 0
  STATE_FILE="$INSTALL_DIR/install.conf"
  mkdir -p "$INSTALL_DIR"
  umask 077
  # ⚠️ API_KEY 一定要记进来：否则小白关掉安装窗口后就再也找不回 Key，
  #    而客户端接入、导号脚本都要用它。--status 也从这里读出来展示。
  cat > "$STATE_FILE" <<EOF
# $APP_LABEL 安装记录 —— 重跑脚本时会读这里的值
# 生成时间：$(date '+%Y-%m-%d %H:%M:%S')
INSTALL_DIR=$INSTALL_DIR
API_PORT=$API_PORT
WEB_PORT=$WEB_PORT
DOMAIN=$DOMAIN
API_KEY=$API_KEY
INSTALLED_AT=$(date +%s)
SCRIPT_VERSION=$SCRIPT_VERSION
EOF
  chmod 600 "$STATE_FILE" 2>/dev/null || true

  # 顺手把**脚本自己**存一份进安装目录（覆盖式，永远是最新版）。
  # 为什么必须做（实测）：小白多半是用「一条命令」装的（curl | bash），
  # 机器上根本没有脚本文件；装完想升级/卸载时他就懵了 —— 因为他手上
  # 既没有 install.sh，也不知道该从哪再弄一个。存一份在这儿之后，
  # 收尾提示就能给他一条**永远可用**的命令：
  #     sudo bash /opt/mvw/install.sh --status
  if [ -n "$SELF_PATH" ] && [ -f "$SELF_PATH" ]; then
    cp -f "$SELF_PATH" "$INSTALL_DIR/install.sh" 2>/dev/null && \
      chmod 755 "$INSTALL_DIR/install.sh" 2>/dev/null || true
  fi
}

# 生命周期命令（--status/--upgrade/--uninstall）该用哪条命令来提示？
# 优先用「安装目录里那份脚本副本」—— 哪怕用户当初是管道装的、或者把
# 下载的脚本删了，这条路也一定通。目录里还没有副本（首次安装中途）时，
# 退回脚本自身的名字。
self_hint() {
  if [ -n "${INSTALL_DIR:-}" ] && [ -f "$INSTALL_DIR/install.sh" ]; then
    printf 'bash %s/install.sh' "$INSTALL_DIR"
  else
    printf 'bash %s' "$SELF"
  fi
}

# 从已有安装里读出 API Key —— 优先状态文件，其次 compose 文件（兼容旧版本安装）。
#
# ⚠️ 两个坑（都实测踩过）：
#  1) 上游仓库自带 docker-compose.yml，里面有个**占位符** MUSE2API_KEY=m2a_change_me_to_your_secure_key。
#     首次安装时 fetch 完代码这个文件就存在了，若不加甄别就会把占位符当成"上次的 Key"。
#  2) 用 [A-Za-z0-9]* 匹配会在下划线处截断，把 m2a_change_me_to... 截成 m2a_change。
# 所以：正则要含下划线，且必须排除已知占位符。
read_existing_key() {
  local k="" raw=""
  if [ -f "$INSTALL_DIR/install.conf" ]; then
    k="$(sed -n 's/^API_KEY=//p' "$INSTALL_DIR/install.conf" 2>/dev/null | head -1)"
  fi
  if [ -z "$k" ] && [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
    raw="$(sed -n 's/.*MUSE2API_KEY=\(m2a_[A-Za-z0-9_]*\).*/\1/p' \
          "$INSTALL_DIR/docker-compose.yml" 2>/dev/null | head -1)"
    k="$raw"
  fi
  # 排除上游占位符 / 明显无效值
  case "$k" in
    ''|m2a_change|m2a_change_me_to_your_secure_key|m2a_your_secret_admin_key_here)
      k="" ;;
  esac
  # 太短的一定不是我们生成的真钥匙（我们生成的是 m2a_ + 32 位 hex）
  if [ -n "$k" ] && [ "${#k}" -lt 20 ]; then k=""; fi
  printf '%s' "$k"
}

need_state() {
  if [ ! -d "$INSTALL_DIR" ]; then
    die "这台机器上还没有装过 $APP_LABEL（找不到目录 $INSTALL_DIR）。
       想安装的话跑：sudo bash ${SELF}"
  fi
  if [ ! -f "$STATE_FILE" ]; then
    # ⚠️ 目录在、记录读不到，有两种可能，别混为一谈：
    #    a) 文件真的不存在（上次装到一半中断）→ 让他重跑安装
    #    b) 文件存在但**当前用户没权限读**（目录是 root 0700）→ 别叫他"重装"，
    #       而是要他用 sudo。实测：普通用户跑 --status 会走到这里，
    #       若只按 a 处理，会对着一个装好的服务说"还没装过"，纯耽误事。
    if [ ! -e "$STATE_FILE" ] && [ ! -r "$INSTALL_DIR" ]; then
      die "看不到安装记录（目录 $INSTALL_DIR 需要管理员权限才能读）。
       加上 sudo 再试：sudo bash ${SELF} --status"
    fi
    die "目录 $INSTALL_DIR 在，但没有安装记录 —— 多半是上次装到一半中断了。
       直接重跑一次安装就能接上：sudo bash ${SELF}"
  fi
}

# ── 资源体检：内存 / 磁盘 ─────────────────────────────────────────────
#
# 这套栈的容器里跑 Chromium（shm 2G），最现实的失败是 **OOM**：
# 1G 内存的小鸡在「点生成」那一刻容器被内核杀掉，docker logs 只有一句
# "Killed"，小白完全看不出原因，只会觉得「这软件坏了」。
# 所以装之前先量一下内存，太小就**明说**（给数字、给出路），
# 但不硬拦 —— 2G 也能跑，只是偶尔卡；决定权留给用户。
check_resources() {
  # 内存（kB）→ 换算 GB 时用整数近似，够用了
  local mem_kb=0 mem_gb=0
  if [ -r /proc/meminfo ]; then
    mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null)"
  fi
  case "$mem_kb" in ''|*[!0-9]*) mem_kb=0 ;; esac
  if [ "$mem_kb" -gt 0 ]; then
    mem_gb=$(( mem_kb / 1024 / 1024 ))
    if [ "$mem_gb" -ge 4 ]; then
      ok "内存：约 ${mem_gb} GB（够用）"
    elif [ "$mem_gb" -ge 2 ]; then
      ok "内存：约 ${mem_gb} GB（够用；同时跑别的服务时可能有点紧）"
    elif [ "$mem_gb" -ge 1 ]; then
      warn "内存只有约 ${mem_gb} GB —— 这套工具要跑无头浏览器，1G 容易在生成视频时被系统杀掉。"
      warn "   现象是「点了生成，然后任务莫名失败」，日志里只有 Killed。"
      warn "   建议升级到 2 核 4G，或先加一块 swap（临时顶一下）："
      warn "     sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile"
      warn "   继续装也可以，只是别指望它稳定出片。"
    else
      warn "读不到内存大小（/proc/meminfo 不可读），跳过内存检查。"
    fi
  fi

  # 磁盘：本体 + 浏览器镜像加起来大约 2GB，留 3GB 才舒服
  local avail_kb=0
  avail_kb="$(df -Pk "${INSTALL_DIR%/*}" 2>/dev/null | awk 'NR==2{print $4}')"
  case "$avail_kb" in ''|*[!0-9]*) avail_kb=0 ;; esac
  if [ "$avail_kb" -gt 0 ]; then
    local avail_gb=$(( avail_kb / 1024 / 1024 ))
    if [ "$avail_gb" -lt 3 ]; then
      warn "磁盘剩余约 ${avail_gb} GB —— 这套工具连镜像加数据要 2GB 出头，可能会装到一半写满。"
      warn "   清一清旧文件，或者换个盘：df -h"
    else
      ok "磁盘剩余：约 ${avail_gb} GB"
    fi
  fi
}

# ── 装机主流程 ────────────────────────────────────────────────────────
API_KEY=""

# 容器名与服务名从安装目录派生 —— 这样同一台机器可以并存多份，
# 分别放到不同目录（--dir）用不同端口，互不干扰。
derive_names() {
  local base
  base="$(basename "$INSTALL_DIR")"
  # 只保留字母数字和连字符，避免 docker 容器名非法
  base="$(printf '%s' "$base" | tr -c 'a-zA-Z0-9_.-' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//')"
  # ⚠️ 目录名如果全是中文/emoji（国内很常见，比如 /opt/视频工作台），
  #    上面那步会把整串都换成 '-'，清洗完**什么都不剩**。
  #    早期版本这时直接退回默认名 "$APP_NAME"，后果是：你在两个不同中文目录
  #    各装一份，两份的容器名/服务名**一模一样**，第二份会覆盖第一份 ——
  #    而且报错信息是别的（project name must not be empty），小白根本联想不到。
  #    这里改成：清不干净时挂一个目录路径的短哈希，保证「不同目录 → 不同名字」。
  if [ -z "$base" ]; then
    local h
    h="$(printf '%s' "$INSTALL_DIR" | cksum | awk '{print $1}')"
    base="${APP_NAME}-$(printf '%x' "$h" | cut -c1-6)"
  fi
  CONTAINER_NAME="$base"
  WEB_UNIT="${base}-web.service"
  CADDY_NAME="${base}-caddy"
}
CONTAINER_NAME="$APP_NAME"
WEB_UNIT="${APP_NAME}-web.service"
CADDY_NAME="${APP_NAME}-caddy"

gen_key() {
  local k=""
  if command -v openssl >/dev/null 2>&1; then
    k="$(openssl rand -hex 16 2>/dev/null)"
  fi
  if [ -z "$k" ]; then
    k="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  printf 'm2a_%s' "$k"
}

write_compose() {
  local dir="$1"
  local compose_file="$dir/docker-compose.yml"
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s 写入 %s（含真实密钥，权限 600）\n' "$C_CYN" "$C_OFF" "$compose_file"
    return 0
  fi
  umask 077
  cat > "$compose_file" <<EOF
services:
  muse2api:
    build: .
    image: ${CONTAINER_NAME}:latest
    container_name: ${CONTAINER_NAME}
    restart: always
    # ⚠️ 用默认 bridge 网络，**不要**让 compose 新建项目网络。
    #
    #    为什么：docker 默认的地址池只有 172.17~172.31 这一小段 /16。
    #    compose 默认会为**每个项目**新建一个网络，各占一个网段。
    #    一台机器上反复安装/卸载、或者本来就有不少容器时，池子很快被分光，
    #    之后所有新容器都起不来，报的还是天书：
    #        all predefined address pools have been fully subnetted
    #    实测就撞上了这个（28 个网络把池子耗干）。小白看到这行完全无法自救。
    #
    #    这个应用只有**一个**容器、只靠端口对外服务，根本不需要项目内网，
    #    共享 bridge 网络完全够用，而且再也不消耗地址池（多装几份也无所谓）。
    network_mode: bridge
    ports:
      - "${API_PORT}:${API_PORT}"
    # 上游 Dockerfile 把 --port 18610 写死了，这里必须显式覆盖，
    # 否则自定义端口会出现「宿主映射通了、应用还在听 18610」的半死状态。
    command: ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port ${API_PORT}"]
    environment:
      # 管理与 API 鉴权密钥（脚本自动生成）
      - MUSE2API_KEY=${API_KEY}
      - MUSE2API_HOST=0.0.0.0
      - MUSE2API_PORT=${API_PORT}
      - MUSE2API_PUBLIC_BASE=
      - MUSE2API_CHROMIUM=/usr/bin/chromium
      - MUSE2API_CDP_PORT=19210
      - MUSE2API_IMAGE_TIMEOUT=240
      - MUSE2API_VIDEO_TIMEOUT=600
      - MUSE2API_CHAT_TIMEOUT=300
    volumes:
      - ./data:/app/data
    shm_size: '2gb'
EOF
  chmod 600 "$compose_file"
}

fetch_muse2api() {
  local dir="$1"
  if [ -d "$dir/.git" ]; then
    ok "代码已存在，拉取最新版本"
    runsh "cd '$dir' && git pull --ff-only 2>&1 | tail -3" || warn "git pull 失败，沿用现有代码"
    return 0
  fi
  step "下载程序本体"
  if command -v git >/dev/null 2>&1; then
    # -b "$MUSE2API_REF" 不能省：不加时 git 只拉默认分支，若修复不在默认分支上就会
    # 静默装到没有修复的旧代码（曾经的线上事故根因）。
    if runsh "git clone --depth 1 -b '$MUSE2API_REF' https://github.com/${MUSE2API_REPO}.git '$dir' 2>&1 | tail -3"; then
      ok "下载完成"
      return 0
    fi
    warn "git 下载失败，改试压缩包"
  fi
  # 兜底：下载 zip
  local url="https://codeload.github.com/${MUSE2API_REPO}/zip/refs/heads/${MUSE2API_REF}"
  run mkdir -p "$dir"
  # ⚠️ zip 的临时文件不能写死 /tmp/muse2api.zip：
  #    两台安装同时跑、或上一次失败留了残file，都会互相踩（比如复用了别人下坏的半截包）。
  #    用 mktemp 生成唯一名，并在结束时一定清掉。
  local ztmp
  ztmp="$(mktemp /tmp/muse2api-XXXXXX.zip 2>/dev/null || echo "/tmp/muse2api-$$.zip")"
  if runsh "curl -fsSL --max-time 300 '$url' -o '$ztmp'"; then
    # 解包 → 把顶层目录里的内容（含隐藏文件）挪到 $dir，再删掉那个空壳目录。
    # 早期写法 `mv muse2api-main/* .` 有两个毛病：
    #   1) 漏掉隐藏文件（.env.example / .gitignore），装完缺文件；
    #   2) 不删 muse2api-main 空目录，$dir 里留个垃圾壳。
    # ⚠️ 顶层目录名不是写死的 "muse2api-main"：GitHub 打包规则是 <repo>-<ref>，
    #    ref 换成别的时候（如 master / v1.5.2）名字就变了，写死会 mv 不到而留下空目录。
    #    所以这里先探出真实目录名再挪 —— 兼容任意分支/标签。
    runsh "cd '$dir' && (command -v unzip >/dev/null 2>&1 && unzip -q -o '$ztmp' || python3 -c \"import zipfile;zipfile.ZipFile('$ztmp').extractall('.')\")"
    local top
    top="$(cd "$dir" && find . -maxdepth 1 -mindepth 1 -type d -name '*-*' | head -1 | sed 's|^\./||')"
    if [ -z "$top" ]; then
      rm -f "$ztmp"
      die "压缩包解出来找不到顶层目录，可能下载不完整。请重跑本脚本。"
    fi
    runsh "cd '$dir' && (shopt -s dotglob nullglob 2>/dev/null; mv '$top'/* . 2>/dev/null; rm -rf '$top') && rm -f '$ztmp'"
    ok "下载完成（压缩包方式）"
  else
    rm -f "$ztmp"
    die "下载程序本体失败。请检查这台机器的网络能否访问 github.com。
       国内机器可以先配好代理，或手动把代码放到 $dir 再重跑本脚本。"
  fi
}

write_webpage() {
  local dir="$1"
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s 写入 %s/index.html\n' "$C_CYN" "$C_OFF" "$dir"
    return 0
  fi
  umask 022
  cat > "$dir/index.html" <<'MUSEHTML'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Muse 视频工作台</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:#f7f7f5; --panel:#fff; --panel-2:#fafafa; --border:#e4e3de;
    --border-strong:#d0cfc9; --text:#23231f; --text-2:#6b6a64; --text-3:#9a9992;
    --accent:#c0392b; --accent-soft:#fdf2f0; --accent-hover:#a5301f;
    --ok:#1e8e5a; --warn:#b7791f; --err:#c0392b; --radius:10px; --radius-lg:14px;
  }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text); font-size:14px; line-height:1.6; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:1180px; margin:0 auto; padding:28px 24px 60px; }
  header { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:24px; }
  .brand { display:flex; align-items:center; gap:12px; }
  .logo { width:38px; height:38px; border-radius:9px; background:var(--accent); color:#fff;
    display:flex; align-items:center; justify-content:center; font-size:17px; font-weight:500; flex-shrink:0; }
  .brand h1 { font-size:17px; font-weight:500; letter-spacing:-0.01em; }
  .brand p { font-size:12px; color:var(--text-3); }
  .status { display:flex; align-items:center; gap:7px; font-size:12px; color:var(--text-2);
    background:var(--panel); border:1px solid var(--border); padding:6px 12px; border-radius:999px; }
  .dot { width:7px; height:7px; border-radius:50%; background:var(--text-3); flex-shrink:0; }
  .dot.on { background:var(--ok); } .dot.off { background:var(--err); }
  .grid { display:grid; grid-template-columns:400px 1fr; gap:20px; align-items:start; }
  @media (max-width:900px) { .grid { grid-template-columns:1fr; } }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:var(--radius-lg); padding:20px; }
  .card + .card { margin-top:16px; }
  .card-title { font-size:13px; font-weight:500; display:flex; align-items:center;
    justify-content:space-between; margin-bottom:16px; }
  .card-title .hint { font-size:11px; color:var(--text-3); font-weight:400; }
  label.field { display:block; margin-bottom:16px; }
  label.field:last-of-type { margin-bottom:0; }
  .lbl { display:flex; align-items:center; justify-content:space-between; font-size:12px;
    color:var(--text-2); margin-bottom:7px; }
  .lbl .count { font-size:11px; color:var(--text-3); font-variant-numeric:tabular-nums; }
  textarea,input[type=text],input[type=password],select { width:100%; font-family:inherit; font-size:13px;
    color:var(--text); background:var(--panel-2); border:1px solid var(--border); border-radius:var(--radius);
    padding:10px 12px; outline:none; transition:border-color .15s, background .15s; }
  textarea { resize:vertical; min-height:108px; line-height:1.7; }
  textarea:focus,input:focus,select:focus { border-color:var(--accent); background:#fff; }
  textarea::placeholder,input::placeholder { color:var(--text-3); }
  select { cursor:pointer; appearance:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%236b6a64' stroke-width='1.4' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat:no-repeat; background-position:right 12px center; padding-right:32px; }
  .sizes { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; }
  .size-opt { border:1px solid var(--border); background:var(--panel-2); border-radius:var(--radius);
    padding:10px 8px; cursor:pointer; display:flex; flex-direction:column; align-items:center;
    gap:5px; transition:all .15s; text-align:center; }
  .size-opt:hover { border-color:var(--border-strong); }
  .size-opt.sel { border-color:var(--accent); background:var(--accent-soft); }
  .size-opt .box { border:1.5px solid var(--text-3); border-radius:3px; transition:border-color .15s; }
  .size-opt.sel .box { border-color:var(--accent); }
  .size-opt .name { font-size:11px; color:var(--text-2); }
  .size-opt.sel .name { color:var(--accent); font-weight:500; }
  .box-16-9 { width:36px; height:20px; } .box-9-16 { width:20px; height:36px; }
  .box-1-1 { width:28px; height:28px; } .box-4-3 { width:32px; height:24px; }
  .drop { border:1.5px dashed var(--border-strong); border-radius:var(--radius); padding:18px;
    text-align:center; cursor:pointer; transition:all .15s; background:var(--panel-2); position:relative; }
  .drop:hover,.drop.over { border-color:var(--accent); background:var(--accent-soft); }
  .drop .ico { font-size:20px; color:var(--text-3); margin-bottom:5px; }
  .drop .t1 { font-size:12px; color:var(--text-2); }
  .drop .t2 { font-size:11px; color:var(--text-3); margin-top:2px; }
  .drop img { max-width:100%; max-height:130px; border-radius:7px; display:block; margin:0 auto; }
  .drop.has-img { padding:8px; border-style:solid; border-color:var(--border); }
  .clear-img { position:absolute; top:6px; right:6px; width:22px; height:22px; border-radius:50%;
    border:none; background:rgba(35,35,31,.72); color:#fff; font-size:13px; cursor:pointer;
    line-height:1; display:flex; align-items:center; justify-content:center; }
  .clear-img:hover { background:rgba(35,35,31,.9); }
  .btn { width:100%; border:none; border-radius:var(--radius); font-family:inherit; font-size:14px;
    font-weight:500; padding:12px; cursor:pointer; transition:all .15s;
    display:flex; align-items:center; justify-content:center; gap:8px; }
  .btn-primary { background:var(--accent); color:#fff; margin-top:20px; }
  .btn-primary:hover:not(:disabled) { background:var(--accent-hover); }
  .btn-primary:disabled { background:var(--border-strong); color:#fff; cursor:not-allowed; }
  .btn-ghost { background:var(--panel-2); color:var(--text-2); border:1px solid var(--border);
    font-size:12px; padding:8px 12px; width:auto; }
  .btn-ghost:hover { border-color:var(--border-strong); color:var(--text); }
  .spinner { width:14px; height:14px; border-radius:50%; border:2px solid rgba(255,255,255,.35);
    border-top-color:#fff; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .task-empty { text-align:center; padding:56px 20px; color:var(--text-3); }
  .task-empty .ico { font-size:30px; margin-bottom:10px; opacity:.5; }
  .task-empty .t { font-size:13px; }
  .prog-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; }
  .prog-status { font-size:13px; font-weight:500; }
  .prog-pct { font-size:13px; color:var(--text-2); font-variant-numeric:tabular-nums; }
  .bar { height:5px; background:var(--border); border-radius:999px; overflow:hidden; }
  .bar > i { display:block; height:100%; background:var(--accent); border-radius:999px; transition:width .5s ease; }
  .prog-note { font-size:12px; color:var(--text-3); margin-top:10px; }
  .prog-note.err { color:var(--err); }
  video { width:100%; border-radius:var(--radius); background:#000; display:block; }
  .result-meta { display:flex; align-items:center; justify-content:space-between; gap:12px;
    flex-wrap:wrap; margin-top:14px; }
  .meta-txt { font-size:12px; color:var(--text-3); }
  .result-actions { display:flex; gap:8px; }
  .result-actions a { text-decoration:none; }
  .result-actions .btn-ghost { display:inline-flex; }
  .hist-list { display:flex; flex-direction:column; gap:8px; max-height:340px; overflow-y:auto; }
  .hist-item { display:flex; gap:11px; align-items:center; padding:9px; border-radius:var(--radius);
    cursor:pointer; border:1px solid transparent; transition:all .15s; }
  .hist-item:hover { background:var(--panel-2); border-color:var(--border); }
  .hist-thumb { width:56px; height:34px; border-radius:6px; flex-shrink:0; background:var(--panel-2);
    border:1px solid var(--border); display:flex; align-items:center; justify-content:center;
    font-size:14px; color:var(--text-3); overflow:hidden; }
  .hist-body { min-width:0; flex:1; }
  .hist-prompt { font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .hist-sub { font-size:11px; color:var(--text-3); margin-top:2px; }
  .hist-badge { font-size:10px; padding:2px 7px; border-radius:999px; background:var(--panel-2);
    border:1px solid var(--border); color:var(--text-2); flex-shrink:0; }
  .hist-badge.ok { color:var(--ok); border-color:#b8e0c8; background:#f0f9f4; }
  .hist-badge.fail { color:var(--err); border-color:#f0c8c4; background:var(--accent-soft); }
  .hist-badge.run { color:var(--warn); border-color:#eed9ac; background:#fdf8ec; }
  .toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%) translateY(80px);
    background:var(--text); color:#fff; font-size:13px; padding:11px 20px; border-radius:var(--radius);
    opacity:0; transition:all .25s; pointer-events:none; z-index:99; max-width:90vw; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo">M</div>
      <div><h1>Muse 视频工作台</h1><p>文生视频 · 首帧图生视频</p></div>
    </div>
    <div class="status"><span class="dot" id="statusDot"></span><span id="statusText">未连接</span></div>
  </header>

  <div class="grid">
    <div>
      <div class="card">
        <div class="card-title">创作</div>
        <label class="field">
          <div class="lbl"><span>视频描述</span><span class="count" id="promptCount">0 / 20000</span></div>
          <textarea id="prompt" maxlength="20000" placeholder="描述你想要的画面，越具体越好。例如：&#10;金色的枫叶在微风中缓缓飘落，阳光穿过树梢，镜头缓慢推进，电影级景深"></textarea>
        </label>
        <label class="field">
          <div class="lbl"><span>时长</span></div>
          <select id="duration">
            <option value="5" selected>5 秒</option>
            <option value="6">6 秒</option>
            <option value="10">10 秒</option>
            <option value="30">30 秒</option>
            <option value="60">60 秒</option>
            <option value="120">120 秒</option>
            <option value="240">240 秒</option>
            <option value="480">480 秒</option>
          </select>
        </label>
        <label class="field">
          <div class="lbl"><span>画幅</span></div>
          <div class="sizes" id="sizes">
            <div class="size-opt sel" data-size="16:9"><div class="box box-16-9"></div><div class="name">16:9 横屏</div></div>
            <div class="size-opt" data-size="9:16"><div class="box box-9-16"></div><div class="name">9:16 竖屏</div></div>
            <div class="size-opt" data-size="1:1"><div class="box box-1-1"></div><div class="name">1:1 方形</div></div>
            <div class="size-opt" data-size="4:3"><div class="box box-4-3"></div><div class="name">4:3</div></div>
          </div>
        </label>
        <label class="field">
          <div class="lbl"><span>首帧图（可选）</span><span class="count">不填＝文生视频</span></div>
          <div class="drop" id="drop">
            <input type="file" id="file" accept="image/*" hidden>
            <div id="dropInner">
              <div class="ico">＋</div>
              <div class="t1">点击或拖拽图片到这里</div>
              <div class="t2">作为视频首帧 · 支持 JPG / PNG / WebP</div>
            </div>
          </div>
        </label>
        <button class="btn btn-primary" id="genBtn"><span id="genBtnText">生成视频</span></button>
      </div>

      <div class="card">
        <div class="card-title">连接设置</div>
        <label class="field">
          <div class="lbl"><span>接口地址</span></div>
          <input type="text" id="baseUrl" placeholder="http://127.0.0.1:18610">
        </label>
        <label class="field">
          <div class="lbl"><span>API Key</span></div>
          <input type="password" id="apiKey" placeholder="m2a_...">
        </label>
        <button class="btn btn-ghost" id="saveCfg">保存并测试连接</button>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-title"><span>当前任务</span><span class="hint" id="taskHint"></span></div>
        <div id="taskArea">
          <div class="task-empty"><div class="ico">▷</div>
            <div class="t">在左侧填写描述，点击「生成视频」开始创作</div></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title"><span>历史记录</span>
          <button class="btn btn-ghost" id="clearHist">清空</button></div>
        <div id="histArea">
          <div class="task-empty" style="padding:28px 10px"><div class="t">暂无记录</div></div>
        </div>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
(function () {
  'use strict';
  var LS_CFG = 'muse_video_cfg', LS_HIST = 'muse_video_hist', POLL_MS = 4000;
  var $ = function (id) { return document.getElementById(id); };
  var state = { base:'', key:'', size:'16:9', firstFrame:null, busy:false,
                timer:null, taskId:null, tasks:[] };
  var curDur = 5;

  function toast(m){ var t=$('toast'); t.textContent=m; t.classList.add('show');
    clearTimeout(t._h); t._h=setTimeout(function(){t.classList.remove('show');},2600); }
  function normBase(u){ u=(u||'').trim().replace(/\/+$/,''); if(!u) return '';
    if(!/^https?:\/\//i.test(u)) u='http://'+u;
    if(/\/v1$/i.test(u)) u=u.slice(0,-3); return u; }
  function api(p){ return state.base+p; }
  function headers(){ return {'Authorization':'Bearer '+state.key,'Content-Type':'application/json'}; }
  function fmtBytes(n){ if(!n) return ''; return n<1048576?(n/1024).toFixed(0)+' KB':(n/1048576).toFixed(1)+' MB'; }
  function fmtTime(ts){ if(!ts) return ''; var d=new Date(ts*1000);
    return (d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0'); }
  function setStatus(c,t){ $('statusDot').className='dot '+c; $('statusText').textContent=t; }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

  function loadCfg(){
    var c={}; try{ c=JSON.parse(localStorage.getItem(LS_CFG)||'{}'); }catch(e){}
    state.base=c.base||location.origin.replace(/:\d+$/, ':18610');
    state.key=c.key||'';
    $('baseUrl').value=state.base; $('apiKey').value=state.key;
    if(state.key) testConn(true);
  }
  function saveCfg(){
    state.base=normBase($('baseUrl').value); state.key=$('apiKey').value.trim();
    $('baseUrl').value=state.base;
    localStorage.setItem(LS_CFG, JSON.stringify({base:state.base,key:state.key}));
  }
  function testConn(silent){
    if(!state.base||!state.key){ setStatus('off','未配置'); return; }
    fetch(api('/v1/models'),{headers:headers()}).then(function(r){
      if(r.ok){ setStatus('on','已连接'); if(!silent) toast('连接成功'); }
      else if(r.status===401){ setStatus('off','Key 无效'); if(!silent) toast('API Key 无效（401）'); }
      else { setStatus('off','HTTP '+r.status); if(!silent) toast('连接失败：HTTP '+r.status); }
    }).catch(function(){ setStatus('off','连接失败');
      if(!silent) toast('连不上 '+state.base+'，请检查地址与网络'); });
  }

  function loadHist(){ try{ state.tasks=JSON.parse(localStorage.getItem(LS_HIST)||'[]'); }catch(e){ state.tasks=[]; } renderHist(); }
  function saveHist(){
    var slim=state.tasks.slice(0,30).map(function(t){ return {id:t.id,prompt:t.prompt,status:t.status,
      size:t.size,duration:t.duration,created:t.created,url:t.url||'',bytes:t.bytes||0,err:t.err||''}; });
    try{ localStorage.setItem(LS_HIST,JSON.stringify(slim)); }catch(e){}
  }
  function addTask(t){ state.tasks.unshift(t); if(state.tasks.length>30) state.tasks.length=30; renderHist(); saveHist(); }
  function updTask(id,patch){ for(var i=0;i<state.tasks.length;i++){ if(state.tasks[i].id===id){
    for(var k in patch) state.tasks[i][k]=patch[k]; break; } } renderHist(); saveHist(); }

  function renderHist(){
    var a=$('histArea');
    if(!state.tasks.length){ a.innerHTML='<div class="task-empty" style="padding:28px 10px"><div class="t">暂无记录</div></div>'; return; }
    var h='<div class="hist-list">';
    state.tasks.forEach(function(t,i){
      var b,c;
      if(t.status==='completed'){ b='已完成'; c='ok'; }
      else if(t.status==='failed'){ b='失败'; c='fail'; }
      else { b='生成中'; c='run'; }
      h+='<div class="hist-item" data-i="'+i+'"><div class="hist-thumb">'+(t.url?'▷':'…')+'</div>'+
        '<div class="hist-body"><div class="hist-prompt">'+esc(t.prompt||'(无描述)')+'</div>'+
        '<div class="hist-sub">'+esc(t.size||'')+' · '+(t.duration||5)+'s · '+fmtTime(t.created)+
        (t.bytes?' · '+fmtBytes(t.bytes):'')+'</div></div>'+
        '<span class="hist-badge '+c+'">'+b+'</span></div>';
    });
    h+='</div>'; a.innerHTML=h;
    a.querySelectorAll('.hist-item').forEach(function(el){
      el.onclick=function(){ showTask(state.tasks[+el.dataset.i]); };
    });
  }

  function showTask(t){
    var area=$('taskArea'); $('taskHint').textContent=t.id||'';
    if(t.status==='completed'&&t.url){
      var abs=/^https?:\/\//i.test(t.url)?t.url:state.base+t.url;
      area.innerHTML='<video src="'+abs+'" controls playsinline preload="metadata"></video>'+
        '<div class="result-meta"><div class="meta-txt">'+esc(t.size||'')+' · '+(t.duration||5)+'s'+
        (t.bytes?' · '+fmtBytes(t.bytes):'')+'</div><div class="result-actions">'+
        '<a href="'+abs+'" download target="_blank" rel="noopener"><button class="btn btn-ghost">下载视频</button></a>'+
        '<a href="'+abs+'" target="_blank" rel="noopener"><button class="btn btn-ghost">新窗口打开</button></a>'+
        '</div></div><div class="prog-note" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">'+
        esc(t.prompt||'')+'</div>';
      return;
    }
    if(t.status==='failed'){
      area.innerHTML='<div class="prog-head"><span class="prog-status" style="color:var(--err)">生成失败</span></div>'+
        '<div class="prog-note err">'+esc(t.err||'未知错误')+'</div>'+
        '<div class="prog-note" style="margin-top:12px">'+esc(t.prompt||'')+'</div>';
      return;
    }
    var pct=t.progress||0;
    area.innerHTML='<div class="prog-head"><span class="prog-status">'+(pct>=100?'正在保存':'生成中')+
      '</span><span class="prog-pct">'+pct+'%</span></div>'+
      '<div class="bar"><i style="width:'+Math.max(pct,4)+'%"></i></div>'+
      '<div class="prog-note">视频生成通常需要 1～2 分钟，请保持页面打开</div>'+
      '<div class="prog-note" style="margin-top:12px">'+esc(t.prompt||'')+'</div>';
  }

  function stopPolling(){ if(state.timer){ clearInterval(state.timer); state.timer=null; } }
  function startPolling(id){ stopPolling(); state.timer=setInterval(function(){ poll(id); }, POLL_MS); }

  function currentPrompt(id){ for(var i=0;i<state.tasks.length;i++){ if(state.tasks[i].id===id) return state.tasks[i].prompt; } return ''; }

  function poll(id){
    fetch(api('/v1/videos/'+id),{headers:headers()}).then(function(r){ return r.json(); }).then(function(j){
      var status=j.status, pct=(typeof j.progress==='number')?j.progress:0;
      var patch={status:status,progress:pct};
      if(status==='completed'||status==='succeeded'){
        var res=j.result||{};
        patch.status='completed'; patch.url=res.url||''; patch.bytes=res.bytes||0;
        stopPolling(); setBusy(false); updTask(id,patch);
        showTask(Object.assign({id:id},patch,{prompt:currentPrompt(id),size:state.size,duration:curDur}));
        toast('视频生成完成'); return;
      }
      if(status==='failed'||status==='error'){
        patch.status='failed'; patch.err=j.error||'生成失败';
        stopPolling(); setBusy(false); updTask(id,patch);
        showTask(Object.assign({id:id},patch,{prompt:currentPrompt(id),size:state.size,duration:curDur}));
        return;
      }
      updTask(id,patch);
      showTask(Object.assign({id:id,progress:pct,status:status},{prompt:currentPrompt(id),size:state.size,duration:curDur}));
    }).catch(function(){});
  }

  function setBusy(b){
    state.busy=b; var btn=$('genBtn'); btn.disabled=b;
    $('genBtnText').textContent=b?'生成中…':'生成视频';
    if(b&&!btn.querySelector('.spinner')){
      var sp=document.createElement('span'); sp.className='spinner'; btn.insertBefore(sp,$('genBtnText'));
    } else if(!b){ var ex=btn.querySelector('.spinner'); if(ex) ex.remove(); }
  }

  function generate(){
    if(state.busy) return;
    if(!state.base||!state.key){ toast('请先在下方填写接口地址与 API Key'); return; }
    var prompt=$('prompt').value.trim();
    if(!prompt){ toast('请填写视频描述'); return; }
    var payload={prompt:prompt,duration:parseInt($('duration').value,10)||5,size:state.size};
    if(state.firstFrame) payload.image=state.firstFrame;
    curDur=payload.duration; setBusy(true);
    fetch(api('/v1/videos'),{method:'POST',headers:headers(),body:JSON.stringify(payload)})
      .then(function(r){ return r.json().then(function(j){ return {ok:r.ok,status:r.status,body:j}; }); })
      .then(function(res){
        if(!res.ok){
          var msg=(res.body&&(res.body.error&&(res.body.error.message||res.body.error)||res.body.detail))||('HTTP '+res.status);
          throw new Error(typeof msg==='string'?msg:JSON.stringify(msg));
        }
        var id=res.body.id||res.body.task_id;
        if(!id) throw new Error('服务端未返回任务 ID');
        state.taskId=id;
        addTask({id:id,prompt:prompt,status:'queued',progress:10,size:state.size,
          duration:payload.duration,created:Math.floor(Date.now()/1000),url:'',bytes:0,err:''});
        showTask({id:id,prompt:prompt,status:'queued',progress:10,size:state.size,duration:payload.duration});
        toast('任务已提交，正在生成…'); startPolling(id);
      })
      .catch(function(e){ setBusy(false); toast('提交失败：'+e.message); });
  }

  function bindDrop(){
    var drop=$('drop'), file=$('file');
    drop.onclick=function(e){ if(e.target.classList.contains('clear-img')) return; file.click(); };
    file.onchange=function(){ if(file.files[0]) readFile(file.files[0]); };
    ['dragenter','dragover'].forEach(function(ev){ drop.addEventListener(ev,function(e){ e.preventDefault(); drop.classList.add('over'); }); });
    ['dragleave','drop'].forEach(function(ev){ drop.addEventListener(ev,function(e){ e.preventDefault(); drop.classList.remove('over'); }); });
    drop.addEventListener('drop',function(e){
      var f=e.dataTransfer.files[0];
      if(f&&/^image\//.test(f.type)) readFile(f); else if(f) toast('请拖入图片文件');
    });
    function readFile(f){
      if(f.size>8*1024*1024){ toast('图片不要超过 8MB'); return; }
      var fr=new FileReader();
      fr.onload=function(){
        state.firstFrame=fr.result; drop.classList.add('has-img');
        $('dropInner').innerHTML='<img src="'+fr.result+'"><button class="clear-img" type="button">×</button>';
        drop.querySelector('.clear-img').onclick=function(e){
          e.stopPropagation(); state.firstFrame=null; drop.classList.remove('has-img');
          $('dropInner').innerHTML='<div class="ico">＋</div><div class="t1">点击或拖拽图片到这里</div>'+
            '<div class="t2">作为视频首帧 · 支持 JPG / PNG / WebP</div>';
          file.value='';
        };
      };
      fr.readAsDataURL(f);
    }
  }

  function bind(){
    $('prompt').oninput=function(){ $('promptCount').textContent=$('prompt').value.length+' / 20000'; };
    $('sizes').onclick=function(e){
      var el=e.target.closest?e.target.closest('.size-opt'):null; if(!el) return;
      this.querySelectorAll('.size-opt').forEach(function(o){ o.classList.remove('sel'); });
      el.classList.add('sel'); state.size=el.dataset.size;
    };
    $('genBtn').onclick=generate;
    $('saveCfg').onclick=function(){ saveCfg(); testConn(false); };
    $('clearHist').onclick=function(){ if(!state.tasks.length) return; state.tasks=[]; renderHist(); saveHist(); toast('历史已清空'); };
    $('prompt').addEventListener('keydown',function(e){
      if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){ e.preventDefault(); generate(); }
    });
  }

  bind(); bindDrop(); loadCfg(); loadHist();
})();
</script>
</body>
</html>
MUSEHTML
}

write_service() {
  local f="/etc/systemd/system/${WEB_UNIT}"
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s 写入并启用 %s\n' "$C_CYN" "$C_OFF" "$f"
    return 0
  fi
  cat > "$f" <<EOF
[Unit]
Description=$APP_LABEL (static site)
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$(command -v python3 || echo /usr/bin/python3) -m http.server ${WEB_PORT} --bind 0.0.0.0 --directory $INSTALL_DIR
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF
  run systemctl daemon-reload
  run systemctl enable --now "${WEB_UNIT}"
}

# 等容器内部端口真的活起来（docker-proxy 会让宿主端口「假通」）
# 判据：能拿到任意 HTTP 状态码即算活 —— 401 恰恰说明服务在跑（缺 Key 而已）。
# 注意不能用 curl -f：它遇 4xx 直接返回错误码，会把正常的 401 误判成「服务没起来」。
api_alive() {
  local code
  code="$(docker exec "$CONTAINER_NAME" curl -s -o /dev/null -w '%{http_code}' \
    --max-time 5 "http://127.0.0.1:${API_PORT}/v1/models" 2>/dev/null)"
  case "$code" in ''|000) return 1 ;; *) return 0 ;; esac
}

# 解析「真正在跑的那个容器名」。
# 为什么不能直接用 $CONTAINER_NAME：容器名是**安装时**写进 compose 的，
# 而手工改过 compose、或用更早的脚本装过、或有人 docker rename 过，都会让它对不上。
# 这时如果死认 $CONTAINER_NAME，--status 会报「× 没找到容器」——明明服务好好地跑着，
# 用户看到只会恐慌（曾经我自己就被这个误导过：容器叫 muse2api，脚本找 mvw）。
# 策略：先用 $CONTAINER_NAME；找不到就退而按「镜像名 / 服务标签」反查一个 muse 容器。
resolve_container_name() {
  if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    printf '%s' "$CONTAINER_NAME"; return 0
  fi
  # 反查：优先本项目的 compose 标签，其次镜像名以 muse2api/mvw 开头
  local c
  c="$(docker ps -a \
        --filter "label=com.docker.compose.service=muse2api" \
        --format '{{.Names}}' 2>/dev/null | head -1)"
  if [ -z "$c" ]; then
    c="$(docker ps -a --format '{{.Names}}\t{{.Image}}' 2>/dev/null \
          | awk -F'\t' '$2 ~ /^(muse2api|mvw|muse-video)/ {print $1; exit}')"
  fi
  printf '%s' "$c"
}

# 装完自检代码级修复是否真的在。
# ⚠️ 为什么需要这个：脚本装的是「仓库的某个 ref」，但 ref 里到底有没有修复，
#    光看「容器起来了 / 接口有响应」是看不出来的 —— 旧版代码同样能起来、同样有响应。
#    曾经的事故就是：默认分支还是旧版，脚本报「安装成功」，用户用了几轮才发现
#    并发一上来就卡死（旧版用裸锁，第二个请求会永久自锁）。
#    所以这里做三项硬校验，任一不过就**明确报警**（而不是假装成功）：
#      ① scheduler.py 存在       → FIFO 队列调度在
#      ② app.py 有鉴权守卫函数    → 405 绕过修复在
#      ③ app.py 有时长校验函数    → 参数校验修复在
#    在容器里查（而不是宿主目录），保证查的就是真正跑起来的那份代码。
verify_installed_fixes() {
  local c
  c="$(resolve_container_name)"
  if [ -z "$c" ]; then
    err "自检跳过：找不到对应的容器（服务没起来？用 docker ps -a 看一眼）"
    return 1
  fi

  local missing=""

  if ! docker exec "$c" test -f /app/scheduler.py 2>/dev/null; then
    missing="${missing} scheduler.py"
  fi
  if ! docker exec "$c" sh -c \
      'grep -q "_guard_method_not_allowed" /app/app.py' 2>/dev/null; then
    missing="${missing} 鉴权守卫"
  fi
  if ! docker exec "$c" sh -c \
      'grep -q "validate_video_duration" /app/app.py' 2>/dev/null; then
    missing="${missing} 时长校验"
  fi

  if [ -n "$missing" ]; then
    err "自检没过：装到的代码缺少关键修复 ——${missing}"
    say "     这说明下载到的版本不对（多半是分支选错了或仓库被改动）。"
    say "     请删除 ${INSTALL_DIR} 后重跑本脚本；若仍失败，把它反馈给维护者。"
    return 1
  fi
  ok "代码自检：关键修复都在（队列调度 / 鉴权守卫 / 时长校验）"
  return 0
}

wait_api_ready() {
  local tries=0
  while [ "$tries" -lt 40 ]; do
    api_alive && return 0
    tries=$((tries + 1)); sleep 3
  done
  return 1
}

print_next_steps() {
  local IP="$1"
  printf '\n%s════════════════════════════════════════════════════════%s\n' "$C_GRN" "$C_OFF"
  printf '%s  装好了！下一步只要做一件事：导入你的 muse.ai 账号%s\n' "$C_BLD" "$C_OFF"
  printf '%s════════════════════════════════════════════════════════%s\n\n' "$C_GRN" "$C_OFF"
  say "  ${C_BLD}① 先打开网页看看${C_OFF}"
  if [ -n "$DOMAIN" ]; then
    say "     https://${DOMAIN}/"
  else
    say "     http://${IP}:${WEB_PORT}/"
  fi
  say ""
  say "  ${C_BLD}② 导入 muse.ai 账号（必须先做这步，否则生成不了）${C_OFF}"
  say "     视频工作台靠 muse.ai 的账号来出片，所以得先给它一个账号。"
  say "     这台服务器上没有浏览器，所以要在${C_BLD}你自己的电脑${C_OFF}上做。"
  say ""
  say "     ${C_BLD}第 1 步${C_OFF} 下载这个小工具（普通网页文件，双击不会执行，安全）："
  say "        https://raw.githubusercontent.com/${SELF_REPO}/main/tools/get_muse_cookie.py"
  say "        （存到桌面就行，名字保持 get_muse_cookie.py）"
  say ""
  say "     ${C_BLD}第 2 步${C_OFF} 在你的电脑上打开命令行，敲这一条（会自动弹出浏览器）："
  say "        python get_muse_cookie.py"
  say ""
  say "        它只问你两件事，照着填就行："
  say "          服务器地址（形如 1.2.3.4:18610）：${IP}:${API_PORT}"
  say "          API Key：${API_KEY}"
  say ""
  say "        ${C_DIM}（上面两条可以直接从这里复制过去；它也记住了，下次直接回车）${C_OFF}"
  say ""
  say "     ${C_BLD}第 3 步${C_OFF} 在弹出来的窗口里登录 muse.ai，看到「导入成功」就好了。"
  say "        想导入第二个账号，它会问你要不要继续，按 y 即可。"
  say ""
  say "     ${C_DIM}没装 Python？搜「python 官网下载」，装的时候勾上 Add to PATH 就行。${C_OFF}"
  say "     ${C_DIM}没装 Chrome？装 Edge（Windows 自带）也可以用。${C_OFF}"
  say ""
  say "  ${C_BLD}③ 回到网页，输入一句话测试${C_OFF}"
  say "     接口地址和 Key 已经自动填好了，直接写描述、点生成就行。"
  say ""
  printf '%s  ────────── 以下是详细信息，以后需要再查 ──────────%s\n\n' "$C_DIM" "$C_OFF"
  say "  网页地址：      http://${IP}:${WEB_PORT}/"
  say "  接口地址：      http://${IP}:${API_PORT}/v1"
  say "  API Key：       ${API_KEY}"
  say "  安装目录：      ${INSTALL_DIR}"
  say "  账号池面板：    http://${IP}:${API_PORT}/admin?key=${API_KEY}"
  # ⚠️ 用户要了域名但这次没配上（DNS 没生效），必须在这里再明确说一次：
  #    否则上面那些 "✓ 服务已启动" 会让他以为域名能用了，打开却打不开。
  if [ -n "${DOMAIN_SKIPPED:-}" ]; then
    say ""
    warn "你给的域名 ${DOMAIN_SKIPPED} 这次没生效 —— 现在请先用上面的 IP 地址访问。"
    warn "等 DNS 解析到这台机器后，重跑一遍安装即可自动配上 HTTPS："
    warn "    sudo bash ${SELF} --domain ${DOMAIN_SKIPPED}"
  fi
  say ""
  say "  常用命令："
  # 用 self_hint：脚本自己已经存了一份到安装目录，这里给出**一定可用**的命令。
  # （管道安装时用户手上没有脚本文件，写 ${SELF} 他会找不到。）
  say "    sudo $(self_hint) --status      看运行状态（也能把上面的地址和 Key 再打印一遍）"
  say "    sudo $(self_hint) --upgrade     升级到最新版"
  say "    sudo $(self_hint) --uninstall   卸载"
  if [ -n "$SELF_PATH" ] && [ "$SELF_PATH" != "$INSTALL_DIR/install.sh" ]; then
    say "    ${C_DIM}（脚本已另存一份到 $INSTALL_DIR/install.sh，你原来的那份可以删）${C_OFF}"
  fi
  say ""
  say "  ${C_DIM}记不住 API Key？随时跑 --status 就能看回来。${C_OFF}"
  say ""
  warn "如果网页打不开，多半是云服务商的安全组没放行 ${WEB_PORT} 和 ${API_PORT} 端口，去控制台加一下。"
  say ""
  dim "  验收清单："
  dim "    □ 网页 http://${IP}:${WEB_PORT}/ 能打开（左侧能看到「生成视频」按钮）"
  dim "    □ 右上角状态灯是绿的（说明 Key 对、接口通）"
  dim "    □ 账号池里有 1 个账号（http://${IP}:${API_PORT}/admin?key=${API_KEY}）"
  dim "      ↑ 现在还是 0 个，做完上面第 ② 步（导号）才会变成 1"
  dim "    □ 填一句描述点生成，1-2 分钟内出片"
  say ""
}

do_install() {
  say ""
  printf '%s╭──────────────────────────────────────────────────────╮%s\n' "$C_BLD" "$C_OFF"
  printf '%s│  %s 一键安装%s  %-34s│\n' "$C_BLD" "$APP_LABEL" "$C_OFF" ""
  printf '%s╰──────────────────────────────────────────────────────╯%s\n\n' "$C_BLD" "$C_OFF"
  say "  我在帮你装一个「输入文字就能生成视频」的网页工具。"
  say "  装好之后你可以："
  say "    · 用浏览器打开一个网址，写一句话就出视频"
  say "    · 让别的软件连上它来调用接口"
  say ""
  say "  大概要 2-5 分钟 —— 第一次得下载程序本体和浏览器（几百 MB），"
  say "  网慢就久一点，别急。"
  say ""
  if [ "$ASSUME_YES" = 1 ]; then
    say "  ${C_DIM}（全自动模式：所有问题都用默认值）${C_OFF}"
  elif [ ! -t 0 ]; then
    say "  ${C_YEL}注意：当前不是交互终端，所有问题会自动采用默认值。${C_OFF}"
    say "  ${C_DIM}想自己选，请直接在自己电脑的终端里运行本脚本。${C_OFF}"
  else
    say "  只会问你 1-2 个问题。拿不准的直接按回车，用默认值就行。"
  fi
  say ""

  # 端口
  # 说明：如果端口是「本安装目录自己的旧容器」占着的，视为可用（会原地重建），
  # 这样重复安装 / 改配置重跑才不会撞墙。
  #
  # ⚠️ dry-run 下**不要**去真正探测端口：
  #    探测结果会被下面重置回默认值，于是"端口被占，会自动往上找"这句提示
  #    和最后显示的端口自相矛盾（实测：先说 18610 被占，最后又显示 18610），
  #    小白看了完全懵。dry-run 只演示默认值，跳过探测最省事也最不容易误导。
  if [ "$DRY_RUN" = 1 ]; then
    [ -n "$API_PORT" ] || API_PORT="$DEFAULT_API_PORT"
    [ -n "$WEB_PORT" ] || WEB_PORT="$DEFAULT_WEB_PORT"
  else
    if [ -z "$API_PORT" ]; then
      API_PORT="$(pick_port "$DEFAULT_API_PORT")"
    else
      require_port_usable "$API_PORT" "接口"
    fi
    if [ -z "$WEB_PORT" ]; then
      WEB_PORT="$(pick_port "$DEFAULT_WEB_PORT")"
    else
      require_port_usable "$WEB_PORT" "网页"
    fi
  fi

  step "开始检查环境"
  detect_os
  [ -n "$PKG" ] && ok "系统：$OS_ID $OS_VER（用 $PKG 装东西）" || warn "认不出这个系统的包管理器，可能需要手工装依赖"
  # 资源体检：内存太小是这套栈最**隐蔽**的失败源。
  # 容器里跑着 Chromium（shm 2G），1G 内存的小鸡会在出片那一刻被 OOM 杀掉，
  # 日志里只留一句 Killed —— 小白根本看不出是内存不够。
  # 提前说清楚，比事后让他对着 "Killed" 发懵强得多。
  check_resources

  # 安装目录能否创建/写入 —— 提前拦住，别等下载完几百 MB 才失败
  check_install_dir_writable
  # 命名冲突保护：宁可停下，也不覆盖别人的服务/容器
  assert_no_unit_conflict
  assert_no_container_conflict

  if [ "$DRY_RUN" != 1 ]; then
    ensure_git
    ensure_docker
  else
    say "    [dry-run] 跳过：检查并自动安装 git / docker / docker compose"
  fi

  if ! command -v python3 >/dev/null 2>&1 && [ "$DRY_RUN" != 1 ]; then
    step "缺 python3，正在自动安装"
    pkg_install "python3" || true
  fi

  step "准备程序文件"
  if [ "$DRY_RUN" != 1 ]; then
    # ⚠️ 创建目录之后必须**立刻验证真的写进去了**，不能只 mkdir 完就往下走。
    #    实测事故：把 --dir 指到 /proc/nope/mvw（父级不存在且不可写）时，
    #    mkdir 失败了却被忽略，脚本一路打印「✓ 配置完成」，直到最后
    #    `cd $INSTALL_DIR` 才炸，报一句没头没尾的 "No such file or directory"，
    #    还让小白去 `cd /proc/nope/mvw` 看日志（一个根本不存在的目录）。
    #    早失败在「准备程序文件」这一步，比晚失败在「启动服务」好得多。
    if ! mkdir -p "$INSTALL_DIR" 2>/dev/null; then
      die "建不了安装目录 $INSTALL_DIR（上级目录不存在或没有写权限）。
    换个目录试试，比如：
      bash $SELF --dir /opt/mvw --api-port $API_PORT --web-port $WEB_PORT
    （/opt 或你的家目录一般都行）"
    fi
    if ! touch "$INSTALL_DIR/.write-test" 2>/dev/null; then
      die "安装目录 $INSTALL_DIR 建出来了，但写不进去（磁盘满？只读挂载？权限不够？）。
    检查一下：df -h $INSTALL_DIR  和  ls -ld $INSTALL_DIR"
    fi
    rm -f "$INSTALL_DIR/.write-test"
    if [ -f "$INSTALL_DIR/.env" ]; then rm -f "$INSTALL_DIR/.env"; fi  # 统一用 compose 环境变量
  fi
  fetch_muse2api "$INSTALL_DIR"

  step "配置密钥"
  if [ "$DRY_RUN" = 1 ]; then
    API_KEY="m2a_<自动生成的随机密钥>"
  else
    # 已有安装 → 复用原来的 Key。
    # 重新生成会让所有已配置的客户端、以及导号命令里的 --key 全部失效。
    local _oldkey; _oldkey="$(read_existing_key)"
    if [ -n "$_oldkey" ]; then
      API_KEY="$_oldkey"
      ok "沿用上次的密钥（客户端不用重配）"
    else
      API_KEY="$(gen_key)"
      ok "已生成一把随机密钥（只显示在最后，请留意）"
    fi
  fi

  step "写入配置"
  write_compose "$INSTALL_DIR"
  write_webpage "$INSTALL_DIR"
  # 一次输出一整句，别拆成两条 —— 拆开会和 pick_port 的告警交错成乱码
  ok "配置完成：接口端口 $API_PORT，网页端口 $WEB_PORT"

  # ── 域名（默认不要） ──
  if [ -z "$DOMAIN" ] && [ "$NO_DOMAIN" != 1 ] && [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
    if ask_yn "要不要给网页绑个域名（会自动配 HTTPS）？" n; then
      DOMAIN="$(ask "你的域名（要已经解析到这台机器）" "")"
      DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
    fi
  fi

  if [ -n "$DOMAIN" ]; then
    step "绑定域名 $DOMAIN"
    IP_NOW="$(public_ip)"
    RESOLVED="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)"
    if [ -z "$RESOLVED" ]; then
      # ⚠️ 这里只 warn 是不够的：早期版本 warn 完就继续，最后照样打印
      #    「✓ 网页服务已启动」，小白以为域名能用了，打开却打不开。
      #    必须把"域名这次没生效、现在只能用 IP 访问"记下来，
      #    在最后的验收清单里再明确说一次。
      warn "域名 $DOMAIN 解析不出来（DNS 还没生效？）"
      warn "这次先不配 HTTPS —— 域名暂时用不了。"
      DOMAIN_SKIPPED="$DOMAIN"
      DOMAIN=""
    elif [ -n "$IP_NOW" ] && [ "$RESOLVED" != "$IP_NOW" ]; then
      warn "域名 $DOMAIN 解析到 $RESOLVED，但本机公网 IP 是 $IP_NOW"
      warn "这次先不配 HTTPS（DNS 没指对，证书签不下来）。"
      DOMAIN_SKIPPED="$DOMAIN"
      DOMAIN=""
    else
      setup_domain_caddy || { warn "HTTPS 配置失败，网页仍可用 IP:${WEB_PORT} 访问"; DOMAIN_SKIPPED="$DOMAIN"; DOMAIN=""; }
    fi
  fi

  step "启动服务"
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s cd %s && docker compose -p %s up -d --build\n' "$C_CYN" "$C_OFF" "$INSTALL_DIR" "$CONTAINER_NAME"
    # ⚠️ 这里必须用 "$WEB_UNIT"（含派生名），**不能**写死 "$APP_NAME-web.service"。
    #    unit 名是按安装目录派生的（见 derive_names），写死的话 dry-run 会显示一个
    #    根本不存在的服务名 —— 比如装到 /opt/mvtest 时实际是 mvtest-web.service，
    #    却打印 mvw-web.service。小白拿这个去 systemctl 查会扑空。
    printf '    %s[dry-run]%s systemctl enable --now %s\n' "$C_CYN" "$C_OFF" "$WEB_UNIT"
  else
    local out rc
    out="$(cd "$INSTALL_DIR" && compose up -d --build 2>&1)"; rc=$?
    if [ "$rc" != 0 ]; then
      printf '%s\n' "$out" | tail -8 | sed 's/^/    /'
      case "$out" in
        *"is already in use"*)  die "容器名被占用了 —— 可能这台机器上已经装过一次。
       先看看：docker ps -a | grep '${CONTAINER_NAME}'
       或者卸载重装：sudo bash ${SELF} --uninstall" ;;
        *"address already in use"*|*"port is already allocated"*)
          die "端口被占用了，换个端口重跑：sudo bash ${SELF} --api-port <另一个端口>" ;;
        # docker 地址池被分光时的天书报错，翻译成人话 + 给出可操作步骤。
        # （write_compose 已经用 network_mode: bridge 避免消耗池子，
        #   但机器上如果本来就有别的 compose 项目把池子占满，仍可能撞上。）
        *"address pools have been fully subnetted"*|*"could not find an available, non-overlapping IPv4 address pool"*)
          die "Docker 的网段用完了 —— 这台机器上的网络太多了，开不出新网段。
    清理一下没人用的旧网络就能继续（不会动到正在跑的服务）：
      docker network prune -f
    然后重跑本命令即可。" ;;
        *) die "启动失败（上面是原始输出）。看日志：cd $INSTALL_DIR && docker compose -p $CONTAINER_NAME logs --tail=40" ;;
      esac
    fi
    ok "容器已启动"

    step "等待服务就绪（初次启动要装浏览器，可能 1-2 分钟）"
    if wait_api_ready; then
      ok "接口服务正常"
      # 接口有响应 ≠ 代码是带修复的版本 —— 再查一遍代码级修复。
      verify_installed_fixes || true
    else
      warn "接口服务等了好久还没就绪，看看日志："
      docker logs "$CONTAINER_NAME" 2>&1 | tail -10 | sed 's/^/    /'
    fi

    write_service
    ok "网页服务已启动"

    save_state
  fi

  save_state
  print_next_steps "$(public_ip)"
}

# ── 域名：复用/自建 Caddy ────────────────────────────────────────────
setup_domain_caddy() {
  local block_file
  # 情况 A：宿主上有 caddy 二进制
  if command -v caddy >/dev/null 2>&1 && [ -f /etc/caddy/Caddyfile ]; then
    local cfg=/etc/caddy/Caddyfile
    local bak="${cfg}.bak-$(date +%Y%m%d-%H%M%S)-preMuse"
    run cp "$cfg" "$bak"
    strip_managed_block "$cfg"
    if [ "$DRY_RUN" != 1 ]; then
      cat >> "$cfg" <<EOF

# >>> muse-video managed block —— 由 install-muse-video.sh 维护，请勿手改 >>>
$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:${WEB_PORT}
}
# <<< muse-video managed block <<<
EOF
    fi
    if run caddy validate --config "$cfg" >/dev/null 2>&1; then
      if run systemctl reload caddy 2>/dev/null || run caddy reload --config "$cfg" 2>/dev/null; then
        ok "已挂到现有网页服务器上（原有网站不受影响）"
        return 0
      fi
    fi
    run cp "$bak" "$cfg"
    return 1
  fi

  # 情况 B：80/443 被占用但不是 caddy
  if ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE ':(80|443)$'; then
    warn "这台机器的 80/443 已经被别的程序占着了，不方便自动接管。"
    say "    想用域名的话，在占着 80/443 的那个软件里加一条反代，指向 127.0.0.1:${WEB_PORT} 即可。"
    return 1
  fi

  # 情况 C：80/443 空着 → 自己起一个 caddy 容器
  run docker volume create muse_caddy_data >/dev/null 2>&1 || true
  if [ "$DRY_RUN" != 1 ]; then
    # 必须先落盘再 up，否则 docker 会把不存在的文件创建成目录
    cat > "$INSTALL_DIR/Caddyfile" <<EOF
{
	email admin@${DOMAIN}
}

$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:${WEB_PORT}
}
EOF
  fi
  run docker run -d --name "$CADDY_NAME" --restart always \
    --network host \
    -v "$INSTALL_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v muse_caddy_data:/data \
    caddy:2-alpine >/dev/null 2>&1 || return 1
  ok "已自动配好 HTTPS"
  return 0
}

strip_managed_block() {
  local f="$1"
  [ -f "$f" ] || return 0
  [ "$DRY_RUN" = 1 ] && return 0
  local tmp; tmp="$(mktemp)"
  awk '
    /# >>> muse-video managed block/ { skip=1; next }
    /# <<< muse-video managed block/ { skip=0; next }
    !skip { print }
  ' "$f" > "$tmp" && mv "$tmp" "$f"
}

# ── --status ─────────────────────────────────────────────────────────
do_status() {
  # ⚠️ --status 是**只读**操作，不需要管理员权限。
  #    早期版本在 main 里对 --status 也调了 check_root，结果普通用户
  #    （或 docker 组用户）想看「服务在跑吗？我的网址和 Key 是什么？」
  #    会被一句「请用管理员权限运行」挡回去 —— 对小白来说是纯粹的惊吓，
  #    他并没有要改任何东西。现在放行，只在**真的**读不到时给温和提示。
  load_state || need_state
  say ""
  printf '%s%s 运行状态%s\n\n' "$C_BLD" "$APP_LABEL" "$C_OFF"
  if ! command -v docker >/dev/null 2>&1; then
    err "这台机器上没有 docker"; return 1
  fi
  local st
  # 先解析出真正在跑的容器名（手工改过 compose / 老脚本装过时，$CONTAINER_NAME 会对不上）
  local real_c
  real_c="$(resolve_container_name)"
  if [ -n "$real_c" ]; then
    CONTAINER_NAME="$real_c"
  fi
  # 容器不存在时 docker 会把错误写进 stderr，必须整段丢弃，
  # 否则报错文字会混进 st，让下面 case 匹配不上（表现为多余的换行+missing）
  st="$(docker inspect "$CONTAINER_NAME" --format '{{.State.Status}}' 2>/dev/null | head -1)"
  [ -n "$st" ] || st="missing"
  case "$st" in
    running)
      if [ "$CONTAINER_NAME" != "$APP_NAME" ]; then
        ok "接口服务：运行中（容器 ${CONTAINER_NAME}，端口 ${API_PORT}）"
      else
        ok "接口服务：运行中（端口 ${API_PORT}）"
      fi
      if api_alive; then
        ok "接口自检：正常"
        # 顺便复查代码级修复还在不在（用户随时可以跑 --status 确认版本没装错）
        verify_installed_fixes || true
      else
        warn "接口自检：没响应（看日志：docker logs "$CONTAINER_NAME" --tail 40）"
      fi ;;
    missing)
      # docker 权限不足时 inspect 也返回空，会落到这里 —— 和「真没容器」长得一样。
      # 区分一下：不是 root、也没有 docker 组权限 → 明确说是权限问题，别误导。
      if [ "$(id -u)" != 0 ] && ! docker ps >/dev/null 2>&1; then
        warn "看不到容器状态（当前用户没有 docker 权限）"
        say "    换个身份再看：sudo bash ${SELF} --status"
      else
        err "接口服务：没找到容器"
        say "    如果你确定服务在跑，可能是容器名和预期不一致。看全部容器："
        say "        docker ps -a"
      fi ;;
    *)       err "接口服务：$st" ;;
  esac
  if systemctl is-active "${WEB_UNIT}" >/dev/null 2>&1; then
    ok "网页服务：运行中（端口 ${WEB_PORT}）"
  else
    err "网页服务：没运行"
  fi

  # 关键信息：小白关掉安装窗口后，要能从这里把地址和 Key 找回来
  local k ip
  k="$(read_existing_key)"
  ip="$(public_ip 2>/dev/null)"
  [ -n "$ip" ] || ip="<本机公网IP>"
  say ""
  say "  ${C_BLD}连接信息${C_OFF}（配客户端、导号都用这些）"
  say "    网页地址：   http://${ip}:${WEB_PORT}/"
  say "    接口地址：   http://${ip}:${API_PORT}/v1"
  if [ -n "$k" ]; then
    say "    API Key：    ${k}"
    say "    账号池面板： http://${ip}:${API_PORT}/admin?key=${k}"
  else
    warn "读不到 API Key（可能装的是旧版本）。可从 $INSTALL_DIR/docker-compose.yml 里找 MUSE2API_KEY"
  fi

  local acct
  acct="$(curl -s --max-time 8 "http://127.0.0.1:${API_PORT}/admin/accounts" \
    -H "Authorization: Bearer ${k}" 2>/dev/null \
    | grep -o '"total":[0-9]*' | head -1)"
  [ -n "$acct" ] && say "    账号池：     ${acct#*:} 个账号"
  if [ -n "$acct" ] && [ "${acct#*:}" = "0" ]; then
    say ""
    warn "账号池是空的 —— 还没导入 muse.ai 账号，现在生成不了视频。"
    say ""
    say "    ${C_BLD}加账号很简单，就两步：${C_OFF}"
    say "      1) 在你自己的电脑上下载这个小工具（存到桌面）："
    say "         https://raw.githubusercontent.com/${SELF_REPO}/main/tools/get_muse_cookie.py"
    say "      2) 命令行里敲：${C_BLD}python get_muse_cookie.py${C_OFF}"
    say "         它问你地址和 Key，把下面这两行复制过去即可（会弹浏览器让你登录）："
    say "           地址：${ip}:${API_PORT}"
    say "           Key： ${k:-<上面那行>}"
    say ""
    say "     ${C_DIM}想加多个账号？它会问「还要再导入一个吗」，按 y 就行。"
    say "     导入完再跑一次 --status 就能看到账号数变成 1（或更多）。${C_OFF}"
  fi
  say ""
  say "  最近的日志："
  docker logs "$CONTAINER_NAME" --tail 5 2>&1 | sed 's/^/    /'
  say ""
}

# ── --uninstall ──────────────────────────────────────────────────────
do_uninstall() {
  load_state || need_state
  say ""
  warn "准备卸载 $APP_LABEL（目录：$INSTALL_DIR）"
  # ⚠️ 卸载是**破坏性**操作，非交互环境下**绝不能默默继续**。
  #
  #    早期版本写的是 `[ "$ASSUME_YES" != 1 ] && [ -t 0 ]` 才问确认 ——
  #    也就是说在非交互环境（管道、`< /dev/null`、CI、从网页复制的命令串）里
  #    会**跳过确认直接卸载**。实测复现：`bash install.sh --uninstall < /dev/null`
  #    一行就把容器删了，小白如果误粘贴这么一条，服务当场就没了。
  #
  #    正确做法：非交互时要求用户**显式**给 --yes 才动手，否则拒绝并告诉他怎么做。
  if [ "$ASSUME_YES" != 1 ]; then
    if [ ! -t 0 ]; then
      die "当前不是交互终端，出于安全我没有直接卸载。
       确认要卸载的话，请显式加上 --yes：
           sudo bash ${SELF} --uninstall --yes
       （不加 --yes 时，请在自己电脑的终端里跑，脚本会问你「确定吗」）"
    fi
    if ! ask_yn "确定要卸载吗？" n; then say "  已取消。"; return 0; fi
  fi
  step "停止并删除容器"
  if [ "$DRY_RUN" != 1 ]; then
    (cd "$INSTALL_DIR" 2>/dev/null && compose down --remove-orphans 2>&1 | tail -2) || true
    docker rm -f "$CADDY_NAME" >/dev/null 2>&1 || true
  fi
  step "摘除域名配置"
  if [ -f /etc/caddy/Caddyfile ]; then
    local bak="/etc/caddy/Caddyfile.bak-$(date +%Y%m%d-%H%M%S)-preUninstall"
    run cp /etc/caddy/Caddyfile "$bak"
    strip_managed_block /etc/caddy/Caddyfile
    run systemctl reload caddy 2>/dev/null || true
  fi
  step "移除网页服务"
  run systemctl disable --now "${WEB_UNIT}" 2>/dev/null || true
  run rm -f "/etc/systemd/system/${WEB_UNIT}"
  run systemctl daemon-reload

  local del_dir=n
  # 非交互时**默认保留**数据（del_dir=n），只删服务不删账号 —— 这是保守的安全默认。
  # 要连数据一起删，只有交互确认（或手动 rm -rf）这一条路。
  # 注：这里不再额外打印说明 —— 下面分支的「数据保留在 …」已经把结果讲清楚了，
  #     多说一句反而啰嗦、还会和它重复。
  if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
    say ""
    say "  账号和生成过的视频都放在 $INSTALL_DIR 里。"
    if ask_yn "连这些数据一起删掉吗？（删了就不能恢复）" n; then del_dir=y; fi
  fi
  if [ "$del_dir" = y ]; then
    run rm -rf "$INSTALL_DIR"
    ok "目录已删除"
  else
    ok "数据保留在 $INSTALL_DIR（下次重装会自动接着用）"
  fi
  say ""
  ok "卸载完成"
  say ""
}

# ── --upgrade ────────────────────────────────────────────────────────
do_upgrade() {
  load_state || need_state
  say ""
  step "升级 $APP_LABEL"
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s git pull && docker compose -p %s up -d --build\n' "$C_CYN" "$C_OFF" "$CONTAINER_NAME"
    return 0
  fi
  # 回滚用的「旧镜像」必须先**打固定 tag 保住**，不能只记 ID。
  #
  #   ⚠️ 为什么：`docker inspect X --format {{.Image}}` 拿到的是容器创建时的镜像 ID
  #   （形如 sha256:ec3c...）。而下面 `compose up --build` 会用**同一个 tag**
  #   （X:latest）重建，旧镜像被顶掉、变成 dangling 层，接着就可能被回收。
  #   实测：拿那个 sha256 去 `docker run` 直接报 "No such image" ——
  #   也就是**原来的回滚根本没生效**（错误还被 `|| true` 吞了，只打印「已尝试回滚」骗人）。
  #   正解：先把当前镜像另存一个固定 tag，回滚时用这个 tag，就一定还在。
  local old_tag=""
  if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    old_tag="${CONTAINER_NAME}:rollback"
    if docker tag "$CONTAINER_NAME:latest" "$old_tag" >/dev/null 2>&1; then
      : # 保住了
    else
      # 容器在但 :latest 不在（少见），退而用它的镜像 ID
      old_tag="$(docker inspect "$CONTAINER_NAME" --format '{{.Image}}' 2>/dev/null || echo '')"
    fi
  fi
  if [ -d "$INSTALL_DIR/.git" ]; then
    (cd "$INSTALL_DIR" && git pull --ff-only 2>&1 | tail -3) || warn "git pull 没成功，仍尝试用现有代码重建"
  else
    warn "安装目录不是 git 仓库，跳过拉取最新代码"
  fi
  if (cd "$INSTALL_DIR" && compose up -d --build 2>&1 | tail -4); then
    if wait_api_ready; then
      ok "升级完成，服务正常"
      # 升级后同样校验一次：新版代码该带的修复不能丢。
      verify_installed_fixes || true
    else
      err "新版本启动异常，正在回滚"
      # ⚠️ 回滚**必须补齐和正常 compose 一样的关键参数**，不能只映射端口。
      #
      #    早期版本这里是：
      #        docker run -d --name X --restart always -p PORT:PORT "$old_id"
      #    后果：回滚出来的容器等于一个「半残」实例 ——
      #      · 没有 -v data 挂载 → 账号/任务数据全看不见（像被清空）
      #      · 没有 MUSE2API_KEY   → 鉴权密钥变了，所有客户端连同导号工具一并失联
      #      · 没有 --shm-size     → 无头浏览器渲染多标签页时可能崩
      #      · 没有 command 覆盖   → 自定义端口时应用还在听 18610
      #    也就是说：升级失败后「回滚」反而把服务搞得更坏，小白会以为数据丢了。
      #    下面每一项都照 write_compose 对齐。
      if [ -n "$old_tag" ]; then
        local rkey; rkey="$(read_existing_key)"
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
        # 校验回滚镜像确实存在；不存在就别假装成功
        if ! docker image inspect "$old_tag" >/dev/null 2>&1; then
          err "回滚镜像 $old_tag 已不存在，无法自动回滚。"
          warn "服务当前是停的。重跑一次安装即可恢复到可用状态：sudo bash ${SELF}"
          return 1
        fi
        if docker run -d --name "$CONTAINER_NAME" --restart always \
          -p "${API_PORT}:${API_PORT}" \
          -v "$INSTALL_DIR/data:/app/data" \
          --shm-size 2g \
          -e "MUSE2API_KEY=${rkey}" \
          -e "MUSE2API_HOST=0.0.0.0" \
          -e "MUSE2API_PORT=${API_PORT}" \
          -e "MUSE2API_PUBLIC_BASE=" \
          -e "MUSE2API_CHROMIUM=/usr/bin/chromium" \
          -e "MUSE2API_CDP_PORT=19210" \
          -e "MUSE2API_IMAGE_TIMEOUT=240" \
          -e "MUSE2API_VIDEO_TIMEOUT=600" \
          -e "MUSE2API_CHAT_TIMEOUT=300" \
          "$old_tag" sh -c "python -m uvicorn app:app --host 0.0.0.0 --port ${API_PORT}" \
          >/dev/null 2>&1; then
          warn "已回滚到升级前的版本（数据卷 / 密钥 / 浏览器参数都已带上），请用 --status 复查"
        else
          err "回滚也没起来。请把下面这条的输出发出来求助："
          say "      docker logs ${CONTAINER_NAME} --tail 50"
        fi
      else
        warn "没有可用的旧镜像，无法自动回滚。服务当前可能不可用，重跑安装可恢复。"
      fi
      return 1
    fi
  else
    err "重建失败，服务可能仍是旧的"
    return 1
  fi
}

# ── 主流程 ───────────────────────────────────────────────────────────
main() {
  detect_os
  derive_names

  if [ "$DO_STATUS" = 1 ]; then     do_status; exit $?; fi
  if [ "$DO_UNINSTALL" = 1 ]; then  check_root "$@"; do_uninstall; exit $?; fi
  if [ "$DO_UPGRADE" = 1 ]; then    check_root "$@"; do_upgrade; exit $?; fi

  check_root "$@"

  # 已装过 → 先读回上次的配置作为默认值（命令行显式给的优先）
  if [ -f "$INSTALL_DIR/install.conf" ]; then
    load_state || true
  fi

  # 已装过 → 让用户选
  if [ -f "$INSTALL_DIR/install.conf" ] && [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
    say ""
    say "  检测到这台机器上已经装过 $APP_LABEL。"
    say "    1) 重新配置并安装（会沿用现有数据）"
    say "    2) 升级到最新版"
    say "    3) 卸载"
    say "    4) 退出"
    local m=""
    while :; do
      printf '  请选择 [1-4，回车=1]: ' >&2
      read -r m || m=""
      m="${m:-1}"
      case "$m" in
        1) break ;;
        2) do_upgrade; exit $? ;;
        3) do_uninstall; exit $? ;;
        4) exit 0 ;;
        *) printf '  %s没看懂「%s」—— 请输入 1 到 4 之间的数字%s\n' "$C_YEL" "$m" "$C_OFF" >&2 ;;
      esac
    done
  fi

  do_install
}

# ⚠️ 小白按 Ctrl+C 中断时（比如嫌下载太慢），脚本默认什么都不说就退出了，
#    他完全不知道自己中断到了哪一步、机器上留了什么、接下来该干什么。
#    这里接住中断，明确告诉他：可以直接重跑，脚本是幂等的、会接着来。
on_interrupt() {
  printf '\n'
  say "  ${C_YEL:-}按了 Ctrl+C，安装中断了。${C_OFF:-}"
  say "  不用担心 —— 这个脚本可以安全地重复运行。刚才下到一半的文件、"
  say "  装好的容器都会保留，重跑一次就会接着来："
  say "      sudo bash ${SELF:-install.sh} --dir ${INSTALL_DIR:-/opt/mvw}"
  say ""
  say "  想看看现在装到哪了："
  say "      sudo bash ${SELF:-install.sh} --status --dir ${INSTALL_DIR:-/opt/mvw}"
  printf '\n'
  exit 130
}
trap on_interrupt INT

main "$@"
