"""Энергия и сеть зданий (M4, plan/02, plan/03). Не импортирует pygame.

Сеть — это **связная группа точек на соседних тайлах** (4 стороны): генераторы
дают энергию, потребители её тратят, провода только соединяют. Отдельных
«проводов между зданиями» нет: провод — такое же здание на тайле, и всё, что
стоит вплотную, оказывается в одной сети. Игрок собирает сеть из плиток, как
дорожку, и видит её границы глазами.

Правила простые и намеренно грубые (в M4 важна связность, а не физика):

- сеть работает, если её генераторы дают **не меньше**, чем просят потребители;
- потребитель без энергии не работает вовсе (лампа гаснет, бур стоит, слот
  лаборатории показывает «нет энергии»);
- энергии не накапливается: избыток пропадает.

Поле `wants` у узла — «этой сети энергия нужна прямо сейчас». Оно считается по
**спросу**, а не по выработке: иначе вышел бы порочный круг (нет топлива → сеть
не работает → генератор не тратит топливо → топливо не нужно). Симуляция по
этому флагу решает, жечь ли топливо в генераторе.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class PowerNode:
    """Точка сети на тайле: генератор, потребитель или просто провод.

    `target` — лампа (`sim/light.py`) или здание (`sim/buildings.py`): кому
    симуляция выставит результат. Сам `PowerSystem` про них ничего не знает.
    """

    x: int
    y: int
    target: object
    supply: float = 0.0     # сколько даёт (0 — не генератор)
    demand: float = 0.0     # сколько тратит (0 — не потребитель)
    powered: bool = False   # есть ли энергия в сети этого узла
    wants: bool = False     # нужна ли сети энергия (для топки генераторов)


@dataclass
class Network:
    """Одна связная сеть: узлы, выработка, спрос и вердикт."""

    nodes: list[PowerNode]
    supply: float
    demand: float
    powered: bool


class PowerSystem:
    """Сети зданий: кто с кем соединён и у кого есть энергия."""

    def __init__(self) -> None:
        self.networks: list[Network] = []
        #: Сколько потребителей сейчас без энергии — для HUD и отладки.
        self.starved = 0

    # ------------------------------------------------------------- пересчёт
    def update(self, nodes: list[PowerNode]) -> list[Network]:
        """Собирает сети из узлов и выставляет каждому `powered` и `wants`."""
        self.networks = self._build(nodes)
        self.starved = 0
        for network in self.networks:
            network.powered = network.demand > 0.0 and network.supply >= network.demand
            wants = network.demand > 0.0
            for node in network.nodes:
                node.powered = network.powered
                node.wants = wants
                if node.demand > 0.0 and not network.powered:
                    self.starved += 1
        return self.networks

    def _build(self, nodes: list[PowerNode]) -> list[Network]:
        """Связные компоненты по соседству тайлов (4 стороны).

        Обход идёт по словарю «тайл → узел», поэтому две сети, случайно
        оказавшиеся рядом, соединяются сами — это и есть правило «вплотную
        значит вместе».
        """
        by_tile: dict[tuple[int, int], PowerNode] = {(n.x, n.y): n for n in nodes}
        seen: set[tuple[int, int]] = set()
        networks: list[Network] = []
        for start in by_tile:
            if start in seen:
                continue
            group: list[PowerNode] = []
            queue: deque[tuple[int, int]] = deque([start])
            seen.add(start)
            while queue:
                tile = queue.popleft()
                group.append(by_tile[tile])
                x, y = tile
                for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if nb in by_tile and nb not in seen:
                        seen.add(nb)
                        queue.append(nb)
            # Порядок узлов детерминирован: слева направо, сверху вниз.
            group.sort(key=lambda n: (n.y, n.x))
            networks.append(Network(
                group,
                sum(n.supply for n in group),
                sum(n.demand for n in group),
                False,
            ))
        networks.sort(key=lambda net: (net.nodes[0].y, net.nodes[0].x))
        return networks

    # ----------------------------------------------------------- для отладки
    def state(self) -> tuple:
        """Слепок сетей — входит в состояние симуляции и в отладочную панель."""
        return tuple((net.powered,
                      tuple((n.x, n.y, round(n.supply, 3), round(n.demand, 3))
                            for n in net.nodes))
                     for net in self.networks)

    def summary(self) -> tuple[float, float]:
        """(выработка, спрос) по всем сетям — строка «Энергия» в HUD."""
        return (sum(net.supply for net in self.networks),
                sum(net.demand for net in self.networks))
