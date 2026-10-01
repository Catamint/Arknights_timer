"""Historical V1 implementation, retained for offline extraction and reference.

All future simulation development uses the independent ark_sim V2 package.
This namespace includes the original engine and the stage-one prototype;
their interfaces and coverage records are historical, not V2 guarantees.
See ../docs/V1_HISTORY.md and ../docs/V2_IMPLEMENTATION.md.
"""

__version__ = "0.1.0"
__implementation_status__ = "historical_v1"
__development_base__ = "ark_sim"

_LAZY = {}


def __getattr__(name):
    if name == "Simulator":
        from . import api
        return api.Simulator
    raise AttributeError(name)
