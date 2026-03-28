"""独立命令行工具 - 不依赖 MCP 协议，直接运行

无参数启动进入交互式菜单，也支持传统子命令模式。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from .core import GameController
from .config_manager import apply_api_keys, load_config, setup_file_logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 美化输出 ───────────────────────────────────────────────


def _banner() -> None:
    print(
        r"""
   ____    _    ___   __  __  ____ ____
  / ___|  / \  |_ _| |  \/  |/ ___|  _ \
 | |  _  / _ \  | |  | |\/| | |   | |_) |
 | |_| |/ ___ \ | |  | |  | | |___|  __/
  \____/_/   \_\___| |_|  |_|\____|_|

  AI 自动游玩游戏  v0.1.0
"""
    )


def _print_divider(char: str = "─", width: int = 50) -> None:
    print(char * width)


def _input_choice(prompt: str, max_val: int) -> int | None:
    """读取用户数字选择，返回 None 表示退出"""
    while True:
        try:
            raw = input(prompt).strip()
            if raw.lower() in ("q", "quit", "exit", ""):
                return None
            n = int(raw)
            if 1 <= n <= max_val:
                return n
            print(f"  请输入 1-{max_val} 之间的数字")
        except (ValueError, EOFError):
            return None
        except KeyboardInterrupt:
            print()
            return None


# ─── 交互式菜单 ─────────────────────────────────────────────


async def _interactive() -> None:
    """无参数时进入交互式菜单"""
    _banner()

    ctrl = GameController()
    cfg = ctrl.load_config()
    games = cfg.get("games", {})

    while True:
        _print_divider()
        print("  主菜单\n")

        # 菜单项
        menu = []
        if games:
            for i, name in enumerate(games, 1):
                g = games[name]
                provider = g.get("ai_provider", cfg.get("ai", {}).get("provider", "local"))
                menu.append(("game", name))
                print(f"  [{i}] 启动游戏: {name}  ({provider})")

        idx_detect = len(menu) + 1
        menu.append(("detect", None))
        print(f"  [{idx_detect}] 自动检测窗口并游玩")

        idx_web = len(menu) + 1
        menu.append(("web", None))
        print(f"  [{idx_web}] 打开 Web 控制台")

        idx_list = len(menu) + 1
        menu.append(("list", None))
        print(f"  [{idx_list}] 列出所有窗口")

        idx_shot = len(menu) + 1
        menu.append(("screenshot", None))
        print(f"  [{idx_shot}] 截图测试")

        print()
        print("  [q] 退出")
        print()

        choice = _input_choice("  请选择> ", len(menu))
        if choice is None:
            print("\n  再见!")
            return

        action, data = menu[choice - 1]

        if action == "game":
            await _quick_play_game(ctrl, cfg, data)
        elif action == "detect":
            await _quick_detect_and_play(ctrl, cfg)
        elif action == "web":
            _launch_web()
        elif action == "list":
            await _cmd_list()
            input("\n  按 Enter 返回菜单...")
        elif action == "screenshot":
            await _quick_screenshot(ctrl)


async def _quick_play_game(ctrl: GameController, cfg: dict, game_name: str) -> None:
    """从已保存配置快速启动游戏"""
    game = cfg["games"][game_name]
    ai_cfg = cfg.get("ai", {})
    provider = game.get("ai_provider", ai_cfg.get("provider", "local"))

    print(f"\n  正在启动 [{game_name}] ...")
    print(f"  窗口: {game.get('window_title', '?')}")
    print(f"  AI: {provider}")
    _print_divider()

    result = await ctrl.start_game(
        window_title=game.get("window_title", ""),
        ai_provider=provider,
        ai_model=None,
        strategy_prompt=game.get("strategy", ""),
        capture_interval=game.get("capture_interval", 2.0),
        action_delay=cfg.get("game_loop", {}).get("action_delay", 0.5),
        use_virtual_desktop=False,
    )

    if "error" in result:
        print(f"\n  启动失败: {result['error']}")
        input("  按 Enter 返回菜单...")
        return

    print(f"  游戏运行中 (hwnd={result['hwnd']})")
    print("  按 Ctrl+C 停止\n")
    await _wait_for_stop(ctrl)


async def _quick_detect_and_play(ctrl: GameController, cfg: dict) -> None:
    """扫描窗口让用户选一个开玩"""
    print("\n  正在扫描窗口...\n")
    windows = ctrl.list_windows()

    # 过滤掉常见系统窗口
    ignore = {"Program Manager", "Windows Input Experience", "MSCTFIME UI",
              "Default IME", "Settings", "Microsoft Text Input Application"}
    windows = [w for w in windows if w["title"] not in ignore and len(w["title"]) > 1]

    if not windows:
        print("  未找到可用窗口")
        input("  按 Enter 返回菜单...")
        return

    for i, w in enumerate(windows[:20], 1):
        print(f"  [{i:2d}] {w['title']}")

    if len(windows) > 20:
        print(f"  ... 还有 {len(windows) - 20} 个窗口")

    print()
    choice = _input_choice("  选择窗口> ", min(len(windows), 20))
    if choice is None:
        return

    selected = windows[choice - 1]
    title = selected["title"]

    # 选 AI
    ai_cfg = cfg.get("ai", {})
    default_provider = ai_cfg.get("provider", "local")
    print(f"\n  AI 提供者 (直接回车使用 {default_provider}):")
    print("  [1] local (Ollama 本地)")
    print("  [2] openai")
    print("  [3] claude")
    print()
    raw = input("  选择> ").strip()
    provider_map = {"1": "local", "2": "openai", "3": "claude"}
    provider = provider_map.get(raw, default_provider)

    print(f"\n  窗口: {title}")
    print(f"  AI: {provider}")
    _print_divider()

    result = await ctrl.start_game(
        window_title=title,
        ai_provider=provider,
        strategy_prompt="",
        capture_interval=cfg.get("game_loop", {}).get("capture_interval", 2.0),
        action_delay=cfg.get("game_loop", {}).get("action_delay", 0.5),
        use_virtual_desktop=False,
    )

    if "error" in result:
        print(f"\n  启动失败: {result['error']}")
        input("  按 Enter 返回菜单...")
        return

    print(f"  游戏运行中 (hwnd={result['hwnd']})")
    print("  按 Ctrl+C 停止\n")
    await _wait_for_stop(ctrl)


async def _quick_screenshot(ctrl: GameController) -> None:
    """交互式截图测试"""
    print("\n  正在扫描窗口...\n")
    windows = ctrl.list_windows()
    windows = [w for w in windows if len(w["title"]) > 1]

    if not windows:
        print("  未找到窗口")
        input("  按 Enter 返回菜单...")
        return

    for i, w in enumerate(windows[:20], 1):
        print(f"  [{i:2d}] {w['title']}")
    print()

    choice = _input_choice("  选择窗口> ", min(len(windows), 20))
    if choice is None:
        return

    selected = windows[choice - 1]

    from .capturer import WindowCapturer
    capturer = WindowCapturer()
    img = capturer.capture(selected["hwnd"])
    if img is None:
        print("  截图失败")
    else:
        output = "screenshot.jpg"
        img.save(output)
        print(f"\n  截图已保存: {output} ({img.width}x{img.height})")

        # Windows 下直接打开图片
        if sys.platform == "win32":
            import os
            os.startfile(output)

    input("  按 Enter 返回菜单...")


def _launch_web() -> None:
    """启动 Web 控制台"""
    print("\n  正在启动 Web 控制台...")
    print("  浏览器将自动打开 http://localhost:9966\n")
    from .web.run import main as web_main
    web_main(port=9966, open_ui=True)


# ─── 传统子命令 ─────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gai-play",
        description="AI 自动游玩游戏 - 无参数启动进入交互式菜单",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令 (不输入则进入交互式菜单)")

    # ── list ──
    sub.add_parser("list", help="列出所有可见窗口")

    # ── play ──
    p_play = sub.add_parser("play", help="开始自动游玩")
    p_play.add_argument("window_title", help="游戏窗口标题 (模糊匹配)")
    p_play.add_argument("--provider", "-p", default=None, help="AI 提供者 (claude/openai/local)")
    p_play.add_argument("--model", "-m", default=None, help="AI 模型名称")
    p_play.add_argument("--strategy", "-s", default="", help="游戏策略提示词")
    p_play.add_argument("--interval", "-i", type=float, default=None, help="截图间隔 (秒)")
    p_play.add_argument("--delay", "-d", type=float, default=None, help="操作间延迟 (秒)")
    p_play.add_argument("--no-vd", action="store_true", help="不使用虚拟桌面")

    # ── play-game ──
    p_game = sub.add_parser("play-game", help="按游戏名启动 (使用已保存的游戏配置)")
    p_game.add_argument("game_name", help="游戏配置名称")

    # ── screenshot ──
    p_shot = sub.add_parser("screenshot", help="截取窗口截图并保存")
    p_shot.add_argument("window_title", help="窗口标题")
    p_shot.add_argument("--output", "-o", default="screenshot.jpg", help="输出文件路径")

    # ── web ──
    sub.add_parser("web", help="启动 Web 控制台")

    return parser.parse_args()


async def _cmd_list() -> None:
    """列出窗口"""
    ctrl = GameController()
    windows = ctrl.list_windows()
    if not windows:
        print("  未找到可见窗口")
        return
    print(f"\n  {'HWND':<12} 标题")
    _print_divider()
    for w in windows:
        print(f"  {w['hwnd']:<12} {w['title']}")
    print(f"\n  共 {len(windows)} 个窗口")


async def _cmd_play(args: argparse.Namespace) -> None:
    """直接指定窗口标题游玩"""
    ctrl = GameController()
    cfg = ctrl.load_config()
    ai_cfg = cfg.get("ai", {})
    loop_cfg = cfg.get("game_loop", {})

    provider = args.provider or ai_cfg.get("provider", "local")
    interval = args.interval or loop_cfg.get("capture_interval", 2.0)
    delay = args.delay or loop_cfg.get("action_delay", 0.5)

    print(f"  正在查找窗口: {args.window_title}")
    result = await ctrl.start_game(
        window_title=args.window_title,
        ai_provider=provider,
        ai_model=args.model,
        strategy_prompt=args.strategy,
        capture_interval=interval,
        action_delay=delay,
        use_virtual_desktop=not args.no_vd,
    )

    if "error" in result:
        print(f"  启动失败: {result['error']}")
        return

    print(f"  游戏已启动 (hwnd={result['hwnd']}, provider={result['ai_provider']})")
    print("  按 Ctrl+C 停止")
    await _wait_for_stop(ctrl)


async def _cmd_play_game(args: argparse.Namespace) -> None:
    """按游戏配置名启动"""
    ctrl = GameController()
    cfg = ctrl.load_config()

    games = cfg.get("games", {})
    game = games.get(args.game_name)
    if not game:
        print(f"  未找到游戏配置: {args.game_name}")
        print(f"  可用游戏: {', '.join(games.keys()) or '无'}")
        return

    ai_cfg = cfg.get("ai", {})
    provider = game.get("ai_provider", ai_cfg.get("provider", "local"))

    print(f"  正在启动: {args.game_name}")
    result = await ctrl.start_game(
        window_title=game.get("window_title", ""),
        ai_provider=provider,
        ai_model=None,
        strategy_prompt=game.get("strategy", ""),
        capture_interval=game.get("capture_interval", 2.0),
        action_delay=cfg.get("game_loop", {}).get("action_delay", 0.5),
        use_virtual_desktop=False,
    )

    if "error" in result:
        print(f"  启动失败: {result['error']}")
        return

    print(f"  游戏已启动 (hwnd={result['hwnd']}, provider={result['ai_provider']})")
    print("  按 Ctrl+C 停止")
    await _wait_for_stop(ctrl)


async def _cmd_screenshot(args: argparse.Namespace) -> None:
    """截图并保存"""
    from .capturer import WindowCapturer

    capturer = WindowCapturer()
    hwnd = capturer.find_window(args.window_title)
    if not hwnd:
        print(f"  未找到窗口: {args.window_title}")
        return

    img = capturer.capture(hwnd)
    if img is None:
        print("  截图失败")
        return

    img.save(args.output)
    print(f"  截图已保存: {args.output} ({img.width}x{img.height})")


# ─── 等待停止 ───────────────────────────────────────────────


async def _wait_for_stop(ctrl: GameController) -> None:
    """等待用户中断"""
    stop_event = asyncio.Event()

    def _on_signal():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            break

    try:
        if sys.platform == "win32":
            while ctrl.game_loop and ctrl.game_loop.is_running:
                await asyncio.sleep(1)
                status = ctrl.get_status()
                if status.get("status") == "error":
                    print(f"  游戏出错: {status.get('last_error')}")
        else:
            await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\n  正在停止...")
        result = await ctrl.stop_game()
        decisions = result.get("total_decisions", 0)
        actions = result.get("total_actions", 0)
        print(f"  已停止。共 {decisions} 次决策，{actions} 次操作。")


# ─── 入口 ───────────────────────────────────────────────────


def main() -> None:
    """CLI 入口 - 无参数进入交互式菜单"""
    setup_file_logging()
    args = _parse_args()

    if not args.command:
        # 无参数 → 交互式菜单
        asyncio.run(_interactive())
        return

    match args.command:
        case "list":
            asyncio.run(_cmd_list())
        case "play":
            asyncio.run(_cmd_play(args))
        case "play-game":
            asyncio.run(_cmd_play_game(args))
        case "screenshot":
            asyncio.run(_cmd_screenshot(args))
        case "web":
            _launch_web()


if __name__ == "__main__":
    main()
