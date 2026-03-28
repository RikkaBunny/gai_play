"""Web 配置后台 - API 路由"""

from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config_manager import (
    apply_api_keys,
    clear_log,
    get_log_content,
    load_config,
    save_config,
)
from .game_runner import runner

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ── API 路由 ──


async def index(request: Request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


async def api_get_config(request: Request) -> JSONResponse:
    """获取配置（隐藏 API Key 中间部分）"""
    config = load_config()
    # 脱敏显示（仅对 API Key 脱敏，URL 字段不脱敏）
    masked = config.copy()
    keys = masked.get("api_keys", {})
    url_fields = {"claude_base_url", "openai_base_url", "local_base_url"}
    for k, v in keys.items():
        if k not in url_fields and isinstance(v, str) and len(v) > 10:
            keys[k] = v[:8] + "****" + v[-4:]
    masked["api_keys"] = keys
    return JSONResponse(masked)


async def api_get_config_raw(request: Request) -> JSONResponse:
    """获取完整配置（含完整 Key，仅内部使用）"""
    return JSONResponse(load_config())


async def api_save_config(request: Request) -> JSONResponse:
    """保存配置"""
    try:
        data = await request.json()
        current = load_config()
        new_keys = data.get("api_keys", {})
        old_keys = current.get("api_keys", {})
        for k, v in new_keys.items():
            if isinstance(v, str) and "****" in v:
                new_keys[k] = old_keys.get(k, "")
        data["api_keys"] = new_keys

        save_config(data)
        apply_api_keys(data)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def api_save_api_keys(request: Request) -> JSONResponse:
    """单独保存 API Keys"""
    try:
        data = await request.json()
        config = load_config()
        old_keys = config.get("api_keys", {})
        for k, v in data.items():
            if isinstance(v, str) and "****" in v:
                data[k] = old_keys.get(k, "")
        config["api_keys"] = data
        save_config(config)
        apply_api_keys(config)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def api_get_games(request: Request) -> JSONResponse:
    """获取已配置的游戏列表"""
    config = load_config()
    return JSONResponse(config.get("games", {}))


async def api_save_game(request: Request) -> JSONResponse:
    """添加/更新游戏配置"""
    try:
        data = await request.json()
        name = data.get("name")
        if not name:
            return JSONResponse({"error": "游戏名称不能为空"}, status_code=400)
        config = load_config()
        config.setdefault("games", {})[name] = {
            "path": data.get("path", ""),
            "window_title": data.get("window_title", ""),
            "strategy": data.get("strategy", ""),
            "skills": data.get("skills", []),
            "ai_provider": data.get("ai_provider", "claude"),
            "capture_interval": data.get("capture_interval", 2.0),
        }
        save_config(config)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def api_delete_game(request: Request) -> JSONResponse:
    """删除游戏配置"""
    name = request.path_params["name"]
    config = load_config()
    games = config.get("games", {})
    if name in games:
        del games[name]
        save_config(config)
    return JSONResponse({"status": "ok"})


async def api_get_logs(request: Request) -> JSONResponse:
    """获取日志"""
    lines = int(request.query_params.get("lines", "200"))
    content = get_log_content(lines)
    return JSONResponse({"logs": content})


async def api_clear_logs(request: Request) -> JSONResponse:
    """清空日志"""
    clear_log()
    return JSONResponse({"status": "ok"})


# ── 游戏控制 API ──


async def api_game_start(request: Request) -> JSONResponse:
    """启动游戏"""
    try:
        data = await request.json()
        game_name = data.get("game_name", "")
        if not game_name:
            return JSONResponse({"error": "请指定游戏名称"}, status_code=400)
        result = await runner.start(game_name)
        return JSONResponse(result)
    except Exception as e:
        import traceback
        logger.error(f"启动游戏异常: {traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_game_stop(request: Request) -> JSONResponse:
    """停止游戏"""
    result = await runner.stop()
    return JSONResponse(result)


async def api_game_pause(request: Request) -> JSONResponse:
    """暂停游戏"""
    return JSONResponse(runner.pause())


async def api_game_resume(request: Request) -> JSONResponse:
    """恢复游戏"""
    return JSONResponse(runner.resume())


async def api_game_status(request: Request) -> JSONResponse:
    """获取游戏运行状态"""
    return JSONResponse(runner.get_status())


async def api_game_decisions(request: Request) -> JSONResponse:
    """获取决策历史"""
    limit = int(request.query_params.get("limit", "20"))
    return JSONResponse(runner.get_decisions(limit))


def create_app() -> Starlette:
    """创建 Web 应用"""
    routes = [
        Route("/", index),
        Route("/api/config", api_get_config, methods=["GET"]),
        Route("/api/config/raw", api_get_config_raw, methods=["GET"]),
        Route("/api/config", api_save_config, methods=["POST"]),
        Route("/api/keys", api_save_api_keys, methods=["POST"]),
        Route("/api/games", api_get_games, methods=["GET"]),
        Route("/api/games", api_save_game, methods=["POST"]),
        Route("/api/games/{name}", api_delete_game, methods=["DELETE"]),
        Route("/api/logs", api_get_logs, methods=["GET"]),
        Route("/api/logs/clear", api_clear_logs, methods=["POST"]),
        # 游戏控制
        Route("/api/play/start", api_game_start, methods=["POST"]),
        Route("/api/play/stop", api_game_stop, methods=["POST"]),
        Route("/api/play/pause", api_game_pause, methods=["POST"]),
        Route("/api/play/resume", api_game_resume, methods=["POST"]),
        Route("/api/play/status", api_game_status, methods=["GET"]),
        Route("/api/play/decisions", api_game_decisions, methods=["GET"]),
        Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]
    return Starlette(routes=routes)
