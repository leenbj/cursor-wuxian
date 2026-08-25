#!/usr/bin/env python3
"""
命令執行移除的回歸測試（issue #219）

v2.6.1 移除了透過 WebSocket 觸發本機程序的能力。原本的攻擊路徑是：
未認證的 /ws 連線 → run_command 訊息 → subprocess.Popen。

這些測試守住「WebSocket 無法啟動任何程序」這個安全邊界，
若有人重新引入命令執行功能，測試必須失敗。
"""

import inspect

import pytest

from mcp_feedback_enhanced.web.models.feedback_session import WebFeedbackSession
from mcp_feedback_enhanced.web.routes import main_routes


class TestCommandExecutionRemoved:
    """確認命令執行相關的 API 已不存在"""

    def test_session_has_no_command_execution_api(self):
        """WebFeedbackSession 不得再提供命令執行或命令日誌介面"""
        for attr in ("run_command", "add_log", "command_logs", "process"):
            assert not hasattr(WebFeedbackSession, attr), (
                f"WebFeedbackSession.{attr} 已被移除，不應重新引入"
            )

    def test_session_instance_has_no_process_state(self, test_project_dir):
        """實例層級也不得保留程序/命令日誌狀態"""
        session = WebFeedbackSession(
            "test-no-command", str(test_project_dir), "測試摘要"
        )
        try:
            for attr in ("process", "command_logs"):
                assert not hasattr(session, attr), (
                    f"session.{attr} 已被移除，不應重新引入"
                )
        finally:
            session._cleanup_sync()

    def test_module_exposes_no_command_parser(self):
        """blocklist 式命令解析器已移除（曾可被 cat/curl/python 繞過）"""
        module = inspect.getmodule(WebFeedbackSession)
        assert module is not None
        assert not hasattr(module, "_safe_parse_command"), (
            "_safe_parse_command 是可繞過的 blocklist，不應重新引入"
        )

    def test_session_module_does_not_import_subprocess(self):
        """session 模組不應再需要 subprocess/shlex"""
        module = inspect.getmodule(WebFeedbackSession)
        assert module is not None
        for name in ("subprocess", "shlex"):
            assert not hasattr(module, name), f"feedback_session 不應再匯入 {name}"


class TestWebSocketMessageHandler:
    """確認 WebSocket 訊息處理器不接受命令"""

    @pytest.mark.asyncio
    async def test_run_command_message_is_not_handled(self, test_project_dir):
        """run_command 訊息必須落入未知類型，不得觸發任何執行路徑"""
        session = WebFeedbackSession(
            "test-ws-command", str(test_project_dir), "測試摘要"
        )
        try:
            # 不應拋出 AttributeError（代表仍嘗試呼叫 session.run_command）
            await main_routes.handle_websocket_message(
                None,  # type: ignore[arg-type]  # 未知訊息類型不會用到 manager
                session,
                {"type": "run_command", "command": "whoami"},
            )
        finally:
            session._cleanup_sync()

    def test_handler_source_has_no_command_branch(self):
        """處理器原始碼不得包含 run_command 分支"""
        source = inspect.getsource(main_routes.handle_websocket_message)
        assert "run_command" not in source, (
            "handle_websocket_message 不應再有 run_command 分支"
        )
