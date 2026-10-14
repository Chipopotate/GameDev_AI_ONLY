"""Скриншоты сцен без окна (plan/09 §12).

Агент обязан **смотреть** результат, а не верить коду: инструмент рендерит
сцены в `screens/`, и по ним проверяется стиль (свой/чужой, силуэты, неон в
темноте, читаемость состояний, отсутствие визуального мусора).

Запуск:
    py tools/screenshot.py                 # все сцены, сид по умолчанию
    py tools/screenshot.py --scene night   # только одна (можно несколько --scene)
    py tools/screenshot.py --seed 42 --out screens

Сцена может отдать несколько кадров (кадры подряд): тогда файлов несколько,
у каждого своё имя (`laser_mine_f1.png` …).

Сцены:
    gallery — вся сетка спрайтов в 3× с подписями (plan/09 §12.1);
    terrain — земля, горы, вода и залежи днём, без отладочных слоёв (§12.2);
    layers  — тот же кадр с отладочным слоем света (F2): какие тайлы освещены;
    base    — база днём крупно: провода, конвейер, запитанная и обесточенная сеть;
    night   — ночь: лампы, свет, неон построек (§12.3);
    foes    — силуэты врагов и частицы (§12.4, заготовка: врагов в симуляции
              ещё нет — M5, поэтому на кадр кладутся сами спрайты из
              `render/sprites/organic.py`).

Призрак постройки и лазеры дрона (plan/10, plan/09 §10). Ввод — настоящий:
игра сама читает мышь (`Game.poll_input`, `Game.ghost_tile`), а на время
съёмки подменяются только `pygame.mouse.get_pos/get_pressed` (см. `Pointer`):
курсор «держится» за тайл, кнопки зажаты по сценарию. Клавиши (1…9, R) идут
событиями через `Game.handle_event`.

    ghost_ok        — зелёный призрак склада у дрона, днём;
    ghost_bad       — красный призрак лампы на горе (в радиусе), днём;
    ghost_far       — красный призрак вне 🔧 `BUILD_RANGE`: видно кольцо дальности;
    ghost_night     — зелёный призрак лампы ночью: читается ли он в темноте;
    ghost_conveyor  — призрак конвейера с шевронами, повёрнутый R, рядом лента;
    laser_mine      — ночь, ЛКМ по залежи: красный луч;
    laser_mine_day  — то же днём;
    laser_mine_f    — ночь, три кадра подряд (`laser_mine_f1…f3`): переливание;
    laser_dismantle — ночь, ЛКМ по своему складу вдали от дрона: луч и кольцо
                      прогресса; второй кадр `dismantle_drop` — что осталось;
    laser_study     — ночь, ЛКМ по реликвии: синий луч;
    laser_scan      — ночь, ПКМ по залежи с пустым слотом: синий скан;
    laser_scan_day  — то же днём;
    laser_low       — ночь, добыча на `low`: простая линия без ореола.

Файлы пишутся без окна: SDL переводится в dummy-режим до импорта pygame.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:            # запуск как `py tools/screenshot.py`
    sys.path.insert(0, str(ROOT))

import argparse  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402
from contextlib import contextmanager  # noqa: E402

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from src.game import config  # noqa: E402
from src.game.data.buildings import (ASSEMBLER, BUILD_ORDER, CONVEYOR,  # noqa: E402
                                     DRILL, GENERATOR, LAB, LAMP, STORAGE,
                                     WIRE, building as building_def)
from src.game.data.recipes import ROLES, role as role_def  # noqa: E402
from src.game.data.resources import Prop  # noqa: E402
from src.game.data.terrain import Terrain  # noqa: E402
from src.game.render import quality, theme  # noqa: E402
from src.game.render.sprites import organic  # noqa: E402
from src.game.render.effects import RING, SMOKE, SPARK  # noqa: E402
from src.game.ui import widgets  # noqa: E402

#: Сид по умолчанию: карта с водой и горами рядом — на ней видно все три типа.
DEFAULT_SEED = 20251013
DEFAULT_OUT = "screens"
#: Холст галереи крупнее окна: только в нём сетка влезает в увеличение 🔧 3×.
GALLERY_CANVAS = (1440, 1280)
#: Сколько кадров прогреть перед снимком (кэши чанков, спрайтов, значки). Больше
#: секунды: кольца и дым только что поставленных построек успевают погаснуть,
#: иначе ночной кадр показывал бы эффект стройки, а не базу.
WARMUP = 70
#: Сколько топлива положить в генератор: ночь длинная, лампы должны гореть.
FUEL = 60
#: Полоса базы в ночной сцене: постройки в ряд вплотную друг к другу, поэтому
#: одной полосы и есть одна сеть (в M4 сеть — соседние тайлы). Генераторы стоят
#: по краям полосы: 🔧 6 энергии на каждый, а полоса с лампами просит 11.
BASE_ROW = (GENERATOR, LAB, CONVEYOR, DRILL, STORAGE, GENERATOR)
#: На сколько тайлов выше дрона стоит полоса базы (по ней же — ряд ламп).
BASE_DY = 4
#: Сцена «base»: (что, dx, dy от дрона, направление ленты). Верхняя сеть с
#: генераторами запитана, нижняя — та же, но без генератора.
EAST = (1, 0)
BASE_LAYOUT = (
    (GENERATOR, -5, -3, None), (GENERATOR, -5, -2, None),
    (WIRE, -4, -3, None), (WIRE, -3, -3, None), (LAB, -2, -3, None),
    (WIRE, -1, -3, None), (WIRE, 0, -3, None), (LAMP, 1, -3, None),
    (WIRE, 0, -4, None), (WIRE, 0, -2, None),
    (CONVEYOR, 0, -1, EAST), (CONVEYOR, 1, -1, EAST), (CONVEYOR, 2, -1, EAST),
    (STORAGE, 3, -1, None),
    (WIRE, -4, 3, None), (WIRE, -3, 3, None), (LAB, -2, 3, None),
    (ASSEMBLER, -1, 3, None), (WIRE, 0, 3, None), (WIRE, 0, 2, None),
    (LAMP, 1, 3, None), (CONVEYOR, 0, 4, EAST), (CONVEYOR, 1, 4, EAST),
)
#: Зум сцены «base»: детали построек читаются только крупно.
BASE_ZOOM = 1.8
#: Враги для сцены «foes»: по одному каждого силуэта (plan/09 §8).
FOE_ROW = (organic.CRAWLER, organic.SHADOW, organic.MOSQUITO, organic.ARTILLERY,
           organic.SPROUT, organic.INCUBATOR, organic.SPIKE)
#: Раскладка врагов: первый ряд выше дрона, второй — ниже, чтобы силуэты не
#: налезали друг на друга и подписи читались. Шаг в тайлах.
FOE_ROWS = ((-2.6, 4), (2.6, 3))
FOE_SPACING = 3.0
FOE_NAMES = {
    organic.CRAWLER: "ходок", organic.SHADOW: "тень", organic.MOSQUITO: "москит",
    organic.ARTILLERY: "артиллерия", organic.SPROUT: "росток",
    organic.INCUBATOR: "инкубатор", organic.SPIKE: "нарост",
}
#: Пустой слот хотбара для старых сцен: запас построек (`_stock`) занимает
#: первые восемь слотов, а с постройкой в руке под курсором висел бы призрак.
EMPTY_SLOT_KEY = pygame.K_1 + len(BUILD_ORDER)
#: Где встать дрону у цели, тайлы от её центра: чуть ближе каждого радиуса
#: действия (🔧 `MINE_RANGE`, `RELIC_STUDY_RANGE`, `SCAN_RANGE`).
MINE_STAND = 1.3
STUDY_STAND = 1.4
SCAN_STAND = 2.0
#: Склад под разбор — дальше света дрона (🔧 `DRONE_LIGHT_RADIUS` = 4), но в
#: `BUILD_RANGE`: луч и кольцо проверяются в настоящей темноте.
DISMANTLE_OFFSET = (5, -1)
#: Призраки: смещение тайла от дрона. «Далеко» — за кольцом `BUILD_RANGE`.
GHOST_NEAR = (2, -1)
GHOST_FAR = (8, 1)
#: Лента рядом с призраком конвейера: (dx, dy) от дрона, везёт на восток. Её
#: шевроны — эталон, с которым сравнивается повёрнутый призрак.
BELT_ROW = ((-2, 2), (-1, 2), (0, 2), (1, 2), (2, 2))
#: Сколько удерживать кнопку до снимка — доля времени действия: середина,
#: когда видно и луч, и прогресс, а действие ещё не закончилось.
HOLD_SHARE = 0.55
#: Кадров подряд для проверки переливания красного луча.
FLICKER_FRAMES = 3
#: Энергия для скана: 🔧 `SCAN_ENERGY_COST` за скан, с запасом на всю сцену.
SCAN_ENERGY = 40


# ------------------------------------------------------------------- сцены
def shot_gallery(game, seed: int) -> pygame.Surface:
    """1. Галерея: все здания, дрон, враги, ресурсы и значки в сетке."""
    return game.gallery.surface(GALLERY_CANVAS,
                                hint="tools/screenshot.py — gallery")


def shot_terrain(game, seed: int) -> pygame.Surface:
    """2. Местность: берег воды у гор — так, как её видит игрок, без отладки."""
    return _frame(game, snap=_shore_tile(game.sim.world))


def shot_layers(game, seed: int) -> pygame.Surface:
    """2б. Отладочный слой света (F2) на том же месте: какие тайлы освещены.

    Отдельная сцена, а не тот же кадр: слой заливает всю неосвещённую карту
    синим, и по нему не видно, хорошо ли нарисованы земля, вода и горы.
    """
    game.debug.show_layers = True
    return _frame(game, snap=_shore_tile(game.sim.world))


def shot_base(game, seed: int) -> pygame.Surface:
    """2в. База днём крупно: провода, линия конвейера, запитанное и обесточенное.

    Проверка §12.4 «читается ли направление конвейера и состояние зданий»:
    сверху сеть с генераторами (всё работает, по проводам бегут точки), снизу
    такая же сеть без генератора — у каждой постройки значок «нет питания».
    """
    sim = game.sim
    _stock(sim)
    cx, cy = int(sim.drone.x), int(sim.drone.y)
    for kind, dx, dy, direction in BASE_LAYOUT:
        x, y = cx + dx, cy + dy
        building = sim.build(kind, x, y, direction=direction)
        if building is not None and kind == GENERATOR:
            _fuel(sim, building)
    for _ in range(3):
        sim.update(config.TICK)
    sim.toasts.clear()
    _press(game, EMPTY_SLOT_KEY)
    game.camera.set_zoom(BASE_ZOOM)
    return _frame(game, snap=(sim.drone.x, sim.drone.y))


def shot_night(game, seed: int) -> pygame.Surface:
    """3. Ночь: полоса базы с лампами, тёплый свет и неон построек."""
    sim = game.sim
    game.skip_to_night()
    _place_base(sim)
    for _ in range(3):                    # сеть пересчитывается на шаге симуляции
        sim.update(config.TICK)
    sim.toasts.clear()                    # сообщения о стройке закрыли бы кадр
    _press(game, EMPTY_SLOT_KEY)
    return _frame(game, snap=(sim.drone.x, sim.drone.y))


def shot_foes(game, seed: int) -> pygame.Surface:
    """4. Враги: силуэты и частицы (M5 — заготовка, спрайты без симуляции)."""
    screen = _frame(game)                 # фон — настоящий кадр дня
    sim, camera = game.sim, game.camera
    game.debug.show_layers = False
    size = int(config.TILE * camera.zoom * 2.2)
    index = 0
    for row, (dy, count) in enumerate(FOE_ROWS):
        wy = sim.drone.y + dy
        for i in range(count):
            offset = (i - (count - 1) / 2.0) * FOE_SPACING
            wx = sim.drone.x + offset
            sx, sy = camera.world_to_screen(wx, wy)
            kind = FOE_ROW[index % len(FOE_ROW)]
            art = game.renderer.sprites.creature(kind, size, phase=0.4 + index * 0.3)
            rect = art.get_rect(center=(int(sx), int(sy)))
            screen.blit(art, rect)
            _caption(screen, rect, FOE_NAMES[kind])
            # Частицы: попадание (искры) и смерть (дым) — plan/09 §10.
            game.renderer.particles.burst(SPARK, wx - 0.6, wy, 8, 2.0, 0.5, 0.9,
                                          theme.FOE_TOXIC, _rng(index), 1.4)
            game.renderer.particles.burst(SMOKE, wx + 0.7, wy + 0.4, 4, 1.0, 0.6, 1.4,
                                          (0, 0, 0), _rng(index + 100), 2.0)
            index += 1
    game.renderer.particles.spawn(RING, sim.drone.x, sim.drone.y, 0.0, 0.0, 0.35,
                                  1.0, theme.NEON_CYAN)
    game.renderer.particles.update(0.12)
    game.renderer.particles.draw(screen, camera)
    _hint(screen, "M5 — заготовка: врагов и турелей в симуляции пока нет, "
                  "на кадр положены спрайты render/sprites/organic.py")
    return screen


# ------------------------------------------------- призрак постройки
def shot_ghost_ok(game, seed: int) -> pygame.Surface:
    """5а. Зелёный призрак: склад на свободной земле рядом с дроном, днём."""
    sim = game.sim
    tile = _offset_tile(sim, GHOST_NEAR)
    return _ghost(game, STORAGE, tile, expect_ok=True)


def shot_ghost_bad(game, seed: int) -> pygame.Surface:
    """5б. Красный призрак: лампа на горе у края стартовой зоны (в радиусе).

    Дрон встаёт у кромки гор: только так «нельзя» вызвано тайлом, а не
    дальностью, — это проверка цвета призрака, а не кольца.
    """
    sim = game.sim
    stand, tile = _mountain_edge(sim)
    _teleport(game, *stand, face=tile)
    return _ghost(game, LAMP, tile, expect_ok=False)


def shot_ghost_far(game, seed: int) -> pygame.Surface:
    """5в. Красный призрак за кольцом дальности: земля свободна, но далеко."""
    sim = game.sim
    tile = _offset_tile(sim, GHOST_FAR)
    _check(sim._tile_ok_for(STORAGE, *tile) and not sim.in_build_range(*tile),
           f"тайл {tile} должен быть свободной землёй вне BUILD_RANGE")
    return _ghost(game, STORAGE, tile, expect_ok=False)


def shot_ghost_night(game, seed: int) -> pygame.Surface:
    """5г. Призрак ночью: подсказка рисуется поверх тьмы и обязана читаться."""
    game.skip_to_night()
    tile = _offset_tile(game.sim, GHOST_NEAR)
    return _ghost(game, LAMP, tile, expect_ok=True)


def shot_ghost_conveyor(game, seed: int) -> pygame.Surface:
    """5д. Призрак конвейера: по умолчанию «от дрона» (восток), R — на юг.

    Рядом стоит настоящая лента на восток: по ней видно, что шевроны
    призрака — та же подсказка направления, но повёрнутая клавишей R.
    """
    sim = game.sim
    cx, cy = int(sim.drone.x), int(sim.drone.y)
    for dx, dy in BELT_ROW:
        sim.cargo.add(CONVEYOR, 1)
        _check(sim.build(CONVEYOR, cx + dx, cy + dy, direction=EAST) is not None,
               "лента рядом с призраком не встала")
    sim.toasts.clear()
    tile = _offset_tile(sim, GHOST_NEAR)
    with _pointer(game) as mouse:
        mouse.tile = tile
        sim.cargo.add(CONVEYOR, 1)
        _press(game, pygame.K_1 + _slot_of(sim, CONVEYOR))
        before = sim.ghost_direction(*tile)
        _press(game, pygame.K_r)               # настоящая клавиша, через Game
        after = sim.ghost_direction(*tile)
        _check(before == EAST and after == (0, 1),
               f"R должен повернуть призрак с востока на юг: {before} → {after}")
        _play(game, WARMUP)
        _check(sim.can_place(CONVEYOR, *tile), "призрак конвейера должен быть зелёным")
        return game.screen.copy()


# ------------------------------------------------------------- лазеры
def shot_laser_mine(game, seed: int) -> pygame.Surface:
    """6а. Ночь, ЛКМ по залежи: красный «жгущий» луч и искры."""
    game.skip_to_night()
    return _mine(game)


def shot_laser_mine_day(game, seed: int) -> pygame.Surface:
    """6б. Добыча днём: на светлой земле луч не должен теряться."""
    return _mine(game)


def shot_laser_mine_frames(game, seed: int) -> list[tuple[str, pygame.Surface]]:
    """6в. Три кадра подряд (1/60 с): бегущие по лучу волны яркости."""
    game.skip_to_night()
    return _mine(game, frames=FLICKER_FRAMES)


def shot_laser_low(game, seed: int) -> pygame.Surface:
    """6г. `low`: луч — простая линия без ореола (plan/09 §3.7)."""
    game.skip_to_night()
    with quality.override(quality.LOW):
        return _mine(game)


def shot_laser_dismantle(game, seed: int) -> list[tuple[str, pygame.Surface]]:
    """6д. Ночь, ЛКМ по своему складу: красный луч и кольцо прогресса.

    Склад стоит дальше света дрона: луч идёт через настоящую темноту. Второй
    кадр — после конца разбора: здание стало предметом на своём тайле.
    """
    sim = game.sim
    game.skip_to_night()
    tile = _offset_tile(sim, DISMANTLE_OFFSET)
    sim.cargo.add(STORAGE, 1)
    building = sim.build(STORAGE, *tile)
    _check(building is not None, f"склад на {tile} не встал")
    sim.toasts.clear()
    hold = _hold_frames(config.DISMANTLE_TIME)
    with _pointer(game) as mouse:
        mouse.tile = tile
        _check(sim.selected_building() is None, "рука должна быть пустой")
        _play(game, WARMUP)
        mouse.left = True
        _play(game, hold)
        _check(sim.drone.action.kind == "dismantle" and sim.buildings.at(*tile),
               "дрон должен разбирать склад")
        shot = game.screen.copy()
        _play(game, _hold_frames(config.DISMANTLE_TIME, 1.0) - hold + 2)
        mouse.left = False
        _play(game, 12)                        # луч угас, дрон отпустил кнопку
        _check(sim.buildings.at(*tile) is None, "склад должен был разобраться")
        return [("laser_dismantle", shot), ("dismantle_drop", game.screen.copy())]


def shot_laser_study(game, seed: int) -> pygame.Surface:
    """6е. Ночь, ЛКМ по реликвии: синий «читающий» луч и кольцо-сканер."""
    sim = game.sim
    game.skip_to_night()
    relic = min((r for r in sim.relics if not r.studied and not r.amplified),
                key=lambda r: sim.drone.distance_to_tile(r.x, r.y))
    tile = (relic.x, relic.y)
    _teleport(game, *_stand_by(sim, tile, STUDY_STAND), face=tile)
    return _hold(game, tile, config.RELIC_STUDY_TIME, "study")


def shot_laser_scan(game, seed: int) -> pygame.Surface:
    """6ж. Ночь, ПКМ по залежи с пустым слотом: синий скан (тратит энергию)."""
    game.skip_to_night()
    return _scan(game)


def shot_laser_scan_day(game, seed: int) -> pygame.Surface:
    """6з. Скан днём: синий луч на светлой земле — самый трудный случай."""
    return _scan(game)


SCENES = {
    "gallery": shot_gallery,
    "terrain": shot_terrain,
    "layers": shot_layers,
    "base": shot_base,
    "night": shot_night,
    "foes": shot_foes,
    "ghost_ok": shot_ghost_ok,
    "ghost_bad": shot_ghost_bad,
    "ghost_far": shot_ghost_far,
    "ghost_night": shot_ghost_night,
    "ghost_conveyor": shot_ghost_conveyor,
    "laser_mine": shot_laser_mine,
    "laser_mine_day": shot_laser_mine_day,
    "laser_mine_f": shot_laser_mine_frames,
    "laser_dismantle": shot_laser_dismantle,
    "laser_study": shot_laser_study,
    "laser_scan": shot_laser_scan,
    "laser_scan_day": shot_laser_scan_day,
    "laser_low": shot_laser_low,
}


# ----------------------------------------------------------- ввод для съёмки
class Pointer:
    """Мышь на время съёмки: курсор над тайлом, кнопки зажаты по сценарию.

    Кадр обязан быть честным — игра сама читает мышь (`Game.poll_input`,
    `Game.ghost_tile`, подсказки HUD), поэтому подменяется только то, что
    отдаёт pygame. Курсор держится за **мировой** тайл: камера каждый кадр
    подтягивается за дроном, и экранная точка пересчитывается от неё — как
    если бы игрок вёл мышью за целью.
    """

    def __init__(self, game) -> None:
        self.game = game
        self.tile: tuple[int, int] | None = None
        self.left = False
        self.right = False
        self._real_pos = pygame.mouse.get_pos

    def get_pos(self) -> tuple[int, int]:
        if self.tile is None:
            return self._real_pos()
        sx, sy = self.game.camera.world_to_screen(self.tile[0] + 0.5, self.tile[1] + 0.5)
        return int(sx), int(sy)

    def get_pressed(self, num_buttons: int = 3, desktop: bool = False):
        buttons = (self.left, False, self.right)
        return buttons if num_buttons == 3 else buttons + (False, False)


@contextmanager
def _pointer(game):
    """Подменяет `pygame.mouse.get_pos/get_pressed` на время сцены."""
    mouse = pygame.mouse
    saved = mouse.get_pos, mouse.get_pressed
    pointer = Pointer(game)
    mouse.get_pos, mouse.get_pressed = pointer.get_pos, pointer.get_pressed
    try:
        yield pointer
    finally:
        mouse.get_pos, mouse.get_pressed = saved


def _press(game, key: int) -> None:
    """Нажатие клавиши — событием, через тот же обработчик, что в игре."""
    game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0,
                                         unicode="", scancode=0))


def _play(game, frames: int) -> None:
    """Кадры игры как в `Game.run`: опрос ввода, шаг, отрисовка (без flip)."""
    for _ in range(max(0, frames)):
        game.poll_input()
        game.draw_frame(config.TICK)


def _hold_frames(seconds: float, share: float = HOLD_SHARE) -> int:
    """Сколько кадров держать кнопку, чтобы действие дошло до доли `share`."""
    return max(1, int(round(seconds * share / config.TICK)))


# ------------------------------------------------------- сценарии сцен
def _ghost(game, kind: str, tile: tuple[int, int], expect_ok: bool) -> pygame.Surface:
    """Постройка в руке, курсор над тайлом: призрак рисует сама игра."""
    sim = game.sim
    sim.cargo.add(kind, 3)
    with _pointer(game) as mouse:
        mouse.tile = tile
        _press(game, pygame.K_1 + _slot_of(sim, kind))
        _check(sim.selected_building() == kind, f"в руке должен быть {kind}")
        _play(game, WARMUP)
        _check(game.ghost_tile() == tuple(tile), "призрак должен стоять под курсором")
        _check(sim.can_place(kind, *tile) == expect_ok,
               f"призрак {kind} на {tile}: ожидалось can_place={expect_ok}")
        return game.screen.copy()


def _mine(game, frames: int = 1):
    """ЛКМ по ближайшей залежи: луч добычи. `frames` > 1 — кадры подряд."""
    sim = game.sim
    deposit = _nearest_deposit(sim)
    tile = (deposit.x, deposit.y)
    _teleport(game, *_stand_by(sim, tile, MINE_STAND), face=tile)
    return _hold(game, tile, config.MINE_TIME_PER_UNIT, "mine", frames=frames)


def _scan(game) -> pygame.Surface:
    """ПКМ по залежи: рука пуста, энергия в инвентаре — скан идёт."""
    sim = game.sim
    sim.cargo.add(role_def("energy").id, SCAN_ENERGY)
    deposit = _nearest_deposit(sim)
    tile = (deposit.x, deposit.y)
    _teleport(game, *_stand_by(sim, tile, SCAN_STAND), face=tile)
    return _hold(game, tile, config.SCAN_TIME, "scan", button="right")


def _hold(game, tile: tuple[int, int], seconds: float, kind: str,
          button: str = "left", frames: int = 1):
    """Прогрев без действия, потом кнопка зажата до середины действия.

    Прогрев отдельно: кольца и дым от телепорта камеры и постановки гаснут, а
    сама работа (разбор, изучение) за 70 кадров уже закончилась бы.
    """
    sim = game.sim
    sim.toasts.clear()
    with _pointer(game) as mouse:
        mouse.tile = tile
        _check(sim.selected_building() is None, "рука должна быть пустой")
        _play(game, WARMUP)
        setattr(mouse, button, True)
        _play(game, _hold_frames(seconds))
        _check(sim.drone.action.kind == kind,
               f"действие дрона должно быть {kind}, а не {sim.drone.action.kind}")
        if frames <= 1:
            return game.screen.copy()
        shots = [("laser_mine_f1", game.screen.copy())]
        for i in range(2, frames + 1):
            _play(game, 1)
            shots.append((f"laser_mine_f{i}", game.screen.copy()))
        return shots


# ---------------------------------------------------------- места на карте
def _check(ok: bool, text: str) -> None:
    """Сцена обязана быть той, за которую себя выдаёт: иначе снимок врёт."""
    if not ok:
        raise RuntimeError(f"сцена не та: {text}")


def _offset_tile(sim, offset: tuple[int, int]) -> tuple[int, int]:
    return int(sim.drone.x) + offset[0], int(sim.drone.y) + offset[1]


def _slot_of(sim, item_id: str) -> int:
    """Слот хотбара с предметом: выбирается клавишей 1…9, как у игрока."""
    for i in range(sim.hotbar.size):
        stack = sim.hotbar.slot(i)
        if stack is not None and stack.item_id == item_id:
            return i
    raise RuntimeError(f"сцена не та: {item_id} не лёг в хотбар")


def _nearest_deposit(sim):
    drone = sim.drone
    return min((d for d in sim.world.deposits if not d.depleted),
               key=lambda d: drone.distance_to_tile(d.x, d.y))


def _stand_by(sim, tile: tuple[int, int], dist: float) -> tuple[float, float]:
    """Где встать дрону в `dist` тайлах от цели — со стороны, откуда он пришёл.

    Перебираются направления, начиная с «к дрону»: место должно быть
    проходимым для корпуса и не лежать на залежи или реликвии.
    """
    tx, ty = tile[0] + 0.5, tile[1] + 0.5
    base = math.atan2(sim.drone.y - ty, sim.drone.x - tx)
    world = sim.world
    for i in range(16):
        a = base + (i + 1) // 2 * (math.pi / 8) * (1 if i % 2 else -1)
        x, y = tx + math.cos(a) * dist, ty + math.sin(a) * dist
        if world.circle_hits_blocked(x, y, sim.drone.radius):
            continue
        cx, cy = int(math.floor(x)), int(math.floor(y))
        if world.deposit_at(cx, cy) is None and sim.run.relic_at(cx, cy) is None:
            return x, y
    raise RuntimeError(f"сцена не та: рядом с {tile} негде встать")


def _mountain_edge(sim) -> tuple[tuple[float, float], tuple[int, int]]:
    """Кромка гор на ряду дрона: (где встать, тайл горы в 3 тайлах от дрона)."""
    world = sim.world
    x, y = int(sim.drone.x), int(sim.drone.y)
    while world.is_walkable_tile(x - 1, y):
        x -= 1
    _check(world.in_bounds(x - 1, y), "на ряду дрона нет гор")
    return (x + 2.5, y + 0.5), (x - 1, y)


def _teleport(game, x: float, y: float, face: tuple[int, int] | None = None) -> None:
    """Дрон на место сцены, лицом к цели: будто игрок подлетел к ней сам."""
    drone = game.sim.drone
    drone.x, drone.y = x, y
    if face is not None:
        drone.facing = math.atan2(face[1] + 0.5 - y, face[0] + 0.5 - x)
    game.camera.snap_to(x, y)
    game.camera.clamp_to(game.sim.world.width, game.sim.world.height)


# ---------------------------------------------------------------- подготовка
def _frame(game, snap: tuple[float, float] | None = None) -> pygame.Surface:
    """Кадр после прогрева. Возвращается копия: её переживёт закрытие дисплея.

    Камера ставится в `snap` **после** прогрева: каждый кадр она подтягивается
    за дроном (`Game.update_camera`), и снимок выходил бы не там, куда смотрели.
    """
    for _ in range(WARMUP):
        game.draw_frame(config.TICK)
    if snap is not None:
        game.camera.snap_to(*snap)
        game.draw_frame(config.TICK)
    return game.screen.copy()


def _shore_tile(world) -> tuple[float, float]:
    """Тайл, где рядом и вода, и горы, и земля — самый показательный кадр.

    Идём по воде: у берега видно и воду, и кромку, и горы. Кандидат не должен
    стоять у края карты, иначе половина кадра — пустота за границей мира.
    """
    terrain = world.terrain
    border = 12
    best: tuple[int, tuple[float, float]] | None = None
    for y, x in np.argwhere(terrain == int(Terrain.WATER)):
        if not (border < x < world.width - border and border < y < world.height - border):
            continue
        block = terrain[y - 7:y + 8, x - 7:x + 8]
        score = int((block == int(Terrain.MOUNTAIN)).sum())
        score += int((block == int(Terrain.GROUND)).sum()) // 2
        if best is None or score > best[0]:
            best = (score, (float(x), float(y)))
    if best is None:
        return world.spawn_point()
    return best[1]


def _stock(sim, count: int = 40) -> None:
    """Запас: материалы ролей и по стаку каждого предмета-постройки.

    Здание ставится только из предмета (plan/03), поэтому сцена получает те же
    предметы, что игрок скрафтил бы: постройки ложатся в хотбар, роли — в
    инвентарь (правило `Cargo`).
    """
    for role in ROLES:
        sim.cargo.add(role.id, count)
    for kind in BUILD_ORDER:
        sim.cargo.add(kind, config.BUILDING_STACK)


def _place_base(sim) -> None:
    """Полоса базы над дроном: постройки в ряд, лампы над ними, генераторы с краю.

    Постройки стоят вплотную друг к другу, а сеть в M4 — это соседние тайлы:
    поэтому полоса и есть сеть. Генератор даёт энергию, **только пока горит
    топка** (`Simulation._power_nodes`), поэтому сцена сначала ставит базу,
    потом крутит несколько шагов симуляции и лишь затем добирает генераторы,
    если выработки не хватило на спрос: с одним генератором (🔧 6 против 11)
    лампы оставались погасшими, и ночная сцена показывала ровно не то, ради
    чего она нужна, — «освещено 0 %».
    """
    _stock(sim)
    drone = sim.drone
    x0, y = int(drone.x) - len(BASE_ROW) // 2, int(drone.y) - BASE_DY
    row: list[int] = []
    for i, kind in enumerate(BASE_ROW):
        direction = (1, 0) if kind == CONVEYOR else None
        building = sim.build(kind, x0 + i, y, direction=direction)
        if building is None:              # место занято залежью или реликвией
            continue
        row.append(x0 + i)
        if kind == GENERATOR:
            _fuel(sim, building)
    for x in row:                         # лампы вплотную к полосе — та же сеть
        sim.build(LAMP, x, y - 1)
    for gx, gy in _generator_spots(x0, y):
        if _powered(sim):
            break
        generator = sim.build(GENERATOR, gx, gy)
        if generator is not None:
            _fuel(sim, generator)


def _generator_spots(x0: int, y: int) -> tuple[tuple[int, int], ...]:
    """Куда ещё можно поставить генератор: с обоих концов полосы, потом под ней."""
    return ((x0 - 1, y), (x0 + len(BASE_ROW), y)) + \
           tuple((x0 + i, y + 1) for i in range(len(BASE_ROW)))


def _fuel(sim, generator) -> None:
    """Заливает в генератор топливо: без него он не даёт энергии вовсе."""
    fuel_id = sim.run.items.with_prop(Prop.FLAMMABLE)[0].id
    generator.container(building_def(GENERATOR).accept_id).add(fuel_id, FUEL)


def _powered(sim) -> bool:
    """Хватает ли сети энергии на спрос. Топка разгорается не в тот же шаг,
    поэтому перед проверкой симуляция прокручивается дважды."""
    for _ in range(2):
        sim.update(config.TICK)
    supply, demand = sim.power_summary
    return demand > 0.0 and supply >= demand


def _rng(index: int) -> random.Random:
    """Свой генератор для разлёта частиц: сцена обязана повторяться от запуска."""
    return random.Random(1000 + index)


def _caption(screen: pygame.Surface, rect: pygame.Rect, text: str) -> None:
    """Подпись силуэта под спрайтом — в галерее врагов подписи нужны."""
    widgets.draw_text(screen, text, (rect.centerx, rect.bottom + 2),
                      color=theme.UI_TEXT_DIM, size=config.UI_FONT_SIZE_SMALL,
                      align="center")


def _hint(screen: pygame.Surface, text: str) -> None:
    """Полоска-предупреждение внизу кадра: заготовка, а не игра."""
    w = config.WINDOW_W
    panel = pygame.Rect(0, config.WINDOW_H - 26, w, 26)
    widgets.draw_panel(screen, panel, alpha=236, radius=0)
    widgets.draw_text(screen, text, (12, panel.y + 5), color=theme.UI_TEXT_WARN,
                      size=config.UI_FONT_SIZE_SMALL)


# ------------------------------------------------------------------- запуск
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/screenshot.py",
        description="Скриншоты сцен игры «Свет» без окна (plan/09 §12)",
    )
    parser.add_argument("--scene", action="append", choices=sorted(SCENES),
                        help="какую сцену рендерить (можно несколько; по умолчанию все)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="сид генерации карты")
    parser.add_argument("--out", default=DEFAULT_OUT, help="папка для PNG")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Импорт здесь: `app` тянет pygame, а драйвер должен быть выставлен раньше.
    from src.game import app

    names = args.scene or list(SCENES)
    for name in names:
        game = app.Game(seed=args.seed)
        try:
            result = SCENES[name](game, args.seed)
            # Сцена отдаёт один кадр или несколько с именами (кадры подряд).
            shots = result if isinstance(result, list) else [(name, result)]
            for shot_name, surface in shots:
                path = out / f"{shot_name}.png"
                pygame.image.save(surface, str(path))  # сохраняем, пока жив дисплей
                print(f"{shot_name}: {path} {surface.get_width()}x{surface.get_height()}")
        finally:
            game.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
