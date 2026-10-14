"""Свойства и предметы забега (plan/02, `07_pygame_guide.md` §4).

Свойства (Prop) постоянны во всех забегах — это «язык», который учит игрок.
А вот названия, значки и привязка свойств к ресурсам **рандомизируются каждый
забег**: этим занимается генератор `sim/run_gen.py`, а здесь лежат только
правила броска (слоги имён, формы, палитра) и таблица предметов `ItemTable`.

В таблице забега лежат и природные ресурсы, и крафченые предметы ролей
(боеприпас, энергия, материал, деталь автоматики): инвентарь, значки и книга
рецептов работают с одним словарём «id → ItemDef», а не с двумя.
"""

from __future__ import annotations

import colorsys
import random
from dataclasses import dataclass, replace
from enum import IntEnum

from src.game import config


class Prop(IntEnum):
    """Шесть постоянных свойств ресурсов (см. plan/02)."""

    FLAMMABLE = 0   # 🔥 энергия
    CONDUCTIVE = 1  # ⚡ автоматика
    TOUGH = 2       # 🪨 конструкции
    UNSTABLE = 3    # 💥 боеприпасы
    LUMINOUS = 4    # ✨ лампы
    CUTTING = 5     # ⚙️ буры, инструменты


PROP_NAMES: dict[Prop, str] = {
    Prop.FLAMMABLE: "Горючий",
    Prop.CONDUCTIVE: "Проводящий",
    Prop.TOUGH: "Прочный",
    Prop.UNSTABLE: "Нестабильный",
    Prop.LUMINOUS: "Светящийся",
    Prop.CUTTING: "Режущий",
}

# Иконки свойств не рандомизируются: форма + цвет закреплены навсегда.
PROP_ICONS: dict[Prop, tuple[str, tuple[int, int, int]]] = {
    Prop.FLAMMABLE: ("triangle", (245, 140, 60)),
    Prop.CONDUCTIVE: ("bolt", (250, 214, 80)),
    Prop.TOUGH: ("square", (150, 150, 158)),
    Prop.UNSTABLE: ("star", (238, 92, 84)),
    Prop.LUMINOUS: ("diamond", (240, 240, 214)),
    Prop.CUTTING: ("gear", (96, 214, 208)),
}

# Формы значков ресурсов (см. render/shapes.py).
RESOURCE_SHAPES = ("circle", "diamond", "hexagon", "triangle", "cluster", "star")

# Слоги имён: имя собирается из 2–3 слогов («Кра» + «зель» → «Кразель»).
NAME_START = ("кра", "зель", "мо", "ти", "ор", "вин", "дро", "пал",
              "ксе", "нум", "саг", "вел", "гро", "иль", "там", "фер")
NAME_MIDDLE = ("ра", "ль", "ни", "ка", "зу", "ве", "мо", "та", "ри", "ло", "ша", "ге")
NAME_END = ("ель", "ин", "ор", "ак", "ум", "ис", "эр", "он", "ит", "аз", "ур", "ем")

# Палитра значка: оттенок случайный, но насыщенность и яркость в узком коридоре,
# чтобы значки не вырождались в серые или неоновые пятна.
COLOR_SATURATION = (0.55, 0.85)
COLOR_VALUE = (0.78, 1.0)


@dataclass(frozen=True)
class ItemDef:
    """Предмет забега: природный ресурс, крафченый предмет роли или постройка.

    `props` есть только у природных ресурсов — крафченые предметы дальше в
    рецепты не идут (plan/02: крафт требует свойства, а не предметы).

    Постройка — тоже предмет (plan/03): у неё есть значок, имя и ссылка на
    **тип здания** (`building`, id из `data/buildings.py`). Ставится она не
    клавишей-режимом, а выбором слота панели быстрого доступа.
    """

    id: str
    name: str
    props: frozenset[Prop]
    color: tuple[int, int, int]
    shape: str
    rare: bool = False
    crafted: bool = False
    # Одно свойство, которое игрок видит, впервые подобрав ресурс (plan/02).
    # У крафченых предметов свойств нет, поэтому и подсказки нет.
    hint: Prop | None = None
    #: id здания, которое ставится этим предметом (`None` — не постройка).
    building: str | None = None
    #: Лимит стека. Обычно 🔧 `SLOT_STACK_MAX`, у построек — 🔧 `BUILDING_STACK`.
    stack_max: int = config.SLOT_STACK_MAX

    def has(self, prop: Prop) -> bool:
        return prop in self.props


