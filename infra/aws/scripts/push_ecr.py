"""Copy images from the local (kind) registry to ECR over the registry v2 API.

    python infra/aws/scripts/push_ecr.py --registry <ecr host> --tag a8567cc --images shell ...

Why not `docker push`: Docker Desktop sends pushes through its internal HTTP proxy
(http.docker.internal:3128). On this laptop that proxy stalled: 2 images in 3 h, then
"use of closed network connection". This laptop's direct upload to S3 Mumbai measured
36 Mbit/s (2026-11-03). This script talks to ECR directly from WSL.

- The source is the kind registry (http://localhost:5001), which already holds every image
  at the same tag.
- Blobs, child manifests and the index/manifest are copied byte for byte, so digests are
  preserved.
- Blobs already in ECR are skipped (HEAD), which makes a re-run resume where it stopped.
- The ECR token comes from `aws ecr get-login-password` and stays in memory only: never
  printed, never written to disk.

Prints one line per image: `name digest` (the index or manifest digest, as k3s will pull it).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time

import httpx

MANIFEST_TYPES = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
CHUNK = 8 * 1024 * 1024


class Copier:
    def __init__(self, src: str, dst_host: str, region: str):
        self.src = src.rstrip("/")
        self.dst = f"https://{dst_host}"
        token = subprocess.run(["aws", "ecr", "get-login-password", "--region", region],
                               capture_output=True, text=True, check=True).stdout.strip()
        auth = "Basic " + base64.b64encode(f"AWS:{token}".encode()).decode()
        del token
        self.s = httpx.Client(timeout=httpx.Timeout(60.0, write=300.0))
        self.d = httpx.Client(timeout=httpx.Timeout(60.0, write=300.0),
                              headers={"Authorization": auth})
        self.bytes_up = 0
        self.blobs_skipped = 0
        self.retries = 0

    def blob(self, repo: str, digest: str) -> None:
        """Upload one blob; transport errors (ECR drops idle keep-alive connections) restart
        that blob's upload, up to 4 attempts, with backoff."""
        for attempt in range(4):
            try:
                return self._blob(repo, digest)
            except httpx.TransportError as e:
                if attempt == 3:
                    raise
                self.retries += 1
                print(f"  retry {repo} {digest[:19]}: {type(e).__name__}", file=sys.stderr)
                time.sleep(2 ** attempt)

    def _blob(self, repo: str, digest: str) -> None:
        if self.d.head(f"{self.dst}/v2/{repo}/blobs/{digest}").status_code == 200:
            self.blobs_skipped += 1
            return
        data = self.s.get(f"{self.src}/v2/{repo}/blobs/{digest}").raise_for_status().content
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(f"{repo} {digest}: source blob does not match its digest")
        r = self.d.post(f"{self.dst}/v2/{repo}/blobs/uploads/").raise_for_status()
        loc = r.headers["Location"]
        for off in range(0, len(data), CHUNK):       # chunked PATCH, then PUT ?digest=
            part = data[off:off + CHUNK]
            loc = self._abs(self.d.patch(loc, content=part, headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"{off}-{off + len(part) - 1}"}).raise_for_status()
                .headers["Location"])
        sep = "&" if "?" in loc else "?"
        self.d.put(f"{loc}{sep}digest={digest}", content=b"").raise_for_status()
        self.bytes_up += len(data)

    def _abs(self, loc: str) -> str:
        return loc if loc.startswith("http") else self.dst + loc

    def manifest(self, repo: str, ref: str, put_ref: str | None = None) -> str:
        r = self.s.get(f"{self.src}/v2/{repo}/manifests/{ref}",
                       headers={"Accept": MANIFEST_TYPES}).raise_for_status()
        body, mtype = r.content, r.headers["Content-Type"].split(";")[0]
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        m = json.loads(body)
        for child in m.get("manifests", []):          # index: children first, by digest
            self.manifest(repo, child["digest"])
        if "config" in m:
            self.blob(repo, m["config"]["digest"])
        for layer in m.get("layers", []):
            self.blob(repo, layer["digest"])
        self.d.put(f"{self.dst}/v2/{repo}/manifests/{put_ref or digest}", content=body,
                   headers={"Content-Type": mtype}).raise_for_status()
        return digest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="http://localhost:5001")
    ap.add_argument("--registry", required=True, help="ECR host <account>.dkr.ecr.<region>...")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--images", nargs="+", required=True)
    a = ap.parse_args()
    c = Copier(a.src, a.registry, a.region)
    t0 = time.monotonic()
    for name in a.images:
        t = time.monotonic()
        digest = c.manifest(f"skypiea/{name}", a.tag, put_ref=a.tag)
        print(f"{name} {digest} ({time.monotonic() - t:.1f} s)", flush=True)
    print(f"done: {len(a.images)} images, {c.bytes_up / 1e6:.1f} MB uploaded, "
          f"{c.blobs_skipped} blobs already present, {c.retries} retries, "
          f"{time.monotonic() - t0:.0f} s",
          file=sys.stderr)


if __name__ == "__main__":
    main()
