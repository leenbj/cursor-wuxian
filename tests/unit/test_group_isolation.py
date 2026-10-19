#!/usr/bin/env python3
"""同窗口多對話隔離（分組隔離）

一個 Cursor 窗口只拉起一個 MCP 行程，窗口內並行的多個對話共用同一個
WebUIManager。舊版 create_session 會把前一個對話的會話清掉並頂替，還會把
它視窗的 WebSocket 轉給新會話：A 的視窗顯示 B 的內容，使用者的回覆串到
錯的對話。

這裡守住：每個分組（目錄 + 標題）一個獨立管理器，
1. 並行對話互不頂替、不搶視窗連線；續調以 session_id 為準
2. 單一對話的行為不變（同一個管理器、新會話接替舊會話）
3. 額外管理器的端口互不相同，且不會走「殺掉佔用端口的 MCP 行程」的清理路徑
4. 對話結束後釋放分組，管理器被後續對話復用
5. 提交回饋後只關自己對話的桌面視窗
6. 桌面視窗連的是自己分組的服務
"""

import os
import socket
from unittest.mock import AsyncMock

import pytest

from mcp_feedback_enhanced.web import main as web_main
from mcp_feedback_enhanced.web.main import WebUIManager
from mcp_feedback_enhanced.web.models.feedback_session import WebFeedbackSession
from mcp_feedback_enhanced.web.utils.port_manager import PortManager


