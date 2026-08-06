from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from reposync.config import Credential


class GitError(RuntimeError):
    """Raised when a git subprocess fails."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


class Git:
    def __init__(self, askpass_path: Path, timeout_seconds: int):
        self.askpass_path = askpass_path
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        credential: Credential | None = None,
        check: bool = True,
    ) -> CommandResult:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(self.askpass_path),
                "GIT_ASKPASS_REQUIRE": "force",
            }
        )
        if credential is not None:
            environment["REPOSYNC_USERNAME"] = credential.username
            environment["REPOSYNC_PASSWORD"] = credential.password
        else:
            environment.pop("REPOSYNC_USERNAME", None)
            environment.pop("REPOSYNC_PASSWORD", None)

        command = ["git", *args]
        try:
            process = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(
                f"git command timed out after {self.timeout_seconds}s: {' '.join(command[:3])}"
            ) from exc
        except OSError as exc:
            raise GitError(f"could not execute git: {exc}") from exc

        result = CommandResult(
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode,
        )
        if check and process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "unknown git error"
            raise GitError(f"git {' '.join(args[:2])} failed: {detail}")
        return result
