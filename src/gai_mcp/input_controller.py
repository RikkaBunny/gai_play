"""输入控制模块 - 在虚拟桌面中模拟键鼠操作"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .models import ActionType, GameAction
from .virtual_desktop import VirtualDesktopManager

logger = logging.getLogger(__name__)

# pyautogui 按键名映射
KEY_MAP = {
    "enter": "enter",
    "return": "enter",
    "esc": "escape",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "shift": "shift",
    "ctrl": "ctrl",
    "alt": "alt",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
}


class InputController:
    """在游戏窗口中模拟键鼠操作

    通过虚拟桌面隔离确保不影响用户操作。
    操作流程: 切换到游戏桌面 → 执行操作 → 切回用户桌面
    """

    def __init__(
        self,
        vd_manager: Optional[VirtualDesktopManager] = None,
        use_virtual_desktop: bool = True,
    ) -> None:
        self.vd_manager = vd_manager
        self.use_virtual_desktop = use_virtual_desktop
        self._hwnd: Optional[int] = None
        self._window_rect: Optional[tuple[int, int, int, int]] = None

    def set_target_window(self, hwnd: int) -> None:
        """设置目标游戏窗口"""
        import win32gui

        self._hwnd = hwnd
        self._window_rect = win32gui.GetWindowRect(hwnd)

    def _to_screen_coords(self, norm_x: float, norm_y: float) -> tuple[int, int]:
        """将归一化坐标 (0.0-1.0) 转换为屏幕绝对坐标"""
        import win32gui

        if self._hwnd is None:
            raise RuntimeError("未设置目标窗口")

        # 将坐标限制在 [0.0, 1.0] 范围内
        norm_x = max(0.0, min(1.0, norm_x))
        norm_y = max(0.0, min(1.0, norm_y))

        rect = win32gui.GetWindowRect(self._hwnd)
        self._window_rect = rect

        screen_x = int(rect[0] + norm_x * (rect[2] - rect[0]))
        screen_y = int(rect[1] + norm_y * (rect[3] - rect[1]))
        return screen_x, screen_y

    async def execute(self, action: GameAction) -> bool:
        """执行一个游戏操作"""
        try:
            switched = False
            # 需要切换到游戏桌面时
            if (
                self.use_virtual_desktop
                and self.vd_manager
                and action.action != ActionType.WAIT
            ):
                switched = self.vd_manager.switch_to_game_desktop()
                if switched:
                    await asyncio.sleep(0.1)  # 等待桌面切换完成

            result = await self._do_execute(action)

            # 切回用户桌面
            if switched and self.vd_manager:
                await asyncio.sleep(0.05)
                self.vd_manager.switch_to_original_desktop()

            return result

        except Exception as e:
            logger.error(f"执行操作失败: {action.action} - {e}")
            # 确保切回用户桌面
            if self.vd_manager:
                self.vd_manager.switch_to_original_desktop()
            return False

    async def _do_execute(self, action: GameAction) -> bool:
        """实际执行操作"""
        import pyautogui

        pyautogui.FAILSAFE = False  # 游戏中不需要 failsafe

        match action.action:
            case ActionType.CLICK:
                if action.x is not None and action.y is not None:
                    sx, sy = self._to_screen_coords(action.x, action.y)
                    pyautogui.click(sx, sy)
                    logger.debug(f"点击 ({sx}, {sy})")

            case ActionType.RIGHT_CLICK:
                if action.x is not None and action.y is not None:
                    sx, sy = self._to_screen_coords(action.x, action.y)
                    pyautogui.rightClick(sx, sy)

            case ActionType.DOUBLE_CLICK:
                if action.x is not None and action.y is not None:
                    sx, sy = self._to_screen_coords(action.x, action.y)
                    pyautogui.doubleClick(sx, sy)

            case ActionType.KEY_PRESS:
                if action.key:
                    key = KEY_MAP.get(action.key.lower(), action.key.lower())
                    pyautogui.press(key)
                    logger.debug(f"按键 {key}")

            case ActionType.KEY_COMBO:
                if action.keys:
                    keys = [
                        KEY_MAP.get(k.lower(), k.lower()) for k in action.keys
                    ]
                    pyautogui.hotkey(*keys)
                    logger.debug(f"组合键 {keys}")

            case ActionType.TYPE_TEXT:
                if action.text:
                    import pyperclip
                    pyperclip.copy(action.text)
                    pyautogui.hotkey("ctrl", "v")
                    logger.debug(f"输入文字: {action.text}")

            case ActionType.DRAG:
                if (
                    action.x is not None
                    and action.y is not None
                    and action.x2 is not None
                    and action.y2 is not None
                ):
                    sx1, sy1 = self._to_screen_coords(action.x, action.y)
                    sx2, sy2 = self._to_screen_coords(action.x2, action.y2)
                    duration = action.duration or 0.5
                    pyautogui.moveTo(sx1, sy1)
                    pyautogui.drag(
                        sx2 - sx1, sy2 - sy1, duration=duration
                    )

            case ActionType.SCROLL:
                if action.scroll_amount is not None:
                    if action.x is not None and action.y is not None:
                        sx, sy = self._to_screen_coords(action.x, action.y)
                        pyautogui.scroll(action.scroll_amount, sx, sy)
                    else:
                        pyautogui.scroll(action.scroll_amount)

            case ActionType.WAIT:
                duration = action.duration or 1.0
                await asyncio.sleep(duration)
                logger.debug(f"等待 {duration}s")

        return True

    async def execute_actions(
        self, actions: list[GameAction], delay: float = 0.5
    ) -> int:
        """批量执行操作列表，返回成功执行的数量"""
        success_count = 0

        # 批量操作: 只切换一次桌面
        switched = False
        if self.use_virtual_desktop and self.vd_manager:
            switched = self.vd_manager.switch_to_game_desktop()
            if switched:
                await asyncio.sleep(0.1)

        try:
            for action in actions:
                if action.action == ActionType.WAIT:
                    duration = action.duration or 1.0
                    await asyncio.sleep(duration)
                    success_count += 1
                else:
                    result = await self._do_execute(action)
                    if result:
                        success_count += 1
                    await asyncio.sleep(delay)
        finally:
            if switched and self.vd_manager:
                await asyncio.sleep(0.05)
                self.vd_manager.switch_to_original_desktop()

        return success_count
