"""Не допускает выпуск пакета с версией, отличающейся от тега и публичного API."""

from __future__ import annotations

import ast
import os
import tomllib
from pathlib import Path


def check_release(root: Path, tag: str) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    tree = ast.parse((root / "src/funora/__init__.py").read_text())
    exported = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    )
    if tag != f"v{version}" or exported != version:
        raise ValueError(f"версии расходятся: тег={tag!r}, пакет={version!r}, API={exported!r}")
    return str(version)


if __name__ == "__main__":
    print(check_release(Path(__file__).resolve().parents[1], os.environ["FUNORA_RELEASE_TAG"]))
