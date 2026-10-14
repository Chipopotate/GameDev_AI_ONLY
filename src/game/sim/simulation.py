"""Корень симуляции: связывает мир, дрона, инвентарь, часы, свет и исследование.

Здесь нет ни pygame, ни ввода — только логика. Ввод кладёт сюда app.py,
отрисовка читает состояние. Пауза реализована снаружи: app просто не
вызывает `update`.

Правило действий (M2 → M3.5): **что под курсором, то и делаем**.
- залежь + зажатая ЛКМ — добыча;
- залежь + зажатая ПКМ — скан свойств;
- реликвия + зажатая ЛКМ — изучение;
- своя постройка + зажатая ЛКМ — демонтаж (🔧 `DISMANTLE_TIME`), по окончании
  предмет постройки и содержимое падают на землю;
- ПКМ по земле с выбранным слотом-постройкой — поставить её (предмет тратится);
- ПКМ по зданию рядом — его окно с контейнерами (само окно живёт в `ui/`).

**Постройка — это предмет** (plan/03): она крафтится обычным рецептом «свойство ×
количество», лежит в панели быстрого доступа и ставится из выбранного слота.
Ни «режима стройки», ни отдельной цены за клик больше нет: цена здания — цена
его предмета (`data/buildings.py`), а `PlaceBuilding` только тратит единицу.

Работа зданий — энергия (`sim/power.py`): сеть считается каждый шаг, до работы.

Контейнеры и брошенные предметы тоже здесь: инвентарь дрона, хотбар и слоты
лаборатории — один `Container` (`sim/resources.py`), а `drop_position`/
`throw_from` кладут предметы на землю (`sim/drops.py`).
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

from src.game import config
from src.game.data.buildings import (BUILDING_START, BUILDING_TYPES,
                                     LAB, LAMP, PLACE_DEPOSIT, WORK_CRAFT,
                                     WORK_MOVE, building as building_def,
                                     unlocked_by)
from src.game.data.recipes import ROLE_IDS, ROLES, role as role_def
from src.game.data.resources import PROP_NAMES, Prop
from src.game.sim.buildings import DIRECTIONS, Building, BuildingSystem, CraftResult
from src.game.sim.commands import (Command, DismantleBuilding, PlaceBuilding,
                                   SelectHotbar)
from src.game.sim.drops import DropSystem
from src.game.sim.light import Lamp, LightSystem
from src.game.sim.power import PowerNode, PowerSystem
from src.game.sim.research import KnownItems, Knowledge, plan_craft
from src.game.sim.resources import (Cargo, Hotbar, Inventory, Stack, give,
                                    transfer)
from src.game.sim.run_gen import Relic, RunPlan, generate_run
from src.game.sim.time_cycle import EVENT_DAWN, EVENT_DUSK, TimeCycle
from src.game.sim.units import (ACTION_DISMANTLE, ACTION_MINE, ACTION_SCAN,
                                ACTION_STUDY, Drone, DroneAction)
from src.game.sim.world import Deposit, World

#: Лампа — особая постройка: живёт в `sim/light.py`, а не в `sim/buildings.py`.
BUILD_LAMP = LAMP

#: Предмет роли «энергия» — топливо скана и генератора (plan/02, M4).
ENERGY_ITEM = role_def("energy").id

#: Рецепты, известные с начала забега: предметы лампы и лаборатории (plan/02).
#: Без лампы первая ночь непроходима, без лаборатории не открыть свойства.
START_RECIPES: tuple[str, ...] = tuple(
    BUILDING_TYPES[kind].recipe.id for kind in BUILDING_START)


def direction_index(direction: tuple[int, int] | None) -> int | None:
    """Индекс направления в `DIRECTIONS` (или None, если такого нет)."""
    if direction is None:
        return None
    for i, step in enumerate(DIRECTIONS):
        if step == direction:
            return i
    return None


@dataclass(frozen=True)
class CraftOption:
    """Строка панели крафта: **только то, что игрок знает** (plan/02).

    Ни одного поля с сырыми свойствами ресурса здесь нет: `have` считается по
    открытым свойствам, поэтому подсказка не выдаёт, какие из своих ресурсов
    подойдут, пока игрок их не изучил.
    """

    recipe_id: str
    name: str
    #: id предмета-результата (по нему панель берёт значок).
    role: str
    outputs: int
    #: Усилитель роли: тот же предмет, но выгоднее (plan/02). Игрок его изучил,
    #: значит, знает, что это второй вариант.
    amplified: bool
    #: ((имя свойства, нужно, есть известных), …)
    requires: tuple[tuple[str, int, int], ...]
    #: Хватает ли того, что игрок **знает** о своих ресурсах (см. `KnownItems`).
    available: bool


@dataclass
class Toast:
    """Короткое сообщение для HUD (например, «+1 Кразель»)."""

    text: str
    ttl: float
    total: float

    @property
    def alpha_ratio(self) -> float:
        return max(0.0, min(1.0, self.ttl / self.total)) if self.total > 0 else 0.0


class Simulation:
    """Состояние забега и его пошаговое обновление."""

    def __init__(self, seed: int, world: World | None = None) -> None:
        self.seed = seed
        # Свой поток случайности для решений симуляции (какое свойство откроет
        # скан): глобальный random в sim/ запрещён, а общий поток с генерацией
        # карты сделал бы правку одного ломающей другое.
        self._rng = random.Random(seed ^ config.SIM_RNG_SALT)
        self.world = world if world is not None else World.generate(seed)
        # Забег собирается по сиду карты: ресурсы и залежи обязаны сходиться.
        self.run: RunPlan = generate_run(self.world.seed)
        self.run.place_relics(self.world)
        self.drone = Drone.spawn(self.world)
        # Два контейнера дрона и правило «куда что кладётся» — в `Cargo`.
        limits = self.run.items.stack_limits
        self.inventory = Inventory(limits=limits)
        self.hotbar = Hotbar(limits=limits)
        self.cargo = Cargo(self.hotbar, self.inventory, self.run.items)
        self.knowledge = Knowledge(self.run.items)
        # Лампа и лаборатория известны с начала забега (plan/02): лампу к первой
        # ночи открыть нечем, а лаборатория — единственный способ узнать
        # свойства ресурсов, так что реликвия для них была бы замкнутым кругом.
        for recipe_id in START_RECIPES:
            self.knowledge.learn(recipe_id)
        self.buildings = BuildingSystem(self.world)
        self.drops = DropSystem()
        self.time_cycle = TimeCycle()
        self.light = LightSystem(self.world)
        self.power = PowerSystem()

        self.time = 0.0
        self.move_input: tuple[float, float] = (0.0, 0.0)
        # Ввод: игрок смотрит на тайл и держит кнопку. Все поля — чистое
        # состояние ввода, их ставит app.py каждый кадр.
        self.cursor_tile: tuple[int, int] | None = None
        self.cursor_deposit: Deposit | None = None
        self.cursor_relic: Relic | None = None
        self.mine_active = False
        self.scan_active = False
        # Выбранный слот хотбара: из него ставится постройка (plan/03).
        self.selected_slot = 0
        # Поворот призрака (R): индекс в `DIRECTIONS`. None — призрак ещё не
        # крутили, и направление берётся «от дрона» (см. `build_direction`).
        self.build_rotation: int | None = None

        # Выход: что происходит прямо сейчас (читают рендер и HUD).
        self.mine_target: Deposit | None = None
        self.mine_progress = 0.0
        self.mined_total = 0
        self.scan_target: Deposit | None = None
        self.scan_progress = 0.0
        self.scans_done = 0
        self.study_target: Relic | None = None
        self.study_progress = 0.0
        self.dismantle_target: Building | Lamp | None = None
        self.dismantle_progress = 0.0
        self.crafted_total = 0
        self._last_craft_time = -1e9
        # О чём уже сказали «инвентарь полон»: иначе сообщение висело бы на
        # экране непрерывно, пока игрок держит ЛКМ на залежи.
        self._full_notice: str | None = None
        # То же для подбора с земли: сообщение — один раз на предмет, а не
        # каждый шаг, пока дрон стоит на куче.
        self._pickup_notice: str | None = None
        # И то же для скана без энергии: одно сообщение на попытку, а не на кадр.
        self._scan_notice = False

        self.toasts: deque[Toast] = deque(maxlen=config.TOAST_MAX)

    # --------------------------------------------------------------- ввод
    def set_move_input(self, x: float, y: float) -> None:
        self.move_input = (x, y)

    def set_cursor_tile(self, x: int, y: int) -> None:
        """Курсор на тайл: из него выводятся залежь и реликвия под ним."""
        self.cursor_tile = (x, y)
        self.cursor_deposit = self.world.deposit_at(x, y)
        self.cursor_relic = self.run.relic_at(x, y)

    def set_cursor_deposit(self, deposit: Deposit | None) -> None:
        """Залежь под курсором (или None). Нужна и добыче, и подсказке в HUD."""
        self.cursor_deposit = deposit

    def set_mining(self, active: bool) -> None:
        """Зажата ли левая кнопка мыши."""
        self.mine_active = active

    def set_scanning(self, active: bool) -> None:
        """Зажата ли правая кнопка мыши (скан свойств)."""
        self.scan_active = active

    def apply(self, command: Command):
        """Выполняет команду интерфейса (`sim/commands.py`). Возвращает её результат.

        Интерфейс ничего не решает сам: он только собирает команду из ввода и
        отдаёт её сюда, а все проверки — внутри симуляции.
        """
        if isinstance(command, SelectHotbar):
            return self.select_hotbar(command.index)
        if isinstance(command, PlaceBuilding):
            return self.place_building(command.slot, command.tile, command.rotation)
        if isinstance(command, DismantleBuilding):
            return self.dismantle_building(*command.tile)
        raise TypeError(f"неизвестная команда: {command!r}")

    def scroll_hotbar(self, step: int) -> int:
        """Shift+колесо: соседний слот хотбара по кругу. Возвращает новый номер."""
        size = self.hotbar.size
        if size > 0 and step:
            self.select_hotbar((self.selected_slot + step) % size)
        return self.selected_slot

    def select_hotbar(self, index: int) -> bool:
        """Выбор слота панели быстрого доступа (клавиши 1…9, клик по слоту).

        False — если такого слота нет. Из выбранного слота ставится постройка,
        поэтому «что строю» — это выбор слота, а не режим.
        """
        if not 0 <= index < self.hotbar.size:
            return False
        if index == self.selected_slot:
            return True
        self.selected_slot = index
        # Призрак поворачивается вместе со слотом: у нового здания своё
        # направление, и старое «накрученное» к нему не относится.
        self.build_rotation = None
        return True

    def selected_stack(self) -> Stack | None:
        """Стак в выбранном слоте хотбара (None — слот пуст)."""
        return self.hotbar.slot(self.selected_slot)

    def selected_building(self) -> str | None:
        """Тип здания в выбранном слоте. None — слот пуст или там не постройка.

        Это единственное место, где ответ на «что сейчас ставится»: слот с
        ресурсом не отличается от пустого слота.
        """
        stack = self.selected_stack()
        if stack is None:
            return None
        item = self.run.items.get(stack.item_id)
        return item.building if item is not None else None

    # ------------------------------------------------------------- дальности
    def in_mine_range(self, deposit: Deposit) -> bool:
        """Достаёт ли дрон до залежи — единственное место с этим правилом."""
        return self.drone.distance_to_tile(deposit.x, deposit.y) <= config.MINE_RANGE

    def in_scan_range(self, deposit: Deposit) -> bool:
        return self.drone.distance_to_tile(deposit.x, deposit.y) <= config.SCAN_RANGE

    def in_study_range(self, relic: Relic) -> bool:
        return self.drone.distance_to_tile(relic.x, relic.y) <= config.RELIC_STUDY_RANGE

    # ------------------------------------------------------------ обновление
    def update(self, dt: float) -> None:
        """Один фиксированный шаг симуляции."""
        if dt <= 0.0:
            return
        self.time += dt
        self._update_time(dt)
        self.drone.move(self.world, self.move_input[0], self.move_input[1], dt)
        self._update_actions(dt)
        self._update_scanning(dt)
        self._update_drone_action()
        # Сначала сеть, потом работа: здание этого шага должно знать, есть ли
        # у него энергия (M4). Топливо горит уже после пересчёта — по спросу.
        self._update_power(dt)
        self._update_buildings(dt)
        self._update_drops(dt)
        self._update_toasts(dt)

    def _update_time(self, dt: float) -> None:
        """Часы забега и реакции на смену фазы."""
        for event in self.time_cycle.update(dt):
            if event == EVENT_DUSK:
                self.toast(f"Ночь {self.time_cycle.day_number}")
            elif event == EVENT_DAWN:
                self.toast(f"День {self.time_cycle.day_number}")

    # ---------------------------------------------------- работа руками
    def _update_actions(self, dt: float) -> None:
        """Зажатая ЛКМ: изучение реликвии, демонтаж постройки или добыча залежи."""
        if not self.mine_active:
            self._reset_mining()
            self._reset_study()
            self._reset_dismantle()
            return
        relic = self.cursor_relic
        # Реликвия важнее: она не может стоять на залежи, так что выбор
        # однозначен, а случайное «промахнулся мимо реликвии» не грозит.
        if relic is not None and not relic.studied and self.in_study_range(relic):
            self._reset_mining()
            self._reset_dismantle()
            self._update_study(dt, relic)
            return
        # Своя постройка важнее залежи: бур стоит **на** залежи, и «добыть
        # сквозь бур» — это точно не то, чего хотел игрок.
        if self.dismantle_candidate() is not None:
            self._reset_mining()
            self._reset_study()
            self._update_dismantle(dt)
            return
        self._reset_study()
        self._reset_dismantle()
        self._update_mining(dt)

    def _update_mining(self, dt: float) -> None:
        target = self.cursor_deposit
        # Добываем только то, на что смотрит игрок, и только если дрон достаёт.
        # Ушёл курсор, отпустили кнопку, кончилась залежь — прогресс сбрасывается.
        if target is None or target.depleted or not self.in_mine_range(target):
            self._reset_mining()
            return
        # Полное хранилище останавливает добычу: ресурс из залежи не пропадает,
        # но и в никуда не девается — игрок освобождает слот и продолжает.
        # Цель при этом не ставится: иначе луч «жёг» бы залежь, а HUD писал
        # «Добываю», хотя добычи нет.
        if not self.cargo.can_accept(target.resource_id, config.MINE_YIELD):
            self.mine_target = None
            self.mine_progress = 0.0
            if self._full_notice != target.resource_id:
                self._full_notice = target.resource_id
                self.toast("Инвентарь полон")
            return
        if target is not self.mine_target:
            self.mine_target = target
            self.mine_progress = 0.0
        self.mine_progress += dt / config.MINE_TIME_PER_UNIT
        while self.mine_progress >= 1.0 and target.amount > 0:
            if not self.cargo.can_accept(target.resource_id, config.MINE_YIELD):
                self.mine_progress = 0.0
                break
            self.mine_progress -= 1.0
            target.amount -= 1
            self.cargo.add(target.resource_id, config.MINE_YIELD)
            self._full_notice = None
            self.mined_total += config.MINE_YIELD
            name = self.run.items[target.resource_id].name
            self.toast(f"+{config.MINE_YIELD} {name}")
            self._note_pickup(target.resource_id, name)
        if target.amount <= 0:
            self.mine_target = None
            self.mine_progress = 0.0

    def _note_pickup(self, item_id: str, name: str) -> None:
        """Первый подбор показывает одно свойство ресурса (plan/02)."""
        for prop in self.knowledge.note_pickup(item_id):
            self.toast(f"{name}: видно «{PROP_NAMES[prop]}»")

    def _reset_mining(self) -> None:
        self.mine_target = None
        self.mine_progress = 0.0
        self._full_notice = None

    def _update_study(self, dt: float, relic: Relic) -> None:
        if self.study_target is not relic:
            self.study_target = relic
            self.study_progress = 0.0
        self.study_progress += dt / config.RELIC_STUDY_TIME
        if self.study_progress >= 1.0:
            self._reset_study()
            self.study_relic(relic)

    def _reset_study(self) -> None:
        self.study_target = None
        self.study_progress = 0.0

    # ------------------------------------------------------------- демонтаж
    def dismantle_candidate(self) -> Building | Lamp | None:
        """Своя постройка под курсором, которую можно снять прямо сейчас.

        Единственное место с этим правилом: ЛКМ держат и по залежи, и по
        реликвии, и по зданию, поэтому «что под курсором» решается здесь.
        """
        tile = self.cursor_tile
        if tile is None:
            return None
        target: Building | Lamp | None = self.buildings.at(*tile)
        if target is None:
            target = self.light.lamp_at(*tile)
        if target is None or not self.in_build_range(target.x, target.y):
            return None
        kind = target.kind if isinstance(target, Building) else BUILD_LAMP
        if not building_def(kind).dismantlable:
            return None
        return target

    def _update_dismantle(self, dt: float) -> None:
        """Держим ЛКМ на своей постройке — она разбирается за 🔧 `DISMANTLE_TIME`."""
        target = self.dismantle_candidate()
        if target is None:
            self._reset_dismantle()
            return
        if target is not self.dismantle_target:
            self.dismantle_target = target
            self.dismantle_progress = 0.0
        self.dismantle_progress += dt / config.DISMANTLE_TIME
        if self.dismantle_progress < 1.0:
            return
        x, y = target.x, target.y
        # Сбрасываем до сноса: после него цель — уже не то, что стоит на карте.
        self._reset_dismantle()
        self.dismantle_building(x, y)

    def _reset_dismantle(self) -> None:
        self.dismantle_target = None
        self.dismantle_progress = 0.0

    def dismantle_building(self, x: int, y: int) -> bool:
        """Снимает свою постройку: предмет падает на землю рядом с ней.

        Возврата «в руку» нет намеренно (plan/03): снятое лежит там же, где
        стояло, и подбирается обычным автоподбором — так снос не превращается в
        бесплатное перемещение здания по карте. Содержимое контейнеров
        высыпается вокруг, и сумма предметов сохраняется.
        """
        if not self.in_build_range(x, y):
            self.toast("Слишком далеко")
            return False
        lamp = self.light.lamp_at(x, y)
        if lamp is not None:
            # Флаг из данных действует и на лампу: она такое же здание (plan/03).
            if not building_def(BUILD_LAMP).dismantlable:
                self.toast(f"{building_def(BUILD_LAMP).name} снять нельзя")
                return False
            self.light.remove_lamp(lamp)
            self.drops.spawn_tile(BUILD_LAMP, 1, x, y)
            self.toast(f"Разобрано: {building_def(BUILD_LAMP).name}")
            return True
        building = self.buildings.at(x, y)
        if building is None:
            return False
        definition = building_def(building.kind)
        if not definition.dismantlable:
            self.toast(f"{definition.name} снять нельзя")
            return False
        # Сначала здание исчезает, потом на его тайл падает предмет постройки:
        # тайл к этому моменту уже свободен, и проверять его не нужно.
        if not self.demolish(building):
            return False
        self.drops.spawn_tile(building.kind, 1, x, y)
        self.toast(f"Разобрано: {definition.name}")
        return True

    # ------------------------------------------------- действие для рендера
    def _update_drone_action(self) -> None:
        """Сводит «что дрон делает» в одно поле — рендер читает только его.

        Луч лазера рисуется ровно тогда, когда здесь не None, поэтому таблица
        «действие → цвет» живёт в `render/theme.py`, а не размазана по рендеру.
        """
        if self.study_target is not None:
            relic = self.study_target
            self.drone.action = DroneAction(ACTION_STUDY,
                                            (relic.x + 0.5, relic.y + 0.5),
                                            self.study_progress)
        elif self.dismantle_target is not None:
            target = self.dismantle_target
            self.drone.action = DroneAction(ACTION_DISMANTLE,
                                            (target.x + 0.5, target.y + 0.5),
                                            self.dismantle_progress)
        elif self.mine_target is not None:
            deposit = self.mine_target
            self.drone.action = DroneAction(ACTION_MINE,
                                            (deposit.x + 0.5, deposit.y + 0.5),
                                            self.mine_progress)
        elif self.scan_target is not None:
            deposit = self.scan_target
            self.drone.action = DroneAction(ACTION_SCAN,
                                            (deposit.x + 0.5, deposit.y + 0.5),
                                            self.scan_progress)
        else:
            self.drone.action = DroneAction()

    def study_relic(self, relic: Relic) -> bool:
        """Изучает реликвию: открывает рецепт её роли. False — если уже изучена.

        ❓ plan/02 оставлял за автором, брать ли за изучение ресурс: пока
        🔧 `RELIC_STUDY_COST` = None, изучение бесплатное (как в M3). Число в
        конфиге включает плату без правки кода.
        """
        if relic.studied or not relic.placed:
            return False
        if config.RELIC_STUDY_COST is not None:
            item_id, count = config.RELIC_STUDY_COST
            if not self.cargo.has(item_id, count):
                self.toast("Для изучения не хватает ресурсов")
                return False
        relic.studied = True
        if config.RELIC_STUDY_COST is not None:
            self.cargo.remove(*config.RELIC_STUDY_COST)
        self.knowledge.learn(relic.recipe_id)
        recipe = self.run.recipe(relic.recipe_id)
        role = role_def(recipe.role)
        what = "Усилитель" if relic.amplified else "Рецепт"
        self.toast(f"{what} открыт: {role.short}")
        # Та же реликвия открывает предметы построек своей роли (plan/03):
        # отдельной «реликвии на постройку» нет, и логика реликвий не менялась.
        if not relic.amplified:
            for kind in unlocked_by(role.id):
                definition = building_def(kind)
                if definition.recipe is not None \
                        and self.knowledge.learn(definition.recipe.id):
                    self.toast(f"Открыта постройка: {definition.name}")
        return True

    # --------------------------------------------------------------- скан
    def _update_scanning(self, dt: float) -> None:
        """Зажатая ПКМ по залежи: дрон сканирует её свойства (plan/02).

        Скан быстрый, но требует близости и энергии: с M4 каждый скан списывает
        🔧 `SCAN_ENERGY_COST` предметов роли «энергия». Нет энергии — скан не
        идёт, и об этом говорится один раз (как «инвентарь полон» при добыче).

        С постройкой в руке ПКМ занята постановкой (plan/10): по залежи она
        ничего не делает — иначе игрок, целясь буром, случайно тратил бы
        энергию на скан.
        """
        target = self.cursor_deposit
        if self.selected_building() is not None:
            self._reset_scan()
            return
        if not self.scan_active or target is None or target.depleted \
                or not self.in_scan_range(target):
            self._reset_scan()
            return
        if not self._scan_ready(target):
            self._reset_scan()
            # Энергии нет — говорим один раз: игрок держит ПКМ и без этого
            # получал бы сообщение каждый кадр.
            if not self._scan_notice:
                self._scan_notice = True
                self.toast(f"Скан: нужна энергия ×{config.SCAN_ENERGY_COST}")
            return
        self._scan_notice = False
        if self.scan_target is not target:
            self.scan_target = target
            self.scan_progress = 0.0
        self.scan_progress += dt / config.SCAN_TIME
        if self.scan_progress < 1.0:
            return
        # Сканируем дальше, только если есть что открывать: иначе игрок просто
        # держит кнопку и получает бесконечные сообщения.
        if self.knowledge.unknown(target.resource_id):
            self._finish_scan(target)
        else:
            self._reset_scan()

    def _scan_ready(self, deposit: Deposit) -> bool:
        """Есть ли что открывать и есть ли на это энергия (M4)."""
        if not self.knowledge.unknown(deposit.resource_id):
            return False
        return self.cargo.has(ENERGY_ITEM, config.SCAN_ENERGY_COST)

    def _finish_scan(self, deposit: Deposit) -> None:
        self.scan_target = None
        self.scan_progress = 0.0
        self.cargo.remove(ENERGY_ITEM, config.SCAN_ENERGY_COST)
        prop = self.knowledge.reveal_one(deposit.resource_id, self._rng)
        if prop is None:
            return
        self.scans_done += 1
        name = self.run.items[deposit.resource_id].name
        self.toast(f"{name}: свойство «{PROP_NAMES[prop]}»")

    def _reset_scan(self) -> None:
        self.scan_target = None
        self.scan_progress = 0.0

    # ------------------------------------------------------------- энергия
    def _power_nodes(self) -> list[PowerNode]:
        """Точки сети: здания, которые проводят, дают или тратят, и лампы.

        Генератор даёт энергию, только пока горит его топливо (`burn`), — так
        сеть сама выключается, когда топка опустела.

        Провода входят в сеть как узлы без мощности: они ничего не дают и не
        тратят, но тянут сеть через пустое место.
        """
        nodes: list[PowerNode] = []
        for building in self.buildings.buildings:
            definition = building_def(building.kind)
            if not (definition.conducts or definition.power_use or definition.power_out):
                continue
            supply = definition.power_out if building.burn > 0.0 else 0.0
            nodes.append(PowerNode(building.x, building.y, building,
                                   supply=supply, demand=definition.power_use))
        for lamp in self.light.lamps:
            nodes.append(PowerNode(lamp.x, lamp.y, lamp,
                                   demand=config.LAMP_POWER_USE))
        return nodes

    def _update_power(self, dt: float) -> None:
        """Пересчитывает сети и раздаёт результат: лампе — свет, зданию — работу."""
        nodes = self._power_nodes()
        self.power.update(nodes)
        for node in nodes:
            if isinstance(node.target, Lamp):
                self.light.set_lamp_on(node.target, node.powered)
            else:
                node.target.powered = node.powered
                node.target.wants_power = node.wants
        self.buildings.burn_fuel(dt, self.run.items)

    @property
    def power_summary(self) -> tuple[float, float]:
        """(выработка, спрос) по всем сетям — для HUD и отладки."""
        return self.power.summary()

    def power_at(self, x: int, y: int) -> bool:
        """Есть ли в тайле точка сети: лампа, провод или здание с мощностью.

        Рендер по этому отвечает, куда тянуть линии провода; сама сеть
        собирается по тому же правилу соседства (см. `sim/power.py`).
        """
        if self.light.lamp_at(x, y) is not None:
            return True
        building = self.buildings.at(x, y)
        if building is None:
            return False
        definition = building_def(building.kind)
        return bool(definition.conducts or definition.power_use or definition.power_out)

    # --------------------------------------------------------- лаборатория
    def _update_buildings(self, dt: float) -> None:
        for result in self.buildings.update(dt, self.knowledge, self.run.items):
            name = self.run.items[result.item_id].name
            if isinstance(result, CraftResult):
                self.toast(f"Сборщик: +{result.count} {name}")
            elif result.revealed:
                props = ", ".join(PROP_NAMES[p] for p in result.revealed)
                self.toast(f"{name}: открыто — {props}")
            else:
                self.toast(f"{name}: анализ завершён")

    def lab_in_range(self) -> Building | None:
        """Лаборатория, до которой дрон дотягивается образцом."""
        return self.buildings.nearest(self.drone.x, self.drone.y, LAB,
                                      config.CONTAINER_RANGE)

    def building_reachable(self, building: Building | None) -> bool:
        """Можно ли открыть окно здания: оно ещё стоит и дрон рядом.

        С M4 так открывается любое здание с контейнерами (склад, сборщик, бур),
        а не только лаборатория. Существование проверяется отдельно от
        расстояния: снесённое здание не должно оставлять открытое окно (app.py).
        """
        if building is None or not building.containers:
            return False
        if building not in self.buildings.buildings:
            return False
        return self.drone.distance_to_tile(building.x, building.y) \
            <= config.CONTAINER_RANGE

    # ------------------------------------------------- предметы на земле
    def drop_position(self) -> tuple[float, float]:
        """Куда падает выброшенный стак: тайлом впереди дрона (см. drops.py)."""
        x = self.drone.x + math.cos(self.drone.facing) * config.DROP_DISTANCE
        y = self.drone.y + math.sin(self.drone.facing) * config.DROP_DISTANCE
        # За край карты предмет не улетает: подобрать его было бы нельзя.
        return (min(max(x, 0.0), config.MAP_W - 0.01),
                min(max(y, 0.0), config.MAP_H - 0.01))

    def throw_stack(self, item_id: str, count: int) -> bool:
        """Кладёт стак на землю перед дроном. Без сообщения — для окна и сброса."""
        return self.drops.spawn(item_id, count, *self.drop_position()) is not None

    def throw_from(self, container, index: int, amount: int | None = None) -> bool:
        """Выбрасывает предмет из слота контейнера (инвентарь, слоты лаборатории).

        `amount=None` — весь стак. False — если слот пуст или брать нечего.
        """
        stack = container.slot(index)
        if stack is None:
            return False
        take = stack.count if amount is None else min(stack.count, max(0, int(amount)))
        if take <= 0 or not self.throw_stack(stack.item_id, take):
            return False
        container.take_slot(index, take)
        name = self.run.items[stack.item_id].name
        self.toast(f"Выброшено: {name} ×{take}")
        return True

    def throw_item(self, index: int, amount: int | None = None) -> bool:
        """Выбрасывает предмет из слота основного инвентаря дрона."""
        return self.throw_from(self.inventory, index, amount)

    def throw_all(self) -> int:
        """Выбрасывает всё из обоих контейнеров — аварийный сброс (отладка и тесты).

        Хотбар тоже: «выбросить всё» — это выбросить всё, что у дрона есть.
        """
        moved = 0
        for container in (self.inventory, self.hotbar):
            for i in range(container.size):
                stack = container.slot(i)
                if stack is not None and self.throw_from(container, i):
                    moved += stack.count
        return moved

    def return_stack(self, stack: Stack | None, *containers) -> None:
        """Возвращает стак «в руки» в контейнеры: что не влезло — падает на землю.

        Так закрывается окно предметов: нести предмет в закрытом окне некуда,
        а терять его нельзя (plan/03).
        """
        if stack is None:
            return
        # По умолчанию — в хранилище дрона по общему правилу (`Cargo`):
        # постройка ложится в хотбар, ресурс — в инвентарь.
        rest = give(stack, *(containers or (self.cargo,)))
        if rest is not None:
            self.throw_stack(rest.item_id, rest.count)

    def _update_drops(self, dt: float) -> None:
        """Подбор предметов под дроном (plan/03).

        Сообщение «инвентарь полон» говорится один раз на предмет: иначе, стоя
        на куче руды с забитым инвентарём, игрок получал бы его каждый кадр.
        """
        picked = self.drops.update(dt, self.cargo, self.drone.x, self.drone.y)
        for result in picked:
            name = self.run.items[result.item_id].name
            if result.count > 0:
                self.toast(f"+{result.count} {name}")
                self._note_pickup(result.item_id, name)
            if result.left > 0:
                self._notice_full(result.item_id, f"Инвентарь полон: {name} остался")
            else:
                self._pickup_notice = None
        if not picked:
            self._pickup_notice = None

    def _notice_full(self, key: str, text: str) -> None:
        """Сообщение о нехватке места — по одному разу на предмет."""
        if self._pickup_notice == key:
            return
        self._pickup_notice = key
        self.toast(text)

    # ---------------------------------------------------------- снос зданий
    def scatter_tiles(self, x: int, y: int) -> list[tuple[int, int]]:
        """Свободные тайлы вокруг точки, по расширяющимся кольцам (plan/03).

        Тайл здания сюда не входит: он оставлен на крайний случай, когда вокруг
        всё занято. Порядок детерминирован (кольцо, затем y, затем x), иначе
        один и тот же снос раскидывал бы предметы по-разному.
        """
        tiles: list[tuple[int, int]] = []
        for radius in range(1, config.DESTROY_SCATTER_RADIUS + 1):
            ring: list[tuple[int, int]] = []
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    tx, ty = x + dx, y + dy
                    if self._drop_tile_is_free(tx, ty):
                        ring.append((tx, ty))
            ring.sort(key=lambda t: (t[1], t[0]))
            tiles.extend(ring)
        return tiles

    def _drop_tile_is_free(self, x: int, y: int) -> bool:
        """Можно ли положить предмет на тайл: земля без здания."""
        if not self.world.is_walkable_tile(x, y):
            return False
        return self.buildings.at(x, y) is None

    def scatter_contents(self, building: Building,
                         ratio: float = config.DESTROY_DROP_RATIO) -> int:
        """Высыпает содержимое здания на землю. Возвращает, сколько выпало.

        Закон сохранения (plan/03): при 🔧 `DESTROY_DROP_RATIO = 1.0` сумма
        предметов до сноса равна сумме на земле после. Если свободных тайлов
        вокруг нет, всё ложится на тайл здания — вещи важнее красоты.
        """
        stacks: list[Stack] = []
        for container in building.containers.values():
            for index in range(container.size):
                stack = container.take_slot(index)
                if stack is None:
                    continue
                count = int(stack.count * ratio)
                if count > 0:
                    stacks.append(Stack(stack.item_id, count))
        if not stacks:
            return 0
        tiles = self.scatter_tiles(building.x, building.y) or [(building.x, building.y)]
        dropped = 0
        for i, stack in enumerate(stacks):
            tx, ty = tiles[i % len(tiles)]
            self.drops.spawn_tile(stack.item_id, stack.count, tx, ty)
            dropped += stack.count
        return dropped

    def demolish(self, building: Building,
                 ratio: float = config.DESTROY_DROP_RATIO) -> bool:
        """Убирает здание с карты: содержимое падает на землю вокруг.

        Низкий уровень без проверок и сообщений: им пользуется и демонтаж
        игроком (тогда сверху падает ещё и предмет постройки), и — на M5 —
        разрушение врагами. Снос игроком отдаёт всё (🔧
        `DESTROY_DROP_RATIO` = 1.0), врагам достанется меньшая доля.
        """
        if not self.buildings.remove(building):
            return False
        self.scatter_contents(building, ratio)
        return True

    # ---------------------------------------------------------------- крафт
    def craft(self, recipe_id: str, times: int | None = 1) -> bool:
        """Крафт по рецепту: тратит ресурсы со свойствами, даёт предмет.

        Одна и та же операция для рецептов ролей и для предметов-построек
        (plan/02): «свойство × количество» не знает, что получится на выходе.
        Руками крафт мгновенный (🔧 `HANDCRAFT_TIME`), а время рецепта
        относится к сборщику.

        `times` — сколько раз подряд (клик — 1, Shift — 5), None — сколько
        хватит материалов и места (Ctrl). Если вышло хоть раз, сообщение одно:
        сколько сделано; почему не вышло — только когда не вышло ни разу.
        """
        if not self.knowledge.is_known(recipe_id):
            self.toast("Рецепт ещё не открыт")
            return False
        if times is not None and times < 1:
            return False
        if self.time - self._last_craft_time < config.HANDCRAFT_TIME:
            return False
        recipe = self.run.recipe(recipe_id)
        # «Максимум» ограничен сверху запасом: каждый крафт тратит хотя бы
        # одну единицу, поэтому больше раз, чем единиц в хранилище, не выйдет.
        limit = times if times is not None else self.cargo.total_count() + 1
        made = 0
        reason: str | None = None
        while made < limit:
            reason = self._craft_once(recipe)
            if reason is not None:
                break
            made += 1
        if made == 0:
            self.toast(reason or "Не хватает материалов")
            return False
        self._last_craft_time = self.time
        name = self.run.items[recipe.role].name
        self.toast(f"+{recipe.outputs * made} {name}")
        return True

    def _craft_once(self, recipe) -> str | None:
        """Один крафт: проверяет материалы и место, потом меняет состояние.

        None — получилось; иначе — почему нет (текст для игрока).
        """
        plan = plan_craft(recipe, self.cargo, self.run.items)
        if plan is None:
            return "Не хватает материалов"
        # Место проверяем на копии: материалы при крафте освобождают слоты,
        # поэтому «сейчас полно» ещё не значит «после крафта не влезет».
        after = self.cargo.copy()
        for item_id, count in plan.items():
            after.remove(item_id, count)
        if not after.can_accept(recipe.role, recipe.outputs):
            return "Нет места в инвентаре"
        for item_id, count in plan.items():
            self.cargo.remove(item_id, count)
        self.cargo.add(recipe.role, recipe.outputs)
        self.crafted_total += recipe.outputs
        return None

    # ------------------------------------------------------- панель крафта
    def craft_options(self, query: str = "") -> tuple[CraftOption, ...]:
        """Что показать в панели крафта: **только известные рецепты** (plan/02).

        Неизвестного здесь нет вообще — ни строки «???», ни числа
        неоткрытых: игрок видит ровно то, что уже знает. Поиск фильтрует по
        имени без учёта регистра, доступные рецепты идут первыми.
        """
        needle = query.strip().casefold()
        known_items = KnownItems(self.run.items, self.knowledge)
        options: list[CraftOption] = []
        for recipe in self.run.recipes:
            if not self.knowledge.is_known(recipe.id):
                continue
            name = self.run.items[recipe.role].name
            if needle and needle not in name.casefold():
                continue
            options.append(self._craft_option(recipe, name, known_items))
        # Усилитель идёт сразу за базовым рецептом той же роли: имя у них одно.
        options.sort(key=lambda option: (not option.available, option.name,
                                         option.amplified))
        return tuple(options)

    def _craft_option(self, recipe, name: str, known_items: KnownItems) -> CraftOption:
        requires = tuple(
            (PROP_NAMES[prop], count, self._known_units_with(prop))
            for prop, count in recipe.requires)
        return CraftOption(
            recipe_id=recipe.id,
            name=name,
            role=recipe.role,
            outputs=recipe.outputs,
            amplified=recipe.amplified,
            requires=requires,
            # По знанию, а не по настоящим свойствам: яркий рецепт не должен
            # выдавать, что в рюкзаке есть ресурс с ещё не открытым свойством.
            available=plan_craft(recipe, self.cargo, known_items) is not None,
        )

    def _known_units_with(self, prop: Prop) -> int:
        """Сколько единиц ресурсов со свойством у дрона **по знанию игрока**.

        Считаются только открытые свойства (`knowledge.visible`): иначе
        подсказка рассказала бы, какие из своих ресурсов подойдут, — а это и
        есть то, что игрок покупает сканом и лабораторией.
        """
        total = 0
        for item_id, count in self.cargo.items():
            if prop in self.knowledge.visible(item_id):
                total += count
        return total

    # ------------------------------------------------------------- постройки
    def can_place(self, kind: str, x: int, y: int) -> bool:
        """Можно ли поставить это здание на тайл. Ничего не меняет.

        **Единственная** проверка места в игре: её зовут и постановка, и
        призрак постройки, поэтому «зелёный призрак» и «здание встало»
        разойтись не могут. Материалы сюда не входят: вопрос призрака — «куда»,
        а не «есть ли чем» (предмет уже в руке, иначе призрака нет).
        """
        return self._tile_ok_for(kind, x, y) and self.in_build_range(x, y)

    def _tile_ok_for(self, kind: str, x: int, y: int) -> bool:
        """Годится ли тайл под это здание: земля, не занято, залежь — по данным.

        `placeable_on` в данных здания (plan/03) решает, что под ним допустимо:
        бур ставится **на** залежь, всё остальное — на свободную землю. Гора и
        вода отсекаются проходимостью тайла, реликвию и постройку с лампой
        занимать нельзя. Предметы на земле не мешают: они остаются лежать под
        зданием и подбираются, когда дрон проходит рядом.
        """
        if not self.world.is_walkable_tile(x, y):
            return False
        if self.light.lamp_at(x, y) is not None:
            return False
        if self.buildings.at(x, y) is not None:
            return False
        if self.run.relic_at(x, y) is not None:
            return False
        deposit = self.world.deposit_at(x, y)
        if building_def(kind).placeable_on == PLACE_DEPOSIT:
            # Бур ставится **на** залежь: он добывает из неё сам (plan/03).
            return deposit is not None and not deposit.depleted
        return deposit is None

    def in_build_range(self, x: int, y: int) -> bool:
        """Достаёт ли дрон до тайла (🔧 `BUILD_RANGE` — общий для всех зданий)."""
        return self.drone.distance_to_tile(x, y) <= config.BUILD_RANGE

    def build_direction(self, x: int, y: int) -> tuple[int, int]:
        """Куда конвейер повезёт, если его поставить: от дрона к тайлу, по оси.

        Игрок ставит линию, стоя у бура, — «от себя» и есть верное направление
        почти всегда. Промах исправляет поворот (R) ещё до постановки.
        """
        dx = (x + 0.5) - self.drone.x
        dy = (y + 0.5) - self.drone.y
        if abs(dx) >= abs(dy):
            return (1 if dx >= 0 else -1, 0)
        return (0, 1 if dy >= 0 else -1)

    def rotation_index(self, x: int, y: int) -> int:
        """Индекс направления для постановки: накрученный R или «от дрона»."""
        if self.build_rotation is not None:
            return self.build_rotation % len(DIRECTIONS)
        return direction_index(self.build_direction(x, y)) or 0

    def ghost_direction(self, x: int, y: int) -> tuple[int, int] | None:
        """Направление призрака (для шевронов). None — у здания его нет."""
        kind = self.selected_building()
        if kind is None or building_def(kind).work != WORK_MOVE:
            return None
        return DIRECTIONS[self.rotation_index(x, y)]

    def rotate_preview(self, x: int, y: int) -> bool:
        """R: повернуть призрак постройки или конвейер под курсором (plan/10).

        Призрак важнее: если в слоте здание с направлением, крутится оно —
        иначе игрок не смог бы поставить линию «поперёк». Стоящий конвейер
        крутится, только когда призрака нет вовсе (слот пуст или в нём не
        постройка): с лампой в руке R ничего не делает — так записано в
        plan/10, и у R не бывает двух смыслов сразу.
        """
        kind = self.selected_building()
        if kind is not None:
            if building_def(kind).work != WORK_MOVE:
                return False
            self.build_rotation = (self.rotation_index(x, y) + 1) % len(DIRECTIONS)
            return True
        return self.rotate_at(x, y)

    def place_building(self, slot: int, tile: tuple[int, int],
                       rotation: int | None = None) -> Building | Lamp | None:
        """Команда постановки: предмет из слота хотбара становится зданием.

        Тратится ровно одна единица из выбранного слота. Если в слоте не
        постройка (пусто или ресурс), не происходит **ничего**: ПКМ по земле с
        ресурсом в руке — не команда (plan/10).
        """
        x, y = int(tile[0]), int(tile[1])
        stack = self.hotbar.slot(slot)
        item = self.run.items.get(stack.item_id) if stack is not None else None
        kind = item.building if item is not None else None
        if kind is None:
            return None
        if not self.can_place(kind, x, y):
            self.toast("Здесь не построить")
            return None
        if rotation is None:
            rotation = self.rotation_index(x, y)
        placed = self._spawn(kind, x, y, DIRECTIONS[rotation % len(DIRECTIONS)])
        if placed is None:
            return None
        self.hotbar.take_slot(slot, 1)
        self.toast(f"Построено: {building_def(kind).name}")
        return placed

    def _spawn(self, kind: str, x: int, y: int,
               direction: tuple[int, int]) -> Building | Lamp | None:
        """Ставит само здание: лампа живёт в `sim/light.py`, остальные — в `sim/buildings.py`."""
        if kind == BUILD_LAMP:
            # Лампа ставится **выключенной**: с M4 она горит, только пока её
            # питает сеть, и решение принимает `_update_power` на ближайшем шаге.
            return self.light.add_lamp(x, y, on=False)
        return self.buildings.add(kind, x, y, direction)

    def build(self, kind: str, x: int, y: int,
              direction: tuple[int, int] | None = None) -> Building | Lamp | None:
        """Поставить здание такого вида, взяв предмет-постройку из хранилища.

        Вспомогательный вход для тестов, скриптов и инструментов съёмки: игрок
        ставит из слота, а здесь предмет сначала перекладывается в хотбар —
        путь постановки всё равно остаётся один (`place_building`).
        """
        if kind not in BUILDING_TYPES:
            return None
        slot = self._hotbar_slot_with(kind)
        if slot is None:
            slot = self._move_to_hotbar(kind)
        if slot is None:
            self.toast("Нет предмета постройки")
            return None
        if direction is None:
            direction = self.build_direction(x, y)
        return self.place_building(slot, (x, y), direction_index(direction))

    def _hotbar_slot_with(self, item_id: str) -> int | None:
        """Слот хотбара, в котором лежит этот предмет."""
        for i in range(self.hotbar.size):
            stack = self.hotbar.slot(i)
            if stack is not None and stack.item_id == item_id:
                return i
        return None

    def _move_to_hotbar(self, item_id: str) -> int | None:
        """Переносит одну единицу из инвентаря в свободный слот хотбара."""
        source = next((i for i in range(self.inventory.size)
                       if self.inventory.slot(i) is not None
                       and self.inventory.slot(i).item_id == item_id), None)
        free = next((i for i in range(self.hotbar.size)
                     if self.hotbar.slot(i) is None), None)
        if source is None or free is None:
            return None
        return free if transfer(self.inventory, self.hotbar, source, 1) > 0 else None

    def rotate_at(self, x: int, y: int) -> bool:
        """Поворот конвейера на тайле. False — если там не конвейер."""
        building = self.buildings.at(x, y)
        if building is None or building_def(building.kind).work != WORK_MOVE:
            return False
        building.rotate()
        return True

    def cycle_recipe(self, building: Building) -> str | None:
        """Перебирает открытые рецепты в сборщике — кнопка «Рецепт» в его окне.

        Только предметы ролей: постройка — не то, что сборщик умеет делать
        (он собирает предметы для конвейера), да и роль у неё своя собственная.
        """
        if building_def(building.kind).work != WORK_CRAFT:
            return None
        # Фильтр — по роли рецепта, а не по id: у усилителя id «energy+», но
        # роль та же, и сборщик обязан уметь собирать по нему.
        known = sorted(r for r in self.knowledge.known_recipes
                       if self.run.recipe(r).role in ROLE_IDS)
        if not known:
            self.toast("Ни один рецепт ещё не открыт")
            return None
        index = known.index(building.recipe) + 1 if building.recipe in known else 0
        building.recipe = known[index % len(known)]
        building.work_progress = 0.0
        self.toast(f"Сборщик: {role_def(self.run.recipe(building.recipe).role).short}")
        return building.recipe

    # --------------------------------------------------------------- события
    def toast(self, text: str) -> None:
        self.toasts.append(Toast(text, config.TOAST_TTL, config.TOAST_TTL))

    def _update_toasts(self, dt: float) -> None:
        # Сообщения добавляются по порядку и убывают с одной скоростью,
        # поэтому истекают тоже по порядку — снимаем с начала очереди.
        for toast in self.toasts:
            toast.ttl -= dt
        while self.toasts and self.toasts[0].ttl <= 0:
            self.toasts.popleft()

    # ------------------------------------------------------------ для тестов
    def state_signature(self) -> tuple:
        """Слепок состояния: два одинаковых слепка = состояние не менялось.

        Включает карту залежей, знание игрока и постройки, чтобы разные сиды
        и разные действия давали разные слепки.
        """
        return (
            round(self.drone.x, 6),
            round(self.drone.y, 6),
            self.drone.hp,
            tuple(sorted(self.inventory.items())),
            tuple(sorted(self.hotbar.items())),
            self.selected_slot,
            tuple((d.resource_id, d.x, d.y, d.amount) for d in self.world.deposits),
            round(self.time, 6),
            self.mined_total,
            self.time_cycle.state(),
            tuple((lamp.x, lamp.y, lamp.on) for lamp in self.light.lamps),
            self.knowledge.state(),
            self.buildings.state(),
            self.power.state(),
            self.drops.state(),
            tuple((r.id, r.x, r.y, r.studied) for r in self.run.relics),
            self.scans_done,
            self.crafted_total,
        )

    @property
    def tiles_lit_ratio(self) -> float:
        """Доля освещённых доступных тайлов, 0..1 (plan/01)."""
        return self.light.lit_ratio()

    @property
    def darkness(self) -> float:
        """0 — светло, 1 — полная тьма. Рендер читает это для затемнения."""
        return self.time_cycle.darkness

    @property
    def relics(self) -> tuple[Relic, ...]:
        return tuple(r for r in self.run.relics if r.placed)

    @property
    def props_known(self) -> tuple[int, int]:
        """Сколько свойств открыто игроку из всех, что есть у ресурсов забега."""
        known = total = 0
        for item in self.run.items.natural:
            total += len(item.props)
            known += len(self.knowledge.visible(item.id))
        return known, total

    def prop_slots(self, item_id: str) -> tuple[Prop | None, ...]:
        """Слоты свойств ресурса для интерфейса: известное свойство или `?`.

        Длина — сколько свойств у ресурса (игрок видит и число слотов, иначе
        он не знал бы, что под `?` что-то есть), содержимое — что открыто.
        """
        item = self.run.items.get(item_id)
        if item is None:
            return ()
        known = tuple(sorted(self.knowledge.visible(item_id)))
        slots: list[Prop | None] = list(known)
        slots.extend([None] * (len(item.props) - len(known)))
        return tuple(slots)
