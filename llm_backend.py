"""
Local LLM backend for stacked PR analysis.

Provides a unified interface to local LLMs with automatic fallback:
  1. ollama       — HTTP to localhost:11434 (zero deps, most common)
  2. llama-cpp    — llama-cpp-python with GGUF models
  3. (disabled)   — rule-based fallback, no LLM

Works on macOS, Linux, and WSL2. No API keys needed.

Model preferences (in order):
  - qwen2.5-coder:7b     (best for code understanding)
  - qwen2.5-coder:3b     (lighter, still good)
  - llama3.2:3b           (general purpose, fast)
  - codellama:7b          (code-specific)
  - phi3:mini             (very small, decent)
  - deepseek-coder:6.7b   (code-focused)
  - mistral:7b            (general purpose)

Usage:
    from llm_backend import get_llm, LLMBackend

    llm = get_llm()                       # auto-detect best backend
    llm = get_llm(backend="ollama")       # force ollama
    llm = get_llm(model="llama3.2:3b")   # specific model

    response = llm.query("Group these files into PRs: ...")
    if llm.available:
        ...
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

# Model preference order for ollama
PREFERRED_MODELS = [
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
    "llama3.2:3b",
    "codellama:7b",
    "phi3:mini",
    "deepseek-coder:6.7b",
    "deepseek-coder-v2:lite",
    "mistral:7b",
    "llama3.1:8b",
    "gemma2:2b",
]

# For llama-cpp-python, search these paths for GGUF models
GGUF_SEARCH_PATHS = [
    Path.home() / ".cache" / "llama-cpp" / "models",
    Path.home() / ".local" / "share" / "llama-cpp" / "models",
    Path.home() / "models",
    Path.home() / ".cache" / "huggingface" / "hub",
    Path("/usr/local/share/models"),
]

# System prompt for all code analysis tasks
SYSTEM_PROMPT = """You are a code analysis assistant that helps split feature branches into \
reviewable stacked pull requests. You understand code dependencies, module boundaries, \
and what makes a good PR scope. Be concise and output structured data when asked."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Response from any LLM backend."""
    text: str
    model: str
    backend: str
    tokens_used: int = 0
    duration_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------

