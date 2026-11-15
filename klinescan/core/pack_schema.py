"""
Declarative Vehicle/ROM Pack Schema & Safe AST Math Expression Evaluator.
Allows definition packs to define arbitrary scaling formulas safely without eval().
"""

import ast
from dataclasses import dataclass
import operator
from typing import Any, Dict, List, Optional, Tuple, Union

# Whitelisted AST operators mapped to standard operator functions
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Invert: operator.invert,
}


class SafeMathEvaluator(ast.NodeVisitor):
    def __init__(self, variables: Dict[str, Union[int, float]]):
        self.variables = variables

    def visit(self, node: ast.AST) -> Any:
        method = "visit_" + node.__class__.__name__
        visitor = getattr(self, method, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"Unsupported or unsafe expression element: {node.__class__.__name__}")

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Union[int, float]:
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    def visit_Name(self, node: ast.Name) -> Union[int, float]:
        name = node.id
        if name in self.variables:
            return self.variables[name]
        # Common byte aliases
        upper = name.upper()
        if upper in self.variables:
            return self.variables[upper]
        raise ValueError(f"Undefined variable in expression: {name}")

    def visit_BinOp(self, node: ast.BinOp) -> Union[int, float]:
        op_type = type(node.op)
        if op_type not in SAFE_OPERATORS:
            raise ValueError(f"Unsafe binary operator: {op_type.__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        op_fn = SAFE_OPERATORS[op_type]
        # Guard against zero division
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            return 0.0
        return op_fn(left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Union[int, float]:
        op_type = type(node.op)
        if op_type not in SAFE_OPERATORS:
            raise ValueError(f"Unsafe unary operator: {op_type.__name__}")
        operand = self.visit(node.operand)
        return SAFE_OPERATORS[op_type](operand)


def evaluate_safe_formula(expr: str, variables: Dict[str, Union[int, float]]) -> float:
    """
    Safely evaluates a mathematical formula using Python's AST parser.
    Whitelists arithmetic and bitwise operations; strictly prohibits function calls,
    imports, attributes, and execution.
    """
    clean_expr = expr.strip()
    if not clean_expr:
        return 0.0
    try:
        parsed = ast.parse(clean_expr, mode="eval")
        evaluator = SafeMathEvaluator(variables)
        result = evaluator.visit(parsed)
        return float(result)
    except Exception as exc:
        raise ValueError(f"Failed to evaluate formula '{expr}': {exc}") from exc


def evaluate_pid_bytes(formula: str, raw_bytes: bytes) -> float:
    """
    Maps raw bytes to variables A, B, C, D... or byte0, byte1... and computes formula.
    Example: formula = '((A * 256 + B) / 4)', raw_bytes = b'\x0D\xAC' -> 875.0
    """
    variables: Dict[str, Union[int, float]] = {}
    for i, b in enumerate(raw_bytes):
        # Letters A, B, C, D...
        if i < 26:
            var_name = chr(ord("A") + i)
            variables[var_name] = b
        variables[f"byte{i}"] = b
        variables[f"b{i}"] = b
    return evaluate_safe_formula(formula, variables)


@dataclass
class CustomPidDefinition:
    pid: int
    name: str
    formula: str
    unit: str
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    category: str = "Custom"

    def decode(self, raw_bytes: bytes) -> float:
        return evaluate_pid_bytes(self.formula, raw_bytes)


def parse_protocol(value) -> "ProtocolType":
    """Accept a ProtocolType value ("ISO 14230 (KWP2000 Fast Init)") or name ("KWP2000_FAST")."""
    from klinescan.core.models import ProtocolType

    try:
        return ProtocolType(value)
    except ValueError:
        by_name = {member.name: member for member in ProtocolType}
        key = str(value).strip().upper()
        if key in by_name:
            return by_name[key]
        raise ValueError(
            f"unknown protocol '{value}' (expected one of: {', '.join(by_name)})") from None


def parse_int(value, field: str = "value") -> int:
    """Pack numbers may be JSON integers or strings such as "0x7E0"."""
    if isinstance(value, bool):
        raise ValueError(f"{field}: expected a number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value.strip(), 0)
    raise ValueError(f"{field}: expected a number, got {value!r}")


def parse_hex_bytes(value, field: str = "payload") -> bytes:
    if value in (None, ""):
        return b""
    try:
        return bytes.fromhex(str(value))
    except ValueError:
        raise ValueError(f"{field}: '{value}' is not hex bytes") from None


_INT_FIELDS = ("baudrate", "slow_init_addr", "tester_addr", "ecu_addr", "header_format",
               "can_tx_id", "can_rx_id", "pin", "keep_alive_ms", "can_bitrate",
               "doip_logical_address", "security_level")


def validate_pack_dict(data: dict) -> Tuple[bool, List[str]]:
    """Strict schema validation for loaded JSON diagnostic packs."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["Root must be a JSON object"]

    for req_field in ("pack_id", "name", "version", "vehicles"):
        if req_field not in data:
            errors.append(f"Missing required pack field: '{req_field}'")

    vehicles = data.get("vehicles", [])
    if not isinstance(vehicles, list):
        errors.append("'vehicles' must be a list of vehicle objects")
        return False, errors
    for idx, v in enumerate(vehicles):
        if not isinstance(v, dict):
            errors.append(f"vehicles[{idx}] must be an object")
            continue
        if "label" not in v:
            errors.append(f"vehicles[{idx}] is missing required 'label'")
        targets = v.get("targets", [])
        if not isinstance(targets, list):
            errors.append(f"vehicles[{idx}].targets must be a list")
            continue
        for t_idx, t in enumerate(targets):
            where = f"vehicles[{idx}].targets[{t_idx}]"
            if not isinstance(t, dict):
                errors.append(f"{where} must be an object")
                continue
            if "name" not in t:
                errors.append(f"{where} missing 'name'")
            if "protocol" not in t:
                errors.append(f"{where} missing 'protocol'")
            else:
                try:
                    parse_protocol(t["protocol"])
                except ValueError as exc:
                    errors.append(f"{where}: {exc}")
            for key in _INT_FIELDS:
                if key in t:
                    try:
                        parse_int(t[key], key)
                    except ValueError as exc:
                        errors.append(f"{where}: {exc}")
            if t.get("five_baud_parity", "odd") not in ("odd", "none"):
                errors.append(f"{where}: five_baud_parity must be 'odd' or 'none'")
            if t.get("echo_mode", "") not in ("", "auto", "on", "off"):
                errors.append(f"{where}: echo_mode must be auto, on or off")
            for a_idx, act in enumerate(t.get("actuators", []) or []):
                for key in ("payload_start", "payload_stop"):
                    try:
                        parse_hex_bytes(act.get(key, ""), key)
                    except ValueError as exc:
                        errors.append(f"{where}.actuators[{a_idx}]: {exc}")
            for s_idx, sp in enumerate(t.get("special_functions", []) or []):
                try:
                    parse_hex_bytes(sp.get("command", ""), "command")
                except ValueError as exc:
                    errors.append(f"{where}.special_functions[{s_idx}]: {exc}")
            for l_idx, lp in enumerate(t.get("live_params", []) or []):
                lw = f"{where}.live_params[{l_idx}]"
                for key in ("name", "request", "offset", "formula"):
                    if key not in lp:
                        errors.append(f"{lw} missing '{key}'")
                try:
                    if not parse_hex_bytes(lp.get("request", ""), "request"):
                        errors.append(f"{lw}: empty request")
                    parse_int(lp.get("offset", 0), "offset")
                    evaluate_pid_bytes(str(lp.get("formula", "A")), bytes(8))
                except ValueError as exc:
                    errors.append(f"{lw}: {exc}")
    return len(errors) == 0, errors
