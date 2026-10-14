"""Призрак постройки: полупрозрачный силуэт того, что встанет под курсором.

Смысл призрака — ответить до клика на два вопроса: **что** появится и **можно
ли** его тут поставить. Поэтому рисуется настоящий спрайт здания
(`SpriteCache.building`), подкрашенный зелёным или красным, — а не абстрактный
квадратик: игрок узнаёт постройку по силуэту, как и всё остальное в игре.

Допустимость считает симуляция (`sim.can_place`), и это **та же** проверка, что
у настоящей постановки: разойтись картинке и результату клика негде. Здесь
только отрисовка — ни одного решения о правилах.

Ни одной `Surface` в кадре: спрайт призрака, рамка зоны и кольцо радиуса
кэшируются (ключ — вид здания, поворот, цвет и размер).
"""

from __future__ import annotations

import pygame

from src.game import config
from src.game.data.buildings import WORK_MOVE, building as building_def
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.render.sprites import SpriteCache

#: Во сколько раз сторона куска кэша ореола больше шага сетки: размеры кольца
#: радиуса берутся дискретно, иначе зум создавал бы новую поверхность на каждый
#: шаг колеса мыши.
RING_BUCKET = 8
#: Сторона призрака в долях тайла: как у настоящего здания (`renderer.BUILDING_SIZE`).
GHOST_SIZE = 0.86
#: Толщина рамки зоны постройки в долях тайла.
FRAME_WIDTH = 0.06
#: Кольцо радиуса постройки: толщина линии в пикселях.
RANGE_WIDTH = 2


class GhostPreview:
    """Рисует призрак постройки под курсором. Состояние читает у симуляции."""

    def __init__(self, sprites: SpriteCache) -> None:
        self.sprites = sprites
        #: Ключ — (вид, поворот, можно ли ставить, размер). Значений мало: вид
        #: здания, четыре поворота и два цвета.
        self._ghosts: dict[tuple, pygame.Surface] = {}
        self._rings: dict[tuple, pygame.Surface] = {}
        self._frames: dict[tuple, pygame.Surface] = {}
        self.drawn = 0

    # ---------------------------------------------------------- поверхности
    def ghost_sprite(self, kind: str, ok: bool, rotation: int, size: int,
                     direction: tuple[int, int] | None) -> pygame.Surface:
        """Силуэт здания в цвете «можно / нельзя». Кэшируется."""
        key = (kind, bool(ok), rotation if direction is not None else 0, size)
        sprite = self._ghosts.get(key)
        if sprite is not None:
            return sprite
        base = self.sprites.building(kind, "on", size)
        sprite = _tinted(base, theme.GHOST_OK if ok else theme.GHOST_BAD,
                         config.GHOST_ALPHA)
        # У зданий с направлением силуэт поворачивается вместе с постройкой:
        # игрок видит ту же ориентацию, что получит после клика (R).
        if direction is not None:
            sprite = pygame.transform.rotate(sprite, -90.0 * rotation)
        self._ghosts[key] = sprite
        return sprite

    def frame(self, size: int) -> pygame.Surface:
        """Тонкая рамка зоны постройки — по ней видно, какой тайл занят."""
        key = size
        surf = self._frames.get(key)
        if surf is None:
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.rect(surf, theme.with_alpha(theme.GHOST_OUTLINE, 150),
                             (0, 0, size, size),
                             max(1, int(size * FRAME_WIDTH)), border_radius=3)
            self._frames[key] = surf
        return surf

    def range_ring(self, radius_px: int) -> pygame.Surface:
        """Кольцо радиуса постройки: где заканчивается досягаемость дрона."""
        bucket = max(RING_BUCKET, int((radius_px + RING_BUCKET - 1) // RING_BUCKET
                                      * RING_BUCKET))
        surf = self._rings.get(bucket)
        if surf is None:
            side = bucket * 2 + 2
            surf = pygame.Surface((side, side), pygame.SRCALPHA)
            pygame.draw.circle(surf, theme.with_alpha(theme.GHOST_OUTLINE,
                                                      config.GHOST_RANGE_ALPHA),
                               (side // 2, side // 2), bucket, RANGE_WIDTH)
            self._rings[bucket] = surf
        return surf

    # ------------------------------------------------------------- отрисовка
    def draw(self, target: pygame.Surface, sim, camera: Camera,
             tile: tuple[int, int] | None) -> None:
        """Призрак на тайле `tile`. None — рисовать нечего (окна, край экрана)."""
        kind = sim.selected_building()
        if kind is None or tile is None:
            return
        x, y = int(tile[0]), int(tile[1])
        if not (0 <= x < sim.world.width and 0 <= y < sim.world.height):
            return
        zoom = camera.zoom
        ok = sim.can_place(kind, x, y)
        size = max(8, int(config.TILE * zoom * GHOST_SIZE))
        rotation = sim.rotation_index(x, y)
        direction = sim.ghost_direction(x, y)
        sx, sy = camera.world_to_screen(x + 0.5, y + 0.5)
        center = (int(sx), int(sy))

        sprite = self.ghost_sprite(kind, ok, rotation, size, direction)
        target.blit(self.frame(size), self.frame(size).get_rect(center=center))
        target.blit(sprite, sprite.get_rect(center=center))
        if direction is not None:
            # Шевроны конвейера — та же подсказка направления, что у настоящей
            # ленты (`renderer._draw_belt`), но цветом призрака.
            chevron = self.sprites.chevron(
                max(6, int(size * 0.34)), direction,
                theme.GHOST_OK if ok else theme.GHOST_BAD)
            target.blit(chevron, chevron.get_rect(center=center))
        self._draw_range(target, sim, camera)
        self.drawn += 1

    def _draw_range(self, target: pygame.Surface, sim, camera: Camera) -> None:
        """Кольцо досягаемости вокруг дрона, пока призрак активен.

        Без него «красный призрак» на границе радиуса выглядит поломкой: игрок
        не видит, где эта граница. Кольцо тусклое — подсказка, а не украшение,
        поэтому оно рисуется на любом уровне графики (это не свечение).
        """
        drone = sim.drone
        sx, sy = camera.world_to_screen(drone.x, drone.y)
        ring = self.range_ring(max(2, int(config.BUILD_RANGE * camera.scale)))
        target.blit(ring, ring.get_rect(center=(int(sx), int(sy))))


def _tinted(base: pygame.Surface, tint: tuple[int, int, int],
            alpha: int) -> pygame.Surface:
    """Копия спрайта, подкрашенная и полупрозрачная.

    Умножение сохраняет форму и её светотень (тёмные грани остаются тёмными), а
    второй проход по альфе делает силуэт полупрозрачным. `Surface.set_alpha` для
    поверхностей с попиксельной альфой ненадёжен, поэтому альфа вписывается в
    сами пиксели.
    """
    surf = base.copy()
    surf.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
    surf.fill((255, 255, 255, max(0, min(255, alpha))),
              special_flags=pygame.BLEND_RGBA_MULT)
    return surf
