"""Скриншоты сцен без окна (plan/09 §12).

Агент обязан **смотреть** результат, а не верить коду: инструмент рендерит
четыре сцены в `screens/`, и по ним проверяется стиль (свой/чужой, силуэты,
неон в темноте, читаемость состояний, отсутствие визуального мусора).

Запуск:
    py tools/screenshot.py                 # все четыре сцены, сид по умолчанию
    py tools/screenshot.py --scene night   # только одна
    py tools/screenshot.py --seed 42 --out screens

Сцены:
    gallery — вся сетка спрайтов в 3× с подписями (plan/09 §12.1);
    terrain — земля, горы, вода и залежи днём, без отладочных слоёв (§12.2);
    layers  — тот же кадр с отладочным слоем света (F2): какие тайлы освещены;
    base    — база днём крупно: провода, конвейер, запитанная и обесточенная сеть;
    night   — ночь: лампы, свет, неон построек (§12.3);
    foes    — силуэты врагов и частицы (§12.4, заготовка: врагов в симуляции
              ещё нет — M5, поэтому на кадр кладутся сами спрайты из
              `render/sprites/organic.py`).

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
import random  # noqa: E402

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from src.game import config  # noqa: E402
from src.game.data.buildings import (ASSEMBLER, CONVEYOR, DRILL, GENERATOR,  # noqa: E402
                                     LAB, LAMP, STORAGE, WIRE,
                                     building as building_def)
from src.game.data.recipes import ROLES  # noqa: E402
from src.game.data.resources import Prop  # noqa: E402
from src.game.data.terrain import Terrain  # noqa: E402
from src.game.render import theme  # noqa: E402
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
        if kind == LAMP:
            sim.place_lamp(x, y)
            continue
        building = sim.build(kind, x, y, direction=direction)
        if building is not None and kind == GENERATOR:
            _fuel(sim, building)
    for _ in range(3):
        sim.update(config.TICK)
    sim.toasts.clear()
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


SCENES = {
    "gallery": shot_gallery,
    "terrain": shot_terrain,
    "layers": shot_layers,
    "base": shot_base,
    "night": shot_night,
    "foes": shot_foes,
}


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
    """Запас материалов: сцена ставится так, как её поставил бы игрок."""
    for role in ROLES:
        sim.inventory.add(role.id, count)


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
        sim.place_lamp(x, y - 1)
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
            surface = SCENES[name](game, args.seed)
            path = out / f"{name}.png"
            pygame.image.save(surface, str(path))     # сохраняем, пока жив дисплей
        finally:
            game.close()
        print(f"{name}: {path} {surface.get_width()}x{surface.get_height()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
