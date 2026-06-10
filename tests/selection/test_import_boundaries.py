"""Import boundary tests for the Selection package.

Ensures the Selection package stays independent of Themis internals,
FastAPI, application planning, and SigMA integrations.
"""

import ast
import importlib
import os
from pathlib import Path
from typing import Sequence


FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "intent_fusion",
    "fastapi",
    "synapse.api",
    "synapse.integrations.sigma",
    "synapse.planning",
)

SELECTION_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "synapse" / "selection"


def _python_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".py"):
                result.append(Path(dirpath) / name)
    return result


def _import_names(node: ast.AST) -> Sequence[str]:
    """Extract top-level module names from import / import-from statements."""
    if isinstance(node, ast.Import):
        return tuple(alias.name.split(".")[0] for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        if node.module is None:
            return ()
        return (node.module.split(".")[0],)
    return ()


def test_selection_package_importable() -> None:
    """Verify the Selection package can be imported without side effects."""
    import synapse.selection  # noqa: F811

    assert synapse.selection is not None


def test_data_management_package_importable() -> None:
    """Verify the Data Management domain package can be imported."""
    import synapse.domains.data_management  # noqa: F811

    assert synapse.domains.data_management is not None


def test_selection_package_no_forbidden_imports() -> None:
    """Scan every Python file in synapse.selection for disallowed imports.

    Disallowed: intent_fusion, fastapi, synapse.api,
                synapse.integrations.sigma, synapse.planning
    """
    files = _python_files(SELECTION_PACKAGE)
    assert files, f"No Python files found in {SELECTION_PACKAGE}"

    violations: list[str] = []

    for filepath in files:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
        for node in ast.walk(tree):
            for name in _import_names(node):
                if name in FORBIDDEN_IMPORTS:
                    violations.append(
                        f"{filepath.name}: imports forbidden module '{name}'"
                    )

    assert not violations, (
        f"Selection package contains {len(violations)} forbidden import(s):\n"
        + "\n".join(violations)
    )


def test_selection_package_no_exports_yet() -> None:
    """Task 01: the __init__.py should not export unimplemented objects."""
    import synapse.selection as sel

    public = [n for n in dir(sel) if not n.startswith("_")]
    # Only built-in dunder attributes and standard package attrs are expected.
    # We allow __doc__, __path__, __spec__, __builtins__, __cached__, __file__,
    # __loader__, __name__, __package__, plus any standard Python machinery.
    unexpected = [n for n in public if n not in (
        "__doc__", "__file__", "__loader__", "__name__", "__package__",
        "__path__", "__spec__", "__builtins__", "__cached__",
    )]
    assert not unexpected, (
        f"Selection __init__.py should not export objects yet, "
        f"but exports: {unexpected}"
    )
