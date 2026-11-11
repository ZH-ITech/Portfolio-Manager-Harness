"""Isolated code execution (PMH-16).

'process' mode: a separate Python interpreter in a temporary folder, with a
wall-clock timeout and a scrubbed environment (no API keys or secrets).
On Linux/macOS it also enforces CPU, memory, file-size and process limits.
Windows has no equivalent of those limits, so local Windows runs only get the
timeout and the clean environment; use Docker (or the Linux server) for the full
limits. Production should use a container/microVM backend behind the same
run_code() interface."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from app.config import settings

try:  # Unix only
    import resource
    HAS_RLIMITS = True
except ImportError:  # Windows
    resource = None
    HAS_RLIMITS = False

_HERE = os.path.dirname(__file__)
_ANALYSIS = os.path.join(_HERE, "..", "analysis")
# files copied into each sandbox run: (source, name inside the sandbox)
_INJECT = [(os.path.join(_HERE, "ptc.py"), "ptc.py"),
           (os.path.join(_ANALYSIS, "technical.py"), "ta.py"),
           (os.path.join(_ANALYSIS, "performance.py"), "perf.py"),
           (os.path.join(_ANALYSIS, "fundamentals.py"), "fa.py")]
MAX_OUTPUT = 20_000


def _limits(mem_mb: int, cpu_s: int):
    def apply():
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        os.setsid()
    return apply


def _clean_env(workdir: str, ptc_token: str) -> dict:
    env = {
        "HOME": workdir, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg",
        "PTC_PROXY_URL": settings.ptc_proxy_url, "PTC_TOKEN": ptc_token,
    }
    if os.name == "nt":
        # Python on Windows needs these to start (random numbers, sockets, temp files)
        for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "COMSPEC"):
            if k in os.environ:
                env[k] = os.environ[k]
        env["TEMP"] = env["TMP"] = env["USERPROFILE"] = workdir
    else:
        env["PATH"] = "/usr/bin:/bin"
    return env


def run_code(code: str, ptc_token: str = "", timeout_s: int | None = None,
             mem_mb: int | None = None) -> dict:
    """Run code in the sandbox service when SANDBOX_URL is set (production: a separate,
    network-isolated container), otherwise in a local isolated process (development)."""
    if settings.sandbox_url:
        return _run_remote(code, ptc_token, timeout_s, mem_mb)
    return run_local(code, ptc_token, timeout_s, mem_mb)


def _run_remote(code, ptc_token, timeout_s, mem_mb) -> dict:
    import httpx
    t = timeout_s or settings.sandbox_timeout_s
    try:
        r = httpx.post(f"{settings.sandbox_url.rstrip('/')}/run", timeout=t + 15,
                       json={"code": code, "ptc_token": ptc_token, "timeout_s": t, "mem_mb": mem_mb})
        if r.status_code == 200:
            return r.json()
        return {"status": "error", "returncode": None, "stdout": "", "duration_s": 0,
                "stderr": f"Sandbox rejected the request (HTTP {r.status_code}): {r.text[:300]}"}
    except httpx.HTTPError as e:
        return {"status": "error", "returncode": None, "stdout": "", "duration_s": 0,
                "stderr": f"Sandbox service unavailable: {type(e).__name__}. Try again shortly."}


def run_local(code: str, ptc_token: str = "", timeout_s: int | None = None,
              mem_mb: int | None = None) -> dict:
    timeout_s = timeout_s or settings.sandbox_timeout_s
    mem_mb = mem_mb or settings.sandbox_memory_mb
    workdir = tempfile.mkdtemp(prefix="sbx_")
    try:
        for src, name in _INJECT:
            shutil.copy(src, os.path.join(workdir, name))
        with open(os.path.join(workdir, "main.py"), "w", encoding="utf-8") as f:
            f.write(code)
        kwargs = {"preexec_fn": _limits(mem_mb, timeout_s)} if HAS_RLIMITS else {}
        start = time.time()
        try:
            p = subprocess.run([sys.executable, "-E", "-s", "main.py"], cwd=workdir,
                               env=_clean_env(workdir, ptc_token), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=timeout_s, **kwargs)
            status = "ok" if p.returncode == 0 else "error"
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            status, rc = "timeout", None
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = f"Execution exceeded {timeout_s}s wall-clock limit."
        return {"status": status, "returncode": rc,
                "stdout": out[-MAX_OUTPUT:], "stderr": err[-MAX_OUTPUT:],
                "duration_s": round(time.time() - start, 2),
                "limits": {"timeout_s": timeout_s, "memory_mb": mem_mb if HAS_RLIMITS else None,
                           "resource_limits_enforced": HAS_RLIMITS}}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
