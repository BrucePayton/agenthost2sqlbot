import ast
from pathlib import Path

ALLOWED = {
    Path("app/runtime/claude.py"),
    Path("app/agui/claude_tools.py"),
}


def test_claude_sdk_imports_are_confined_to_adapter() -> None:
    offenders: list[Path] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(
                name.split(".", 1)[0] == "claude_agent_sdk" for name in names
            ) and path not in ALLOWED:
                offenders.append(path)

    assert offenders == []
