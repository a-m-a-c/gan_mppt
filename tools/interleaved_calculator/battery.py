from dataclasses import dataclass

@dataclass
class Battery:
    vmin: float
    vmax: float
    vnom: float
