#!/usr/bin/env python3
"""
Setup script for ollama — the local LLM backend.

Detects platform (macOS, Linux, WSL2), installs ollama if missing,
starts the service, pulls a code-focused model, and verifies it works.

Usage:
    python setup_ollama.py                    # interactive, picks best model
    python setup_ollama.py --model llama3.2:3b  # specific model
    python setup_ollama.py --check            # just verify, don't install
    python setup_ollama.py --minimal          # smallest usable model (~2GB)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Models ordered by code quality (best first), with approximate sizes
MODELS = [
    ("qwen2.5-coder:7b", "4.7 GB", "Best for code analysis"),
    ("qwen2.5-coder:3b", "2.0 GB", "Good balance of quality and speed"),
    ("llama3.2:3b", "2.0 GB", "General purpose, fast"),
    ("codellama:7b", "3.8 GB", "Meta's code model"),
    ("deepseek-coder-v2:lite", "8.9 GB", "Strong code understanding, large"),
    ("phi3:mini", "2.3 GB", "Microsoft, very small and decent"),
    ("gemma2:2b", "1.6 GB", "Google, smallest usable model"),
]

MINIMAL_MODEL = "gemma2:2b"
DEFAULT_MODEL = "qwen2.5-coder:7b"

# ANSI
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform() -> str:
    """Return 'macos', 'linux', 'wsl2', or 'unknown'."""
    system = platform.system().lower()

    if system == "darwin":
        return "macos"

    if system == "linux":
        if _is_wsl():
            return "wsl2"
        return "linux"

    return "unknown"


def _is_wsl() -> bool:
    """Detect WSL2 environment."""
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower()
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Ollama status checks
# ---------------------------------------------------------------------------

def is_ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def is_ollama_running() -> bool:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def list_models() -> list[str]:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def test_model(model: str) -> bool:
    """Quick smoke test — send a trivial prompt."""
    try:
        payload = json.dumps({
            "model": model,
            "prompt": "Reply with just the word 'ok'.",
            "stream": False,
            "options": {"num_predict": 5},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        return bool(data.get("response", "").strip())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def install_ollama(plat: str) -> bool:
    """Install ollama for the detected platform."""
    print(f"\n{BOLD}Installing ollama...{RESET}")

    if plat == "macos":
        return _install_macos()
    elif plat in ("linux", "wsl2"):
        return _install_linux()
    else:
        print(f"{RED}Unsupported platform: {plat}{RESET}")
        print(f"Install manually: https://ollama.com/download")
        return False


def _install_macos() -> bool:
    """Install via brew or direct download."""
    if shutil.which("brew"):
        print(f"  {DIM}$ brew install ollama{RESET}")
        result = subprocess.run(
            ["brew", "install", "ollama"],
            capture_output=False,
        )
        return result.returncode == 0

    # Direct download fallback
    print(f"  Homebrew not found. Installing via curl...")
    print(f"  {DIM}$ curl -fsSL https://ollama.com/install.sh | sh{RESET}")
    result = subprocess.run(
        ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
        capture_output=False,
    )
    return result.returncode == 0


def _install_linux() -> bool:
    """Install via official script."""
    print(f"  {DIM}$ curl -fsSL https://ollama.com/install.sh | sh{RESET}")
    result = subprocess.run(
        ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
        capture_output=False,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------

def start_ollama(plat: str) -> bool:
    """Start the ollama service in the background."""
    print(f"\n{BOLD}Starting ollama service...{RESET}")

    if plat == "macos":
        # Try brew services first, then direct
        if shutil.which("brew"):
            subprocess.run(
                ["brew", "services", "start", "ollama"],
                capture_output=True,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        # Linux/WSL2 — try systemctl, then direct
        systemctl = subprocess.run(
            ["systemctl", "is-active", "ollama"],
            capture_output=True, text=True,
        )
        if systemctl.stdout.strip() != "active":
            # Try systemctl start
            started = subprocess.run(
                ["sudo", "systemctl", "start", "ollama"],
                capture_output=True,
            )
            if started.returncode != 0:
                # Direct fallback
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    # Wait for service to be ready
    for i in range(15):
        if is_ollama_running():
            print(f"  {GREEN}Service is running{RESET}")
            return True
        time.sleep(1)
        print(f"  Waiting... ({i+1}s)", end="\r")

    print(f"\n  {RED}Service failed to start after 15s{RESET}")
    return False


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

def pull_model(model: str) -> bool:
    """Pull a model. Shows progress."""
    print(f"\n{BOLD}Pulling {model}...{RESET}")

    size = next(
        (s for name, s, _ in MODELS if name == model),
        "unknown size",
    )
    print(f"  Size: ~{size}")
    print(f"  This may take a few minutes on first download.\n")

    result = subprocess.run(
        ["ollama", "pull", model],
        capture_output=False,
    )
    return result.returncode == 0


def pick_model(minimal: bool = False) -> str:
    """Pick the best model for the user's setup."""
    if minimal:
        return MINIMAL_MODEL

    available = list_models()
    if not available:
        return DEFAULT_MODEL

    # Check if any preferred model is already pulled
    for name, _, _ in MODELS:
        for avail in available:
            if avail == name or avail.startswith(name.split(":")[0]):
                return avail

    return DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Main setup flow
