#!/usr/bin/env python3
"""Sync local MEDIA_STORAGE_ROOT into MinIO/S3 (ADR-010 P16 cutover).

Uploads files under ``{MEDIA_STORAGE_ROOT}/media/`` to bucket keys ``media/...``
so existing CDN URLs (``{CDN_BASE_URL}/media/...``) keep working.
Does not invent paths — mirrors disk layout only.
"""

from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("MEDIA_STORAGE_ROOT") or "var/media").resolve()
    media_dir = root / "media"
    if not media_dir.is_dir():
        print(f"missing media dir: {media_dir}", file=sys.stderr)
        return 1

    bucket = os.environ.get("S3_BUCKET") or ""
    endpoint = os.environ.get("S3_ENDPOINT_URL") or None
    region = os.environ.get("S3_REGION") or "us-east-1"
    if not bucket:
        print("S3_BUCKET required", file=sys.stderr)
        return 1

    import boto3
    from botocore.client import Config

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )

    dry = "--dry-run" in sys.argv
    uploaded = 0
    skipped = 0
    errors = 0
    for path in media_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()  # media/...
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if dry:
            uploaded += 1
            continue
        try:
            # Skip if same size already present
            try:
                head = client.head_object(Bucket=bucket, Key=rel)
                if int(head.get("ContentLength") or -1) == path.stat().st_size:
                    skipped += 1
                    continue
            except client.exceptions.ClientError:
                pass
            client.upload_file(
                str(path),
                bucket,
                rel,
                ExtraArgs={"ContentType": ctype},
            )
            uploaded += 1
            if uploaded % 100 == 0:
                print(f"uploaded {uploaded}…", flush=True)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"ERR {rel}: {exc}", file=sys.stderr)

    print(
        {
            "root": str(root),
            "bucket": bucket,
            "endpoint": endpoint,
            "uploaded": uploaded,
            "skipped_unchanged": skipped,
            "errors": errors,
            "dry_run": dry,
        }
    )
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
