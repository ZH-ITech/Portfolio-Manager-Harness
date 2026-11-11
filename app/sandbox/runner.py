"""Isolated code execution. 'process' mode: separate interpreter in a temp dir
with CPU / memory / file-size / process limits, wall-clock timeout and a scrubbed
environment. For production, swap in a container or microVM backend (e.g.
Docker with --network limited to the PTC proxy, gVisor, Firecracker, E2B) behind
the same run_code() interface."""
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time

from app.config import settings

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


def run_code(code: str, ptc_token: str = "", timeout_s: int | None = None,
             mem_mb: int | None = None) -> dict:
    timeout_s = timeout_s or settings.sandbox_timeout_s
    mem_mb = mem_mb or settings.sandbox_memory_mb
    workdir = tempfile.mkdtemp(prefix="sbx_")
    try:
        for src, name in _INJECT:
            shutil.copy(src, os.path.join(workdir, name))
        with open(os.path.join(workdir, "main.py"), "w") as f:
            f.write(code)
        env = {
            "PATH": "/usr/bin:/bin", "HOME": workdir, "PYTHONDONTWRITEBYTECODE": "1",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg",
            "PTC_PROXY_URL": settings.ptc_proxy_url, "PTC_TOKEN": ptc_token,
        }
        start = time.time()
        try:
            p = subprocess.run([sys.executable, "-E", "-s", "main.py"], cwd=workdir, env=env,
                               capture_output=True, text=True, timeout=timeout_s,
                               preexec_fn=_limits(mem_mb, timeout_s))
            status = "ok" if p.returncode == 0 else "error"
            out, err, code_rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            status, code_rc = "timeout", None
            out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = f"Execution exceeded {timeout_s}s wall-clock limit."
        return {"status": status, "returncode": code_rc,
                "stdout": out[-MAX_OUTPUT:], "stderr": err[-MAX_OUTPUT:],
                "duration_s": round(time.time() - start, 2),
                "limits": {"timeout_s": timeout_s, "memory_mb": mem_mb}}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
