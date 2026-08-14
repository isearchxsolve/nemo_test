"""Server lifecycle management for codebase mode.

Boots the application under test as a child process in its own process group,
optionally installs dependencies first, waits for a health check, captures its
logs to a file, and ensures the process group is killed on exit.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Sequence

import httpx

from ..logging import get_logger
from ..retry import retry

__all__ = ["ServerManager", "pick_free_port", "wait_for_health"]

log = get_logger(__name__)


def pick_free_port(default: int | None = None) -> int:
    """Return a free TCP port chosen by the OS (preferring ``default``)."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0 if default is None else default))
        return s.getsockname()[1]


@retry(max_attempts=3, base_delay=0.25)
def wait_for_health(url: str, timeout: float = 30.0, interval: float = 0.25) -> bool:
    """Poll ``url`` until it returns a non-5xx status, or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2.0, follow_redirects=True)
            if r.status_code < 500:
                log.info("server.healthy", url=url, status=r.status_code)
                return True
            last_err = f"HTTP {r.status_code}"
        except (httpx.HTTPError, OSError) as exc:
            last_err = repr(exc)
        time.sleep(interval)
    log.warning("server.health_failed", url=url, error=last_err)
    return False


class ServerManager:
    """Owns a child process group running the dev server.

    Parameters
    ----------
    command:
        Argv to execute. The literal token ``__PORT__`` is replaced with
        the chosen port at start time.
    port:
        TCP port the server will bind to.
    log_file:
        File that receives the child's stdout+stderr.
    cwd:
        Working directory for the child process.
    env_overrides:
        Extra environment variables merged on top of the parent env.
    install_command:
        Optional argv to run *before* the server start command, e.g.
        ``["pip", "install", "-r", "requirements.txt", "-q"]``. Runs
        synchronously; failure is logged but does not abort the start.
    """

    PORT_PLACEHOLDER = "__PORT__"

    def __init__(
        self,
        command: Sequence[str],
        *,
        port: int,
        log_file: Path,
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
        install_command: Sequence[str] | None = None,
    ) -> None:
        self.command = list(command)
        self.port = port
        self.log_file = Path(log_file)
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.env_overrides = dict(env_overrides or {})
        self.install_command = list(install_command) if install_command else None
        self.process: subprocess.Popen[bytes] | None = None

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def install_deps(self) -> bool:
        """Run the install command synchronously, streaming output to the log.

        Returns True on success, False on failure. Never raises.
        """
        if not self.install_command:
            return True
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log.info("server.install_deps", cmd=self.install_command, cwd=str(self.cwd))
        try:
            with self.log_file.open("ab") as fh:
                result = subprocess.run(
                    self.install_command,
                    cwd=str(self.cwd),
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            if result.returncode != 0:
                log.warning("server.install_failed", returncode=result.returncode)
                return False
            log.info("server.install_ok")
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("server.install_error", error=str(exc))
            return False

    @retry(max_attempts=3, base_delay=0.5)
    def start(self, health_path: str = "/", timeout: float = 30.0) -> bool:
        """Install deps (if any), spawn the child, wait for health."""
        self.install_deps()

        env = {**os.environ, **self.env_overrides, "PORT": str(self.port)}
        env.setdefault("VITE_PORT", str(self.port))
        env.setdefault("HOST", "127.0.0.1")
        env.setdefault("CI", "true")

        argv = self._normalize_command(self.command)
        argv = [c.replace(self.PORT_PLACEHOLDER, str(self.port)) for c in argv]

        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log.info("server.start", cmd=argv, port=self.port, cwd=str(self.cwd))

        kwargs: dict[str, object] = dict(
            cwd=str(self.cwd),
            env=env,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["preexec_fn"] = os.setsid

        # Open the log file here so we can close it properly on failure.
        log_handle = self.log_file.open("ab")
        try:
            kwargs["stdout"] = log_handle
            self.process = subprocess.Popen(argv, **kwargs)  # type: ignore[arg-type]
        except Exception:
            log_handle.close()
            raise
        # Popen takes ownership of the fd; close our duplicate so the child
        # gets a clean EOF when it exits (on POSIX). On Windows this is a no-op.
        log_handle.close()

        ok = wait_for_health(
            f"http://127.0.0.1:{self.port}{health_path}", timeout=timeout
        )
        if not ok:
            self.stop()
            tail = self._tail_log(1500)
            raise RuntimeError(
                f"Server failed health check at "
                f"http://127.0.0.1:{self.port}{health_path}. "
                f"Server log tail:\n{tail}"
            )
        return True

    def stop(self) -> None:
        """Kill the child process group. Best-effort and idempotent."""
        proc = self.process
        if not proc:
            return
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    self._kill_pg(proc, "SIGTERM")
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if sys.platform == "win32":
                        proc.kill()
                    else:
                        self._kill_pg(proc, "SIGKILL")
                    proc.wait(timeout=5)
            log.info("server.stopped", port=self.port)
        finally:
            self.process = None

    @staticmethod
    def _kill_pg(proc: subprocess.Popen[bytes], sig: str) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), getattr(__import__("signal"), sig))
        except (OSError, ProcessLookupError):
            proc.send_signal(getattr(__import__("signal"), sig))

    def _tail_log(self, n: int) -> str:
        try:
            return self.log_file.read_text(encoding="utf-8", errors="ignore")[-n:]
        except OSError:
            return ""

    @staticmethod
    def _normalize_command(command: list[str]) -> list[str]:
        """Inject ``--port`` for common Node dev servers when absent."""
        argv = list(command)
        has_port = "--port" in argv or "-p" in argv
        if has_port:
            return argv
        for i, tok in enumerate(argv):
            if tok in {"vite", "next", "astro", "svelte-kit", "remix", "nuxt"}:
                return argv[: i + 1] + ["--port", "__PORT__"] + argv[i + 1 :]
        return argv

    def __enter__(self) -> "ServerManager":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
