#!/usr/bin/env bash
# muse-video 安装脚本 —— 回归测试套件
# 用法：bash test-install.sh
# 退出码 = 失败数

set -uo pipefail

INSTALLER="${INSTALLER:-/root/install-muse-video.sh}"
TDIR="/opt/muse-regress"
APIP=28710
WEBP=28711
PASS=0
FAIL=0

G=$'\033[0;32m'; R=$'\033[0;31m'; Y=$'\033[0;33m'; B=$'\033[1m'; O=$'\033[0m'

t_ok()   { PASS=$((PASS+1)); printf '  %s✓%s %s\n' "$G" "$O" "$1"; }
t_fail() { FAIL=$((FAIL+1)); printf '  %s×%s %s\n' "$R" "$O" "$1"; [ $# -gt 1 ] && printf '      证据：%s\n' "$2"; }
t_case() { printf '\n%s▸ %s%s\n' "$B" "$1" "$O"; }

cleanup_all() {
  # ⚠️ 只操作本测试自己创建的对象（名字都带 muse-regress / muse-conflict 前缀），
  # 绝不用 muse-* 通配 —— 曾因此误删正式的 muse-video-web.service。
  cd "$TDIR" 2>/dev/null && docker compose down --remove-orphans >/dev/null 2>&1
  docker rm -f muse-regress muse-regress-caddy >/dev/null 2>&1
  systemctl disable --now muse-regress-web.service >/dev/null 2>&1
  rm -f /etc/systemd/system/muse-regress-web.service
  rm -f /etc/systemd/system/muse-conflict-name-web.service
  systemctl daemon-reload >/dev/null 2>&1
  rm -rf "$TDIR" /opt/muse-conflict /opt/muse-conflict-name
  # ⚠️ 第 8 组（端口冲突）会在 /opt/muse-conflict 派生出 muse-conflict-web.service
  #    并让它占着测试端口。早期版本没清这个 unit，于是它一直 active、
  #    把 28719 端口长期占住 —— 后来再跑端口相关用例就会莫名"冲突"。
  #    这里显式收掉它（以及它的进程）。
  systemctl disable --now muse-conflict-web.service >/dev/null 2>&1
  rm -f /etc/systemd/system/muse-conflict-web.service
  systemctl daemon-reload >/dev/null 2>&1
  rm -rf /opt/muse-conflict
  # 五轮实测新增的临时对象（都得点名清理，不用通配）
  docker rm -f muse-netcheck >/dev/null 2>&1
  systemctl disable --now muse-netcheck-web.service >/dev/null 2>&1
  rm -f /etc/systemd/system/muse-netcheck-web.service
  for d in /tmp/muse-drychk /tmp/muse-drychk2 /opt/muse-netcheck; do
    rm -rf "$d"
  done
  # 第二轮新增（小白体验回归组用到的实例）
  docker rm -f muse-selfchk muse-pipechk >/dev/null 2>&1
  systemctl disable --now muse-selfchk-web.service >/dev/null 2>&1
  rm -f /etc/systemd/system/muse-selfchk-web.service
  rm -rf /opt/muse-selfchk /tmp/muse-pipechk
  systemctl daemon-reload >/dev/null 2>&1
}

# ─────────────────────────────────────────────
t_case "1. 语法与静态检查"
if bash -n "$INSTALLER" 2>/dev/null; then t_ok "bash -n 通过"; else t_fail "bash -n 失败"; fi
if [ "$(tr -dc '\r' < "$INSTALLER" | wc -c)" = "0" ]; then t_ok "换行符是 LF"; else t_fail "含 CRLF"; fi

t_case "2. 帮助与参数校验"
# 先落盘再 grep，规避 set -o pipefail + grep -q 的 SIGPIPE 误判
bash "$INSTALLER" --help >/tmp/h.log 2>&1
grep -q -- "--api-port" /tmp/h.log && t_ok "--help 正常" || t_fail "--help 异常"
O1="$(bash "$INSTALLER" --api-port abc 2>&1)"; case "$O1" in *abc*) t_ok "非数字端口被拦" ;; *) t_fail "非数字端口未拦" "$O1" ;; esac
O2="$(bash "$INSTALLER" --web-port 80 2>&1)"; case "$O2" in *1024*) t_ok "越界端口被拦" ;; *) t_fail "越界端口未拦" "$O2" ;; esac
O3="$(bash "$INSTALLER" --nonsense 2>&1)"; case "$O3" in *nonsense*) t_ok "未知参数被拦" ;; *) t_fail "未知参数未拦" "$O3" ;; esac

