"""The Calculator tool: exact arithmetic without eval(). The expression is parsed into a Python syntax tree
and only numbers, + - * / // % ** and parentheses (plus round/abs/min/max/sqrt) are allowed; anything else -
names, calls to other functions, attributes, strings - is refused. Exponents and result sizes are capped,
so a request cannot hang the api."""

import ast
import re
import math
import operator
from decimal import Decimal

MAX_LENGTH = 200
MAX_EXPONENT = 100
MAX_ABS_RESULT = 1e15

OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
FUNCTIONS = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt}


class CalculatorError(ValueError):
    pass


def _normalize(expression: str) -> str:
    """Accept what people and models write: '1,200 x 80%', '₹300', '18% of 4500'."""
    text = expression.strip().replace("₹", "").replace("Rs.", "").replace("Rs", "")
    text = text.replace("×", "*").replace("÷", "/").replace("^", "**")
    text = text.replace(" x ", " * ").replace(" X ", " * ")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)          # 1,200 -> 1200
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", text)  # 18% of 4500
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", text)           # 80% -> (80/100)
    return text


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculatorError(f"Exponent {right} is too large (max {MAX_EXPONENT}).")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise CalculatorError("Division by zero.")
        value = OPERATORS[type(node.op)](left, right)
    elif isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        value = UNARY[type(node.op)](_eval(node.operand))
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
          and not node.keywords and 1 <= len(node.args) <= 4):
        value = FUNCTIONS[node.func.id](*[_eval(a) for a in node.args])
    else:
        raise CalculatorError(f"Not allowed in a calculation: {ast.dump(node)[:60]}")
    if isinstance(value, complex) or abs(value) > MAX_ABS_RESULT:
        raise CalculatorError("The result is too large or not a real number.")
    return value


def evaluate(expression: str) -> dict:
    """Return {"expression", "normalized", "result"} or raise CalculatorError with a clear reason."""
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("Empty expression.")
    if len(expression) > MAX_LENGTH:
        raise CalculatorError(f"Expression longer than {MAX_LENGTH} characters.")
    normalized = _normalize(expression)
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Not a valid calculation: {expression!r}") from exc
    try:
        value = _eval(tree)
    except (OverflowError, ValueError) as exc:  # e.g. sqrt(-1)
        raise CalculatorError(str(exc) or "Invalid calculation.") from exc
    # Show money-like results with at most 4 decimals, without float noise (0.1+0.2 -> 0.3).
    result = float(Decimal(repr(value)).quantize(Decimal("0.0001")).normalize()) if isinstance(value, float) else value
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return {"expression": expression, "normalized": normalized, "result": result}
