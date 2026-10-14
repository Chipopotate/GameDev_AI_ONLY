"""Автоматические тесты графики (plan/09 §13).

Пять проверок из документа, и у каждой — своя причина:

1. **Ресурсы** различимы на 200 сидах: забег перемешивает цвета и огранки, и
   «два зелёных камня одной формы» игрок не различит (plan/09 §7).
2. **Силуэты врагов** различаются маской: в темноте врага выдают светящиеся
   точки, но тип надо читать и днём (plan/09 §8).
3. **Контраст** тела врага с землёй — или светящиеся элементы: тело тёмное, и
   если оно сольётся с землёй, враг станет невидимкой.
4. **Кэш**: после прогрева кадр не создаёт ни одной `Surface` — это правило
   производительности из CLAUDE.md, и оно же условие 60 FPS.
5. **Производительность**: тестовая сцена (200 сущностей + свет + эффекты)
   укладывается в кадр на `medium` (plan/09 §13).
"""

from __future__ import annotations

import time

import numpy as np
import pygame
import pytest

from src.game import config
from src.game.data.buildings import (CONVEYOR, GENERATOR, LAMP,
                                     building as building_def)
from src.game.data.resources import Prop
from src.game.render import quality, theme
from src.game.render.camera import Camera
from src.game.render.renderer import Renderer
from src.game.render.sprites import SpriteCache, organic
from src.game.render.sprites.factory import SpriteFactory
from src.game.render.sprites.gems import (HUE_MIN_DISTANCE, SILHOUETTE_MAX_IOU,
                                          hue_distance, mask_iou)
from src.game.sim.buildings import DIRECTIONS
from src.game.sim.commands import SelectHotbar
from src.game.sim.run_gen import generate_run
from src.game.sim.simulation import Simulation
from tests.conftest import SEED, STOCK_COUNT, place, slot_of, stock

#: Размер спрайта в проверках различимости: вдвое крупнее игрового тайла.
#: Мелкий значок размывает силуэт, и различие «на глаз» пропадает раньше, чем
#: различие в маске, — проверять надо то, что читается.
CHECK_SIZE = 64
#: Фаза пульсации врагов в проверках: форма от неё зависит, поэтому берётся
#: одна и та же фаза — иначе сравнивались бы разные кадры дыхания.
FOE_PHASE = 1.1
#: Запас, на который нарисованный силуэт ресурса может обогнать внутреннюю
#: маску. Внутренняя маска — растр 32×32, а спрайт рисуется с обводкой и
#: сглаживанием, поэтому её IoU систематически чуть ниже (замер: 0.79 против
#: 0.80). Запас нужен ровно на эту разницу, а не на «пусть сойдёт».
DRAWN_IOU_SLACK = 0.03
#: Кадров прогрева в тесте кэша: 1.5 с — дольше самой долгой частицы (искра
#: живёт до 1.3 с, кольцо и дым постройки — меньше секунды).
WARMUP_FRAMES = int(1.5 / config.TICK)


@pytest.fixture(scope="module")
def display() -> pygame.Surface:
    """Дисплей в dummy-режиме: тестам графики окно не нужно."""
    pygame.display.init()
    screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H))
    yield screen
    pygame.display.quit()


# ------------------------------------------------------------------ утилиты
def iou_of(first, second) -> float:
    """Пересечение силуэтов поверх объединения — по двум маскам."""
    overlap = first.overlap_area(second, (0, 0))
    union = first.count() + second.count() - overlap
    return overlap / union if union else 1.0


