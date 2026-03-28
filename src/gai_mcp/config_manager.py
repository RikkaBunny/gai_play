"""配置管理模块 - 读写用户配置（API Key、游戏路径等）"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 用户配置文件路径
CONFIG_DIR = Path(os.environ.get("GAI_MCP_CONFIG_DIR", Path.home() / ".gai_mcp"))
USER_CONFIG_PATH = CONFIG_DIR / "user_config.json"
LOG_PATH = CONFIG_DIR / "gai_mcp.log"

# 默认配置
DEFAULT_CONFIG: dict[str, Any] = {
    "api_keys": {
        "anthropic": "sk-LM8rTxhCLzbyjoOJRyT5sziuOtocmNfYzYpKxMIlIAwXcl2g",
        "openai": "sk-LM8rTxhCLzbyjoOJRyT5sziuOtocmNfYzYpKxMIlIAwXcl2g",
        "claude_base_url": "http://localhost:3000/v1",
        "openai_base_url": "http://localhost:3000/v1",
        "local_base_url": "http://localhost:11434",
    },
    "ai": {
        "provider": "local",
        "claude_model": "claude-sonnet-4-20250514",
        "openai_model": "gpt-4o",
        "local_model": "fredrezones55/qwen3.5-opus:9b",
    },
    "game_loop": {
        "capture_interval": 2.0,
        "action_delay": 0.5,
        "change_threshold": 0.02,
        "screenshot_quality": 75,
        "screenshot_max_width": 1280,
    },
    "virtual_desktop": {
        "enabled": True,
        "auto_cleanup": True,
    },
    # 高级功能配置
    "advanced": {
        "task_inference_enabled": True,
        "reflection_enabled": True,
        "reflection_diff_threshold": 0.005,
        "reflection_max_retries": 2,
        "memory_enabled": True,
        "short_term_capacity": 10,
        "long_term_enabled": True,
        "dynamic_skills_enabled": True,
        "max_dynamic_skills": 50,
        "layered_decision_enabled": True,
        "local_cv_change_threshold": 0.01,
        "static_frame_patience": 3,
    },
    "games": {
        "三色绘恋": {
            "path": r"C:\BunnyAPP\Steam\steamapps\common\Tricolour Lovestory\TricolourLovestory_chs.exe",
            "window_title": "Tricolour Lovestory",
            "strategy": "你正在玩一款中文视觉小说《三色绘恋》。你的目标是推进剧情、体验故事、在选项中做出有趣的选择。",
            "skills": ["galgame.md", "tricolour_lovestory.md"],
            "ai_provider": "local",
            "capture_interval": 3.0,
        },
    },
}


def ensure_config_dir() -> None:
    """确保配置目录存在"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """加载用户配置"""
    ensure_config_dir()
    if USER_CONFIG_PATH.exists():
        try:
            with open(USER_CONFIG_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            # 合并默认值（确保新增字段存在）
            config = _deep_merge(DEFAULT_CONFIG, saved)
            return config
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any]) -> None:
    """保存用户配置"""
    ensure_config_dir()
    with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logger.info("配置已保存")


def update_config(updates: dict[str, Any]) -> dict[str, Any]:
    """部分更新配置"""
    config = load_config()
    config = _deep_merge(config, updates)
    save_config(config)
    return config


def apply_api_keys(config: dict[str, Any]) -> None:
    """将 API Key 设置到环境变量"""
    keys = config.get("api_keys", {})
    if keys.get("anthropic"):
        os.environ["ANTHROPIC_API_KEY"] = keys["anthropic"]
    if keys.get("openai"):
        os.environ["OPENAI_API_KEY"] = keys["openai"]


def get_log_content(lines: int = 200) -> str:
    """读取最近的日志内容"""
    if not LOG_PATH.exists():
        return ""
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception:
        return ""


def clear_log() -> None:
    """清空日志文件"""
    if LOG_PATH.exists():
        LOG_PATH.write_text("", encoding="utf-8")


def setup_file_logging() -> None:
    """配置文件日志（自动轮转，最大 5MB，保留 3 个备份）"""
    from logging.handlers import RotatingFileHandler

    ensure_config_dir()
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    logging.getLogger().addHandler(file_handler)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并字典，override 覆盖 base"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
