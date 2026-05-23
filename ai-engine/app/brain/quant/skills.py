"""Skill loader for analysis skills."""

from pathlib import Path
from typing import Optional, Any

SKILLS_DIR = Path(__file__).parent / "skills_data"


class Skill:
    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path

    def execute(self, symbol: str, **kwargs) -> dict:
        return {"skill": self.name, "symbol": symbol, "status": "not_implemented"}


def load_skill(name: str) -> Optional[Skill]:
    skill_path = SKILLS_DIR / name
    if not skill_path.exists() or not skill_path.is_dir():
        return None
    return Skill(name, skill_path)
