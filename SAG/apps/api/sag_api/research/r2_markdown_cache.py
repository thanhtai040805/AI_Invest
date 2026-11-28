"""Cloudflare R2 Cleaned Markdown Recovery Cache (`SagR2MarkdownCache`, `vn-clean-v3`).

Implements Tier-4 of `sag_vn_source_and_bilingual_cleaning_spec.md`:
- Stores ONLY cleaned Vietnamese Markdown bundles (`{SUB_DOC_SLOT}.md` + `manifest.json`)
  and lightweight `EXCLUDED_FULL_ENGLISH` tombstones on Cloudflare R2 (plus optional local
  disk mirror) keyed by `(CLEANER_VERSION, ticker, pdf_sha256)`.
- 100% Native Async (`httpx.AsyncClient`) supporting BOTH:
  1. S3-compatible R2 credentials (`R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY`) via stdlib
     AWS Signature Version 4 (`AWS4-HMAC-SHA256` using `hmac` + `hashlib`), and
  2. Cloudflare REST API Token (`R2_TOKEN` / `CLOUDFLARE_API_TOKEN`),
  plus standalone/L1 `local_cache_dir` so wire-level tests (`httpx.MockTransport`) and
  production containers require zero extra PyPI dependencies.
- Crash-Safe Checkpoint Order:
  - Read BEFORE PDF extraction / MinerU OCR -> `0s` OCR wait & `0` MinerU quota on retry/re-index.
  - Write IMMEDIATELY AFTER `build_research_sub_documents` + `vn_bilingual_cleaner` and BEFORE
    DB / Qdrant / Table / KG persistence -> downstream crashes never lose OCR work.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from sag_api.core.logging import get_logger
from sag_api.research.input_builder import (
    DocumentCoverageAudit,
    InputBuildError,
    PageOffsetCalibration,
    ResearchInputBuildResult,
    SubDocumentBundle,
)
from sag_api.research.input_inspector import InspectionResult
from sag_api.research.vn_bilingual_cleaner import CLEANER_VERSION

log = get_logger("research.r2_markdown_cache")


def compute_pdf_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def build_s3_sigv4_headers(
    *,
    method: str,
    url: str,
    access_key_id: str,
    secret_access_key: str,
    payload: bytes = b"",
    content_type: str | None = None,
    region: str = "auto",
    now_utc: datetime | None = None,
) -> dict[str, str]:
    """Compute AWS Signature Version 4 (`AWS4-HMAC-SHA256`) headers for Cloudflare R2 S3 endpoints."""
    dt = (now_utc or datetime.now(UTC)).astimezone(UTC)
    amz_date = dt.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = dt.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    parsed = urlsplit(url)
    host = parsed.netloc
    # Canonical URI with RFC 3986 path-segment encoding
    canonical_uri = quote(parsed.path or "/", safe="/-_.~")
    canonical_querystring = parsed.query or ""

    headers_to_sign: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        headers_to_sign["content-type"] = content_type

    sorted_keys = sorted(headers_to_sign.keys())
    canonical_headers = "".join(f"{k}:{headers_to_sign[k].strip()}\n" for k in sorted_keys)
    signed_headers = ";".join(sorted_keys)

    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    k_date = _hmac_sha256(("AWS4" + secret_access_key).encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, "s3")
    k_signing = _hmac_sha256(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out = {
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    if content_type:
        out["Content-Type"] = content_type
    return out


class SagR2MarkdownCache:
    """Async Cloudflare R2 + optional local-disk cache for cleaned Vietnamese `.md` sub-documents."""

    def __init__(
        self,
        *,
        account_id: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        api_token: str | None = None,
        bucket_name: str | None = None,
        local_cache_dir: str | pathlib.Path | None = None,
        settings_obj: Any | None = None,
        cleaner_version: str = CLEANER_VERSION,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if settings_obj is None:
            from sag_api.core.config import settings as default_settings

            cfg: Any = default_settings
        else:
            cfg = settings_obj

        self.account_id = (
            account_id
            if account_id is not None
            else (
                os.getenv("SAG_R2_ACCOUNT_ID")
                or os.getenv("R2_ACCOUNT_ID")
                or cfg.r2_account_id
                or ""
            ).strip()
        )
        self.endpoint_url = (
            endpoint_url
            if endpoint_url is not None
            else (
                os.getenv("SAG_R2_ENDPOINT_URL")
                or os.getenv("R2_ENDPOINT_URL")
                or cfg.r2_endpoint_url
                or (f"https://{self.account_id}.r2.cloudflarestorage.com" if self.account_id else "")
            ).strip()
        ).rstrip("/")
        self.access_key_id = (
            access_key_id
            if access_key_id is not None
            else (
                os.getenv("SAG_R2_ACCESS_KEY_ID")
                or os.getenv("R2_ACCESS_KEY_ID")
                or cfg.r2_access_key_id
                or ""
            ).strip()
        )
        self.secret_access_key = (
            secret_access_key
            if secret_access_key is not None
            else (
                os.getenv("SAG_R2_SECRET_ACCESS_KEY")
                or os.getenv("R2_SECRET_ACCESS_KEY")
                or cfg.r2_secret_access_key
                or ""
            ).strip()
        )
        self.api_token = (
            api_token
            if api_token is not None
            else (
                os.getenv("SAG_R2_TOKEN")
                or os.getenv("R2_TOKEN")
                or os.getenv("CLOUDFLARE_API_TOKEN")
                or cfg.r2_token
                or ""
            ).strip()
        )
        self.bucket_name = (
            bucket_name
            if bucket_name is not None
            else (
                os.getenv("SAG_R2_BUCKET_NAME")
                or os.getenv("R2_BUCKET_NAME")
                or cfg.r2_bucket_name
                or "aiinvest-bctc-prod"
            ).strip()
        )
        env_local = (
            os.getenv("SAG_R2_MD_LOCAL_CACHE_DIR")
            or os.getenv("R2_MD_LOCAL_CACHE_DIR")
            or cfg.r2_md_local_cache_dir
            or ""
        ).strip()
        resolved_local = local_cache_dir if local_cache_dir is not None else (env_local or None)
        self.local_cache_dir = pathlib.Path(resolved_local) if resolved_local else None
        self.cleaner_version = cleaner_version
        self._http_client = http_client
        self._timeout = timeout_seconds

    @classmethod
    def from_settings(cls, settings_obj: Any, **kwargs: Any) -> SagR2MarkdownCache:
        return cls(settings_obj=settings_obj, **kwargs)

    @property
    def remote_mode(self) -> str:
        if self.endpoint_url and self.access_key_id and self.secret_access_key:
            return "s3"
        if self.account_id and self.api_token:
            return "rest_token"
        return "none"

    @property
    def is_configured(self) -> bool:
        return self.remote_mode != "none" or self.local_cache_dir is not None

    def build_prefix(
        self,
        *,
        ticker: str,
        pdf_sha256: str,
        doc_type: str = "DOC",
        fiscal_year: int | None = None,
        fiscal_quarter: int = 0,
    ) -> str:
        sym = ticker.strip().upper()
        fy_tag = f"FY{fiscal_year}" if fiscal_year else "FY0"
        dt_tag = (doc_type or "DOC").strip().upper()
        return f"sag-clean-md/{self.cleaner_version}/{sym}/{fy_tag}_Q{fiscal_quarter}_{dt_tag}_{pdf_sha256[:16]}"

    def _local_path_for_key(self, key: str) -> pathlib.Path | None:
        if self.local_cache_dir is None:
            return None
        safe_rel = pathlib.Path(*[seg for seg in key.split("/") if seg and seg not in (".", "..")])
        return self.local_cache_dir / safe_rel

    async def get_text_object(self, key: str) -> str | None:
        local_p = self._local_path_for_key(key)
        if local_p is not None and local_p.is_file():
            return local_p.read_text(encoding="utf-8")

        if self.remote_mode == "none":
            return None

        raw = await self._remote_get(key)
        if raw is None:
            return None
        text = raw.decode("utf-8")
        if local_p is not None:
            local_p.parent.mkdir(parents=True, exist_ok=True)
            local_p.write_text(text, encoding="utf-8")
        return text

    async def put_text_object(
        self,
        key: str,
        content: str,
        *,
        content_type: str = "text/markdown; charset=utf-8",
    ) -> None:
        local_p = self._local_path_for_key(key)
        if local_p is not None:
            local_p.parent.mkdir(parents=True, exist_ok=True)
            local_p.write_text(content, encoding="utf-8")

        if self.remote_mode != "none":
            await self._remote_put(key, content.encode("utf-8"), content_type=content_type)

    async def delete_object(self, key: str) -> None:
        """Delete an object from both local mirror cache and remote Cloudflare R2 (404-safe)."""
        local_p = self._local_path_for_key(key)
        if local_p is not None and local_p.is_file():
            try:
                local_p.unlink()
            except OSError:
                pass
        if self.remote_mode != "none":
            await self._remote_delete(key)

    async def _remote_delete(self, key: str) -> None:
        if self.remote_mode == "s3":
            url = f"{self.endpoint_url}/{self.bucket_name}/{key.lstrip('/')}"
            headers = build_s3_sigv4_headers(
                method="DELETE",
                url=url,
                access_key_id=self.access_key_id,
                secret_access_key=self.secret_access_key,
            )
        else:
            url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/r2/buckets/{self.bucket_name}/objects/{quote(key.lstrip('/'), safe='/')}"
            )
            headers = {"Authorization": f"Bearer {self.api_token}"}

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                if self._http_client is not None:
                    resp = await self._http_client.delete(url, headers=headers)
                else:
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        resp = await client.delete(url, headers=headers)
                if resp.status_code in (200, 204, 404):
                    return
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"R2 DELETE failed ({resp.status_code}) for {key}")
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"R2 DELETE failed ({type(exc).__name__}) for {key}") from exc
        raise RuntimeError(f"R2 DELETE failed for {key}: {last_exc}")

    async def _remote_get(self, key: str) -> bytes | None:
        if self.remote_mode == "s3":
            url = f"{self.endpoint_url}/{self.bucket_name}/{key.lstrip('/')}"
            headers = build_s3_sigv4_headers(
                method="GET",
                url=url,
                access_key_id=self.access_key_id,
                secret_access_key=self.secret_access_key,
            )
        else:
            url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/r2/buckets/{self.bucket_name}/objects/{quote(key.lstrip('/'), safe='/')}"
            )
            headers = {"Authorization": f"Bearer {self.api_token}"}

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                if self._http_client is not None:
                    resp = await self._http_client.get(url, headers=headers)
                else:
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        resp = await client.get(url, headers=headers)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 200:
                    return resp.content
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"R2 GET failed ({resp.status_code}) for {key}")
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"R2 GET failed ({type(exc).__name__}) for {key}") from exc
        raise RuntimeError(f"R2 GET failed for {key}: {last_exc}")

    async def _remote_put(self, key: str, data: bytes, *, content_type: str) -> None:
        if self.remote_mode == "s3":
            url = f"{self.endpoint_url}/{self.bucket_name}/{key.lstrip('/')}"
            headers = build_s3_sigv4_headers(
                method="PUT",
                url=url,
                access_key_id=self.access_key_id,
                secret_access_key=self.secret_access_key,
                payload=data,
                content_type=content_type,
            )
        else:
            url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/r2/buckets/{self.bucket_name}/objects/{quote(key.lstrip('/'), safe='/')}"
            )
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": content_type,
            }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                if self._http_client is not None:
                    resp = await self._http_client.put(url, headers=headers, content=data)
                else:
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        resp = await client.put(url, headers=headers, content=data)
                if resp.status_code in (200, 201, 204):
                    return
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"R2 PUT failed ({resp.status_code}) for {key}")
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"R2 PUT failed ({type(exc).__name__}) for {key}") from exc
        raise RuntimeError(f"R2 PUT failed for {key}: {last_exc}")

    async def save_cleaned_build_result(
        self,
        *,
        pdf_bytes: bytes,
        build_result: ResearchInputBuildResult,
        fiscal_year: int | None = None,
        fiscal_quarter: int = 0,
    ) -> str | None:
        """Persist cleaned `.md` bundles + `manifest.json` immediately after OCR/cleaning."""
        if not self.is_configured or not build_result.sub_documents:
            return None
        sha = compute_pdf_sha256(pdf_bytes)
        eff_fy = fiscal_year or build_result.sub_documents[0].fiscal_year
        eff_fq = build_result.sub_documents[0].fiscal_quarter if fiscal_year is None else fiscal_quarter
        prefix = self.build_prefix(
            ticker=build_result.ticker,
            pdf_sha256=sha,
            doc_type=build_result.doc_type,
            fiscal_year=eff_fy,
            fiscal_quarter=eff_fq,
        )

        sub_entries: list[dict[str, Any]] = []
        for bundle in build_result.sub_documents:
            md_key = f"{prefix}/{bundle.sub_doc_slot}.md"
            await self.put_text_object(
                md_key,
                bundle.markdown_content,
                content_type="text/markdown; charset=utf-8",
            )
            sub_entries.append(
                {
                    "project_id": bundle.project_id,
                    "sub_doc_slot": bundle.sub_doc_slot,
                    "half_life_days": bundle.half_life_days,
                    "fiscal_year": bundle.fiscal_year,
                    "fiscal_quarter": bundle.fiscal_quarter,
                    "doc_type": bundle.doc_type,
                    "published_date": bundle.published_date.isoformat(),
                    "valid_from": bundle.valid_from.isoformat(),
                    "routed_branch": bundle.routed_branch,
                    "page_numbers": list(bundle.page_numbers),
                    "content_hash": bundle.content_hash,
                    "source_url": bundle.source_url,
                    "title": bundle.title,
                    "md_key": md_key,
                }
            )

        manifest = {
            "cleaner_version": self.cleaner_version,
            "status": "READY",
            "pdf_sha256": sha,
            "ticker": build_result.ticker,
            "doc_type": build_result.doc_type,
            "routed_branch": build_result.routed_branch,
            "localization_method": build_result.localization_method,
            "pr_narrative_markdown": build_result.pr_narrative_markdown,
            "follow_up_urls": list(build_result.follow_up_urls),
            "missing_core_slots": list(build_result.missing_core_slots),
            "inspection": build_result.inspection.to_dict(),
            "coverage_audit": asdict(build_result.coverage_audit),
            "sub_documents": sub_entries,
        }
        manifest_key = f"{prefix}/manifest.json"
        await self.put_text_object(
            manifest_key,
            json.dumps(manifest, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        return manifest_key

    async def save_english_tombstone(
        self,
        *,
        ticker: str,
        pdf_bytes: bytes,
        doc_type: str,
        fiscal_year: int | None,
        fiscal_quarter: int,
        reason: str,
    ) -> str | None:
        """Persist a lightweight tombstone when a Full-English PDF/scan is rejected so retries use 0 OCR."""
        if not self.is_configured:
            return None
        sha = compute_pdf_sha256(pdf_bytes)
        prefix = self.build_prefix(
            ticker=ticker,
            pdf_sha256=sha,
            doc_type=doc_type,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
        )
        manifest = {
            "cleaner_version": self.cleaner_version,
            "status": "EXCLUDED_FULL_ENGLISH",
            "exclusion_reason": reason,
            "pdf_sha256": sha,
            "ticker": ticker.strip().upper(),
            "doc_type": doc_type,
        }
        manifest_key = f"{prefix}/manifest.json"
        await self.put_text_object(
            manifest_key,
            json.dumps(manifest, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        return manifest_key

    async def load_cached_build_result(
        self,
        *,
        ticker: str,
        pdf_bytes: bytes,
        doc_type: str,
        published_date: date,
        fiscal_year: int | None,
        fiscal_quarter: int = 0,
    ) -> ResearchInputBuildResult | None:
        """Return cached `ResearchInputBuildResult` (`0` OCR pages used) on R2 HIT, or `None` on MISS."""
        if not self.is_configured:
            return None
        sha = compute_pdf_sha256(pdf_bytes)
        eff_fy = fiscal_year or published_date.year
        prefix = self.build_prefix(
            ticker=ticker,
            pdf_sha256=sha,
            doc_type=doc_type,
            fiscal_year=eff_fy,
            fiscal_quarter=fiscal_quarter,
        )
        raw_manifest = await self.get_text_object(f"{prefix}/manifest.json")
        if not raw_manifest:
            return None

        manifest = json.loads(raw_manifest)
        if manifest.get("cleaner_version") != self.cleaner_version:
            return None
        if manifest.get("pdf_sha256") != sha:
            return None
        if manifest.get("status") == "EXCLUDED_FULL_ENGLISH":
            reason = manifest.get("exclusion_reason") or "EXCLUDED_FULL_ENGLISH_DOCUMENT"
            raise InputBuildError(f"EXCLUDED_CACHED_ENGLISH_DOC:{reason}")

        sub_entries = manifest.get("sub_documents") or []
        if not sub_entries:
            return None

        valid_from_dt = datetime(
            published_date.year, published_date.month, published_date.day, tzinfo=UTC
        )
        bundles: list[SubDocumentBundle] = []
        for entry in sub_entries:
            md_key = str(entry.get("md_key") or "")
            if not md_key:
                return None
            md_text = await self.get_text_object(md_key)
            if md_text is None:
                return None
            bundles.append(
                SubDocumentBundle(
                    project_id=str(entry.get("project_id") or ticker.strip().upper()),
                    sub_doc_slot=str(entry["sub_doc_slot"]),
                    half_life_days=int(entry.get("half_life_days") or 365),
                    fiscal_year=int(entry.get("fiscal_year") or eff_fy),
                    fiscal_quarter=int(entry.get("fiscal_quarter") or fiscal_quarter),
                    doc_type=str(entry.get("doc_type") or doc_type),
                    published_date=published_date,
                    valid_from=valid_from_dt,
                    routed_branch=entry.get("routed_branch") or manifest.get("routed_branch") or "A",
                    page_numbers=[int(p) for p in (entry.get("page_numbers") or [])],
                    markdown_content=md_text,
                    content_hash=hashlib.sha256(md_text.encode("utf-8")).hexdigest(),
                    ocr_pages_used=0,
                    source_url=str(entry.get("source_url") or ""),
                    title=str(entry.get("title") or ""),
                )
            )

        insp_dict = manifest.get("inspection") or {}
        insp = InspectionResult(
            pages=int(insp_dict.get("pages") or 0),
            mb=float(insp_dict.get("mb") or 0.0),
            is_zip=bool(insp_dict.get("is_zip")),
            zip_entries=list(insp_dict.get("zip_entries") or []),
            chosen_entry=insp_dict.get("chosen_entry"),
            spread_pages=int(insp_dict.get("spread_pages") or 0),
            digital_clean_pages=int(insp_dict.get("digital_clean_pages") or 0),
            bad_font_pages=int(insp_dict.get("bad_font_pages") or 0),
            scan_pages=int(insp_dict.get("scan_pages") or 0),
            median_vn_readability=float(insp_dict.get("median_vn_readability") or 0.0),
            doc_class=insp_dict.get("doc_class") or "DIGITAL",
            doc_language=str(insp_dict.get("doc_language") or "VIETNAMESE"),
            has_embedded_notes=bool(insp_dict.get("has_embedded_notes")),
            has_segment_or_bank_notes=bool(insp_dict.get("has_segment_or_bank_notes")),
            has_plan=bool(insp_dict.get("has_plan")),
            embedded_urls=list(insp_dict.get("embedded_urls") or []),
            routed_branch=insp_dict.get("routed_branch") or "A",
            est_ocr_pages=0,
            auto_sidebar_x=float(insp_dict.get("auto_sidebar_x") or 0.0),
            source_url=str(insp_dict.get("source_url") or ""),
            english_page_numbers=list(insp_dict.get("english_page_numbers") or []),
        )
        audit_dict = manifest.get("coverage_audit") or {}
        valid_audit_keys = {f.name for f in DocumentCoverageAudit.__dataclass_fields__.values()}
        filtered_audit = {k: v for k, v in audit_dict.items() if k in valid_audit_keys}
        orig_loc = str(manifest.get("localization_method") or "NATIVE_4CLASS_DETERMINISTIC")
        loc_tag = orig_loc if orig_loc.startswith("R2_CACHE:") else f"R2_CACHE:{orig_loc}"

        return ResearchInputBuildResult(
            ticker=ticker.strip().upper(),
            doc_type=str(manifest.get("doc_type") or doc_type),
            routed_branch=manifest.get("routed_branch") or "A",
            inspection=insp,
            sub_documents=bundles,
            pr_narrative_markdown=str(manifest.get("pr_narrative_markdown") or ""),
            follow_up_urls=list(manifest.get("follow_up_urls") or []),
            ocr_pages_used=0,
            localization_method=loc_tag,
            page_calibration=PageOffsetCalibration(),
            missing_core_slots=list(manifest.get("missing_core_slots") or []),
            coverage_audit=DocumentCoverageAudit(**filtered_audit),
        )
