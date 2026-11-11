import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from amre.benchmarks.models import BenchmarkResult
from amre.benchmarks.service import run_benchmark
from amre.dashboard.models import ResearchSnapshot
from amre.errors import ResearchError
from amre.execution.queue import load_policy
from amre.reports import generate_report
from amre.research.storage import read_yaml, yaml_text
from amre.scheduling.portfolio import load_settings
from amre.strategy.config import StrategyProfile, active_profile
from amre.strategy.service import StrategyRegistry

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _cli(workspace, *args, check=True, limit=90):
    return subprocess.run(
        [sys.executable, "-m", "amre", *args],
        cwd=workspace,
        env={**os.environ, "AMRE_WORKSPACE_ROOT": str(workspace), "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=limit,
        check=check,
    )


def test_fixed_known_math_suite_and_deep_short_comparison(tmp_path):
    pytest.importorskip("sympy")
    output = tmp_path / "bench"
    result = run_benchmark(EXAMPLES / "benchmarks.yaml", output, 19)
    assert result.engine == "OFFLINE_MOCK" and result.real_model_effect == "UNVERIFIED"
    assert result == BenchmarkResult.model_validate_json((output / "benchmark.json").read_text())
    assert {case.kind for case in result.cases} == {"B1", "B2", "B3"}
    for case in result.cases:
        assert all(case.checked.values())
        assert (output / case.computation).is_file()
        workflows = {item.variant: item for item in case.workflows}
        deep, short = workflows["deep"], workflows["short"]
        assert deep.model == short.model and deep.budget_ms == short.budget_ms
        assert deep.computation_checks == short.computation_checks == 1
        assert deep.formal_checks == short.formal_checks == 0
        assert deep.attempts == 1 and short.attempts > deep.attempts
        assert all(item.completed and item.state_restored for item in case.workflows)
        assert workflows["short-without-memory"].context_loss_events > short.context_loss_events
        assert all(0 < item.elapsed_ms <= item.budget_ms for item in case.workflows)
    wave = next(item for item in result.cases if item.kind == "B3")
    assert len(wave.ablations) == 3
    assert wave.ablations[1].repeated_failures == (1, 0)
    fixed, adaptive = wave.ablations[-1].trace
    assert len(fixed) == len(adaptive) == 3 and fixed[-1].startswith("A /")
    assert adaptive[-1].startswith("B /")
    registry = StrategyRegistry(tmp_path)
    proposal = registry.recommend(output / "benchmark.json", StrategyProfile(name="Baseline"))
    assert proposal.proposed.profile.prompt_template == "explicit-gaps-v2"
    assert proposal.proposed.profile.portfolio.stagnation_window == 2
    assert active_profile(tmp_path) is None


def test_strategy_evaluation_applies_a_version_and_rolls_back(tmp_path):
    pytest.importorskip("sympy")
    fixtures = tmp_path / "fixtures"
    shutil.copytree(EXAMPLES / "gr", fixtures / "gr")
    raw = read_yaml(EXAMPLES / "benchmarks.yaml")
    raw["cases"] = raw["cases"][:1]
    suite = fixtures / "suite.yaml"
    suite.write_text(yaml_text(raw), encoding="utf-8")
    registry = StrategyRegistry(tmp_path)
    default = registry.propose(StrategyProfile(name="Baseline"))
    profile = StrategyProfile.model_validate(read_yaml(EXAMPLES / "strategy" / "profile.yaml"))
    candidate = registry.propose(profile)
    assert registry.propose(profile) == candidate
    assert registry.compare(default.version_id, candidate.version_id)["context_bytes"] == {
        "before": 16000,
        "after": 18000,
    }
    assert active_profile(tmp_path) is None
    with pytest.raises(ResearchError):
        registry.apply(candidate.version_id, "EV-" + "0" * 24)
    evaluation = registry.evaluate(candidate.version_id, suite, tmp_path / "evaluation", 19)
    assert evaluation.eligible and all(evaluation.gates.values())
    with pytest.raises(ResearchError, match="passing evaluation"):
        registry.apply(default.version_id, evaluation.evaluation_id)
    applied = registry.apply(candidate.version_id, evaluation.evaluation_id)
    assert applied.version_id == load_settings(tmp_path).strategy.version
    assert load_policy(tmp_path).prompt_template == "explicit-gaps-v2"
    assert load_policy(tmp_path).max_context_bytes == 18000
    assert registry.rollback().version_id is None
    assert active_profile(tmp_path) is None and load_policy(tmp_path).max_context_bytes == 16000
    with pytest.raises(ResearchError, match="No prior"):
        registry.rollback()


def test_cli_demo_cold_restart_reports_dashboard_and_preserves_evidence(tmp_path):
    pytest.importorskip("sympy")
    work = tmp_path / "run"
    result = json.loads(
        _cli(
            tmp_path,
            "demo",
            "--output",
            str(work),
            "--examples",
            str(EXAMPLES),
        ).stdout
    )
    assert result["engine"] == "OFFLINE_MOCK" and result["active_route"] == "B"
    assert result["derived_problem"].startswith("Derived-")
    assert result["computation_evidence"]
    report = (work / result["reports"][0]).read_text(encoding="utf-8")
    assert "Current independently supported results\n\nNone recorded." in report
    assert "COMPUTATION_TESTED" in report and result["obstacle_id"] in report
    assert result["paper_id"] in report
    tex = (work / result["reports"][1]).read_text(encoding="utf-8")
    assert r"\begin{document}" in tex and r"\int_{\mathbb R^3}" in tex
    public = ResearchSnapshot.model_validate_json((work / result["dashboard"]).read_text())
    assert len(public.sessions) == 2 and public.checkpoints[-1].route_id == "B"
    assert any(event.kind == "ROUTE_SWITCHED" for event in public.events)
    assert any(item.properties == ("COMPUTATION_TESTED",) for item in public.artifacts)
    session = json.loads(_cli(work, "session", "inspect", result["session_id"]).stdout)
    assert session["semantic_events"] and session["reasoning_budget"]["tokens"] == 0
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from pathlib import Path;"
            " from amre.research.memory import ResearchMemory;"
            " s=ResearchMemory(Path.cwd()).restore('FlatWave');"
            " print(json.dumps([s.workspace_matches,s.checkpoint.checkpoint_id,"
            "s.checkpoint.active_route,s.checkpoint.active_obstacles,s.checkpoint.next_research_action,"
            "s.checkpoint.budget_snapshot.elapsed_seconds]))",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=35,
        check=True,
    )
    match, checkpoint, route, obstacles, next_action, elapsed = json.loads(process.stdout)
    assert match and checkpoint == result["checkpoint"] and route == "B"
    assert result["obstacle_id"] in obstacles and next_action and elapsed > 0


def test_latex_report_escapes_untrusted_commands(example_store):
    statement = r"Unsafe LaTeX source \(x + \input{private.tex}\) appears as quoted source."
    example_store.update("P-T", {}, statement=statement)
    markdown, latex = generate_report(example_store.root, "P-T", example_store.root / "report")
    assert statement in markdown.read_text(encoding="utf-8")
    content = latex.read_text(encoding="utf-8")
    assert r"\input{private.tex}" not in content
    assert r"\textbackslash{}input" in content
