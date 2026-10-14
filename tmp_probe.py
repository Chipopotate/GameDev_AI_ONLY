"""Временный зонд: какие ключи спрайтов создаются в «прогретом» кадре."""
import os
import sys
from collections import Counter

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

pygame.display.init()
screen = pygame.display.set_mode((1280, 720))

from src.game import config  # noqa: E402
from src.game.render import theme  # noqa: E402
from src.game.render.camera import Camera  # noqa: E402
from src.game.render.renderer import Renderer  # noqa: E402
from src.game.render.sprites import factory as factory_mod  # noqa: E402
from src.game.sim.simulation import Simulation  # noqa: E402
from tests.conftest import SEED, power_lamp  # noqa: E402

sim = Simulation(SEED)
camera = Camera(*sim.world.spawn_point(), viewport_w=1280, viewport_h=720)
renderer = Renderer(sim.world)
for dy in range(-3, 4):
    for dx in range(-3, 4):
        if abs(dx) < 2 and abs(dy) < 2:
            continue
        if sim.can_place_lamp(int(sim.drone.x) + dx, int(sim.drone.y) + dy):
            power_lamp(sim, int(sim.drone.x) + dx, int(sim.drone.y) + dy)
            break
    else:
        continue
    break
sim.time_cycle.update(sim.time_cycle.first_day_length + config.DARKNESS_FADE + 0.1)
item = sim.run.items.natural_ids[0]
x0, y0, _x1, _y1 = camera.visible_tiles(margin=1)
for i in range(40):
    sim.drops.spawn_tile(item, i % 5 + 1, x0 + i % 8, y0 + i // 8)

new_keys = Counter()
halo_keys = Counter()
original_get = factory_mod.SpriteFactory.get
original_halo = factory_mod.SpriteFactory.halo


def get(self, key, size, draw):
    before = self.rendered
    surf = original_get(self, key, size, draw)
    if self.rendered != before:
        new_keys[(key, size)] += 1
    return surf


def halo(self, radius, color, inner=0.0, peak=1.0, **kw):
    before = self.halos_made
    surf = original_halo(self, radius, color, inner, peak, **kw)
    if self.halos_made != before:
        halo_keys[(radius, tuple(color))] += 1
    return surf


factory_mod.SpriteFactory.get = get
factory_mod.SpriteFactory.halo = halo


def spark(count):
    renderer.particles.burst("spark", sim.drone.x, sim.drone.y, count, 3.0,
                             ttl=1.0, size=3.0, color=theme.NEON_CYAN,
                             rng=renderer.effects.rng)


for _ in range(3):
    spark(40)
    renderer.draw_world(screen, sim, camera, dt=config.TICK)
new_keys.clear()
halo_keys.clear()
for _ in range(10):
    spark(20)
    renderer.draw_world(screen, sim, camera, dt=config.TICK)
print("новые спрайты:", sum(new_keys.values()), "уникальных:", len(new_keys))
for (key, size), n in new_keys.most_common(6):
    print("   ", key, size, "x", n)
print("новые ореолы:", sum(halo_keys.values()), "уникальных:", len(halo_keys))
for (radius, color), n in halo_keys.most_common(6):
    print("   ", radius, color, "x", n)
