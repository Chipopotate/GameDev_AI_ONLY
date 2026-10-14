"""Процедурные значки предметов и свойств (plan/09 §7).

Значок предмета — **тот же спрайт, что и в мире**: природный ресурс рисуется
генератором огранок (`render/sprites/gems.py`), крафченый предмет роли — стальным
жетоном. Поэтому камень в инвентаре выглядит как камень на земле, и игрок узнаёт
ресурс по силуэту, а не по подписи.

Значки свойств остаются простыми формами: свойство — это «язык» из шести знаков
(plan/02), он не рандомизируется и должен читаться мгновенно.

Отдельно кэшируется слот «?» — свойство, которое игрок ещё не открыл.

Один `SpriteCache` на всю игру: значки берутся из него, поэтому ни спрайт мира,
ни значок интерфейса не рисуются дважды.
"""

from __future__ import annotations

import pygame

from src.game.data.resources import PROP_ICONS, ItemDef, ItemTable, Prop
from src.game.render import shapes, theme
from src.game.render.sprites import SpriteCache
from src.game.ui.fonts import get_font

ICON_SIZE = 24
#: Размер значка свойства в узких местах интерфейса (план/09 §7 — 16 и 24 px).
PROP_ICON_SMALL = 16
UNKNOWN_BG = (44, 48, 60)
UNKNOWN_FG = (150, 158, 178)
#: Кайма редкого ресурса: единственное, что отличает его значок от обычного.
RARE_RING = (255, 240, 180)


def _new_surface(size: int = ICON_SIZE) -> pygame.Surface:
    return pygame.Surface((size, size), pygame.SRCALPHA)


def _maybe_convert(surf: pygame.Surface) -> pygame.Surface:
    """convert_alpha ускоряет блит, но требует инициализированного дисплея."""
    if pygame.display.get_surface() is not None:
        return surf.convert_alpha()
    return surf


class IconCache:
    """Кэш значков. Один Surface на предмет/свойство, создаётся лениво."""

    def __init__(self, items: ItemTable | None = None, size: int = ICON_SIZE,
                 sprites: SpriteCache | None = None) -> None:
        self.items = items
        self.size = int(size)
        # Своя фабрика — только если её не дали снаружи: значок обязан быть тем
        # же спрайтом, что и в мире, а значит жить в общем кэше.
        self.sprites = sprites or (SpriteCache(items) if items is not None else None)
        self._items: dict[str, pygame.Surface] = {}
        self._scaled: dict[tuple[str, int], pygame.Surface] = {}
        self._props: dict[tuple[Prop, int], pygame.Surface] = {}
        self._marks: dict[tuple[str, tuple[int, int, int]], pygame.Surface] = {}
        self._unknown: pygame.Surface | None = None

    # ------------------------------------------------------------ предметы
    def item_icon(self, item_id: str) -> pygame.Surface:
        """Значок предмета забега: природного ресурса или крафченого продукта."""
        surf = self._items.get(item_id)
        if surf is None:
            surf = self._make_item(self._def_of(item_id), self.size)
            self._items[item_id] = surf
        return surf

    def item_icon_scaled(self, item_id: str, size: int) -> pygame.Surface:
        """Тот же значок другого размера (слоты, предметы на земле).

        Спрайт **перерисовывается в нужном размере**, а не растягивается из
        24 px: у фабрики есть увеличенный холст и сглаживание, поэтому мелкий
        значок на земле остаётся чётким. Размеры приходят из раскладки, поэтому
        кэш не растёт.
        """
        size = max(4, int(size))
        if size == self.size:
            return self.item_icon(item_id)
        key = (item_id, size)
        surf = self._scaled.get(key)
        if surf is None:
            surf = self._make_item(self._def_of(item_id), size)
            self._scaled[key] = surf
        return surf

    def _make_item(self, item: ItemDef, size: int) -> pygame.Surface:
        """Предмет рисует спрайтовый кэш: огранка ресурса или жетон крафта.

        Копия делается только ради редкого ресурса — один раз на размер, не в
        кадре (спрайт из фабрики трогать нельзя: он общий с миром).
        """
        assert self.sprites is not None
        if item.crafted:
            # Сделанное — деталь, а не камень: правило plan/09 §1 работает и в инвентаре.
            return self.sprites.chip(item.id, size, item.color)
        surf = self.sprites.gem(item.id, size, item.color, glow=bool(item.props))
        if not item.rare:
            return surf
        # Редкий ресурс один на забег: его значок обведён, чтобы не потерялся.
        out = surf.copy()
        pygame.draw.circle(out, RARE_RING, (size // 2, size // 2),
                           int(size * 0.46), max(1, int(size * 0.07)))
        return out

    # ------------------------------------------------------------ свойства
    def prop_icon(self, prop: Prop, size: int = ICON_SIZE) -> pygame.Surface:
        """Значок свойства: форма закреплена в данных, размер выбирает интерфейс."""
        size = max(8, int(size))
        key = (prop, size)
        surf = self._props.get(key)
        if surf is None:
            shape, color = PROP_ICONS[prop]
            surf = _maybe_convert(self._draw_shape(shape, color, size))
            self._props[key] = surf
        return surf

    def unknown_icon(self) -> pygame.Surface:
        """Слот `?` — свойство, которого игрок ещё не знает."""
        if self._unknown is None:
            surf = _new_surface(self.size)
            c = self.size / 2.0
            pygame.draw.rect(surf, UNKNOWN_BG,
                             (2, 2, self.size - 4, self.size - 4), border_radius=4)
            pygame.draw.rect(surf, theme.UI_BORDER,
                             (2, 2, self.size - 4, self.size - 4), 1, border_radius=4)
            glyph = get_font(int(self.size * 0.7)).render("?", True, UNKNOWN_FG)
            surf.blit(glyph, glyph.get_rect(center=(int(c), int(c))))
            self._unknown = _maybe_convert(surf)
        return self._unknown

    def mark_icon(self, text: str, color: tuple[int, int, int]) -> pygame.Surface:
        """Маленькая метка над объектом в мире (например, «?» над реликвией).

        Кэшируется по (текст, цвет): шрифт и рендер текста не должны попадать
        в кадр (plan/07 §4).
        """
        key = (text, color)
        surf = self._marks.get(key)
        if surf is None:
            font = get_font(int(self.size * 0.6))
            glyph = font.render(text, True, color)
            surf = pygame.Surface(glyph.get_size(), pygame.SRCALPHA)
            # Тёмная обводка: метка должна читаться и на свету, и в темноте.
            shadow = font.render(text, True, theme.OUTLINE)
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                surf.blit(shadow, (ox, oy))
            surf.blit(glyph, (0, 0))
            surf = _maybe_convert(surf)
            self._marks[key] = surf
        return surf

    # ---------------------------------------------------------- внутреннее
    def _def_of(self, item_id: str) -> ItemDef:
        if self.items is not None:
            return self.items[item_id]
        raise KeyError(f"нет таблицы предметов для значка {item_id!r}")

    def _draw_shape(self, shape: str, color, size: int) -> pygame.Surface:
        """Значок свойства: тёмный контур и заливка — как у всех фигур в игре."""
        surf = _new_surface(size)
        c = size / 2.0
        shapes.draw_shape(surf, shape, theme.OUTLINE, (c, c), size * 0.44)
        shapes.draw_shape(surf, shape, color, (c, c), size * 0.34)
        return surf

    def clear(self) -> None:
        self._items.clear()
        self._scaled.clear()
        self._props.clear()
        self._marks.clear()
        self._unknown = None