# ---------------------------------------------------------------------------

def run_setup(
    model: str | None = None,
    check_only: bool = False,
    minimal: bool = False,
) -> bool:
    """
    Full setup flow:
      1. Detect platform
      2. Install ollama if missing
      3. Start service if not running
      4. Pull model if needed
      5. Verify with smoke test
    """
    plat = detect_platform()

    print(f"\n{BOLD}{'=' * 50}{RESET}")
    print(f"{BOLD}  Ollama Setup for kapa-stacker{RESET}")
    print(f"{BOLD}{'=' * 50}{RESET}")
    print(f"  Platform : {CYAN}{plat}{RESET}")

    # Step 1: Check installation
    installed = is_ollama_installed()
    print(f"  Installed: {_status(installed)}")

    running = is_ollama_running()
    print(f"  Running  : {_status(running)}")

    available = list_models()
    print(f"  Models   : {len(available)} available")
    if available:
        for m in available[:5]:
            print(f"    - {m}")
        if len(available) > 5:
            print(f"    ... +{len(available)-5} more")

    if check_only:
        _print_verdict(installed, running, available)
        return installed and running and bool(available)

    # Step 2: Install if needed
    if not installed:
        resp = input(f"\n  Ollama not found. Install it? [Y/n] ").strip().lower()
        if resp in ("", "y", "yes"):
            if not install_ollama(plat):
                print(f"\n  {RED}Installation failed.{RESET}")
                print(f"  Install manually: https://ollama.com/download")
                return False
        else:
            print(f"  Skipped installation.")
            return False

    # Step 3: Start service if needed
    if not is_ollama_running():
        if not start_ollama(plat):
            print(f"\n  {RED}Could not start ollama service.{RESET}")
            print(f"  Try manually: {CYAN}ollama serve{RESET}")
            return False

    # Step 4: Pull model
    target_model = model or pick_model(minimal=minimal)
    available = list_models()

    already_pulled = any(
        m == target_model or m.startswith(target_model.split(":")[0])
        for m in available
    )

    if already_pulled:
        # Find exact name
        for m in available:
            if m == target_model or m.startswith(target_model.split(":")[0]):
                target_model = m
                break
        print(f"\n  {GREEN}Model {target_model} already available{RESET}")
    else:
        print(f"\n  Model {target_model} not found locally.")
        resp = input(f"  Pull it now? [Y/n] ").strip().lower()
        if resp in ("", "y", "yes"):
            if not pull_model(target_model):
                print(f"\n  {RED}Failed to pull {target_model}{RESET}")
                return False
        else:
            print(f"  Skipped model pull.")
            return False

    # Step 5: Smoke test
    print(f"\n{BOLD}Verifying...{RESET}")
    print(f"  Testing {target_model} with a quick prompt...")

    if test_model(target_model):
        print(f"  {GREEN}Model responds correctly!{RESET}")
    else:
        print(f"  {YELLOW}Model loaded but response was empty. It may need a moment to warm up.{RESET}")

    # Done
    print(f"\n{BOLD}{'=' * 50}{RESET}")
    print(f"  {GREEN}Setup complete!{RESET}")
    print(f"  Model: {CYAN}{target_model}{RESET}")
    print(f"\n  {BOLD}Usage:{RESET}")
    print(f"    {CYAN}python stacked_pr_analyzer.py --ai{RESET}")
    print(f"    {CYAN}python stacked_pr_analyzer.py --ai --extract \"auth changes\"{RESET}")
    print(f"    {CYAN}python stacked_pr_analyzer.py --ai --generate-plan{RESET}")
    print(f"{BOLD}{'=' * 50}{RESET}\n")

    return True


def _status(ok: bool) -> str:
    return f"{GREEN}yes{RESET}" if ok else f"{RED}no{RESET}"


def _print_verdict(installed: bool, running: bool, models: list) -> None:
    print()
    if installed and running and models:
        print(f"  {GREEN}Ready to use with --ai{RESET}")
    elif not installed:
        print(f"  {RED}Not installed.{RESET} Run: {CYAN}python setup_ollama.py{RESET}")
    elif not running:
        print(f"  {YELLOW}Installed but not running.{RESET} Run: {CYAN}ollama serve{RESET}")
    elif not models:
        print(f"  {YELLOW}Running but no models.{RESET} Run: {CYAN}ollama pull qwen2.5-coder:7b{RESET}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Setup ollama for kapa-stacker's --ai mode.",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help=f"Model to install (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check status without installing anything",
    )
    parser.add_argument(
        "--minimal", action="store_true",
        help=f"Use smallest model ({MINIMAL_MODEL}, ~1.6 GB)",
    )
    parser.add_argument(
        "--list-models", action="store_true",
        help="Show recommended models with sizes",
    )
    args = parser.parse_args()

    if args.list_models:
        print(f"\n{BOLD}  Recommended models:{RESET}\n")
        for name, size, desc in MODELS:
            marker = " *" if name == DEFAULT_MODEL else ""
            print(f"    {name:30s} {size:>8s}  {DIM}{desc}{RESET}{marker}")
        print(f"\n  {DIM}* = default{RESET}\n")
        return

    success = run_setup(
        model=args.model,
        check_only=args.check,
        minimal=args.minimal,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
