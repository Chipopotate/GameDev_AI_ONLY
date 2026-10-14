"""Луч дрона (plan/09 §10): цвета из theme, появление и угасание, пауза, кэши.

Что рисовать, решает только `drone.action` из симуляции, поэтому луч
проверяется двумя способами: отдельно (`LaserBeam` с подставным действием) и
в целом кадре `Renderer.draw_world`, где действие ставит настоящая добыча.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pygame
import pytest

from src.game import config
from src.game.render import quality, theme
from src.game.render.camera import Camera
from src.game.render.effects import ParticlePool
from src.game.render.effects import laser as laser_module
from src.game.render.effects.laser import HOT_ACTIONS, LaserBeam
from src.game.render.renderer import Renderer
from src.game.render.sprites.factory import SpriteFactory
from src.game.sim.simulation import Simulation
from src.game.sim.units import (ACTION_DISMANTLE, ACTION_MINE, ACTION_REPAIR,
                                ACTION_SCAN, ACTION_STUDY, Drone, DroneAction)
from tests.conftest import SEED

ACTIONS = (ACTION_MINE, ACTION_DISMANTLE, ACTION_STUDY, ACTION_SCAN, ACTION_REPAIR)
RED_ACTIONS = (ACTION_MINE, ACTION_DISMANTLE)
BLUE_ACTIONS = (ACTION_STUDY, ACTION_SCAN)

#: Сцена для отдельного луча: камера на дроне, цель в трёх с половиной тайлах.
VIEW = (640, 360)
BACKGROUND = (16, 16, 20)
DRONE_XY = (10.0, 10.0)
TARGET = (13.5, 10.0)


@pytest.fixture(scope="module")
def display():
    pygame.display.init()
    screen = pygame.display.set_mode((config.WINDOW_W, config.WINDOW_H))
    yield screen
    pygame.display.quit()


@pytest.fixture
def scene(display):
    """Луч со своей фабрикой и пулом, камера, дрон и тёмный холст."""
    factory = SpriteFactory()
    beam = LaserBeam(factory, ParticlePool(0, factory), seed=7)
    camera = Camera(*DRONE_XY, viewport_w=VIEW[0], viewport_h=VIEW[1])
    drone = Drone(x=DRONE_XY[0], y=DRONE_XY[1])
    surface = pygame.Surface(VIEW)
    surface.fill(BACKGROUND)
    return beam, factory, camera, drone, surface


# ------------------------------------------------------------------ хелперы
def action(kind: str | None = ACTION_MINE, progress: float = 0.5) -> DroneAction:
    if kind is None:
        return DroneAction()
    return DroneAction(kind, TARGET, progress)


def warm(beam: LaserBeam, kind: str = ACTION_MINE, frames: int = 0) -> None:
    """Держит действие, пока луч не разгорится полностью."""
    frames = frames or math.ceil(config.LASER_FADE_TIME / config.TICK) + 2
    for _ in range(frames):
        beam.update(action(kind), config.TICK)
    assert beam.intensity == 1.0


def beam_points(camera: Camera, start_xy, end_xy, count: int = 9):
    """Экранные точки на средней части луча — вдали от дрона и от цели."""
    sx, sy = camera.world_to_screen(*start_xy)
    ex, ey = camera.world_to_screen(*end_xy)
    out = []
    for i in range(count):
        t = 0.3 + 0.4 * i / (count - 1)
        out.append((int(round(sx + (ex - sx) * t)), int(round(sy + (ey - sy) * t))))
    return out


def channels(surface: pygame.Surface, points) -> tuple[int, int, int]:
    """Сумма каналов по точкам (и по пикселю над и под каждой — луч не в 1 px)."""
    r = g = b = 0
    for x, y in points:
        for dy in (-1, 0, 1):
            color = surface.get_at((x, y + dy))
            r, g, b = r + color.r, g + color.g, b + color.b
    return r, g, b


def background_level(points) -> int:
    return sum(BACKGROUND) * len(points) * 3


# ------------------------------------------------------- таблица цветов
def test_action_color_table_matches_drone_actions():
    """Каждому виду действия дрона — свой цвет, лишних ключей нет."""
    assert set(theme.ACTION_COLORS) == set(ACTIONS)
    assert set(theme.ACTION_CORES) == set(ACTIONS)


@pytest.mark.parametrize("kind", RED_ACTIONS)
def test_burning_actions_are_red(kind):
    assert theme.ACTION_COLORS[kind] == theme.LASER_RED
    assert theme.ACTION_CORES[kind] == theme.LASER_RED_CORE
    assert theme.laser_color(kind) == theme.LASER_RED
    assert theme.laser_core(kind) == theme.LASER_RED_CORE
    assert kind in HOT_ACTIONS


@pytest.mark.parametrize("kind", BLUE_ACTIONS)
def test_reading_actions_are_blue(kind):
    assert theme.ACTION_COLORS[kind] == theme.LASER_BLUE
    assert theme.ACTION_CORES[kind] == theme.LASER_BLUE_CORE
    assert theme.laser_color(kind) == theme.LASER_BLUE
    assert theme.laser_core(kind) == theme.LASER_BLUE_CORE
    assert kind not in HOT_ACTIONS


def test_red_and_blue_read_apart():
    """Красный действительно красный, синий — синий: их не перепутать на глаз."""
    r, g, b = theme.LASER_RED
    assert r > g and r > b
    r, g, b = theme.LASER_BLUE
    assert b > r and b > g


def test_laser_takes_its_colors_only_from_theme():
    """В модуле луча нет своих цветов: только таблица из render/theme.py."""
    source = Path(laser_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{6}\b", source), "hex-цвет в laser.py"
    assert "rgb(" not in source


# ----------------------------------------------------- появление и угасание
def test_no_action_no_beam(scene):
    beam, _factory, camera, drone, surface = scene
    beam.update(action(None), config.TICK)
    assert not beam.visible and beam.intensity == 0.0
    before = surface.copy()
    beam.draw(surface, camera, drone)
    assert beam.dots == 0
    assert pygame.image.tobytes(surface, "RGB") == pygame.image.tobytes(before, "RGB")


def test_beam_fades_in_over_fade_time(scene):
    beam, *_ = scene
    half = config.LASER_FADE_TIME * 0.5
    beam.update(action(), half)
    assert beam.visible
    assert beam.intensity == pytest.approx(0.5)
    beam.update(action(), half)
    assert beam.intensity == pytest.approx(1.0)
    beam.update(action(), config.LASER_FADE_TIME)
    assert beam.intensity == 1.0, "ярче единицы не бывает"


def test_beam_needs_fade_time_frames_at_60_fps(scene):
    """При 60 FPS луч разгорается ровно за 🔧 `LASER_FADE_TIME`, а не мгновенно."""
    beam, *_ = scene
    frames = math.ceil(config.LASER_FADE_TIME / config.TICK - 1e-9)
    for _ in range(frames - 1):
        beam.update(action(), config.TICK)
    assert 0.0 < beam.intensity < 1.0
    beam.update(action(), config.TICK)
    beam.update(action(), config.TICK)
    assert beam.intensity == 1.0


def test_beam_fades_out_after_release(scene):
    beam, *_ = scene
    warm(beam)
    half = config.LASER_FADE_TIME * 0.5
    beam.update(action(None), half)
    assert beam.intensity == pytest.approx(0.5)
    assert beam.visible, "луч гаснет плавно, а не пропадает"
    beam.update(action(None), half)
    assert beam.intensity == pytest.approx(0.0, abs=1e-9)
    beam.update(action(None), config.TICK)
    assert beam.intensity == 0.0 and not beam.visible
    assert beam.target is None


def test_new_action_takes_the_beam_at_once(scene):
    """Сменилось действие — сразу другой цвет и цель, без перетекания."""
    beam, *_ = scene
    warm(beam, ACTION_MINE)
    assert beam.hot and beam.kind == ACTION_MINE
    other = DroneAction(ACTION_SCAN, (12.0, 8.0), 0.2)
    beam.update(other, config.TICK)
    assert beam.kind == ACTION_SCAN and not beam.hot
    assert beam.target == (12.0, 8.0)
    assert beam.progress == pytest.approx(0.2)
    assert beam.intensity == 1.0


def test_pause_freezes_time_and_intensity(scene):
    """dt = 0 (пауза): своё время луча и яркость стоят, картинка не гаснет."""
    beam, _factory, camera, drone, surface = scene
    beam.update(action(), config.LASER_FADE_TIME * 0.5)
    time, intensity = beam.time, beam.intensity
    for _ in range(10):
        beam.update(action(), 0.0)
    assert (beam.time, beam.intensity) == (time, intensity)
    for _ in range(10):
        beam.update(action(None), 0.0)
    assert (beam.time, beam.intensity) == (time, intensity), "и отпускание на паузе не гасит"
    assert beam.visible
    beam.draw(surface, camera, drone)
    assert beam.dots > 0, "на паузе луч виден (замороженная картинка)"


def test_paused_frames_are_identical(scene):
    """На паузе волны не бегут: два кадра подряд совпадают пиксель в пиксель."""
    beam, _factory, camera, drone, surface = scene
    warm(beam)
    frames = []
    for _ in range(2):
        surface.fill(BACKGROUND)
        beam.update(action(), 0.0)
        beam.draw(surface, camera, drone)
        frames.append(pygame.image.tobytes(surface, "RGB"))
    assert frames[0] == frames[1]


def test_running_beam_shimmers(scene):
    """Без паузы красный луч переливается: кадры отличаются."""
    beam, _factory, camera, drone, surface = scene
    warm(beam)
    frames = set()
    for _ in range(6):
        surface.fill(BACKGROUND)
        beam.update(action(), config.TICK)
        beam.draw(surface, camera, drone)
        frames.add(pygame.image.tobytes(surface, "RGB"))
    assert len(frames) > 1


# --------------------------------------------------------------- отрисовка
@pytest.mark.parametrize("kind", ACTIONS)
def test_beam_lights_pixels_along_the_line(scene, kind):
    """Луч рисует светящиеся элементы и делает пиксели вдоль себя ярче фона."""
    beam, _factory, camera, drone, surface = scene
    warm(beam, kind)
    beam.draw(surface, camera, drone)
    assert beam.dots > 0
    points = beam_points(camera, DRONE_XY, TARGET)
    r, g, b = channels(surface, points)
    assert r + g + b > background_level(points) * 2, "вдоль луча ничего не светится"
    if kind in RED_ACTIONS:
        assert r > b, "красный луч должен быть красным"
    else:
        assert b > r, "синий луч должен быть синим"


def test_beam_leaves_the_rest_of_the_screen_alone(scene):
    """Луч светит только вокруг себя: дальний угол экрана не тронут."""
    beam, _factory, camera, drone, surface = scene
    warm(beam)
    beam.draw(surface, camera, drone)
    for corner in ((2, 2), (VIEW[0] - 3, 2), (2, VIEW[1] - 3), (VIEW[0] - 3, VIEW[1] - 3)):
        assert tuple(surface.get_at(corner))[:3] == BACKGROUND


@pytest.mark.parametrize("kind", (ACTION_MINE, ACTION_SCAN))
def test_no_new_surfaces_with_frozen_time(scene, kind):
    """Прогретый луч на паузе: ни новых спрайтов, ни новых ореолов в кадре."""
    beam, factory, camera, drone, surface = scene
    warm(beam, kind)
    beam.draw(surface, camera, drone)
    made = (factory.rendered, factory.halos_made)
    for _ in range(50):
        beam.update(action(kind), 0.0)
        beam.draw(surface, camera, drone)
    assert (factory.rendered, factory.halos_made) == made


def test_no_new_surfaces_once_warmed_up(scene, monkeypatch):
    """С идущим временем кэши стабилизируются: после прогрева кадр не создаёт Surface.

    Прогрев — несколько сотен кадров обоих цветов с разным прогрессом: так
    перебираются все фазы волн, пульса и дыхания, и всё, что луч кэширует,
    уже лежит в кэше.
    """
    beam, factory, camera, drone, surface = scene

    def frames(count: int) -> None:
        for i in range(count):
            kind = ACTIONS[(i // 60) % len(ACTIONS)]
            beam.update(action(kind, progress=(i % 90) / 90.0), config.TICK)
            beam.draw(surface, camera, drone)

    frames(480)
    made = (factory.rendered, factory.halos_made)

    created: list[tuple] = []
    real_surface = pygame.Surface

    class CountingSurface(real_surface):
        def __init__(self, *args, **kwargs):
            created.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(pygame, "Surface", CountingSurface)
    frames(300)
    monkeypatch.setattr(pygame, "Surface", real_surface)
    assert (factory.rendered, factory.halos_made) == made
    assert created == [], "луч создаёт Surface в кадре"


def test_low_quality_draws_a_plain_line(scene):
    """`low`: простая линия цвета луча, без ореола и светящихся точек."""
    beam, _factory, camera, drone, surface = scene
    warm(beam, ACTION_MINE)
    with quality.override(quality.LOW):
        beam.draw(surface, camera, drone)
    assert beam.dots == 0
    mid_x = int(round(camera.world_to_screen((DRONE_XY[0] + TARGET[0]) / 2, 0)[0]))
    mid_y = int(round(camera.world_to_screen(0, DRONE_XY[1])[1]))
    column = {tuple(surface.get_at((mid_x, y)))[:3] for y in range(mid_y - 2, mid_y + 3)}
    assert theme.LASER_RED in column, "на low — линия цвета луча"
    # Ореола нет: чуть дальше от линии — нетронутый фон.
    assert tuple(surface.get_at((mid_x, mid_y + 6)))[:3] == BACKGROUND
    assert tuple(surface.get_at((mid_x, mid_y - 6)))[:3] == BACKGROUND


def test_low_quality_without_action_draws_nothing(scene):
    beam, _factory, camera, drone, surface = scene
    before = surface.copy()
    with quality.override(quality.LOW):
        beam.update(action(None), config.TICK)
        beam.draw(surface, camera, drone)
    assert pygame.image.tobytes(surface, "RGB") == pygame.image.tobytes(before, "RGB")


# --------------------------------------------------------- кадр целиком
def mining_sim() -> tuple[Simulation, object]:
    """Симуляция, где дрон стоит у залежи и держит ЛКМ на ней (добыча идёт)."""
    sim = Simulation(SEED)
    deposits = sorted((d for d in sim.world.deposits if not d.depleted),
                      key=lambda d: sim.drone.distance_to_tile(d.x, d.y))
    for deposit in deposits:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            x, y = deposit.x + dx, deposit.y + dy
            if not sim.world.is_walkable_tile(x, y) or sim.world.deposit_at(x, y):
                continue
            if sim.run.relic_at(x, y) is not None:
                continue
            sim.drone.x, sim.drone.y = x + 0.5, y + 0.5
            sim.set_cursor_tile(deposit.x, deposit.y)
            sim.set_mining(True)
            for _ in range(3):
                sim.update(config.TICK)
            assert sim.drone.action.kind == ACTION_MINE, "добыча не началась"
            return sim, deposit
    raise AssertionError("на карте нет залежи со свободным тайлом рядом")


def draw_frames(renderer: Renderer, screen, sim, camera, count: int,
                dt: float = config.TICK) -> None:
    for _ in range(count):
        renderer.draw_world(screen, sim, camera, dt=dt)


def world_camera(sim: Simulation) -> Camera:
    return Camera(sim.drone.x, sim.drone.y,
                  viewport_w=config.WINDOW_W, viewport_h=config.WINDOW_H)


def fade_frames() -> int:
    return math.ceil(config.LASER_FADE_TIME / config.TICK) + 2


def test_renderer_draws_the_beam_while_mining(display):
    sim, _deposit = mining_sim()
    renderer = Renderer(sim.world)
    camera = world_camera(sim)
    draw_frames(renderer, display, sim, camera, fade_frames())
    assert renderer.laser.visible and renderer.laser.kind == ACTION_MINE
    assert renderer.laser.dots > 0

    sim.set_mining(False)
    sim.update(config.TICK)
    assert not sim.drone.action.active
    draw_frames(renderer, display, sim, camera, fade_frames())
    assert not renderer.laser.visible and renderer.laser.dots == 0, "отпустил — луч погас"


def test_renderer_beam_is_seen_at_night(display):
    """Ночью луч поверх тьмы: вдоль него кадр заметно ярче, чем без луча."""
    sim, deposit = mining_sim()
    cycle = sim.time_cycle
    cycle.update(cycle.first_day_length + cycle.night_length * 0.5)
    sim.update(config.TICK)
    assert cycle.is_night and sim.darkness > 0.5
    assert sim.drone.action.kind == ACTION_MINE
    renderer = Renderer(sim.world)
    camera = world_camera(sim)
    draw_frames(renderer, display, sim, camera, fade_frames())
    assert renderer.laser.visible and renderer.laser.dots > 0
    points = beam_points(camera, (sim.drone.x, sim.drone.y),
                         (deposit.x + 0.5, deposit.y + 0.5))
    lit = channels(display, points)

    sim.set_mining(False)
    sim.update(config.TICK)
    draw_frames(renderer, display, sim, camera, 45)   # луч погас, искры догорели
    assert not renderer.laser.visible
    dark = channels(display, points)
    assert sum(lit) > sum(dark) * 2, (lit, dark)
    gain = [a - b for a, b in zip(lit, dark)]
    assert gain[0] > gain[2], "и ночью луч добычи читается красным"


def test_renderer_keeps_the_beam_on_pause(display):
    """Пауза (рендер получает dt = 0): луч на месте, его время стоит."""
    sim, _deposit = mining_sim()
    renderer = Renderer(sim.world)
    camera = world_camera(sim)
    draw_frames(renderer, display, sim, camera, fade_frames())
    time = renderer.laser.time
    draw_frames(renderer, display, sim, camera, 10, dt=0.0)
    assert renderer.laser.time == time
    assert renderer.laser.visible and renderer.laser.dots > 0


def test_low_quality_spawns_no_beam_sparks(scene):
    """`low` — без свечения: у луча нет искр-ореолов в точке попадания."""
    beam, _factory, _camera, _drone, _surface = scene
    with quality.override(quality.LOW):
        for _ in range(120):
            beam.update(action(ACTION_MINE, 1.0), config.TICK)
    assert beam.pool.active_count() == 0


def test_low_quality_draws_pool_sparks_without_halos(scene):
    """Искры пула на `low` — точки, а не ореолы (plan/09 §3.7: «low — без свечения»)."""
    _beam, factory, camera, _drone, surface = scene
    pool = ParticlePool(0, factory)
    pool.spawn("spark", *TARGET, 0.0, 0.0, 0.5, 1.0, theme.LASER_RED)
    made = factory.halos_made
    with quality.override(quality.LOW):
        pool.draw(surface, camera)
    assert pool.drawn == 1
    assert factory.halos_made == made, "на low ореол не строится"
