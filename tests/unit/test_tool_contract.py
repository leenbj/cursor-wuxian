#!/usr/bin/env python3
"""MCP 工具回傳契約的回歸測試（相關：issue #234）

`interactive_feedback` 回傳的是 MCP content blocks。若把回傳註解寫成模糊的
`list`，FastMCP 無法判斷內容型別，只能生成一個 wrap-result 的 outputSchema
（`{"result": {"type": "array"}}` 加上 `x-fastmcp-wrap-result`），並把結果同時
包成 structuredContent。

宣告精確型別後 FastMCP 會正確識別為 content blocks，不再生成該 schema。
這些測試守住那個註解，避免有人改回 `-> list`。
"""

import inspect
import typing

from mcp.types import ImageContent, TextContent

from mcp_feedback_enhanced import server


class TestInteractiveFeedbackReturnType:
    """回傳註解必須精確描述 content blocks"""

    def _annotation(self):
        fn = server.interactive_feedback
        # FastMCP 會包裝工具函式，取回原始 callable
        fn = getattr(fn, "fn", getattr(fn, "__wrapped__", fn))
        return typing.get_type_hints(fn).get("return")

    def test_return_annotation_is_not_bare_list(self):
        ann = self._annotation()

        assert ann is not list, (
            "回傳註解不可是裸 list —— 那會讓 FastMCP 生成 wrap-result outputSchema"
        )

    def test_return_annotation_is_list_of_content_blocks(self):
        ann = self._annotation()

        assert typing.get_origin(ann) is list, f"預期 list[...]，實際為 {ann}"

        (inner,) = typing.get_args(ann)
        members = set(typing.get_args(inner)) or {inner}

        assert TextContent in members, "必須能回傳 TextContent"
        assert ImageContent in members, "必須能回傳 ImageContent"


class TestReturnPathsAreContentBlocks:
    """所有 return 敘述都必須產生 content blocks，而非裸值"""

    def test_every_return_uses_content_types(self):
        source = inspect.getsource(
            getattr(
                server.interactive_feedback,
                "fn",
                getattr(
                    server.interactive_feedback,
                    "__wrapped__",
                    server.interactive_feedback,
                ),
            )
        )

        returns = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith("return ")
        ]

        assert returns, "找不到任何 return 敘述"
        for stmt in returns:
            assert "TextContent" in stmt or "feedback_items" in stmt, (
                f"回傳敘述未使用 content block: {stmt}"
            )