class ItemTable:
    """Все предметы одного забега: природные ресурсы и крафченые предметы.

    Идентификаторы уникальны, поэтому таблица — это ещё и словарь лукапов
    (`items[resource_id]`), которым пользуются рендер, HUD и книга рецептов.
    """

    def __init__(self, items: tuple[ItemDef, ...]) -> None:
        self.items = items
        self.by_id: dict[str, ItemDef] = {item.id: item for item in items}
        if len(self.by_id) != len(items):
            raise ValueError("идентификаторы предметов забега должны быть уникальны")
        self.natural: tuple[ItemDef, ...] = tuple(i for i in items if not i.crafted)
        self.crafted: tuple[ItemDef, ...] = tuple(i for i in items if i.crafted)
        self.natural_ids: tuple[str, ...] = tuple(i.id for i in self.natural)
        rare = [i for i in self.natural if i.rare]
        self.rare_id: str | None = rare[0].id if rare else None
        # Обычные ресурсы: именно они считаются «доступными до первой ночи» —
        # редкий ресурс в критичную цепочку рецептов не входит (plan/02).
        self.common_ids: tuple[str, ...] = tuple(
            i.id for i in self.natural if not i.rare)
        # Лимиты стека, отличающиеся от общего: контейнер спрашивает их у
        # таблицы предметов, а не носит своё исключение для построек.
        self.stack_limits: dict[str, int] = {
            i.id: i.stack_max for i in items
            if i.stack_max != config.SLOT_STACK_MAX}

    # ------------------------------------------------------------- лукапы
    def __getitem__(self, item_id: str) -> ItemDef:
        return self.by_id[item_id]

    def get(self, item_id: str) -> ItemDef | None:
        return self.by_id.get(item_id)

    def __contains__(self, item_id: object) -> bool:
        return item_id in self.by_id

    def __len__(self) -> int:
        return len(self.items)

    def with_prop(self, prop: Prop, only: tuple[str, ...] | None = None) -> tuple[ItemDef, ...]:
        """Природные ресурсы со свойством. Основа рецептов «свойство × количество».

        `only` ограничивает выборку (например, ресурсами стартовой зоны).
        """
        return tuple(i for i in self.natural
                     if i.has(prop) and (only is None or i.id in only))

    def covering(self, prop: Prop) -> ItemDef:
        """Первый ресурс со свойством. Нужен только как «источник свойства»."""
        found = self.with_prop(prop)
        return found[0]


# ------------------------------------------------------------------ генерация
def random_color(rng: random.Random, hue: float | None = None) -> tuple[int, int, int]:
    """Цвет значка из HSV. Оттенок можно задать — так палитра забега расходится."""
    if hue is None:
        hue = rng.random()
    sat = rng.uniform(*COLOR_SATURATION)
    val = rng.uniform(*COLOR_VALUE)
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, sat, val)
    return int(r * 255), int(g * 255), int(b * 255)


def random_name(rng: random.Random, used: set[str] | None = None,
                tries: int = 40) -> str:
    """Имя из слогов. Пытается не повторяться внутри забега."""
    used = used if used is not None else set()
    for _ in range(tries):
        parts = [rng.choice(NAME_START)]
        if rng.random() < 0.45:
            parts.append(rng.choice(NAME_MIDDLE))
        parts.append(rng.choice(NAME_END))
        name = "".join(parts).capitalize()
        if name not in used:
            return name
    return "".join(parts).capitalize()


def with_extra_prop(item: ItemDef, prop: Prop) -> ItemDef:
    """Копия ресурса с добавленным свойством — правка генератора ради проходимости."""
    return replace(item, props=item.props | {prop})


def with_prop_replaced(item: ItemDef, old: Prop, new: Prop) -> ItemDef:
    """Копия ресурса, у которого одно свойство заменено другим."""
    props = (item.props - {old}) | {new}
    hint = item.hint if item.hint != old else new
    return replace(item, props=props, hint=hint)
