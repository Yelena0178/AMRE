"""Public metadata and validated input definitions for the GR domain pack."""

from amre.domains.gr.models import (
    ConsistencyInput,
    CurvatureInput,
    GRConventions,
    GRInput,
    NumericInput,
    WaveInput,
)
from amre.domains.models import ToolDescription

TOOL_VERSION = "gr-1.0"

INPUTS = {
    "gr.curvature": CurvatureInput,
    "gr.wave_energy": WaveInput,
    "gr.numeric": NumericInput,
    "gr.consistency": ConsistencyInput,
    "gr.lean": GRInput,
    "gr.xact": GRInput,
}

_SCOPES = {
    "gr.curvature": "Symbolic curvature of an explicit metric with two to four coordinates",
    "gr.wave_energy": "Local flat scalar-wave energy identity and boundary obligations",
    "gr.numeric": "Finite ODE, quadrature, and real-symmetric eigenvalue experiments",
    "gr.consistency": "Selected convention, index, dimension, and residual comparisons",
    "gr.lean": "Optional Lean/Mathlib adapter availability",
    "gr.xact": "Optional xAct/Wolfram adapter availability",
}


class GRDomainPack:
    domain_name = "gr"
    conventions = GRConventions
    definitions = {
        "curvature": (
            "R^a_bcd = d_c Gamma^a_db - d_d Gamma^a_cb + Gamma^a_ce Gamma^e_db "
            "- Gamma^a_de Gamma^e_cb"
        ),
        "ricci": "R_bd = R^a_bad",
        "einstein": "G_ab = R_ab - (R/2) g_ab",
    }
    available_tools = {
        name: ToolDescription(
            name=name, scope=_SCOPES[name], input_schema=model.model_json_schema()
        )
        for name, model in INPUTS.items()
    }
    consistency_checks = (
        "signature",
        "indices",
        "metric symmetry",
        "dimensions",
        "curvature sign",
        "residual",
        "scaling",
        "perturbation order",
    )
    benchmarks = ("minkowski", "unit-sphere", "flat-wave-energy")
    reasoning_guidelines = (
        "Specify the coordinate domain, gauge, regularity, and boundary before using identities.",
        "Track curvature signs and metric signatures when comparing sources.",
        "Use symbolic equalities and finite experiments as scoped computation evidence.",
    )
