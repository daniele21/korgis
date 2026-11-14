"""No-network and non-destructive safeguards for pinned macOS installer."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install_llama_cpp_v1_macos.sh"


def test_installer_shell_syntax_and_help():
    assert subprocess.run(["bash", "-n", str(INSTALLER)], check=False).returncode == 0
    proc = subprocess.run(["bash", str(INSTALLER), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "Never overwrite" in proc.stdout
    assert "LOCAL_LLM_SERVER_BIN" in proc.stdout


def test_installer_rejects_relative_root_without_side_effects(tmp_path):
    proc = subprocess.run(
        ["bash", str(INSTALLER), "--root", "relative-directory"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert not (tmp_path / "relative-directory").exists()


def test_installer_preserves_existing_destination_before_network(tmp_path):
    fake_bin = tmp_path / "fake-tools"
    fake_bin.mkdir()
    fake_uname = fake_bin / "uname"
    fake_uname.write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac\n',
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    root = tmp_path / "engines"
    existing = root / "v1.0.0-2d8a325"
    existing.mkdir(parents=True)
    marker = existing / "sentinel"
    marker.write_text("retain", encoding="utf-8")
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"])
    proc = subprocess.run(
        ["bash", str(INSTALLER), "--root", str(root)],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "refusing to overwrite" in proc.stderr
    assert marker.read_text(encoding="utf-8") == "retain"


def test_installer_refuses_concurrent_install_before_network(tmp_path):
    fake_bin = tmp_path / "fake-tools"
    fake_bin.mkdir()
    fake_uname = fake_bin / "uname"
    fake_uname.write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac\n',
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    root = tmp_path / "engines"
    lock = root / ".llama-v1-install.lock"
    lock.mkdir(parents=True)
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"])
    proc = subprocess.run(
        ["bash", str(INSTALLER), "--root", str(root)],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "another installation" in proc.stderr
    assert lock.is_dir()
    assert not (root / "v1.0.0-2d8a325").exists()
