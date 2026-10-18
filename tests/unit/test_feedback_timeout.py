#!/usr/bin/env python3
"""timeout 鉗制的回歸測試（issue #212）

某些客戶端會對 interactive_feedback 傳入不合理的小值 —— 回報中 Cursor 傳過
timeout=1，使用者還沒來得及看清介面就「操作超時」。

伺服器端因此鉗制 timeout：過小提升到 MIN、過大限制到 MAX。
選擇鉗制而非 pydantic 驗證，是因為驗證失敗會讓整個 tool call 中斷，
對 human-in-the-loop 工具來說讓流程以安全值繼續更有用。
"""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from mcp_feedback_enhanced import server
from mcp_feedback_enhanced.server import (
    DEFAULT_FEEDBACK_TIMEOUT,
    MAX_FEEDBACK_TIMEOUT,
    MIN_FEEDBACK_TIMEOUT,
    resolve_feedback_timeout,
)
from mcp_feedback_enhanced.web.models import feedback_session
from mcp_feedback_enhanced.web.models.feedback_session import (
    SessionStatus,
    WebFeedbackSession,
)


class TestTimeoutBounds:
    """邊界常數必須自洽"""

    def test_bounds_are_ordered(self):
        assert MIN_FEEDBACK_TIMEOUT < DEFAULT_FEEDBACK_TIMEOUT < MAX_FEEDBACK_TIMEOUT

    def test_minimum_allows_real_interaction(self):
        """下限必須足夠讓人類實際閱讀摘要並回覆"""
        assert MIN_FEEDBACK_TIMEOUT >= 30


class TestResolveFeedbackTimeout:
    """鉗制邏輯"""

    @pytest.mark.parametrize("bad", [1, 5, 30, 59])
    def test_too_small_is_raised_to_minimum(self, bad):
        """#212 回報的 timeout=1 必須被提升"""
        assert resolve_feedback_timeout(bad) == MIN_FEEDBACK_TIMEOUT

    @pytest.mark.parametrize("value", [60, 600, 3600, 86400])
    def test_valid_values_pass_through(self, value):
        assert resolve_feedback_timeout(value) == value

    def test_too_large_is_capped(self):
        assert resolve_feedback_timeout(999_999) == MAX_FEEDBACK_TIMEOUT

    @pytest.mark.parametrize("bad", [0, -1, -600])
    def test_non_positive_is_raised_to_minimum(self, bad):
        assert resolve_feedback_timeout(bad) == MIN_FEEDBACK_TIMEOUT

    @pytest.mark.parametrize("bad", [None, "abc", [], {}])
    def test_unparseable_falls_back_to_default(self, bad):
        """壞型別不應讓 tool call 爆掉"""
        assert resolve_feedback_timeout(bad) == DEFAULT_FEEDBACK_TIMEOUT

    def test_numeric_string_is_accepted(self):
        """有些客戶端會把數字包成字串"""
        assert resolve_feedback_timeout("600") == 600

    def test_float_is_truncated(self):
        assert resolve_feedback_timeout(600.9) == 600


class TestWaitTimeoutMargin:
    """wait_for_feedback 的提前結束邏輯不得反而延長等待

    舊版對 timeout<=30 使用 max(timeout - 1, 5)，在 timeout=1 時會等 5 秒 ——
    比呼叫端要求的還久，與「提前結束避免競爭」的本意相反。
    """

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            (1, 1),  # 不得延長
            (2, 1),
            (10, 9),
            (30, 29),
            (31, 26),
            (600, 595),
            (3600, 3595),
        ],
    )
    def test_margin_never_exceeds_requested(self, requested, expected):
        margin = 5 if requested > 30 else 1
        actual = max(requested - margin, 1)

        assert actual == expected
        assert actual <= requested, "實際等待時間不得超過呼叫端要求的值"

    @pytest.mark.asyncio
    async def test_short_timeout_returns_promptly(self, test_project_dir):
        """直接以極短 timeout 呼叫時，必須在該時間內結束而非等更久"""
        import time

        session = WebFeedbackSession("t-timeout", str(test_project_dir), "摘要")
        try:
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                await session.wait_for_feedback(timeout=2)
            elapsed = time.monotonic() - start

            assert elapsed < 5, f"等待了 {elapsed:.1f} 秒，超過呼叫端要求的 2 秒"
        finally:
            session._cleanup_sync()


class TestNoResponseTellsClientToKeepWaiting:
    """沒有回饋時必須回傳「繼續等待」指令，嚴禁誘導結束對話。

    舊版（#125）在無回饋時指示「視為使用者離開、直接結束任務」，導致
    用戶稍作停頓對話就被提前收尾。現行協議改為分片等待：未收到用戶
    明確回饋或「結束對話」信號前，不得終止對話。
    """

    @pytest.mark.asyncio
    async def test_timeout_returns_keep_waiting_instruction(self, monkeypatch):
        async def fake_launch(*_args, **_kwargs):
            raise TimeoutError("等待用戶回饋超時（595秒），介面已自動關閉")

        monkeypatch.setattr(server, "launch_web_feedback_ui", fake_launch)
        tool_fn = getattr(
            server.interactive_feedback, "fn", server.interactive_feedback
        )

        result = await tool_fn(project_directory=".", summary="摘要", timeout=600)

        text = result[0].text
        assert "嚴禁終止對話" in text
        assert "再次呼叫 interactive_feedback" in text, "必須指示續等而非結束"
        assert "595" in text, "原因必須帶入，讓使用者事後能從對話看出發生了什麼"
        assert "不要再次呼叫 interactive_feedback" not in text, "不得再指示停止呼叫"

    @pytest.mark.asyncio
    async def test_timeout_after_feedback_arrived_is_not_no_response(self, monkeypatch):
        """取得回饋之後的 TimeoutError（如存檔 I/O 的 ETIMEDOUT）是真錯誤，不能把回饋說成沒回應"""

        async def fake_launch(*_args, **_kwargs):
            return {"interactive_feedback": "真實回饋", "images": [], "settings": {}}

        def fake_save(_result):
            raise TimeoutError("storage timed out")

        monkeypatch.setattr(server, "launch_web_feedback_ui", fake_launch)
        monkeypatch.setattr(server, "save_feedback_to_file", fake_save)
        tool_fn = getattr(
            server.interactive_feedback, "fn", server.interactive_feedback
        )

        result = await tool_fn(project_directory=".", summary="摘要", timeout=600)

        assert "使用者未回應" not in result[0].text

    def test_tool_description_requires_waiting_until_feedback_or_end(self):
        """工具描述必須寫明強制等待協議：waiting 續調、ended 才可結束"""
        doc = server.interactive_feedback.__doc__ or ""

        assert "[FEEDBACK_WAITING]" in doc, "描述必須包含仍在等待的返回標記"
        assert "session_id" in doc, "描述必須要求續調時帶回 session_id"
        assert "[CONVERSATION_ENDED]" in doc, "描述必須包含正式結束信號"
        assert "do NOT call this tool again" not in doc, (
            "不得再把無回應描述為可停止呼叫的條件"
        )


class TestWaitingProtocolOutcomes:
    """分片等待三態：waiting 續等標記 / ended 結束信號 / feedback 原樣回傳"""

    @pytest.mark.asyncio
    async def test_waiting_outcome_returns_keep_waiting_marker(self, monkeypatch):
        async def fake_launch(*_args, **_kwargs):
            return {
                "outcome": "waiting",
                "session_id": "sess-123",
                "title": "任務甲",
                "interactive_feedback": "",
                "images": [],
            }

        monkeypatch.setattr(server, "launch_web_feedback_ui", fake_launch)
        tool_fn = getattr(
            server.interactive_feedback, "fn", server.interactive_feedback
        )

        result = await tool_fn(project_directory=".", summary="摘要", timeout=600)

        text = result[0].text
        assert text.startswith("[FEEDBACK_WAITING]")
        assert "session_id=sess-123" in text
        assert "嚴禁終止對話" in text

    @pytest.mark.asyncio
    async def test_ended_outcome_returns_end_signal(self, monkeypatch):
        async def fake_launch(*_args, **_kwargs):
            return {
                "outcome": "ended",
                "session_id": "sess-123",
                "interactive_feedback": "",
                "images": [],
            }

        monkeypatch.setattr(server, "launch_web_feedback_ui", fake_launch)
        tool_fn = getattr(
            server.interactive_feedback, "fn", server.interactive_feedback
        )

        result = await tool_fn(project_directory=".", summary="摘要", timeout=600)

        text = result[0].text
        assert text.startswith("[CONVERSATION_ENDED]")
        assert "結束對話" in text

    @pytest.mark.asyncio
    async def test_end_conversation_marks_session_and_wakes_waiter(self, test_project_dir):
        """點擊結束對話：會話標記 ended 並喚醒等待端（與關窗嚴格區分）"""
        session = WebFeedbackSession("t-end", str(test_project_dir), "摘要")
        try:
            session.end_conversation()

            assert session.ended_by_user
            assert session.status is SessionStatus.ENDED

            result = await session.wait_slice(1)
            assert result["outcome"] == "ended"
        finally:
            session._cleanup_sync()