def lamps_around(sim: Simulation, count: int) -> list:
    """Зажигает до `count` ламп вокруг дрона, на свободных тайлах.

    Тайлы перебираются, а не задаются: стартовая зона — обычная карта, и по
    половине соседних тайлов уже стоит залежь или гора. Лампа — не предмет
    проверки, проверке нужен свет.
    """
    sx, sy = int(sim.drone.x), int(sim.drone.y)
    lamps = []
    # Радиус 3, а не больше: у каждой лампы рядом встаёт генератор, а здание
    # ставится не дальше 🔧 `BUILD_RANGE` от дрона — на диагонали ±4 генератор
    # уже не влезает в это расстояние.
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            if len(lamps) >= count:
                return lamps
            if abs(dx) < 2 and abs(dy) < 2:
                continue                      # под дроном лампе места нет
            if not sim.can_place(LAMP, sx + dx, sy + dy):
                continue                      # залежь, гора или уже занято
            lamps.append(lit_lamp(sim, sx + dx, sy + dy))
    return lamps


def lit_lamp(sim: Simulation, x: int, y: int):
    """Горящая лампа на (x, y): предмет-лампа и рядом заправленный генератор.

    То же, что `power_lamp` из conftest, но без `stock` на каждую лампу: тот
    выдаёт по стаку всех построек, и уже на второй лампе запас в хранилище
    дрона не влезает. Здесь ламп несколько, а от каждой нужен только свет —
    поэтому выдаются ровно лампа и генератор.
    """
    lamp = place(sim, LAMP, x, y)
    assert lamp is not None, f"лампа не встала на {x}, {y}"
    for dx, dy in DIRECTIONS:
        if not sim.can_place(GENERATOR, x + dx, y + dy):
            continue
        generator = place(sim, GENERATOR, x + dx, y + dy)
        fuel = sim.run.items.with_prop(Prop.FLAMMABLE)[0].id
        generator.container(building_def(GENERATOR).accept_id).add(fuel, STOCK_COUNT)
        break
    else:
        raise AssertionError(f"рядом с лампой {x}, {y} нет места под генератор")
    for _ in range(2):                         # топка разгорается, сеть раздаёт
        sim.update(config.TICK)
    assert lamp.on, "лампа не загорелась от генератора"
    return lamp


def body_pixels(surf: pygame.Surface) -> np.ndarray:
    """Пиксели тела спрайта: непрозрачные, без мягкого края сглаживания."""
    rgb = pygame.surfarray.array3d(surf).astype(np.float32)
    alpha = pygame.surfarray.array_alpha(surf)
    opaque = alpha > 200
    if not opaque.any():
        return np.empty((0, 3), dtype=np.float32)
    return rgb[opaque]


def ground_brightness() -> float:
    """Средняя яркость земли: с ней и сравнивается тело врага (plan/09 §13)."""
    tiles = [theme.GROUND_VARIANTS[i % len(theme.GROUND_VARIANTS)] for i in range(4)]
    return sum(sum(c) for c in tiles) / (3.0 * len(tiles))


# ------------------------------------------------------------------ ресурсы
def test_resources_are_pairwise_distinguishable(display):
    """plan/09 §7, §13: на 200 сидах ресурсы забега различимы попарно.

    Проверяются обе стороны правила: оттенок разошёлся **или** силуэт не похож.
    Считается дважды — по внутренней маске огранки (ею пользуется сам генератор
    форм) и по нарисованному спрайту (то, что видит игрок). Второй расчёт нужен,
    потому что маска и спрайт могут разойтись, и тогда правило охраняет не то.
    """
    worst_hue = (1.0, None)
    worst_drawn = (0.0, None)
    for seed in range(config.VISUAL_SEEDS):
        run = generate_run(seed)
        sprites = SpriteCache(run.items)      # огранки строятся по ресурсам забега
        items = run.items.natural
        masks = {item.id: pygame.mask.from_surface(
            sprites.gem(item.id, CHECK_SIZE, item.color)) for item in items}
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                first, second = items[i], items[j]
                hue = hue_distance(first.color, second.color)
                if hue >= HUE_MIN_DISTANCE:
                    continue                       # оттенки разошлись — пара читается
                inner = mask_iou(sprites.gems.mask(first.id), sprites.gems.mask(second.id))
                drawn = iou_of(masks[first.id], masks[second.id])
                where = f"сид {seed}, {first.name} / {second.name}"
                assert inner <= SILHOUETTE_MAX_IOU, (
                    f"{where}: форма не перевыбрана, IoU масок {inner:.3f}")
                assert drawn <= SILHOUETTE_MAX_IOU + DRAWN_IOU_SLACK, (
                    f"{where}: спрайты похожи, IoU {drawn:.3f}")
                if hue < worst_hue[0]:
                    worst_hue = (hue, where)
                if drawn > worst_drawn[0]:
                    worst_drawn = (drawn, where)
    # Худшая пара должна быть далеко от порога, а не «в притирку»: запас — это
    # то, что отличает работающее правило от совпадения на этих 200 сидах.
    assert worst_drawn[0] < SILHOUETTE_MAX_IOU - 0.01, (
        f"худшая пара ресурсов у самого порога: {worst_drawn[0]:.3f} ({worst_drawn[1]})")