class LLMBackend(ABC):
    """Base class for LLM backends."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is usable right now."""
        ...

    @abstractmethod
    def query(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a prompt and get a response."""
        ...

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the model being used."""
        ...

    @property
    def available(self) -> bool:
        return self.is_available()


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

class OllamaBackend(LLMBackend):
    """
    Ollama backend — communicates via HTTP to localhost:11434.

    Auto-detects the best available model from PREFERRED_MODELS.
    Can optionally auto-pull a model if none are available.
    """

    name = "ollama"

    def __init__(self, model: str | None = None, host: str = OLLAMA_HOST, auto_pull: bool = False):
        self._host = host.rstrip("/")
        self._requested_model = model
        self._resolved_model: str | None = None
        self._auto_pull = auto_pull

    def is_available(self) -> bool:
        """Check if ollama is running and has a usable model."""
        try:
            self._resolve_model()
            return self._resolved_model is not None
        except Exception:
            return False

    def _ollama_request(self, endpoint: str, data: dict | None = None, timeout: int = 10) -> dict:
        """Make an HTTP request to ollama."""
        url = f"{self._host}{endpoint}"
        if data is not None:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = urllib.request.Request(url)

        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))

    def _list_models(self) -> list[str]:
        """List locally available models."""
        try:
            result = self._ollama_request("/api/tags", timeout=5)
            return [m["name"] for m in result.get("models", [])]
        except Exception:
            return []

    def _resolve_model(self) -> None:
        """Find the best available model."""
        if self._resolved_model:
            return

        available = self._list_models()
        if not available:
            if self._auto_pull and self._requested_model:
                self._pull_model(self._requested_model)
                self._resolved_model = self._requested_model
            return

        # If user requested a specific model
        if self._requested_model:
            # Check exact match or prefix match
            for m in available:
                if m == self._requested_model or m.startswith(self._requested_model):
                    self._resolved_model = m
                    return
            # Not found — try to pull if auto_pull
            if self._auto_pull:
                self._pull_model(self._requested_model)
                self._resolved_model = self._requested_model
            return

        # Auto-select from preference order
        available_set = set(available)
        # Also match without version tags
        available_base = {}
        for m in available:
            base = m.split(":")[0]
            available_base.setdefault(base, m)

        for preferred in PREFERRED_MODELS:
            if preferred in available_set:
                self._resolved_model = preferred
                return
            base = preferred.split(":")[0]
            if base in available_base:
                self._resolved_model = available_base[base]
                return

        # Fall back to first available
        self._resolved_model = available[0]

    def _pull_model(self, model: str) -> None:
        """Pull a model (blocking). Shows progress on stderr."""
        print(f"  Pulling {model} via ollama (this may take a few minutes)...", file=sys.stderr)
        try:
            subprocess.run(
                ["ollama", "pull", model],
                timeout=600,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  Failed to pull {model}: {e}", file=sys.stderr)

    def get_model_name(self) -> str:
        self._resolve_model()
        return self._resolved_model or "unknown"

    def query(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        self._resolve_model()
        if not self._resolved_model:
            return LLMResponse(text="", model="", backend=self.name, error="No model available")

        payload = {
            "model": self._resolved_model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        start = time.monotonic()
        try:
            result = self._ollama_request("/api/generate", payload, timeout=OLLAMA_TIMEOUT)
            elapsed = int((time.monotonic() - start) * 1000)
            return LLMResponse(
                text=result.get("response", ""),
                model=self._resolved_model,
                backend=self.name,
                tokens_used=result.get("eval_count", 0),
                duration_ms=elapsed,
            )
        except urllib.error.URLError as e:
            return LLMResponse(text="", model=self._resolved_model, backend=self.name,
                               error=f"Connection error: {e}")
        except Exception as e:
            return LLMResponse(text="", model=self._resolved_model, backend=self.name,
                               error=str(e))


# ---------------------------------------------------------------------------
# llama-cpp-python backend
# ---------------------------------------------------------------------------

class LlamaCppBackend(LLMBackend):
    """
    llama-cpp-python backend — loads GGUF models directly.

    Searches common paths for .gguf files or accepts an explicit path.
    Good fallback when ollama isn't installed.
    """

    name = "llama-cpp"

    def __init__(self, model_path: str | None = None, n_ctx: int = 4096, n_gpu_layers: int = -1):
        self._model_path = model_path
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._llm = None
        self._model_name = ""

    def is_available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return False

        path = self._find_model()
        return path is not None

    def _find_model(self) -> str | None:
        """Find a GGUF model file."""
        if self._model_path and Path(self._model_path).exists():
            return self._model_path

        # Search common locations
        for search_dir in GGUF_SEARCH_PATHS:
            if not search_dir.exists():
                continue
            # Find .gguf files, prefer smaller quantizations for speed
            gguf_files = sorted(
                search_dir.rglob("*.gguf"),
                key=lambda p: (
                    # Prefer code models
                    0 if "coder" in p.name.lower() or "code" in p.name.lower() else 1,
                    # Prefer Q4 quantization (good balance)
                    0 if "q4" in p.name.lower() else 1,
                    # Prefer smaller files
                    p.stat().st_size,
                ),
            )
            if gguf_files:
                return str(gguf_files[0])

        return None

    def _load_model(self) -> bool:
        """Lazy-load the model."""
        if self._llm is not None:
            return True

        model_path = self._find_model()
        if not model_path:
            return False

        try:
            from llama_cpp import Llama

            self._model_name = Path(model_path).stem
            print(f"  Loading {self._model_name}...", file=sys.stderr)
            self._llm = Llama(
                model_path=model_path,
                n_ctx=self._n_ctx,
                n_gpu_layers=self._n_gpu_layers,
                verbose=False,
            )
            return True
        except Exception as e:
            print(f"  Failed to load model: {e}", file=sys.stderr)
            return False

    def get_model_name(self) -> str:
        return self._model_name or "unknown"

    def query(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self._load_model():
            return LLMResponse(text="", model="", backend=self.name, error="No model loaded")

        full_prompt = f"<|system|>\n{system}<|end|>\n<|user|>\n{prompt}<|end|>\n<|assistant|>\n"

        start = time.monotonic()
        try:
            result = self._llm(
                full_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=["<|end|>", "<|user|>"],
            )
            elapsed = int((time.monotonic() - start) * 1000)
            text = result["choices"][0]["text"] if result["choices"] else ""
            tokens = result.get("usage", {}).get("total_tokens", 0)
            return LLMResponse(
                text=text.strip(),
                model=self._model_name,
                backend=self.name,
                tokens_used=tokens,
                duration_ms=elapsed,
            )
        except Exception as e:
            return LLMResponse(text="", model=self._model_name, backend=self.name, error=str(e))


# ---------------------------------------------------------------------------
# Null backend (fallback — signals callers to use rule-based logic)
# ---------------------------------------------------------------------------

class NullBackend(LLMBackend):
    """No-op backend. Callers should check .available and fall back to rules."""

    name = "none"

    def is_available(self) -> bool:
        return False

    def query(self, prompt: str, **kwargs) -> LLMResponse:
        return LLMResponse(text="", model="", backend=self.name, error="No LLM backend available")

    def get_model_name(self) -> str:
        return "none"


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

_cached_backend: LLMBackend | None = None


def get_llm(
    backend: str | None = None,
    model: str | None = None,
    auto_pull: bool = False,
    verbose: bool = True,
) -> LLMBackend:
    """
    Get the best available LLM backend.

    Auto-detection order: ollama → llama-cpp-python → null

    Args:
        backend: Force a specific backend ("ollama", "llama-cpp", "none")
        model: Force a specific model name/path
        auto_pull: If True, ollama will pull a model if none available
        verbose: Print status messages to stderr

    Environment variables:
        OLLAMA_HOST: Override ollama URL (default: http://localhost:11434)
        OLLAMA_TIMEOUT: Override ollama timeout in seconds (default: 120)
        STACKER_LLM_BACKEND: Override backend selection
        STACKER_LLM_MODEL: Override model selection
    """
    global _cached_backend

    # Env var overrides
    backend = backend or os.environ.get("STACKER_LLM_BACKEND")
    model = model or os.environ.get("STACKER_LLM_MODEL")

    # Return cached if same config
    if _cached_backend and not backend and not model:
        return _cached_backend

    if backend == "none":
        _cached_backend = NullBackend()
        return _cached_backend

    # Try each backend in order
    backends_to_try: list[LLMBackend] = []

    if backend == "ollama" or backend is None:
        backends_to_try.append(OllamaBackend(model=model, auto_pull=auto_pull))
    if backend == "llama-cpp" or backend is None:
        backends_to_try.append(LlamaCppBackend(model_path=model if model and model.endswith(".gguf") else None))

    for b in backends_to_try:
        if b.is_available():
            if verbose:
                print(f"  LLM: {b.name} ({b.get_model_name()})", file=sys.stderr)
            _cached_backend = b
            return b

    if verbose:
        print("  LLM: none available (using rule-based analysis)", file=sys.stderr)
    _cached_backend = NullBackend()
    return _cached_backend


def check_backends() -> dict[str, dict]:
    """Diagnostic: check all backends and report status."""
    results = {}

    # Ollama
    ollama = OllamaBackend()
    try:
        models = ollama._list_models()
        results["ollama"] = {
            "available": bool(models),
            "host": ollama._host,
            "models": models,
            "selected": ollama.get_model_name() if models else None,
        }
    except Exception as e:
        results["ollama"] = {"available": False, "error": str(e)}

    # llama-cpp
    llama = LlamaCppBackend()
    try:
        import llama_cpp  # noqa: F401
        model_path = llama._find_model()
        results["llama-cpp"] = {
            "available": model_path is not None,
            "library": True,
            "model_path": model_path,
        }
    except ImportError:
        results["llama-cpp"] = {
            "available": False,
            "library": False,
            "install": "pip install llama-cpp-python",
        }

    return results


# ---------------------------------------------------------------------------
# Prompt builders for specific tasks
# ---------------------------------------------------------------------------

def build_extraction_prompt(
    user_prompt: str,
    file_summaries: list[dict],
) -> str:
    """
    Build a prompt for LLM-powered file extraction.

    Args:
        user_prompt: What the user wants to extract (e.g., "the auth changes")
        file_summaries: [{path, status, added, removed, summary}, ...]
    """
    files_text = "\n".join(
        f"  {f['status']:1s} {f['path']} (+{f['added']}/-{f['removed']})"
        + (f"  # {f['summary']}" if f.get('summary') else "")
        for f in file_summaries
    )

    return f"""A developer wants to extract specific files from a feature branch into a separate PR.

Their request: "{user_prompt}"

Here are all changed files on the branch:
{files_text}

Return a JSON object with:
- "matched": list of file paths that match the request
- "reasoning": brief explanation of why each file was included
- "suggested_title": a short PR title for the extraction

Only include files that clearly match the request. Be precise, not greedy.
Return valid JSON only."""


def build_grouping_prompt(
    file_summaries: list[dict],
    dependency_edges: list[tuple[str, str]],
    max_files: int = 3,
    max_lines: int = 200,
) -> str:
    """
    Build a prompt for LLM-powered PR grouping.

    Args:
        file_summaries: [{path, status, added, removed, module, is_docs, summary}, ...]
        dependency_edges: [(file_a, file_b), ...] where a depends on b
        max_files: target max files per PR
        max_lines: target max code lines per PR (docs exempt)
    """
    files_text = "\n".join(
        f"  {f['status']:1s} {f['path']} (+{f['added']}/-{f['removed']})"
        + (f"  [docs]" if f.get('is_docs') else "")
        + (f"  module={f['module']}" if f.get('module') else "")
        for f in file_summaries
    )

    deps_text = "\n".join(f"  {a} → depends on → {b}" for a, b in dependency_edges)
    if not deps_text:
        deps_text = "  (no dependencies detected)"

    return f"""Split these changed files into stacked pull requests for code review.

Constraints:
- ~{max_files} files per PR (soft limit)
- ~{max_lines} lines of code per PR (docs/config files are exempt from line count)
- Test files (test_*.py, *_test.go, *.test.ts, etc.) MUST be in the same PR as the code they test
- Files that depend on each other should be in different PRs, with dependencies landing first
- Each PR should be a logical, reviewable unit

Changed files:
{files_text}

Dependencies (A depends on B means B must merge first):
{deps_text}

Return a JSON object with:
- "prs": list of objects, each with:
  - "title": short descriptive PR title
  - "files": list of file paths
  - "depends_on": list of PR indices (1-based) this PR depends on
  - "merge_strategy": "squash" | "merge" | "rebase"
  - "reasoning": brief explanation of why these files are grouped together

Order PRs so dependencies come first. Return valid JSON only."""


def build_pr_description_prompt(
    title: str,
    files: list[dict],
    diff_summary: str,
    depends_on: list[str],
    merge_strategy: str,
) -> str:
    """
    Build a prompt for generating a PR description.
    """
    files_text = "\n".join(
        f"  {f['status']:1s} {f['path']} (+{f['added']}/-{f['removed']})"
        for f in files
    )

    deps_text = ", ".join(depends_on) if depends_on else "none"

    return f"""Write a concise GitHub pull request description.

PR title: {title}
Merge strategy: {merge_strategy}
Depends on: {deps_text}

Files:
{files_text}

Diff summary (first 500 chars of key changes):
{diff_summary[:500]}

Write a description with:
1. A "## Summary" section (2-3 bullet points of what this PR does)
2. A "## Changes" section (brief description of each file change)
3. A "## Test plan" section (how to verify this works)

Keep it concise. Do not use filler text. Output markdown only."""


def parse_json_response(response: LLMResponse) -> dict | list | None:
    """Attempt to parse JSON from an LLM response, handling common issues."""
    if not response.ok:
        return None

    text = response.text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (fences)
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object or array in the text
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            if start == -1:
                continue
            # Find matching close
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break
        return None
