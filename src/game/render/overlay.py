"""Слой поверх мира: 1 пиксель на тайл, растянутый на экран.

Так рисуются тьма (приём из plan/07 §5) и отладочный слой света: мелкая
картинка размером с видимую область в тайлах, растянутая на экран. Мягкий
край тьмы получается бесплатно — из `smoothscale`.

Surface-ы живут здесь и переживают кадры: каждый кадр только перезаписываются
пиксели и растягивается уже готовый буфер (`dest_surface`), без аллокаций.
"""

from __future__ import annotations

import numpy as np
import pygame

from src.game.render.camera import Camera


class TileOverlay:
    """Блитит потайловый RGBA-слой, растягивая его под текущий зум."""

    def __init__(self) -> None:
        self._tiles: pygame.Surface | None = None
        self._tiles_size = (0, 0)
        self._scaled: pygame.Surface | None = None
        self._scaled_size = (0, 0)
        self.draws = 0  # для отладочной панели

    def _tile_surface(self, w: int, h: int) -> pygame.Surface:
        if self._tiles is None or self._tiles_size != (w, h):
            self._tiles = pygame.Surface((w, h), pygame.SRCALPHA)
            self._tiles_size = (w, h)
        return self._tiles

    def _scaled_surface(self, w: int, h: int) -> pygame.Surface:
        if self._scaled is None or self._scaled_size != (w, h):
            self._scaled = pygame.Surface((w, h), pygame.SRCALPHA)
            self._scaled_size = (w, h)
        return self._scaled

    def draw(self, target: pygame.Surface, camera: Camera, x0: int, y0: int,
             rgba: np.ndarray, smooth: bool = True) -> None:
        """Рисует слой. `rgba` — (h, w, 4) uint8, тайл (x0, y0) — его левый верх."""
        h, w = rgba.shape[:2]
        if w <= 0 or h <= 0:
            return
        small = self._tile_surface(w, h)
        rgb = pygame.surfarray.pixels3d(small)
        alpha = pygame.surfarray.pixels_alpha(small)
        # surfarray адресует [x][y], а массив — [y][x], отсюда транспонирование.
        rgb[:] = rgba[:, :, :3].transpose(1, 0, 2)
        alpha[:] = rgba[:, :, 3].T
        del rgb, alpha

        scale = camera.scale
        size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
        dest = self._scaled_surface(*size)
        if smooth:
            pygame.transform.smoothscale(small, size, dest)
        else:
            pygame.transform.scale(small, size, dest)
        self.draws += 1
        sx, sy = camera.world_to_screen(x0, y0)
        target.blit(dest, (int(sx), int(sy)))