def test_resource_colours_stay_varied(display):
    """plan/09 §7: палитра забега разнообразна, а не «девять оттенков зелёного»."""
    for seed in range(20):
        items = generate_run(seed).items.natural
        hues = sorted(hue_distance(items[0].color, item.color) for item in items[1:])
        assert hues[-1] > 0.25, f"сид {seed}: весь забег в одном углу круга"


# -------------------------------------------------------------------- враги
def test_foe_silhouettes_differ(display):
    """plan/09 §13: силуэты врагов попарно различаются маской (IoU < 🔧 0.75)."""
    sprites = SpriteCache()
    masks = {kind: pygame.mask.from_surface(
        sprites.creature(kind, CHECK_SIZE, FOE_PHASE)) for kind in organic.SHAPES}
    worst = (0.0, None)
    kinds = sorted(masks)
    for i in range(len(kinds)):
        for j in range(i + 1, len(kinds)):
            pair = f"{kinds[i]} / {kinds[j]}"
            value = iou_of(masks[kinds[i]], masks[kinds[j]])
            assert masks[kinds[i]].count() > 0, f"{kinds[i]}: пустой силуэт"
            if value > worst[0]:
                worst = (value, pair)
            assert value < config.FOE_MASK_MAX_IOU, (
                f"силуэты {pair} похожи: IoU {value:.3f}")
    assert worst[0] < config.FOE_MASK_MAX_IOU - 0.001, (
        f"самая похожая пара врагов у самого порога: {worst[0]:.3f} ({worst[1]})")


def test_foe_body_contrasts_with_the_ground(display):
    """plan/09 §13: тело врага отличается от земли — или у него есть свечение.

    Порог не «хоть сколько-нибудь»: тело врага тёмное по замыслу (plan/09 §1),
    и разница в 10 из 255 — это уже «сливается» на глаз.
    """
    sprites = SpriteCache()
    ground = ground_brightness()
    for kind in organic.SHAPES:
        body = body_pixels(sprites.creature(kind, CHECK_SIZE, FOE_PHASE))
        assert body.size, f"{kind}: тело не нарисовано"
        body_diff = abs(float(body.mean()) - ground)
        glow_diff = abs(float(np.percentile(body.mean(axis=1), 95)) - ground)
        assert (body_diff >= config.FOE_BODY_CONTRAST_MIN
                or glow_diff >= config.FOE_GLOW_CONTRAST_MIN), (
            f"{kind}: тело сливается с землёй "
            f"(тело {body_diff:.1f}, свечение {glow_diff:.1f} из 255)")


