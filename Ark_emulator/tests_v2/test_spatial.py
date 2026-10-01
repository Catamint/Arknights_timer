import pytest

from ark_sim.domains.spatial import GridTopology, UnreachablePathError


def test_missing_tiles_are_floor_and_shortest_route_has_independent_expected_length():
    grid = GridTopology({"rows": 3, "cols": 4})
    assert grid.inside(2, 3)
    assert not grid.inside(3, 0)
    assert not grid.inside(True, 0)
    route = grid.path({"row": 2, "col": 0}, {"row": 0, "col": 3})
    assert len(route) == 5
    assert route[0] == {"row": 1, "col": 0}
    assert route[-1] == {"row": 0, "col": 3}
    assert grid.path({"row": 1, "col": 1}, {"row": 1, "col": 1}) == []


def test_row_major_tiles_and_wall_detour_are_safe():
    tiles = [{"tileKey": "tile_floor", "passableMask": 1} for _ in range(9)]
    tiles[4] = {"tileKey": "tile_wall", "passableMask": 3}
    grid = GridTopology({"rows": 3, "cols": 3, "tiles": tiles})
    tiles[4]["tileKey"] = "tile_floor"
    assert grid.tile(1, 1)["tileKey"] == "tile_wall"
    detached = grid.tile(1, 1)
    detached["tileKey"] = "changed"
    route = grid.path({"row": 1, "col": 0}, {"row": 1, "col": 2})
    assert len(route) == 4
    assert {"row": 1, "col": 1} not in route
    previous = {"row": 1, "col": 0}
    for point in route:
        assert abs(point["row"]-previous["row"])+abs(point["col"]-previous["col"]) == 1
        previous = point


def test_ground_mask_and_start_end_special_cases():
    grid = GridTopology({"rows": 1, "cols": 4, "tiles": [
        {"tileKey": "tile_start", "passableMask": 0},
        {"tileKey": "tile_floor", "passableMask": 3},
        {"tileKey": "tile_forbidden", "passableMask": 1},
        {"tileKey": "tile_end", "passableMask": 0},
    ]})
    assert grid.passable(0, 0) and grid.passable(0, 3)
    assert not grid.passable(0, 2)
    with pytest.raises(UnreachablePathError, match="No ground route"):
        grid.path({"row": 0, "col": 0}, {"row": 0, "col": 3})


def test_diagonal_neighbours_cannot_cut_blocked_corners():
    grid = GridTopology({"rows": 2, "cols": 2, "tiles": [
        {"passableMask": 1}, {"passableMask": 2}, {"passableMask": 0}, {"passableMask": 1},
    ]})
    with pytest.raises(UnreachablePathError):
        grid.path({"row": 0, "col": 0}, {"row": 1, "col": 1})


def test_continuous_origin_uses_nearest_tile_center():
    grid = GridTopology({"rows": 2, "cols": 2})
    assert grid.path({"row": 0.1, "col": 0.1}, {"row": 0, "col": 0}) == [{"row": 0, "col": 0}]
    assert grid.path({"row": 0.1, "col": 0.1}, {"row": 1, "col": 0}) == [{"row": 1, "col": 0}]
    with pytest.raises(ValueError, match="outside map"):
        grid.path({"row": -1, "col": 0}, {"row": 1, "col": 0})


@pytest.mark.parametrize("definition", [{"rows": True, "cols": 3}, {"rows": 2, "cols": 2, "tiles": []},
                                          {"rows": 1, "cols": 1, "tiles": [{"passableMask": True}]}])
def test_invalid_map_never_silently_becomes_an_open_map(definition):
    with pytest.raises(ValueError):
        GridTopology(definition)
