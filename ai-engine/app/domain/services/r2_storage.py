"""Read/delete adapter for the SAG-owned Cloudflare R2 artifact store."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional
from pathlib import Path

import httpx

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

logger = logging.getLogger("ai_engine.services.r2_storage")


class R2StorageService:
    """Read/delete adapter for SAG-owned R2 artifacts.

    Durable Markdown writes are performed by SAG. ai-engine retains only the
    read path for extraction/cache reuse and the delete path for legacy PDF
    staging cleanup.
    """

    def __init__(
        self,
        account_id: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        api_token: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ) -> None:
        self.account_id = (
            account_id
            or os.getenv("R2_ACCOUNT_ID")
            or "46ef2c1ebc29131ed4f7727515ec96ca"
        )
        self.endpoint_url = (
            endpoint_url
            or os.getenv("R2_ENDPOINT_URL")
            or f"https://{self.account_id}.r2.cloudflarestorage.com"
        )
        self.access_key_id = (access_key_id or os.getenv("R2_ACCESS_KEY_ID", "")).strip()
        self.secret_access_key = (secret_access_key or os.getenv("R2_SECRET_ACCESS_KEY", "")).strip()
        self.api_token = (
            api_token
            or os.getenv("R2_TOKEN", "")
            or os.getenv("CLOUDFLARE_API_TOKEN", "")
        ).strip()
        env_name = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "")).lower().strip()
        is_test = env_name in ("test", "testing")
        if not bucket_name:
            if is_test:
                self.bucket_name = (os.getenv("R2_TEST_BUCKET_NAME") or "aiinvest-bctc-test").strip()
            else:
                # Mặc định Local và PROD dùng chung bucket aiinvest-bctc-prod để khai thác chung kho BCTC thật
                self.bucket_name = (os.getenv("R2_BUCKET_NAME") or "aiinvest-bctc-prod").strip()
        else:
            self.bucket_name = bucket_name.strip()
        self._s3_client: Any = None

    @property
    def is_configured(self) -> bool:
        has_s3 = bool(self.access_key_id and self.secret_access_key)
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
            raise RuntimeError("Thư viện 'boto3' chưa được cài đặt. Vui lòng chạy: pip install boto3")
        if not (self.access_key_id and self.secret_access_key):
            raise RuntimeError("Thiếu R2_ACCESS_KEY_ID hoặc R2_SECRET_ACCESS_KEY.")

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

    def file_exists(self, s3_key: str, bucket_name: Optional[str] = None) -> bool:
        """Kiểm tra xem file đã tồn tại trên Cloudflare R2 hay chưa mà không cần tải dữ liệu về."""
        bucket = bucket_name or self.bucket_name
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            try:
                client.head_object(Bucket=bucket, Key=s3_key)
                return True
            except ClientError as err:
                code = err.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey"):
                    return False
                logger.warning("Lỗi khi kiểm tra file_exists trên R2 (%s): %s", s3_key, err)
                return False

        elif self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{bucket}/objects/{s3_key}"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.head(url, headers=headers)
                    return resp.status_code == 200
            except Exception as err:
                logger.warning("REST HEAD kiểm tra file_exists lỗi (%s): %s", s3_key, err)
                return False

        return False

    def download_bytes(self, s3_key: str, bucket_name: Optional[str] = None) -> bytes:
        cache_path = self._local_markdown_cache_path(s3_key)
        if cache_path and cache_path.is_file():
            return cache_path.read_bytes()
        """Tải dữ liệu từ R2 dạng bytes."""
        bucket = bucket_name or self.bucket_name
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            resp = client.get_object(Bucket=bucket, Key=s3_key)
            data = resp["Body"].read()

        elif self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{bucket}/objects/{s3_key}"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code != 200:
                    raise RuntimeError(f"Cloudflare R2 REST Download Error ({resp.status_code}): {resp.text}")
                data = resp.content
        else:
            raise RuntimeError("R2 credentials are not configured")

        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        return data

        raise RuntimeError("R2 chưa được cấu hình credentials")

    @staticmethod
    def object_key_from_uri(object_uri: Optional[str]) -> Optional[str]:
        """Convert SAG's ``r2://bucket/key`` URI to an object key."""
        if not object_uri:
            return None
        value = str(object_uri).strip()
        if not value.startswith("r2://"):
            return None
        remainder = value[5:]
        _, separator, key = remainder.partition("/")
        return key if separator and key else None

    def list_keys(self, prefix: str, bucket_name: Optional[str] = None) -> list[str]:
        """List object keys below a prefix without downloading their content."""
        bucket = bucket_name or self.bucket_name
        if self.auth_mode != "s3":
            raise RuntimeError("R2 list_keys hiện yêu cầu S3 credentials")
        client = self.get_s3_client()
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(str(item["Key"]) for item in page.get("Contents", []) if item.get("Key"))
        return keys

    @staticmethod
    def _local_markdown_cache_path(s3_key: str) -> Optional[Path]:
        name = Path(s3_key).name
        stem, suffix = Path(name).stem, Path(name).suffix.lower()
        if suffix not in {".md", ".markdown"} or not re.fullmatch(r"[0-9a-f]{64}", stem):
            return None
        root = Path(os.getenv("R2_LOCAL_MARKDOWN_CACHE", ".data/r2-markdown-cache"))
        return root / f"{stem}{suffix}"

    def delete_object(self, s3_key: str, bucket_name: Optional[str] = None) -> bool:
        """Xóa 1 object trên R2 Storage (dành cho cleanup/teardown hoặc thay thế tài liệu)."""
        bucket = bucket_name or self.bucket_name
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            try:
                client.delete_object(Bucket=bucket, Key=s3_key)
                logger.info("🗑️ Deleted object qua S3 -> r2://%s/%s", bucket, s3_key)
                return True
            except Exception as e:
                logger.error("Lỗi khi xóa object S3 r2://%s/%s: %s", bucket, s3_key, e)
                return False

        if self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{bucket}/objects/{s3_key}"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            with httpx.Client(timeout=60.0) as client:
                resp = client.delete(url, headers=headers)
                if resp.status_code in (200, 204):
                    logger.info("🗑️ Deleted object qua REST Token -> r2://%s/%s", bucket, s3_key)
                    return True
                logger.error("Lỗi khi xóa object REST r2://%s/%s (%d): %s", bucket, s3_key, resp.status_code, resp.text)
                return False

        return False
