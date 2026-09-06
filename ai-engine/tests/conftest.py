"""Global Pytest Configuration and Environment Isolation Fixtures."""

import os
import pytest

# Thiết lập biến môi trường ngay khi pytest khởi động
os.environ["ENVIRONMENT"] = "test"
os.environ["R2_TEST_BUCKET_NAME"] = "aiinvest-bctc-test"


@pytest.fixture(autouse=True, scope="session")
def isolate_test_environment():
    """Đảm bảo mọi test trong session đều chạy trong môi trường test an toàn."""
    os.environ["ENVIRONMENT"] = "test"
    os.environ["R2_TEST_BUCKET_NAME"] = "aiinvest-bctc-test"
    yield
