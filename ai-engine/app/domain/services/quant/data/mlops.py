"""MLOps & Data Infrastructure.

- Feature store (versioned features with timestamps)
- Model registry (experiment tracking, model metadata)
- Data integrity checks
- Backfill orchestration
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional


@dataclass
class FeatureRecord:
    symbol: str
    feature_name: str
    value: float
    timestamp: datetime
    version: str = "v1"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelRecord:
    model_id: str
    version: str
    created_at: datetime
    parameters: dict[str, Any] = field(default_factory=dict)
    metrics_oos: dict[str, float] = field(default_factory=dict)
    status: str = "staging"


class FeatureStore:
    """Simple in-memory feature store with versioning."""

    def __init__(self):
        self._features: dict[str, list[FeatureRecord]] = {}

    def store(self, record: FeatureRecord) -> None:
        key = f"{record.symbol}_{record.feature_name}"
        self._features.setdefault(key, []).append(record)

    def get_latest(
        self, symbol: str, feature_name: str, as_of: Optional[datetime] = None
    ) -> Optional[float]:
        key = f"{symbol}_{feature_name}"
        records = self._features.get(key, [])
        if as_of:
            records = [r for r in records if r.timestamp <= as_of]
        if not records:
            return None
        return max(records, key=lambda r: r.timestamp).value

    def get_range(
        self, symbol: str, feature_name: str, start: datetime, end: datetime
    ) -> list[FeatureRecord]:
        key = f"{symbol}_{feature_name}"
        return [
            r for r in self._features.get(key, [])
            if start <= r.timestamp <= end
        ]


class ModelRegistry:
    """Model registry for tracking experiments and production models."""

    def __init__(self):
        self._models: dict[str, list[ModelRecord]] = {}

    def register(self, model: ModelRecord) -> None:
        self._models.setdefault(model.model_id, []).append(model)

    def promote_to_production(self, model_id: str, version: str) -> bool:
        records = self._models.get(model_id, [])
        for r in records:
            if r.version == version:
                r.status = "production"
                for other in records:
                    if other.version != version and other.status == "production":
                        other.status = "archived"
                return True
        return False

    def get_production_model(self, model_id: str) -> Optional[ModelRecord]:
        for r in self._models.get(model_id, []):
            if r.status == "production":
                return r
        return None


def check_data_integrity(df, min_rows: int = 10, max_null_pct: float = 0.3) -> dict:
    """Check if DataFrame passes basic integrity checks."""
    checks = {
        "passed": True,
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "null_max": None,
        "issues": [],
    }
    if len(df) < min_rows:
        checks["passed"] = False
        checks["issues"].append(f"Only {len(df)} rows, minimum is {min_rows}")
    for col in df.columns:
        null_pct = df[col].isnull().mean()
        if null_pct > max_null_pct:
            checks["passed"] = False
            checks["issues"].append(f"{col}: {null_pct:.1%} nulls exceeds {max_null_pct:.0%}")
            checks["null_max"] = max(checks["null_max"] or 0, null_pct)
    return checks
