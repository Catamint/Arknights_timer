"""Deterministic grid topology and geometry, independent of movement rates."""
import math
from collections import deque
from collections.abc import Mapping

from ark_sim.contracts import thaw


class UnreachablePathError(ValueError):
    pass


class GridTopology:
    """Row-major tiles and safe four-neighbour shortest paths.

    Tile centres have integer row/col coordinates. Returned paths exclude the
    origin and include the destination. No diagonal corner cutting is applied.
    """
    _NEIGHBOURS = ((-1, 0), (0, -1), (0, 1), (1, 0))

    def __init__(self, map_definition):
        if not isinstance(map_definition, Mapping):
            raise ValueError("map definition must be an object")
        self.rows, self.cols = map_definition.get("rows"), map_definition.get("cols")
        for field, value in (("rows", self.rows), ("cols", self.cols)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"map {field} must be a positive integer")
        tiles = map_definition.get("tiles")
        if tiles is None:
            self._tiles = [{"tileKey": "tile_floor", "passableMask": 1, "buildableType": 1}
                           for _ in range(self.rows * self.cols)]
        else:
            if not isinstance(tiles, (list, tuple)) or len(tiles) != self.rows * self.cols:
                raise ValueError("map tiles must contain exactly rows * cols row-major entries")
            if any(not isinstance(tile, Mapping) for tile in tiles):
                raise ValueError("map tiles must be objects")
            self._tiles = thaw(tiles)
        for tile in self._tiles:
            mask = tile.get("passableMask", 1)
            if mask is not None and (type(mask) is not int or mask < 0):
                raise ValueError("tile passableMask must be a nonnegative integer")

    def inside(self, row, col):
        return (type(row) is int and type(col) is int and
                0 <= row < self.rows and 0 <= col < self.cols)

    def tile(self, row, col):
        if not self.inside(row, col):
            raise ValueError(f"tile outside map: ({row}, {col})")
        return thaw(self._tiles[row * self.cols + col])

    def passable(self, row, col):
        if not self.inside(row, col):
            return False
        tile = self._tiles[row * self.cols + col]
        key = tile.get("tileKey", "tile_floor")
        if key in {"tile_wall", "tile_forbidden"}:
            return False
        if key in {"tile_start", "tile_end"}:
            return True
        return bool((tile.get("passableMask", 1) or 0) & 1)

    def _cell(self, position):
        if not isinstance(position, Mapping) or not {"row", "col"}.issubset(position):
            raise ValueError("position needs row and col")
        values = []
        for coordinate in ("row", "col"):
            value = position[coordinate]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("position coordinates must be finite numbers")
            values.append(math.floor(value + 0.5))
        cell = tuple(values)
        if not self.inside(*cell):
            raise ValueError(f"position outside map: {dict(position)}")
        return cell

    def path(self, origin, destination):
        start, end = self._cell(origin), self._cell(destination)
        if not self.passable(*start) or not self.passable(*end):
            raise UnreachablePathError(f"Route endpoint is not ground-passable: {start} -> {end}")
        target = {"row": destination["row"], "col": destination["col"]}
        if start == end:
            return [] if origin["row"] == target["row"] and origin["col"] == target["col"] else [target]
        queue = deque([start])
        previous = {start: None}
        while queue and end not in previous:
            row, col = queue.popleft()
            for dr, dc in self._NEIGHBOURS:
                neighbour = (row + dr, col + dc)
                if neighbour not in previous and self.passable(*neighbour):
                    previous[neighbour] = (row, col)
                    queue.append(neighbour)
        if end not in previous:
            raise UnreachablePathError(f"No ground route: {start} -> {end}")
        route = []
        current = end
        while current != start:
            route.append({"row": current[0], "col": current[1]})
            current = previous[current]
        route.reverse()
        route[-1] = target
        return route
