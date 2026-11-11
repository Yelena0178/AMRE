"""Validated inputs and explicit GR conventions."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from amre.research.models import Record, Text, VersionedRecord, unique_ids

Symbol = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,23}$")]
Expression = Annotated[Text, Field(max_length=300)]
Scalar = Annotated[
    str,
    StringConstraints(
        pattern=r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?$", max_length=50
    ),
]


class Signature(StrEnum):
    RIEMANNIAN = "RIEMANNIAN"
    MINUS_PLUS = "MINUS_PLUS"
    PLUS_MINUS = "PLUS_MINUS"


class Boundary(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    COMPACT_SUPPORT = "COMPACT_SUPPORT"
    DECAY = "DECAY"
    PERIODIC = "PERIODIC"
    HOMOGENEOUS_DIRICHLET = "HOMOGENEOUS_DIRICHLET"
    HOMOGENEOUS_NEUMANN = "HOMOGENEOUS_NEUMANN"


class GRConventions(VersionedRecord):
    version: Annotated[str, StringConstraints(pattern=r"^gr-1\.0$")] = "gr-1.0"
    signature: Signature
    curvature: Annotated[str, StringConstraints(pattern=r"^(DCD_MINUS_DDC|DDC_MINUS_DCD)$")] = (
        "DCD_MINUS_DDC"
    )
    connection: Annotated[str, StringConstraints(pattern=r"^LEVI_CIVITA$")] = "LEVI_CIVITA"
    coordinate_chart: Text
    chart_domain: Text
    background: Text
    gauge: Text
    boundary: Boundary
    function_space: Text
    regularity: Text
    units: Text
    perturbation_order: Annotated[int, Field(strict=True, ge=0, le=4)]


class GRInput(Record):
    conventions: GRConventions


class CurvatureInput(GRInput):
    coordinates: Annotated[tuple[Symbol, ...], Field(min_length=2, max_length=4)]
    parameters: Annotated[tuple[Symbol, ...], Field(max_length=4)] = ()
    metric: Annotated[tuple[tuple[Expression, ...], ...], Field(min_length=2, max_length=4)]
    sample: dict[Symbol, Scalar] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        unique_ids((*self.coordinates, *self.parameters), "coordinate or parameter")
        n = len(self.coordinates)
        if len(self.metric) != n or any(len(row) != n for row in self.metric):
            raise ValueError("metric dimensions must match the coordinates")
        if self.sample is not None and set(self.sample) != set(
            (*self.coordinates, *self.parameters)
        ):
            raise ValueError("signature sample must bind every coordinate and parameter")
        return self


class WaveInput(GRInput):
    spatial_dimension: Annotated[int, Field(strict=True, ge=1, le=3)] = 3
    wave_speed: Scalar = "1"


class NumericOperation(StrEnum):
    ODE = "ODE"
    INTEGRAL = "INTEGRAL"
    EIGENVALUES = "EIGENVALUES"


class NumericInput(GRInput):
    operation: NumericOperation
    expression: Expression | None = None
    interval: tuple[Scalar, Scalar] | None = None
    initial_value: Scalar | None = None
    matrix: Annotated[tuple[tuple[Scalar, ...], ...], Field(min_length=1, max_length=4)] | None = (
        None
    )
    precision_digits: Annotated[int, Field(strict=True, ge=15, le=80)] = 30
    steps: Annotated[int, Field(strict=True, ge=4, le=2048)] = 64
    rigorous_interval: bool = False

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.operation == NumericOperation.EIGENVALUES:
            if not self.matrix or any(len(row) != len(self.matrix) for row in self.matrix):
                raise ValueError("provide a square numeric matrix of size at most 4")
            if self.expression or self.interval or self.initial_value:
                raise ValueError("matrix computation does not accept scalar inputs")
        elif (
            not self.expression
            or not self.interval
            or self.matrix
            or (self.operation == NumericOperation.ODE) != (self.initial_value is not None)
        ):
            raise ValueError("provide expression and interval, and initial_value for an ODE")
        return self


class Dimension(Record):
    length: Annotated[int, Field(strict=True, ge=-16, le=16)] = 0
    time: Annotated[int, Field(strict=True, ge=-16, le=16)] = 0
    mass: Annotated[int, Field(strict=True, ge=-16, le=16)] = 0


class ConsistencyInput(GRInput):
    reference_curvature: (
        Annotated[str, StringConstraints(pattern=r"^(DCD_MINUS_DDC|DDC_MINUS_DCD)$")] | None
    ) = None
    tensor_rank: Annotated[int, Field(strict=True, ge=1, le=4)] | None = None
    indices: tuple[Annotated[int, Field(strict=True, ge=0, le=3)], ...] = ()
    coordinate_dimension: Annotated[int, Field(strict=True, ge=1, le=4)] | None = None
    dimension_left: Dimension | None = None
    dimension_right: Dimension | None = None
    scaling_weights: tuple[Annotated[int, Field(strict=True, ge=-16, le=16)], ...] = ()
    scaling_target: Annotated[int, Field(strict=True, ge=-16, le=16)] | None = None
    constraint_residual: Scalar | None = None
    residual_tolerance: Scalar | None = None
    max_perturbation_order: Annotated[int, Field(strict=True, ge=0, le=4)] | None = None

    @model_validator(mode="after")
    def _pairs(self) -> Self:
        for left, right in (
            (self.dimension_left, self.dimension_right),
            (self.constraint_residual, self.residual_tolerance),
            (self.tensor_rank, self.coordinate_dimension),
        ):
            if (left is None) != (right is None):
                raise ValueError("provide both sides of a scoped check")
        if self.indices and self.tensor_rank is None:
            raise ValueError("index checking requires tensor rank and coordinate dimension")
        if bool(self.scaling_weights) != (self.scaling_target is not None):
            raise ValueError("scaling check needs weights and a target")
        return self
