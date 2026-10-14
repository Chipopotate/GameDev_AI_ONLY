"""Атмосфера кадра: виньетка и цветокоррекция по фазе суток (plan/09 §9).

Два полноэкранных слоя, оба кэшируются по размеру окна и по ступени фазы:

- **виньетка** — мягкое затемнение к краям экрана; она собирает взгляд в центр
  и заодно скрывает край карты, где чанков уже нет;
- **цветокоррекция** — день «чуть холоднее и светлее», ночь «тёмная и синяя».
  Ночь здесь именно *синяя*, а не чёрная: чёрную тьму уже даёт слой тьмы,
  а цвет даёт разницу между «сумерками» и «глухой ночью».

Слои блитятся целым экраном, поэтому ступени дискретны (🔧 12 на фазу):
пересобирать виньетку каждый кадр было бы дороже, чем она экономит.
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.render import quality, theme

#: Сколько ступеней фазы суток кэшируется. 12 ступеней — шаг ~8% тьмы,
#: глаз не видит перехода, а пересборка слоя идёт 12 раз за цикл, не за кадр.
PHASE_STEPS = 12
#: Сколько ступеней плотности виньетки кэшируется.
VIGNETTE_STEPS = 5
#: Плотность ночной цветокоррекции в глухую ночь, 0..255. Подобрана замером:
#: затемняет ночь слой тьмы, а этот слой отвечает только за синеву, и слишком
#: плотный он убивает цвет источника — тёплый свет лампы переставал читаться
#: тёплым (`test_drone_light_is_cool_and_lamp_light_is_warm`). При 46 ночной
#: кадр светлел до 0.63 от дневного, то есть «ночь тёмная» переставало быть
#: правдой (`test_night_darkens_the_frame`); 38 даёт 0.58.
TINT_MAX_ALPHA = 38


class PostFX:
    """Виньетка и цветокоррекция. Состояние симуляции только читает."""

    def __init__(self) -> None:
        self._vignette: dict[tuple[int, int, int], pygame.Surface] = {}
        self._tints: dict[tuple[int, int, int, tuple[int, int, int]], pygame.Surface] = {}
        self.vignettes_made = 0
        self.tints_made = 0

    # ---------------------------------------------------------- виньетка
    def vignette(self, size: tuple[int, int], strength: float) -> pygame.Surface:
        """Затемнение к краям экрана. Собирается один раз на размер и ступень."""
        step = max(1, min(VIGNETTE_STEPS, int(round(strength * VIGNETTE_STEPS))))
        key = (size[0], size[1], step)
        surf = self._vignette.get(key)
        if surf is None:
            surf = _build_vignette(size, step / VIGNETTE_STEPS,
                                   config.VIGNETTE_STRENGTH)
            self._vignette[key] = surf
            self.vignettes_made += 1
        return surf

    def tint(self, size: tuple[int, int], color, strength: float) -> pygame.Surface:
        """Одноцветный слой для цветокоррекции: свой на цвет и ступень."""
        step = max(1, min(PHASE_STEPS, int(round(strength * PHASE_STEPS))))
        key = (size[0], size[1], step, tuple(color))
        surf = self._tints.get(key)
        if surf is None:
            alpha = max(1, int(TINT_MAX_ALPHA * step / PHASE_STEPS))
            surf = pygame.Surface(size, pygame.SRCALPHA)
            surf.fill(theme.with_alpha(color, alpha))
            self._tints[key] = surf
            self.tints_made += 1
        return surf

    # ------------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, sim, sky: float) -> None:
        """Атмосфера поверх мира, но под интерфейсом.

        `sim` читается только ради тьмы (фазы суток и уровня освещённости);
        `sky` — 0 днём, 1 в глухую ночь, ту же величину знает симуляция.
        """
        size = target.get_size()
        if quality.grade_enabled() and sky > 0.02:
            # Ночь: синий оттенок поверх тьмы. День не трогаем цветом —
            # дневная картинка и так холодная (plan/09 §1).
            target.blit(self.tint(size, theme.NIGHT_TINT, sky), (0, 0))
        if quality.vignette_enabled():
            strength = 0.55 + 0.45 * sky
            target.blit(self.vignette(size, strength), (0, 0))

    def clear(self) -> None:
        self._vignette.clear()
        self._tints.clear()


def _build_vignette(size: tuple[int, int], k: float, max_strength: float) -> pygame.Surface:
    """Радиальная виньетка: прозрачно в центре, темно по краям.

    Строится через маленькую картинку и `smoothscale` — так край получается
    мягким за одну операцию, без попиксельного расчёта на весь экран.
    """
    import numpy as np

    small_h, small_w = 24, 32
    little = pygame.Surface((small_w, small_h), pygame.SRCALPHA)
    yy, xx = np.mgrid[0:small_h, 0:small_w].astype(np.float32)
    nx = (xx + 0.5) / small_w * 2.0 - 1.0
    ny = (yy + 0.5) / small_h * 2.0 - 1.0
    dist = np.sqrt(nx * nx + ny * ny) / 1.4142
    alpha = np.clip((dist - 0.35) / 0.65, 0.0, 1.0) ** 1.8
    alpha = (alpha * 255.0 * max_strength * k).astype(np.uint8)
    rgb = pygame.surfarray.pixels3d(little)
    pix_alpha = pygame.surfarray.pixels_alpha(little)
    for ch in range(3):
        rgb[..., ch] = theme.VIGNETTE[ch]
    pix_alpha[:] = alpha.T
    del rgb, pix_alpha
    return pygame.transform.smoothscale(little, size)
