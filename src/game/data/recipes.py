"""Роли, рецепты и крафченые предметы (plan/02).

Роли постоянны во всех забегах, как и свойства: боеприпас, энергия,
конструкционный материал, деталь автоматики. Рецепт требует **свойство ×
количество**, а не конкретный ресурс, поэтому забег можно пройти, выучив язык
свойств, а не запомнив названия (главная особенность игры).

У каждой роли два варианта рецепта:
- **базовый** — открывается базовой реликвией;
- **усилитель** — открывается реликвией-усилителем, тратит меньше на единицу.

Значок крафченого предмета повторяет значок своего свойства — так по инвентарю
видно, что и на что меняется.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.game.data.resources import PROP_ICONS, ItemDef, Prop


@dataclass(frozen=True)
class Recipe:
    """Рецепт «свойство × количество → предмет роли»."""

    id: str                            # "ammo" или "ammo+" (усилитель)
    role: str                          # id роли и он же id крафченого предмета
    requires: tuple[tuple[Prop, int], ...]
    outputs: int
    craft_time: float
    amplified: bool = False

    @property
    def props(self) -> tuple[Prop, ...]:
        return tuple(prop for prop, _ in self.requires)


@dataclass(frozen=True)
class RoleDef:
    """Роль ресурса: её свойство, значок предмета и два варианта рецепта."""

    id: str
    name: str
    short: str
    prop: Prop
    shape: str
    base: Recipe
    amplified: Recipe


def _recipe(role: str, prop: Prop, amount: int, outputs: int, time: float,
            amplified: bool) -> Recipe:
    return Recipe(
        id=f"{role}+" if amplified else role,
        role=role,
        requires=((prop, amount),),
        outputs=outputs,
        craft_time=time,
        amplified=amplified,
    )


# 🔧 числа крафта: базовый — как в примере plan/02 (💥×3 → 1 за 2 с),
# усилитель тратит 2 единицы на предмет вместо 3 и вдвое быстрее.
ROLES: tuple[RoleDef, ...] = (
    RoleDef(
        id="ammo", name="Боеприпас", short="Боеприпас", prop=Prop.UNSTABLE,
        shape=PROP_ICONS[Prop.UNSTABLE][0], base=_recipe("ammo", Prop.UNSTABLE, 3, 1, 2.0, False),
        amplified=_recipe("ammo", Prop.UNSTABLE, 4, 2, 2.5, True),
    ),
    RoleDef(
        id="energy", name="Энергия", short="Энергия", prop=Prop.FLAMMABLE,
        shape=PROP_ICONS[Prop.FLAMMABLE][0], base=_recipe("energy", Prop.FLAMMABLE, 3, 1, 2.0, False),
        amplified=_recipe("energy", Prop.FLAMMABLE, 4, 2, 2.5, True),
    ),
    RoleDef(
        id="structure", name="Конструкционный материал", short="Материал", prop=Prop.TOUGH,
        shape=PROP_ICONS[Prop.TOUGH][0], base=_recipe("structure", Prop.TOUGH, 3, 1, 2.0, False),
        amplified=_recipe("structure", Prop.TOUGH, 4, 2, 2.5, True),
    ),
    RoleDef(
        id="automation", name="Деталь автоматики", short="Автоматика", prop=Prop.CONDUCTIVE,
        shape=PROP_ICONS[Prop.CONDUCTIVE][0], base=_recipe("automation", Prop.CONDUCTIVE, 3, 1, 2.0, False),
        amplified=_recipe("automation", Prop.CONDUCTIVE, 4, 2, 2.5, True),
    ),
)

ROLES_BY_ID: dict[str, RoleDef] = {role.id: role for role in ROLES}

#: id базовых рецептов ролей. По ним отличают «предмет роли» от предмета
#: постройки: у обоих `Recipe.role` равен id своего предмета, но только у роли
#: есть `RoleDef`. Нужен там, где перебирают именно рецепты ролей, — например
#: при выборе рецепта сборщиком (plan/03).
ROLE_IDS: frozenset[str] = frozenset(role.id for role in ROLES)

RECIPES: tuple[Recipe, ...] = tuple(
    recipe for role in ROLES for recipe in (role.base, role.amplified))

RECIPES_BY_ID: dict[str, Recipe] = {recipe.id: recipe for recipe in RECIPES}


def role_item(role: RoleDef) -> ItemDef:
    """Крафченый предмет роли. Свойств у него нет: он уже продукт, не сырьё."""
    return ItemDef(
        id=role.id,
        name=role.name,
        props=frozenset(),
        color=PROP_ICONS[role.prop][1],
        shape=role.shape,
        crafted=True,
    )


CRAFTED_ITEMS: tuple[ItemDef, ...] = tuple(role_item(role) for role in ROLES)


def role(role_id: str) -> RoleDef:
    return ROLES_BY_ID[role_id]


def recipe(recipe_id: str) -> Recipe:
    return RECIPES_BY_ID[recipe_id]
