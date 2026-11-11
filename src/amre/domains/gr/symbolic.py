"""Scoped curvature tensors and the flat scalar-wave energy identity."""

from itertools import product

import mpmath as mp
import sympy as sp

from amre.domains.gr.expressions import parse_expression
from amre.domains.gr.models import Boundary, CurvatureInput, Signature, WaveInput
from amre.domains.models import ComputationResult, ProofObligation
from amre.errors import ResearchError


def _signature(metric, spec: CurvatureInput, symbols):
    expected = {
        Signature.RIEMANNIAN: 0,
        Signature.MINUS_PLUS: 1,
        Signature.PLUS_MINUS: metric.rows - 1,
    }[spec.conventions.signature]
    diagonal = metric.is_diagonal()
    signs = [sp.sign(metric[index, index]) for index in range(metric.rows)] if diagonal else []
    if signs and all(item in (-1, 1) for item in signs):
        if signs.count(-1) != expected:
            raise ResearchError("The metric disagrees with the declared signature")
        return "EXACT_DIAGONAL"
    if spec.sample is None:
        return "UNVERIFIED_ON_CHART"
    sampled = metric.subs({symbols[key]: sp.Rational(value) for key, value in spec.sample.items()})
    if any(item.is_real is not True or item.is_finite is not True for item in sampled):
        raise ResearchError("Signature sample is outside the finite real chart")
    with mp.workdps(45):
        values = mp.eigsy(
            mp.matrix(
                [
                    [mp.mpf(str(sampled[i, j].evalf(45))) for j in range(metric.rows)]
                    for i in range(metric.rows)
                ]
            ),
            eigvals_only=True,
        )
        if any(abs(value) < mp.mpf("1e-35") for value in values):
            raise ResearchError("Signature sample is degenerate or numerically unresolved")
        if sum(value < 0 for value in values) != expected:
            raise ResearchError("Sampled metric disagrees with the declared signature")
    return "NUMERIC_SAMPLE_ONLY"


def _sparse(values):
    return {
        ",".join(map(str, indices)): sp.sstr(value)
        for indices, value in values.items()
        if value != 0
    }


def curvature(spec: CurvatureInput) -> ComputationResult:
    symbols = {key: sp.Symbol(key, real=True) for key in (*spec.coordinates, *spec.parameters)}
    x = tuple(symbols[key] for key in spec.coordinates)
    metric = sp.Matrix([[parse_expression(value, symbols) for value in row] for row in spec.metric])
    if any(
        sp.simplify(metric[i, j] - metric[j, i]) != 0 for i, j in product(range(len(x)), repeat=2)
    ):
        raise ResearchError("Metric symmetry could not be established")
    determinant = sp.simplify(metric.det())
    if determinant == 0 or determinant.is_zero is True:
        raise ResearchError("Metric is singular")
    signature = _signature(metric, spec, symbols)
    inverse = metric.inv().applyfunc(sp.simplify)
    n = range(len(x))
    gamma = {
        (a, b, c): sp.simplify(
            sum(
                inverse[a, d]
                * (
                    sp.diff(metric[d, c], x[b])
                    + sp.diff(metric[d, b], x[c])
                    - sp.diff(metric[b, c], x[d])
                )
                for d in n
            )
            / 2
        )
        for a, b, c in product(n, repeat=3)
    }
    sign = 1 if spec.conventions.curvature == "DCD_MINUS_DDC" else -1
    riemann = {}
    for a, b, c, d in product(n, repeat=4):
        if c >= d:
            continue
        value = sp.simplify(
            sign
            * (
                sp.diff(gamma[a, d, b], x[c])
                - sp.diff(gamma[a, c, b], x[d])
                + sum(gamma[a, c, e] * gamma[e, d, b] - gamma[a, d, e] * gamma[e, c, b] for e in n)
            )
        )
        if value != 0:
            riemann[a, b, c, d] = value
            riemann[a, b, d, c] = -value
    ricci = {
        (b, d): sp.simplify(sum(riemann.get((a, b, a, d), 0) for a in n))
        for b, d in product(n, repeat=2)
    }
    scalar = sp.simplify(sum(inverse[b, d] * ricci[b, d] for b, d in product(n, repeat=2)))
    einstein = {
        (b, d): sp.simplify(ricci[b, d] - metric[b, d] * scalar / 2)
        for b, d in product(n, repeat=2)
    }
    obligations = [
        ProofObligation(
            subject="Chart validity",
            automation="PARTIALLY_AUTOMATED",
            condition="Establish smoothness and nonsingularity on the entire declared chart.",
        ),
    ]
    if signature != "EXACT_DIAGONAL":
        obligations.append(
            ProofObligation(
                subject="Metric signature",
                automation="PARTIALLY_AUTOMATED",
                condition="Establish constant inertia on the chart, beyond any point sample.",
            )
        )
    return ComputationResult(
        status="COMPLETED",
        scope="Symbolic Levi-Civita curvature in the declared chart",
        output={
            "coordinates": spec.coordinates,
            "determinant": sp.sstr(determinant),
            "christoffel": _sparse(gamma),
            "riemann": _sparse(riemann),
            "ricci": _sparse(ricci),
            "scalar_curvature": sp.sstr(scalar),
            "einstein": _sparse(einstein),
            "metric_symmetry": "PASS",
            "signature_check": signature,
            "zero_components_omitted": True,
            "index_base": 0,
            "riemann_index_order": "R^a_bcd",
        },
        obligations=tuple(obligations),
        software={"sympy": sp.__version__, "mpmath": mp.__version__},
    )


