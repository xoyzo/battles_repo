"""Restricted execution environment for dashboard-authored ability scripts.

Admins write plain Python in the Ability editor. It never touches the real
interpreter unrestricted: the source is AST-validated (no imports, no
dunder access, no exec/eval-style escapes) before being compiled, and run
with a minimal builtins allowlist plus a wall-clock timeout on a worker
thread so a runaway or malicious script can't block the bot or reach
outside the `AbilityContext` API it's handed.
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Any, Callable

from .ability_api import AbilityContext

log = logging.getLogger("battles.ability_sandbox")

HOOK_NAMES: tuple[str, ...] = (
    "execute",
    "battle_start",
    "before_turn",
    "after_turn",
    "before_action",
    "after_action",
    "on_attack",
    "on_defend",
    "on_damage_taken",
    "on_win",
    "on_loss",
)

_SAFE_BUILTIN_NAMES = (
    "abs", "min", "max", "round", "len", "range", "enumerate", "sum",
    "sorted", "reversed", "int", "float", "str", "bool", "dict", "list",
    "tuple", "set", "zip", "any", "all", "isinstance",
)
_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES if hasattr(builtins, name)
}
_SAFE_BUILTINS.update({"True": True, "False": False, "None": None})

_DISALLOWED_NODE_TYPES = (
    ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
    ast.With, ast.AsyncWith, ast.AsyncFunctionDef, ast.AsyncFor,
    ast.Delete, ast.ClassDef,
)
_DISALLOWED_NAMES = {"__import__", "eval", "exec", "compile", "open", "input", "vars", "globals", "locals", "getattr", "setattr", "delattr", "__builtins__"}

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ability-sandbox")
_SCRIPT_TIMEOUT_SECONDS = 0.25


class AbilityScriptError(Exception):
    """Raised when a script fails AST validation. Execution errors inside
    a validated script are caught and logged rather than raised, so one
    bad ability can't break a battle turn.
    """


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit(self, node: ast.AST) -> None:
        if isinstance(node, _DISALLOWED_NODE_TYPES):
            self.errors.append(f"{type(node).__name__} is not allowed in ability scripts.")
            return
        super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DISALLOWED_NAMES:
            self.errors.append(f"Use of '{node.id}' is not allowed.")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.errors.append(f"Dunder attribute access ('{node.attr}') is not allowed.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in _DISALLOWED_NAMES:
            self.errors.append(f"Calling '{node.func.id}' is not allowed.")
        self.generic_visit(node)


@dataclass
class CompiledAbility:
    code: Any
    source_hash: int


_COMPILE_CACHE: dict[int, CompiledAbility] = {}


def validate_script(source: str) -> list[str]:
    """Return a list of human-readable problems with `source`, empty if safe."""
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return [f"Syntax error: {exc}"]

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    return visitor.errors


def compile_script(source: str) -> CompiledAbility:
    source_hash = hash(source)
    cached = _COMPILE_CACHE.get(source_hash)
    if cached is not None:
        return cached

    errors = validate_script(source)
    if errors:
        raise AbilityScriptError("; ".join(errors))

    code = compile(source, filename="<ability_script>", mode="exec")
    compiled = CompiledAbility(code=code, source_hash=source_hash)
    _COMPILE_CACHE[source_hash] = compiled
    return compiled


def _run_sync(code: Any, hook_name: str, ctx: AbilityContext) -> bool:
    module_ns: dict[str, Any] = {}
    exec(code, {"__builtins__": _SAFE_BUILTINS}, module_ns)  # noqa: S102 - validated + sandboxed builtins
    hook_fn: Callable[[AbilityContext], None] | None = module_ns.get(hook_name)
    if callable(hook_fn):
        hook_fn(ctx)
        return True
    return False


async def run_hook(source: str, hook_name: str, ctx: AbilityContext) -> bool:
    """Compile (if needed) and run `hook_name` from an ability's script
    against `ctx`, off the event loop with a hard timeout. Returns True
    only if the hook function was actually defined in the script and ran
    without error.
    """
    if hook_name not in HOOK_NAMES:
        raise ValueError(f"Unknown ability hook: {hook_name}")
    if not source.strip():
        return False

    try:
        compiled = compile_script(source)
    except AbilityScriptError:
        log.exception("Ability script failed validation; skipping.")
        return False

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _run_sync, compiled.code, hook_name, ctx),
            timeout=_SCRIPT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning("Ability script hook %r timed out after %ss.", hook_name, _SCRIPT_TIMEOUT_SECONDS)
        return False
    except Exception:  # noqa: BLE001 - a broken ability script must never break a battle
        log.exception("Ability script hook %r raised.", hook_name)
        return False
