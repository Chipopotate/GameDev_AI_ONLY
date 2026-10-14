"""Галерея спрайтов (plan/09 §12): весь «язык форм» на одном экране.

Зачем она нужна. Проверять графику по кадру игры неудобно: ресурсы раскиданы по
карте, враги ещё не подключены, а состояния зданий видно только когда здание
работает. Галерея собирает **все** спрайты в одну сетку с подписями — по ней
видно, различаются ли силуэты ресурсов, читаются ли состояния построек и нет ли
среди камней двух одинаковых.

Собирается один раз на размер холста и кэшируется целиком: спрайты берутся из
общего `SpriteCache`, подписи рисуются при сборке, а в кадре остаётся один
`blit`. Игра открывает галерею по F4 (`app.py`), инструмент скриншотов зовёт её
же (`tools/screenshot.py`) — поэтому она живёт в `render/`, а не в `ui/`.

Холст подбирается по месту: сперва пробуется увеличение 🔧 3× (как в plan/09
§12), и если сетка не влезает — размер ячейки уменьшается. Поэтому скриншот
рендерится на холсте повыше и показывает крупную сетку, а окно игры — ту же
галерею, ужатую по высоте.
"""

from __future__ import annotations

from collections.abc import Callable

import pygame

from src.game.data import buildings as building_data
from src.game.data.resources import PROP_NAMES, Prop
from src.game.render import theme
from src.game.render.sprites import organic

#: Увеличение спрайтов в ячейке, plan/09 §12.
ZOOM = 3
#: Кандидаты размера спрайта в ячейке: первый, который влезает в холст, и берётся.
SPRITE_SIZES = (96, 80, 64, 48, 36)
#: Поля и шаг сетки.
MARGIN = 16
CELL_PAD_X = 18
CELL_PAD_Y = 22
SECTION_GAP = 10
TITLE_H = 40
SECTION_H = 26
#: Доли фазы для врагов: один кадр пульсации, чтобы галерея не «дышала».
CREATURE_PHASE = 1.1
#: Сдвиг шевронов конвейера в галерее: лента стоит посередине хода — галерея
#: не анимируется, и «бег» здесь показывать нечем.
BELT_OFFSET = 0.35

#: Подписи состояний постройки (plan/09 §5).
STATE_LABELS = {"on": "работа", "off": "без энергии", "plan": "заготовка"}
#: Сколько стадий трещин показывать (plan/09 §5 — три стадии по HP).
DAMAGE_STAGES = 3
#: Названия врагов для подписи. В `data/` они появятся вместе с M5, а галерее
#: подпись нужна уже сейчас: без неё силуэт не с чем соотнести.
FOE_LABELS = {
    organic.CRAWLER: "ходок",
    organic.SHADOW: "тень",
    organic.MOSQUITO: "москит",
    organic.ARTILLERY: "артиллерия",
    organic.SPROUT: "росток",
    organic.INCUBATOR: "инкубатор",
    organic.SPIKE: "нарост",
}

#: Ячейка галереи: подпись и функция, рисующая спрайт нужного размера.
Cell = tuple[str, Callable[[int], pygame.Surface]]


