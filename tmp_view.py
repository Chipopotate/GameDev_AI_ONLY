"""Временный зонд: обзор карты и крупные кадры для рассматривания графики."""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

pygame.display.init()
screen = pygame.display.set_mode((1280, 720))

from src.game.render.terrain_chunks import ChunkCache  # noqa: E402
from src.game.sim.simulation import Simulation  # noqa: E402

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 20251013
mode = sys.argv[2] if len(sys.argv) > 2 else "map"
sim = Simulation(seed)
world = sim.world
chunks = ChunkCache(world)
w, h = world.width * chunks.tile, world.height * chunks.tile
full = pygame.Surface((w, h))
for cy in range((world.height + chunks.chunk - 1) // chunks.chunk):
    for cx in range((world.width + chunks.chunk - 1) // chunks.chunk):
        full.blit(chunks.surface(cx, cy), (cx * chunks.chunk * chunks.tile,
                                           cy * chunks.chunk * chunks.tile))
if mode == "map":
    k = 1400 / max(w, h)
    pygame.image.save(pygame.transform.smoothscale(full, (int(w * k), int(h * k))),
                      "screens/_map.png")
    print("spawn", world.spawn_point(), world.width, world.height)
else:
    # крупно: кусок вокруг тайла x,y
    x, y = int(sys.argv[3]), int(sys.argv[4])
    n = 14
    t = chunks.tile
    surf = pygame.Surface((n * t, n * t))
    surf.blit(full, (0, 0), pygame.Rect((x - n // 2) * t, (y - n // 2) * t, n * t, n * t))
    pygame.image.save(pygame.transform.scale(surf, (n * t * 2, n * t * 2)), "screens/_zoom.png")
