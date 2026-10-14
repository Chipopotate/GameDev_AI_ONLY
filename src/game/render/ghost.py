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
from src.game.render import theme
from src.game.render.camera import Camera
from src.game.render.sprites import SpriteCache

#: Сторона призрака в долях тайла: как у настоящего здания (`renderer.BUILDING_SIZE`).
GHOST_SIZE = 0.86
#: Толщина рамки зоны постройки в долях тайла.
FRAME_WIDTH = 0.06
#: Рамка и заливка зоны — цветом «можно / нельзя»: спрайты зданий тёмные, и
#: одна подкраска силуэта на тёмной земле читалась как «тёмный квадрат», а не
#: как зелёный или красный. Альфа рамки и заливки, 0..255.
FRAME_ALPHA = 210
FILL_ALPHA = 44
#: Подсветка силуэта цветом статуса (доля цвета, прибавляемая к пикселям):
#: без неё тёмные грани после умножения на цвет оставались почти чёрными.
GHOST_LIFT = 0.35
#: Шевроны направления у призрака — как у ленты (`renderer.CHEVRONS`,
#: `CHEVRON_SPAN`): та же подсказка, что игрок видит на поставленной ленте.
CHEVRONS = 3
CHEVRON_SPAN = 0.62
CHEVRON_SIZE = 0.34
#: Кольцо радиуса постройки: толщина линии в пикселях. Когда призрак за
#: кольцом, оно ярче и красное — видно, **почему** нельзя.
RANGE_WIDTH = 2
RANGE_FAR_ALPHA = 140


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

    def frame(self, size: int, ok: bool = True, outline: bool = True) -> pygame.Surface:
        """Зона постройки цветом «можно / нельзя»: лёгкая заливка тайла или рамка.

        Заливка ложится **под** силуэт, тонкая рамка — **поверх** него
        (plan/09): по рамке видно, какой тайл займёт здание, а по её цвету —
        встанет ли оно, даже когда сам силуэт мелкий (лампа) или тёмный (склад).
        """
        key = (size, bool(ok), bool(outline))
        surf = self._frames.get(key)
        if surf is None:
            tint = theme.GHOST_OK if ok else theme.GHOST_BAD
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            if outline:
                pygame.draw.rect(surf, theme.with_alpha(tint, FRAME_ALPHA),
                                 (0, 0, size, size),
                                 max(1, int(size * FRAME_WIDTH)), border_radius=3)
            else:
                pygame.draw.rect(surf, theme.with_alpha(tint, FILL_ALPHA),
                                 (0, 0, size, size), border_radius=3)
            self._frames[key] = surf
        return surf

    def range_ring(self, radius_px: int, far: bool = False) -> pygame.Surface:
        """Кольцо радиуса постройки: где заканчивается досягаемость дрона.

        Радиус точный: кольцо объясняет, почему призрак красный, и не должно
        врать о границе. Зум дискретный (шаг 🔧 `ZOOM_STEP`), поэтому разных
        радиусов в кэше немного. `far` — призрак за кольцом: тогда оно красное
        и заметнее, иначе тусклое.
        """
        radius = max(2, int(radius_px))
        key = (radius, bool(far))
        surf = self._rings.get(key)
        if surf is None:
            side = radius * 2 + RANGE_WIDTH * 2
            surf = pygame.Surface((side, side), pygame.SRCALPHA)
            color = theme.with_alpha(theme.GHOST_BAD, RANGE_FAR_ALPHA) if far else \
                theme.with_alpha(theme.GHOST_OUTLINE, config.GHOST_RANGE_ALPHA)
            pygame.draw.circle(surf, color, (side // 2, side // 2), radius, RANGE_WIDTH)
            self._rings[key] = surf
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
        fill = self.frame(size, ok, outline=False)
        target.blit(fill, fill.get_rect(center=center))
        target.blit(sprite, sprite.get_rect(center=center))
        if direction is not None:
            self._draw_chevrons(target, center, size, direction, ok)
        frame = self.frame(size, ok)
        target.blit(frame, frame.get_rect(center=center))
        self._draw_range(target, sim, camera, far=not sim.in_build_range(x, y))
        self.drawn += 1

    def _draw_chevrons(self, target: pygame.Surface, center: tuple[int, int],
                       size: int, direction: tuple[int, int], ok: bool) -> None:
        """Шевроны направления — как три шеврона на настоящей ленте
        (`renderer._draw_belt`), но неподвижные и светлым цветом призрака:
        один маленький шеврон в центре на обычном зуме не читался.
        """
        color = theme.lighten(theme.GHOST_OK if ok else theme.GHOST_BAD, 0.35)
        chevron = self.sprites.chevron(max(6, int(size * CHEVRON_SIZE)), direction, color)
        for i in range(CHEVRONS):
            along = ((i + 0.5) / CHEVRONS - 0.5) * size * CHEVRON_SPAN
            target.blit(chevron, chevron.get_rect(center=(
                int(center[0] + along * direction[0]),
                int(center[1] + along * direction[1]))))

    def _draw_range(self, target: pygame.Surface, sim, camera: Camera,
                    far: bool = False) -> None:
        """Кольцо досягаемости вокруг дрона, пока призрак активен.

        Без него «красный призрак» на границе радиуса выглядит поломкой: игрок
        не видит, где эта граница. Кольцо тусклое — подсказка, а не украшение,
        поэтому оно рисуется на любом уровне графики (это не свечение).
        """
        drone = sim.drone
        sx, sy = camera.world_to_screen(drone.x, drone.y)
        ring = self.range_ring(max(2, int(config.BUILD_RANGE * camera.scale)), far)
        target.blit(ring, ring.get_rect(center=(int(sx), int(sy))))


def _tinted(base: pygame.Surface, tint: tuple[int, int, int],
            alpha: int) -> pygame.Surface:
    """Копия спрайта, подкрашенная и полупрозрачная.

    Умножение сохраняет форму и её светотень, прибавка цвета статуса
    поднимает тёмные грани (иначе тёмный спрайт оставался тёмным пятном), а
    последний проход по альфе делает силуэт полупрозрачным. `Surface.set_alpha` для
    поверхностей с попиксельной альфой ненадёжен, поэтому альфа вписывается в
    сами пиксели.
    """
    surf = base.copy()
    surf.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
    # Подъём цветом статуса: только RGB, альфа (форма силуэта) не меняется.
    surf.fill(theme.scale(tint, GHOST_LIFT), special_flags=pygame.BLEND_RGB_ADD)
    surf.fill((255, 255, 255, max(0, min(255, alpha))),
              special_flags=pygame.BLEND_RGBA_MULT)
    return surf