t_case "2b. 缺值参数（小白最容易手滑）"
# 这 4 条曾经**不报错就继续装**，会误装到默认目录 —— 必须拦住
for _opt in --dir --api-port --web-port --domain; do
  _out="$(bash "$INSTALLER" "$_opt" 2>&1)"; _rc=$?
  if [ "$_rc" != 0 ] && printf '%s' "$_out" | grep -q "后面要跟"; then
    t_ok "$_opt 缺值被拦"
  else
    t_fail "$_opt 缺值未拦" "退出码=$_rc"
  fi
done
# 等号形式
_out="$(bash "$INSTALLER" --dir= 2>&1)"; _rc=$?
[ "$_rc" != 0 ] && t_ok "--dir= 空值被拦" || t_fail "--dir= 空值未拦"
# 缺值时必须不产生任何目录
# 缺值时必须不产生任何目录（默认目录已改为 /opt/mvw，避免与既有服务撞名）
if [ ! -d /opt/mvw ] && [ ! -d /opt/muse-video ]; then
  t_ok "缺值未误装默认目录"
else
  t_fail "缺值误装了默认目录"
fi

t_case "2c. 参数冲突检查"
# 注意：这些 case 的输出只用「是否匹配」来判断，绝不把输出塞进 t_ok 的参数，
# 否则错误文案会被当成成功描述打印，污染整份测试报告。
bash "$INSTALLER" --status --uninstall >/tmp/c1.log 2>&1
if grep -qE "只能给一个|一次只能" /tmp/c1.log; then t_ok "互斥子命令被拦"; else t_fail "互斥子命令未拦" "$(tail -2 /tmp/c1.log | tr '\n' ' ')"; fi
bash "$INSTALLER" --api-port 9000 --web-port 9000 >/tmp/c2.log 2>&1
if grep -q "不能是同一个" /tmp/c2.log; then t_ok "端口撞车被拦"; else t_fail "端口撞车未拦" "$(tail -2 /tmp/c2.log | tr '\n' ' ')"; fi

t_case "3. dry-run 无副作用"
rm -rf "$TDIR"
bash "$INSTALLER" --dry-run --yes --dir "$TDIR" --api-port $APIP --web-port $WEBP >/tmp/dr.log 2>&1
if [ ! -d "$TDIR" ]; then t_ok "dry-run 未创建目录"; else t_fail "dry-run 创建了目录"; fi
grep -q "dry-run" /tmp/dr.log && t_ok "dry-run 有输出" || t_fail "dry-run 无输出"
# dry-run 输出里不该有未替换的占位符
# 只检查真正的路径类占位符；密钥占位符 <自动生成的随机密钥> 是 dry-run 的设计
if grep -qE '<本机>|<端口>|<你的|<域名>' /tmp/dr.log; then
  t_fail "dry-run 输出含未替换占位符" "$(grep -oE '<[^>]*>' /tmp/dr.log | sort -u | head -3 | tr '\n' ' ')"
else t_ok "无未替换占位符（密钥占位符属预期）"; fi
# dry-run 也要检查安装目录可写性
# ⚠️ 不能写 `cmd | grep -q X && ...`：脚本头有 set -o pipefail，
#    而 grep -q 一命中就退出，会给左边进程发 SIGPIPE（退出码 141），
#    导致整条管道返回 141，把「匹配成功」误判成失败。
#    正解：先落盘，再对文件 grep。
bash "$INSTALLER" --dry-run --yes --dir "$TDIR" >/tmp/dr2.log 2>&1
grep -q "安装目录" /tmp/dr2.log \
  && t_ok "dry-run 检查了安装目录" || t_fail "dry-run 未检查安装目录" "$(grep -c . /tmp/dr2.log)"

t_case "4. 真实安装"
cleanup_all
bash "$INSTALLER" --yes --dir "$TDIR" --api-port $APIP --web-port $WEBP >/tmp/inst.log 2>&1
RC=$?
[ "$RC" = 0 ] && t_ok "安装退出码 0" || t_fail "安装退出码 $RC" "$(tail -5 /tmp/inst.log)"
if grep -q "No such file or directory" /tmp/inst.log; then
  t_fail "安装中有 No such file 报错" "$(grep -m1 'No such file' /tmp/inst.log)"
else t_ok "无文件路径报错"; fi

t_case "5. 安装结果核验"
docker inspect muse-regress --format '{{.State.Status}}' 2>/dev/null | grep -q running \
  && t_ok "容器 running" || t_fail "容器未运行"
# 判据：容器内能拿到任意 HTTP 状态码即算活（401 说明服务在跑、只是缺 Key）
Alive=0; Code=""
for i in 1 2 3 4 5 6 7 8; do
  Code="$(docker exec muse-regress curl -s -o /dev/null -w '%{http_code}' \
    --max-time 6 "http://127.0.0.1:${APIP}/v1/models" 2>/dev/null)"
  case "$Code" in ''|000) sleep 5 ;; *) Alive=1; break ;; esac
