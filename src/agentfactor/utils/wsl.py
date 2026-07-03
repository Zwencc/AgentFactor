"""Helpers for running provider tooling inside WSL from a Windows host."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def is_windows_host() -> bool:
    return platform.system().lower() == "windows"


def wsl_available() -> bool:
    return is_windows_host() and shutil.which("wsl.exe") is not None


def command_exists(command: str) -> bool:
    """Return whether a command exists on the host or inside WSL on Windows."""
    if wsl_available():
        result = subprocess.run(
            ["wsl.exe", "sh", "-lc", f"command -v {command} >/dev/null 2>&1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    return shutil.which(command) is not None


def to_wsl_path(path: str | os.PathLike[str]) -> str:
    """Convert a Windows path to the WSL view when needed."""
    raw = str(path)
    if not wsl_available():
        return raw

    result = subprocess.run(
        ["wsl.exe", "wslpath", "-a", "-u", raw],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    expanded = Path(raw).expanduser()
    return str(expanded)


def wsl_home() -> str:
    if not wsl_available():
        return str(Path.home())
    result = subprocess.run(
        ["wsl.exe", "sh", "-lc", "printf %s \"$HOME\""],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "/home"


def ensure_wsl_tmux_wrapper() -> str:
    """Create a tiny tmux wrapper so libtmux can call WSL tmux as one executable."""
    wrapper_dir = Path.home() / ".conductor" / "bin"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = wrapper_dir / "tmux-wsl.cmd"
    wrapper.write_text("@echo off\r\nwsl.exe tmux %*\r\n", encoding="utf-8")
    return str(wrapper)
