"""Камера: преобразования координат, зум, границы."""

from __future__ import annotations

import pytest

from src.game import config
from src.game.render.camera import Camera


def make_camera(x: float = 48.0, y: float = 48.0, zoom: float = 1.0) -> Camera:
    return Camera(x, y, viewport_w=config.WINDOW_W, viewport_h=config.WINDOW_H, zoom=zoom)


def test_scale_depends_on_zoom():
    cam = make_camera(zoom=1.0)
    assert cam.scale == config.TILE
    cam.set_zoom(2.0)
    assert cam.scale == config.TILE * 2


def test_center_maps_to_screen_center():
    cam = make_camera(10.0, 20.0)
    sx, sy = cam.world_to_screen(10.0, 20.0)
    assert (sx, sy) == (config.WINDOW_W / 2, config.WINDOW_H / 2)


def test_roundtrip_world_screen():
    for zoom in (0.5, 1.0, 1.7, 2.0):
        cam = make_camera(30.0, 40.0, zoom)
        for wx, wy in ((30.0, 40.0), (33.25, 37.75), (0.0, 0.0)):
            sx, sy = cam.world_to_screen(wx, wy)
            bx, by = cam.screen_to_world(sx, sy)
            assert bx == pytest.approx(wx, abs=1e-9)
            assert by == pytest.approx(wy, abs=1e-9)


def test_zoom_is_clamped():
    cam = make_camera()
    cam.set_zoom(99.0)
    assert cam.zoom == config.ZOOM_MAX
    cam.set_zoom(0.01)
    assert cam.zoom == config.ZOOM_MIN


def test_zoom_by_steps():
    cam = make_camera()
    cam.zoom_by(3)
    assert cam.zoom == pytest.approx(config.ZOOM_DEFAULT + 3 * config.ZOOM_STEP)
    cam.zoom_by(-100)
    assert cam.zoom == config.ZOOM_MIN


def test_follow_smooths_towards_target():
    cam = make_camera(0.0, 0.0)
    cam.follow(10.0, 0.0, config.TICK)
    assert 0.0 < cam.x < 10.0
    for _ in range(600):
        cam.follow(10.0, 0.0, config.TICK)
    assert cam.x == pytest.approx(10.0, abs=1e-6)


def test_follow_with_zero_dt_does_nothing():
    cam = make_camera(1.0, 2.0)
    cam.follow(50.0, 50.0, 0.0)
    assert (cam.x, cam.y) == (1.0, 2.0)


def test_visible_tiles_cover_viewport():
    cam = make_camera(48.0, 48.0, zoom=1.0)
    x0, y0, x1, y1 = cam.visible_tiles(margin=0)
    # левый верхний угол экрана должен попадать в диапазон
    wx, wy = cam.screen_to_world(0, 0)
    assert x0 <= int(wx) <= x1
    assert y0 <= int(wy) <= y1
    wx2, wy2 = cam.screen_to_world(config.WINDOW_W - 1, config.WINDOW_H - 1)
    assert x0 <= int(wx2) <= x1
    assert y0 <= int(wy2) <= y1


def test_clamp_keeps_view_inside_world():
    cam = make_camera(0.0, 0.0)
    cam.clamp_to(96, 96)
    hw, hh = cam.half_extent
    assert cam.x >= hw - 1e-9
    assert cam.y >= hh - 1e-9
    cam.snap_to(1000.0, 1000.0)
    cam.clamp_to(96, 96)
    assert cam.x <= 96 - hw + 1e-9
    assert cam.y <= 96 - hh + 1e-9


def test_clamp_centers_small_world():
    cam = make_camera(5.0, 5.0, zoom=config.ZOOM_MIN)
    cam.clamp_to(4, 4)
    assert cam.x == 2.0 and cam.y == 2.0
