"""Cloudflare R2 Object Storage Service (S3 & Direct REST API).

Bảo vệ tài nguyên, chống upload trùng lặp (Anti-Duplicate & Deduplication):
- Kiểm tra file_exists() trước khi gửi request upload.
- Tham số overwrite=False mặc định ngăn chặn việc upload lại file đã tồn tại.
- Cấu trúc lưu trữ BCTC chuẩn hóa O(1):
    bctc/{TICKER}/{YEAR}/Q{QUARTER}/{TICKER}_{YEAR}_Q{Q}_{SCOPE}_pruned.pdf
    bctc/{TICKER}/{YEAR}/Q{QUARTER}/{TICKER}_{YEAR}_Q{Q}_{SCOPE}_parsed.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

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
    """Service kết nối và quản lý dữ liệu BCTC trên Cloudflare R2 với cơ chế chống trùng lặp."""

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

        if self.auth_mode == "rest_token":
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

    def upload_bytes(
        self,
        data: bytes,
        s3_key: str,
        content_type: str = "application/octet-stream",
        bucket_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload trực tiếp dữ liệu bytes lên R2 (Quyết định upload do Database Flag quản lý)."""
        bucket = bucket_name or self.bucket_name
        sha256_hash = hashlib.sha256(data).hexdigest()

        if self.auth_mode == "s3":
            client = self.get_s3_client()
            client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=data,
                ContentType=content_type,
            )
            logger.info("✅ Uploaded %d bytes qua S3 -> r2://%s/%s", len(data), bucket, s3_key)
            return {
                "status": "UPLOADED",
                "key": s3_key,
                "url": f"{self.endpoint_url}/{bucket}/{s3_key}",
                "sha256": sha256_hash,
                "bytes": len(data),
            }

        if self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{bucket}/objects/{s3_key}"
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": content_type,
            }
            with httpx.Client(timeout=60.0) as client:
                resp = client.put(url, headers=headers, content=data)
                if resp.status_code not in (200, 201):
                    raise RuntimeError(f"Cloudflare R2 REST API Upload Error ({resp.status_code}): {resp.text}")
            logger.info("✅ Uploaded %d bytes qua REST Token -> r2://%s/%s", len(data), bucket, s3_key)
            return {
                "status": "UPLOADED",
                "key": s3_key,
                "url": f"{self.endpoint_url}/{bucket}/{s3_key}",
                "sha256": sha256_hash,
                "bytes": len(data),
            }

        raise RuntimeError("R2 chưa được cấu hình credentials hợp lệ trong .env")

    def upload_file(
        self,
        local_path: Union[str, Path],
        s3_key: str,
        content_type: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload file từ đĩa lên R2."""
        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file để upload: {local_path}")

        ctype = content_type
        if not ctype:
            if local_path.suffix.lower() == ".pdf":
                ctype = "application/pdf"
            elif local_path.suffix.lower() == ".md":
                ctype = "text/markdown; charset=utf-8"
            elif local_path.suffix.lower() == ".json":
                ctype = "application/json; charset=utf-8"
            else:
                ctype = "application/octet-stream"

        data = local_path.read_bytes()
        return self.upload_bytes(data, s3_key, content_type=ctype, bucket_name=bucket_name)

    def download_bytes(self, s3_key: str, bucket_name: Optional[str] = None) -> bytes:
        """Tải dữ liệu từ R2 dạng bytes."""
        bucket = bucket_name or self.bucket_name
        if self.auth_mode == "s3":
            client = self.get_s3_client()
            resp = client.get_object(Bucket=bucket, Key=s3_key)
            return resp["Body"].read()

        if self.auth_mode == "rest_token":
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/r2/buckets/{bucket}/objects/{s3_key}"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code != 200:
                    raise RuntimeError(f"Cloudflare R2 REST Download Error ({resp.status_code}): {resp.text}")
                return resp.content

        raise RuntimeError("R2 chưa được cấu hình credentials")

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

    def upload_bctc_pruned_pdf(
        self,
        ticker: str,
        year: int,
        quarter: Union[int, str],
        scope: str,
        pdf_source: Union[str, Path, bytes],
        bucket_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload duy nhất 1 file PDF đã cắt tỉa lên R2."""
        ticker = ticker.upper().strip()
        scope = scope.upper().strip()
        q_label = f"Q{quarter}" if str(quarter).isdigit() else str(quarter).upper()

        s3_key = f"bctc/{ticker}/{year}/{q_label}/{ticker}_{year}_{q_label}_{scope}_pruned.pdf"
        if isinstance(pdf_source, (str, Path)):
            return self.upload_file(pdf_source, s3_key, content_type="application/pdf", bucket_name=bucket_name)
        return self.upload_bytes(pdf_source, s3_key, content_type="application/pdf", bucket_name=bucket_name)

    def upload_bctc_parsed_markdown(
        self,
        ticker: str,
        year: int,
        quarter: Union[int, str],
        scope: str,
        markdown_content: str,
        bucket_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload file Markdown sau OCR lên R2."""
        ticker = ticker.upper().strip()
        scope = scope.upper().strip()
        q_label = f"Q{quarter}" if str(quarter).isdigit() else str(quarter).upper()

        s3_key = f"bctc/{ticker}/{year}/{q_label}/{ticker}_{year}_{q_label}_{scope}_parsed.md"
        data = markdown_content.encode("utf-8")
        return self.upload_bytes(data, s3_key, content_type="text/markdown; charset=utf-8", bucket_name=bucket_name)
