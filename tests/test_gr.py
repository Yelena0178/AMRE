import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from amre.domains.gr.service import GRComputations
from amre.execution.adapters import MockModelAdapter
from amre.execution.deep import DeepResearchRunner
from amre.research.storage import read_yaml, yaml_text

pytest.importorskip("sympy")
pytest.importorskip("mpmath")

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "gr"


def _input(name):
    return read_yaml(EXAMPLES / f"{name}.yaml")


def test_curvature_minkowski_and_sphere_with_signed_convention(tmp_path):
    service = GRComputations(tmp_path)
    flat = service.run("gr.curvature", _input("minkowski"))
    assert flat.result.status == "COMPLETED"
    assert flat.result.output["scalar_curvature"] == "0"
    assert all(flat.result.output[key] == {} for key in ("christoffel", "riemann", "einstein"))
    assert flat.result.output["signature_check"] == "EXACT_DIAGONAL"
    sphere_input = _input("unit-sphere")
    sphere = service.run("gr.curvature", sphere_input)
    assert sphere.result.status == "COMPLETED"
    assert sphere.result.output["scalar_curvature"] == "2"
    assert sphere.result.output["signature_check"] == "NUMERIC_SAMPLE_ONLY"
    assert sphere.result.obligations
    assert service.show(sphere.computation_id) == sphere
    assert sphere.conventions["version"] == "gr-1.0"
    sphere_input["conventions"]["curvature"] = "DDC_MINUS_DCD"
    flipped = service.run("gr.curvature", sphere_input)
    assert flipped.result.output["scalar_curvature"] == "-2"


def test_wave_local_identity_and_failure_archived_in_proof_graph(example_store):
    service = GRComputations(example_store.root)
    claim_status = example_store.load_contract("P-T").claim_status
    payload = _input("flat-wave-energy")
    wave = service.run("gr.wave_energy", payload, problem_id="P-T", assertion_id="L1")
    assert wave.result.status == "COMPLETED"
    assert wave.result.output["local_identity_residual"] == "0"
    assert wave.result.output["integrated_energy_conservation"] == "NOT_AUTOMATED"
    assert {item.subject for item in wave.result.obligations} == {"Boundary flux", "Regularity"}
    evidence = example_store.graph().evidence_for("L1")[0]
    assert evidence.file_path == wave.path and evidence.properties == ("COMPUTATION_TESTED",)
    assert evidence.coverage == "PARTIAL" and not evidence.applies(1, "SUPPORT")
    payload["conventions"]["boundary"] = "NOT_APPLICABLE"
    failed = service.run("gr.wave_energy", payload, problem_id="P-T")
    assert failed.result.status == "FAILED" and "boundary" in failed.result.detail.lower()
    graph = example_store.graph()
    obstacle = graph.node(f"Result-{failed.computation_id}").obstruction
    assert obstacle.step.file == failed.path
    assert graph.problems_affected_by_obstacle(obstacle.obstacle_id) == ("P-T",)
    assert example_store.load_contract("P-T").claim_status == claim_status


def test_numerical_refinement_eigenvalues_and_interval_boundary(tmp_path):
    service = GRComputations(tmp_path)
    conventions = _input("minkowski")["conventions"]
    base = {"conventions": conventions, "precision_digits": 35, "steps": 32}
    ode = service.run(
        "gr.numeric",
        {
            **base,
            "operation": "ODE",
            "expression": "y",
            "interval": ["0", "1"],
            "initial_value": "1",
        },
    )
    assert ode.result.status == "COMPLETED"
    assert ode.result.output["fine_steps"] == 64 and ode.result.output["precision_digits"] == 35
    assert abs(float(ode.result.output["fine"]) - 2.718281828459045) < 3e-9
    integral = service.run(
        "gr.numeric",
        {
            **base,
            "operation": "INTEGRAL",
            "expression": "x**2",
            "interval": ["0", "1"],
        },
    )
    assert abs(float(integral.result.output["fine"]) - 1 / 3) < 3e-5
    spectral = {**base, "operation": "EIGENVALUES", "matrix": [["2", "1"], ["1", "2"]]}
    eigen = service.run("gr.numeric", spectral)
    assert [float(item) for item in eigen.result.output["values"]] == [1.0, 3.0]
    assert float(eigen.result.output["max_residual"]) < 1e-30
    unsupported = service.run("gr.numeric", {**spectral, "rigorous_interval": True})
    assert unsupported.result.status == "UNAVAILABLE" and not unsupported.result.output


