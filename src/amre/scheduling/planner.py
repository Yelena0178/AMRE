"""Pure, replayable ranking and exploration allocation."""

from hashlib import sha256

from amre.execution.models import BranchDispatch
from amre.scheduling.models import DecisionRecord, PlanningInput


def choose(inputs: PlanningInput) -> DecisionRecord:
    """Rank eligible candidates and allocate serial attempts using share debt."""
    count = sum(inputs.dispatched.values())
    shares = inputs.strategy.shares()
    debt = {
        kind: share * (count + 1) - 100 * inputs.dispatched.get(kind, 0)
        for kind, share in shares.items()
    }
    identity = "DR-" + sha256(inputs.model_dump_json().encode()).hexdigest()[:32]
    eligible = [
        item
        for item in inputs.candidates
        if not item.exclusions and item.reservation_ms and shares[item.allocation]
    ]

    def rank(item):
        tie = sha256(f"{inputs.seed}/{inputs.position}/{item.branch.key}".encode()).hexdigest()
        return item.continuity_priority, debt[item.allocation], item.score.total, tie

    shortlist = sorted(eligible, key=rank, reverse=True)[: inputs.strategy.max_active_branches]
    if not shortlist:
        return DecisionRecord(
            decision_id=identity,
            inputs=inputs,
            shortlisted=(),
            allocation_debt=debt,
            reason="No schedulable branch within graph, cooldown and budget constraints",
        )
    selected = shortlist[0]
    branch = selected.branch
    reason = (
        "Continue the current route after mathematical progress or one scoped obstacle"
        if selected.continuity_priority
        else f"{selected.allocation} debt {debt[selected.allocation]}, "
        f"heuristic score {selected.score.total}; seed resolves tied candidates"
    )
    return DecisionRecord(
        decision_id=identity,
        inputs=inputs,
        shortlisted=tuple(item.branch.key for item in shortlist),
        allocation_debt=debt,
        selected=branch.key,
        reason=reason,
        dispatch=BranchDispatch(
            decision_id=identity,
            problem_id=branch.problem_id,
            route_id=branch.route_id,
            objective=branch.next_action,
            strategy_version=inputs.strategy.version,
            graph_fingerprint=inputs.graph_fingerprint,
            limits=selected.limits,
            max_attempt_ms=selected.reservation_ms,
        ),
    )
