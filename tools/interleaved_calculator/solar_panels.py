class SolarPanel:
    def __init__(self, voc: float, isc: float, vmpp: float, impp: float, irradiance: float):
        self.voc = voc
        self.isc = isc
        self.vmpp = vmpp
        self.impp = impp
        self.irradiance = irradiance  # W/m²

    def get_impp(self) -> float:
        return self.impp * (self.irradiance / 1000.0)
