"""make fetch-models against a local fake release (http on 127.0.0.1 only). Offline."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSET = "skypiea-models-af000231.tar.gz"
pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


@pytest.fixture
def server(tmp_path):
    rel = tmp_path / "release"
    rel.mkdir()
    h = partial(SimpleHTTPRequestHandler, directory=str(rel))
    h.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield rel, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _repo(tmp_path) -> Path:
    """A throwaway copy of what the script needs, so models/ of the real repo is untouched."""
    r = tmp_path / "repo"
    (r / "demo").mkdir(parents=True)
    (r / "configs" / "live").mkdir(parents=True)
    shutil.copy(ROOT / "demo" / "fetch_models.sh", r / "demo")
    shutil.copy(ROOT / "configs" / "live" / "models_files.sha256", r / "configs" / "live")
    return r


def _run(repo, url, **env):
    return subprocess.run(["bash", "demo/fetch_models.sh"], cwd=repo, capture_output=True,
                          text=True, env={**os.environ, "MODELS_BASE_URL": url, **env})


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _publish(rel: Path, blob: bytes, sha: str | None = None) -> None:
    (rel / ASSET).write_bytes(blob)
    (rel / "SHA256SUMS").write_text(f"{sha or hashlib.sha256(blob).hexdigest()}  {ASSET}\n")


def test_not_published_yet_gives_a_clear_message(server, tmp_path) -> None:
    _, url = server
    p = _run(_repo(tmp_path), url)
    assert p.returncode == 2 and "not available" in p.stderr and "Nothing was changed" in p.stderr


def test_refuses_plain_http_to_other_hosts(tmp_path) -> None:
    p = _run(_repo(tmp_path), "http://example.com/x")
    assert p.returncode == 1 and "https only" in p.stderr


def test_archive_checksum_mismatch_is_refused(server, tmp_path) -> None:
    rel, url = server
    _publish(rel, _tar({"lgbm-x/MANIFEST.json": b"{}"}), sha="0" * 64)
    repo = _repo(tmp_path)
    p = _run(repo, url)
    assert p.returncode == 3 and "checksum mismatch" in p.stderr
    assert not (repo / "models").exists()


def test_tampered_bundle_file_is_refused(server, tmp_path) -> None:
    rel, url = server
    _publish(rel, _tar({"lgbm-coding-af00023/MANIFEST.json": b"{}"}))
    p = _run(_repo(tmp_path), url)
    assert p.returncode == 3 and "per-file" in p.stderr


def test_real_archive_round_trip(server, tmp_path) -> None:
    if not all((ROOT / "models" / b / "MANIFEST.json").exists() for b in (
            "lgbm-coding-af00023", "lgbm-support-aabdfd2", "lgbm-prd-a7642d0")):
        pytest.skip("real bundles not in models/ (CI): nothing to round-trip")
    rel, url = server
    out = subprocess.run(["bash", "demo/build_models_archive.sh"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    assert ASSET in out.stdout
    shutil.copy(ROOT / "dist" / ASSET, rel / ASSET)
    shutil.copy(ROOT / "dist" / "SHA256SUMS", rel / "SHA256SUMS")
    repo = _repo(tmp_path)
    p = _run(repo, url)
    assert p.returncode == 0, p.stderr
    assert (repo / "models" / "lgbm-support-aabdfd2" / "MANIFEST.json").exists()
    assert (repo / "models" / "ATTRIBUTION.md").exists()


def _dry(repo, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("MODELS_BASE_URL", "MODELS_REPO")}
    return subprocess.run(["bash", "demo/fetch_models.sh"], cwd=repo, capture_output=True,
                          text=True, env={**e, "FETCH_DRY_RUN": "1", **env})


def _git_origin(repo: Path, url: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)


def test_default_url_comes_from_the_configured_slug(tmp_path) -> None:
    repo = _repo(tmp_path)
    _git_origin(repo, "git@gitlab.com:someone/fork.git")      # origin is ignored by default
    p = _dry(repo)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ("https://github.com/SYNKRONOX/skypiea-prewarm/releases/"
                                f"download/v1.2/{ASSET}")


def test_models_repo_override(tmp_path) -> None:
    p = _dry(_repo(tmp_path), MODELS_REPO="team/fork")
    assert p.stdout.strip().startswith("https://github.com/team/fork/releases/download/v1.2/")


@pytest.mark.parametrize("origin", ["git@github.com:team/fork.git",
                                    "https://github.com/team/fork.git",
                                    "https://github.com/team/fork"])
def test_empty_slug_falls_back_to_github_origin(tmp_path, origin) -> None:
    repo = _repo(tmp_path)
    _git_origin(repo, origin)
    p = _dry(repo, MODELS_REPO="")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().startswith("https://github.com/team/fork/releases/download/v1.2/")


def test_empty_slug_with_non_github_origin_is_refused(tmp_path) -> None:
    repo = _repo(tmp_path)
    _git_origin(repo, "https://gitlab.com/team/fork.git")
    p = _dry(repo, MODELS_REPO="")
    assert p.returncode == 1 and "not a GitHub URL" in p.stderr


def test_bad_slug_is_refused(tmp_path) -> None:
    p = _dry(_repo(tmp_path), MODELS_REPO="a/b/c; rm")
    assert p.returncode == 1 and "<owner>/<repo>" in p.stderr