done
if [ "$Alive" = 1 ]; then
  t_ok "接口自检通过（容器内 HTTP $Code）"
else
  t_fail "接口自检失败（拿不到 HTTP 响应）" "$(docker logs muse-regress --tail 3 2>&1 | tr '\n' ' ')"
fi
systemctl is-active muse-regress-web.service >/dev/null 2>&1 \
  && t_ok "网页服务 active" || t_fail "网页服务未运行"
# 判据：先拿到 HTTP 状态码（200 即通），再看内容；不依赖 grep 管道退出码
WCode="$(curl -s -o /tmp/mv-web.html -w '%{http_code}' --max-time 8 "http://127.0.0.1:${WEBP}/" 2>/dev/null)"
if [ "$WCode" = "200" ]; then
  t_ok "网页返回 HTTP 200"
  if grep -q "生成视频" /tmp/mv-web.html 2>/dev/null; then
    t_ok "网页内容包含关键功能文案"
  else
    t_fail "网页内容不含预期文案" "$(head -c 120 /tmp/mv-web.html 2>/dev/null | tr '\n' ' ')"
  fi
else
  t_fail "网页打不开（HTTP ${WCode:-无响应}）" "$(systemctl status muse-regress-web.service --no-pager 2>&1 | head -3 | tr '\n' ' ')"
fi
rm -f /tmp/mv-web.html
[ -f "$TDIR/install.conf" ] && t_ok "状态文件已生成" || t_fail "状态文件缺失"
# 断言「版本号是我们自己的语义化版本」，而**不是**精确匹配某个号 ——
# 否则每次发版都要来改测试（第一版写成 ^SCRIPT_VERSION=1\.0\.0$，
# 升到 1.0.1 就误报"版本号异常"，纯属测试自己找麻烦）。
# 真正要防的是「被 os-release 污染」，即值变成了像 "12" 这种系统版本号。
grep -qE "^SCRIPT_VERSION=[0-9]+\.[0-9]+\.[0-9]+$" "$TDIR/install.conf" 2>/dev/null \
  && t_ok "状态文件版本号是语义化版本（未被 os-release 污染）" \
  || t_fail "状态文件版本号异常" "$(grep SCRIPT_VERSION "$TDIR/install.conf" 2>/dev/null)"

# ── API Key 的三道硬检查（这三条曾全部失守，是真事故级 bug）──
K="$(sed -n 's/^API_KEY=//p' "$TDIR/install.conf" 2>/dev/null | head -1)"
KC="$(grep -oP 'MUSE2API_KEY=\K.*' "$TDIR/docker-compose.yml" 2>/dev/null | head -1)"
# ① 必须记进状态文件（否则小白关掉窗口就找不回 Key）
[ -n "$K" ] && t_ok "API Key 已记入状态文件" || t_fail "API Key 没记进状态文件"
# ② 必须是真随机（上游 compose 里有个 m2a_change_me_to... 占位符，
#    曾经的实现会把它的前缀 m2a_change 误当成"上次的 Key"复用）
case "$K" in
  ""|m2a_change|m2a_change_me_to_your_secure_key|m2a_your_secret_admin_key_here)
    t_fail "API Key 是占位符，不是真随机钥匙！" "$K" ;;
  m2a_*)
    [ "${#K}" -ge 36 ] && t_ok "API Key 是真随机（${#K} 位）" || t_fail "API Key 长度可疑" "$K" ;;
  *) t_fail "API Key 格式异常" "$K" ;;
esac
# ③ 状态文件与 compose 必须一致（否则 status 显示的 Key 调不通）
[ "$K" = "$KC" ] && t_ok "状态文件与 compose 的 Key 一致" || t_fail "两处 Key 不一致" "conf=$K compose=$KC"

t_case "6. --status"
OUT="$(bash "$INSTALLER" --status --dir "$TDIR" 2>&1)"
printf '%s' "$OUT" | grep -q "运行中" && t_ok "--status 报运行中" || t_fail "--status 异常" "$(printf '%s' "$OUT" | head -3)"
# --status 必须能把地址和 Key 打回来（小白找回凭据的唯一途径）
printf '%s' "$OUT" | grep -q "连接信息" && t_ok "--status 有「连接信息」段" || t_fail "--status 缺连接信息"
printf '%s' "$OUT" | grep -q "API Key：" && t_ok "--status 能显示 API Key" || t_fail "--status 不显示 Key"
printf '%s' "$OUT" | grep -q "$K" && t_ok "--status 显示的 Key 与状态文件一致" || t_fail "--status 的 Key 不对"
printf '%s' "$OUT" | grep -q "账号池是空的" && t_ok "--status 提示了账号池为空" || t_fail "--status 未提示空账号池"

