#!/usr/bin/env python3
"""
WebSocket Origin 驗證的回歸測試

對應兩份私下回報的 advisory：
- GHSA-cmr5-gpm3-79vf (critical) — Unauthenticated WebSocket Command Execution via CSWSH
- GHSA-2wx7-r4rh-f663 (high)     — CSWSH to RCE via missing Origin validation

瀏覽器開啟 WebSocket 不受 same-origin policy 限制，因此惡意網頁可以讓使用者的
瀏覽器連上 ws://127.0.0.1:<port>/ws。v2.6.1 已移除命令執行，切斷了 RCE 路徑，
但連線本身仍需擋下 —— 否則攻擊者仍可讀取會話內容、替使用者提交回饋、
或覆蓋合法前端的連線。

這些測試守住 Origin 驗證。若有人移除檢查，測試必須失敗。
"""

import pytest

from mcp_feedback_enhanced.web.routes.main_routes import is_allowed_origin


HOST = "127.0.0.1"
PORT = 8765


class TestRejectsCrossOrigin:
    """跨站來源必須被拒絕"""

    @pytest.mark.parametrize(
        "origin",
        [
            "http://evil.example",  # GHSA-2wx7 PoC 使用的來源
            "https://evil-attacker.com",  # GHSA-2wx7 PoC 使用的來源
            "http://evil.example:8765",  # 埠相符但主機不符
            "https://attacker.com",
            "http://127.0.0.1.evil.com:8765",  # 前綴混淆
            "http://localhost.evil.com:8765",
        ],
    )
    def test_foreign_origin_rejected(self, origin):
        assert is_allowed_origin(origin, HOST, PORT) is False, (
            f"{origin} 必須被拒絕，否則惡意網頁可劫持本機 WebSocket"
        )

    def test_different_port_rejected(self):
        """同主機但不同埠也應拒絕（可能是另一個本機服務）"""
        assert is_allowed_origin("http://127.0.0.1:9999", HOST, PORT) is False

    @pytest.mark.parametrize("origin", ["ws://127.0.0.1:8765", "file://", "null"])
    def test_non_http_scheme_rejected(self, origin):
        assert is_allowed_origin(origin, HOST, PORT) is False


class TestAllowsLegitimateOrigin:
    """正常使用情境必須放行"""

    @pytest.mark.parametrize(
        "origin",
        [
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "https://127.0.0.1:8765",
        ],
    )
    def test_loopback_origin_allowed(self, origin):
        assert is_allowed_origin(origin, HOST, PORT) is True

    def test_missing_origin_allowed(self):
        """非瀏覽器客戶端（桌面應用、CLI）不會帶 Origin"""
        assert is_allowed_origin("", HOST, PORT) is True

    @pytest.mark.parametrize(
        "origin",
        ["tauri://localhost", "https://tauri.localhost", "http://tauri.localhost"],
    )
    def test_desktop_webview_origin_allowed(self, origin):
        """Tauri WebView 依平台使用不同的自訂 scheme"""
        assert is_allowed_origin(origin, HOST, PORT) is True

    def test_matching_bound_host_allowed(self):
        """綁定到具體 LAN 位址時，該位址自身應被允許"""
        assert (
            is_allowed_origin("http://192.168.1.50:8765", "192.168.1.50", PORT) is True
        )

    def test_wildcard_bind_accepts_matching_port(self):
        """綁定 0.0.0.0 時無法預知對外位址，退為以埠相符為準

        這是使用者明確選擇對外開放時的行為（README 已標示不建議），
        仍能擋掉埠不符的惡意來源。
        """
        assert is_allowed_origin("http://192.168.1.50:8765", "0.0.0.0", PORT) is True  # noqa: S104
        assert is_allowed_origin("http://evil.example:9999", "0.0.0.0", PORT) is False  # noqa: S104


class TestEndpointEnforcesCheck:
    """端點本身必須實際執行檢查，而非只有 helper 存在"""

    def test_endpoint_validates_before_accept(self):
        """Origin 檢查必須在 websocket.accept() 之前

        若順序顛倒，連線已被接受才拒絕，攻擊者仍可能取得一次訊息往返。
        """
        import inspect

        from mcp_feedback_enhanced.web.routes import main_routes

        source = inspect.getsource(main_routes.setup_routes)
        ws_section = source.split('@manager.app.websocket("/ws")')[1]

        check_pos = ws_section.find("is_allowed_origin")
        accept_pos = ws_section.find("websocket.accept()")

        assert check_pos != -1, "WebSocket 端點必須呼叫 is_allowed_origin"
        assert accept_pos != -1, "找不到 websocket.accept()"
        assert check_pos < accept_pos, "Origin 檢查必須在 accept() 之前"
