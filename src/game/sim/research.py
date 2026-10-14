"""Знание игрока и крафт по свойствам (plan/02). Не импортирует pygame.

Две вещи, которые игрок узнаёт в забеге:
- **свойства ресурсов.** При подборе видно одно свойство, остальные — `?`.
  Скан дрона открывает ещё одно, лаборатория — все сразу;
- **рецепты ролей.** Их открывают реликвии: базовая даёт базовый рецепт,
  усилитель — более выгодный вариант той же роли.

Крафт идёт «свойство × количество»: подходит любой ресурс с этим свойством,
а ресурс с двумя нужными свойствами закрывает сразу два слота (plan/02).
"""

from __future__ import annotations

from dataclasses import replace

from src.game.data.recipes import Recipe
from src.game.data.resources import ItemDef, ItemTable, Prop
from src.game.sim.resources import Inventory


class Knowledge:
    """Что игрок знает о свойствах ресурсов и о рецептах."""

    def __init__(self, items: ItemTable) -> None:
        self.items = items
        self.seen: set[str] = set()
        self.revealed: dict[str, set[Prop]] = {}
        self.known_recipes: set[str] = set()

    # ------------------------------------------------------------ свойства
    def note_pickup(self, item_id: str) -> tuple[Prop, ...]:
        """Первый подбор ресурса показывает одно его свойство (plan/02).

        Возвращает вновь показанные свойства — симуляция сообщает о них игроку.
        """
        item = self.items.get(item_id)
        if item is None or item.crafted or item_id in self.seen:
            return ()
        self.seen.add(item_id)
        if item.hint is None:
            return ()
        self.revealed.setdefault(item_id, set()).add(item.hint)
        return (item.hint,)

    def visible(self, item_id: str) -> frozenset[Prop]:
        """Открытые свойства ресурса (подбор, скан, лаборатория)."""
        return frozenset(self.revealed.get(item_id, ()))

    def unknown(self, item_id: str) -> tuple[Prop, ...]:
        """Свойства, которые ещё показываются как `?`."""
        item = self.items.get(item_id)
        if item is None:
            return ()
        return tuple(sorted(item.props - self.visible(item_id)))

    def reveal(self, item_id: str, prop: Prop) -> bool:
        """Открывает свойство. False — если оно уже известно или его нет."""
        item = self.items.get(item_id)
        if item is None or prop not in item.props:
            return False
        if prop in self.revealed.get(item_id, ()):
            return False
        self.revealed.setdefault(item_id, set()).add(prop)
        return True

    def reveal_one(self, item_id: str, rng) -> Prop | None:
        """Скан: одно случайное неизвестное свойство."""
        unknown = self.unknown(item_id)
        if not unknown:
            return None
        prop = unknown[rng.randrange(len(unknown))]
        self.reveal(item_id, prop)
        return prop

    def reveal_all(self, item_id: str) -> tuple[Prop, ...]:
        """Лаборатория: все свойства сразу. Возвращает вновь открытые."""
        item = self.items.get(item_id)
        if item is None:
            return ()
        fresh = self.unknown(item_id)
        self.revealed.setdefault(item_id, set()).update(item.props)
        self.seen.add(item_id)
        return fresh

    # ------------------------------------------------------------- рецепты
    def learn(self, recipe_id: str) -> bool:
        """Открывает рецепт (изучение реликвии). False — если он уже известен."""
        if recipe_id in self.known_recipes:
            return False
        self.known_recipes.add(recipe_id)
        return True

    def is_known(self, recipe_id: str) -> bool:
        return recipe_id in self.known_recipes

    @property
    def known_count(self) -> int:
        return len(self.known_recipes)

    # ---------------------------------------------------------- для тестов
    def state(self) -> tuple:
        """Слепок знания: входит в состояние симуляции."""
        return (
            tuple(sorted(self.seen)),
            tuple(sorted((item_id, tuple(sorted(props)))
                         for item_id, props in self.revealed.items())),
            tuple(sorted(self.known_recipes)),
        )


# ------------------------------------------------------------------- крафт
def plan_craft(recipe: Recipe, inventory: Inventory,
               items: ItemTable) -> dict[str, int] | None:
    """Какие единицы ресурсов потратить на рецепт. None — если не хватает.

    Рецепт требует свойства, а не ресурсы, поэтому выбор есть: жадный алгоритм
    берёт сначала ресурс, закрывающий больше всего ещё не закрытых слотов
    (ресурс с двумя нужными свойствами тратится один раз, а не два), при
    равенстве — ресурс с меньшим числом свойств: узкий не жалко, широкий
    пригодится в другом рецепте. Выбор детерминирован (порядок по id).
    """
    need: dict[Prop, int] = {}
    for prop, count in recipe.requires:
        need[prop] = need.get(prop, 0) + count

    stock = {item_id: count for item_id, count in inventory.items()
             if count > 0 and _has_props(items, item_id)}
    plan: dict[str, int] = {}

    while True:
        unmet = {prop: count for prop, count in need.items() if count > 0}
        if not unmet:
            return plan
        best: tuple[tuple, str, tuple[Prop, ...]] | None = None
        for item_id, count in stock.items():
            if count <= 0:
                continue
            item = items[item_id]
            covers = tuple(p for p in sorted(item.props) if p in unmet)
            if not covers:
                continue
            key = (-len(covers), len(item.props), item_id)
            if best is None or key < best[0]:
                best = (key, item_id, covers)
        if best is None:
            return None
        _, item_id, covers = best
        stock[item_id] -= 1
        plan[item_id] = plan.get(item_id, 0) + 1
        for prop in covers:
            need[prop] -= 1


class KnownItems:
    """Таблица предметов «глазами игрока»: у ресурса только открытые свойства.

    Нужна панели крафта (plan/02): «можно скрафтить сейчас» и «есть M» обязаны
    считаться по тому, что игрок знает, — иначе яркий рецепт выдал бы, что у
    какого-то ресурса в рюкзаке есть ещё не открытое свойство. Сам крафт этой
    таблицей не пользуется: подбор материалов идёт по настоящим свойствам, как
    и раньше.

    Подставные `ItemDef` кэшируются на время жизни объекта: его создают на
    один запрос панели, поэтому устаревшее знание в нём не задерживается.
    """

    def __init__(self, items: ItemTable, knowledge: Knowledge) -> None:
        self._items = items
        self._knowledge = knowledge
        self._cache: dict[str, ItemDef] = {}

    def get(self, item_id: str) -> ItemDef | None:
        item = self._cache.get(item_id)
        if item is None:
            real = self._items.get(item_id)
            if real is None:
                return None
            item = replace(real, props=self._knowledge.visible(item_id) & real.props)
            self._cache[item_id] = item
        return item

    def __getitem__(self, item_id: str) -> ItemDef:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return item


def can_craft(recipe: Recipe, inventory: Inventory, items: ItemTable) -> bool:
    return plan_craft(recipe, inventory, items) is not None


def _has_props(items: ItemTable, item_id: str) -> bool:
    item = items.get(item_id)
    return item is not None and bool(item.props)
