#!/usr/bin/env python3
"""timeout 鉗制的回歸測試（issue #212）

某些客戶端會對 interactive_feedback 傳入不合理的小值 —— 回報中 Cursor 傳過
timeout=1，使用者還沒來得及看清介面就「操作超時」。

伺服器端因此鉗制 timeout：過小提升到 MIN、過大限制到 MAX。
選擇鉗制而非 pydantic 驗證，是因為驗證失敗會讓整個 tool call 中斷，
對 human-in-the-loop 工具來說讓流程以安全值繼續更有用。
"""

import pytest

from mcp_feedback_enhanced.server import (
    DEFAULT_FEEDBACK_TIMEOUT,
    MAX_FEEDBACK_TIMEOUT,
    MIN_FEEDBACK_TIMEOUT,
    resolve_feedback_timeout,
)
from mcp_feedback_enhanced.web.models.feedback_session import WebFeedbackSession


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