class Gallery:
    """Сетка всех спрайтов с подписями. Состояние симуляции не читает.

    `spawn` — тайл старта: галерея показывает залежь и реликвию так, как их
    встретит игрок, поэтому форма камня берётся из того же генератора огранок.
    """

    def __init__(self, sprites, icons, items, seed: int = 0) -> None:
        self.sprites = sprites
        self.icons = icons
        self.items = items
        self.seed = seed
        self._cache: dict[tuple[int, int], pygame.Surface] = {}
        self.builds = 0

    # ------------------------------------------------------------- холст
    def draw(self, target: pygame.Surface, hint: str = "") -> None:
        """Галерея во весь экран. Первый вызов собирает холст, дальше — один blit."""
        target.blit(self.surface(target.get_size(), hint), (0, 0))

    def surface(self, size: tuple[int, int], hint: str = "") -> pygame.Surface:
        """Холст галереи под размер. Собирается один раз на размер и подсказку."""
        key = (size[0], size[1], hint)
        surf = self._cache.get(key)
        if surf is None:
            surf = self._build(size[0], size[1], hint)
            self._cache[key] = surf
            self.builds += 1
        return surf

    def clear(self) -> None:
        self._cache.clear()

    # ----------------------------------------------------------- разметка
    def _build(self, w: int, h: int, hint: str) -> pygame.Surface:
        """Рисует галерею на холсте `w`×`h`."""
        sections = self._sections()
        sprite, cols = _fit(sections, w, h)
        cell_w = sprite + CELL_PAD_X
        cell_h = sprite + CELL_PAD_Y
        surf = pygame.Surface((w, h))
        surf.fill(theme.UI_BG)
        _title(surf, w, hint)
        y = TITLE_H
        for name, cells in sections:
            y += SECTION_H
            _section_title(surf, name, y - SECTION_H + 4)
            x = MARGIN
            for label, make in cells:
                if x + cell_w > w - MARGIN:
                    x = MARGIN
                    y += cell_h
                art = make(sprite)
                surf.blit(art, (x + (cell_w - sprite) // 2, y))
                _caption(surf, label, (x + cell_w // 2, y + sprite + 2))
                x += cell_w
            y += cell_h + SECTION_GAP
        return surf

    # ------------------------------------------------------------ разделы
    def _sections(self) -> list[tuple[str, list[Cell]]]:
        """Что показывать: раздел → ячейки. Спрайты создаются позже, при отрисовке."""
        return [
            ("Постройки: работа, без энергии, заготовка, трещины", self._buildings()),
            ("Дрон", self._drone()),
            ("Заражение: враги (M5) и пятна (M8) — заготовка отрисовки", self._foes()),
            ("Ресурсы забега", self._resources()),
            ("Крафт и значки", self._icons_section()),
        ]

    def _buildings(self) -> list[Cell]:
        cells: list[Cell] = []
        for kind in building_data.BUILD_ORDER:
            name = building_data.building(kind).name
            for state in ("on", "off", "plan"):
                cells.append((f"{name}: {STATE_LABELS[state]}",
                              _building(self.sprites, kind, state, 0)))
            cells.append((f"{name}: трещины {DAMAGE_STAGES}",
                          _building(self.sprites, kind, "on", DAMAGE_STAGES)))
        return cells

    def _drone(self) -> list[Cell]:
        sprites = self.sprites
        cells: list[Cell] = [
            ("дрон", lambda s: sprites.drone(s, 0.0)),
            ("тень дрона", sprites.drone_shadow),
        ]
        for chassis, label in (("ground", "корпус наземный"), ("flying", "корпус лётный")):
            cells.append((label, _chassis(sprites, chassis)))
        cells.append(("ротор бура", lambda s: sprites.rotor(s, 0.4, theme.NEON_CYAN, True)))
        return cells

    def _foes(self) -> list[Cell]:
        sprites = self.sprites
        cells: list[Cell] = [
            (FOE_LABELS[kind], _creature(sprites, kind)) for kind in organic.SHAPES
        ]
        for density, label in ((0.35, "пятно заражения: слабое"),
                               (1.0, "пятно заражения: плотное")):
            cells.append((label, _infection(density)))
        return cells

    def _resources(self) -> list[Cell]:
        """Все ресурсы забега рядом: именно здесь видно, различимы ли силуэты."""
        sprites, items = self.sprites, self.items
        cells: list[Cell] = []
        for item in items.natural:
            mark = " (редкий)" if item.rare else ""
            cells.append((f"{item.name}{mark}", _gem(sprites, item.id, item.color)))
        if cells:
            first = items.natural[0]
            cells.append(("истощённая залежь",
                          _gem(sprites, first.id, theme.DEPOSIT_DEPLETED)))
            cells.append(("изученная реликвия",
                          _gem(sprites, first.id, theme.RELIC_STUDIED)))
        return cells

    def _icons_section(self) -> list[Cell]:
        icons = self.icons
        cells: list[Cell] = [
            (item.name, _chip(self.sprites, item.id, item.color))
            for item in self.items.crafted
        ]
        cells += [(PROP_NAMES[prop], _prop(icons, prop)) for prop in Prop]
        cells.append(("неизвестное свойство", _scaled(icons.unknown_icon, 0.8)))
        # Метки — это текст, а не спрайт: растянутая буква выглядела бы мутным
        # пятном, поэтому их видно ровно такими, какими они ложатся в мир.
        cells.append(("метка «?» над реликвией",
                      _native(lambda: icons.mark_icon("?", theme.RELIC_RING))))
        cells.append(("число на куче «×12»",
                      _native(lambda: icons.mark_icon("×12", theme.UI_TEXT))))
        return cells


# ----------------------------------------------------------------- фабрики ячеек
# Каждая ячейка — замыкание вокруг кэша спрайтов: сама по себе она поверхности не
# создаёт, поэтому разметку можно считать до выбора размера.
def _building(sprites, kind: str, state: str, damage: int):
    if kind == building_data.WIRE and state != "plan":
        # Провод в галерее — с кабелем на все четыре стороны: так видно, что
        # кабель доходит до края тайла и стыкуется с соседом.
        return lambda s: _with_mark(sprites, sprites.wire(s, (True,) * 4, state == "on"),
                                    state, s)
    if kind != building_data.CONVEYOR:
        return lambda s: _with_mark(sprites, sprites.building(kind, state, s, damage),
                                    state, s)
    # Конвейер в галерее — вместе с шевронами: они лежат не в спрайте, а в живом
    # слое (`Renderer._draw_belt`), и без них в галерее не видно направления
    # ленты — а это первое, что по ней должен читать игрок (plan/09 §5, §12).
    return lambda s: _belt(sprites, state, s, damage)


def _belt(sprites, state: str, size: int, damage: int) -> pygame.Surface:
    """Спрайт конвейера с шевронами: те же три шеврона, что и в кадре игры."""
    # Импорт внутри функции: `renderer` тянет за собой весь рендер, а галерея
    # нужна ему самому (F4) — на уровне модуля вышел бы цикл.
    from src.game.render.renderer import CHEVRON_SPAN, CHEVRONS

    art = sprites.building(building_data.CONVEYOR, state, size, damage).copy()
    running = state == "on"
    color = theme.NEON_CYAN if running else theme.POWER_OFF
    chevron = sprites.chevron(max(6, int(size * 0.34)), (1, 0), color)
    half = size / 2.0
    for i in range(CHEVRONS):
        along = (i / CHEVRONS + BELT_OFFSET) % 1.0 - 0.5
        art.blit(chevron, chevron.get_rect(
            center=(int(half + along * size * CHEVRON_SPAN), int(half))))
    return _with_mark(sprites, art, state, size)


def _with_mark(sprites, art: pygame.Surface, state: str, size: int) -> pygame.Surface:
    """Обесточенная постройка — со значком «нет питания», как в кадре игры.

    В игре значок кладёт рендер отдельным слоем (`Renderer._draw_status_marks`),
    поэтому в спрайте его нет — и галерея добавляет его сама, тем же сдвигом.
    """
    if state != "off":
        return art
    from src.game.render.renderer import NO_POWER_OFFSET, NO_POWER_SIZE

    out = art.copy()
    mark = sprites.no_power(max(8, int(size * NO_POWER_SIZE)))
    ox, oy = NO_POWER_OFFSET
    out.blit(mark, mark.get_rect(center=(int(size / 2 + ox * size),
                                         int(size / 2 + oy * size))))
    return out


def _gem(sprites, item_id: str, color):
    return lambda s: sprites.gem(item_id, s, color, glow=True)


def _chip(sprites, item_id: str, color):
    return lambda s: sprites.chip(item_id, s, color)


def _creature(sprites, kind: str):
    return lambda s: sprites.creature(kind, s, CREATURE_PHASE)


def _chassis(sprites, chassis: str):
    return lambda s: sprites.chassis(s, chassis, 0.0)


def _infection(density: float):
    def make(s: int) -> pygame.Surface:
        surf = _layer(s)
        _ground(surf, s)
        organic.draw_infection_patch(surf, s, seed=int(density * 1000), density=density,
                                     phase=CREATURE_PHASE)
        return surf
    return make


def _ground(surf: pygame.Surface, s: int) -> None:
    """Подложка под пятно заражения: та же земля, что и в мире (plan/09 §4)."""
    surf.fill(theme.GROUND_VARIANTS[0])
    for k in range(3):                      # редкие «камешки» тёмными точками
        x = (k * 37 + 11) % s
        y = (k * 53 + 7) % s
        surf.set_at((x, y), theme.GROUND_PEBBLE)


def _prop(icons, prop: Prop):
    def make(s: int) -> pygame.Surface:
        # Значок свойства рисуется в круглом размере ячейки, но остаётся мелким:
        # он и в интерфейсе мелкий, крупным его делать незачем.
        art = icons.prop_icon(prop, max(16, int(s * 0.7)))
        out = _layer(s)
        out.blit(art, art.get_rect(center=(s // 2, s // 2)))
        return out
    return make


def _scaled(get_icon, scale: float):
    """Значок интерфейса в ячейке галереи: масштабируется, а не перерисовывается.

    Эти значки мелкие и вне галереи (24 px в слотах и над объектами), поэтому
    крупнее их делает `smoothscale`, а не отдельный рисунок: важно увидеть
    ровно тот значок, который игрок встретит в интерфейсе.
    """
    def make(s: int) -> pygame.Surface:
        art = get_icon()
        side = max(8, int(s * 0.5 * scale))
        out = _layer(s)
        out.blit(pygame.transform.smoothscale(art, (side, side)),
                 ((s - side) // 2, (s - side) // 2))
        return out
    return make


def _native(get_icon):
    """Текстовая метка в ячейке — без увеличения: растянутая буква мутнеет."""
    def make(s: int) -> pygame.Surface:
        art = get_icon()
        out = _layer(s)
        out.blit(art, art.get_rect(center=(s // 2, s // 2)))
        return out
    return make


def _layer(s: int) -> pygame.Surface:
    return pygame.Surface((s, s), pygame.SRCALPHA)


# -------------------------------------------------------------------- разметка
def _fit(sections: list[tuple[str, list[Cell]]], w: int, h: int) -> tuple[int, int]:
    """Подбирает размер спрайта и число столбцов, чтобы сетка влезла в холст.

    Идём от крупного к мелкому: на скриншотном холсте выходит 🔧 3× (plan/09
    §12), в окне игры — то, что влезает по высоте.
    """
    for sprite in SPRITE_SIZES:
        cols = _cols(sprite, w)
        if cols < 1:
            continue
        if _height(sections, sprite, cols) <= h:
            return sprite, cols
    sprite = SPRITE_SIZES[-1]
    return sprite, max(1, _cols(sprite, w))


def _cols(sprite: int, w: int) -> int:
    return (w - 2 * MARGIN) // (sprite + CELL_PAD_X)


def _height(sections: list[tuple[str, list[Cell]]], sprite: int, cols: int) -> int:
    """Высота сетки целиком — по ней и решается, влезает ли галерея."""
    total = TITLE_H
    for _name, cells in sections:
        rows = (len(cells) + cols - 1) // cols
        total += SECTION_H + rows * (sprite + CELL_PAD_Y) + SECTION_GAP
    return total


# --------------------------------------------------------------------- подписи
def _title(surf: pygame.Surface, w: int, hint: str) -> None:
    from src.game.ui.fonts import get_font

    font = get_font(22)
    text = font.render("Галерея спрайтов", True, theme.UI_TEXT)
    surf.blit(text, (MARGIN, 10))
    if hint:
        dim = get_font(15).render(hint, True, theme.UI_TEXT_DIM)
        surf.blit(dim, dim.get_rect(midright=(w - MARGIN, 20)))


def _section_title(surf: pygame.Surface, name: str, y: int) -> None:
    from src.game.ui.fonts import get_font

    text = get_font(16).render(name, True, theme.ACCENT)
    surf.blit(text, (MARGIN, y))
    pygame.draw.line(surf, theme.UI_BORDER, (MARGIN, y + 18),
                     (surf.get_width() - MARGIN, y + 18), 1)


def _caption(surf: pygame.Surface, label: str, pos: tuple[int, int]) -> None:
    from src.game.ui.fonts import get_font

    text = get_font(13).render(label, True, theme.UI_TEXT_DIM)
    surf.blit(text, text.get_rect(midtop=pos))
