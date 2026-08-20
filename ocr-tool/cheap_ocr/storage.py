# pyright: reportAttributeAccessIssue=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Storage backends for source PDFs and OCR outputs.

Three sections: the ``Storage`` protocol and ``resolve_storage`` dispatch, the
local filesystem backend, and the fsspec-backed cloud backend (S3, GCS, Azure —
their provider-specific credential wiring is one options table, not one class
each).
"""

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlparse

from cheap_ocr.models import DocumentInput

# ---------------------------------------------------------------------------
# Protocol and dispatch
# ---------------------------------------------------------------------------


class Storage(Protocol):
    """Small storage interface used by provider-neutral workflows."""

    def list_pdfs(self, source: str) -> list[DocumentInput]:
        """List PDF inputs below ``source``."""
        ...

    def read_bytes(self, uri: str) -> bytes:
        """Read an object as bytes."""
        ...

    def list_files(self, prefix: str) -> list[str]:
        """List the URIs of all files below a prefix (recursively)."""
        ...

    def write_text(self, uri: str, content: str) -> None:
        """Write UTF-8 text."""
        ...

    def read_json(self, uri: str) -> Any:
        """Read a JSON payload."""
        ...

    def write_json(self, uri: str, payload: Any) -> None:
        """Write a JSON payload."""
        ...

    def join(self, root: str, relative_path: str) -> str:
        """Join a storage root and relative path."""
        ...


def resolve_storage(uri: str) -> Storage:
    """Return a storage backend for a local path or cloud URI."""
    scheme = urlparse(str(uri)).scheme.lower()
    if scheme in {"", "file"}:
        return LocalStorage()
    if scheme in {"s3", "gs", "gcs", "az", "abfs", "abfss"}:
        return FsspecStorage(storage_options=_options_for_scheme(scheme))
    raise ValueError(f"Unsupported storage URI scheme: {scheme}")


def normalize_relative_path(path: str) -> str:
    """Normalize a relative path for output path construction."""
    parts = [part for part in path.replace("\\", "/").strip("/").split("/") if part and part != "."]
    return str(PurePosixPath(*parts)) if parts else ""


def _options_for_scheme(scheme: str) -> dict[str, Any]:
    """Build fsspec storage options from provider environment variables."""
    if scheme == "s3":
        return _s3_options()
    if scheme in {"az", "abfs", "abfss"}:
        return _azure_options()
    return {}  # GCS: gcsfs reads GOOGLE_APPLICATION_CREDENTIALS itself.


def _s3_options() -> dict[str, Any]:
    """Target AWS S3 by default; S3-compatible endpoints via CHEAP_OCR_S3_* / AWS_* env.

    Credentials are read by s3fs from the standard ``AWS_ACCESS_KEY_ID`` /
    ``AWS_SECRET_ACCESS_KEY`` variables.
    """
    endpoint = os.environ.get("CHEAP_OCR_S3_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3")
    region = os.environ.get("CHEAP_OCR_S3_REGION") or os.environ.get("AWS_REGION")
    client_kwargs: dict[str, Any] = {}
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    if region:
        client_kwargs["region_name"] = region
    return {"client_kwargs": client_kwargs} if client_kwargs else {}


def _azure_options() -> dict[str, Any]:
    """Build ADLFS options from common Azure environment variables.

    Accepts a connection string, a full SAS URL in ``CHEAP_OCR_AZURE_SAS_URL`` /
    ``AZURE_STORAGE_SAS_URL`` (account and token are extracted from it), a bare
    SAS token, or service-principal ``AZURE_CLIENT_ID``/``AZURE_CLIENT_SECRET``/
    ``AZURE_TENANT_ID`` credentials.
    """
    connection_string = os.environ.get("CHEAP_OCR_AZURE_CONNECTION_STRING") or os.environ.get(
        "AZURE_STORAGE_CONNECTION_STRING"
    )
    if connection_string:
        return {"connection_string": connection_string}

    options: dict[str, Any] = {}
    account_name = os.environ.get("CHEAP_OCR_AZURE_ACCOUNT_NAME") or os.environ.get("AZURE_STORAGE_ACCOUNT")
    sas_url = (
        os.environ.get("CHEAP_OCR_AZURE_SAS_URL")
        or os.environ.get("AZURE_STORAGE_SAS_URL")
        or os.environ.get("AZURE_BLOB_SAS_URL")
    )
    sas_token = os.environ.get("CHEAP_OCR_AZURE_SAS_TOKEN") or os.environ.get("AZURE_STORAGE_SAS_TOKEN")

    if sas_url:
        parsed = urlparse(sas_url)
        if not account_name:
            host = parsed.netloc.split(":", 1)[0]
            if ".blob." in host:
                account_name = host.split(".blob.", 1)[0]
            elif ".dfs." in host:
                account_name = host.split(".dfs.", 1)[0]
        if not sas_token and parsed.query:
            sas_token = parsed.query

    if account_name:
        options["account_name"] = account_name
    if sas_token:
        options["sas_token"] = sas_token.lstrip("?")

    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    if client_id and client_secret and tenant_id:
        options.update({"client_id": client_id, "client_secret": client_secret, "tenant_id": tenant_id})

    return options


# ---------------------------------------------------------------------------
# Local filesystem
# ---------------------------------------------------------------------------


class LocalStorage:
    """Storage backend for local folders and files."""

    def list_pdfs(self, source: str) -> list[DocumentInput]:
        """List PDF files below a local source path."""
        root = _local_path(source)
        if root.is_file():
            candidates = [root] if root.suffix.lower() == ".pdf" else []
            base = root.parent
        else:
            candidates = sorted(path for path in root.rglob("*.pdf") if path.is_file()) if root.exists() else []
            base = root

        documents: list[DocumentInput] = []
        for path in candidates:
            relative = normalize_relative_path(str(path.relative_to(base)))
            documents.append(
                DocumentInput(
                    uri=str(path),
                    relative_path=relative,
                    input_id=_input_id(str(path.resolve())),
                    metadata=_local_metadata(path),
                )
            )
        return documents

    def read_bytes(self, uri: str) -> bytes:
        """Read a local file as bytes."""
        return _local_path(uri).read_bytes()

    def list_files(self, prefix: str) -> list[str]:
        """List all files below a local prefix (recursively)."""
        root = _local_path(prefix)
        if root.is_file():
            return [str(root)]
        if not root.exists():
            return []
        return sorted(str(path) for path in root.rglob("*") if path.is_file())

    def write_text(self, uri: str, content: str) -> None:
        """Write UTF-8 text to a local file."""
        path = _local_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_json(self, uri: str) -> Any:
        """Read a JSON payload from a local file."""
        return json.loads(self.read_bytes(uri).decode("utf-8"))

    def write_json(self, uri: str, payload: Any) -> None:
        """Write a JSON payload to a local file."""
        self.write_text(uri, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def exists(self, uri: str) -> bool:
        """Return whether a local path exists."""
        return _local_path(uri).exists()

    def join(self, root: str, relative_path: str) -> str:
        """Join a local root and relative path."""
        return str(_local_path(root) / normalize_relative_path(relative_path))


def _local_path(uri: str) -> Path:
    parsed = urlparse(str(uri))
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser()
    return Path(str(uri)).expanduser()


def _local_metadata(path: Path) -> dict[str, int | float | str]:
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified_time": stat.st_mtime,
    }


# ---------------------------------------------------------------------------
# fsspec-backed cloud storage
# ---------------------------------------------------------------------------


class FsspecStorage:
    """Storage backend for fsspec-compatible cloud URIs."""

    def __init__(self, storage_options: dict[str, Any] | None = None) -> None:
        """Create a backend, optionally passing fsspec storage options.

        ``storage_options`` is forwarded to the underlying filesystem (e.g. a
        custom S3-compatible ``endpoint_url``).
        """
        self._storage_options: dict[str, Any] = dict(storage_options or {})

    def _fs(self, uri: str) -> tuple[Any, str]:
        """Resolve a URI to an (filesystem, path) pair using this backend's options."""
        return _url_to_fs(uri, self._storage_options)

    def list_pdfs(self, source: str) -> list[DocumentInput]:
        """List PDF objects below an fsspec URI."""
        fs, root_path = self._fs(source)
        root_path = root_path.rstrip("/")
        if source.lower().endswith(".pdf"):
            if fs.exists(root_path):
                candidates = {root_path: _cloud_metadata(fs, root_path)}
            else:
                candidates = {}
            base = str(PurePosixPath(root_path).parent)
        else:
            candidates = _find_pdf_details(fs, root_path)
            base = root_path

        documents: list[DocumentInput] = []
        for path, metadata in sorted(candidates.items()):
            relative = _relative_cloud_path(path, base)
            uri = _full_uri(source, path)
            documents.append(
                DocumentInput(
                    uri=uri,
                    relative_path=relative,
                    input_id=_input_id(uri),
                    metadata=metadata,
                )
            )
        return documents

    def read_bytes(self, uri: str) -> bytes:
        """Read a cloud object as bytes."""
        fs, path = self._fs(uri)
        with fs.open(path, "rb") as handle:
            return cast(bytes, handle.read())

    def list_files(self, prefix: str) -> list[str]:
        """List the URIs of all objects below a cloud prefix (recursively)."""
        fs, root_path = self._fs(prefix)
        root_path = root_path.rstrip("/")
        try:
            if fs.isfile(root_path):
                paths = [root_path]
            else:
                paths = [str(path) for path in fs.find(root_path)]
        except FileNotFoundError:
            paths = []
        return sorted(_full_uri(prefix, path) for path in paths)

    def write_text(self, uri: str, content: str) -> None:
        """Write UTF-8 text to a cloud object."""
        fs, path = self._fs(uri)
        parent = str(PurePosixPath(path).parent)
        if parent and parent != ".":
            fs.makedirs(parent, exist_ok=True)
        with fs.open(path, "wb") as handle:
            handle.write(content.encode("utf-8"))

    def read_json(self, uri: str) -> Any:
        """Read a JSON payload from a cloud object."""
        return json.loads(self.read_bytes(uri).decode("utf-8"))

    def write_json(self, uri: str, payload: Any) -> None:
        """Write a JSON payload to a cloud object."""
        self.write_text(uri, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def exists(self, uri: str) -> bool:
        """Return whether a cloud object exists."""
        fs, path = self._fs(uri)
        return bool(fs.exists(path))

    def join(self, root: str, relative_path: str) -> str:
        """Join a cloud root URI and relative path."""
        return f"{root.rstrip('/')}/{normalize_relative_path(relative_path)}"


def _find_pdf_details(fs: Any, root_path: str) -> dict[str, dict[str, Any]]:
    """Return PDF paths with metadata, preferring one detailed listing call."""
    try:
        details = fs.find(root_path, detail=True)
    except TypeError:
        details = None
    except FileNotFoundError:
        return {}
    if isinstance(details, dict):
        result: dict[str, dict[str, Any]] = {}
        for raw_path, raw_info in details.items():
            path = str(raw_path)
            if not path.lower().endswith(".pdf"):
                continue
            result[path] = _json_safe_metadata(raw_info) if isinstance(raw_info, dict) else {"path": path}
        return result

    try:
        paths = fs.find(root_path)
    except FileNotFoundError:
        return {}
    return {str(path): _cloud_metadata(fs, str(path)) for path in paths if str(path).lower().endswith(".pdf")}


def _url_to_fs(uri: str, storage_options: dict[str, Any] | None = None) -> tuple[Any, str]:
    try:
        import fsspec
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path.
        raise RuntimeError("Cloud storage requires fsspec and the provider-specific optional extra") from exc
    fs, path = fsspec.core.url_to_fs(uri, **(storage_options or {}))
    return fs, str(path)


def _full_uri(source_uri: str, path: str) -> str:
    # Keep the caller's scheme spelling (gs:// vs gcs://): output paths are built
    # by join() from the same root string, and resumability compares the two —
    # rewriting the scheme here would make completion checks never match.
    return f"{urlparse(source_uri).scheme}://{path.lstrip('/')}"


def _relative_cloud_path(path: str, base: str) -> str:
    path = path.strip("/")
    base = base.strip("/")
    if base and path.startswith(f"{base}/"):
        return normalize_relative_path(path[len(base) + 1 :])
    return normalize_relative_path(PurePosixPath(path).name)


def _input_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_safe_metadata(value: Mapping[Any, Any]) -> dict[str, Any]:
    """Return JSON-serializable object metadata for stats output."""
    return {str(key): _json_safe_value(item) for key, item in value.items()}


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _cloud_metadata(fs: Any, path: str) -> dict[str, Any]:
    try:
        info = fs.info(path)
    except Exception:
        return {"path": path}
    return _json_safe_metadata(info) if isinstance(info, dict) else {"path": path}
