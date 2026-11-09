from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from geo.config import Settings


@dataclass(frozen=True)
class ArchiveResult:
    uri: str
    sha256: bytes


class RawResponseArchive:
    """Content-addressed gzip archive with optional Azure Blob storage."""

    def __init__(self, settings: Settings, local_root: Path | None = None):
        self.settings = settings
        self.local_root = local_root or Path(__file__).resolve().parents[1] / "raw-weather"

    def store(self, raw: bytes, logical_date: date, model_slug: str) -> ArchiveResult:
        digest = hashlib.sha256(raw).digest()
        digest_hex = digest.hex()
        blob_name = f"forecasts/{logical_date:%Y/%m/%d}/{model_slug}/{digest_hex}.json.gz"
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        if self.settings.azure_storage_connection_string:
            from azure.storage.blob import BlobServiceClient
            from azure.core.exceptions import ResourceExistsError

            service = BlobServiceClient.from_connection_string(self.settings.azure_storage_connection_string)
            client = service.get_blob_client(self.settings.azure_storage_container, blob_name)
            try:
                client.upload_blob(compressed, overwrite=False, content_type="application/gzip")
            except ResourceExistsError:
                pass
            return ArchiveResult(f"azure://{self.settings.azure_storage_container}/{blob_name}", digest)
        path = self.local_root / Path(blob_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(compressed)
        return ArchiveResult(path.resolve().as_uri(), digest)