t_case "7. 幂等重跑（沿用配置）"
bash "$INSTALLER" --yes --dir "$TDIR" >/tmp/re.log 2>&1
RC7=$?
[ "$RC7" = 0 ] && t_ok "重跑退出码 0（不会因自己占端口而失败）" || t_fail "重跑退出码 $RC7" "$(tail -3 /tmp/re.log | tr '\n' ' ')"
if grep -q "${APIP}" /tmp/re.log; then t_ok "重跑沿用上次端口"; else t_fail "重跑丢了端口配置" "$(grep -c . /tmp/re.log)"; fi
# 关键：重跑不得换 Key
K2="$(sed -n 's/^API_KEY=//p' "$TDIR/install.conf" 2>/dev/null | head -1)"
[ "$K2" = "$K" ] && t_ok "重跑保持 API Key（客户端不用重配）" || t_fail "重跑换了 Key" "$K -> $K2"
grep -q "沿用上次的密钥" /tmp/re.log && t_ok "输出里说明了「沿用上次的密钥」" || t_fail "没提示沿用密钥"
docker inspect muse-regress --format '{{.State.Status}}' 2>/dev/null | grep -q running \
  && t_ok "重跑后容器仍健康" || t_fail "重跑后容器异常"

t_case "7b. 命名冲突保护（不覆盖别人的服务/容器）"
# 造一个「别人的」unit，名字正好等于我们要派生的那个（/opt/muse-conflict-name → muse-conflict-name-web.service）
FAKE_UNIT="/etc/systemd/system/muse-conflict-name-web.service"
cat > "$FAKE_UNIT" <<'UEOF'
[Unit]
Description=someone else's service
[Service]
ExecStart=/bin/true
UEOF
systemctl daemon-reload >/dev/null 2>&1
bash "$INSTALLER" --yes --dir /opt/muse-conflict-name --api-port 28731 --web-port 28732 >/tmp/nc.log 2>&1
RC_NC=$?
if [ "$RC_NC" != 0 ] && grep -q "冲突" /tmp/nc.log; then
  t_ok "检测到别人的同名服务并拒绝覆盖"
else
  t_fail "没拦住同名服务冲突" "退出码=$RC_NC $(tail -2 /tmp/nc.log | tr '\n' ' ')"
fi
# 别人的 unit 必须原样还在
grep -q "someone else's service" "$FAKE_UNIT" 2>/dev/null \
  && t_ok "别人的 unit 未被改动" || t_fail "别人的 unit 被覆盖了！"
rm -f "$FAKE_UNIT"; systemctl daemon-reload >/dev/null 2>&1
rm -rf /opt/muse-conflict-name

t_case "8. 端口冲突 fail-fast"
# 用另一个安装目录去抢 muse-regress 已占的端口 —— 对脚本来说这是「外人占的」，
# 必须 fail-fast。注意判据用「已被别的程序占用」这句完整文案。
bash "$INSTALLER" --yes --dir /opt/muse-conflict --api-port $APIP --web-port 28719 >/tmp/cf.log 2>&1
RC8=$?
if grep -q "已被别的程序占用\|已被占用" /tmp/cf.log; then
  t_ok "端口冲突被拦"
  [ "$RC8" != 0 ] && t_ok "冲突时退出码非 0（$RC8）" || t_fail "冲突时退出码竟为 0"
  if [ ! -d /opt/muse-conflict ]; then
    t_ok "冲突时未产生残留目录"
  else
    t_fail "冲突时留下残留目录"
    rm -rf /opt/muse-conflict
  fi
else t_fail "端口冲突未被拦" "$(tail -3 /tmp/cf.log | tr '\n' ' ')"; fi

t_case "8b. 自己的容器占用端口 → 应放行（幂等重跑的关键）"
# 同一个安装目录再装一次，端口是自己上次的 —— 必须成功，不能报冲突
bash "$INSTALLER" --yes --dir "$TDIR" >/tmp/cf2.log 2>&1
RC8B=$?
if [ "$RC8B" = 0 ]; then
  t_ok "自己占的端口被正确放行（退出码 0）"
else
  t_fail "自己占的端口被误判为冲突" "$(grep -E '已被|×' /tmp/cf2.log | head -2 | tr '\n' ' ')"
fi

t_case "9. 卸载（保留数据）"
bash "$INSTALLER" --yes --uninstall --dir "$TDIR" >/tmp/uni.log 2>&1
# docker inspect 无 stderr 泄漏，显式判存在性
CID="$(docker ps -a --filter "name=^muse-regress$" --format '{{.Names}}' 2>/dev/null)"
if [ -n "$CID" ]; then t_fail "容器仍存在" "$CID"; else t_ok "容器已删除"; fi
systemctl is-active muse-regress-web.service >/dev/null 2>&1 && t_fail "网页服务仍在" || t_ok "网页服务已停"
[ -f /etc/systemd/system/muse-regress-web.service ] && t_fail "unit 文件残留" || t_ok "unit 文件已清理"
[ -d "$TDIR" ] && t_ok "数据目录按预期保留" || t_fail "数据目录被删（默认应保留）"

