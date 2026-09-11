"""Cloudflare R2 Object Storage Adapter cho Phân hệ SAG (Có chống trùng lặp Deduplication)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

import httpx

try:
    from dotenv import load_dotenv

    # R2_* is intentionally unprefixed and therefore is not loaded by the
    # SAG_* pydantic settings model. Load it for the storage adapter only;
    # production environment variables still take precedence.
    load_dotenv(".env", override=False)
except ImportError:  # pragma: no cover - production images normally include it
    pass

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

from sag_api.core.config import Settings
from sag_api.core.errors import ConfigurationError, ServiceUnavailableError, UpstreamError

logger = logging.getLogger("sag_api.services.r2_storage")


class SagR2StorageClient:
    """Client kết nối tới Cloudflare R2 Object Storage từ SAG với cơ chế kiểm tra chống trùng lặp."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.account_id = os.getenv("R2_ACCOUNT_ID") or "46ef2c1ebc29131ed4f7727515ec96ca"
        self.endpoint_url = (
            os.getenv("R2_ENDPOINT_URL")
            or f"https://{self.account_id}.r2.cloudflarestorage.com"
        )
        self.access_key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
        self.secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
        self.api_token = (
            os.getenv("R2_TOKEN", "") or os.getenv("CLOUDFLARE_API_TOKEN", "")
        ).strip()
        self.bucket_name = os.getenv("R2_BUCKET_NAME", "aiinvest-bctc").strip()
        self._s3_client: Any = None

    @property
    def is_configured(self) -> bool:
        has_s3 = bool(self.access_key_id and self.secret_access_key and HAS_BOTO3)
        has_token = bool(self.api_token)
        return has_s3 or has_token

    @property
    def auth_mode(self) -> str:
        if self.access_key_id and self.secret_access_key and HAS_BOTO3:
            return "s3"
        if self.api_token:
            return "rest_token"
        return "unconfigured"

    def get_s3_client(self) -> Any:
        if not HAS_BOTO3:
            raise ConfigurationError("Thư viện 'boto3' chưa được cài đặt")
        if not (self.access_key_id and self.secret_access_key):
            raise ConfigurationError("Thiếu R2_ACCESS_KEY_ID hoặc R2_SECRET_ACCESS_KEY")

        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
            )
        return self._s3_client

    def file_exists(self, s3_key: str) -> bool:
        """Kiểm tra xem file đã tồn tại trên R2 hay chưa."""
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            try:
                client.head_object(Bucket=self.bucket_name, Key=s3_key)
                return True
            except ClientError as err:
                code = err.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey"):
                    return False
                logger.warning("SAG R2 file_exists check failed: %s", err)
                return False

        if self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{self.bucket_name}/objects/{s3_key}"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.head(url, headers=headers)
                    return resp.status_code == 200
            except Exception:
                return False

        return False

    def download_bctc_to_file(
        self,
        s3_key: str,
        destination: Union[str, Path],
    ) -> Dict[str, Any]:
        """Stream an R2 object to disk while calculating its digest."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0

        try:
            if self.auth_mode == "s3":
                response = self.get_s3_client().get_object(Bucket=self.bucket_name, Key=s3_key)
                body = response["Body"]
                with target.open("wb") as output:
                    for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                body.close()
                return {"sha256": digest.hexdigest(), "size": size}

            if self.auth_mode == "rest_token":
                url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{self.bucket_name}/objects/{s3_key}"
                headers = {"Authorization": f"Bearer {self.api_token}"}
                with httpx.stream("GET", url, headers=headers, timeout=120.0) as response:
                    if response.status_code != 200:
                        detail = response.read().decode("utf-8", errors="replace")
                        raise UpstreamError(f"Không thể tải file từ R2 qua REST Token ({response.status_code}): {detail}")
                    with target.open("wb") as output:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            if chunk:
                                output.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                return {"sha256": digest.hexdigest(), "size": size}
        except ClientError as err:
            raise UpstreamError(f"Không thể tải file từ R2 ({s3_key}): {err}") from err
        except Exception:
            target.unlink(missing_ok=True)
            raise

        raise ConfigurationError("Cloudflare R2 chưa được cấu hình credentials hợp lệ")

    def download_bctc_bytes(self, s3_key: str) -> bytes:
        """Tải file PDF hoặc Markdown từ R2 bucket (legacy/small-object path)."""
        """Tải file PDF hoặc Markdown từ R2 bucket."""
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            try:
                resp = client.get_object(Bucket=self.bucket_name, Key=s3_key)
                return resp["Body"].read()
            except ClientError as err:
                raise UpstreamError(f"Không thể tải file từ R2 ({s3_key}): {err}") from err

        if self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{self.bucket_name}/objects/{s3_key}"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code != 200:
                    raise UpstreamError(f"Không thể tải file từ R2 qua REST Token ({resp.status_code}): {resp.text}")
                return resp.content

        raise ConfigurationError("Cloudflare R2 chưa được cấu hình credentials hợp lệ")

    def upload_parsed_markdown(self, s3_key: str, markdown_content: str) -> Dict[str, Any]:
        """Upload trực tiếp file Markdown đã làm sạch lên R2."""
        data = markdown_content.encode("utf-8")
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            try:
                client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=data,
                    ContentType="text/markdown; charset=utf-8",
                )
                return {"status": "UPLOADED", "key": s3_key}
            except ClientError as err:
                raise UpstreamError(f"Không thể upload Markdown lên R2: {err}") from err

        if self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{self.bucket_name}/objects/{s3_key}"
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "text/markdown; charset=utf-8",
            }
            with httpx.Client(timeout=60.0) as client:
                resp = client.put(url, headers=headers, content=data)
                if resp.status_code not in (200, 201):
                    raise UpstreamError(f"Không thể upload Markdown lên R2 qua REST Token: {resp.text}")
            return {"status": "UPLOADED", "key": s3_key}

        raise ConfigurationError("Cloudflare R2 chưa được cấu hình credentials hợp lệ")
