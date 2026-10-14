"""Временный инструмент: вырезать кусок PNG и сохранить — чтобы рассмотреть его вблизи."""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

src, x, y, w, h, dst = sys.argv[1], *[int(v) for v in sys.argv[2:6]], sys.argv[6]
pygame.display.init()
image = pygame.image.load(src)
crop = pygame.Surface((w, h))
crop.blit(image, (0, 0), pygame.Rect(x, y, w, h))
pygame.image.save(pygame.transform.scale(crop, (w * 2, h * 2)), dst)
print(dst)