class TestClientDisconnectGrace:
    """關閉視窗不等於結束對話：誤關後自動重新拉起，會話保持等待。

    舊行為（#162）是寬限到期即把等待判成超時結束；新需求明確要求
    只有點「結束對話」按鈕才結束，誤關必須把單一視窗拉回來。
    """

    @pytest.fixture
    def session(self, monkeypatch, test_project_dir):
        monkeypatch.setattr(feedback_session, "CLIENT_DISCONNECT_GRACE_SECONDS", 0.2)
        s = WebFeedbackSession("t-disconnect", str(test_project_dir), "摘要")
        yield s
        s._cleanup_sync()

    @pytest.mark.asyncio
    async def test_disconnect_without_reconnect_relaunches_and_keeps_waiting(
        self, session
    ):
        relaunched = threading.Event()
        session.on_window_lost = relaunched.set

        session.on_client_disconnected()

        assert relaunched.wait(2), "寬限到期必須觸發自動重新拉起"
        assert not session.feedback_completed.is_set(), "關窗不得結束等待"
        assert session.status is SessionStatus.WAITING

        result = await session.wait_slice(1)
        assert result["outcome"] == "waiting"

    @pytest.mark.asyncio
    async def test_feedback_arriving_after_relaunch_window_wins(self, session):
        """視窗被誤關並重拉後使用者送出回饋：等待端必須拿到回饋"""
        relaunched = threading.Event()
        session.on_window_lost = relaunched.set
        session.on_client_disconnected()
        assert relaunched.wait(2), "寬限回呼未到期"

        await session.submit_feedback("使用者剛好送出的回饋", [], {})

        result = await session.wait_slice(1)

        assert result["outcome"] == "feedback"
        assert result["interactive_feedback"] == "使用者剛好送出的回饋"

    @pytest.mark.asyncio
    async def test_timer_firing_during_submit_never_returns_partial_feedback(
        self, session
    ):
        """計時器在提交寫到一半（圖片還在處理）時到期：等待端不得拿到半套回饋

        寬限計時器要先斷線才會啟動、建立也要取同一把 lock，所以真正能與提交
        交錯的是使用者設定的會話超時計時器（不依賴連線狀態）。Web UI 的提交在
        獨立執行緒的 event loop 上執行，這裡照樣以另一個執行緒提交。
        """
        in_images, release = threading.Event(), threading.Event()
        image = {"name": "shot.png", "data": b"x", "size": 1}

        def slow_process(_images):
            in_images.set()
            release.wait(3)  # 持鎖等到主執行緒確認計時器已到期才放行
            return [image]

        session._process_images = slow_process
        submit = threading.Thread(
            target=lambda: asyncio.run(
                session.submit_feedback("附圖回饋", [image], {})
            ),
            daemon=True,
        )
        submit.start()
        assert in_images.wait(3), "提交未進入圖片處理"
        # 提交已持鎖、圖片處理中：啟動計時器並等它到期（回呼此時只能卡在等鎖）
        session.update_timeout_settings(enabled=True, timeout_seconds=0.05)
        await asyncio.sleep(0.3)
        assert not session.feedback_completed.is_set(), "回呼不得在提交持鎖期間寫入"
        release.set()

        result = await session.wait_for_feedback(timeout=60)
        submit.join(3)

        assert result["interactive_feedback"] == "附圖回饋"
        assert result["images"] == [image], "圖片不得因計時器插入而遺失"

    @pytest.mark.asyncio
    async def test_reconnect_within_grace_keeps_waiting(self, session):
        session.on_client_disconnected()
        session.on_client_connected()  # 模擬 F5 或短暫斷線後重連

        await asyncio.sleep(0.4)

        assert not session.feedback_completed.is_set()
        assert session.status is SessionStatus.WAITING

    def test_disconnect_after_feedback_is_noop(self, session):
        session.feedback_completed.set()

        session.on_client_disconnected()

        assert session.disconnect_timer is None


class TestWebSocketLifecycleWiring:
    """/ws 端點接線：登記的連線斷開才啟動寬限，誤關觸發重拉且不結束等待"""

    @pytest.fixture
    def wired(self, monkeypatch, web_ui_manager, test_project_dir):
        monkeypatch.setattr(feedback_session, "CLIENT_DISCONNECT_GRACE_SECONDS", 0.2)
        web_ui_manager.create_session(str(test_project_dir), "摘要")
        session = web_ui_manager.get_current_session()
        client = TestClient(web_ui_manager.app)
        origin = {"origin": f"http://{web_ui_manager.host}:{web_ui_manager.port}"}
        yield session, client, origin
        session._cleanup_sync()

    def test_ws_disconnect_relaunches_and_keeps_waiting_after_grace(self, wired):
        session, client, origin = wired
        relaunched = threading.Event()
        session.on_window_lost = relaunched.set

        with client.websocket_connect("/ws", headers=origin) as ws:
            ws.receive_json()  # connection_established

        assert relaunched.wait(2), "登記的連線斷開後，寬限到期必須觸發重新拉起"
        assert not session.feedback_completed.is_set(), "關窗不得結束等待"
        assert session.status is SessionStatus.WAITING

    def test_ws_reconnect_within_grace_keeps_waiting(self, wired):
        session, client, origin = wired

        with client.websocket_connect("/ws", headers=origin) as ws:
            ws.receive_json()
        with client.websocket_connect("/ws", headers=origin) as ws:
            ws.receive_json()
            time.sleep(0.4)
            assert not session.feedback_completed.is_set(), "重連後寬限必須被取消"
