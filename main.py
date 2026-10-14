"""Точка входа игры «Свет».

Запуск:
    python main.py                 # играть
    python main.py --seed 42       # фиксированная карта
    python main.py --headless --screenshot shot.png   # кадр без окна
    python main.py --headless --bench 300             # замер производительности
"""

from __future__ import annotations

import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Свет — 2D рогалик + автоматизация + защита базы (прототип M3)",
    )
    parser.add_argument("--seed", type=int, default=None,
                        help="сид генерации карты (по умолчанию случайный)")
    parser.add_argument("--fullscreen", action="store_true", help="полноэкранный режим")
    parser.add_argument("--night", action="store_true",
                        help="отладка: начать сразу с ночи (проверка тьмы и света)")
    parser.add_argument("--headless", action="store_true",
                        help="без окна (SDL dummy) — для скриншотов и замеров")
    parser.add_argument("--screenshot", metavar="PATH",
                        help="отрисовать кадр и сохранить в PNG, затем выйти")
    parser.add_argument("--frames", type=int, default=30,
                        help="сколько кадров прогреть перед скриншотом")
    parser.add_argument("--bench", type=int, metavar="FRAMES",
                        help="замерить производительность на N кадрах и выйти")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    # Драйвер выбирается до импорта pygame — иначе окно уже создано.
    if args.headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    from src.game import app

    if args.bench is not None:
        stats = app.run_bench(frames=args.bench, seed=args.seed, night=args.night)
        print(f"кадров: {stats['frames']}, "
              f"среднее: {stats['ms_per_frame']:.2f} мс, "
              f"FPS: {stats['fps']:.1f}")
        return 0

    if args.screenshot:
        path = app.save_screenshot(args.screenshot, seed=args.seed, frames=args.frames,
                                   night=args.night)
        print(f"скриншот сохранён: {path}")
        return 0

    game = app.Game(seed=args.seed, fullscreen=args.fullscreen)
    if args.night:
        game.skip_to_night()
    game.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