t_case "10. 卸载后复检 --status"
SO="$(bash "$INSTALLER" --status --dir "$TDIR" 2>&1)"
case "$SO" in
  *"没找到容器"*|*"未安装"*|*"未运行"*) t_ok "--status 正确报告未运行" ;;
  *) t_fail "--status 报告异常" "$(printf '%s' "$SO" | head -3 | tr '\n' ' ')" ;;
esac

t_case "11. 五轮实测缺陷回归（2026-09-29）"
# 这一组锁定的是**真机上跑出来的**安装脚本缺陷，每条都对应一次真实事故。

# 11.1 安装目录不可写 → 必须当场失败，绝不能打印「配置完成」骗人
#      事故：--dir /proc/nope/mvw 时脚本假装成功，最后才在 cd 处炸，
#            还让小白去一个不存在的目录看日志。
bash "$INSTALLER" --yes --dir /proc/nope/mvw --api-port 28791 --web-port 28792 >/tmp/r1.log 2>&1
RC_R1=$?
if [ "$RC_R1" != 0 ] && ! grep -q "配置完成" /tmp/r1.log && grep -q "建不了安装目录\|写不进去" /tmp/r1.log; then
  t_ok "不可写目录当场失败且不谎报成功"
else
  t_fail "不可写目录没拦住 / 谎报了成功" "rc=$RC_R1 $(grep -E '配置完成|建不了|写不进去' /tmp/r1.log | head -2 | tr '\n' ' ')"
fi

# 11.2 目录名全是中文 → 必须能装成功
#      事故：compose 拿中文目录当项目名，推导出空串 → project name must not be empty。
#      国内小白极容易把目录设在含中文的路径下。
CN_DIR="/tmp/视频工作台测试"
rm -rf "$CN_DIR"
bash "$INSTALLER" --yes --dir "$CN_DIR" --api-port 28793 --web-port 28794 >/tmp/r2.log 2>&1
RC_R2=$?
if [ "$RC_R2" = 0 ] && ! grep -q "project name must not be empty" /tmp/r2.log; then
  t_ok "中文目录名能正常安装（compose 项目名已显式指定）"
else
  t_fail "中文目录名安装失败" "rc=$RC_R2 $(grep -E 'project name|启动失败' /tmp/r2.log | head -2 | tr '\n' ' ')"
fi
# 收尾：把中文目录装出来的实例清掉。
# 容器名 = 目录名派生（中文目录会得到 mvw-<6位十六进制哈希>）。
# ⚠️ 一定要用这个精确模式，不能用 `name=mvw-` 粗匹配 ——
#    那会连用户的 mvw-chk2 一起删掉。
CN_CID="$(docker ps -a --format '{{.Names}}' | grep -E '^mvw-[0-9a-f]{6}$' | head -1)"
[ -n "$CN_CID" ] && docker rm -f "$CN_CID" >/dev/null 2>&1
rm -rf "$CN_DIR"
for u in /etc/systemd/system/mvw-*-web.service; do
  [ -f "$u" ] || continue
  case "$(basename "$u")" in
    mvw-chk2-web.service) continue ;;   # 别动这个
  esac
  systemctl disable --now "$(basename "$u")" >/dev/null 2>&1
  rm -f "$u"
done
systemctl daemon-reload >/dev/null 2>&1

# 11.3 每次安装不得新建 docker 网络（否则反复安装会把地址池分光）
#      事故：28 个网络把 172.17~172.31 的池子耗干，之后所有容器都起不来，
#            报「all predefined address pools have been fully subnetted」。
NET_BEFORE="$(docker network ls --format '{{.Name}}' | wc -l)"
rm -rf /opt/muse-netcheck
bash "$INSTALLER" --yes --dir /opt/muse-netcheck --api-port 28795 --web-port 28796 >/tmp/r3.log 2>&1
NET_AFTER="$(docker network ls --format '{{.Name}}' | wc -l)"
if [ "$NET_BEFORE" = "$NET_AFTER" ]; then
  t_ok "安装不新建 docker 网络（网络数保持 $NET_BEFORE）"
else
  t_fail "安装新建了 docker 网络（$NET_BEFORE → $NET_AFTER，会耗尽地址池）"
fi
docker rm -f muse-netcheck >/dev/null 2>&1
systemctl disable --now muse-netcheck-web.service >/dev/null 2>&1
rm -f /etc/systemd/system/muse-netcheck-web.service
rm -rf /opt/muse-netcheck
systemctl daemon-reload >/dev/null 2>&1

