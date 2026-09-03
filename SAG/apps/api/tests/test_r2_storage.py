"""Unit tests cho Cloudflare R2 Storage Adapter của phân hệ SAG (SagR2StorageClient)."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from sag_api.services.r2_storage import SagR2StorageClient


def test_sag_r2_service_s3_mode(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "46ef2c1ebc29131ed4f7727515ec96ca")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://46ef2c1ebc29131ed4f7727515ec96ca.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "mock_key_id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "mock_secret_key")
    monkeypatch.setenv("R2_BUCKET_NAME", "aiinvest-bctc")

    service = SagR2StorageClient()
    assert service.is_configured is True
    assert service.auth_mode == "s3"

    mock_s3 = MagicMock()
    service._s3_client = mock_s3

    # Test upload_parsed_markdown
    res = service.upload_parsed_markdown(
        s3_key="bctc/HPG/2025/HPG_2025_KiemToan.md",
        markdown_content="# HPG Audited 2025",
    )
    assert res["status"] == "UPLOADED"
    assert res["key"] == "bctc/HPG/2025/HPG_2025_KiemToan.md"
    assert mock_s3.put_object.called


def test_sag_r2_service_token_mode(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "46ef2c1ebc29131ed4f7727515ec96ca")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setenv("R2_TOKEN", "mock_cf_token_12345")
    monkeypatch.setenv("R2_BUCKET_NAME", "aiinvest-bctc")

    service = SagR2StorageClient()
    assert service.is_configured is True
    assert service.auth_mode == "rest_token"
