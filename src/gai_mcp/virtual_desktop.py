"""虚拟桌面管理模块 - 将游戏隔离到独立虚拟桌面"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VirtualDesktopManager:
    """管理 Windows 虚拟桌面，将游戏窗口隔离到独立桌面"""

    def __init__(self) -> None:
        self._game_desktop = None
        self._original_desktop = None
        self._managed_hwnds: list[int] = []

    @property
    def is_available(self) -> bool:
        """检查虚拟桌面功能是否可用"""
        try:
            import pyvda  # noqa: F401

            return True
        except ImportError:
            logger.warning("pyvda 未安装，虚拟桌面功能不可用")
            return False

    def create_game_desktop(self) -> bool:
        """创建游戏专用虚拟桌面"""
        if not self.is_available:
            return False

        try:
            from pyvda import AppView, VirtualDesktop

            # 记录当前桌面
            self._original_desktop = VirtualDesktop.current()

            # 获取所有桌面，如果只有一个则创建新的
            desktops = VirtualDesktop.get_desktops()
            if len(desktops) < 2:
                VirtualDesktop.create()
                desktops = VirtualDesktop.get_desktops()

            # 使用最后一个桌面作为游戏桌面
            self._game_desktop = desktops[-1]
            logger.info(
                f"游戏桌面已准备: 桌面 {self._game_desktop.number}"
            )
            return True

        except Exception as e:
            logger.error(f"创建游戏桌面失败: {e}")
            return False

    def move_window_to_game_desktop(self, hwnd: int) -> bool:
        """将窗口移动到游戏桌面"""
        if self._game_desktop is None:
            logger.error("游戏桌面未创建")
            return False

        try:
            from pyvda import AppView

            app_view = AppView.from_hwnd(hwnd)
            app_view.move(self._game_desktop)
            self._managed_hwnds.append(hwnd)
            logger.info(f"窗口 {hwnd} 已移至游戏桌面")
            return True

        except Exception as e:
            logger.error(f"移动窗口失败: {e}")
            return False

    def switch_to_game_desktop(self) -> bool:
        """切换到游戏桌面"""
        if self._game_desktop is None:
            return False
        try:
            self._game_desktop.go()
            return True
        except Exception as e:
            logger.error(f"切换到游戏桌面失败: {e}")
            return False

    def switch_to_original_desktop(self) -> bool:
        """切换回用户原始桌面"""
        if self._original_desktop is None:
            return False
        try:
            self._original_desktop.go()
            return True
        except Exception as e:
            logger.error(f"切换回原始桌面失败: {e}")
            return False

    def cleanup(self) -> None:
        """清理: 将窗口移回原始桌面，删除游戏桌面"""
        if not self.is_available or self._game_desktop is None:
            return

        try:
            from pyvda import AppView

            # 将所有管理的窗口移回原始桌面
            if self._original_desktop:
                for hwnd in self._managed_hwnds:
                    try:
                        app_view = AppView.from_hwnd(hwnd)
                        app_view.move(self._original_desktop)
                    except Exception:
                        pass  # 窗口可能已关闭

                # 切回原始桌面
                self.switch_to_original_desktop()

            logger.info("游戏桌面已清理")

        except Exception as e:
            logger.error(f"清理游戏桌面失败: {e}")
        finally:
            self._game_desktop = None
            self._original_desktop = None
            self._managed_hwnds.clear()

    @property
    def game_desktop_number(self) -> Optional[int]:
        """返回游戏桌面编号"""
        if self._game_desktop is None:
            return None
        try:
            return self._game_desktop.number
        except Exception:
            return None
