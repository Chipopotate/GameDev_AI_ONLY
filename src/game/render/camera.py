"""Камера: мировые тайловые координаты ↔ экранные пиксели."""

from __future__ import annotations

import math

from src.game import config


class Camera:
    """Камера смотрит в точку (x, y) в тайлах. Масштаб — колёсиком.

    Пикселей на тайл = `config.TILE * zoom`, поэтому зум не влияет на логику.
    """

    def __init__(self, x: float, y: float, viewport_w: int, viewport_h: int,
                 tile: int = config.TILE, zoom: float = config.ZOOM_DEFAULT) -> None:
        self.x = float(x)
        self.y = float(y)
        self.viewport_w = int(viewport_w)
        self.viewport_h = int(viewport_h)
        self.tile = int(tile)
        self.zoom = float(zoom)

    # ------------------------------------------------------------- свойства
    @property
    def scale(self) -> float:
        """Пикселей на тайл с учётом зума."""
        return self.tile * self.zoom

    @property
    def half_extent(self) -> tuple[float, float]:
        """Половина видимой области в тайлах."""
        return (self.viewport_w / (2.0 * self.scale),
                self.viewport_h / (2.0 * self.scale))

    # ------------------------------------------------------------- движение
    def follow(self, tx: float, ty: float, dt: float) -> None:
        """Плавно подтягивается к цели."""
        k = min(1.0, config.CAMERA_SMOOTH * max(dt, 0.0))
        self.x += (tx - self.x) * k
        self.y += (ty - self.y) * k

    def snap_to(self, tx: float, ty: float) -> None:
        self.x, self.y = float(tx), float(ty)

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(config.ZOOM_MIN, min(config.ZOOM_MAX, float(zoom)))

    def zoom_by(self, steps: float) -> None:
        self.set_zoom(self.zoom + steps * config.ZOOM_STEP)

    def clamp_to(self, world_w: int, world_h: int) -> None:
        """Не выпускает камеру за пределы карты (если карта больше экрана)."""
        hw, hh = self.half_extent
        if world_w <= hw * 2:
            self.x = world_w / 2.0
        else:
            self.x = max(hw, min(world_w - hw, self.x))
        if world_h <= hh * 2:
            self.y = world_h / 2.0
        else:
            self.y = max(hh, min(world_h - hh, self.y))

    # ---------------------------------------------------------- координаты
    def world_to_screen(self, wx: float, wy: float) -> tuple[float, float]:
        s = self.scale
        return ((wx - self.x) * s + self.viewport_w / 2.0,
                (wy - self.y) * s + self.viewport_h / 2.0)

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        s = self.scale
        return ((sx - self.viewport_w / 2.0) / s + self.x,
                (sy - self.viewport_h / 2.0) / s + self.y)

    def visible_tiles(self, margin: int = 1) -> tuple[int, int, int, int]:
        """Включающий диапазон тайлов (x0, y0, x1, y1) в кадре."""
        hw, hh = self.half_extent
        x0 = int(math.floor(self.x - hw)) - margin
        y0 = int(math.floor(self.y - hh)) - margin
        x1 = int(math.ceil(self.x + hw)) + margin
        y1 = int(math.ceil(self.y + hh)) + margin
        return x0, y0, x1, y1