# --------------------------------------------------------------------- кэш
def _surfaces_made(renderer: Renderer) -> dict[str, int]:
    """Счётчики всего, что создаёт `Surface`, — по одному на каждый кэш.

    Считать сами `Surface` нечем (это тип из C), поэтому считаются кэши: если
    кадр создал поверхность мимо кэша, счётчик прогрева не изменится только
    тогда, когда поверхность одноразовая — а это и есть искомый мусор.
    """
    return {
        "спрайты": renderer.sprites.factory.rendered,
        "ореолы": renderer.sprites.factory.halos_made,
        "цветокоррекция": renderer.postfx.tints_made,
        "виньетка": renderer.postfx.vignettes_made,
        "чанки": len(renderer.chunks._chunks) + len(renderer.chunks._scaled),
        "значки": (len(renderer.icons._items) + len(renderer.icons._scaled)
                   + len(renderer.icons._props) + len(renderer.icons._marks)),
        "пятна света": len(renderer._glow_cache),
        "призрак": (len(renderer.ghost._ghosts) + len(renderer.ghost._rings)
                    + len(renderer.ghost._frames)),
    }


def test_warm_frame_creates_no_new_surfaces(display):
    """CLAUDE.md и plan/09 §13: после прогрева кадр не создаёт ни одной `Surface`.

    Сцена ночная и полная: лампа (пятно света и мягкая тьма), кучи предметов
    (значки), застройка, частицы. Днём большая часть этих путей не работает, и
    такой тест не поймал бы новую поверхность на кадр в самом дорогом случае.
    """
    sim = Simulation(SEED)
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(sim.world)
    lamps = lamps_around(sim, 4)
    assert lamps, "ни одной лампы не встало — сцена не та"
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)
    assert sim.darkness == pytest.approx(1.0), "сцена должна быть ночной"

    item = sim.run.items.natural_ids[0]
    x0, y0, _x1, _y1 = camera.visible_tiles(margin=1)
    for i in range(40):
        sim.drops.spawn_tile(item, i % 5 + 1, x0 + i % 8, y0 + i // 8)

    # Прогрев — дольше жизни любой частицы: кэш частиц конечен (ступени размера
    # и угасания), но ступень, до которой частица ещё не дожила, честно
    # рисуется впервые. Новая поверхность после прогрева — это уже мусор.
    for _ in range(WARMUP_FRAMES):
        renderer.effects.add_shake(config.SCREEN_SHAKE_MAX)
        spark(renderer, sim, 40)
        renderer.draw_world(display, sim, camera, dt=config.TICK)
    assert renderer.glows_drawn > 0, "лампа должна светить, иначе сцена не та"

    before = _surfaces_made(renderer)
    for _ in range(10):
        spark(renderer, sim, 20)
        renderer.draw_world(display, sim, camera, dt=config.TICK)
    after = _surfaces_made(renderer)
    grew = {name: (before[name], after[name]) for name in after if after[name] != before[name]}
    assert not grew, f"кадр создаёт поверхности каждый раз: {grew}"
    assert all(lamp.on for lamp in lamps), "лампы погасли — сцена развалилась, а не кэш"


@pytest.mark.parametrize("kind", [LAMP, CONVEYOR])
def test_warm_frame_with_a_ghost_creates_no_new_surfaces(display, kind):
    """Призрак кэшируется по (вид, поворот, цвет, размер): кадр с ним — без `Surface`.

    Курсор ходит между зелёным тайлом рядом с дроном и красным за 🔧
    `BUILD_RANGE`: оба цвета, рамка, заливка, шевроны конвейера и кольцо
    дальности после прогрева берутся из кэша.
    """
    sim = stock(Simulation(SEED))
    assert sim.apply(SelectHotbar(slot_of(sim.hotbar, kind)))
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(sim.world)
    dx, dy = int(sim.drone.x), int(sim.drone.y)
    near = (dx + 1, dy)
    far = (dx + int(config.BUILD_RANGE) + 3, dy)
    assert sim.can_place(kind, *near) and not sim.can_place(kind, *far)
    tiles = (near, far)

    for i in range(WARMUP_FRAMES):
        renderer.draw_world(display, sim, camera, dt=config.TICK,
                            ghost_tile=tiles[i % 2])
    before = _surfaces_made(renderer)
    drawn = renderer.ghost.drawn
    for i in range(10):
        renderer.draw_world(display, sim, camera, dt=config.TICK,
                            ghost_tile=tiles[i % 2])
    assert renderer.ghost.drawn - drawn == 10, "призрак рисовался в каждом кадре"
    after = _surfaces_made(renderer)
    grew = {name: (before[name], after[name]) for name in after if after[name] != before[name]}
    assert not grew, f"кадр с призраком создаёт поверхности: {grew}"


# ------------------------------------------------------------------ ореолы
def test_halo_is_as_bright_as_its_colour(display):
    """Ореол — это свет: в центре он `peak` × цвет, а не чёрное пятно.

    Цвет уже 0..255, а `peak` — доля 0..1: лишнее деление на 255 делало все
    ореолы фабрики чёрными, и при аддитивном блите неона построек, ламп,
    огня двигателей и искр просто не было видно.
    """
    factory = SpriteFactory()
    color = (200, 120, 60)
    radius = 12
    center = (radius, radius)
    full = factory.halo(radius, color, peak=1.0).get_at(center)[:3]
    assert all(c > p * 1.0 * 0.5 for c, p in zip(full, color)), f"ореол тёмный: {full}"
    half = factory.halo(radius, color, peak=0.5).get_at(center)[:3]
    assert all(abs(c - p * 0.5) <= 1 for c, p in zip(half, color)), half
    edge = factory.halo(radius, color, peak=1.0).get_at((0, radius))[:3]
    assert sum(edge) < sum(full), "к краю ореол гаснет"
    hot = factory.halo(radius, color, peak=1.5).get_at(center)[:3]
    assert hot == (255, 180, 90), f"яркость насыщается, а не переполняет байт: {hot}"


# --------------------------------------------------------- производительность
def test_scene_stays_within_frame_budget(display):
    """plan/09 §13: 200 сущностей + свет + эффекты укладываются в кадр на `medium`."""
    sim = Simulation(SEED)
    stock(sim)
    camera = Camera(*sim.world.spawn_point(), viewport_w=config.WINDOW_W,
                    viewport_h=config.WINDOW_H)
    renderer = Renderer(sim.world)
    item = sim.run.items.natural_ids[0]
    x0, y0, _x1, _y1 = camera.visible_tiles(margin=1)
    for i in range(200):                   # все двести — в кадре, как в приёмке M3.5
        sim.drops.spawn_tile(item, i % 7 + 1, x0 + i % 20, y0 + i // 20)
    lamps = lamps_around(sim, 6)           # свет, тьма и ореолы в том же кадре
    assert len(lamps) >= 4, f"ламп встало только {len(lamps)}"
    sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)

    with quality.override(quality.MEDIUM):
        for _ in range(3):
            spark(renderer, sim, 60)
            renderer.draw_world(display, sim, camera, dt=config.TICK)
        assert renderer.drops_drawn == 200
        assert renderer.glows_drawn >= len(lamps), "лампы должны светить"
        best = min(_frame_ms(renderer, display, sim, camera) for _ in range(5))

    assert best < config.SCENE_BUDGET_MS, (
        f"тестовая сцена заняла {best:.1f} мс — 60 FPS это {config.SCENE_BUDGET_MS:.0f} мс")


def spark(renderer: Renderer, sim, count: int) -> None:
    """Вспышка частиц в кадре: без эффектов замер был бы замером одной карты."""
    renderer.particles.burst("spark", sim.drone.x, sim.drone.y, count, 3.0,
                             ttl=1.0, size=3.0, color=theme.NEON_CYAN,
                             rng=renderer.effects.rng)


def _frame_ms(renderer: Renderer, display: pygame.Surface, sim, camera) -> float:
    """Время одного кадра целиком, в миллисекундах."""
    start = time.perf_counter()
    renderer.draw_world(display, sim, camera, dt=config.TICK)
    return (time.perf_counter() - start) * 1000.0
