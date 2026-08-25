#!/usr/bin/env python3
"""
Web UI 啟動 smoke test（issues #213 / #217 / #221 / #228）

Starlette 新版要求 TemplateResponse(request, name, context=...)。舊的
呼叫順序會讓頁面直接 500（TypeError: unhashable type: 'dict'），使用者
以 uvx @latest 安裝後完全無法使用。

這些測試守住兩件事：
1. 首頁與回饋頁能實際 render 出 HTML，不是 500。
2. feedback.html 需要的 session_id 有被放進 template context。
"""

import pytest
from fastapi.testclient import TestClient


class TestWebUIRendering:
    """頁面必須能真正 render"""

    @pytest.mark.asyncio
    async def test_index_renders_without_session(self, web_ui_manager):
        """無活躍會話時顯示等待頁面"""
        client = TestClient(web_ui_manager.app)

        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert len(response.text) > 0

    @pytest.mark.asyncio
    async def test_feedback_page_renders_with_session(
        self, web_ui_manager, test_project_dir
    ):
        """有活躍會話時顯示回饋頁面"""
        web_ui_manager.create_session(str(test_project_dir), "測試摘要")
        client = TestClient(web_ui_manager.app)

        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_feedback_page_receives_session_id(
        self, web_ui_manager, test_project_dir
    ):
        """session_id 必須進入 template context（前端 FeedbackApp 初始化需要）"""
        session_id = web_ui_manager.create_session(str(test_project_dir), "測試摘要")
        client = TestClient(web_ui_manager.app)

        response = client.get("/")

        assert response.status_code == 200
        # 模板會把 session_id 寫入 data-full-id 與 FeedbackApp 建構參數
        assert session_id in response.text, (
            "session_id 未出現在頁面中，template context 缺少 session_id"
        )
        assert "loading" not in response.text.split("data-full-id=")[1][:20], (
            "session_id 未正確帶入 data-full-id"
        )


class TestSessionApi:
    """會話 API 必須反映目前狀態"""

    @pytest.mark.asyncio
    async def test_current_session_api_without_session(self, web_ui_manager):
        client = TestClient(web_ui_manager.app)

        response = client.get("/api/current-session")

        assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_current_session_api_reports_no_command_logs(
        self, web_ui_manager, test_project_dir
    ):
        """command_logs 已隨命令執行功能移除，API 不應再回傳該欄位"""
        web_ui_manager.create_session(str(test_project_dir), "測試摘要")
        client = TestClient(web_ui_manager.app)

        response = client.get("/api/current-session")

        assert response.status_code == 200
        assert "command_logs" not in response.json()
