"""Resolve a declared dependency graph before a battle can start."""
from .schemas import UnsupportedContent


def resolve(graph, roots, available):
    visited, active, ordered = set(), set(), []

    def visit(key, path):
        if key in visited:
            return
        if key in active:
            raise UnsupportedContent(path, f"dependency cycle at {key}")
        if key not in available:
            raise UnsupportedContent(path, f"missing definition {key}")
        active.add(key)
        for child in graph.get(key, ()):
            visit(child, f"{path} -> {child}")
        active.remove(key)
        visited.add(key)
        ordered.append(key)

    for root in roots:
        visit(root, root)
    return tuple(ordered)
