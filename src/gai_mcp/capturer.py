"""窗口截图模块 - 支持后台窗口截图和画面变化检测"""

from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class WindowCapturer:
    """对指定窗口进行截图，支持后台窗口和画面变化检测"""

    def __init__(
        self,
        quality: int = 75,
        max_width: int = 1280,
        change_threshold: float = 0.02,
    ) -> None:
        self.quality = quality
        self.max_width = max_width
        self.change_threshold = change_threshold
        self._last_frame: Optional[np.ndarray] = None

    def find_window(self, title: str) -> Optional[int]:
        """通过标题查找窗口句柄（优先选择尺寸最大的匹配窗口）"""
        import win32gui

        candidates: list[tuple[int, int]] = []  # (hwnd, area)

        def enum_callback(hwnd: int, _: None) -> None:
            try:
                if win32gui.IsWindowVisible(hwnd):
                    window_title = win32gui.GetWindowText(hwnd)
                    if title.lower() in window_title.lower():
                        rect = win32gui.GetWindowRect(hwnd)
                        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
                        candidates.append((hwnd, area))
            except Exception:
                pass

        win32gui.EnumWindows(enum_callback, None)
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def list_windows(self) -> list[dict]:
        """列出所有可见窗口"""
        import win32gui

        windows = []

        def enum_callback(hwnd: int, _: None) -> None:
            try:
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title:
                        windows.append({"hwnd": hwnd, "title": title})
            except Exception:
                pass

        win32gui.EnumWindows(enum_callback, None)
        return windows

    def capture(
        self,
        hwnd: int,
        roi: Optional[tuple[float, float, float, float]] = None,
    ) -> Optional[Image.Image]:
        """截取指定窗口的画面

        Args:
            hwnd: 窗口句柄
            roi: 感兴趣区域 (归一化坐标 x1, y1, x2, y2, 范围 0.0-1.0)

        Returns:
            PIL Image 或 None (截图失败时)
        """
        import ctypes
        import ctypes.wintypes

        import win32con
        import win32gui
        import win32ui

        try:
            # 获取窗口尺寸
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]

            if width <= 0 or height <= 0:
                logger.warning(f"窗口尺寸无效: {width}x{height}")
                return None

            # 创建设备上下文
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            # 创建位图
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            # 使用 PrintWindow 截图 (支持后台窗口)
            PW_RENDERFULLCONTENT = 0x00000002
            ctypes.windll.user32.PrintWindow(
                hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT
            )

            # 转换为 PIL Image
            try:
                bmp_info = bitmap.GetInfo()
                bmp_bits = bitmap.GetBitmapBits(True)
                img = Image.frombuffer(
                    "RGB",
                    (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                    bmp_bits,
                    "raw",
                    "BGRX",
                    0,
                    1,
                )
            finally:
                # 确保 Win32 资源始终被清理
                save_dc.DeleteDC()
                mfc_dc.DeleteDC()
                win32gui.ReleaseDC(hwnd, hwnd_dc)
                win32gui.DeleteObject(bitmap.GetHandle())

            # 裁剪 ROI
            if roi:
                x1 = int(roi[0] * img.width)
                y1 = int(roi[1] * img.height)
                x2 = int(roi[2] * img.width)
                y2 = int(roi[3] * img.height)
                img = img.crop((x1, y1, x2, y2))

            # 缩放
            if img.width > self.max_width:
                ratio = self.max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize(
                    (self.max_width, new_height), Image.LANCZOS
                )

            return img

        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def has_changed(self, img: Image.Image) -> bool:
        """检测画面是否相比上一帧有变化"""
        current = np.array(img.convert("L"))  # 转灰度

        if self._last_frame is None:
            self._last_frame = current
            return True

        # 尺寸不同视为有变化
        if current.shape != self._last_frame.shape:
            self._last_frame = current
            return True

        # 计算像素差异比例
        diff = np.abs(current.astype(float) - self._last_frame.astype(float))
        change_ratio = np.mean(diff) / 255.0

        self._last_frame = current
        return change_ratio > self.change_threshold

    def image_to_base64(self, img: Image.Image) -> str:
        """将图片转为 base64 编码的 JPEG"""
        import base64

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self.quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def reset(self) -> None:
        """重置帧缓存"""
        self._last_frame = None