_BOUNDARY = {
    Boundary.COMPACT_SUPPORT: "Establish vanishing flux outside the support for all times used.",
    Boundary.DECAY: "Establish sufficient decay and justify the limit of boundary flux.",
    Boundary.PERIODIC: "Justify cancellation of flux across the identified boundary faces.",
    Boundary.HOMOGENEOUS_DIRICHLET: "Verify time compatibility and the zero boundary trace of u_t.",
    Boundary.HOMOGENEOUS_NEUMANN: "Verify that the normal derivative vanishes on the boundary.",
}


def wave_energy(spec: WaveInput) -> ComputationResult:
    if spec.conventions.boundary not in _BOUNDARY:
        raise ResearchError("An explicit decay or boundary condition is required")
    if (
        spec.conventions.signature == Signature.RIEMANNIAN
        or spec.conventions.perturbation_order != 1
        or spec.conventions.background.casefold() not in {"flat", "minkowski"}
    ):
        raise ResearchError(
            "This tool assumes a first-order scalar wave on a Lorentzian background"
        )
    speed = sp.Rational(spec.wave_speed)
    if not 0 < speed <= 10**6:
        raise ResearchError("Wave speed must be positive and at most 1e6 in the declared units")
    t = sp.Symbol("t", real=True)
    space = sp.symbols(f"x1:{spec.spatial_dimension + 1}", real=True)
    u = sp.Function("u")(t, *space)
    u_t = sp.diff(u, t)
    gradient = tuple(sp.diff(u, coord) for coord in space)
    density = (u_t**2 + speed**2 * sum(item**2 for item in gradient)) / 2
    flux = tuple(-(speed**2) * u_t * item for item in gradient)
    wave = sp.diff(u, t, 2) - speed**2 * sum(sp.diff(u, coord, 2) for coord in space)
    residual = sp.simplify(
        sp.diff(density, t)
        + sum(sp.diff(item, coord) for item, coord in zip(flux, space, strict=True))
        - u_t * wave
    )
    return ComputationResult(
        status="COMPLETED" if residual == 0 else "FAILED",
        scope="Local flat scalar-wave energy identity by symbolic differentiation",
        output={
            "density": sp.sstr(density),
            "flux": [sp.sstr(item) for item in flux],
            "wave_operator": sp.sstr(wave),
            "local_identity_residual": sp.sstr(residual),
            "integrated_energy_conservation": "NOT_AUTOMATED",
        },
        obligations=(
            ProofObligation(
                subject="Boundary flux",
                automation="NOT_AUTOMATED",
                condition=_BOUNDARY[spec.conventions.boundary],
            ),
            ProofObligation(
                subject="Regularity",
                automation="NOT_AUTOMATED",
                condition="Justify commuting derivatives and integration by parts.",
            ),
        ),
        software={"sympy": sp.__version__},
    )
