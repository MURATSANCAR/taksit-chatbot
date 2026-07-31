# Operator runbook — S3 / MinIO + CDN origin (ADR-010 P16)

**Goal:** Point media pipeline at real object storage and a CDN origin so chatbot
cards never hotlink merchant images.

## Local (compose / laptop)

Defaults:

```bash
OBJECT_STORAGE_BACKEND=local
MEDIA_STORAGE_ROOT=/data/media   # or /tmp/taksitlio-media
CDN_BASE_URL=http://localhost:8000/cdn
```

API mounts `MEDIA_STORAGE_ROOT` at **`/cdn`**. Scheduler uses the same factory
(`build_object_storage_from_env`) so MEDIA_FETCH writes land on the shared volume.

Check:

```bash
curl -sS "$API/v1/admin/media/storage"
curl -sS "$API/ready"
```

## MinIO / S3-compatible

```bash
export OBJECT_STORAGE_BACKEND=s3
export S3_BUCKET=taksitlio-media
export S3_PREFIX=taksitlio
export S3_ENDPOINT_URL=http://127.0.0.1:9000   # omit for AWS
export S3_REGION=us-east-1
export CDN_BASE_URL=https://cdn.your-domain.example
# boto3 standard env (never commit):
export AWS_ACCESS_KEY_ID=…
export AWS_SECRET_ACCESS_KEY=…
# pip install '.[storage]'  # boto3
```

Validate config (fails if bucket missing):

```bash
python - <<'PY'
from taksitlio.media.config import load_object_storage_config
load_object_storage_config().validate(strict=True)
print("ok")
PY
```

Live probe:

```bash
curl -sS "$API/v1/admin/media/storage?probe=true"
```

CDN must map to the bucket (or prefix). Object keys look like:

```text
{S3_PREFIX}/media/original/… 
{S3_PREFIX}/media/variants/…/*.webp
```

Public card URLs are `{CDN_BASE_URL}/{storage_key}` only — never `source_url`.

## Cutover checklist

1. Create bucket + IAM/MinIO user with `s3:PutObject` / `s3:HeadBucket`
2. Point CDN origin at the bucket (or path `{S3_PREFIX}/`)
3. Set env on **api** and **scheduler** identically
4. `GET /v1/admin/media/storage?probe=true` → `ready: true`
5. Trigger a MEDIA_FETCH (feed persist with images) and confirm card
   `image.thumbnail_cdn_url` uses your CDN host
6. Confirm `placeholder_cdn: false`

## Guardrails

- No secrets in DB or adapter config
- Merchant hotlinks stay quarantine-only until CDN attach succeeds
- Placeholder CDN hosts (`cdn.example.test`) are flagged in admin status
