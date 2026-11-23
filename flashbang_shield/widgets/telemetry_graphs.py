"""High-performance 60 FPS double-buffered telemetry oscilloscope & transfer curve widgets."""

from __future__ import annotations

import math
import time
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPaintEvent,
    QResizeEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from flashbang_shield.gui_bridge import TelemetrySample


class OscilloscopeWidget(QWidget):
    """Dual-channel 60 FPS scrolling oscilloscope for screen luminance and dimming alpha.

    Channels:
      - Cyan (#38BDF8): Raw normalized screen luminance [0.0..1.0]
      - Amber (#F59E0B): Active overlay dimming suppression [0.0..1.0]
      - Dashed Emerald (#10B981): User comfort threshold (target_luminance)
      - Crimson Pulse (#EF4444): Reflex shock clamp indicator (decays smoothly over 450 ms)
    """

    COLOR_BG = QColor(18, 20, 26, 235)
    COLOR_GRID = QColor(255, 255, 255, 16)
    COLOR_AXIS_TEXT = QColor(148, 163, 184, 180)
    COLOR_LUMA = QColor(56, 189, 248)        # #38BDF8 Cyan
    COLOR_ALPHA = QColor(245, 158, 11)       # #F59E0B Visor Amber
    COLOR_TARGET = QColor(16, 185, 129)      # #10B981 Emerald
    COLOR_FLASH = QColor(239, 68, 68)        # #EF4444 Crimson

    PADDING_LEFT = 42
    PADDING_RIGHT = 14
    PADDING_TOP = 34
    PADDING_BOTTOM = 24

    def __init__(self, max_points: int = 360, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._max_points = max_points
        self._samples: List[TelemetrySample] = []
        self._latest: Optional[TelemetrySample] = None
        self._flash_trigger_ts: float = 0.0
        self._grid_cache: Optional[QPixmap] = None

        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def update_data(
        self,
        latest: TelemetrySample,
        samples: List[TelemetrySample],
        flash_trigger_ts: float,
    ) -> None:
        """Accept a fresh ring-buffer snapshot and schedule an immediate repaint."""
        self._latest = latest
        self._samples = samples[-self._max_points :]
        self._flash_trigger_ts = flash_trigger_ts
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rebuild_grid_cache()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.PADDING_LEFT,
            self.PADDING_TOP,
            max(10.0, self.width() - self.PADDING_LEFT - self.PADDING_RIGHT),
            max(10.0, self.height() - self.PADDING_TOP - self.PADDING_BOTTOM),
        )

    def _rebuild_grid_cache(self) -> None:
        """Pre-render static background, rounded frame, grid lines, and Y-axis ticks."""
        w, h = max(1, self.width()), max(1, self.height())
        dpr = self.devicePixelRatio()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Rounded obsidian container
        outer = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        p.setBrush(self.COLOR_BG)
        p.drawRoundedRect(outer, 10.0, 10.0)

        rect = self._plot_rect()
        grid_pen = QPen(self.COLOR_GRID, 1.0)
        p.setPen(grid_pen)

        axis_font = QFont("Segoe UI", 8)
        p.setFont(axis_font)

        # Horizontal grid lines (0%, 25%, 50%, 75%, 100%)
        for step in range(5):
            frac = step / 4.0
            y = rect.bottom() - frac * rect.height()
            p.setPen(grid_pen)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(self.COLOR_AXIS_TEXT)
            p.drawText(
                QRectF(4.0, y - 8.0, self.PADDING_LEFT - 10.0, 16.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{int(frac * 100)}%",
            )

        # Vertical time grid lines (6 columns)
        p.setPen(grid_pen)
        for col in range(1, 6):
            x = rect.left() + (col / 6.0) * rect.width()
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        # Subtle static legend in top-left header
        legend_y = 17.0
        legend_items = [
            (self.COLOR_LUMA, "Raw Luma"),
            (self.COLOR_ALPHA, "Dimming"),
            (self.COLOR_TARGET, "Target"),
        ]
        cursor_x = float(self.PADDING_LEFT)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        for color, label in legend_items:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(cursor_x, legend_y), 3.5, 3.5)
            p.setPen(QColor(203, 213, 225))
            p.drawText(QPointF(cursor_x + 8.0, legend_y + 3.5), label)
            cursor_x += 78.0

        p.end()
        self._grid_cache = pm

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._grid_cache is None:
            self._rebuild_grid_cache()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._grid_cache is not None:
            painter.drawPixmap(0, 0, self._grid_cache)

        rect = self._plot_rect()
        now = time.perf_counter()

        # 1. Shock pulse visual feedback (decays smoothly over 450 ms)
        flash_age = now - self._flash_trigger_ts
        if 0.0 <= flash_age < 0.45:
            intensity = (1.0 - flash_age / 0.45) ** 1.6
            flash_border = QColor(self.COLOR_FLASH)
            flash_border.setAlphaF(0.85 * intensity)
            flash_fill = QColor(self.COLOR_FLASH)
            flash_fill.setAlphaF(0.14 * intensity)
            painter.setPen(QPen(flash_border, 2.0))
            painter.setBrush(flash_fill)
            painter.drawRoundedRect(QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0), 10.0, 10.0)

        samples = self._samples
        latest = self._latest
        if len(samples) >= 2:
            painter.save()
            painter.setClipRect(rect.adjusted(-1.0, -2.0, 1.0, 2.0))

            n = len(samples)
            x_step = rect.width() / max(1, self._max_points - 1)
            start_x = rect.right() - (n - 1) * x_step

            luma_path = QPainterPath()
            alpha_path = QPainterPath()
            spike_points: List[QPointF] = []

            for i, s in enumerate(samples):
                x = start_x + i * x_step
                y_luma = rect.bottom() - s.luminance * rect.height()
                y_alpha = rect.bottom() - s.overlay_alpha * rect.height()
                if i == 0:
                    luma_path.moveTo(x, y_luma)
                    alpha_path.moveTo(x, y_alpha)
                else:
                    luma_path.lineTo(x, y_luma)
                    alpha_path.lineTo(x, y_alpha)
                if s.is_flashbang:
                    spike_points.append(QPointF(x, y_luma))

            # Translucent area fills under curves
            self._draw_area_fill(painter, luma_path, start_x, rect, self.COLOR_LUMA, 0.22)
            self._draw_area_fill(painter, alpha_path, start_x, rect, self.COLOR_ALPHA, 0.20)

            # Stroke primary waveforms
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.COLOR_LUMA, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(luma_path)

            painter.setPen(QPen(self.COLOR_ALPHA, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(alpha_path)

            # Transient flashbang spike markers
            if spike_points:
                painter.setPen(QPen(QColor(255, 255, 255, 210), 1.0))
                painter.setBrush(self.COLOR_FLASH)
                for pt in spike_points[-12:]:
                    painter.drawEllipse(pt, 3.0, 3.0)

            painter.restore()

        # 2. Comfort target threshold dashed line
        if latest is not None:
            y_target = rect.bottom() - latest.target_luminance * rect.height()
            target_pen = QPen(self.COLOR_TARGET, 1.4, Qt.PenStyle.DashLine)
            target_pen.setDashPattern([5.0, 4.0])
            painter.setPen(target_pen)
            painter.drawLine(QPointF(rect.left(), y_target), QPointF(rect.right(), y_target))

            # 3. Live numeric readout in top-right header
            badge_font = QFont("Cascadia Mono", 8, QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            status_text = (
                f"LUMA {latest.luminance * 100:4.0f}%  │  "
                f"DIM {latest.overlay_alpha * 100:4.0f}%  │  "
                f"{latest.fps:4.1f} FPS"
            )
            painter.setPen(QColor(226, 232, 240))
            painter.drawText(
                QRectF(self.width() * 0.42, 6.0, self.width() * 0.58 - 14.0, 22.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                status_text,
            )

        painter.end()

    @staticmethod
    def _draw_area_fill(
        painter: QPainter,
        line_path: QPainterPath,
        start_x: float,
        rect: QRectF,
        base_color: QColor,
        top_alpha: float,
    ) -> None:
        fill_path = QPainterPath(line_path)
        fill_path.lineTo(rect.right(), rect.bottom())
        fill_path.lineTo(start_x, rect.bottom())
        fill_path.closeSubpath()

        grad = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        top = QColor(base_color)
        top.setAlphaF(top_alpha)
        bottom = QColor(base_color)
        bottom.setAlphaF(0.0)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPath(fill_path)


class SoftplusCurveWidget(QWidget):
    """Interactive Log-Softplus dynamic range compression curve visualizer.

    Plots:
        D(L) = min(D_max, k * ln(1 + exp((L - L_target) / k)))
    and superimposes the real-time operating point (L_current, D_current).
    """

    SOFTNESS_K = 0.08

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._target: float = 0.38
        self._max_dimming: float = 0.80
        self._live_luma: float = 0.0
        self._live_alpha: float = 0.0
        self.setMinimumSize(210, 175)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_parameters(self, target_brightness: float, max_dimming: float) -> None:
        self._target = max(0.05, min(0.95, target_brightness))
        self._max_dimming = max(0.10, min(0.98, max_dimming))
        self.update()

    def set_live_point(self, luminance: float, alpha: float) -> None:
        self._live_luma = max(0.0, min(1.0, luminance))
        self._live_alpha = max(0.0, min(1.0, alpha))
        self.update()

    @classmethod
    def evaluate_softplus(cls, luma: float, target: float, max_dim: float) -> float:
        """Numerically stable Log-Softplus evaluation."""
        k = cls.SOFTNESS_K
        z = (luma - target) / k
        if z > 20.0:
            raw = k * z
        elif z < -20.0:
            raw = 0.0
        else:
            raw = k * math.log1p(math.exp(z))
        return min(max_dim, raw)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        p.setBrush(QColor(18, 20, 26, 225))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 10.0, 10.0)

        rect = QRectF(36.0, 26.0, max(20.0, w - 50.0), max(20.0, h - 52.0))

        # Title
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.setPen(QColor(203, 213, 225))
        p.drawText(QRectF(12.0, 6.0, w - 24.0, 16.0), Qt.AlignmentFlag.AlignLeft, "Log-Softplus Transfer Curve")

        # Subtle axes & cap reference
        p.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        p.drawRect(rect)

        # Max dimming ceiling line
        y_cap = rect.bottom() - self._max_dimming * rect.height()
        cap_pen = QPen(QColor(245, 158, 11, 110), 1.0, Qt.PenStyle.DotLine)
        p.setPen(cap_pen)
        p.drawLine(QPointF(rect.left(), y_cap), QPointF(rect.right(), y_cap))

        # Target threshold vertical knee marker
        x_target = rect.left() + self._target * rect.width()
        knee_pen = QPen(QColor(16, 185, 129, 140), 1.1, Qt.PenStyle.DashLine)
        p.setPen(knee_pen)
        p.drawLine(QPointF(x_target, rect.top()), QPointF(x_target, rect.bottom()))

        # Transfer curve polyline
        curve = QPainterPath()
        steps = 64
        for i in range(steps + 1):
            luma = i / steps
            dim = self.evaluate_softplus(luma, self._target, self._max_dimming)
            px = rect.left() + luma * rect.width()
            py = rect.bottom() - dim * rect.height()
            if i == 0:
                curve.moveTo(px, py)
            else:
                curve.lineTo(px, py)

        p.setPen(QPen(QColor(245, 158, 11), 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(curve)

        # Live operating point glow
        live_x = rect.left() + self._live_luma * rect.width()
        live_y = rect.bottom() - self._live_alpha * rect.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(56, 189, 248, 70))
        p.drawEllipse(QPointF(live_x, live_y), 7.5, 7.5)
        p.setPen(QPen(QColor(255, 255, 255), 1.4))
        p.setBrush(QColor(56, 189, 248))
        p.drawEllipse(QPointF(live_x, live_y), 3.6, 3.6)

        # Axis captions
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor(148, 163, 184))
        p.drawText(QRectF(rect.left(), rect.bottom() + 4.0, rect.width(), 16.0), Qt.AlignmentFlag.AlignCenter, "Input Luminance (L)")
        p.drawText(QRectF(4.0, rect.top(), 28.0, 14.0), Qt.AlignmentFlag.AlignRight, "100%")
        p.drawText(QRectF(4.0, rect.bottom() - 12.0, 28.0, 14.0), Qt.AlignmentFlag.AlignRight, "0%")
        p.end()