def test_invalid_metric_expression_and_consistency_checks(tmp_path):
    service = GRComputations(tmp_path)
    metric = _input("unit-sphere")
    metric["metric"][1][1] = "__import__('os').getcwd()"
    result = service.run("gr.curvature", metric)
    assert result.result.status == "FAILED" and "unsupported" in result.result.detail
    metric["metric"][1][1] = "0"
    singular = service.run("gr.curvature", metric)
    assert "singular" in singular.result.detail
    checks = service.run(
        "gr.consistency",
        {
            "conventions": metric["conventions"],
            "reference_curvature": "DDC_MINUS_DCD",
            "tensor_rank": 2,
            "indices": [0, 2],
            "coordinate_dimension": 2,
            "dimension_left": {"length": 1},
            "dimension_right": {"length": 1},
            "scaling_weights": [1, -2],
            "scaling_target": -1,
            "constraint_residual": "1e-8",
            "residual_tolerance": "1e-5",
            "max_perturbation_order": 0,
        },
    )
    assert checks.result.status == "FAILED"
    assert checks.result.output["checks"] == {
        "curvature_convention": "FAIL",
        "indices": "FAIL",
        "dimensions": "PASS",
        "scaling": "PASS",
        "residual_at_supplied_value": "PASS",
        "perturbation_order": "PASS",
    }
    assert "Gauge compatibility" in {item.subject for item in checks.result.obligations}
    assert service.run("gr.lean", {"conventions": metric["conventions"]}).result.status == (
        "UNAVAILABLE"
    )


def test_gr_tool_runs_inside_session_and_reaches_checkpoint_evidence(example_store):
    root = example_store.root
    (root / "research" / "execution.yaml").write_text(
        yaml_text(
            {
                "deep": {"tools": ["gr.curvature"], "session_tool_calls": 1},
                "workspace": {"attempts": 4, "seconds": 90, "tool_calls": 1},
                "problem": {"attempts": 3, "seconds": 60, "tool_calls": 1},
            }
        ),
        encoding="utf-8",
    )
    seen = []

    class Model(MockModelAdapter):
        def run_deep_with_tools(self, task, *, call_tool, on_event, **options):
            value = call_tool(
                {
                    "name": "gr.curvature",
                    "request_id": "Flat",
                    "arguments": {**_input("minkowski"), "assertion_id": "L1"},
                }
            )
            assert value.status == "COMPLETED" and value.value["output"]["riemann"] == {}
            seen.append(value.value["computation_id"])
            return self.run_deep(task, on_event=on_event, **options)

    runner = DeepResearchRunner(root)
    result = runner.start("P-T", Model())
    assert result.state == "COMPLETED" and len(seen) == 1
    session = runner.inspect(result.deep_session_id)
    restored = runner.runner.memory.restore("P-T", session.last_checkpoint)
    computation = GRComputations(root).show(seen[0])
    evidence = restored.graph.evidence_for("L1")[0]
    assert evidence.file_path == computation.path
    assert computation.attempt_id == result.attempt_id
    assert evidence.evidence_id in restored.checkpoint.evidence_references
    assert json.loads(restored.read_text(evidence.file_path))["computation_id"] == seen[0]
    assert runner.runner.queue.read().reservations[0].usage.tool_calls == 1


def test_gr_cli_can_replay_a_scope_without_adding_evidence(tmp_path):
    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "amre", "gr", *args],
            cwd=tmp_path,
            env={**os.environ, "AMRE_WORKSPACE_ROOT": str(tmp_path), "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    original = cli("run", "gr.curvature", str(EXAMPLES / "unit-sphere.yaml"))
    assert cli("show", original["computation_id"]) == original
    second = cli("replay", original["computation_id"])
    assert second["result"] == original["result"] and not second["assertion_id"]
    assert second["computation_id"] != original["computation_id"]
