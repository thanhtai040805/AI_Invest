from fastapi import UploadFile

from sag_api.core.errors import ValidationError


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(min(1024 * 1024, max_bytes + 1 - size)):
        size += len(chunk)
        if size > max_bytes:
            raise ValidationError(f"File vượt giới hạn {max_bytes // (1024 * 1024)}MB")
        chunks.append(chunk)
    return b"".join(chunks)
