"""Full setup — install all external tools and Python deps."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys

BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def run_full_setup(
    ollama_model: str | None = None,
    minimal: bool = False,
) -> bool:
    """Install all external tools and Python deps."""
    print(f"\n{BOLD}{'=' * 50}{RESET}")
    print(f"{BOLD}  kapa-stacker full setup{RESET}")
    print(f"{BOLD}{'=' * 50}{RESET}")
    print(f"  Platform: {CYAN}{_detect_platform()}{RESET}\n")

    ok = True
    ok = _install_python_deps() and ok
    ok = _install_ctags() and ok
    ok = _install_scc() and ok
    ok = _install_ast_grep() and ok
    ok = _install_ollama(ollama_model, minimal) and ok

    _print_status()

    if ok:
        print(f"\n  {GREEN}Setup complete!{RESET}")
        print(f"  Run: {CYAN}kapa-stacker --index{RESET} to pre-compute caches")
        print(f"  Then: {CYAN}kapa-stacker{RESET} to analyze your branch")
    else:
        print(f"\n  {YELLOW}Some tools failed to install. Core features still work.{RESET}")

    print()
    return ok


def _detect_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    return "wsl2"
        except FileNotFoundError:
            pass
        return "linux"
    return system


def _install_python_deps() -> bool:
    """Install Python packages: networkx, lizard, tree-sitter-languages."""
    print(f"  {BOLD}Python packages{RESET}")
    deps = ["networkx", "lizard"]
    ok = True
    for dep in deps:
        if _pip_install(dep):
            print(f"    {GREEN}✓{RESET} {dep}")
        else:
            print(f"    {RED}✗{RESET} {dep}")
            ok = False

    # tree-sitter-languages is optional, can fail on some platforms
    if _pip_install("tree-sitter-languages"):
        print(f"    {GREEN}✓{RESET} tree-sitter-languages")
    else:
        print(f"    {YELLOW}⊘{RESET} tree-sitter-languages {DIM}(optional){RESET}")

    return ok


def _install_ctags() -> bool:
    """Install universal-ctags."""
    print(f"  {BOLD}universal-ctags{RESET}")
    if shutil.which("ctags"):
        print(f"    {GREEN}✓{RESET} already installed")
        return True
    return _install_system_pkg("universal-ctags", "ctags")


def _install_scc() -> bool:
    """Install scc (Sloc Cloc and Code)."""
    print(f"  {BOLD}scc{RESET}")
    if shutil.which("scc"):
        print(f"    {GREEN}✓{RESET} already installed")
        return True
    return _install_system_pkg("scc", "scc")


def _install_ast_grep() -> bool:
    """Install ast-grep."""
    print(f"  {BOLD}ast-grep{RESET}")
    if shutil.which("ast-grep") or shutil.which("sg"):
        print(f"    {GREEN}✓{RESET} already installed")
        return True

    plat = _detect_platform()
    if plat == "macos" and shutil.which("brew"):
        return _run_cmd("brew install ast-grep", "ast-grep")

    # Try cargo install
    if shutil.which("cargo"):
        return _run_cmd("cargo install ast-grep", "ast-grep")

    # Try npm
    if shutil.which("npm"):
        return _run_cmd("npm install -g @ast-grep/cli", "ast-grep")

    print(f"    {YELLOW}⊘{RESET} skipped — install manually: {DIM}cargo install ast-grep{RESET}")
    return False


def _install_ollama(model: str | None, minimal: bool) -> bool:
    """Install and configure ollama."""
    print(f"  {BOLD}ollama{RESET}")
    from src.infrastructure.llm.setup_ollama import run_setup
    return run_setup(model=model, minimal=minimal)


def _install_system_pkg(pkg_name: str, binary_name: str) -> bool:
    """Install via brew (macOS) or apt/dnf (Linux)."""
    plat = _detect_platform()

    if plat == "macos" and shutil.which("brew"):
        return _run_cmd(f"brew install {pkg_name}", binary_name)

    if plat in ("linux", "wsl2"):
        if shutil.which("apt-get"):
            return _run_cmd(f"sudo apt-get install -y {pkg_name}", binary_name)
        if shutil.which("dnf"):
            return _run_cmd(f"sudo dnf install -y {pkg_name}", binary_name)

    # Try go install for scc
    if binary_name == "scc" and shutil.which("go"):
        return _run_cmd("go install github.com/boyter/scc/v3@latest", binary_name)

    print(f"    {YELLOW}⊘{RESET} skipped — install manually")
    return False


def _pip_install(package: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", package],
            capture_output=True, timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_cmd(cmd: str, label: str) -> bool:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, timeout=300,
        )
        if result.returncode == 0:
            print(f"    {GREEN}✓{RESET} {label}")
            return True
        print(f"    {RED}✗{RESET} {label}")
        return False
    except Exception:
        print(f"    {RED}✗{RESET} {label}")
        return False


def _print_status():
    """Print status of all tools."""
    print(f"\n  {BOLD}Tool status:{RESET}")
    tools = [
        ("python", sys.executable),
        ("networkx", None),
        ("lizard", None),
        ("ctags", "ctags"),
        ("scc", "scc"),
        ("ast-grep", "ast-grep"),
        ("ollama", "ollama"),
    ]
    for name, binary in tools:
        if binary:
            ok = shutil.which(binary) is not None
        else:
            ok = _check_python_module(name)
        icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        print(f"    {icon} {name}")


def _check_python_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False
