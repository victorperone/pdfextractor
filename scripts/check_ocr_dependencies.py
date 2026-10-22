"""AST gate for engine ownership, including literal dynamic/relative imports.

Composition roots (factory/registry) may name engines. Neutral new pipeline and
common mechanisms may not depend on an engine implementation. Legacy modules
retain their paths and existing dependency direction until their migration.
"""
from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from structured_pdf_text.ocr.registry import public_engines

OCR = "structured_pdf_text.ocr"
PUBLIC_ENGINES = public_engines()
GLOBAL_PATHS = (
    "src/structured_pdf_text/__init__.py", "src/structured_pdf_text/api.py",
    "src/structured_pdf_text/config.py", "src/structured_pdf_text/cli.py",
    "src/structured_pdf_text/ocr/__init__.py", "src/structured_pdf_text/ocr/contracts.py",
    "src/structured_pdf_text/ocr/engine.py", "src/structured_pdf_text/ocr/factory.py",
    "src/structured_pdf_text/ocr/registry.py", "src/structured_pdf_text/ocr/readiness.py",
    "src/structured_pdf_text/ocr/recovery.py", "src/structured_pdf_text/ocr/coordinates.py",
    "src/structured_pdf_text/ocr/image_views.py", "src/structured_pdf_text/ocr/pipeline/",
    "src/structured_pdf_text/ocr/common/", "src/structured_pdf_text/fusion/",
    "src/structured_pdf_text/tables/", "src/structured_pdf_text/assemble/",
    "src/structured_pdf_text/geometry.py", "pyproject.toml", ".github/workflows/",
)


def owner(module: str) -> str | None:
    for prefix in (f"{OCR}.backends.", f"{OCR}.pipelines."):
        if module.startswith(prefix):
            name = module[len(prefix):].split(".")[0]
            return None if name.startswith("_") else name
    if module in (f"{OCR}.paddle", f"{OCR}._paddle_subprocess_worker", f"{OCR}.models", f"{OCR}.runtime_policy") or module.startswith(f"{OCR}.paddle_"):
        return "paddle"
    return None


def violations(source: str, module: str, *, package: bool = False) -> list[str]:
    tree = ast.parse(source)
    package_name = module if package else module.rpartition(".")[0]
    own = owner(module)
    neutral = module in (f"{OCR}.backends", f"{OCR}.pipelines") or module.startswith(f"{OCR}.backends._") or any(module == prefix or module.startswith(prefix + ".") for prefix in (f"{OCR}.pipeline", f"{OCR}.common"))
    # Include all core consumers, not just files beneath ocr/. CLI setup and
    # legacy config aliases remain explicit composition boundaries.
    neutral |= (module.startswith("structured_pdf_text.")
                and module != OCR
                and not module.startswith(OCR + ".")
                and module not in ("structured_pdf_text.cli", "structured_pdf_text.config"))
    dynamic_names = {"__import__"}
    importlib_names = {"importlib"}
    builtins_names = {"builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_names.update(alias.asname or alias.name for alias in node.names if alias.name == "importlib")
            builtins_names.update(alias.asname or alias.name for alias in node.names if alias.name == "builtins")
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic_names.update(a.asname or a.name for a in node.names if a.name == "import_module")
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            dynamic_names.update(a.asname or a.name for a in node.names if a.name == "__import__")
    found = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_name("."*node.level+(node.module or ""), package_name) if node.level else node.module or ""
            targets = [base] + [base+"."+alias.name for alias in node.names]
        elif isinstance(node, ast.Call):
            first = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "name"), None)
            function = node.func
            dynamic = isinstance(function, ast.Name) and function.id in dynamic_names
            dynamic |= isinstance(function, ast.Attribute) and function.attr == "import_module" and isinstance(function.value, ast.Name) and function.value.id in importlib_names
            dynamic |= isinstance(function, ast.Attribute) and function.attr == "__import__" and isinstance(function.value, ast.Name) and function.value.id in builtins_names
            if dynamic and isinstance(first, ast.Constant) and isinstance(first.value, str):
                name = first.value
                second = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "package"), None)
                dynamic_package = second.value if isinstance(second, ast.Constant) and isinstance(second.value, str) else package_name
                targets = [resolve_name(name, dynamic_package) if name.startswith(".") else name]
        for target in targets:
            other = owner(target)
            runtime = target.split(".")[0] in {"torch", "easyocr", "paddle", "paddleocr", "paddlex", "rapidocr", "onnxruntime", "openvino", "pytesseract"}
            # General inference runtimes can support a fifth engine. OCR
            # libraries themselves and private engine implementations cannot
            # be borrowed across families.
            family = {"easyocr":"easyocr", "paddleocr":"paddle", "paddlex":"paddle", "rapidocr":"rapidocr", "pytesseract":"tesseract"}.get(target.split(".")[0])
            if (other is not None and (neutral or own is not None and other != own)) or neutral and runtime or own is not None and family is not None and family != own:
                found.append(f"{module}:{node.lineno}: forbidden dependency on {target}")
    return sorted(set(found))


def regression_engines(paths) -> tuple[str, ...]:
    """Core controls always run; shared edits additionally require all engines."""
    selected = set()
    for path in paths:
        path = str(path).replace("\\", "/")
        if any(path == p or p.endswith("/") and path.startswith(p) for p in GLOBAL_PATHS):
            return PUBLIC_ENGINES
        module = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        engine = owner(module)
        if engine:
            selected.add(engine)
        elif path.startswith("src/"):
            return PUBLIC_ENGINES  # Unclassified production edits are global.
    return tuple(engine for engine in PUBLIC_ENGINES if engine in selected)


def check_tree(root: Path) -> list[str]:
    result = []
    for path in sorted((root/"structured_pdf_text").rglob("*.py")):
        name = ".".join(path.relative_to(root).with_suffix("").parts)
        package = path.name == "__init__.py"
        if package:
            name = name.removesuffix(".__init__")
        result.extend(violations(path.read_text(encoding="utf-8"), name, package=package))
    return result


if __name__ == "__main__":
    errors = check_tree(Path(__file__).resolve().parents[1]/"src")
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
