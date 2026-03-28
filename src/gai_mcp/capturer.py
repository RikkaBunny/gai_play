"""窗口截图模块 - 支持后台窗口截图和画面变化检测"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 确保进程 DPI 感知（在模块加载时设置，仅 Windows）
try:
    import ctypes
    # Per-Monitor DPI Aware V2 = 最精确的坐标映射
    # 返回值: S_OK=0 表示成功, E_ACCESSDENIED 表示已设置过
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    logger.debug("已设置 Per-Monitor DPI Awareness V2")
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        logger.debug("已设置 System DPI Awareness (回退)")
    except Exception:
        pass  # 非 Windows 系统忽略


@dataclass
class CaptureResult:
    """截图结果，携带坐标转换所需的元信息"""

    image: Image.Image
    # 原始客户区尺寸 (物理像素)
    client_width: int = 0
    client_height: int = 0
    # DPI 缩放倍率 (1.0 = 100%, 1.5 = 150%, 2.0 = 200%)
    dpi_scale: float = 1.0
    # 截图是否经过缩放
    resized: bool = False
    # 缩放比 (截图宽度 / 原始宽度)
    resize_ratio: float = 1.0
    # ROI 信息 (归一化坐标, None 表示未裁剪)
    roi: Optional[tuple[float, float, float, float]] = None


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
        # 最近一次截图的元信息 (供 InputController 使用)
        self.last_capture_info: Optional[CaptureResult] = None

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

    def _get_dpi_scale(self, hwnd: int) -> float:
        """获取窗口所在显示器的 DPI 缩放倍率"""
        try:
            import ctypes
            # GetDpiForWindow (Win10 1607+)
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                return dpi / 96.0
        except Exception:
            pass
        return 1.0

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

        Side Effects:
            更新 self.last_capture_info 供 InputController 使用
        """
        import ctypes
        import ctypes.wintypes

        import win32con
        import win32gui
        import win32ui

        try:
            dpi_scale = self._get_dpi_scale(hwnd)

            # 获取客户区尺寸（不含标题栏和边框）
            # 已设置 DPI Awareness 后，返回的是物理像素
            client_rect = win32gui.GetClientRect(hwnd)
            width = client_rect[2]
            height = client_rect[3]

            if width <= 0 or height <= 0:
                logger.warning(f"窗口尺寸无效: {width}x{height}")
                return None

            logger.debug(
                f"截图参数: 客户区={width}x{height}, DPI={dpi_scale:.0%}, "
                f"max_width={self.max_width}"
            )

            # 使用客户区 DC 截图（不含标题栏和边框）
            hwnd_dc = ctypes.windll.user32.GetDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            # PrintWindow + PW_CLIENTONLY 只截客户区
            PW_CLIENTONLY = 0x00000001
            PW_RENDERFULLCONTENT = 0x00000002
            ctypes.windll.user32.PrintWindow(
                hwnd, save_dc.GetSafeHdc(), PW_CLIENTONLY | PW_RENDERFULLCONTENT
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
                ctypes.windll.user32.ReleaseDC(hwnd, hwnd_dc)
                win32gui.DeleteObject(bitmap.GetHandle())

            # 构建截图元信息
            capture_info = CaptureResult(
                image=img,
                client_width=width,
                client_height=height,
                dpi_scale=dpi_scale,
                roi=roi,
            )

            # 裁剪 ROI
            if roi:
                x1 = int(roi[0] * img.width)
                y1 = int(roi[1] * img.height)
                x2 = int(roi[2] * img.width)
                y2 = int(roi[3] * img.height)
                img = img.crop((x1, y1, x2, y2))
                logger.debug(f"ROI 裁剪: ({roi[0]:.2f},{roi[1]:.2f})-({roi[2]:.2f},{roi[3]:.2f})")

            # 缩放
            if img.width > self.max_width:
                ratio = self.max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize(
                    (self.max_width, new_height), Image.LANCZOS
                )
                capture_info.resized = True
                capture_info.resize_ratio = ratio
                logger.debug(f"截图缩放: {width}→{self.max_width} (ratio={ratio:.3f})")

            capture_info.image = img
            self.last_capture_info = capture_info
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

    def draw_click_marker(
        self,
        img: Image.Image,
        norm_x: float,
        norm_y: float,
        color: str = "red",
        label: str = "",
    ) -> Image.Image:
        """在截图上画出点击落点标记（用于调试坐标精度）

        Args:
            img: 截图
            norm_x, norm_y: AI 返回的归一化坐标 (0.0-1.0)
            color: 标记颜色
            label: 标注文字

        Returns:
            带标记的截图副本
        """
        from PIL import ImageDraw

        marked = img.copy()
        draw = ImageDraw.Draw(marked)
        px = int(norm_x * img.width)
        py = int(norm_y * img.height)
        r = max(8, img.width // 80)  # 标记半径

        # 十字准星
        draw.line([(px - r, py), (px + r, py)], fill=color, width=2)
        draw.line([(px, py - r), (px, py + r)], fill=color, width=2)
        # 圆圈
        draw.ellipse(
            [(px - r, py - r), (px + r, py + r)],
            outline=color,
            width=2,
        )
        # 坐标文字
        text = f"({norm_x:.2f},{norm_y:.2f})"
        if label:
            text = f"{label} {text}"
        draw.text((px + r + 4, py - 8), text, fill=color)

        return marked

    def reset(self) -> None:
        """重置帧缓存"""
        self._last_frame = None
        self.last_capture_info = None
