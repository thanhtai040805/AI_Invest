"""Unit tests cho Cloudflare R2 Storage Adapter của ai-engine."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from app.domain.services.r2_storage import R2StorageService


def test_r2_service_s3_mode(monkeypatch):
    monkeypatch.delenv("R2_API_TOKEN", raising=False)
    monkeypatch.setattr("app.domain.services.r2_storage.HAS_BOTO3", True)
    monkeypatch.setenv("R2_ACCOUNT_ID", "46ef2c1ebc29131ed4f7727515ec96ca")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://46ef2c1ebc29131ed4f7727515ec96ca.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "mock_key_id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "mock_secret_key")
    monkeypatch.setenv("R2_BUCKET_NAME", "aiinvest-bctc-prod")

    service = R2StorageService()
    assert service.is_configured is True
    assert service.auth_mode == "s3"

    mock_s3 = MagicMock()
    service._s3_client = mock_s3

    results_pdf = service.upload_bctc_pruned_pdf(
        ticker="AAA",
        year=2025,
        quarter=4,
        scope="HN",
        pdf_source=b"%PDF-test",
    )
    assert results_pdf["key"] == "bctc/AAA/2025/Q4/AAA_2025_Q4_HN_pruned.pdf"
    assert results_pdf["status"] == "UPLOADED"

    results_md = service.upload_bctc_parsed_markdown(
        ticker="AAA",
        year=2025,
        quarter=4,
        scope="HN",
        markdown_content="# Header AAA 2025",
    )
    assert results_md["key"] == "bctc/AAA/2025/Q4/AAA_2025_Q4_HN_parsed.md"
    assert results_md["status"] == "UPLOADED"

    # Test delete_object
    deleted = service.delete_object(results_pdf["key"])
    assert deleted is True
    mock_s3.delete_object.assert_called_once_with(Bucket=service.bucket_name, Key=results_pdf["key"])


def test_r2_service_token_mode(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "46ef2c1ebc29131ed4f7727515ec96ca")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setenv("R2_TOKEN", "mock_cf_token_12345")
    monkeypatch.setenv("R2_BUCKET_NAME", "aiinvest-bctc-prod")

    service = R2StorageService()
    assert service.is_configured is True
    assert service.auth_mode == "rest_token"


def test_r2_service_environment_isolation(monkeypatch):
    """Kiểm tra tự động phân tách bucket: Local/Dev và PROD dùng chung aiinvest-bctc-prod, TEST dùng aiinvest-bctc-test."""
    monkeypatch.delenv("R2_BUCKET_NAME", raising=False)
    monkeypatch.delenv("R2_TEST_BUCKET_NAME", raising=False)
    monkeypatch.setenv("R2_ACCOUNT_ID", "mock_acc")
    monkeypatch.setenv("R2_TOKEN", "mock_tok")

    monkeypatch.setenv("ENVIRONMENT", "test")
    service_test = R2StorageService()
    assert service_test.bucket_name == "aiinvest-bctc-test"

    monkeypatch.setenv("ENVIRONMENT", "local")
    service_local = R2StorageService()
    assert service_local.bucket_name == "aiinvest-bctc-prod"

    monkeypatch.setenv("ENVIRONMENT", "prod")
    service_prod = R2StorageService()
    assert service_prod.bucket_name == "aiinvest-bctc-prod"
