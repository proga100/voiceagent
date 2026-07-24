"""Dump the generated OpenAPI schema to a JSON file.

Usage (from backend/):
    python scripts/export_openapi.py [out_path]

Default out_path is <repo_root>/docs/openapi.json, next to
multichat_contract.md, so the REST + WS contract can be diffed in git and
shared with the mobile team without running the server.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402


def main() -> None:
    default_out = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default_out
    schema = create_app().openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out} ({len(schema['components']['schemas'])} schemas)")


if __name__ == "__main__":
    main()