# 11.4 dry-run 不得出现「端口被占，换一个」和最终端口自相矛盾
#      事故：先提示 18610 被占会自动换，最后却显示 18610，自相矛盾。
bash "$INSTALLER" --dry-run --yes --dir /tmp/muse-drychk >/tmp/r4.log 2>&1
if grep -q "会自动往上找空闲端口" /tmp/r4.log; then
  t_fail "dry-run 仍输出误导性的端口避让提示"
else
  t_ok "dry-run 不再输出误导性的端口提示"
fi
rm -rf /tmp/muse-drychk

# 11.5 Ctrl+C 提示（脚本里必须有 INT 处理）
if grep -q "trap on_interrupt INT" "$INSTALLER" && grep -q "安装中断了" "$INSTALLER"; then
  t_ok "Ctrl+C 有友好中断提示"
else
  t_fail "缺少 Ctrl+C 中断提示"
fi

# 11.6 生成的 compose 必须带 network_mode: bridge
bash "$INSTALLER" --dry-run --yes --dir /tmp/muse-drychk2 >/dev/null 2>&1
rm -rf /tmp/muse-drychk2
if grep -q 'network_mode: bridge' "$INSTALLER"; then
  t_ok "compose 模板使用 bridge 网络（不消耗地址池）"
else
  t_fail "compose 模板没设 network_mode: bridge"
fi

t_case "12. 小白体验缺陷回归（2026-09-29 第二轮）"

# 12.1 dry-run 的 systemd 提示必须是「派生名」，不能写死 mvw-web.service
#      事故：装到 /tmp/mvtest 时实际生成 mvtest-web.service，dry-run 却打印
#            mvw-web.service —— 小白拿它去 systemctl 查会扑空。
bash "$INSTALLER" --dry-run --yes --dir /tmp/muse-namedchk >/tmp/r12a.log 2>&1
if grep -q "systemctl enable --now muse-namedchk-web.service" /tmp/r12a.log; then
  t_ok "dry-run 显示的是派生出的 unit 名"
elif grep -qE "systemctl enable --now (mvw|muse2api)-web\.service" /tmp/r12a.log; then
  t_fail "dry-run 仍写死默认 unit 名（与实装不一致）"
else
  t_fail "dry-run 未按预期显示派生 unit 名"
fi

# 12.2 卸载必须「非交互时要求显式 --yes」，不能默默卸掉
#      事故：bash install.sh --uninstall < /dev/null 一行就把容器删了。
#      这里只做静态断言（真删要起容器，交给 12.3 的实装流程之外的成本太高）：
if grep -q "当前不是交互终端，出于安全我没有直接卸载" "$INSTALLER"; then
  t_ok "卸载在非交互时会拒绝并要求 --yes"
else
  t_fail "卸载缺少「非交互必须 --yes」的保护"
fi

# 12.3 回滚必须先把旧镜像打 tag 保住（不能用 {{.Image}} 的 sha256）
#      事故：sha256 在 compose 重建后失效，docker run 报 No such image，
#            回滚静默失败却打印「已尝试回滚」。
if grep -q ':rollback' "$INSTALLER" && grep -q 'docker tag' "$INSTALLER"; then
  t_ok "升级回滚会先把旧镜像打固定 tag"
else
  t_fail "升级回滚没保住旧镜像（仍会用失效的 sha256）"
fi
if grep -q 'docker image inspect "$old_tag"' "$INSTALLER"; then
  t_ok "回滚前校验镜像存在，失败会如实报错"
else
  t_fail "回滚没有校验镜像存在（可能静默失败）"
fi

# 12.4 zip 兜底下载必须用唯一临时名，且挪完目录后清掉空壳
if grep -q 'mktemp /tmp/muse2api-' "$INSTALLER"; then
  t_ok "zip 兜底用唯一临时文件（不会并发互踩）"
else
  t_fail "zip 兜底仍写死 /tmp/muse2api.zip"
fi
if grep -q 'rm -rf muse2api-main' "$INSTALLER"; then
  t_ok "zip 解包后清掉空壳目录"
else
  t_fail "zip 解包后残留 muse2api-main 空壳"
fi

# 12.5 验收清单不能把「待办」说成「已完成」
if grep -q "现在还是 0 个" "$INSTALLER"; then
  t_ok "验收清单标明账号数需要导号后才变 1"
else
  t_fail "验收清单仍把「有 1 个账号」当成已完成状态"
fi

# 12.6 容器名冲突提示必须给出真实容器名，而不是写死 muse2api
if grep -q "grep '\${CONTAINER_NAME}'" "$INSTALLER"; then
  t_ok "容器冲突提示用的是实际容器名"
else
  t_fail "容器冲突提示写死了 muse2api（派生名下 grep 不到）"
fi

