class BoostConverter:
    def __init__(
        self,
        vin: float,
        iin: float,
        vout: float,
        efficiency: float,
        switching_frequency_hz: float,
        inductance_h: float,
        output_capacitance_f: float,
    ):
        self._vin = vin
        self._vout = vout
        self._iin = iin
        self._efficiency = efficiency
        self._switching_frequency_hz = switching_frequency_hz
        self._inductance_h = inductance_h
        self._output_capacitance_f = output_capacitance_f

    def get_output_current(self) -> float:
        pin = self._vin * self._iin
        pout = pin * self._efficiency
        iout = pout / self._vout
        return iout

    def get_duty_cycle(self) -> float:
        return 1 - (self._vin / self._vout)

    def get_inductor_ripple_current(self) -> float:
        duty = self.get_duty_cycle()
        iin_avg = self._iin
        il_ripple = (self._vin * duty) / (self._inductance_h * self._switching_frequency_hz)
        return il_ripple

    def get_peak_inductor_current(self) -> float:
        il_ripple = self.get_inductor_ripple_current()
        iin_avg = self._iin
        il_peak = iin_avg + il_ripple / 2
        return il_peak

    def get_output_ripple_voltage(self) -> float:
        duty = self.get_duty_cycle()
        iout = self.get_output_current()
        vout_ripple = iout * duty / (self._switching_frequency_hz * self._output_capacitance_f)
        return vout_ripple
