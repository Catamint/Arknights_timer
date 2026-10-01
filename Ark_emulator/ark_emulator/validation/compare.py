"""First difference, with explicit per-path tolerances and no blanket ignore."""
import math


def first_difference(expected, actual, tolerances=None, path="$"):
    tolerances = tolerances or {}
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            p = f"{path}.{key}"
            if key not in expected or key not in actual:
                return {"path": p, "expected": expected.get(key), "actual": actual.get(key), "reason": "missing key"}
            diff = first_difference(expected[key], actual[key], tolerances, p)
            if diff:
                return diff
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return {"path": path, "expected": len(expected), "actual": len(actual), "reason": "length"}
        for i, (e, a) in enumerate(zip(expected, actual)):
            diff = first_difference(e, a, tolerances, f"{path}[{i}]")
            if diff:
                return diff
        return None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if math.isfinite(expected) and math.isfinite(actual) and abs(expected - actual) <= tolerances.get(path, 0):
            return None
    elif type(expected) is type(actual) and expected == actual:
        return None
    return {"path": path, "expected": expected, "actual": actual, "reason": "value"}