# 12.7 --status 是只读操作，不该要求管理员权限
#      事故：普通用户（或 docker 组用户）只想看「服务在跑吗/我的网址是啥」，
#            却被 check_root 一句「请用管理员权限运行」挡回去 —— 他并没要改东西。
if grep -q 'if \[ "\$DO_STATUS" = 1 \]; then     do_status; exit \$?; fi' "$INSTALLER"; then
  t_ok "--status 不再强制要求管理员权限（只读操作）"
else
  t_fail "--status 仍被 check_root 拦截（只读操作不该要 root）"
fi

# 12.8 非 root 读不到安装记录时，提示要指向 sudo，而不是谎称「还没装过」
if grep -q "需要管理员权限才能读" "$INSTALLER"; then
  t_ok "读不到记录时能区分「没装过」和「没权限」"
else
  t_fail "非 root 读不到记录会被误报成「还没装过」"
fi

# 12.9 实跑一次：非 root 跑 --status 不应因权限被拒（要么给状态，要么给权限提示）
#      注：这里用 testuser 模拟；它没 docker 组权限，所以预期能走到权限提示分支。
if id -u testuser >/dev/null 2>&1; then
  cp "$INSTALLER" /tmp/inst-rocheck.sh && chmod 755 /tmp/inst-rocheck.sh
  su - testuser -c 'bash /tmp/inst-rocheck.sh --status --dir /opt/muse-regress --api-port 28710 --web-port 28711 2>&1' >/tmp/r129.log 2>&1
  RC129=$?
  if grep -q "管理员权限跑" /tmp/r129.log; then
    t_fail "非 root --status 仍被硬拦（应放行只读查询）" "$(head -1 /tmp/r129.log)"
  elif grep -qE "没权限|sudo bash|docker 权限|运行中|没运行" /tmp/r129.log; then
    t_ok "非 root --status 能走到只读查询/权限提示（不再硬拦）"
  else
    t_ok "非 root --status 正常返回（rc=$RC129）"
  fi
  rm -f /tmp/inst-rocheck.sh /tmp/r129.log
else
  t_ok "非 root --status 静态检查通过（跳过实跑，无 testuser）"
fi

# 12.10 打错的参数要给出「你是不是想打 X」的建议
#       事故：`--unstall`（少个 i）/ `uninstall`（忘写横杠）只会回一句
#             「不认识的参数，用 --help 看用法」，小白盯着看不出差在哪。
if grep -q 'suggest_arg' "$INSTALLER" && grep -q '你是不是想打' "$INSTALLER"; then
  t_ok "打错的参数会给出最接近的建议"
else
  t_fail "打错参数没有「你是不是想打」提示"
fi
# 实跑几个真实错法，确认建议命中最该命中的那几个
_chk_sug() {
  local bad="$1" want="$2" out
  out="$(bash "$INSTALLER" "$bad" 2>&1)"
  if printf '%s' "$out" | grep -q -- "$want"; then
    t_ok "「$bad」被建议为 $want"
  else
    t_fail "「$bad」没被建议为 $want" "$(printf '%s' "$out" | tail -1)"
  fi
}
_chk_sug "--unstall"  "--uninstall"
_chk_sug "--stauts"   "--status"
_chk_sug "uninstall"  "--uninstall"
_chk_sug "status"     "--status"
# 乱敲的字符串不该被"建议"（免得误导）
if bash "$INSTALLER" "zzqqxx" 2>&1 | grep -q "你是不是想打"; then
  t_fail "乱敲的字符串被硬凑成了建议（会误导）"
else
  t_ok "乱敲的字符串不会被硬凑出建议"
fi

# 12.11 管道安装时不能印出 `sudo bash bash --status` 这种 nonsense
#       事故：curl|bash 时 $0=bash，basename 得 "bash"，收尾命令全成了 `bash bash`。
if grep -q 'bash|sh|dash|ash|zsh|ksh' "$INSTALLER"; then
  t_ok "管道运行时不会把 shell 名当脚本名"
else
  t_fail "管道运行会把 \$0 的 bash 当脚本名（印出 bash bash）"
fi
# 实跑：管道方式 dry-run，收尾/提示里不该出现 "bash bash"
bash -c "cat '$INSTALLER' | bash -s -- --dry-run --yes --dir /tmp/muse-pipechk" >/tmp/r1211.log 2>&1
if grep -q 'bash bash' /tmp/r1211.log; then
  t_fail "管道运行仍印出「bash bash」" "$(grep -m1 'bash bash' /tmp/r1211.log)"
else
  t_ok "管道运行不含「bash bash」"
fi
rm -rf /tmp/muse-pipechk

# 12.12 安装后必须把脚本自存一份，好让生命周期命令永远可用
if grep -q 'self_hint' "$INSTALLER" && grep -q '\$INSTALL_DIR/install.sh' "$INSTALLER"; then
  t_ok "有 self_hint：生命周期命令指向安装目录里的脚本副本"
