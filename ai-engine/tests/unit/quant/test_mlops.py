import pytest
from datetime import datetime, date
from app.domain.services.quant.data.mlops import (
    FeatureStore,
    FeatureRecord,
    ModelRegistry,
    ModelRecord,
    check_data_integrity,
)
import pandas as pd


class TestFeatureStore:
    def test_store_and_retrieve(self):
        fs = FeatureStore()
        rec = FeatureRecord("VNM", "momentum", 0.5, datetime(2024, 1, 5))
        fs.store(rec)
        val = fs.get_latest("VNM", "momentum")
        assert val == 0.5

    def test_as_of_filter(self):
        fs = FeatureStore()
        fs.store(FeatureRecord("VNM", "momentum", 0.3, datetime(2024, 1, 5)))
        fs.store(FeatureRecord("VNM", "momentum", 0.6, datetime(2024, 1, 10)))
        val = fs.get_latest("VNM", "momentum", datetime(2024, 1, 8))
        assert val == 0.3

    def test_missing_return_none(self):
        fs = FeatureStore()
        assert fs.get_latest("VNM", "nonexistent") is None

    def test_get_range(self):
        fs = FeatureStore()
        fs.store(FeatureRecord("VNM", "momentum", 0.3, datetime(2024, 1, 5)))
        fs.store(FeatureRecord("VNM", "momentum", 0.5, datetime(2024, 1, 10)))
        fs.store(FeatureRecord("VNM", "momentum", 0.7, datetime(2024, 1, 15)))
        records = fs.get_range("VNM", "momentum", datetime(2024, 1, 8), datetime(2024, 1, 12))
        assert len(records) == 1
        assert records[0].value == 0.5


class TestModelRegistry:
    def test_register_and_promote(self):
        mr = ModelRegistry()
        m1 = ModelRecord("alpha_v1", "1.0", datetime(2024, 1, 1))
        m2 = ModelRecord("alpha_v1", "2.0", datetime(2024, 2, 1))
        mr.register(m1)
        mr.register(m2)
        assert mr.promote_to_production("alpha_v1", "2.0") is True
        prod = mr.get_production_model("alpha_v1")
        assert prod is not None
        assert prod.version == "2.0"
        assert prod.status == "production"

    def test_promote_nonexistent(self):
        mr = ModelRegistry()
        assert mr.promote_to_production("nonexistent", "1.0") is False

    def test_get_production_none(self):
        mr = ModelRegistry()
        m = ModelRecord("test", "1.0", datetime(2024, 1, 1))
        mr.register(m)
        assert mr.get_production_model("test") is None


class TestCheckDataIntegrity:
    def test_passes_good_data(self):
        df = pd.DataFrame({"A": range(20), "B": range(20)})
        result = check_data_integrity(df, min_rows=10)
        assert result["passed"] is True

    def test_fails_too_few_rows(self):
        df = pd.DataFrame({"A": range(3)})
        result = check_data_integrity(df, min_rows=10)
        assert result["passed"] is False

    def test_fails_too_many_nulls(self):
        df = pd.DataFrame({"A": [1] + [None] * 10})
        result = check_data_integrity(df, min_rows=5, max_null_pct=0.3)
        assert result["passed"] is False
