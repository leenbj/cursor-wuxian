"""install.sh 一键配置脚本测试。

意图：装完仓库后，Cursor 侧所有配置必须一步到位，且绝不破坏用户已有配置：
- mcp.json 条目（autoApprove 必须含 interactive_feedback，否则每次调用都会在对话里弹审批，
  而对话内的任何交互都会计费，违背「所有交互走 MCP 窗口」的初衷）
- 强制等待规则（alwaysApply，防止模型在 MCP 窗口无回复时提前结束对话）
- 已有 ui_settings.json 时关闭自动提交/会话超时（它们会在无人回复时自动结束等待）
全部使用临时 HOME 与 SKIP_TOOL_INSTALL=1，不联网、不碰真实配置。
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "install.sh"


def run_install(home: Path, **extra_env: str) -> subprocess.CompletedProcess:
    # PATH 只放一个指向当前解释器的 python3 垫片 + 系统基础工具：
    # 避免被开发机/虚拟环境里真实存在的 mcp-feedback-enhanced 可执行文件影响断言
    shim = home / "_shim"
    shim.mkdir(exist_ok=True)
    if not (shim / "python3").exists():
        (shim / "python3").symlink_to(sys.executable)
    env = {
        "PATH": f"{shim}:/usr/bin:/bin",
        "HOME": str(home),
        "SKIP_TOOL_INSTALL": "1",
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
    )


def mcp_json(home: Path) -> Path:
    return home / ".cursor" / "mcp.json"


def rule_file(home: Path) -> Path:
    return home / ".cursor" / "rules" / "mcp-feedback-enhanced.mdc"


def ui_settings(home: Path) -> Path:
    return home / ".config" / "mcp-feedback-enhanced" / "ui_settings.json"


def backups(path: Path) -> list:
    return sorted(path.parent.glob(path.name + ".bak-*"))


class TestFreshMachine:
    def test_writes_mcp_entry_and_rule_without_touching_settings(self, tmp_path):
        r = run_install(tmp_path)
        assert r.returncode == 0, r.stderr

        entry = json.loads(mcp_json(tmp_path).read_text("utf-8"))["mcpServers"][
            "mcp-feedback-enhanced"
        ]
        assert entry["command"] == str(tmp_path / ".local" / "bin" / "mcp-feedback-enhanced")
        assert entry["timeout"] == 86400
        assert entry["env"]["MCP_DESKTOP_MODE"] == "true"
        assert entry["env"]["MCP_WEB_PORT"] == "8765"
        # 不预授权的话，每次调用都会在对话内弹审批 -> 触发对话内计费交互
        assert "interactive_feedback" in entry["autoApprove"]

        rule = rule_file(tmp_path).read_text("utf-8")
        assert "alwaysApply: true" in rule
        assert "禁止提前终止对话" in rule
        assert "title" in rule  # 单窗口 + 标题自适配的约束

        # 默认值本就是关闭，不应凭空创建用户设置文件
        assert not ui_settings(tmp_path).exists()

    def test_port_can_be_overridden_for_parallel_tasks(self, tmp_path):
        r = run_install(tmp_path, MCP_WEB_PORT="8899")
        assert r.returncode == 0, r.stderr
        entry = json.loads(mcp_json(tmp_path).read_text("utf-8"))["mcpServers"][
            "mcp-feedback-enhanced"
        ]
        assert entry["env"]["MCP_WEB_PORT"] == "8899"


class TestExistingUserConfig:
    def test_other_servers_and_personal_settings_survive(self, tmp_path):
        mcp_json(tmp_path).parent.mkdir(parents=True)
        mcp_json(tmp_path).write_text(
            json.dumps({"mcpServers": {"gitnexus": {"command": "gitnexus"}}, "other": 1}),
            "utf-8",
        )
        ui_settings(tmp_path).parent.mkdir(parents=True)
        ui_settings(tmp_path).write_text(
            json.dumps(
                {
                    "autoSubmitEnabled": True,
                    "sessionTimeoutEnabled": True,
                    "promptSettings": {"prompts": [{"name": "继续任务"}]},
                    "language": "zh-CN",
                }
            ),
            "utf-8",
        )

        r = run_install(tmp_path)
        assert r.returncode == 0, r.stderr

        data = json.loads(mcp_json(tmp_path).read_text("utf-8"))
        assert data["mcpServers"]["gitnexus"] == {"command": "gitnexus"}
        assert data["other"] == 1
        assert "mcp-feedback-enhanced" in data["mcpServers"]
        assert len(backups(mcp_json(tmp_path))) == 1  # 改动前留有备份

        settings = json.loads(ui_settings(tmp_path).read_text("utf-8"))
        # 这两项会在无人回复时自动提交/结束等待，与「必须等用户回复」冲突
        assert settings["autoSubmitEnabled"] is False
        assert settings["sessionTimeoutEnabled"] is False
        assert settings["promptSettings"] == {"prompts": [{"name": "继续任务"}]}
        assert settings["language"] == "zh-CN"

    def test_invalid_mcp_json_is_never_overwritten(self, tmp_path):
        mcp_json(tmp_path).parent.mkdir(parents=True)
        mcp_json(tmp_path).write_text("{not json", "utf-8")

        r = run_install(tmp_path)

        assert r.returncode != 0
        assert "mcp.json" in r.stderr
        assert mcp_json(tmp_path).read_text("utf-8") == "{not json"


class TestIdempotent:
    def test_second_run_changes_nothing_and_adds_no_backup(self, tmp_path):
        mcp_json(tmp_path).parent.mkdir(parents=True)
        mcp_json(tmp_path).write_text(json.dumps({"mcpServers": {}}), "utf-8")

        assert run_install(tmp_path).returncode == 0
        first_mcp = mcp_json(tmp_path).read_text("utf-8")
        first_rule = rule_file(tmp_path).read_text("utf-8")
        # 备份名只精确到秒，同一秒内重复写会同名覆盖，所以不能只比备份列表；
        # 用 mtime_ns 判断“文件根本没被重写”，这才是幂等的真实含义
        first_mtimes = (mcp_json(tmp_path).stat().st_mtime_ns, rule_file(tmp_path).stat().st_mtime_ns)
        first_backups = backups(mcp_json(tmp_path)) + backups(rule_file(tmp_path))

        assert run_install(tmp_path).returncode == 0

        assert mcp_json(tmp_path).read_text("utf-8") == first_mcp
        assert rule_file(tmp_path).read_text("utf-8") == first_rule
        assert (
            mcp_json(tmp_path).stat().st_mtime_ns,
            rule_file(tmp_path).stat().st_mtime_ns,
        ) == first_mtimes, "内容未变时不应重写配置文件"
        # 重复运行不能堆积备份文件
        assert backups(mcp_json(tmp_path)) + backups(rule_file(tmp_path)) == first_backups
