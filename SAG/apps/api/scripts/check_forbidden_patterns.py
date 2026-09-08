from __future__ import annotations

import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
SCAN_ROOTS = (
    WORKSPACE / "SAG" / "apps" / "api" / "sag_api",
    WORKSPACE / "SAG" / "apps" / "web" / "lib",
    WORKSPACE / "SAG" / "apps" / "web" / "components",
    WORKSPACE / "SAG" / "apps" / "web" / "app",
    WORKSPACE / "ai-engine" / "app",
)

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FinancialEntityType", re.compile(r"\bFinancialEntityType\b")),
    ("source ticker convention", re.compile(r"source_\{?ticker\}?", re.IGNORECASE)),
    ("BCTC ticker identifier", re.compile(r"BCTC_\{?ticker\}?", re.IGNORECASE)),
    ("SAG v1 consumer path", re.compile(r"/api/v1/(sources/by-ticker|analysis/by-ticker|gil/sources)")),
    ("content prefix embedding", re.compile(r"content\[:1024\]")),
    ("runtime create_all", re.compile(r"Base\.metadata\.create_all")),
    ("runtime ALTER TABLE", re.compile(r"ALTER\s+TABLE", re.IGNORECASE)),
    ("moat default 50", re.compile(r"get\(['\"]moat_score['\"],\s*50", re.IGNORECASE)),
    ("gil default PASS", re.compile(r"get\(['\"]gil_flag['\"],\s*['\"](?:PASS|UNKNOWN)['\"]|gil_flag[^\\n]{0,40}or\s+['\"](?:PASS|UNKNOWN)['\"]", re.IGNORECASE)),
)

EXCLUDED_PARTS = {
    ".git",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    "scripts",
    "tests",
}

EXCLUDED_PREFIXES = (
    "SAG/apps/api/sag_api/sag/",
    "SAG/apps/api/sag_api/api/v1/",
    "SAG/apps/api/sag_api/services/source_service.py",
    "SAG/apps/api/sag_api/services/analysis_v2_service.py",
    "SAG/apps/api/sag_api/services/gil_service.py",
    "SAG/apps/web/components/features/detail-panel.tsx",
    "ai-engine/app/infrastructure/database/migrations/",
)


def _excluded(path: Path) -> bool:
    rel = path.relative_to(WORKSPACE).as_posix()
    return any(part in path.parts for part in EXCLUDED_PARTS) or any(rel.startswith(part) for part in EXCLUDED_PREFIXES)


def main() -> None:
    failures: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        paths = root.rglob("*")
        for path in paths:
            if not path.is_file() or _excluded(path):
                continue
            if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".json", ".toml", ".prisma", ".sql"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for label, pattern in PATTERNS:
                if pattern.search(text):
                    failures.append(f"{path.relative_to(WORKSPACE)}: forbidden {label}")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print("forbidden-pattern check passed")


if __name__ == "__main__":
    main()
