"""Здания (plan/03): имя, значок, контейнеры, цена и работа. Логика — в `sim/buildings.py`.

Здесь только описание, поэтому новый вид здания — строка в таблице, а не новый
класс: контейнеры объявляются в данных, и окно предметов (`ui/item_window.py`)
показывает их само, а рендер рисует любую форму по имени (`render/shapes.py`).

У каждого здания в данных (plan/03): `hp`, `cost`, `enemy_priority`,
`needs_power` — плюс то, что понадобилось экономике M4:

- `work` — что здание делает **само**, без дрона: бурит (`WORK_MINE`),
  двигает предметы (`WORK_MOVE`), собирает по рецепту роли (`WORK_CRAFT`).
  Лаборатория работает по полю `analyzes` (M3) — образцы в её слотах;
- `power_out` / `power_use` — сколько энергии здание даёт и сколько тратит
  (единицы условные, 🔧). Потребитель без сети не работает;
- `conducts` — провод: энергии не даёт и не тратит, но соединяет соседей в
  одну сеть (без него сеть — это только стоящие вплотную здания);
- `fuel` — чем топится генератор: свойство предмета (`Prop`) или id предмета.

**Порты контейнеров** (`ContainerDef.role`) — то, как здание связано с
конвейером: конвейер кладёт в контейнер `PORT_IN`, забирает из `PORT_OUT` или
`PORT_STORE`, а генератор принимает топливо в `PORT_FUEL`. Порядок контейнеров
в таблице — это порядок панелей в окне предметов.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.game import config
from src.game.data.recipes import Recipe
from src.game.data.resources import ItemDef, Prop

LAMP = "lamp"
LAB = "lab"
DRILL = "drill"
CONVEYOR = "conveyor"
WIRE = "wire"
STORAGE = "storage"
ASSEMBLER = "assembler"
GENERATOR = "generator"
TURRET = "turret"
WALL = "wall"
WORKSHOP = "workshop"

#: Роли контейнеров: куда конвейер кладёт и откуда берёт.
PORT_IN = "in"          # принимает предметы (вход сборщика, образцы лаборатории)
PORT_OUT = "out"        # отдаёт предметы (выход бура и сборщика)
PORT_STORE = "store"    # и то и другое (склад, лоток конвейера)
PORT_FUEL = "fuel"      # топливо генератора

#: Что здание делает само (см. `sim/buildings.py`).
WORK_MINE = "mine"
WORK_MOVE = "move"
WORK_CRAFT = "craft"
WORK_ROBOT = "robot"    # мастерская: собирает робота из корпуса и модулей (M7)

#: Контейнер лаборатории: слоты, в которые игрок (или конвейер) кладёт образцы.
LAB_SAMPLES = "samples"

#: Порядок построек: в нём предметы-постройки лежат в таблице забега (и в
#: галерее). Клавиш «режима стройки» больше нет — постройка ставится из хотбара.
BUILD_ORDER = (LAMP, LAB, DRILL, CONVEYOR, WIRE, STORAGE, ASSEMBLER, GENERATOR,
               TURRET, WALL, WORKSHOP)

#: На какой тайл ставится здание: свободная земля или залежь (бур).
PLACE_GROUND = "ground"
PLACE_DEPOSIT = "deposit"


@dataclass(frozen=True)
class ContainerDef:
    """Контейнер здания: сколько слотов, по скольку рисовать в строке и его роль."""

    id: str
    name: str
    slots: int
    cols: int
    role: str = PORT_STORE


#: Роли, реликвии которых открывают постройки (`unlock_role`). id роли — он же
#: id базового рецепта этой роли (см. `data/recipes.py`): отдельной ветки
#: «реликвия на постройку» нет, поэтому и логика реликвий не меняется.
ROLE_STRUCTURE = "structure"
ROLE_AUTOMATION = "automation"
ROLE_ENERGY = "energy"
ROLE_AMMO = "ammo"


@dataclass(frozen=True)
class WeaponDef:
    """Модуль оружия на стационарном корпусе (plan/04): турель = корпус + модуль.

    Мастерская и сборка корпуса с модулями — M7; до неё турель описана в
    данных готовым сочетанием, но поля те же, что будут у модуля.
    """

    id: str
    range: float          # тайлы
    damage: float
    cooldown: float       # с между выстрелами
    #: Сколько выстрелов даёт одна единица предмета «боеприпас».
    shots_per_ammo: int


def _item_recipe(kind: str, requires: tuple[tuple[Prop, int], ...],
                 craft_time: float) -> Recipe:
    """Рецепт предмета-постройки: обычная схема «свойство × количество» (plan/02).

    🔧 цены дешёвые намеренно: постройка — расходник, а не достижение. Время
    крафта относится к сборщику: руками крафт мгновенный (🔧 `HANDCRAFT_TIME`).
    """
    return Recipe(id=kind, role=kind, requires=requires, outputs=1,
                  craft_time=craft_time)


@dataclass(frozen=True)
class BuildingDef:
    id: str
    name: str
    shape: str
    color: tuple[int, int, int]
    containers: tuple[ContainerDef, ...] = ()
    #: Какой контейнер здание обрабатывает само: лаборатория берёт в работу
    #: образцы, у склада такого нет. None — здание ничего не анализирует.
    analyzes: str | None = None
    #: Что здание делает само (WORK_*). None — здание просто стоит.
    work: str | None = None
    #: Рецепт **предмета-постройки** («свойство × количество», plan/02). Ставится
    #: здание именно этим предметом, поэтому отдельной «цены клика» больше нет:
    #: цена постройки — цена её предмета, и она видна в панели крафта.
    recipe: Recipe | None = None
    #: Куда здание ставится: свободная земля или залежь (`PLACE_*`).
    placeable_on: str = PLACE_GROUND
    #: Снимается ли здание обратно (удержание ЛКМ). Ядро базы и то, что ломать
    #: нельзя по замыслу, ставит здесь False.
    dismantlable: bool = True
    #: Реликвия какой роли открывает рецепт предмета. None — известен с начала
    #: забега (лампа и лаборатория: без них первая ночь непроходима).
    unlock_role: str | None = None
    hp: int = 100
    #: Приоритет цели у врагов (plan/03: стена 1, конвейер/провод 3, бур 4 …).
    enemy_priority: int = 5
    needs_power: bool = False
    power_out: float = 0.0
    power_use: float = 0.0
    #: Проводит ли здание энергию дальше. Сеть собирается из соседних тайлов, и
    #: провод — это её плитка: он ничего не даёт и не тратит, а только тянет
    #: линию через пустое место к далёкому потребителю.
    conducts: bool = False
    #: Топливо генератора: свойство предмета (сырьё с 🔥) или id (предмет роли).
    fuel: tuple[Prop | str, ...] = ()
    #: Оружие (у турели): стреляет само по ближайшему врагу в радиусе (M5).
    weapon: WeaponDef | None = None
    #: Корпус (plan/04): «static» у турели и стены. Пока только метка данных.
    chassis: str | None = None

    # --------------------------------------------------------------- порты
    def _first(self, role: str) -> str | None:
        return next((c.id for c in self.containers if c.role == role), None)

    @property
    def input_id(self) -> str | None:
        """Контейнер, в который конвейер кладёт предметы."""
        return self._first(PORT_IN)

    @property
    def output_id(self) -> str | None:
        """Контейнер, из которого конвейер забирает предметы."""
        return self._first(PORT_OUT)

    @property
    def accept_id(self) -> str | None:
        """Куда конвейер кладёт: вход, иначе склад, иначе топку генератора."""
        return (self.input_id or self._first(PORT_STORE)
                or self._first(PORT_FUEL))

    @property
    def take_id(self) -> str | None:
        """Откуда конвейер забирает: выход, иначе склад."""
        return self.output_id or self._first(PORT_STORE)

    @property
    def on_deposit(self) -> bool:
        """Ставится ли здание только на залежь (бур)."""
        return self.work == WORK_MINE

    @property
    def work_container_id(self) -> str | None:
        """Контейнер, над которым здание работает само: по нему окно рисует прогресс."""
        if self.analyzes is not None:
            return self.analyzes
        if self.work in (WORK_CRAFT, WORK_ROBOT):
            return self.input_id
        return None


BUILDING_TYPES: dict[str, BuildingDef] = {
    # Лампа — тоже здание: у неё есть цена, HP и потребление. Но живёт она не в
    # `sim/buildings.py`, а в `sim/light.py`: лампа работает со слоем света, и
    # пересчёт слоя идёт только по её включению и выключению (plan/01).
    LAMP: BuildingDef(
        LAMP, "Лампа", "circle", (238, 214, 132),
        recipe=_item_recipe(LAMP, ((Prop.LUMINOUS, 2),), 1.0),
        hp=20, enemy_priority=5, needs_power=True, power_use=config.LAMP_POWER_USE,
    ),
    LAB: BuildingDef(
        LAB, "Лаборатория", "cross", (188, 208, 232),
        containers=(ContainerDef(LAB_SAMPLES, "Образцы", 2, 2, PORT_IN),),
        analyzes=LAB_SAMPLES,
        recipe=_item_recipe(LAB, ((Prop.LUMINOUS, 2), (Prop.TOUGH, 2)), 2.0),
        hp=60, enemy_priority=4, needs_power=True, power_use=2.0,
    ),
    DRILL: BuildingDef(
        DRILL, "Бур", "gear", (206, 176, 132),
        containers=(ContainerDef("out", "Выход", 2, 2, PORT_OUT),),
        work=WORK_MINE,
        recipe=_item_recipe(DRILL, ((Prop.CUTTING, 3), (Prop.TOUGH, 2)), 2.5),
        placeable_on=PLACE_DEPOSIT, unlock_role=ROLE_AUTOMATION,
        hp=60, enemy_priority=4, needs_power=True, power_use=2.0,
    ),
    CONVEYOR: BuildingDef(
        CONVEYOR, "Конвейер", "triangle", (168, 176, 196),
        containers=(ContainerDef("belt", "Лоток", 1, 1, PORT_STORE),),
        work=WORK_MOVE,
        recipe=_item_recipe(CONVEYOR, ((Prop.CONDUCTIVE, 2),), 1.0),
        unlock_role=ROLE_AUTOMATION,
        hp=30, enemy_priority=3, needs_power=True, power_use=1.0,
    ),
    WIRE: BuildingDef(
        WIRE, "Провод", "bolt", (232, 208, 120),
        recipe=_item_recipe(WIRE, ((Prop.CONDUCTIVE, 1),), 0.5),
        unlock_role=ROLE_AUTOMATION,
        hp=20, enemy_priority=3, conducts=True,
    ),
    STORAGE: BuildingDef(
        STORAGE, "Склад", "square", (176, 188, 208),
        containers=(ContainerDef("store", "Склад", 12, 4),),
        recipe=_item_recipe(STORAGE, ((Prop.TOUGH, 4),), 2.0),
        unlock_role=ROLE_STRUCTURE,
        hp=80, enemy_priority=6,
    ),
    ASSEMBLER: BuildingDef(
        ASSEMBLER, "Сборщик", "hexagon", (196, 156, 220),
        containers=(ContainerDef("in", "Вход", 3, 3, PORT_IN),
                    ContainerDef("out", "Выход", 2, 2, PORT_OUT)),
        work=WORK_CRAFT,
        recipe=_item_recipe(ASSEMBLER, ((Prop.CONDUCTIVE, 3), (Prop.TOUGH, 3)), 3.0),
        unlock_role=ROLE_AUTOMATION,
        hp=70, enemy_priority=4, needs_power=True, power_use=3.0,
    ),
    GENERATOR: BuildingDef(
        GENERATOR, "Генератор", "star", (240, 168, 96),
        containers=(ContainerDef("fuel", "Топливо", 2, 2, PORT_FUEL),),
        recipe=_item_recipe(GENERATOR, ((Prop.FLAMMABLE, 3), (Prop.CONDUCTIVE, 2)), 2.5),
        unlock_role=ROLE_ENERGY,
        hp=80, enemy_priority=7, power_out=6.0,
        fuel=(Prop.FLAMMABLE, "energy"),
    ),
    # Турель (M5): стационарный корпус + модуль «быстрое оружие». Боеприпасы —
    # предмет роли «боеприпас» в контейнере `ammo` (его же довозит конвейер).
    TURRET: BuildingDef(
        TURRET, "Турель", "hexagon", (232, 120, 96),
        containers=(ContainerDef("ammo", "Боеприпасы", 2, 2, PORT_IN),),
        recipe=_item_recipe(TURRET, ((Prop.TOUGH, 3), (Prop.UNSTABLE, 2)), 3.0),
        unlock_role=ROLE_AMMO,
        hp=150, enemy_priority=8, chassis="static",
        weapon=WeaponDef("gun_fast", range=config.TURRET_RANGE,
                         damage=config.TURRET_DAMAGE, cooldown=config.TURRET_COOLDOWN,
                         shots_per_ammo=config.TURRET_SHOTS_PER_AMMO),
    ),
    # Стена (plan/03): блок с минимальным приоритетом у врагов. Для поля пути
    # это дорогой тайл, а не непроходимый (plan/05): враг ломает её, только
    # если обхода нет.
    WALL: BuildingDef(
        WALL, "Стена", "square", (150, 156, 170),
        recipe=_item_recipe(WALL, ((Prop.TOUGH, 1),), 0.5),
        unlock_role=ROLE_STRUCTURE,
        hp=300, enemy_priority=1, chassis="static",
    ),
    # Мастерская (M7, plan/03): собирает робота (корпус + модули) из предметов
    # ролей во входе; что собирать — выбирает игрок кнопкой в окне.
    WORKSHOP: BuildingDef(
        WORKSHOP, "Мастерская", "hexagon", (120, 200, 230),
        containers=(ContainerDef("in", "Материалы", 6, 3, PORT_IN),),
        work=WORK_ROBOT,
        recipe=_item_recipe(WORKSHOP, ((Prop.TOUGH, 3), (Prop.CONDUCTIVE, 3)), 3.0),
        unlock_role=ROLE_AUTOMATION,
        hp=120, enemy_priority=9, needs_power=True, power_use=3.0,
    ),
}

#: Рецепты предметов-построек: те же «свойство × количество», что у ролей, и
#: тот же крафт — отдельной ветки «постройка» в симуляции нет.
BUILDING_RECIPES: tuple[Recipe, ...] = tuple(
    definition.recipe for definition in BUILDING_TYPES.values()
    if definition.recipe is not None)

#: Постройки, известные с начала забега: их `unlock_role` пуст. Лампа нужна к
#: первой ночи, лаборатория — чтобы открывать свойства ресурсов (plan/02).
BUILDING_START: tuple[str, ...] = tuple(
    kind for kind, definition in BUILDING_TYPES.items()
    if definition.unlock_role is None)


def building_item(kind: str) -> ItemDef:
    """Предмет-постройка: значок и имя — от здания, свойства ему не нужны.

    Значок рисуется спрайтом самого здания (`render/icons.py`), поэтому в
    инвентаре видно ровно то, что появится на тайле.
    """
    definition = BUILDING_TYPES[kind]
    return ItemDef(
        id=definition.id,
        name=definition.name,
        props=frozenset(),
        color=definition.color,
        shape=definition.shape,
        crafted=True,
        building=definition.id,
        stack_max=config.BUILDING_STACK,
    )


BUILDING_ITEMS: tuple[ItemDef, ...] = tuple(
    building_item(kind) for kind in BUILD_ORDER)


def building(kind: str) -> BuildingDef:
    return BUILDING_TYPES[kind]


def containers(kind: str) -> tuple[ContainerDef, ...]:
    return BUILDING_TYPES[kind].containers


def recipe_of(kind: str) -> Recipe | None:
    """Рецепт предмета постройки (plan/02). None — если здание не крафтится."""
    return BUILDING_TYPES[kind].recipe


def unlocked_by(role_id: str) -> tuple[str, ...]:
    """Постройки, которые открывает реликвия этой роли.

    Отдельного «реликвия на постройку» нет: базовая реликвия роли открывает и
    рецепт роли, и предметы построек этой роли (`unlock_role`).
    """
    return tuple(kind for kind, definition in BUILDING_TYPES.items()
                 if definition.unlock_role == role_id)
