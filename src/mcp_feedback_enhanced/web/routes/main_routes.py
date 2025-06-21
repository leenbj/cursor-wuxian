#!/usr/bin/env python3
"""
主要路由處理
============

設置 Web UI 的主要路由和處理邏輯。
"""

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ... import __version__
from ...debug import web_debug_log as debug_log


if TYPE_CHECKING:
    from ..main import WebUIManager


def load_user_layout_settings() -> str:
    """載入用戶的佈局模式設定"""
    try:
        # 使用統一的設定檔案路徑
        config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
        settings_file = config_dir / "ui_settings.json"

        if settings_file.exists():
            with open(settings_file, encoding="utf-8") as f:
                settings = json.load(f)
                layout_mode = settings.get("layoutMode", "combined-vertical")
                debug_log(f"從設定檔案載入佈局模式: {layout_mode}")
                # 修復 no-any-return 錯誤 - 確保返回 str 類型
                return str(layout_mode)
        else:
            debug_log("設定檔案不存在，使用預設佈局模式: combined-vertical")
            return "combined-vertical"
    except Exception as e:
        debug_log(f"載入佈局設定失敗: {e}，使用預設佈局模式: combined-vertical")
        return "combined-vertical"


def setup_routes(manager: "WebUIManager"):
    """設置路由"""

    @manager.app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """統一回饋頁面 - 重構後的主頁面"""
        # 獲取當前活躍會話
        current_session = manager.get_current_session()

        if not current_session:
            # 沒有活躍會話時顯示等待頁面
            return manager.templates.TemplateResponse(
                "index.html",
                {
                    "request": request,
                    "title": "MCP Feedback Enhanced",
                    "has_session": False,
                    "version": __version__,
                },
            )

        # 有活躍會話時顯示回饋頁面
        # 載入用戶的佈局模式設定
        layout_mode = load_user_layout_settings()

        return manager.templates.TemplateResponse(
            "feedback.html",
            {
                "request": request,
                "project_directory": current_session.project_directory,
                "summary": current_session.summary,
                "title": "Interactive Feedback - 回饋收集",
                "version": __version__,
                "has_session": True,
                "layout_mode": layout_mode,
                "i18n": manager.i18n,
            },
        )

    @manager.app.get("/api/translations")
    async def get_translations():
        """獲取翻譯數據 - 從 Web 專用翻譯檔案載入"""
        translations = {}

        # 獲取 Web 翻譯檔案目錄
        web_locales_dir = Path(__file__).parent.parent / "locales"
        supported_languages = ["zh-TW", "zh-CN", "en"]

        for lang_code in supported_languages:
            lang_dir = web_locales_dir / lang_code
            translation_file = lang_dir / "translation.json"

            try:
                if translation_file.exists():
                    with open(translation_file, encoding="utf-8") as f:
                        lang_data = json.load(f)
                        translations[lang_code] = lang_data
                        debug_log(f"成功載入 Web 翻譯: {lang_code}")
                else:
                    debug_log(f"Web 翻譯檔案不存在: {translation_file}")
                    translations[lang_code] = {}
            except Exception as e:
                debug_log(f"載入 Web 翻譯檔案失敗 {lang_code}: {e}")
                translations[lang_code] = {}

        debug_log(f"Web 翻譯 API 返回 {len(translations)} 種語言的數據")
        return JSONResponse(content=translations)

    @manager.app.get("/api/session-status")
    async def get_session_status():
        """獲取當前會話狀態"""
        current_session = manager.get_current_session()

        if not current_session:
            return JSONResponse(
                content={
                    "has_session": False,
                    "status": "no_session",
                    "message": "沒有活躍會話",
                }
            )

        return JSONResponse(
            content={
                "has_session": True,
                "status": "active",
                "session_info": {
                    "project_directory": current_session.project_directory,
                    "summary": current_session.summary,
                    "feedback_completed": current_session.feedback_completed.is_set(),
                },
            }
        )

    @manager.app.get("/api/current-session")
    async def get_current_session():
        """獲取當前會話詳細信息"""
        current_session = manager.get_current_session()

        if not current_session:
            return JSONResponse(status_code=404, content={"error": "沒有活躍會話"})

        return JSONResponse(
            content={
                "session_id": current_session.session_id,
                "project_directory": current_session.project_directory,
                "summary": current_session.summary,
                "feedback_completed": current_session.feedback_completed.is_set(),
                "command_logs": current_session.command_logs,
                "images_count": len(current_session.images),
            }
        )

    @manager.app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket 端點 - 重構後移除 session_id 依賴"""
        # 獲取當前活躍會話
        session = manager.get_current_session()
        if not session:
            await websocket.close(code=4004, reason="沒有活躍會話")
            return

        await websocket.accept()

        # 檢查會話是否已有 WebSocket 連接
        if session.websocket and session.websocket != websocket:
            debug_log("會話已有 WebSocket 連接，替換為新連接")

        session.websocket = websocket
        debug_log(f"WebSocket 連接建立: 當前活躍會話 {session.session_id}")

        # 發送連接成功消息
        try:
            await websocket.send_json(
                {"type": "connection_established", "message": "WebSocket 連接已建立"}
            )

            # 檢查是否有待發送的會話更新
            if getattr(manager, "_pending_session_update", False):
                debug_log("檢測到待發送的會話更新，準備發送通知")
                await websocket.send_json(
                    {
                        "type": "session_updated",
                        "message": "新會話已創建，正在更新頁面內容",
                        "session_info": {
                            "project_directory": session.project_directory,
                            "summary": session.summary,
                            "session_id": session.session_id,
                        },
                    }
                )
                manager._pending_session_update = False
                debug_log("✅ 已發送會話更新通知到前端")
            else:
                # 發送當前會話狀態
                await websocket.send_json(
                    {"type": "status_update", "status_info": session.get_status_info()}
                )
                debug_log("已發送當前會話狀態到前端")

        except Exception as e:
            debug_log(f"發送連接確認失敗: {e}")

        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)

                # 重新獲取當前會話，以防會話已切換
                current_session = manager.get_current_session()
                if current_session and current_session.websocket == websocket:
                    await handle_websocket_message(manager, current_session, message)
                else:
                    debug_log("會話已切換或 WebSocket 連接不匹配，忽略消息")
                    break

        except WebSocketDisconnect:
            debug_log("WebSocket 連接正常斷開")
        except ConnectionResetError:
            debug_log("WebSocket 連接被重置")
        except Exception as e:
            debug_log(f"WebSocket 錯誤: {e}")
        finally:
            # 安全清理 WebSocket 連接
            current_session = manager.get_current_session()
            if current_session and current_session.websocket == websocket:
                current_session.websocket = None
                debug_log("已清理會話中的 WebSocket 連接")

    @manager.app.post("/api/save-settings")
    async def save_settings(request: Request):
        """保存設定到檔案"""
        try:
            data = await request.json()

            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_file = config_dir / "ui_settings.json"

            # 保存設定到檔案
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            debug_log(f"設定已保存到: {settings_file}")

            return JSONResponse(content={"status": "success", "message": "設定已保存"})

        except Exception as e:
            debug_log(f"保存設定失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"保存失敗: {e!s}"},
            )

    @manager.app.get("/api/load-settings")
    async def load_settings():
        """從檔案載入設定"""
        try:
            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            settings_file = config_dir / "ui_settings.json"

            if settings_file.exists():
                with open(settings_file, encoding="utf-8") as f:
                    settings = json.load(f)

                debug_log(f"設定已從檔案載入: {settings_file}")
                return JSONResponse(content=settings)
            debug_log("設定檔案不存在，返回空設定")
            return JSONResponse(content={})

        except Exception as e:
            debug_log(f"載入設定失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"載入失敗: {e!s}"},
            )

    @manager.app.post("/api/clear-settings")
    async def clear_settings():
        """清除設定檔案"""
        try:
            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            settings_file = config_dir / "ui_settings.json"

            if settings_file.exists():
                settings_file.unlink()
                debug_log(f"設定檔案已刪除: {settings_file}")
            else:
                debug_log("設定檔案不存在，無需刪除")

            return JSONResponse(content={"status": "success", "message": "設定已清除"})

        except Exception as e:
            debug_log(f"清除設定失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"清除失敗: {e!s}"},
            )

    @manager.app.get("/api/load-session-history")
    async def load_session_history():
        """從檔案載入會話歷史"""
        try:
            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            history_file = config_dir / "session_history.json"

            if history_file.exists():
                with open(history_file, encoding="utf-8") as f:
                    history_data = json.load(f)

                debug_log(f"會話歷史已從檔案載入: {history_file}")

                # 確保資料格式相容性
                if isinstance(history_data, dict):
                    # 新格式：包含版本資訊和其他元資料
                    sessions = history_data.get("sessions", [])
                    last_cleanup = history_data.get("lastCleanup", 0)
                else:
                    # 舊格式：直接是會話陣列（向後相容）
                    sessions = history_data if isinstance(history_data, list) else []
                    last_cleanup = 0

                # 回傳會話歷史資料
                return JSONResponse(
                    content={"sessions": sessions, "lastCleanup": last_cleanup}
                )

            debug_log("會話歷史檔案不存在，返回空歷史")
            return JSONResponse(content={"sessions": [], "lastCleanup": 0})

        except Exception as e:
            debug_log(f"載入會話歷史失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"載入失敗: {e!s}"},
            )

    @manager.app.post("/api/save-session-history")
    async def save_session_history(request: Request):
        """保存會話歷史到檔案"""
        try:
            data = await request.json()

            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            config_dir.mkdir(parents=True, exist_ok=True)
            history_file = config_dir / "session_history.json"

            # 建立新格式的資料結構
            history_data = {
                "version": "1.0",
                "sessions": data.get("sessions", []),
                "lastCleanup": data.get("lastCleanup", 0),
                "savedAt": int(time.time() * 1000),  # 當前時間戳
            }

            # 保存會話歷史到檔案
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)

            debug_log(f"會話歷史已保存到: {history_file}")
            session_count = len(history_data["sessions"])
            debug_log(f"保存了 {session_count} 個會話記錄")

            return JSONResponse(
                content={
                    "status": "success",
                    "message": f"會話歷史已保存（{session_count} 個會話）",
                }
            )

        except Exception as e:
            debug_log(f"保存會話歷史失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"保存失敗: {e!s}"},
            )

    @manager.app.get("/api/log-level")
    async def get_log_level():
        """獲取日誌等級設定"""
        try:
            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            settings_file = config_dir / "ui_settings.json"

            if settings_file.exists():
                with open(settings_file, encoding="utf-8") as f:
                    settings_data = json.load(f)
                    log_level = settings_data.get("logLevel", "INFO")
                    debug_log(f"從設定檔案載入日誌等級: {log_level}")
                    return JSONResponse(content={"logLevel": log_level})
            else:
                # 預設日誌等級
                default_log_level = "INFO"
                debug_log(f"使用預設日誌等級: {default_log_level}")
                return JSONResponse(content={"logLevel": default_log_level})

        except Exception as e:
            debug_log(f"獲取日誌等級失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": f"獲取日誌等級失敗: {e!s}"},
            )

    @manager.app.post("/api/log-level")
    async def set_log_level(request: Request):
        """設定日誌等級"""
        try:
            data = await request.json()
            log_level = data.get("logLevel")

            if not log_level or log_level not in ["DEBUG", "INFO", "WARN", "ERROR"]:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "無效的日誌等級，必須是 DEBUG, INFO, WARN, ERROR 之一"
                    },
                )

            # 使用統一的設定檔案路徑
            config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_file = config_dir / "ui_settings.json"

            # 載入現有設定或創建新設定
            settings_data = {}
            if settings_file.exists():
                with open(settings_file, encoding="utf-8") as f:
                    settings_data = json.load(f)

            # 更新日誌等級
            settings_data["logLevel"] = log_level

            # 保存設定到檔案
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings_data, f, ensure_ascii=False, indent=2)

            debug_log(f"日誌等級已設定為: {log_level}")

            return JSONResponse(
                content={
                    "status": "success",
                    "logLevel": log_level,
                    "message": "日誌等級已更新",
                }
            )

        except Exception as e:
            debug_log(f"設定日誌等級失敗: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"設定失敗: {e!s}"},
            )


async def handle_websocket_message(manager: "WebUIManager", session, data: dict):
    """處理 WebSocket 消息"""
    message_type = data.get("type")

    if message_type == "submit_feedback":
        # 提交回饋
        feedback = data.get("feedback", "")
        images = data.get("images", [])
        settings = data.get("settings", {})
        await session.submit_feedback(feedback, images, settings)

    elif message_type == "run_command":
        # 執行命令
        command = data.get("command", "")
        if command.strip():
            await session.run_command(command)

    elif message_type == "get_status":
        # 獲取會話狀態
        if session.websocket:
            try:
                await session.websocket.send_json(
                    {"type": "status_update", "status_info": session.get_status_info()}
                )
            except Exception as e:
                debug_log(f"發送狀態更新失敗: {e}")

    elif message_type == "heartbeat":
        # WebSocket 心跳處理（簡化版）
        # 發送心跳回應
        if session.websocket:
            try:
                await session.websocket.send_json(
                    {
                        "type": "heartbeat_response",
                        "timestamp": data.get("timestamp", 0),
                    }
                )
            except Exception as e:
                debug_log(f"發送心跳回應失敗: {e}")

    elif message_type == "user_timeout":
        # 用戶設置的超時已到
        debug_log(f"收到用戶超時通知: {session.session_id}")
        # 清理會話資源
        await session._cleanup_resources_on_timeout()
        # 重構：不再自動停止服務器，保持服務器運行以支援持久性

    else:
        debug_log(f"未知的消息類型: {message_type}")


async def _delayed_server_stop(manager: "WebUIManager"):
    """延遲停止服務器"""
    import asyncio

    await asyncio.sleep(5)  # 等待 5 秒讓前端有時間關閉
    from ..main import stop_web_ui

    stop_web_ui()
    debug_log("Web UI 服務器已因用戶超時而停止")
