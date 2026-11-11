"""Finite numerical experiments with resolution and precision diagnostics."""

import mpmath as mp
import sympy as sp

from amre.domains.gr.expressions import parse_expression
from amre.domains.gr.models import NumericInput, NumericOperation
from amre.domains.models import ComputationResult, ProofObligation
from amre.errors import ResearchError


def _real(value):
    if (
        not isinstance(value, (int, float, mp.mpf))
        or not mp.isfinite(value)
        or abs(value) > 10**100
    ):
        raise ResearchError("Nonfinite, nonreal, or out-of-range numerical value")
    return mp.mpf(value)


def _eigenvalues(spec: NumericInput):
    matrix = mp.matrix([[_real(mp.mpf(item)) for item in row] for row in spec.matrix])
    if matrix != matrix.T:
        raise ResearchError("This eigenvalue tool requires a real symmetric matrix")
    values, vectors = mp.eigsy(matrix)
    residual = max(abs(entry) for entry in (matrix * vectors - vectors * mp.diag(values)))
    return {
        "values": [mp.nstr(item, spec.precision_digits) for item in values],
        "max_residual": mp.nstr(residual, 10),
    }


def _refine(spec: NumericInput):
    variables = ("t", "y") if spec.operation == NumericOperation.ODE else ("x",)
    symbols = {name: sp.Symbol(name, real=True) for name in variables}
    expression = parse_expression(spec.expression, symbols)
    fn = sp.lambdify(tuple(symbols.values()), expression, modules="mpmath")
    a, b = (_real(mp.mpf(item)) for item in spec.interval)
    if b <= a or max(abs(a), abs(b)) > 10**6:
        raise ResearchError("Interval endpoints must be increasing and bounded by 1e6")

    def integrate(steps: int):
        width = (b - a) / steps
        return width * mp.fsum(_real(fn(a + (i + mp.mpf("0.5")) * width)) for i in range(steps))

    def solve(steps: int):
        width = (b - a) / steps
        y = _real(mp.mpf(spec.initial_value))
        for index in range(steps):
            t = a + index * width
            k1 = _real(fn(t, y))
            k2 = _real(fn(t + width / 2, y + width * k1 / 2))
            k3 = _real(fn(t + width / 2, y + width * k2 / 2))
            k4 = _real(fn(t + width, y + width * k3))
            y = _real(y + width * (k1 + 2 * k2 + 2 * k3 + k4) / 6)
        return y

    solver = solve if spec.operation == NumericOperation.ODE else integrate
    coarse = solver(spec.steps)
    fine = solver(2 * spec.steps)
    return {
        "coarse": mp.nstr(coarse, spec.precision_digits),
        "fine": mp.nstr(fine, spec.precision_digits),
        "refinement_delta": mp.nstr(abs(fine - coarse), 12),
        "coarse_steps": spec.steps,
        "fine_steps": 2 * spec.steps,
        "method": "RK4" if solver == solve else "COMPOSITE_MIDPOINT",
    }


def numeric(spec: NumericInput) -> ComputationResult:
    if spec.rigorous_interval:
        return ComputationResult(
            status="UNAVAILABLE",
            scope="Rigorous interval enclosure",
            detail="No validated interval arithmetic backend is configured.",
        )
    with mp.workdps(spec.precision_digits):
        if spec.operation == NumericOperation.EIGENVALUES:
            output = _eigenvalues(spec)
        else:
            output = _refine(spec)
    return ComputationResult(
        status="COMPLETED",
        scope=f"Finite numerical {spec.operation} experiment",
        output={**output, "precision_digits": spec.precision_digits, "rigorous_interval": False},
        obligations=(
            ProofObligation(
                subject="Error certification",
                automation="NOT_AUTOMATED",
                condition="Prove convergence or bound errors on the claimed domain separately.",
            ),
        ),
        software={"sympy": sp.__version__, "mpmath": mp.__version__},
    )
