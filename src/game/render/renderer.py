"""Отрисовка мира: рельеф, залежи, постройки, дрон, свет и тьма.

Рендер только читает состояние симуляции и ничего в нём не меняет (это
проверяет `tests/test_render.py`).

Порядок кадра — из plan/09 §3.5, и он не случаен:

    земля → заражение на земле → тени → здания → юниты → тьма
        → emissive и свечение → частицы → виньетка и цветокоррекция → UI

Тьма рисуется **поверх** зданий и дрона (иначе ночью всё было бы видно), а
свет — **поверх тьмы**: неон не гаснет в темноте, он и есть то, что видно
ночью. Поэтому у света отдельный аддитивный проход после затемнения.

Всё, что зависит от времени (`anim_time`), считается здесь, а не в спрайтах:
спрайт рисуется один раз и лежит в кэше, а «живая» часть (шевроны ленты, ротор,
стрела сборщика, мерцание лампы) блитится каждый кадр из тех же кэшей. После
прогрева в кадре не создаётся ни одной `Surface` — это проверяет
`tests/test_visual.py`. Ради этого все плавные величины (яркость «дыхания»,
ступень тьмы) квантуются: плавная яркость означала бы новую поверхность
ореола каждый кадр.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from src.game import config
from src.game.data.buildings import (ASSEMBLER, CONVEYOR, DRILL, GENERATOR, LAB,
                                     STORAGE, building as building_def)
from src.game.data.enemies import ENEMY_TYPES
from src.game.data.relics import relic_def
from src.game.data.terrain import Terrain
from src.game.render import quality, theme
from src.game.render.camera import Camera
from src.game.render.effects import EffectsDirector, ParticlePool
from src.game.render.icons import IconCache
from src.game.render.overlay import TileOverlay
from src.game.render.postfx import PostFX
from src.game.render.sprites import SpriteCache, buildings, factory
from src.game.render.terrain_chunks import ChunkCache
from src.game.sim.buildings import DIRECTIONS
from src.game.sim.simulation import Simulation
from src.game.sim.units import Drone
from src.game.sim.world import World

DEPOSIT_BASE_SIZE = 0.42   # доля тайла под значок залежи
RELIC_BASE_SIZE = 0.44     # доля тайла под реликвию
BUILDING_SIZE = 1.0        # постройка занимает тайл целиком (plan/09 §5)
DROP_ICON_TILE = 0.62      # доля тайла под значок брошенного предмета
#: Частота мигания исчезающего предмета — из конфига: время жизни предмета это
#: решение симуляции, и рендер не должен заводить своё второе время.
DROP_BLINK_HZ = config.DROP_BLINK_HZ
#: Сдвиги для нескольких куч **на одном тайле** (в долях тайла): без них
#: верхняя куча закрывала бы нижнюю и игрок видел бы одну вместо двух.
DROP_OFFSETS = ((0.0, 0.0), (-0.28, -0.28), (0.28, 0.28), (-0.28, 0.28),
                (0.28, -0.28), (0.0, -0.36))
# Доля радиуса света дрона, внутри которой совсем светло; дальше — плавный
# переход к тьме, чтобы пятно не было обрубленным кругом.
DRONE_LIGHT_INNER = 0.45
#: Добавка яркости в центре пятна дрона и лампы (аддитивно). Пятно — только
#: оттенок света поверх того, что открыла тьма: прежние 🔧 120/100 по максимуму
#: поднимали **всё** под светом до бежевого, и чёрные контуры построек, тени и
#: детали пропадали в «тумане». Тёплый свет при этом обязан перебивать и
#: синеву ночной цветокоррекции, иначе лампа читалась бы как «ещё одна синева»
#: (проверяет `test_drone_light_is_cool_and_lamp_light_is_warm`).
DRONE_GLOW_PEAK = 34
LAMP_GLOW_PEAK = 44
LAMP_GLOW_INNER = 0.20     # доля радиуса лампы, где свет ещё ровный
#: Показатель спада яркости от центра пятна к краю. Общий для самого пятна
#: (`_make_glow`) и для слоя света (`_light_field`) — иначе мягкий край тьмы
#: и пятно света имели бы разную форму и на стыке был бы виден ободок.
GLOW_FALLOFF_POW = 1.35
#: Насколько пятно лампы уходит в холодный оттенок к краю: центр тёплый, край
#: нейтральный (plan/09 §9). Много дать нельзя — лампа перестанет быть тёплой.
LAMP_COOL_MIX = 0.15
#: Ступеней тьмы, по которым квантуется яркость свечения. Плавная тьма давала бы
#: новую поверхность пятна каждый кадр, поэтому яркость дискретна.
GLOW_LEVELS = 4
#: Ступеней «дыхания» лампы, ядра и двигателей дрона — по той же причине.
BREATH_STEPS = 5
LAMP_BREATH_HZ = 3.1       # как часто «дышит» лампа
CORE_BREATH_HZ = 4.0       # и ядро генератора
#: Доля яркости emissive: здание лишь «подсвечено» своим неоном, лампа — свет.
#: Прежние 0.42/0.45 заливали полосу базы сплошным бежевым пятном.
EMISSIVE_BUILDING = 0.24
EMISSIVE_LAMP = 0.34
#: Радиус свечения в тайлах: у здания — вокруг корпуса, у лампы — шире.
BUILDING_BLOOM_TILES = 0.75
LAMP_BLOOM_TILES = 1.0
DEBUG_LAYER_ALPHA = 96     # плотность отладочного слоя света (F2)
#: Насколько тень дрона смещена вниз-вправо: он «парит» (plan/09 §6).
DRONE_SHADOW_OFFSET = (0.16, 0.30)
DRONE_BOB = 0.06           # амплитуда покачивания дрона в тайлах
DRONE_BOB_HZ = 2.6
DRONE_SIZE = 1.15          # доля тайла под корпус дрона
#: Шевронов на ленте, какую долю тайла занимает дорожка и скорость бега.
CHEVRONS = 3
CHEVRON_SPAN = 0.62
CHEVRON_SPEED = 1.6        # тайлов в секунду
WIRE_FLOW_SPEED = 0.9      # скорость бегущей по проводу точки, тайлов в секунду
#: Значок «нет питания»: доля тайла, сдвиг от центра здания к правому верхнему
#: углу (значок-«бейдж» не закрывает саму постройку) и частота мигания.
NO_POWER_SIZE = 0.46
NO_POWER_OFFSET = (0.30, -0.30)
NO_POWER_BLINK_HZ = 1.2
FILL_BAR_WIDTH = 0.60      # доля тайла под полоску заполнения склада
BAR_WIDTH = 0.90           # доля тайла под полосу прогресса
WATER_GLINT_ALPHA = 46     # плотность блика по воде, 0..255
#: Заражение (M8): плотность активного и погасшего слоя, 0..255.
INFECT_ACTIVE_ALPHA = 110
INFECT_DEAD_ALPHA = 55
SPROUT_SIZE = 1.6          # доля тайла под росток
INCUBATOR_SIZE = 1.1       # и под инкубатор


class Renderer:
    """Рисует мир. Кэши (чанки, значки, спрайты, слои) живут здесь и переживают кадры."""

    def __init__(self, world: World, tile: int = config.TILE) -> None:
        self.world = world
        self.tile = tile
        self.chunks = ChunkCache(world, tile)
        self.sprites = SpriteCache(world.items)
        # Значки интерфейса рисуются теми же спрайтами, что и мир: ресурс в
        # инвентаре обязан выглядеть как тот же камень на земле (plan/09 §7).
        self.icons = IconCache(world.items, sprites=self.sprites)
        self.overlay = TileOverlay()
        # Блик по воде — **отдельный** слой: он анимирован, а тьма дискретна, и
        # складывать их в один слой значило бы перерисовывать тьму каждый кадр.
        self.water = TileOverlay()
        # Заражение — свой потайловый слой (plan/09 §4): оно «дышит», а чанк
        # рельефа статичен.
        self.infection = TileOverlay()
        self._world_version = world.version
        self.postfx = PostFX()
        self.particles = ParticlePool(0, self.sprites.factory)
        self.effects = EffectsDirector(self.particles, world.seed)
        # Пятна света: ключ — (вид источника, радиус, яркость). Ровно по одному
        # на источник, и между кадрами они не пересоздаются.
        self._glow_cache: dict[tuple[str, int, int], pygame.Surface] = {}
        # Буферы emissive: маленькая поверхность и её растянутая копия.
        self._emissive: pygame.Surface | None = None
        self._emissive_size = (0, 0)
        self._emissive_full: pygame.Surface | None = None
        self._emissive_full_size = (0, 0)
        # Где в этом кадре стоят обесточенные постройки и лампы: значки «нет
        # питания» рисуются по этому списку последним слоем мира.
        self._marks: list[tuple[float, float]] = []

        self.anim_time = 0.0
        self.deposits_drawn = 0
        self.tiles_drawn = 0
        self.glows_drawn = 0
        self.relics_drawn = 0
        self.buildings_drawn = 0
        self.drops_drawn = 0

    # ------------------------------------------------------------- мир
    def draw_world(self, target: pygame.Surface, sim: Simulation, camera: Camera,
                   show_lit_layer: bool = False, dt: float = config.TICK) -> None:
        """Кадр целиком. Камера сдвигается на время тряски и возвращается назад."""
        self.anim_time += dt
        self.effects.update(sim, dt)
        shake = self.effects.shake
        offset = (0.0, 0.0)
        if shake > 0.01:
            offset = _shake_offset(shake, self.anim_time)
            camera.x += offset[0] / camera.scale
            camera.y += offset[1] / camera.scale
        try:
            self._draw_scene(target, sim, camera, show_lit_layer)
        finally:
            # Камера — состояние приложения, а не рендера: сдвиг обязан вернуться,
            # иначе тряска уползла бы в логику (курсор считается от камеры).
            if shake > 0.01:
                camera.x -= offset[0] / camera.scale
                camera.y -= offset[1] / camera.scale

    def _draw_scene(self, target: pygame.Surface, sim: Simulation, camera: Camera,
                    show_lit_layer: bool) -> None:
        self.glows_drawn = 0
        self._marks.clear()
        if self.world.version != self._world_version:
            # Обвал гор (M8): рельеф изменился, чанки рисуются заново.
            self._world_version = self.world.version
            self.chunks.invalidate_all()
        self.chunks.draw(target, camera)
        self._draw_water_shimmer(target, camera)
        self._draw_infection(target, camera, sim)
        self._draw_deposits(target, camera, sim)
        self._draw_relics(target, camera, sim)
        self._draw_buildings(target, camera, sim)
        self._draw_lamps(target, camera, sim)
        self._draw_bases(target, camera, sim)
        self._draw_drops(target, camera, sim)
        self._draw_building_bars(target, camera, sim)
        self._draw_enemies(target, camera, sim)
        self._draw_drone(target, sim, camera)
        self._draw_bars(target, sim, camera)
        # Тьма — поверх мира, но под интерфейсом.
        self._draw_darkness(target, sim, camera)
        # Свет и неон — уже поверх тьмы: иначе ночью их не было бы видно.
        self._draw_glows(target, sim, camera)
        self._draw_emissive(target, sim, camera)
        # Значки состояния — сигнал игроку, а не часть мира: их не закрывает
        # ни соседняя постройка, ни ночь.
        self._draw_status_marks(target, camera)
        self.particles.draw(target, camera)
        self.postfx.draw(target, sim, sim.darkness)
        if show_lit_layer:
            self.draw_lit_layer(target, sim, camera)

    # --------------------------------------------------- вода (анимация)
    def _draw_water_shimmer(self, target: pygame.Surface, camera: Camera) -> None:
        """Медленный блик по воде (plan/09 §4).

        Чанк с водой кэширован, и анимировать его нельзя — поэтому блик идёт
        отдельным потайловым слоем: одна маленькая поверхность на кадр, как у
        тьмы. Яркость — от синуса фазы и координат тайла: вода «дышит», а не
        мигает, и волна идёт по карте, а не по экрану.
        """
        if not quality.glow_enabled():
            return
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        ix0, iy0 = max(x0, 0), max(y0, 0)
        ix1, iy1 = min(x1, self.world.width - 1), min(y1, self.world.height - 1)
        if ix0 > ix1 or iy0 > iy1:
            return
        # Срез numpy — это вид, а не копия: проверка «есть ли вода в кадре»
        # бесплатна, и в кадре без воды весь блик пропускается.
        block = self.world.terrain[iy0:iy1 + 1, ix0:ix1 + 1]
        is_water = block == int(Terrain.WATER)
        if not is_water.any():
            return
        h, w = y1 - y0 + 1, x1 - x0 + 1
        yy = np.arange(iy0, iy1 + 1, dtype=np.float32)[:, None]
        xx = np.arange(ix0, ix1 + 1, dtype=np.float32)[None, :]
        phase = np.sin(self.anim_time * 1.1 + xx * 0.35 + yy * 0.21)
        strength = np.clip((phase * 0.5 + 0.5) * is_water, 0.0, 1.0)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = theme.WATER_GLINT
        alpha = np.zeros((h, w), dtype=np.float32)
        alpha[iy0 - y0:iy1 - y0 + 1, ix0 - x0:ix1 - x0 + 1] = strength * WATER_GLINT_ALPHA
        rgba[..., 3] = alpha.astype(np.uint8)
        self.water.draw(target, camera, x0, y0, rgba, smooth=True)

    # ------------------------------------------------- заражение и враги (M8)
    def _draw_infection(self, target: pygame.Surface, camera: Camera,
                        sim: Simulation) -> None:
        """Слой заражения: живое — пульсирующий пурпур, погасшее — бурый след."""
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        ix0, iy0 = max(x0, 0), max(y0, 0)
        ix1, iy1 = min(x1, self.world.width - 1), min(y1, self.world.height - 1)
        if ix0 > ix1 or iy0 > iy1:
            return
        infected = sim.bases.infected[iy0:iy1 + 1, ix0:ix1 + 1]
        if not infected.any():
            return
        active = sim.bases.active[iy0:iy1 + 1, ix0:ix1 + 1]
        h, w = y1 - y0 + 1, x1 - x0 + 1
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        block = rgba[iy0 - y0:iy1 - y0 + 1, ix0 - x0:ix1 - x0 + 1]
        pulse = 0.85 + 0.15 * math.sin(self.anim_time * 2.0)
        dead = infected & ~active
        block[dead, :3] = theme.INFECT_DARK
        block[dead, 3] = INFECT_DEAD_ALPHA
        block[active, :3] = theme.INFECT_MID
        block[active, 3] = int(INFECT_ACTIVE_ALPHA * pulse)
        self.infection.draw(target, camera, x0, y0, rgba, smooth=True)

    def _draw_bases(self, target: pygame.Surface, camera: Camera,
                    sim: Simulation) -> None:
        """Ростки и инкубаторы: органика из `sprites/organic.py` и полоска здоровья."""
        x0, y0, x1, y1 = camera.visible_tiles(margin=2)
        tile = config.TILE * camera.zoom
        phase = self.anim_time * 2.4
        for sprout in sim.bases.live:
            for inc in sprout.incubators:
                if x0 <= inc.x <= x1 and y0 <= inc.y <= y1:
                    self._draw_creature(target, camera, "incubator", inc.x + 0.5,
                                        inc.y + 0.5, tile * INCUBATOR_SIZE, phase + inc.id)
                    self._hp_bar(target, camera, inc, tile * INCUBATOR_SIZE)
            if x0 <= sprout.x <= x1 and y0 <= sprout.y <= y1:
                self._draw_creature(target, camera, "sprout", sprout.x + 0.5,
                                    sprout.y + 0.5, tile * SPROUT_SIZE, phase)
                self._hp_bar(target, camera, sprout, tile * SPROUT_SIZE)

    def _hp_bar(self, target, camera: Camera, obj, size: float) -> None:
        if obj.hp < obj.max_hp:
            self._progress_bar(target, camera, obj.x + 0.5, obj.y + 0.5,
                               obj.hp / obj.max_hp, theme.FOE_BLOOD, dy=int(size * 0.5) + 4)

    def _draw_creature(self, target, camera: Camera, kind: str, wx: float, wy: float,
                       size: float, phase: float) -> None:
        sprite = self.sprites.creature(kind, max(8, int(size)), phase)
        sx, sy = camera.world_to_screen(wx, wy)
        target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))

    def _draw_enemies(self, target: pygame.Surface, camera: Camera,
                      sim: Simulation) -> None:
        """Враги и луч оружия дрона по цели (M8)."""
        x0, y0, x1, y1 = camera.visible_tiles(margin=2)
        tile = config.TILE * camera.zoom
        for enemy in sim.enemies.enemies:
            if not (x0 <= enemy.x <= x1 + 1 and y0 <= enemy.y <= y1 + 1):
                continue
            etype = ENEMY_TYPES[enemy.kind]
            size = tile * etype.size
            self._draw_creature(target, camera, etype.sprite, enemy.x, enemy.y, size,
                                self.anim_time * 3.0 + enemy.id)
            if enemy.hp < enemy.max_hp:
                self._progress_bar(target, camera, enemy.x, enemy.y,
                                   enemy.hp / enemy.max_hp, theme.FOE_BLOOD,
                                   dy=int(size * 0.5) + 2)
        action = sim.drone.action
        if action.kind == "attack" and action.target_world_pos is not None:
            start = camera.world_to_screen(sim.drone.x, sim.drone.y)
            end = camera.world_to_screen(*action.target_world_pos)
            pygame.draw.line(target, theme.laser_color(action.kind), start, end,
                             max(2, int(tile * 0.08)))

    # ------------------------------------------------------------- залежи
    def _draw_deposits(self, target: pygame.Surface, camera: Camera,
                       sim: Simulation) -> None:
        """Залежь — кластер крупных кристаллов того же облика, что и ресурс.

        Огранка берётся из генератора по id ресурса (`render/sprites/gems.py`),
        поэтому залежь узнаётся по силуэту, а не по подписи. Истощённая залежь
        остаётся серым камнем той же формы: «здесь пусто, но это был ванадий».
        """
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        zoom = camera.zoom
        size = max(8, int(config.TILE * zoom * DEPOSIT_BASE_SIZE * 2.0))
        self.deposits_drawn = 0
        target_deposit = sim.mine_target
        for deposit in self.world.deposits:
            if not (x0 <= deposit.x <= x1 and y0 <= deposit.y <= y1):
                continue
            self.deposits_drawn += 1
            sx, sy = camera.world_to_screen(deposit.x + 0.5, deposit.y + 0.5)
            if deposit.depleted:
                ghost = self.sprites.gem(deposit.resource_id, size * 0.8,
                                         theme.DEPOSIT_DEPLETED)
                target.blit(ghost, ghost.get_rect(center=(int(sx), int(sy))))
                continue
            item = self.world.items[deposit.resource_id]
            # Залежь «тает» вместе с запасом: по размеру видно, много ли осталось.
            frac = max(0.0, min(1.0, deposit.amount / config.DEPOSIT_AMOUNT))
            gem = self.sprites.gem(deposit.resource_id,
                                   max(8, int(size * (0.72 + 0.28 * frac))),
                                   item.color, glow=bool(item.props))
            target.blit(gem, gem.get_rect(center=(int(sx), int(sy))))
            radius = size * 0.5
            if deposit is target_deposit:
                pygame.draw.circle(target, theme.SELECT_RING, (int(sx), int(sy)),
                                   int(radius * 1.05), max(1, int(2 * zoom)))
            elif deposit is sim.cursor_deposit:
                # Курсор на залежи, но добыча не идёт: далеко или кнопка отпущена.
                pygame.draw.circle(target, theme.HOVER_RING, (int(sx), int(sy)),
                                   int(radius * 1.02), max(1, int(zoom)))

    # --------------------------------------------------------- реликвии
    def _draw_relics(self, target: pygame.Surface, camera: Camera,
                     sim: Simulation) -> None:
        """Реликвии (plan/02): изученная гаснет, неизученная зовёт кольцом."""
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        zoom = camera.zoom
        size = max(8, int(config.TILE * zoom * RELIC_BASE_SIZE * 2.0))
        self.relics_drawn = 0
        for relic in sim.relics:
            if not (x0 <= relic.x <= x1 and y0 <= relic.y <= y1):
                continue
            self.relics_drawn += 1
            rdef = relic_def(relic.amplified)
            sx, sy = camera.world_to_screen(relic.x + 0.5, relic.y + 0.5)
            color = theme.RELIC_STUDIED if relic.studied else rdef.color
            shape = self.sprites.gem(f"relic:{relic.amplified}", size, color,
                                     glow=not relic.studied)
            target.blit(shape, shape.get_rect(center=(int(sx), int(sy))))
            if relic.studied:
                continue
            pygame.draw.circle(target, theme.RELIC_RING, (int(sx), int(sy)),
                               int(size * 0.62), max(1, int(zoom)))
            mark = self.icons.mark_icon(rdef.mark, color)
            target.blit(mark, mark.get_rect(midbottom=(int(sx), int(sy) - size * 0.55)))

    # --------------------------------------------------------- постройки
    def _draw_buildings(self, target: pygame.Surface, camera: Camera,
                        sim: Simulation) -> None:
        """Постройки (M4): форма и цвет — из данных здания (`data/buildings.py`).

        Поэтому новое здание появляется на карте само, без правок здесь.
        Состояние видно без открытия окон: обесточенное здание тускнеет и
        получает значок, у конвейера бегут шевроны, у бура крутится ротор,
        а над работающим зданием идёт полоса прогресса.
        """
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        zoom = camera.zoom
        size = max(8, int(config.TILE * zoom * BUILDING_SIZE))
        self.buildings_drawn = 0
        visible = [b for b in sim.buildings.buildings
                   if x0 <= b.x <= x1 and y0 <= b.y <= y1]
        # Провода — первым проходом: кабель доходит до края тайла, и корпус
        # соседнего здания должен лечь поверх его конца, а не под него.
        for building in visible:
            if building_def(building.kind).conducts:
                self._draw_wire(target, camera, sim, building)
        for building in visible:
            self.buildings_drawn += 1
            bdef = building_def(building.kind)
            if bdef.conducts:
                continue
            sx, sy = camera.world_to_screen(building.x + 0.5, building.y + 0.5)
            state = buildings.STATE_ON
            if bdef.needs_power and not building.powered:
                state = buildings.STATE_OFF
                self._marks.append((sx, sy))
            sprite = self.sprites.building(building.kind, state, size)
            target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))
            self._draw_building_live(target, building, sx, sy, size, bdef)

    def _draw_building_bars(self, target: pygame.Surface, camera: Camera,
                            sim: Simulation) -> None:
        """Полосы прогресса построек — после всех построек, ламп и куч.

        Полоса висит под зданием, то есть на соседнем тайле: нарисованная
        вместе со зданием, она уходила под постройку, стоящую ниже.
        """
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        size = max(8, int(config.TILE * camera.zoom * BUILDING_SIZE))
        for building in sim.buildings.buildings:
            if not (x0 <= building.x <= x1 and y0 <= building.y <= y1):
                continue
            hurt = sim.structure_damage(building.x, building.y)
            if hurt > 0.0:
                self._progress_bar(target, camera, building.x + 0.5, building.y + 0.5,
                                   1.0 - hurt, theme.FOE_BLOOD, dy=-int(size * 0.5) - 8)
            progress = building.bar_progress
            if progress > 0.0:
                self._progress_bar(target, camera, building.x + 0.5,
                                   building.y + 0.5, progress, theme.SCAN_BAR_FG,
                                   dy=int(size * 0.5) + 6)

    def _draw_status_marks(self, target: pygame.Surface, camera: Camera) -> None:
        """Значки «нет питания» — последним слоем мира, поверх тьмы и свечения.

        Пока значок был частью спрайта здания, его закрывали соседи, лампы и
        кучи предметов, нарисованные позже, а ночью — ещё и тьма. Значок мигает
        (plan/09 §5): яркий и тусклый варианты чередуются, оба лежат в кэше.
        """
        if not self._marks:
            return
        tile = camera.scale
        size = max(8, int(tile * NO_POWER_SIZE))
        bright = int(self.anim_time * NO_POWER_BLINK_HZ * 2.0) % 2 == 0
        mark = self.sprites.no_power(size, bright)
        ox, oy = NO_POWER_OFFSET
        for sx, sy in self._marks:
            target.blit(mark, mark.get_rect(center=(int(sx + ox * tile),
                                                    int(sy + oy * tile))))
        self._marks.clear()

    def _draw_building_live(self, target: pygame.Surface, building, sx: float,
                            sy: float, size: int, bdef) -> None:
        """Живая часть здания: вращение, лента, стрела, заполнение.

        Отдельно от спрайта, потому что зависит от `anim_time` и от состояния,
        которое меняется каждый кадр. Всё берётся из кэшей (ротор, шеврон,
        стрела) или рисуется примитивами — поверхностей в кадре не появляется.
        """
        role = buildings.ROLE_COLOR.get(building.kind, theme.NEON_CYAN)
        powered = building.powered or not bdef.needs_power
        kind = building.kind
        if kind == DRILL:
            # Ротор крутится, пока бур работает; при простое стоит и приглушён.
            working = powered and building.work_progress > 0.0
            angle = self.anim_time * 6.0 if working else 0.0
            rotor = self.sprites.rotor(max(8, int(size * 0.62)), angle, role, working)
            target.blit(rotor, rotor.get_rect(center=(int(sx), int(sy))))
        elif kind == CONVEYOR:
            self._draw_belt(target, building, sx, sy, size)
        elif kind == LAB:
            # Линия-сканер вращается, пока в слотах есть работа.
            if powered and building.busy:
                buildings.draw_scanner(target, sx, sy, size, self.anim_time * 2.2, role)
        elif kind == ASSEMBLER and powered:
            frac = building.work_progress
            buildings.draw_arm(target, sx, sy, size,
                               self.anim_time * 1.4 + frac * math.tau, role, frac)
        elif kind == STORAGE:
            self._draw_storage_fill(target, building, sx, sy, size)
        elif kind == GENERATOR:
            self._draw_generator_core(target, building, sx, sy, size, role)

    def _draw_belt(self, target: pygame.Surface, building, sx: float, sy: float,
                   size: int) -> None:
        """Лента конвейера: бегущие шевроны и то, что сейчас едет.

        Смещение берётся от `anim_time` — он не растёт на паузе, и лента
        замирает вместе с симуляцией, как и должно. У обесточенного конвейера
        шевроны стоят и приглушены: это и есть «нет питания» без надписи.
        """
        direction = building.direction
        angle = math.atan2(direction[1], direction[0])
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        running = building.powered or not building_def(CONVEYOR).needs_power
        offset = (self.anim_time * CHEVRON_SPEED) % 1.0 if running else 0.35
        chevron = self.sprites.chevron(max(6, int(size * 0.34)), direction,
                                       theme.NEON_CYAN if running else theme.POWER_OFF)
        for i in range(CHEVRONS):
            t = (i / CHEVRONS + offset) % 1.0
            along = (t - 0.5) * size * CHEVRON_SPAN
            target.blit(chevron, chevron.get_rect(center=(
                int(sx + along * cos_a), int(sy + along * sin_a))))
        # Что везёт лента: иначе «работает ли линия» приходится выяснять окном.
        belt = building.container(building_def(CONVEYOR).take_id)
        stack = belt.slot(0) if belt is not None else None
        if stack is None:
            return
        item = self.world.items.get(stack.item_id)
        if item is None:
            return
        gem = self.sprites.gem(stack.item_id, max(6, int(size * 0.30)), item.color)
        target.blit(gem, gem.get_rect(center=(int(sx), int(sy))))

    @staticmethod
    def _draw_storage_fill(target: pygame.Surface, building, sx: float, sy: float,
                           size: int) -> None:
        """Индикатор заполнения склада: полоска у нижнего края панели."""
        container = building.container(building_def(STORAGE).accept_id)
        if container is None or container.size <= 0:
            return
        w = int(size * FILL_BAR_WIDTH)
        rect = pygame.Rect(int(sx - w / 2), int(sy + size * 0.30), w,
                           max(2, int(size * 0.08)))
        buildings.draw_fill_bar(target, rect, container.used_slots / container.size,
                                theme.NEON_CYAN)

    def _draw_generator_core(self, target: pygame.Surface, building, sx: float,
                             sy: float, size: int, role) -> None:
        """Пульсирующее ядро генератора (plan/09 §5).

        Пульс идёт от топки: пока генератор жжёт топливо, ядро «дышит». Яркость
        квантована — иначе ореол пересобирался бы каждый кадр.
        """
        burning = building.burn > 0.0
        beat = _breath(self.anim_time * CORE_BREATH_HZ, _phase(building.id))
        radius = size * (0.13 + 0.03 * beat)
        color = role if burning else theme.POWER_OFF
        factory.circle(target, theme.darken(color, 0.5), sx, sy, radius * 1.35)
        factory.circle(target, color, sx, sy, radius)
        if burning and quality.glow_enabled():
            glow = self.sprites.factory.halo(
                max(6, int(size * 0.55)), theme.NEON_AMBER, 0.0,
                _quantize(0.45 + 0.25 * beat, BREATH_STEPS))
            self.sprites.factory.blit_add(target, glow, (sx, sy))

    def _draw_wire(self, target: pygame.Surface, camera: Camera, sim: Simulation,
                   building) -> None:
        """Провод: спрайт по маске соседей по сети и бегущие по кабелю точки.

        Маска — те же четыре стороны, по которым `sim/power.py` собирает сеть,
        поэтому на картинке провод соединён ровно с тем, с чем соединён в
        симуляции. Кабель каждого провода доходит до края тайла: два соседа
        сходятся в одну сплошную линию. «Живой» провод — тот, чья сеть сейчас
        запитана (`building.powered`), а не «где-нибудь на карте есть энергия».
        """
        x, y = building.x, building.y
        mask = tuple(sim.power_at(x + dx, y + dy) for dx, dy in DIRECTIONS)
        live = building.powered
        size = max(8, int(config.TILE * camera.zoom * BUILDING_SIZE))
        sx, sy = camera.world_to_screen(x + 0.5, y + 0.5)
        sprite = self.sprites.wire(size, mask, live)
        target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))
        # Бегущая точка: по ней видно, что сеть живая, а не просто нарисована.
        # Фаза — от мировой координаты, поэтому на прямом участке точка
        # переходит с плитки на плитку, не прыгая обратно к центру.
        if not (live and quality.glow_enabled()):
            return
        t = (self.anim_time * WIRE_FLOW_SPEED) % 1.0
        scale = camera.scale
        radius = max(1.0, 0.06 * scale)
        east, south, west, north = mask
        if (t >= 0.5 and east) or (t < 0.5 and west):
            factory.circle(target, theme.WIRE_FLOW, sx + (t - 0.5) * scale, sy, radius)
        if (t >= 0.5 and south) or (t < 0.5 and north):
            factory.circle(target, theme.WIRE_FLOW, sx, sy + (t - 0.5) * scale, radius)

    # ------------------------------------------------------------ лампы
    def _draw_lamps(self, target: pygame.Surface, camera: Camera,
                    sim: Simulation) -> None:
        """Лампы: спрайт, ореол и контур радиуса света.

        Контур — единственный способ увидеть, какой тайл накрыт лампой: счётчик
        «% освещено» отвечает на это только в сумме по карте.
        """
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        scale = camera.scale
        size = max(8, int(config.TILE * camera.zoom))
        for lamp in sim.light.lamps:
            if not (x0 <= lamp.x <= x1 and y0 <= lamp.y <= y1):
                continue
            sx, sy = camera.world_to_screen(lamp.x + 0.5, lamp.y + 0.5)
            if lamp.on:
                pygame.draw.circle(target, theme.LAMP_RADIUS, (int(sx), int(sy)),
                                   int(lamp.radius * scale), 1)
            sprite = self.sprites.building(
                "lamp", buildings.STATE_ON if lamp.on else buildings.STATE_OFF, size)
            target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))
            if not lamp.on:
                self._marks.append((sx, sy))
            if lamp.on and quality.glow_enabled():
                # Лёгкое мерцание: лампа не светодиод, она «дышит» (plan/09 §5).
                beat = _breath(self.anim_time * LAMP_BREATH_HZ, _phase(lamp.id))
                glow = self.sprites.factory.halo(
                    max(8, int(size * 0.85)), theme.NEON_AMBER, 0.0,
                    _quantize(0.35 + 0.20 * beat, BREATH_STEPS))
                self.sprites.factory.blit_add(target, glow, (sx, sy))

    # -------------------------------------------------- брошенные предметы
    def _draw_drops(self, target: pygame.Surface, camera: Camera,
                    sim: Simulation) -> None:
        """Брошенные предметы (M3.5): значок, число и мигание у исчезающих.

        Идём по словарю «тайл → стеки», а не по всем тайлам кадра: в словаре
        лежат только тайлы, где что-то есть, поэтому двести куч в другом конце
        карты не стоят ничего, а лишних объектов в кадре не создаётся
        (plan/07). Мигание считается от возраста предмета, а не от таймера
        рендера: картинка совпадает с состоянием симуляции.
        """
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        zoom = camera.zoom
        icon_size = max(8, int(config.TILE * zoom * DROP_ICON_TILE))
        cage = sim.cursor_tile
        self.drops_drawn = 0
        for (tx, ty), stack in sim.drops.tiles.items():
            if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                continue
            sx, sy = camera.world_to_screen(tx + 0.5, ty + 0.5)
            for i, drop in enumerate(stack):
                if drop.expiring and int(drop.age * DROP_BLINK_HZ) % 2:
                    continue               # предмет мигает перед исчезновением
                self.drops_drawn += 1
                ox, oy = DROP_OFFSETS[min(i, len(DROP_OFFSETS) - 1)]
                cx = int(sx + ox * zoom * config.TILE)
                cy = int(sy + oy * zoom * config.TILE)
                if cage == (tx, ty):
                    # Курсор на предмете: он подберётся сам, но игрок должен
                    # видеть, что именно лежит под дроном.
                    pygame.draw.circle(target, theme.DROP_RING, (cx, cy),
                                       int(icon_size * 0.7), max(1, int(zoom)))
                icon = self.icons.item_icon_scaled(drop.item_id, icon_size)
                target.blit(icon, icon.get_rect(center=(cx, cy)))
                if drop.count > 1:
                    label = self.icons.mark_icon(f"×{drop.count}", theme.UI_TEXT)
                    target.blit(label, label.get_rect(
                        midtop=(cx, cy + int(icon_size * 0.55))))

    # -------------------------------------------------------------- тьма
    def _draw_darkness(self, target: pygame.Surface, sim: Simulation,
                       camera: Camera) -> None:
        """Ночное затемнение по слою света. Днём не рисуется вовсе.

        Тьма **плавная**: яркость света считается на тайл (`_light_field`), и
        альфа тьмы падает вместе с ней. Пока тайл был либо «освещён», либо
        «тёмный», пятно света обрезалось ровным кругом по границе тайлов, а
        несколько ламп рядом не складывались в более светлое место.
        """
        darkness = sim.darkness
        if darkness <= 0.0:
            return
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        h, w = y1 - y0 + 1, x1 - x0 + 1
        base = float(darkness * config.NIGHT_DARKNESS_ALPHA)
        light = self._light_field(sim, x0, y0, w, h)
        # Вне карты света нет: там тьма в полную силу. (Раньше обнулялась как
        # раз область **внутри** карты — лампы не открывали тьму вовсе, и всё
        # освещение держалось на ярком пятне поверх неё.)
        ix0, iy0 = max(x0, 0), max(y0, 0)
        ix1, iy1 = min(x1, self.world.width - 1), min(y1, self.world.height - 1)
        inside = np.zeros((h, w), dtype=bool)
        if ix0 <= ix1 and iy0 <= iy1:
            inside[iy0 - y0:iy1 - y0 + 1, ix0 - x0:ix1 - x0 + 1] = True
        light[~inside] = 0.0
        alpha = (base * (1.0 - light)).astype(np.uint8)
        alpha = self._drone_light_hole(alpha, x0, y0, sim.drone)
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = theme.DARKNESS
        rgba[..., 3] = alpha
        self.overlay.draw(target, camera, x0, y0, rgba, smooth=True)

    @staticmethod
    def _light_field(sim: Simulation, x0: int, y0: int, w: int, h: int) -> np.ndarray:
        """Яркость света на тайл видимой области, 0..1.

        Берётся **максимум** по лампам, а не сумма: тайл либо освещён, либо
        нет, и полоса лампы вплотную не должна светить в пять раз ярче
        одиночной — иначе центр такой полосы выбеливается, и на нём пропадает
        всё, что стоит. Максимум совпадает и со смыслом `LightSystem.lit`
        («накрыт хоть одной лампой»), только с плавным краем.
        """
        light = np.zeros((h, w), dtype=np.float32)
        if w <= 0 or h <= 0:
            return light
        yy = np.arange(y0, y0 + h, dtype=np.float32)[:, None] + 0.5
        xx = np.arange(x0, x0 + w, dtype=np.float32)[None, :] + 0.5
        for lamp in sim.light.lamps:
            if not lamp.on:
                continue
            radius = max(1e-6, float(lamp.radius))
            cx, cy = lamp.x + 0.5, lamp.y + 0.5
            if not (x0 - radius <= cx <= x0 + w + radius
                    and y0 - radius <= cy <= y0 + h + radius):
                continue
            dist = np.hypot(xx - cx, yy - cy) / radius
            np.maximum(light, _falloff(dist, LAMP_GLOW_INNER), out=light)
        return light

    @staticmethod
    def _drone_light_hole(alpha: np.ndarray, x0: int, y0: int,
                          drone: Drone) -> np.ndarray:
        """Вырезает в тьме пятно собственного света дрона.

        Света дрона мало, для победы он не считается (plan/01), но видеть себя
        в темноте игрок должен.
        """
        radius = config.DRONE_LIGHT_RADIUS
        h, w = alpha.shape
        yy = np.arange(y0, y0 + h, dtype=np.float32)[:, None] + 0.5
        xx = np.arange(x0, x0 + w, dtype=np.float32)[None, :] + 0.5
        dist = np.sqrt((xx - drone.x) ** 2 + (yy - drone.y) ** 2)
        inner = radius * DRONE_LIGHT_INNER
        k = np.clip((dist - inner) / max(1e-6, radius - inner), 0.0, 1.0)
        return (alpha * k).astype(np.uint8)

    def _draw_glows(self, target: pygame.Surface, sim: Simulation,
                    camera: Camera) -> None:
        """Пятна света поверх тьмы: тёплые от ламп, холодное от дрона.

        Тьма умеет только затемнять, а свет должен подсвечивать — поэтому пятна
        рисуются отдельным аддитивным проходом. Цвет — единственное, чем свет
        дрона (синеватый) отличается от света лампы (жёлтый), и путать их нельзя.
        Пятно ровно одно на источник: два (ядро и кромка) удваивали бы и работу,
        и кэш, а разницу даёт оттенок внутри самого пятна.
        """
        level = _glow_level(sim.darkness)
        if level <= 0.0 or not quality.glow_enabled():
            return
        x0, y0, x1, y1 = camera.visible_tiles(margin=2)
        for lamp in sim.light.lamps:
            if not lamp.on or not (x0 <= lamp.x <= x1 and y0 <= lamp.y <= y1):
                continue
            self._blit_glow(target, camera, lamp.x + 0.5, lamp.y + 0.5, lamp.radius,
                            "lamp", LAMP_GLOW_PEAK * level)
        drone = sim.drone
        if x0 <= drone.x <= x1 and y0 <= drone.y <= y1:
            self._blit_glow(target, camera, drone.x, drone.y,
                            config.DRONE_LIGHT_RADIUS, "drone",
                            DRONE_GLOW_PEAK * level)

    def _blit_glow(self, target: pygame.Surface, camera: Camera, wx: float,
                   wy: float, radius: float, kind: str, peak: float) -> None:
        """Одно пятно света. Поверхность берётся из кэша и блитится по максимуму.

        Максимум, а не сложение: пятна соседних ламп перекрываются, и при
        `BLEND_RGB_ADD` полоса ламп вплотную выбеливала кадр до чистого белого.
        Максимум держит яркость пятна на заданном пике и не затирает то, что
        под светом светлее его. Пик низкий: пятно лишь подкрашивает светом
        уже открытую тьмой картинку и не съедает тёмные контуры.
        """
        radius_px = max(2, int(radius * camera.scale))
        key = (kind, radius_px, int(peak))
        glow = self._glow_cache.get(key)
        if glow is None:
            glow = _make_glow(radius_px, kind, int(peak))
            self._glow_cache[key] = glow
        sx, sy = camera.world_to_screen(wx, wy)
        half = glow.get_width() // 2
        # Без альфы: за пределами круга поверхность чёрная, то есть ноль.
        target.blit(glow, (int(sx) - half, int(sy) - half),
                    special_flags=pygame.BLEND_RGB_MAX)
        self.glows_drawn += 1

    # ----------------------------------------------------------- emissive
    def _draw_emissive(self, target: pygame.Surface, sim: Simulation,
                       camera: Camera) -> None:
        """Свечение поверх тьмы: неон построек не гаснет в темноте (plan/09 §3.4).

        Светящиеся точки собираются в список, а дальше всё зависит от уровня
        графики: на `high` они рисуются на поверхности в 4× меньшего размера и
        растягиваются `smoothscale` обратно — это дешёвое размытие, из-за
        которого свет «расплывается» вокруг здания, а не выглядит пикселем. На
        `medium` размытия нет, и те же точки получают плоский ореол.
        """
        level = _glow_level(sim.darkness)
        if level <= 0.0 or not quality.glow_enabled():
            return
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        points: list[tuple[float, float, tuple[int, int, int], float, float]] = []
        for building in sim.buildings.buildings:
            if not (x0 <= building.x <= x1 and y0 <= building.y <= y1):
                continue
            bdef = building_def(building.kind)
            if not (building.powered or not bdef.needs_power):
                continue                    # обесточенное не светится
            if bdef.conducts:
                continue                    # провод светится линией, а не пятном
            color = buildings.ROLE_COLOR.get(building.kind, theme.NEON_CYAN)
            sx, sy = camera.world_to_screen(building.x + 0.5, building.y + 0.5)
            points.append((sx, sy, color, BUILDING_BLOOM_TILES, EMISSIVE_BUILDING))
        for lamp in sim.light.lamps:
            if not lamp.on or not (x0 <= lamp.x <= x1 and y0 <= lamp.y <= y1):
                continue
            sx, sy = camera.world_to_screen(lamp.x + 0.5, lamp.y + 0.5)
            points.append((sx, sy, theme.LAMP_GLOW, LAMP_BLOOM_TILES, EMISSIVE_LAMP))
        if not points:
            return
        if quality.blur_enabled():
            self._emissive_blur(target, camera, points, level)
        else:
            self._emissive_flat(target, camera, points, level)

    def _emissive_blur(self, target: pygame.Surface, camera: Camera, points, level) -> None:
        """Размытие свечения: рисуем мелко, растягиваем обратно, складываем."""
        full_w, full_h = target.get_size()
        k = max(2, int(config.EMISSIVE_DOWNSCALE))
        small = self._emissive_source(max(1, full_w // k), max(1, full_h // k))
        small.fill((0, 0, 0))
        px_per_tile = camera.scale / k
        for sx, sy, color, tiles, share in points:
            # В маленький буфер кладётся уже мягкий ореол, а не круги: залитые
            # круги в 4× меньшем буфере после растягивания читались ступенчатыми
            # «пикселями» поверх построек.
            glow = self.sprites.factory.halo(
                max(2, int(tiles * px_per_tile)), color, 0.0,
                _quantize(level * share, BREATH_STEPS * 2))
            self.sprites.factory.blit_add(small, glow, (sx / k, sy / k))
        dest = self._emissive_full_surface(full_w, full_h)
        pygame.transform.smoothscale(small, (full_w, full_h), dest)
        target.blit(dest, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

    def _emissive_flat(self, target: pygame.Surface, camera: Camera, points, level) -> None:
        """Свечение без размытия: плоский ореол на каждую светящуюся точку."""
        for sx, sy, color, tiles, share in points:
            glow = self.sprites.factory.halo(
                max(6, int(tiles * camera.scale)), color, 0.0,
                _quantize(level * share, BREATH_STEPS * 2))
            self.sprites.factory.blit_add(target, glow, (sx, sy))

    def _emissive_source(self, w: int, h: int) -> pygame.Surface:
        if self._emissive is None or self._emissive_size != (w, h):
            # Без альфы: чёрный фон — это ноль для BLEND_RGB_ADD.
            self._emissive = pygame.Surface((w, h))
            self._emissive_size = (w, h)
        return self._emissive

    def _emissive_full_surface(self, w: int, h: int) -> pygame.Surface:
        if self._emissive_full is None or self._emissive_full_size != (w, h):
            self._emissive_full = pygame.Surface((w, h))
            self._emissive_full_size = (w, h)
        return self._emissive_full

    # ------------------------------------------------------ отладочный слой
    def draw_lit_layer(self, target: pygame.Surface, sim: Simulation,
                       camera: Camera) -> None:
        """Отладочный слой света (F2): какие тайлы симуляция считает освещёнными."""
        x0, y0, x1, y1 = camera.visible_tiles(margin=1)
        h, w = y1 - y0 + 1, x1 - x0 + 1
        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = theme.DARK_TILE
        rgba[..., 3] = DEBUG_LAYER_ALPHA
        ix0, iy0 = max(x0, 0), max(y0, 0)
        ix1, iy1 = min(x1, self.world.width - 1), min(y1, self.world.height - 1)
        if ix0 <= ix1 and iy0 <= iy1:
            lit = sim.light.lit[iy0:iy1 + 1, ix0:ix1 + 1]
            block = rgba[iy0 - y0:iy1 - y0 + 1, ix0 - x0:ix1 - x0 + 1]
            block[..., 0][lit] = theme.LIGHT_ACCENT[0]
            block[..., 1][lit] = theme.LIGHT_ACCENT[1]
            block[..., 2][lit] = theme.LIGHT_ACCENT[2]
        self.overlay.draw(target, camera, x0, y0, rgba, smooth=False)

    # ------------------------------------------------------------- дрон
    def _draw_drone(self, target: pygame.Surface, sim: Simulation,
                    camera: Camera) -> None:
        """Дрон: тень со смещением, корпус-дельта и огонь двигателей.

        Покачивание и мягкая тень дают «высоту»: дрон парит над картой, и это
        видно по смещению тени, без подписи (plan/09 §6).
        """
        drone = sim.drone
        scale = camera.scale
        bob = math.sin(self.anim_time * DRONE_BOB_HZ) * DRONE_BOB
        sx, sy = camera.world_to_screen(drone.x, drone.y + bob)
        # scale — уже пикселей на тайл, поэтому тайл в нём не повторяется.
        size = max(10, int(scale * DRONE_SIZE))

        shadow = self.sprites.drone_shadow(size)
        ox, oy = DRONE_SHADOW_OFFSET
        target.blit(shadow, shadow.get_rect(center=(
            int(sx + ox * scale), int(sy + oy * scale))))
        # Радиус света дрона — виден, но для победы не считается (plan/01).
        pygame.draw.circle(target, theme.DRONE_RADIUS, (int(sx), int(sy)),
                           int(config.DRONE_LIGHT_RADIUS * scale), 1)
        sprite = self.sprites.drone(size, drone.facing)
        target.blit(sprite, sprite.get_rect(center=(int(sx), int(sy))))
        if not quality.glow_enabled():
            return
        # Сопла светятся неровно: тяга «дышит» по той же лестнице яркостей,
        # поэтому ореолов в кэше конечное число.
        thrust = _quantize(0.5 + 0.5 * math.sin(self.anim_time * 7.0), BREATH_STEPS)
        engine = self.sprites.factory.halo(max(6, int(size * 0.34)),
                                           theme.NEON_ICE, 0.0, 0.25 + 0.45 * thrust)
        cos_a, sin_a = math.cos(drone.facing), math.sin(drone.facing)
        for side in (-1.0, 1.0):
            ex = sx - cos_a * size * 0.20 - sin_a * size * 0.22 * side
            ey = sy - sin_a * size * 0.20 + cos_a * size * 0.22 * side
            self.sprites.factory.blit_add(target, engine, (ex, ey))

    # -------------------------------------------------------- полосы
    def _draw_bars(self, target: pygame.Surface, sim: Simulation,
                   camera: Camera) -> None:
        """Полосы прогресса над целями: добыча, скан, изучение.

        Прогресс добычи висит над дроном, а скан и изучение — над тем, на что
        смотрит игрок: так видно, что именно тянет время.
        """
        if sim.mine_target is not None and sim.mine_progress > 0.0:
            drone = sim.drone
            self._progress_bar(target, camera, drone.x, drone.y, sim.mine_progress,
                               theme.MINE_BAR_FG, dy=-int(config.TILE * camera.zoom) - 10)
        if sim.scan_target is not None and sim.scan_progress > 0.0:
            self._progress_bar(target, camera, sim.scan_target.x + 0.5,
                               sim.scan_target.y + 0.5, sim.scan_progress,
                               theme.SCAN_BAR_FG, dy=-int(config.TILE * camera.zoom * 0.6))
        if sim.study_target is not None and sim.study_progress > 0.0:
            self._progress_bar(target, camera, sim.study_target.x + 0.5,
                               sim.study_target.y + 0.5, sim.study_progress,
                               theme.STUDY_BAR_FG,
                               dy=-int(config.TILE * camera.zoom * 0.66))

    def _progress_bar(self, target: pygame.Surface, camera: Camera, wx: float,
                      wy: float, frac: float, color: tuple[int, int, int],
                      dy: int = 0) -> None:
        """Полоска прогресса над мировой точкой. Сурфейсы не создаёт."""
        sx, sy = camera.world_to_screen(wx, wy)
        w = int(config.TILE * camera.zoom * BAR_WIDTH)
        h = max(3, int(config.TILE * camera.zoom * 0.14))
        x = int(sx - w / 2)
        y = int(sy) + dy
        pygame.draw.rect(target, theme.OUTLINE, (x - 2, y - 2, w + 4, h + 4),
                         border_radius=3)
        pygame.draw.rect(target, theme.MINE_BAR_BG, (x - 1, y - 1, w + 2, h + 2),
                         border_radius=2)
        filled = int(w * max(0.0, min(1.0, frac)))
        if filled > 0:
            pygame.draw.rect(target, color, (x, y, filled, h), border_radius=2)

    # ------------------------------------------------------- вспомогательное
    def world_surface_point(self, camera: Camera, tx: float, ty: float):
        """Экранная точка центра тайла — удобно для UI, привязанного к миру."""
        return camera.world_to_screen(tx + 0.5, ty + 0.5)


# ------------------------------------------------------------- вспомогательное
def _shake_offset(amplitude: float, t: float) -> tuple[float, float]:
    """Смещение камеры при тряске: две несоизмеримые частоты, чтобы не мигало."""
    return (math.sin(t * 47.0) * amplitude, math.cos(t * 61.0) * amplitude)


def _phase(index: int) -> float:
    """Фаза «дыхания» объекта по его номеру: свет не мигает синхронно.

    Множитель золотого сечения разводит фазы соседей так, чтобы они не совпали
    ни при каком числе ламп.
    """
    return (index * 0.6180339887 % 1.0) * math.tau


def _breath(t: float, phase: float) -> float:
    """0…1 — медленное «дыхание»: синус, переведённый в положительную шкалу."""
    return 0.5 + 0.5 * math.sin(t + phase)


def _quantize(value: float, steps: int) -> float:
    """Округляет 0…1 до `steps` ступеней.

    Нужно кэшу: плавная яркость давала бы новую поверхность ореола каждый кадр,
    а ступени не видно на глаз (plan/09 §3.2 — «ноль создания поверхностей»).
    """
    return round(max(0.0, min(1.0, value)) * steps) / steps


def _glow_level(darkness: float) -> float:
    """Ступень тьмы 0…1, по которой квантуется яркость всего свечения."""
    return int(round(max(0.0, min(1.0, darkness)) * GLOW_LEVELS)) / GLOW_LEVELS


def _make_glow(radius: int, kind: str, peak: int) -> pygame.Surface:
    """Предрассчитанное пятно света (plan/09 §3.4). Строится один раз на ключ.

    Пятно лампы тёплое в центре и чуть холоднее к краю (plan/09 §9), пятно
    дрона — холодное целиком. Поверхность без альфы: чёрный край — это ноль
    для `BLEND_RGB_MAX`.
    """
    size = radius * 2 + 1
    surf = pygame.Surface((size, size))
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = float(radius)
    dist = np.hypot(xx - c, yy - c) / max(1.0, c)
    inner = LAMP_GLOW_INNER if kind == "lamp" else DRONE_LIGHT_INNER
    falloff = _falloff(dist, inner)
    warm = np.array(theme.LAMP_LIGHT, dtype=np.float32)
    cool = np.array(theme.DRONE_LIGHT, dtype=np.float32)
    if kind == "lamp":
        base = warm + (cool - warm) * np.clip(dist, 0.0, 1.0)[..., None] * LAMP_COOL_MIX
    else:
        base = np.broadcast_to(cool, (size, size, 3)).copy()
    value = base * (peak * falloff / 255.0)[..., None]
    arr = pygame.surfarray.pixels3d(surf)
    for ch in range(3):
        arr[..., ch] = value[..., ch].astype(np.uint8).T
    del arr
    return surf


def _falloff(dist: np.ndarray, inner: float) -> np.ndarray:
    """Спад яркости от центра пятна к краю, 0..1. `dist` — доля радиуса.

    Живёт отдельно от `_make_glow`, потому что тот же спад нужен слою тьмы
    (`Renderer._light_field`): пятно света и вырез в темноте обязаны совпадать
    по форме, иначе на краю пятна виден ободок.
    """
    k = 1.0 - np.clip((dist - inner) / max(1e-6, 1.0 - inner), 0.0, 1.0)
    return np.clip(k, 0.0, 1.0) ** GLOW_FALLOFF_POW
