"""输入控制模块 - 通过 Win32 消息后台操作游戏窗口，不抢占键鼠"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
from typing import Optional

from .models import ActionType, GameAction
from .virtual_desktop import VirtualDesktopManager

logger = logging.getLogger(__name__)

# Win32 消息常量
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
WHEEL_DELTA = 120

# 虚拟键码映射
VK_MAP = {
    "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
    "z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
}


def _make_lparam(x: int, y: int) -> int:
    """构造鼠标消息的 lParam (低16位=x, 高16位=y)"""
    return (y << 16) | (x & 0xFFFF)


def _make_key_lparam(vk: int, down: bool) -> int:
    """构造键盘消息的 lParam"""
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    lparam = (scan << 16) | 1  # repeat=1
    if not down:
        lparam |= (1 << 30) | (1 << 31)  # previous key state + transition
    return lparam


class InputController:
    """通过 Win32 消息后台操作游戏窗口

    使用 PostMessage/SendMessage 发送鼠标和键盘消息，
    不移动物理鼠标，不抢占键盘焦点。

    坐标系说明:
    - AI 返回归一化坐标 (0.0-1.0)，相对于它看到的截图
    - 截图可能经过 ROI 裁剪和缩放，需要反向映射回客户区像素坐标
    - 通过 capturer.last_capture_info 获取坐标转换信息
    """

    def __init__(
        self,
        vd_manager: Optional[VirtualDesktopManager] = None,
        use_virtual_desktop: bool = True,
        capturer: Optional[object] = None,
    ) -> None:
        self.vd_manager = vd_manager
        self.use_virtual_desktop = use_virtual_desktop
        self._hwnd: Optional[int] = None
        self._capturer = capturer  # WindowCapturer 引用，用于获取截图元信息

    def set_target_window(self, hwnd: int) -> None:
        """设置目标游戏窗口"""
        self._hwnd = hwnd

    def set_capturer(self, capturer: object) -> None:
        """设置 capturer 引用，用于获取截图坐标转换信息"""
        self._capturer = capturer

    def _to_client_coords(self, norm_x: float, norm_y: float) -> tuple[int, int]:
        """将归一化坐标 (0.0-1.0) 转换为客户区像素坐标

        处理流程:
        1. AI 看到的是 (可能经过 ROI 裁剪 + 缩放后的) 截图
        2. 归一化坐标是相对于该截图的
        3. 如果有 ROI，需要将坐标映射回完整客户区
        4. 最终得到物理像素坐标
        """
        import win32gui

        if self._hwnd is None:
            raise RuntimeError("未设置目标窗口")

        norm_x = max(0.0, min(1.0, norm_x))
        norm_y = max(0.0, min(1.0, norm_y))

        # 获取截图元信息
        capture_info = None
        if self._capturer and hasattr(self._capturer, "last_capture_info"):
            capture_info = self._capturer.last_capture_info

        # 如果有 ROI，将归一化坐标从 ROI 空间映射回完整客户区空间
        if capture_info and capture_info.roi:
            roi = capture_info.roi  # (x1, y1, x2, y2) 归一化
            # ROI 内的 (norm_x, norm_y) → 完整客户区的归一化坐标
            full_x = roi[0] + norm_x * (roi[2] - roi[0])
            full_y = roi[1] + norm_y * (roi[3] - roi[1])
            logger.debug(
                f"ROI 坐标映射: ({norm_x:.3f},{norm_y:.3f}) "
                f"→ 全局({full_x:.3f},{full_y:.3f})"
            )
            norm_x, norm_y = full_x, full_y

        client_rect = win32gui.GetClientRect(self._hwnd)
        cx = int(norm_x * client_rect[2])
        cy = int(norm_y * client_rect[3])

        # 边界安全检查
        cx = max(0, min(cx, client_rect[2] - 1))
        cy = max(0, min(cy, client_rect[3] - 1))

        logger.debug(
            f"坐标转换: norm({norm_x:.3f},{norm_y:.3f}) "
            f"→ pixel({cx},{cy}) / 客户区({client_rect[2]}x{client_rect[3]})"
        )
        return cx, cy

    def _post(self, msg: int, wparam: int = 0, lparam: int = 0) -> None:
        """向目标窗口发送消息"""
        ctypes.windll.user32.PostMessageW(self._hwnd, msg, wparam, lparam)

    def _send(self, msg: int, wparam: int = 0, lparam: int = 0) -> int:
        """向目标窗口发送消息并等待处理"""
        return ctypes.windll.user32.SendMessageW(self._hwnd, msg, wparam, lparam)

    def _click_at(self, cx: int, cy: int) -> None:
        """在客户区坐标发送左键点击

        先发 MOUSEMOVE 让游戏感知鼠标位置，短暂等待后再点击，
        避免部分游戏因消息处理太快而忽略位置。
        """
        import time

        lp = _make_lparam(cx, cy)
        self._post(WM_MOUSEMOVE, 0, lp)
        time.sleep(0.02)  # 20ms 让游戏处理 MOUSEMOVE
        self._post(WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.02)  # 20ms 模拟真实按下时长
        self._post(WM_LBUTTONUP, 0, lp)

    def _right_click_at(self, cx: int, cy: int) -> None:
        """在客户区坐标发送右键点击"""
        import time

        lp = _make_lparam(cx, cy)
        self._post(WM_MOUSEMOVE, 0, lp)
        time.sleep(0.02)
        self._post(WM_RBUTTONDOWN, MK_RBUTTON, lp)
        time.sleep(0.02)
        self._post(WM_RBUTTONUP, 0, lp)

    def _double_click_at(self, cx: int, cy: int) -> None:
        """在客户区坐标发送双击"""
        import time

        lp = _make_lparam(cx, cy)
        self._post(WM_MOUSEMOVE, 0, lp)
        time.sleep(0.02)
        self._post(WM_LBUTTONDOWN, MK_LBUTTON, lp)
        self._post(WM_LBUTTONUP, 0, lp)
        time.sleep(0.05)  # 双击间隔
        self._post(WM_LBUTTONDBLCLK, MK_LBUTTON, lp)
        self._post(WM_LBUTTONUP, 0, lp)

    def _press_key(self, vk: int) -> None:
        """发送按键（按下+弹起）"""
        self._post(WM_KEYDOWN, vk, _make_key_lparam(vk, True))
        self._post(WM_KEYUP, vk, _make_key_lparam(vk, False))

    def _send_text(self, text: str) -> None:
        """通过 WM_CHAR 逐字发送文本（支持中文）"""
        for ch in text:
            self._post(WM_CHAR, ord(ch), 0)

    async def execute(self, action: GameAction) -> bool:
        """执行一个游戏操作"""
        try:
            return await self._do_execute(action)
        except Exception as e:
            logger.error(f"执行操作失败: {action.action} - {e}")
            return False

    async def _do_execute(self, action: GameAction) -> bool:
        """实际执行操作（通过 Win32 消息，不抢占键鼠）"""
        match action.action:
            case ActionType.CLICK:
                if action.x is not None and action.y is not None:
                    cx, cy = self._to_client_coords(action.x, action.y)
                    self._click_at(cx, cy)
                    logger.info(
                        f"后台点击 norm({action.x:.3f},{action.y:.3f}) → pixel({cx},{cy})"
                        + (f" | {action.reason}" if action.reason else "")
                    )

            case ActionType.RIGHT_CLICK:
                if action.x is not None and action.y is not None:
                    cx, cy = self._to_client_coords(action.x, action.y)
                    self._right_click_at(cx, cy)

            case ActionType.DOUBLE_CLICK:
                if action.x is not None and action.y is not None:
                    cx, cy = self._to_client_coords(action.x, action.y)
                    self._double_click_at(cx, cy)

            case ActionType.KEY_PRESS:
                if action.key:
                    vk = VK_MAP.get(action.key.lower())
                    if vk:
                        self._press_key(vk)
                        logger.debug(f"后台按键 {action.key} (vk=0x{vk:02X})")
                    else:
                        logger.warning(f"未知按键: {action.key}")

            case ActionType.KEY_COMBO:
                if action.keys:
                    vks = [VK_MAP.get(k.lower()) for k in action.keys]
                    if all(vks):
                        # 按顺序按下，再倒序弹起
                        for vk in vks:
                            self._post(WM_KEYDOWN, vk, _make_key_lparam(vk, True))
                        for vk in reversed(vks):
                            self._post(WM_KEYUP, vk, _make_key_lparam(vk, False))
                        logger.debug(f"后台组合键 {action.keys}")

            case ActionType.TYPE_TEXT:
                if action.text:
                    self._send_text(action.text)
                    logger.debug(f"后台输入文字: {action.text}")

            case ActionType.DRAG:
                if (
                    action.x is not None
                    and action.y is not None
                    and action.x2 is not None
                    and action.y2 is not None
                ):
                    cx1, cy1 = self._to_client_coords(action.x, action.y)
                    cx2, cy2 = self._to_client_coords(action.x2, action.y2)
                    # 模拟拖拽：按下 → 移动 → 弹起
                    self._post(WM_MOUSEMOVE, 0, _make_lparam(cx1, cy1))
                    self._post(WM_LBUTTONDOWN, MK_LBUTTON, _make_lparam(cx1, cy1))
                    # 插值移动
                    steps = 10
                    for i in range(1, steps + 1):
                        t = i / steps
                        mx = int(cx1 + (cx2 - cx1) * t)
                        my = int(cy1 + (cy2 - cy1) * t)
                        self._post(WM_MOUSEMOVE, MK_LBUTTON, _make_lparam(mx, my))
                        await asyncio.sleep(0.02)
                    self._post(WM_LBUTTONUP, 0, _make_lparam(cx2, cy2))

            case ActionType.SCROLL:
                if action.scroll_amount is not None:
                    cx, cy = 0, 0
                    if action.x is not None and action.y is not None:
                        cx, cy = self._to_client_coords(action.x, action.y)
                    wparam = (action.scroll_amount * WHEEL_DELTA) << 16
                    self._post(WM_MOUSEWHEEL, wparam, _make_lparam(cx, cy))

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
        return success_count
