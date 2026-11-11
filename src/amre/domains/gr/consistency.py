"""Mechanical checks over explicitly declared inputs and proof obligations."""

from decimal import Decimal

from amre.domains.gr.models import ConsistencyInput
from amre.domains.models import ComputationResult, ProofObligation
from amre.errors import ResearchError


def consistency(spec: ConsistencyInput) -> ComputationResult:
    checks = {}

    def compare(name: str, condition: bool) -> None:
        checks[name] = "PASS" if condition else "FAIL"

    if spec.reference_curvature is not None:
        compare("curvature_convention", spec.reference_curvature == spec.conventions.curvature)
    if spec.tensor_rank is not None:
        compare(
            "indices",
            len(spec.indices) == spec.tensor_rank
            and all(index < spec.coordinate_dimension for index in spec.indices),
        )
    if spec.dimension_left is not None:
        compare("dimensions", spec.dimension_left == spec.dimension_right)
    if spec.scaling_target is not None:
        compare("scaling", sum(spec.scaling_weights) == spec.scaling_target)
    if spec.constraint_residual is not None:
        tolerance = Decimal(spec.residual_tolerance)
        if tolerance < 0:
            raise ResearchError("Residual tolerance must be nonnegative")
        compare("residual_at_supplied_value", abs(Decimal(spec.constraint_residual)) <= tolerance)
    if spec.max_perturbation_order is not None:
        compare(
            "perturbation_order", spec.conventions.perturbation_order <= spec.max_perturbation_order
        )
    return ComputationResult(
        status="FAILED" if "FAIL" in checks.values() else "COMPLETED" if checks else "UNAVAILABLE",
        scope="Mechanical consistency of declared inputs",
        output={"checks": checks},
        obligations=tuple(
            ProofObligation(
                subject=subject,
                automation="NOT_AUTOMATED",
                condition=condition,
            )
            for subject, condition in (
                (
                    "Gauge compatibility",
                    "Prove compatibility with the chosen gauge and initial data.",
                ),
                (
                    "Constraint propagation",
                    "Derive propagation for the chosen Einstein-matter system.",
                ),
                ("Boundary regularity", "Check the required trace and function-space conditions."),
            )
        ),
    )