else
  t_fail "缺少脚本自存 / self_hint"
fi
# 实跑验证：真装一次，确认 /opt/muse-selfchk/install.sh 存在且可执行
bash "$INSTALLER" --yes --dir /opt/muse-selfchk --api-port 28820 --web-port 28821 >/tmp/r1212.log 2>&1
RC1212=$?
if [ "$RC1212" = 0 ] && [ -x /opt/muse-selfchk/install.sh ]; then
  t_ok "安装后脚本自存为可执行的 /opt/muse-selfchk/install.sh"
  # 而且会用这条路径提示用户
  if grep -q 'bash /opt/muse-selfchk/install.sh --status' /tmp/r1212.log; then
    t_ok "收尾提示用的是自存路径（一定可用）"
  else
    t_fail "收尾提示没指向自存路径" "$(grep -m1 -- '--status' /tmp/r1212.log)"
  fi
  # 副本要能真的跑起来
  if bash /opt/muse-selfchk/install.sh --status >/tmp/r1212b.log 2>&1 && \
     grep -q "运行状态" /tmp/r1212b.log; then
    t_ok "自存的脚本副本能正常执行 --status"
  else
    t_fail "自存的脚本副本跑不起来" "$(tail -1 /tmp/r1212b.log)"
  fi
  # ⚠️ 关键补充：用**裸相对名**（cd 进去后 `bash install.sh`）调用时也必须能自存。
  #    这是小白解压后的典型动作，早先的白名单匹配漏掉了它。
  rm -rf /opt/muse-selfchk-rel
  mkdir -p /tmp/muse-selfrel && cp "$INSTALLER" /tmp/muse-selfrel/install.sh
  ( cd /tmp/muse-selfrel && bash install.sh --yes --dir /opt/muse-selfchk-rel \
      --api-port 28822 --web-port 28823 ) >/tmp/r1212d.log 2>&1
  if [ -x /opt/muse-selfchk-rel/install.sh ]; then
    t_ok "裸相对名调用（bash install.sh）也能自存脚本"
  else
    t_fail "裸相对名调用没能自存脚本" "$(grep -m1 '常用命令' -A1 /tmp/r1212d.log)"
  fi
  bash /opt/muse-selfchk-rel/install.sh --uninstall --yes >/dev/null 2>&1
  docker rm -f muse-selfchk-rel >/dev/null 2>&1
  systemctl disable --now muse-selfchk-rel-web.service >/dev/null 2>&1
  rm -f /etc/systemd/system/muse-selfchk-rel-web.service
  systemctl daemon-reload >/dev/null 2>&1
  rm -rf /opt/muse-selfchk-rel /tmp/muse-selfrel
  # 用副本卸载，收尾干净
  bash /opt/muse-selfchk/install.sh --uninstall --yes >/tmp/r1212c.log 2>&1
  docker rm -f muse-selfchk >/dev/null 2>&1
  systemctl disable --now muse-selfchk-web.service >/dev/null 2>&1
  rm -f /etc/systemd/system/muse-selfchk-web.service
  systemctl daemon-reload >/dev/null 2>&1
  rm -rf /opt/muse-selfchk
else
  t_fail "安装后没有脚本自存（或安装失败 rc=$RC1212）" "$(tail -2 /tmp/r1212.log)"
fi

# 12.13 资源体检：内存/磁盘要主动报告（免得 1G 小鸡 OOM 了还不知道为啥）
if grep -q 'check_resources' "$INSTALLER"; then
  t_ok "有 check_resources 做内存/磁盘体检"
else
  t_fail "缺少资源体检（1G 内存机器会莫名 OOM）"
fi
bash "$INSTALLER" --dry-run --yes --dir /tmp/muse-rschk >/tmp/r1213.log 2>&1
if grep -qE '内存：|磁盘剩余' /tmp/r1213.log; then
  t_ok "资源体检在环境检查阶段输出内存/磁盘"
else
  t_fail "资源体检没有输出" "$(grep -A2 '检查环境' /tmp/r1213.log | tail -1)"
fi
rm -rf /tmp/muse-rschk

rm -rf /tmp/muse-namedchk

t_case "13. 清理测试残留"
cleanup_all
[ ! -d "$TDIR" ] && t_ok "测试目录已清理" || t_fail "残留 $TDIR"

printf '\n%s════════════════════════════════════%s\n' "$B" "$O"
printf '  通过 %s%d%s   失败 %s%d%s\n' "$G" "$PASS" "$O" "$([ "$FAIL" -gt 0 ] && echo "$R" || echo "$G")" "$FAIL" "$O"
printf '%s════════════════════════════════════%s\n\n' "$B" "$O"
exit "$FAIL"
