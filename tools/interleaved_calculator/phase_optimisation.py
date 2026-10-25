"""Numerical phase search for the rectangular-output-pulse bus model."""

from collections.abc import Sequence

from scipy.optimize import differential_evolution

from .bus_ripple import calculate_bus_ripple


def optimise_phases(
    duty_cycles: Sequence[float],
    output_currents_a: Sequence[float],
    switching_frequency_hz: float,
    bus_capacitance_f: float,
    seeds: Sequence[int] = (7, 23, 61, 109),
) -> tuple[list[float], list[float]]:
    """Return best phases (degrees) and each search's best ripple (volts).

    Fix channel one's phase at zero: a common phase shift leaves ripple
    unchanged. Multiple seeded searches improve coverage, not prove optimality.
    Always retain the aligned/equally spaced baseline if a search is worse.
    """
    count = len(duty_cycles)
    aligned = [0.0] * count

    def objective(free_phases: Sequence[float]) -> float:
        return calculate_bus_ripple(
            duty_cycles, output_currents_a, switching_frequency_hz,
            bus_capacitance_f, [0.0, *free_phases],
        )

    # Validate through the same model used for plotting and reporting.
    aligned_ripple = calculate_bus_ripple(
        duty_cycles, output_currents_a, switching_frequency_hz,
        bus_capacitance_f, aligned,
    )
    if not seeds:
        raise ValueError("Provide at least one search seed.")
    if count == 1 or aligned_ripple == 0:
        return aligned, [aligned_ripple]

    spaced = [360.0 * k / count for k in range(count)]
    best = min((aligned, spaced), key=lambda phases: objective(phases[1:]))
    best_ripple = objective(best[1:])
    run_ripples = []
    for seed in seeds:
        result = differential_evolution(
            objective,
            bounds=[(0.0, 360.0)] * (count - 1),
            rng=seed,
            x0=spaced[1:],
            popsize=20,
            maxiter=1200,
            tol=1e-8,
            atol=1e-10,
            polish=False,
        )
        run_ripples.append(float(result.fun))
        if result.fun < best_ripple:
            best = [0.0, *(float(phase) % 360.0 for phase in result.x)]
            best_ripple = float(result.fun)
    return best, run_ripples