class FakeWebSocket:
    """只需要能收 send_json 的最小 WebSocket 替身"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message):
        self.sent.append(message)


class FakeDesktop:
    """桌面視窗替身：只記錄有沒有被關掉"""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


# 模組導入時抓取真實的 wait_slice，早於任何夾具替身；需要真實等待語義的用例用它還原
REAL_WAIT_SLICE = WebFeedbackSession.wait_slice


def waiting_result(*_args):
    # 每次回新字典：launch_web_feedback_ui 會往結果裡寫 session_id，共用同一個會互相覆蓋
    return {"outcome": "waiting", "interactive_feedback": "", "images": []}


def ended_result(*_args):
    return {"outcome": "ended", "interactive_feedback": "", "images": []}


def extra_managers() -> list[WebUIManager]:
    return list(web_main._extra_managers)


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    """分組註冊表是行程級全域狀態，每個用例從空白開始，避免互相污染"""
    monkeypatch.setattr(web_main, "_group_managers", {}, raising=False)
    monkeypatch.setattr(web_main, "_extra_managers", [], raising=False)


@pytest.fixture
def group_env(web_ui_manager, monkeypatch, tmp_path):
    """不啟動真實伺服器、不開瀏覽器、不真的等回饋。

    參數順序刻意把 web_ui_manager 放在 monkeypatch 前面：teardown 時 monkeypatch
    先還原，web_ui_manager 後還原它自己設的環境變數，不會把 MCP_TEST_MODE 漏到後面的用例。
    """
    monkeypatch.delenv("MCP_DESKTOP_MODE", raising=False)
    monkeypatch.setattr(web_main, "get_web_ui_manager", lambda: web_ui_manager)
    monkeypatch.setattr(WebUIManager, "start_server", lambda self: None)

    async def no_browser(self, url):
        return True

    monkeypatch.setattr(WebUIManager, "smart_open_browser", no_browser)
    monkeypatch.setattr(
        WebFeedbackSession, "wait_slice", AsyncMock(side_effect=waiting_result)
    )

    dirs = []
    for name in ("proj_a", "proj_b", "proj_c"):
        directory = tmp_path / name
        directory.mkdir()
        dirs.append(str(directory))
    return (web_ui_manager, *dirs)


class TestParallelConversationsStayIsolated:
    @pytest.mark.asyncio
    async def test_parallel_conversations_do_not_replace_each_other_or_steal_the_window(
        self, group_env
    ):
        """舊版 bug 的正面復現：B 建會話時把 A 視窗的連線轉給 B，A 的視窗顯示 B 的內容"""
        _, dir_a, dir_b, _ = group_env

        ra = await web_main.launch_web_feedback_ui(dir_a, "A 摘要", title="任務A")
        manager_a = web_main.get_group_manager(dir_a, "任務A")
        session_a = manager_a.get_session(ra["session_id"])
        ws_a = FakeWebSocket()
        session_a.websocket = ws_a  # A 的視窗已連上

        rb = await web_main.launch_web_feedback_ui(dir_b, "B 摘要", title="任務B")
        manager_b = web_main.get_group_manager(dir_b, "任務B")

        assert manager_b is not manager_a, "不同分組必須是不同管理器"
        session_b = manager_b.get_current_session()
        assert session_b.session_id == rb["session_id"]
        assert session_a.websocket is ws_a, "A 視窗的連線不能被 B 搶走"
        assert session_b.websocket is None

        # A 續調：仍是自己的會話，沒被重建；B 也沒被頂替
        ra2 = await web_main.launch_web_feedback_ui(
            dir_a, "A 摘要", title="任務A", session_id=ra["session_id"]
        )
        assert ra2["session_id"] == ra["session_id"]
        assert manager_a.get_current_session() is session_a
        assert manager_b.get_current_session() is session_b

    @pytest.mark.asyncio
    async def test_continuation_with_session_id_routes_to_owner_even_if_title_drifts(
        self, group_env
    ):
        """模型續調時標題漂移、目錄寫錯是常態；session_id 才是對話身分，不能因此多開窗口"""
        _, dir_a, dir_b, _ = group_env
        ra = await web_main.launch_web_feedback_ui(dir_a, "A", title="任務A")
        await web_main.launch_web_feedback_ui(dir_b, "B", title="任務B")
        manager_a = web_main.get_group_manager(dir_a, "任務A")
        manager_b = web_main.get_group_manager(dir_b, "任務B")
        session_a = manager_a.get_current_session()
        session_b = manager_b.get_current_session()

        ra2 = await web_main.launch_web_feedback_ui(
            dir_b, "A", title="漂移後的標題", session_id=ra["session_id"]
        )

        assert ra2["session_id"] == ra["session_id"]
        assert manager_a.get_current_session() is session_a
        assert manager_b.get_current_session() is session_b
        assert extra_managers() == [manager_b], "標題漂移不能多出第三個管理器"

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param(
                ("proj_a", "任務一"), ("proj_a", "任務二"), id="same_dir_other_title"
            ),
            pytest.param(
                ("proj_a", "任務"), ("proj_b", "任務"), id="same_title_other_dir"
            ),
        ],
    )
    def test_group_key_is_directory_plus_title(self, group_env, first, second):
        """同一個倉庫裡開兩個對話（同目錄不同標題）是最常見的並行場景；
        標題相同但目錄不同也是兩個對話。兩個維度缺一不可"""
        _, dir_a, dir_b, _ = group_env
        dirs = {"proj_a": dir_a, "proj_b": dir_b}

        one = web_main.get_group_manager(dirs[first[0]], first[1])
        two = web_main.get_group_manager(dirs[second[0]], second[1])

        assert one is not two
        assert web_main.get_group_manager(dirs[first[0]], first[1]) is one, (
            "同一分組要穩定地回到同一個管理器"
        )

    @pytest.mark.asyncio
    async def test_repeated_reports_of_one_conversation_keep_the_same_manager(
        self, group_env
    ):
        """單一對話行為不變：同一個管理器、新會話接替舊會話（窗口復用邏輯不動）"""
        default, dir_a, _, _ = group_env

        await web_main.launch_web_feedback_ui(dir_a, "第一次", title="任務A")
        second = await web_main.launch_web_feedback_ui(dir_a, "第二次", title="任務A")

        assert web_main.get_group_manager(dir_a, "任務A") is default
        assert extra_managers() == []
        assert default.get_current_session().session_id == second["session_id"]


class TestEndSignalIsNeverLost:
    """真实端到端验证时发现：用户在「兩次輪詢之間」點結束對話，結束信號會被吞掉。

    續調之間有一輪模型往返的延遲（每個 45 秒時間片之後），這段空檔裡沒有輪詢在進行。
    此時會話已是終態，launch_web_feedback_ui 把它當「舊會話」丟掉並新建會話，
    用戶點了結束卻看到新窗口、模型拿到的是 waiting 而不是 ended。
    """

    @pytest.mark.asyncio
    async def test_end_clicked_between_polls_is_delivered_by_the_next_poll(
        self, group_env, monkeypatch
    ):
        default, dir_a, _, _ = group_env
        ra = await web_main.launch_web_feedback_ui(dir_a, "A", title="任務A")
        session_a = default.get_session(ra["session_id"])

        session_a.end_conversation()  # 此刻沒有輪詢在進行
        monkeypatch.setattr(WebFeedbackSession, "wait_slice", REAL_WAIT_SLICE)
        delivered = await web_main.launch_web_feedback_ui(
            dir_a, "A", timeout=1, title="任務A", session_id=ra["session_id"]
        )

        assert delivered["outcome"] == "ended", "結束信號被吞了：模型拿到的不是 ended"
        assert delivered["session_id"] == ra["session_id"]
        assert len(default.sessions) == 1, "不能為了吞掉結束信號而新建會話"

    @pytest.mark.asyncio
    async def test_pending_end_still_routes_to_its_manager_when_title_drifts(
        self, group_env, monkeypatch
    ):
        default, dir_a, dir_b, _ = group_env
        ra = await web_main.launch_web_feedback_ui(dir_a, "A", title="任務A")
        await web_main.launch_web_feedback_ui(dir_b, "B", title="任務B")
        default.get_session(ra["session_id"]).end_conversation()
        monkeypatch.setattr(WebFeedbackSession, "wait_slice", REAL_WAIT_SLICE)

        delivered = await web_main.launch_web_feedback_ui(
            dir_b, "A", timeout=1, title="漂移後的標題", session_id=ra["session_id"]
        )

        assert delivered["outcome"] == "ended"
        assert delivered["session_id"] == ra["session_id"]

    @pytest.mark.asyncio
    async def test_end_signal_is_consumed_once_so_a_later_report_can_start_fresh(
        self, group_env, monkeypatch
    ):
        """交付過一次就算收尾：同一個聊天之後再次回報不會永遠被 ended 擋住"""
        default, dir_a, _, _ = group_env
        ra = await web_main.launch_web_feedback_ui(dir_a, "A", title="任務A")
        default.get_session(ra["session_id"]).end_conversation()
        monkeypatch.setattr(WebFeedbackSession, "wait_slice", REAL_WAIT_SLICE)
        first = await web_main.launch_web_feedback_ui(
            dir_a, "A", timeout=1, title="任務A", session_id=ra["session_id"]
        )
        assert first["outcome"] == "ended"

        monkeypatch.setattr(
            WebFeedbackSession, "wait_slice", AsyncMock(side_effect=waiting_result)
        )
        again = await web_main.launch_web_feedback_ui(
            dir_a, "A", title="任務A", session_id=ra["session_id"]
        )

        assert again["outcome"] == "waiting"
        assert again["session_id"] != ra["session_id"], "第二次應是全新會話"


class TestExtraManagerPorts:
    def test_second_concurrent_group_gets_a_new_manager_without_killing_port_owner(
        self, web_ui_manager, monkeypatch, tmp_path
    ):
        """模擬生產：預設端口被自己的 MCP 行程占著、非測試模式。

        走 WebUIManager() 預設路徑會 auto_cleanup=True，把「占用者」（同一行程裡的
        第一個管理器，命令行含 mcp-feedback-enhanced）當成可清理對象去殺——等於自殺。
        """
        dir_a, dir_b = tmp_path / "a", tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        killed: list[int] = []

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            busy_port = busy.getsockname()[1]

            web_ui_manager.port = busy_port
            monkeypatch.setenv("MCP_WEB_PORT", str(busy_port))
            monkeypatch.delenv("MCP_TEST_MODE", raising=False)
            monkeypatch.setattr(web_main, "get_web_ui_manager", lambda: web_ui_manager)
            monkeypatch.setattr(
                PortManager,
                "find_process_using_port",
                staticmethod(
                    lambda port: {
                        "pid": os.getpid(),
                        "name": "python",
                        "cmdline": "mcp-feedback-enhanced",
                        "create_time": 0,
                        "status": "running",
                    }
                ),
            )
            monkeypatch.setattr(
                PortManager,
                "kill_process_on_port",
                staticmethod(lambda port, force=False: killed.append(port) or False),
            )

            first = web_main.get_group_manager(str(dir_a), "任務A")
            second = web_main.get_group_manager(str(dir_b), "任務B")

        assert first is web_ui_manager, "第一個分組沿用預設管理器（單對話行為不變）"
        assert second is not first
        assert second.port != first.port
        assert killed == [], f"不能對占用端口的行程動手: {killed}"


class TestGroupRelease:
    @pytest.mark.asyncio
    async def test_ended_conversation_releases_its_manager_for_reuse(
        self, group_env, monkeypatch
    ):
        """對話結束後管理器要能被新對話復用，否則端口和窗口數隨對話總數增長"""
        _, dir_a, dir_b, dir_c = group_env
        await web_main.launch_web_feedback_ui(dir_a, "A", title="任務A")
        rb = await web_main.launch_web_feedback_ui(dir_b, "B", title="任務B")
        manager_b = web_main.get_group_manager(dir_b, "任務B")
        assert extra_managers() == [manager_b]

        monkeypatch.setattr(
            WebFeedbackSession, "wait_slice", AsyncMock(side_effect=ended_result)
        )
        rb2 = await web_main.launch_web_feedback_ui(
            dir_b, "B", title="任務B", session_id=rb["session_id"]
        )
        assert rb2["outcome"] == "ended"

        monkeypatch.setattr(
            WebFeedbackSession, "wait_slice", AsyncMock(side_effect=waiting_result)
        )
        await web_main.launch_web_feedback_ui(dir_c, "C", title="任務C")

        assert web_main.get_group_manager(dir_c, "任務C") is manager_b
        assert extra_managers() == [manager_b], "沒有新建第二個額外管理器"

    @pytest.mark.asyncio
    async def test_stale_session_id_of_an_ended_conversation_cannot_hijack_a_reused_manager(
        self, group_env, monkeypatch
    ):
        """已結束對話的模型若又帶舊 session_id 調用：不能被路由進已被別的對話復用的管理器，
        否則會替換掉那個對話的當前會話——正是要防的串線"""
        _, dir_a, dir_b, dir_c = group_env
        await web_main.launch_web_feedback_ui(dir_a, "A", title="任務A")
        rb = await web_main.launch_web_feedback_ui(dir_b, "B", title="任務B")
        manager_b = web_main.get_group_manager(dir_b, "任務B")

        # B 點了結束對話：等待端以 ended 返回，會話進入終態，分組被釋放
        monkeypatch.setattr(
            WebFeedbackSession, "wait_slice", AsyncMock(side_effect=ended_result)
        )
        await web_main.launch_web_feedback_ui(
            dir_b, "B", title="任務B", session_id=rb["session_id"]
        )
        manager_b.get_session(rb["session_id"]).end_conversation()
        monkeypatch.setattr(
            WebFeedbackSession, "wait_slice", AsyncMock(side_effect=waiting_result)
        )

        # C 復用了 B 釋放出來的管理器
        await web_main.launch_web_feedback_ui(dir_c, "C", title="任務C")
        assert web_main.get_group_manager(dir_c, "任務C") is manager_b
        session_c = manager_b.get_current_session()

        # B 的模型不聽話，又拿舊 session_id 調用
        await web_main.launch_web_feedback_ui(
            dir_b, "B", title="任務B", session_id=rb["session_id"]
        )

        assert manager_b.get_current_session() is session_c, (
            "C 的會話被已結束的 B 頂替了"
        )

    def test_stop_web_ui_stops_every_manager_and_clears_the_registry(
        self, group_env, monkeypatch
    ):
        default, dir_a, dir_b, _ = group_env
        monkeypatch.setattr(web_main, "_web_ui_manager", default)
        stopped: list[WebUIManager] = []

        def fake_stop(self):
            stopped.append(self)

        monkeypatch.setattr(WebUIManager, "stop", fake_stop)
        web_main.get_group_manager(dir_a, "任務A")
        manager_b = web_main.get_group_manager(dir_b, "任務B")

        web_main.stop_web_ui()

        assert {id(m) for m in stopped} == {id(default), id(manager_b)}
        assert extra_managers() == []
        assert web_main._group_managers == {}


class TestDesktopWindowIsolation:
    @pytest.mark.asyncio
    async def test_submitting_feedback_closes_only_its_own_desktop_window(
        self, group_env, monkeypatch
    ):
        """舊版提交後關窗用全域預設管理器：B 提交回饋，關掉的卻是 A 的窗口"""
        _, dir_a, dir_b, _ = group_env
        manager_a = web_main.get_group_manager(dir_a, "任務A")
        manager_b = web_main.get_group_manager(dir_b, "任務B")
        desktop_a, desktop_b = FakeDesktop(), FakeDesktop()
        manager_a.desktop_app_instance = desktop_a
        manager_b.desktop_app_instance = desktop_b
        manager_b.create_session(dir_b, "B 摘要", title="任務B")
        session_b = manager_b.get_current_session()
        session_b.websocket = FakeWebSocket()
        monkeypatch.setenv("MCP_DESKTOP_MODE", "true")

        await session_b.submit_feedback("好的", [], {})

        assert desktop_b.stopped, "B 提交後應關掉 B 自己的窗口"
        assert not desktop_a.stopped, "A 的窗口不能被 B 的提交關掉"

    @pytest.mark.asyncio
    async def test_manager_hands_itself_and_the_title_to_the_desktop_factory(
        self, group_env, monkeypatch
    ):
        """工廠不知道該連哪個管理器就會退回全域預設：窗口連到別人的服務"""
        _, dir_a, dir_b, _ = group_env
        web_main.get_group_manager(dir_a, "任務A")
        manager_b = web_main.get_group_manager(dir_b, "任務B")
        calls: list[dict] = []

        class FakeApp:
            app_handle = None

        async def fake_factory(*args, **kwargs):
            calls.append(kwargs)
            return FakeApp()

        monkeypatch.setattr(
            "mcp_feedback_enhanced.desktop_app.launch_desktop_app", fake_factory
        )

        assert await manager_b.launch_desktop_app(manager_b.get_server_url(), "任務B")

        assert calls[0].get("web_manager") is manager_b
        assert calls[0].get("window_title") == "任務B"

    @pytest.mark.asyncio
    async def test_desktop_app_connects_to_the_injected_manager_not_the_global_default(
        self, group_env, monkeypatch
    ):
        from mcp_feedback_enhanced.desktop_app import desktop_app as desktop_module

        _, dir_a, dir_b, _ = group_env
        web_main.get_group_manager(dir_a, "任務A")
        manager_b = web_main.get_group_manager(dir_b, "任務B")

        class AliveThread:
            def is_alive(self):
                return True

        manager_b.server_thread = AliveThread()  # 該分組的服務已在跑
        monkeypatch.setenv(
            "MCP_DESKTOP_MODE", "true"
        )  # start_web_backend 會設它，讓它能被還原

        def forbidden():
            pytest.fail("注入了管理器就不該再去取全域預設管理器")

        monkeypatch.setattr(desktop_module, "get_web_ui_manager", forbidden)

        app = desktop_module.DesktopApp(web_manager=manager_b, window_title="任務B")
        url = await app.start_web_backend()

        assert app.web_manager is manager_b
        assert url == manager_b.get_server_url()
        assert app.window_title == "任務B"
