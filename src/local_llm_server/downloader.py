"""
downloader.py — model download with resume support and inline progress bar.

No external dependencies (uses stdlib urllib only).
"""
from __future__ import annotations

import hashlib
import logging
import sys
import urllib.request
from pathlib import Path

logger = logging.getLogger("local-llm.downloader")


def ensure_model(
    url: str,
    dest: Path,
    *,
    resume: bool = True,
    no_download: bool = False,
    expected_sha256: str | None = None,
    verify_existing: bool = False,
) -> None:
    """
    Ensure the model file exists at *dest*.

    If it is already present, this is a no-op.
    If it is absent and *no_download* is True, raises FileNotFoundError.
    Otherwise downloads from *url* with resume support.
    """
    if dest.exists():
        if verify_existing and expected_sha256 is not None:
            verify_file_sha256(dest, expected_sha256)
        return

    if no_download:
        raise FileNotFoundError(
            f"Model not found at {dest} and --no-download is set. "
            "Run 'local-llm download <model>' first."
        )

    if not url:
        raise ValueError(
            f"Model not found at {dest} and no download URL is configured for this model."
        )

    download_model(
        url=url,
        dest=dest,
        resume=resume,
        expected_sha256=expected_sha256,
    )


def download_model(
    url: str,
    dest: Path,
    *,
    resume: bool = True,
    max_retries: int = 3,
    expected_sha256: str | None = None,
) -> None:
    """
    Download *url* to *dest* with resume, atomic write, and a progress bar.

    Uses a temporary *.part* file and renames atomically on success.
    Retries up to *max_retries* times on network errors.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    for attempt in range(1, max_retries + 1):
        try:
            _download_attempt(url=url, dest=dest, part=part, resume=resume)
            if expected_sha256 is not None:
                verify_file_sha256(dest, expected_sha256)
            return
        except Exception as exc:
            if attempt == max_retries:
                raise RuntimeError(f"Download failed after {max_retries} attempts: {exc}") from exc
            logger.warning("Download attempt %d/%d failed: %s — retrying…", attempt, max_retries, exc)


def _download_attempt(url: str, dest: Path, part: Path, resume: bool) -> None:
    resume_size = part.stat().st_size if (resume and part.exists()) else 0

    req = urllib.request.Request(url, headers={"User-Agent": "local-llm-server/0.1"})
    if resume_size > 0:
        req.add_header("Range", f"bytes={resume_size}-")
        logger.info("Resuming download from %.1f MB", resume_size / 1024 / 1024)

    with urllib.request.urlopen(req, timeout=3600) as response:
        content_length = int(response.headers.get("Content-Length") or 0)
        total = content_length + resume_size

        downloaded = resume_size
        mode = "ab" if resume_size > 0 else "wb"
        chunk_size = 1024 * 1024  # 1 MB

        logger.info("Downloading %s → %s (%.1f GB)", url, dest, total / 1024 / 1024 / 1024)

        with open(part, mode) as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                _print_progress(downloaded, total)

    # Final newline after progress bar
    print(flush=True)

    # Validate size if server reported Content-Length
    if total > 0 and part.stat().st_size < total:
        raise RuntimeError(f"Incomplete download: got {part.stat().st_size} bytes, expected {total}")

    # Atomic rename
    part.rename(dest)
    logger.info("Model saved to %s", dest)


def _print_progress(downloaded: int, total: int) -> None:
    if total <= 0:
        mb = downloaded / 1024 / 1024
        print(f"\r  Downloading… {mb:.0f} MB", end="", flush=True)
        return

    pct = downloaded / total
    bar_width = 30
    filled = int(bar_width * pct)
    bar = "█" * filled + "░" * (bar_width - filled)
    dl_gb = downloaded / 1024 / 1024 / 1024
    total_gb = total / 1024 / 1024 / 1024
    print(
        f"\r  [{bar}] {pct * 100:.1f}% — {dl_gb:.2f}/{total_gb:.2f} GB",
        end="",
        file=sys.stderr,
        flush=True,
    )



def verify_file_sha256(path: Path, expected_sha256: str) -> None:
    """Verify one local artifact against an expected SHA-256 digest."""
    expected = str(expected_sha256).strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected_sha256 must be a SHA-256 hex digest")

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeError(f"Cannot hash downloaded artifact {path}: {exc}") from exc

    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )
