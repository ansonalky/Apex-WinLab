#!/usr/bin/env python3
"""Download one Apex Legends telemetry payload and record a manifest.

The response body is stored byte-for-byte. It is not reformatted.
An existing file is reused unless --refresh is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = (
    "https://overstat.gg/api/stats/livedata/"
    "623ae07f-81cf-4634-ad7f-2d27f7911896?format=timeseries"
)
MATCH_ID = "623ae07f-81cf-4634-ad7f-2d27f7911896"
RAW_FILENAME = f"{MATCH_ID}.timeseries.json"
MANIFEST_FILENAME = f"{MATCH_ID}.timeseries.manifest.json"
USER_AGENT = "ApexWinLab/0.1 (milestone-1 telemetry download)"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paths_for(data_dir: Path) -> tuple[Path, Path]:
    raw_path = data_dir / "raw" / RAW_FILENAME
    manifest_path = data_dir / "manifests" / MANIFEST_FILENAME
    return raw_path, manifest_path


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))


def download_bytes(url: str, timeout: float, retries: int) -> bytes:
    if retries < 1:
        raise ValueError("retries must be at least 1")
    last_error: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                if status is None or status >= 400:
                    raise urllib.error.HTTPError(
                        url,
                        int(status or 0),
                        f"HTTP {status}",
                        response.headers,
                        None,
                    )
                return response.read()
        except Exception as exc:
            if not is_retryable(exc) or attempt == retries:
                raise
            last_error = exc
            delay = min(2 ** (attempt - 1), 8)
            print(
                f"Attempt {attempt}/{retries} failed ({exc}). Retrying in {delay}s.",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError(f"download failed: {last_error}")


def require_json(data: bytes) -> None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"Rejected response: body is not UTF-8 JSON ({exc}).") from exc
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Rejected response: body is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno}: {exc.msg})."
        ) from exc


def write_manifest(path: Path, url: str, retrieved_at: str, filename: str, digest: str) -> dict:
    manifest = {
        "source_url": url,
        "retrieved_at": retrieved_at,
        "filename": filename,
        "sha256": digest,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def read_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read manifest {path}: {exc}") from exc
    required = ("source_url", "retrieved_at", "filename", "sha256")
    missing = [key for key in required if key not in payload]
    if missing:
        raise SystemExit(f"Manifest {path} is missing fields: {', '.join(missing)}")
    return payload


def print_manifest(manifest: dict, reused: bool) -> None:
    action = "Reused existing download" if reused else "Downloaded"
    print(action)
    print(f"source_url: {manifest['source_url']}")
    print(f"retrieved_at: {manifest['retrieved_at']}")
    print(f"filename: {manifest['filename']}")
    print(f"sha256: {manifest['sha256']}")


def reuse_existing(raw_path: Path, manifest_path: Path) -> dict:
    if not manifest_path.exists():
        raise SystemExit(
            f"Found {raw_path} but no manifest at {manifest_path}. "
            "Refusing to invent a retrieval time. Re-run with --refresh."
        )
    manifest = read_manifest(manifest_path)
    actual = sha256_file(raw_path)
    if actual != manifest["sha256"]:
        raise SystemExit(
            "Existing file does not match the manifest SHA-256. "
            "Refusing to reuse it. Re-run with --refresh."
        )
    return manifest


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download one Overstat timeseries telemetry payload."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Source URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory for raw payloads and manifests (default: data)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download again even if a local copy already exists",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-attempt socket timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Maximum attempts, including the first (default: 3)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.url != DEFAULT_URL:
        raise SystemExit(
            "This milestone downloads one payload. "
            f"--url must stay {DEFAULT_URL}"
        )

    raw_path, manifest_path = paths_for(args.data_dir)
    if raw_path.exists() and not args.refresh:
        manifest = reuse_existing(raw_path, manifest_path)
        print_manifest(manifest, reused=True)
        return 0

    try:
        body = download_bytes(args.url, timeout=args.timeout, retries=args.retries)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Rejected HTTP {exc.code} from {args.url}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Download failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SystemExit(f"Download timed out after {args.retries} attempt(s).") from exc

    retrieved_at = utc_now_iso()
    require_json(body)
    digest = sha256_bytes(body)
    atomic_write(raw_path, body)
    relative_name = Path("data") / "raw" / RAW_FILENAME
    if args.data_dir.resolve() == Path("data").resolve():
        filename = relative_name.as_posix()
    else:
        filename = raw_path.as_posix()
    manifest = write_manifest(
        manifest_path,
        url=args.url,
        retrieved_at=retrieved_at,
        filename=filename,
        digest=digest,
    )
    print_manifest(manifest, reused=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
