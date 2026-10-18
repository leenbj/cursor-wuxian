#!/usr/bin/env bash
# cursor-wuxian 一键安装与配置（Cursor）
#
#   curl -fsSL https://raw.githubusercontent.com/leenbj/cursor-wuxian/main/install.sh | bash
#   或在仓库目录内：  bash install.sh
#
# 幂等，可重复运行；只合并、不覆盖你已有的其它配置（改动前自动留 .bak-时间戳 备份）：
#   1. uv tool install 本仓库，得到 mcp-feedback-enhanced 可执行文件
#   2. 合并 ~/.cursor/mcp.json 的 mcp-feedback-enhanced 条目
#   3. 写入强制等待规则 ~/.cursor/rules/mcp-feedback-enhanced.mdc（alwaysApply）
#   4. 若已有 ui_settings.json，则关闭「自动提交」「会话超时」（二者会在无人回复时自动结束等待）
#
# 可选环境变量：
#   MCP_WEB_PORT=8765          Web UI 端口（并行多任务时，每个项目用不同端口以隔离）
#   REPO_SPEC=git+https://github.com/leenbj/cursor-wuxian   安装来源
#   SKIP_TOOL_INSTALL=1        只写配置、不安装工具（已安装或测试时使用）
set -euo pipefail

REPO_SPEC="${REPO_SPEC:-git+https://github.com/leenbj/cursor-wuxian}"
MCP_WEB_PORT="${MCP_WEB_PORT:-8765}"
CURSOR_DIR="$HOME/.cursor"
SETTINGS_FILE="$HOME/.config/mcp-feedback-enhanced/ui_settings.json"
RULE_FILE="$CURSOR_DIR/rules/mcp-feedback-enhanced.mdc"
STAMP="$(date +%Y%m%d-%H%M%S)"

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "需要 python3（用于安全合并 JSON 配置）"

# ---------- 1. 安装工具 ----------
if [ "${SKIP_TOOL_INSTALL:-}" != "1" ]; then
  command -v uv >/dev/null 2>&1 || die "未找到 uv，请先安装：https://docs.astral.sh/uv/"
  src="$REPO_SPEC"
  # 在仓库目录内运行时优先装本地副本（免网络）
  self="${BASH_SOURCE[0]:-}"
  if [ -n "$self" ] && [ -f "$self" ]; then
    here="$(cd "$(dirname "$self")" && pwd)"
    if [ -f "$here/pyproject.toml" ] && grep -q '^name = "mcp-feedback-enhanced"' "$here/pyproject.toml"; then
      src="$here"
    fi
  fi
  say "安装工具：$src"
  UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}" uv tool install --force "$src"
fi

# 写进 mcp.json 的可执行文件：优先 uv 实际安装的那份。
# 不能直接取 PATH 上第一个同名文件——排在前面的旧副本（如上游旧版）会让用户装完仍是没有修复的老版本，且毫无报错
BIN=""
if command -v uv >/dev/null 2>&1; then
  UV_BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
  if [ -n "$UV_BIN_DIR" ] && [ -x "$UV_BIN_DIR/mcp-feedback-enhanced" ]; then
    BIN="$UV_BIN_DIR/mcp-feedback-enhanced"
  fi
fi
if [ -z "$BIN" ] && [ -x "$HOME/.local/bin/mcp-feedback-enhanced" ]; then
  BIN="$HOME/.local/bin/mcp-feedback-enhanced"
fi
if [ -z "$BIN" ]; then
  BIN="$(command -v mcp-feedback-enhanced 2>/dev/null || true)"
fi
if [ -z "$BIN" ]; then
  BIN="$HOME/.local/bin/mcp-feedback-enhanced"  # 默认位置（SKIP_TOOL_INSTALL 场景下可能尚不存在）
fi
if [ "${SKIP_TOOL_INSTALL:-}" != "1" ] && [ ! -x "$BIN" ]; then
  die "安装后仍找不到可执行文件：$BIN（请确认 uv tool 的 bin 目录在 PATH 中：uv tool update-shell）"
fi

# ---------- 2. 合并 mcp.json ----------
python3 - "$CURSOR_DIR/mcp.json" "$BIN" "$MCP_WEB_PORT" "$STAMP" <<'PY'
import json
import os
import shutil
import sys

path, command, port, stamp = sys.argv[1:5]
entry = {
    "command": command,
    "args": [],
    "timeout": 86400,
    "env": {
        "MCP_DESKTOP_MODE": "true",
        "MCP_WEB_HOST": "127.0.0.1",
        "MCP_WEB_PORT": port,
        "MCP_LANGUAGE": "zh-CN",
        "MCP_DEBUG": "false",
    },
    # 不预授权，每次调用都会在对话内弹审批；对话内交互会计费，违背「全部走 MCP 窗口」
    "autoApprove": ["interactive_feedback"],
}

