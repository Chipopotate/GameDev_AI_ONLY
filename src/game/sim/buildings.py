"""Здания (plan/03): лаборатория, бур, конвейер, провода, склад, сборщик, генератор.

Здание — это **набор контейнеров из данных** (`data/buildings.py`) плюс своя
работа над ними. Работа бывает разная, и вся она идёт сама, без кнопок: игрок
кладёт предметы и уходит, а результат появляется к его возвращению (прогресс
идёт в `sim.update`, значит пауза его останавливает, а уход дрона — нет).

- **лаборатория** анализирует образцы в своих слотах (`analyzes`);
- **бур** (`WORK_MINE`) добывает из залежи под собой, складывая добычу в выход;
- **конвейер** (`WORK_MOVE`) переносит предметы: сначала отдаёт вперёд, потом
  берёт из-за спины — так из бур→конвейер→склад собирается линия;
- **сборщик** (`WORK_CRAFT`) собирает предмет роли по выбранному рецепту,
  тратя 🔧 `Recipe.craft_time` — то время крафта, которое книга рецептов не
  использует (plan/02);
- **склад** ничего не делает: это просто большой контейнер.

Три решения, которые здесь видно:

- **у каждого слота лаборатории свой прогресс.** Слоты — самостоятельные
  анализаторы, а не очередь: две пачки образцов обрабатываются параллельно;
- **изученный ресурс не тратится.** Если все свойства типа уже открыты, слот
  показывает «уже изучено» и образец остаётся лежать;
- **без энергии здание не работает вовсе** (`Building.powered`): бур стоит,
  сборщик не начинает, слот лаборатории показывает «нет энергии». Сеть считает
  `sim/power.py`, а здание только читает флаг.

Логика без pygame; лампы живут отдельно (`sim/light.py`), потому что работают
со слоем света, а энергия у них общая с зданиями — она в `sim/power.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.game import config
from src.game.data.buildings import (WORK_CRAFT, WORK_MINE, WORK_MOVE,
                                     building as building_def)
from src.game.data.recipes import Recipe, recipe as recipe_def
from src.game.data.resources import Prop, ItemTable
from src.game.sim.research import Knowledge, plan_craft
from src.game.sim.resources import Container, transfer
from src.game.sim.world import World

#: Состояния слота образцов для интерфейса (plan/03: пусто / анализ / изучено / энергия).
SLOT_EMPTY = "empty"
SLOT_ANALYZING = "analyzing"
SLOT_STUDIED = "studied"
SLOT_POWER = "power"

#: Состояния сборщика для интерфейса. «Нет энергии» у него общее со слотом:
#: причина одна и та же, и текст в окне должен быть один.
CRAFT_NONE = "no_recipe"
CRAFT_ITEMS = "no_materials"
CRAFT_WORK = "crafting"

#: Направления конвейера: шаг по тайлам. Порядок — для клавиши поворота.
DIRECTIONS: tuple[tuple[int, int], ...] = ((1, 0), (0, 1), (-1, 0), (0, -1))


@dataclass
class SampleJob:
    """Анализ одного образца: что именно разбирают и насколько продвинулись."""

    item_id: str
    progress: float = 0.0


@dataclass
class Building:
    """Построенное здание на одном тайле карты."""

    id: int
    kind: str
    x: int
    y: int
    #: Контейнеры из данных здания: id → содержимое.
    containers: dict[str, Container] = field(default_factory=dict)
    #: Работа по слотам анализируемого контейнера (у лаборатории — образцы).
    jobs: list[SampleJob | None] = field(default_factory=list)
    processed: int = 0
    #: Есть ли энергия в сети (M4). По умолчанию True: питание пересчитывается
    #: каждый шаг симуляции, а система зданий проверяется и без сети.
    powered: bool = True
    #: Нужна ли сети энергия прямо сейчас — по этому флагу генератор жжёт топливо.
    wants_power: bool = False
    #: Сколько секунд генератор ещё проработает на текущей единице топлива.
    burn: float = 0.0
    #: Куда конвейер двигает предметы (шаг по тайлам).
    direction: tuple[int, int] = DIRECTIONS[0]
    #: Отсчёт конвейера до следующего переноса, с.
    move_timer: float = 0.0
    #: Прогресс собственной работы здания: бурение или сборка, 0..1.
    work_progress: float = 0.0
    #: Выбранный рецепт сборщика (id из `data/recipes.py`) — его ставит игрок в окне.
    recipe: str | None = None
    #: Счётчики сделанного: добыто буром, собрано сборщиком.
    mined: int = 0
    made: int = 0

    # ---------------------------------------------------------- контейнеры
    def container(self, container_id: str | None) -> Container | None:
        if container_id is None:
            return None
        return self.containers.get(container_id)

    @property
    def analyzed(self) -> Container | None:
        """Контейнер, который здание обрабатывает само (лаборатория — образцы)."""
        return self.container(building_def(self.kind).analyzes)

    @property
    def input(self) -> Container | None:
        """Вход: куда конвейер кладёт и откуда сборщик берёт материалы."""
        return self.container(building_def(self.kind).input_id)

    @property
    def output(self) -> Container | None:
        """Выход: откуда конвейер забирает готовое."""
        return self.container(building_def(self.kind).output_id)

    @property
    def is_empty(self) -> bool:
        """Нет ли у здания ни одной вещи (нужно сносу и интерфейсу)."""
        return all(c.is_empty for c in self.containers.values())

    # ------------------------------------------------------------- анализ
    @property
    def busy(self) -> bool:
        return any(job is not None for job in self.jobs)

    @property
    def current(self) -> str | None:
        """Первый образец в работе (для полосы прогресса над зданием)."""
        for job in self.jobs:
            if job is not None:
                return job.item_id
        return None

    @property
    def progress(self) -> float:
        """Прогресс самой продвинутой линии — то, что рисуется над зданием."""
        return max((job.progress for job in self.jobs if job is not None), default=0.0)

    def progress_of(self, index: int) -> float:
        """Прогресс конкретного слота (полоски в окне лаборатории)."""
        job = self.jobs[index] if 0 <= index < len(self.jobs) else None
        return job.progress if job is not None else 0.0

    @property
    def full(self) -> bool:
        """Заняты ли все слоты образцов (в интерфейсе — «нет места»)."""
        container = self.analyzed
        return container is not None and container.is_full

    @property
    def bar_progress(self) -> float:
        """Что рисовать полосой над зданием: анализ, бурение или сборку, 0..1."""
        if self.current is not None:
            return self.progress
        return self.work_progress

    # ------------------------------------------------------------ поворот
    def rotate(self) -> tuple[int, int]:
        """Поворот конвейера на 90° по часовой стрелке. Возвращает новое направление."""
        index = DIRECTIONS.index(self.direction) if self.direction in DIRECTIONS else 0
        self.direction = DIRECTIONS[(index + 1) % len(DIRECTIONS)]
        return self.direction


@dataclass(frozen=True)
class SampleResult:
    """Итог анализа образца: что лаборатория открыла игроку."""

    building: Building
    item_id: str
    revealed: tuple[Prop, ...]


@dataclass(frozen=True)
class CraftResult:
    """Итог сборки: что сборщик положил в свой выход."""

    building: Building
    item_id: str
    count: int


def slot_status(building: Building, index: int, knowledge: Knowledge) -> str:
    """Что сейчас в слоте образцов. Считается на месте — пауза ничего не меняет.

    Порядок проверок — это и есть правила лаборатории: пусто → уже изучено →
    нет энергии → анализ.
    """
    container = building.analyzed
    if container is None:
        return SLOT_EMPTY
    stack = container.slot(index)
    if stack is None:
        return SLOT_EMPTY
    if not knowledge.unknown(stack.item_id):
        return SLOT_STUDIED
    if not building.powered:
        return SLOT_POWER
    return SLOT_ANALYZING


class BuildingSystem:
    """Здания забега. Строит и сносит их симуляция, здесь — состояние и работа."""

    def __init__(self, world: World | None = None) -> None:
        self.buildings: list[Building] = []
        #: Мир нужен буру: он добывает из залежи под собой. Без мира (тесты
        #: системы зданий) бур просто не находит залежь и стоит.
        self.world = world
        self._next_id = 0

    # ------------------------------------------------------------- запросы
    def add(self, kind: str, x: int, y: int,
            direction: tuple[int, int] | None = None) -> Building:
        """Ставит здание: его контейнеры берутся из данных (`data/buildings.py`)."""
        definition = building_def(kind)
        containers = {c.id: Container(c.slots) for c in definition.containers}
        jobs: list[SampleJob | None] = []
        if definition.analyzes is not None:
            jobs = [None] * containers[definition.analyzes].size
        building = Building(self._next_id, kind, int(x), int(y), containers, jobs,
                            direction=direction or DIRECTIONS[0])
        self._next_id += 1
        self.buildings.append(building)
        return building

    def remove(self, building: Building) -> bool:
        if building not in self.buildings:
            return False
        self.buildings.remove(building)
        return True

    def at(self, x: int, y: int) -> Building | None:
        for building in self.buildings:
            if building.x == x and building.y == y:
                return building
        return None

    def of_kind(self, kind: str) -> tuple[Building, ...]:
        return tuple(b for b in self.buildings if b.kind == kind)

    def nearest(self, x: float, y: float, kind: str | None = None,
                max_dist: float | None = None) -> Building | None:
        """Ближайшее здание (при желании — только своего типа и в радиусе)."""
        best: Building | None = None
        best_dist = math.inf
        for building in self.buildings:
            if kind is not None and building.kind != kind:
                continue
            dist = math.hypot(x - (building.x + 0.5), y - (building.y + 0.5))
            if max_dist is not None and dist > max_dist:
                continue
            if dist < best_dist:
                best, best_dist = building, dist
        return best

    # -------------------------------------------------------------- работа
    def update(self, dt: float, knowledge: Knowledge,
               items: ItemTable | None = None) -> tuple[SampleResult | CraftResult, ...]:
        """Продвигает работу всех зданий. Возвращает итоги законченного.

        Анализ каждого слота идёт своим ходом (у него свой прогресс и своя
        полоса). Прогресс начинается заново, если образец в слоте подменили, —
        иначе новая руда дособрала бы чужой прогресс.
        """
        if dt <= 0.0:
            return ()
        results: list[SampleResult | CraftResult] = []
        for building in self.buildings:
            for index in range(len(building.jobs)):
                result = self._update_slot(building, index, dt, knowledge)
                if result is not None:
                    results.append(result)
            definition = building_def(building.kind)
            if definition.work == WORK_MINE:
                self._update_drill(building, dt)
            elif definition.work == WORK_MOVE:
                self._update_conveyor(building, dt)
            elif definition.work == WORK_CRAFT:
                result = self._update_assembler(building, dt, items)
                if result is not None:
                    results.append(result)
        return tuple(results)

    def _update_slot(self, building: Building, index: int, dt: float,
                     knowledge: Knowledge) -> SampleResult | None:
        """Один слот за один шаг: продолжает, начинает или бросает анализ."""
        container = building.analyzed
        stack = container.slot(index)
        status = slot_status(building, index, knowledge)
        if stack is None or status != SLOT_ANALYZING:
            building.jobs[index] = None               # пусто, изучено или нет энергии
            return None
        job = building.jobs[index]
        if job is None or job.item_id != stack.item_id:
            job = SampleJob(stack.item_id)            # новый образец — с нуля
            building.jobs[index] = job
        job.progress += dt / config.LAB_ANALYZE_TIME
        if job.progress < 1.0:
            return None
        item_id = stack.item_id
        container.take_slot(index, config.LAB_SAMPLE_COST)
        revealed = knowledge.reveal_all(item_id)
        building.jobs[index] = None
        building.processed += 1
        return SampleResult(building, item_id, revealed)

    # ------------------------------------------------------------------ бур
    def _update_drill(self, building: Building, dt: float) -> None:
        """Бур сам добывает из залежи под собой и складывает добычу в выход.

        Без энергии, без залежи или с полным выходом бур стоит: прогресс
        сбрасывается, чтобы он не «докопил» чужое время, пока игрок убирал
        склад.
        """
        deposit = self.world.deposit_at(building.x, building.y) if self.world else None
        out = building.output
        if not building.powered or deposit is None or deposit.depleted or out is None \
                or not out.can_accept(deposit.resource_id, 1):
            building.work_progress = 0.0
            return
        building.work_progress += dt / config.DRILL_TIME_PER_UNIT
        if building.work_progress < 1.0:
            return
        building.work_progress = 0.0
        deposit.amount -= 1
        out.add(deposit.resource_id, 1)
        building.mined += 1

    # ------------------------------------------------------------ конвейер
    def _update_conveyor(self, building: Building, dt: float) -> None:
        """Конвейер: сначала отдаёт вперёд, потом берёт из-за спины.

        Такой порядок и делает линию: пока впереди есть место, предметы уезжают
        дальше, а как только линия забилась — конвейер перестаёт тянуть из
        бура, и добыча встаёт сама (буру некуда складывать). Пока лоток пуст,
        отсчёт держится на готовности: первая же единица поедет сразу.
        """
        building.move_timer = min(building.move_timer + dt, config.CONVEYOR_MOVE_TIME)
        if not building.powered or building.move_timer < config.CONVEYOR_MOVE_TIME:
            return
        if self._conveyor_step(building):
            building.move_timer = 0.0

    def _conveyor_step(self, building: Building) -> bool:
        """Один перенос: вперёд или из-за спины. True — если что-то поехало."""
        dx, dy = building.direction
        ahead = self.at(building.x + dx, building.y + dy)
        behind = self.at(building.x - dx, building.y - dy)
        belt = building.container(building_def(building.kind).take_id)
        if belt is None:
            return False
        if not belt.is_empty and ahead is not None:
            target = ahead.container(building_def(ahead.kind).accept_id)
            if target is not None and transfer(belt, target, 0,
                                              config.CONVEYOR_BATCH) > 0:
                return True
        if behind is None or belt.is_full:
            return False
        source = behind.container(building_def(behind.kind).take_id)
        if source is None:
            return False
        for index in range(source.size):        # берём из первого непустого слота
            if source.slot(index) is not None:
                return transfer(source, belt, index, config.CONVEYOR_BATCH) > 0
        return False

    # ------------------------------------------------------------- сборщик
    def _update_assembler(self, building: Building, dt: float,
                          items: ItemTable | None) -> CraftResult | None:
        """Сборщик собирает предмет роли по выбранному рецепту.

        Материалы берутся из входа по свойствам (тем же `plan_craft`, что и
        крафт в книге рецептов), готовое кладётся в выход. План считается
        **дважды** — на старте и в момент готовности: за время сборки игрок мог
        забрать материалы, и тогда сборка просто не завершается.
        """
        definition = building_def(building.kind)
        source = building.input
        target = building.output
        if items is None or building.recipe is None or source is None or target is None:
            building.work_progress = 0.0
            return None
        recipe: Recipe = recipe_def(building.recipe)
        if not building.powered or plan_craft(recipe, source, items) is None \
                or not target.can_accept(recipe.role, recipe.outputs):
            building.work_progress = 0.0
            return None
        building.work_progress += dt * config.ASSEMBLER_SPEED / recipe.craft_time
        if building.work_progress < 1.0:
            return None
        plan = plan_craft(recipe, source, items)
        if plan is None or not target.can_accept(recipe.role, recipe.outputs):
            building.work_progress = 0.0
            return None
        for item_id, count in plan.items():
            source.remove(item_id, count)
        target.add(recipe.role, recipe.outputs)
        building.work_progress = 0.0
        building.made += recipe.outputs
        return CraftResult(building, recipe.role, recipe.outputs)

    # --------------------------------------------------------- топливо
    def burn_fuel(self, dt: float, items: ItemTable) -> None:
        """Генераторы жгут топливо, пока их сети нужна энергия (M4).

        Одна единица топлива — 🔧 `GENERATOR_FUEL_TIME` секунд работы. Топливом
        считается сырьё со свойством из данных здания и предмет роли
        (plan/03: «генератор — энергия из ресурса с 🔥»): сырьё можно жечь
        сразу, а крафченую энергию — как концентрат. Топливо тратится только
        тогда, когда сети есть что питать (`wants_power`), — иначе генератор
        выжигал бы склад впустую.
        """
        for building in self.buildings:
            definition = building_def(building.kind)
            if not definition.fuel:
                continue
            if building.burn > 0.0:
                building.burn = max(0.0, building.burn - dt)
            if building.burn <= 0.0 and building.wants_power:
                self._load_fuel(building, definition, items)

    def _load_fuel(self, building: Building, definition,
                   items: ItemTable) -> bool:
        """Достаёт одну единицу топлива из топки. False — если топки нет или пусто."""
        container = building.container(definition.accept_id)
        if container is None:
            return False
        for index in range(container.size):
            stack = container.slot(index)
            if stack is not None and _is_fuel(stack.item_id, definition.fuel, items):
                container.take_slot(index, 1)
                building.burn = config.GENERATOR_FUEL_TIME
                return True
        return False

    # ------------------------------------------------------------ отладочно
    def statuses(self, building: Building,
                 knowledge: Knowledge) -> tuple[tuple[str, float], ...]:
        """Состояние и прогресс каждого слота — то, что рисует окно лаборатории."""
        container = building.analyzed
        if container is None:
            return ()
        return tuple((slot_status(building, i, knowledge), building.progress_of(i))
                     for i in range(container.size))

    def work_statuses(self, building: Building, knowledge: Knowledge,
                      items: ItemTable) -> tuple[tuple[str, float], ...]:
        """Строки состояния для контейнера, над которым здание работает само.

        У лаборатории это статусы её слотов, у сборщика — одна строка о том,
        что он делает сейчас. Пусто — если здание работает не над контейнером.
        """
        definition = building_def(building.kind)
        if definition.analyzes is not None:
            return self.statuses(building, knowledge)
        if definition.work == WORK_CRAFT:
            return ((_craft_status(building, items), building.work_progress),)
        return ()

    # ---------------------------------------------------------- для тестов
    def state(self) -> tuple:
        return tuple((b.kind, b.x, b.y,
                      # Экономика M4: питание, поворот, топливо, прогресс работы.
                      b.powered, b.direction, round(b.burn, 6),
                      round(b.work_progress, 6), b.recipe, b.mined, b.made,
                      tuple((b.container(cid).state() if b.container(cid) else ())
                            for cid in sorted(b.containers)),
                      tuple(None if job is None else (job.item_id, round(job.progress, 6))
                            for job in b.jobs),
                      b.processed) for b in self.buildings)


def _is_fuel(item_id: str, fuel: tuple[Prop | str, ...], items: ItemTable) -> bool:
    """Годится ли предмет в топку: совпал id или есть нужное свойство."""
    item = items.get(item_id)
    for mark in fuel:
        if isinstance(mark, Prop):
            if item is not None and mark in item.props:
                return True
        elif mark == item_id:
            return True
    return False


def _craft_status(building: Building, items: ItemTable) -> str:
    """Что показывает строка состояния сборщика (текст — в `ui/item_window.py`)."""
    if building.recipe is None:
        return CRAFT_NONE
    if not building.powered:
        return SLOT_POWER
    source = building.input
    if source is None or plan_craft(recipe_def(building.recipe), source, items) is None:
        return CRAFT_ITEMS
    return CRAFT_WORK
