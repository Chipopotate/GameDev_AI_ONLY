"""Генератор забега (M3, plan/02). Не импортирует pygame.

Каждый забег перемешивает «язык свойств»: какие свойства у ресурсов, как они
называются, как выглядят значки. Постоянными остаются роли, свойства и логика —
игрок учит правила, а не конкретный список ресурсов.

Здесь же живёт **гарантия проходимости** (plan/02, обязательна под тестом):
генератор идёт от конца к началу — проверяет, что все свойства, нужные
рецептам критичных ролей, есть у ресурсов стартовой зоны, и если нет, выдаёт
недостающее свойство одному из стартовых ресурсов. Реликвии, открывающие
базовые рецепты, размещаются в стартовой зоне: до обвала гор (M8) выйти из неё
нельзя, а значит иначе забег был бы непроходим.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from src.game import config
from src.game.data.buildings import (BUILDING_ITEMS, BUILDING_RECIPES,
                                     BUILDING_START, building as building_def)
from src.game.data.robots import ROBOT_ITEMS, ROBOT_RECIPES
from src.game.data.recipes import CRAFTED_ITEMS, RECIPES, ROLES, Recipe
from src.game.data.resources import (
    RESOURCE_SHAPES,
    ItemDef,
    ItemTable,
    Prop,
    random_color,
    random_name,
    with_extra_prop,
    with_prop_replaced,
)

# Тайл реликвии: -1 значит «не размещена» (на крошечной тестовой карте места
# может не хватить — это не ошибка, а отсутствие места).
UNPLACED = -1


@dataclass
class Relic:
    """Реликвия на карте. Изучение открывает рецепт своей роли (plan/02)."""

    id: int
    role: str
    amplified: bool = False
    x: int = UNPLACED
    y: int = UNPLACED
    studied: bool = False

    @property
    def placed(self) -> bool:
        return self.x >= 0 and self.y >= 0

    @property
    def recipe_id(self) -> str:
        """id рецепта, который откроет эта реликвия."""
        return f"{self.role}+" if self.amplified else self.role


@dataclass
class RunPlan:
    """План забега: предметы, рецепты и реликвии."""

    seed: int
    items: ItemTable
    recipes: tuple[Recipe, ...]
    relics: list[Relic] = field(default_factory=list)

    # ------------------------------------------------------------- лукапы
    def recipe(self, recipe_id: str) -> Recipe:
        for recipe in self.recipes:
            if recipe.id == recipe_id:
                return recipe
        raise KeyError(recipe_id)

    def relic_of(self, role: str, amplified: bool = False) -> Relic | None:
        for relic in self.relics:
            if relic.role == role and relic.amplified == amplified:
                return relic
        return None

    def relic_at(self, x: int, y: int) -> Relic | None:
        for relic in self.relics:
            if relic.placed and relic.x == x and relic.y == y:
                return relic
        return None

    # -------------------------------------------------------- проходимость
    def is_passable(self) -> bool:
        """Выполнена ли гарантия проходимости: все свойства и все роли закрыты.

        Ресурсами стартовой зоны считаются обычные ресурсы (у каждого из них
        залежь в стартовой зоне гарантирована генератором карты). Редкий ресурс
        в критичную цепочку не входит — он бонус, а не обязательство.
        """
        available = self.items.common_ids
        for prop in Prop:
            if not self.items.with_prop(prop, available):
                return False
        for recipe in self.recipes:
            for prop, _count in recipe.requires:
                if not self.items.with_prop(prop, available):
                    return False
        return True

    def missing_props(self) -> tuple[Prop, ...]:
        """Свойства, у которых нет источника среди стартовых ресурсов."""
        return tuple(p for p in Prop if not self.items.with_prop(p, self.items.common_ids))

    def craftable_at_start(self) -> bool:
        """Можно ли скрафтить стартовые постройки на любом сиде (plan/02).

        Лампа и лаборатория нужны к первой ночи, а первая ночь наступает
        раньше, чем игрок выйдет из стартовой зоны. Поэтому их рецепты
        проверяются отдельно и жёстко: у каждого нужного свойства обязан быть
        источник среди ресурсов стартовой зоны (залежей каждого ресурса в зоне
        🔧 две по 60 единиц, так что количества хватает с запасом).
        """
        available = self.items.common_ids
        for kind in BUILDING_START:
            recipe = building_def(kind).recipe
            if recipe is None:
                continue
            for prop, _count in recipe.requires:
                if not self.items.with_prop(prop, available):
                    return False
        return True

    # ----------------------------------------------------------- размещение
    def place_relics(self, world) -> None:
        """Раскладывает реликвии по карте: базовые — в стартовой зоне.

        Отдельный поток случайности (`RELIC_SALT`), чтобы размещение реликвий
        не сдвигало рельеф и залежи.
        """
        rng = random.Random(self.seed ^ config.RELIC_SALT)
        self._place_group([r for r in self.relics if not r.amplified], world, rng,
                          inside=True)
        self._place_group([r for r in self.relics if r.amplified], world, rng,
                          inside=False)

    @staticmethod
    def _place_group(relics: list[Relic], world, rng: random.Random,
                     inside: bool) -> None:
        """Раскладывает группу реликвий: только на свободную землю, с интервалом."""
        spawn_x, spawn_y = world.spawn_point()
        placed: list[tuple[int, int]] = []
        for relic in relics:
            spot = None
            for _ in range(config.RELIC_PLACEMENT_TRIES):
                x = rng.randrange(0, world.width)
                y = rng.randrange(0, world.height)
                if inside != world.in_start_zone(x, y):
                    continue
                if not world.is_walkable_tile(x, y):
                    continue
                if world.deposit_at(x, y) is not None:
                    continue
                if max(abs(x - spawn_x), abs(y - spawn_y)) < config.RELIC_SPAWN_CLEAR:
                    continue
                if any(max(abs(x - px), abs(y - py)) < config.RELIC_MIN_SPACING
                       for px, py in placed):
                    continue
                spot = (x, y)
                break
            if spot is None:
                continue
            relic.x, relic.y = spot
            placed.append(spot)


# ------------------------------------------------------------------ генерация
def generate_run(seed: int) -> RunPlan:
    """Собирает забег по seed: ресурсы, их свойства и значки, рецепты, реликвии."""
    rng = random.Random(seed ^ config.RUN_GEN_SALT)
    items = _generate_items(rng)
    relics = _generate_relics(rng)
    # Рецепты построек идут в тот же список, что рецепты ролей: `run.recipe(id)`
    # и панель крафта не различают их — крафт у них общий (plan/02).
    return RunPlan(seed=seed, items=items,
                   recipes=RECIPES + BUILDING_RECIPES + ROBOT_RECIPES, relics=relics)


def _generate_items(rng: random.Random) -> ItemTable:
    """Природные ресурсы забега: 8 обычных и один редкий."""
    count = config.RESOURCES_PER_RUN
    used_names: set[str] = set()
    shapes = _shape_pool(rng, count + 1)

    def make(index: int, props: frozenset[Prop], rare: bool) -> ItemDef:
        name = random_name(rng, used_names)
        used_names.add(name)
        color = random_color(rng, _hue(index, count + 1, rng))
        return ItemDef(
            id="rare" if rare else f"res{index}",
            name=name,
            props=props,
            color=color,
            shape=shapes[index],
            rare=rare,
            hint=_pick(rng, tuple(sorted(props))),
        )

    items: list[ItemDef] = []
    for i in range(count):
        # 1–2 свойства. Половина ресурсов получает одно свойство, половина — два:
        # так в забеге есть и «чистые» источники, и ресурсы «на два слота».
        amount = 1 if rng.random() < 0.5 else config.RESOURCE_PROPS_MAX
        props = frozenset(rng.sample(list(Prop), amount))
        items.append(make(i, props, False))

    rare_props = frozenset(rng.sample(list(Prop), config.RARE_PROPS))
    rare = make(count, rare_props, True)

    _ensure_sources(items, rng)

    table = ItemTable(tuple(items) + (rare,) + CRAFTED_ITEMS + BUILDING_ITEMS
                      + ROBOT_ITEMS)
    if not table.rare_id or len(table.natural) != count + 1:
        raise AssertionError("генератор забега собрал некорректный набор ресурсов")
    return table


def _shape_pool(rng: random.Random, size: int) -> list[str]:
    """Формы значков: сначала по одной разной, потом любые — значки не сливаются."""
    pool = list(RESOURCE_SHAPES)
    rng.shuffle(pool)
    while len(pool) < size:
        pool.append(rng.choice(RESOURCE_SHAPES))
    return pool[:size]


def _hue(index: int, total: int, rng: random.Random) -> float:
    """Оттенки разносятся по кругу: палитра забега читается, а не сливается."""
    step = 1.0 / max(1, total)
    return (index * step + rng.uniform(-0.35, 0.35) * step) % 1.0


def _pick(rng: random.Random, values: tuple):
    return values[rng.randrange(len(values))] if values else None


def _ensure_sources(items: list[ItemDef], rng: random.Random) -> None:
    """Гарантия plan/02: у каждого свойства есть хотя бы один источник.

    Проверка идёт по ресурсам стартовой зоны (обычным). Если свойства не
    хватает — один из ресурсов получает его; если свободных слотов нет,
    свойство заменяется у ресурса, который не единственный источник своего.
    Правка повторяется, пока проверка не пройдёт (шаги 4–5 алгоритма plan/02).
    """
    for _ in range(config.RUN_GEN_PATCH_TRIES):
        missing = [p for p in Prop
                   if not any(p in item.props for item in items)]
        if not missing:
            return
        _patch_prop(items, rng.choice(missing), rng)
    raise AssertionError("не удалось обеспечить источник для каждого свойства")


def _patch_prop(items: list[ItemDef], prop: Prop, rng: random.Random) -> None:
    """Выдаёт свойство одному из ресурсов, ломая минимум инвариантов."""
    free = [i for i, item in enumerate(items)
            if len(item.props) < config.RESOURCE_PROPS_MAX]
    if free:
        index = rng.choice(free)
        items[index] = with_extra_prop(items[index], prop)
        return
    # Все слоты заняты: меняем свойство у того ресурса, чьё свойство не
    # единственный источник — иначе гарантия сломается в другом месте.
    order = list(range(len(items)))
    rng.shuffle(order)
    for index in order:
        item = items[index]
        for old in sorted(item.props):
            sources = sum(1 for other in items if old in other.props)
            if sources > 1:
                items[index] = with_prop_replaced(item, old, prop)
                return


def _generate_relics(rng: random.Random) -> list[Relic]:
    """Базовая реликвия на каждую роль + усилители на случайные роли."""
    relics: list[Relic] = []
    for role in ROLES:
        relics.append(Relic(id=len(relics), role=role.id, amplified=False))
    roles = [role.id for role in ROLES]
    rng.shuffle(roles)
    for role_id in roles[:min(config.AMP_RELICS, len(roles))]:
        relics.append(Relic(id=len(relics), role=role_id, amplified=True))
    return relics