data = {}
existed = os.path.exists(path)
if existed:
    raw = open(path, encoding="utf-8").read()
    if raw.strip():
        try:
            data = json.loads(raw)
        except ValueError as exc:
            sys.exit("拒绝覆盖：%s 不是合法 JSON（%s），请先修复后重试" % (path, exc))
    if not isinstance(data, dict):
        sys.exit("拒绝覆盖：%s 顶层不是 JSON 对象，请先修复后重试" % path)

servers = data.setdefault("mcpServers", {})
if servers.get("mcp-feedback-enhanced") == entry:
    print("mcp.json 已是最新，无需改动")
    sys.exit(0)

if existed:
    shutil.copy2(path, "%s.bak-%s" % (path, stamp))
servers["mcp-feedback-enhanced"] = entry
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("已写入 mcp.json 的 mcp-feedback-enhanced 条目：%s" % path)
PY

# ---------- 3. 写入强制等待规则 ----------
mkdir -p "$(dirname "$RULE_FILE")"
RULE_TMP="$(mktemp)"
trap 'rm -f "$RULE_TMP"' EXIT
cat > "$RULE_TMP" <<'RULE'
---
description: MCP Interactive Feedback 强制等待规则（系统级）
globs: ["*"]
alwaysApply: true
---

# MCP Interactive Feedback 强制规则

与用户的一切确认、征求意见与反馈收集，必须通过 `mcp-feedback-enhanced` 的 `interactive_feedback` 工具（桌面窗口）完成，并严格遵守：

1. **禁止提前终止对话**：在收到用户通过该 MCP 窗口发出的明确反馈内容之前，严禁终止对话、输出“最终总结”收尾、或自行判定任务结束。工具返回的超时、无响应、连接中断等通用文案**不是终止理由**，不得据此结束对话。
2. **必须等到有效回复才推进**：单次工具调用返回“仍在等待/无响应”状态时，必须按工具返回的指引立即再次调用同一工具续接等待（同一轮内静默衔接，不在对话中输出中间文本），直到：①收到用户的明确反馈；②收到工具返回的“用户已点击结束对话按钮”的正式结束信号。用户彻底关闭窗口视为其明确信号，可据此终止或继续。
3. **只用该 MCP 交互**：不得使用对话内选择组件、Elicitation 或在原生输入框中罗列选项征求用户决定（宿主强制的安全审批除外）。所有需要用户拍板的内容写入该 MCP 的窗口说明中，请用户在窗口内以文字回复。
4. **单一窗口**：一个对话只挂起一个反馈窗口；不要反复新开窗口。每次调用必须传入当前任务名作为窗口标题（title 参数），由系统自动适配显示，不要求用户干预。
5. **根据反馈调整**：收到非空反馈后按反馈继续执行，并再次调用该工具等待下一步；只有用户明确表示结束、或点击“结束对话”按钮发出正式结束信号时，才可输出最终总结终止对话。
6. **图片反馈处理**：用户在窗口上传图片并提交后，必须读取工具返回的图片文件路径进行视觉识别与分析，再据此完成后续任务。
RULE
if [ -f "$RULE_FILE" ] && cmp -s "$RULE_TMP" "$RULE_FILE"; then
  say "规则文件已是最新，无需改动"
else
  if [ -f "$RULE_FILE" ]; then
    cp "$RULE_FILE" "$RULE_FILE.bak-$STAMP"
  fi
  cp "$RULE_TMP" "$RULE_FILE"
  say "已写入强制等待规则：$RULE_FILE"
fi

# ---------- 4. 关闭会在无人回复时自动结束等待的设置 ----------
python3 - "$SETTINGS_FILE" "$STAMP" <<'PY'
import json
import os
import shutil
import sys

path, stamp = sys.argv[1:3]
if not os.path.exists(path):
    print("未发现 ui_settings.json，沿用默认值（自动提交/会话超时默认关闭）")
    sys.exit(0)
try:
    data = json.load(open(path, encoding="utf-8"))
except ValueError as exc:
    print("跳过：%s 不是合法 JSON（%s），未修改" % (path, exc), file=sys.stderr)
    sys.exit(0)

changed = False
for key in ("autoSubmitEnabled", "sessionTimeoutEnabled"):
    if data.get(key) is True:
        data[key] = False
        changed = True
if not changed:
    print("ui_settings.json 无需改动")
    sys.exit(0)

shutil.copy2(path, "%s.bak-%s" % (path, stamp))
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("已关闭「自动提交」与「会话超时」：%s" % path)
PY

say ""
say "完成。请在 Cursor 的 MCP 设置面板确认 mcp-feedback-enhanced 已启用（必要时重载窗口）。"
say "并行多任务：每个项目的 .cursor/mcp.json 使用不同的 MCP_WEB_PORT 与工作目录名，避免互相抢端口。"
