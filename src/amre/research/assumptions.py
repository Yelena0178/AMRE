"""Explicit assumption sets, entailment rules, and incompatibilities."""

from collections.abc import Iterable
from enum import StrEnum

from amre.errors import ResearchError
from amre.research.models import Identifier, Record, Text, unique_ids


class AssumptionKind(StrEnum):
    MATHEMATICAL = "MATHEMATICAL"
    PHYSICAL = "PHYSICAL"


class AssumptionSet(Record):
    id: Identifier
    members: tuple[Identifier, ...]


class Implication(Record):
    premises: tuple[Identifier, ...]
    conclusion: Identifier
    basis: Text


class Conflict(Record):
    left: Identifier
    right: Identifier
    basis: Text


class AssumptionDocument(Record):
    sets: tuple[AssumptionSet, ...] = ()
    implications: tuple[Implication, ...] = ()
    conflicts: tuple[Conflict, ...] = ()


class AssumptionLattice:
    def __init__(self, assumption_ids: Iterable[str], document: AssumptionDocument):
        self._ids = frozenset(assumption_ids)
        self._document = AssumptionDocument.model_validate(document)
        self._sets = {item.id: item.members for item in self._document.sets}
        try:
            unique_ids(tuple(item.id for item in self._document.sets), "assumption set IDs")
            for members in self._sets.values():
                unique_ids(members, "assumptions in a set")
            for rule in self._document.implications:
                unique_ids(rule.premises, "implication premises")
        except ValueError as exc:
            raise ResearchError(str(exc)) from None

        references = {item for members in self._sets.values() for item in members}
        references.update(
            item
            for rule in self._document.implications
            for item in (*rule.premises, rule.conclusion)
        )
        references.update(
            item
            for conflict in self._document.conflicts
            for item in (conflict.left, conflict.right)
        )
        self._known(references)
        rules = {
            (frozenset(rule.premises), rule.conclusion) for rule in self._document.implications
        }
        if len(rules) != len(self._document.implications):
            raise ResearchError("Duplicate assumption implication")
        self._conflicts = {
            (min(item.left, item.right), max(item.left, item.right))
            for item in self._document.conflicts
        }
        if len(self._conflicts) != len(self._document.conflicts):
            raise ResearchError("Duplicate assumption conflict")
        if any(left == right for left, right in self._conflicts):
            raise ResearchError("An assumption cannot conflict with itself")

    def _known(self, assumptions: Iterable[str]) -> set[str]:
        values = set(assumptions)
        if unknown := values - self._ids:
            raise ResearchError("Unknown assumption: " + ", ".join(sorted(unknown)))
        return values

    def members(self, set_id: str) -> tuple[str, ...]:
        if set_id not in self._sets:
            raise ResearchError(f"Unknown assumption set: {set_id}")
        return self._sets[set_id]

    def closure(self, assumptions: Iterable[str]) -> frozenset[str]:
        available = self._known(assumptions)
        while True:
            inferred = {
                rule.conclusion
                for rule in self._document.implications
                if available.issuperset(rule.premises)
            }
            if inferred <= available:
                return frozenset(available)
            available.update(inferred)

    def conflicts(self, assumptions: Iterable[str]) -> tuple[tuple[str, str], ...]:
        available = self.closure(assumptions)
        return tuple(sorted(pair for pair in self._conflicts if available.issuperset(pair)))

    def implies(self, premises: tuple[str, ...], conclusion: str) -> bool | None:
        """Return None when no entailment or incompatibility is declared."""
        self._known((conclusion,))
        available = self.closure(premises)
        if self.conflicts(available):
            return None
        if conclusion in available:
            return True
        if self.conflicts(available | {conclusion}):
            return False
        return None
