"""Parse a small real-expression language without evaluating source code."""

import ast
import math
import operator

import sympy as sp

from amre.errors import ResearchError

_FUNCTIONS = {item.__name__: item for item in (sp.sin, sp.cos, sp.exp, sp.sqrt, sp.log)}
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def parse_expression(text: str, symbols: dict[str, sp.Symbol]) -> sp.Expr:
    if set(symbols) & {*_FUNCTIONS, "pi"}:
        raise ResearchError("Coordinate or parameter uses a reserved math name")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ResearchError("Invalid expression syntax") from None
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ResearchError("Expression complexity exceeds 80 syntax nodes")

    def visit(node):
        if isinstance(node, ast.Constant):
            value = node.value
            if type(value) is int and abs(value) <= 10**9:
                return sp.Integer(value)
            if type(value) is float and math.isfinite(value) and 1e-12 <= abs(value) <= 1e9:
                return sp.Rational(repr(value))
            if type(value) is float and value == 0.0:
                return sp.Integer(0)
        if isinstance(node, ast.Name):
            if node.id in symbols:
                return symbols[node.id]
            if node.id == "pi":
                return sp.pi
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = visit(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and (not right.is_Integer or abs(right) > 8):
                raise ResearchError("Powers must be integers from -8 through 8")
            return _BINARY[type(node.op)](left, right)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
            and len(node.args) == 1
            and not node.keywords
        ):
            return _FUNCTIONS[node.func.id](visit(node.args[0]))
        raise ResearchError("Expression uses an unsupported name, literal, or operation")

    value = visit(tree.body)
    if value.has(sp.zoo, sp.nan, sp.oo, -sp.oo) or value.is_real is False:
        raise ResearchError("Expression is not a finite real expression on its declared domain")
    return value
