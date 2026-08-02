#!/usr/bin/env python3
import json, os, urllib.request, urllib.error
token = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()
print("token_len", len(token), "prefix", token[:6] if token else None)
headers = {
    "Content-Type": "application/json",
    "X-Taksitlio-Traffic": "internal",
    "X-Taksitlio-Internal-Token": token,
    "X-Taksitlio-Cohort-Id": "1",
    "X-Taksitlio-Cohort-Version": "2",
}
req = urllib.request.Request(
    "http://127.0.0.1:8040/v1/search-sessions",
    data=json.dumps({"conversation_id": "probe-1", "message": "laptop"}).encode(),
    headers=headers,
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        print("status", resp.status)
        print(resp.read()[:400])
except urllib.error.HTTPError as e:
    print("status", e.code)
    print(e.read()[:500])
