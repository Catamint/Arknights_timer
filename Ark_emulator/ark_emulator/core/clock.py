"""Integer logic clock; wall time never changes battle rules."""
from dataclasses import dataclass
import math

RATE = 30
DT = 1.0 / RATE


def ticks(seconds):
    return max(0, math.ceil(float(seconds) * RATE - 1e-9))


@dataclass
class Clock:
    tick: int = 0

    @property
    def seconds(self):
        return self.tick / RATE
