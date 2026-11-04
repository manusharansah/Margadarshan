"""
Download the prebuilt road-network artifact from S3 at container start.

    MARGA_NETWORK_S3_URI=s3://my-bucket/network.json.gz python -m gis_backend.fetch_artifact

A no-op when the variable is unset (local development) or the file already exists.
Credentials come from the ECS task role.
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config import get_settings


def fetch(uri: str, dest: Path, client=None) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Expected s3://bucket/key, got {uri!r}")
    if client is None:
        import boto3
        client = boto3.client("s3")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    client.download_file(parsed.netloc, parsed.path.lstrip("/"), str(tmp))
    tmp.replace(dest)
    return True


def main() -> None:
    uri = os.environ.get("MARGA_NETWORK_S3_URI")
    dest = get_settings().network_path
    if not uri:
        print("MARGA_NETWORK_S3_URI not set; using local network file.")
    else:
        fetch(uri, dest)
        print(f"Fetched {uri} -> {dest}")
    if not dest.exists():
        sys.exit(f"No network artifact at {dest}. Build one with `python -m gis_backend.ingest`.")


if __name__ == "__main__":
    main()
