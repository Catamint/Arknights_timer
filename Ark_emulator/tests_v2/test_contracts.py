"""Immutable shared records safely reuse only their own recursively frozen trees."""
from types import MappingProxyType

import pytest

from ark_sim.contracts import freeze, thaw


def test_freeze_reuses_its_own_recursive_immutable_trees():
    value = freeze({"a": [{"b": 2}]})
    assert freeze(value) is value
    assert freeze(value["a"]) is value["a"]
    with pytest.raises(TypeError):
        value["a"][0]["b"] = 3
    with pytest.raises(TypeError):
        value._data = {}


def test_external_readonly_mapping_still_requires_recursive_copy():
    child = [{"x": 1}]
    source = MappingProxyType({"a": child})
    value = freeze(source)
    child[0]["x"] = 2
    assert value["a"][0]["x"] == 1


def test_thaw_never_exposes_shared_mutable_children():
    value = freeze({"a": [{"x": 1}]})
    copied = thaw(value)
    copied["a"][0]["x"] = 8
    assert value["a"][0]["x"] == 1
