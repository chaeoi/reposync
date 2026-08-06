from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from reposync.config import Config, Credential, Repository
from reposync.adapters.git import Git, GitError


LOG = logging.getLogger("reposync")
_MAX_STABILIZATION_ATTEMPTS = 3
_ASKPASS = """#!/bin/sh
case "$1" in
  *sername*) printf '%s\\n' "${REPOSYNC_USERNAME:-}" ;;
  *)         printf '%s\\n' "${REPOSYNC_PASSWORD:-}" ;;
esac
"""


@dataclass(frozen=True)
class RefAction:
    ref: str
    source_oid: str | None
    expected_oid: str | None
    reason: str


@dataclass(frozen=True)
class SyncResult:
    repository: str
    changed: int
    refs: int
    dry_run: bool


def _cache_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "repository"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:80]}-{digest}.git"


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another sync is already running for cache {path.parent}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class RepositorySynchronizer:
    def __init__(self, config: Config):
        self.config = config
        self.config.workdir.mkdir(parents=True, exist_ok=True)
        self.askpass_path = self._write_askpass()
        self.git = Git(self.askpass_path, config.git_timeout_seconds)

    def close(self) -> None:
        self.askpass_path.unlink(missing_ok=True)

    def __enter__(self) -> RepositorySynchronizer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _write_askpass(self) -> Path:
        descriptor, filename = tempfile.mkstemp(
            prefix=".askpass-", dir=self.config.workdir, text=True
        )
        path = Path(filename)
        try:
            os.write(descriptor, _ASKPASS.encode("utf-8"))
        finally:
            os.close(descriptor)
        path.chmod(0o700)
        return path

    def sync_all(self, *, dry_run: bool = False) -> list[SyncResult]:
        results: list[SyncResult] = []
        errors: list[tuple[str, Exception]] = []
        workers = min(self.config.concurrency, len(self.config.repositories))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="reposync") as executor:
            futures = {
                executor.submit(self.sync_one, repository, dry_run=dry_run): repository.name
                for repository in self.config.repositories
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # Keep independent repository jobs running.
                    errors.append((name, exc))
                    LOG.error("repository=%s sync failed: %s", name, exc)

        if errors:
            summary = "; ".join(f"{name}: {error}" for name, error in errors)
            raise RuntimeError(f"{len(errors)} repository sync(s) failed: {summary}")
        return sorted(results, key=lambda result: result.repository)

    def sync_one(self, repository: Repository, *, dry_run: bool = False) -> SyncResult:
        cache = self.config.workdir / "repositories" / _cache_name(repository.name)
        with _exclusive_lock(cache.parent / f"{cache.name}.lock"):
            self._prepare_cache(cache, repository)
            total_changes = 0
            for attempt in range(1, _MAX_STABILIZATION_ATTEMPTS + 1):
                self._fetch(cache, "source", repository.source.credential)
                self._fetch(cache, "target", repository.target.credential)

                source_refs = self._read_refs(cache, "source")
                target_refs = self._read_refs(cache, "target")
                actions = self._plan(source_refs, target_refs)

                for action in actions:
                    LOG.info(
                        "repository=%s action=%s ref=%s target=target%s",
                        repository.name,
                        action.reason,
                        action.ref,
                        " dry-run=true" if dry_run else "",
                    )
                if dry_run:
                    return SyncResult(
                        repository=repository.name,
                        changed=len(actions),
                        refs=len(source_refs),
                        dry_run=True,
                    )

                if actions:
                    self._push_all(cache, actions, repository.target.credential)
                    total_changes += len(actions)

                self._fetch(cache, "source", repository.source.credential)
                self._fetch(cache, "target", repository.target.credential)
                verified_source = self._read_refs(cache, "source")
                verified_target = self._read_refs(cache, "target")
                if verified_source == verified_target:
                    LOG.info(
                        "repository=%s status=ok changed=%d refs=%d",
                        repository.name,
                        total_changes,
                        len(verified_source),
                    )
                    return SyncResult(
                        repository=repository.name,
                        changed=total_changes,
                        refs=len(verified_source),
                        dry_run=False,
                    )

                LOG.warning(
                    "repository=%s refs changed during sync; retrying attempt=%d/%d",
                    repository.name,
                    attempt,
                    _MAX_STABILIZATION_ATTEMPTS,
                )

            raise GitError(
                f"repository {repository.name!r} did not reach a stable mirrored state "
                f"after {_MAX_STABILIZATION_ATTEMPTS} attempts"
            )

    def _prepare_cache(self, cache: Path, repository: Repository) -> None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            self.git.run(["init", "--bare", str(cache)])
        elif not (cache / "HEAD").exists():
            raise GitError(f"cache path is not a bare git repository: {cache}")

        existing = self.git.run(["remote"], cwd=cache).stdout.splitlines()
        for name, endpoint in (
            ("source", repository.source),
            ("target", repository.target),
        ):
            if name in existing:
                self.git.run(["remote", "set-url", name, endpoint.url], cwd=cache)
            else:
                self.git.run(["remote", "add", name, endpoint.url], cwd=cache)

    def _fetch(self, cache: Path, remote: str, credential: Credential | None) -> None:
        self.git.run(
            [
                "fetch",
                "--prune",
                "--no-tags",
                remote,
                f"+refs/heads/*:refs/reposync/{remote}/heads/*",
                f"+refs/tags/*:refs/reposync/{remote}/tags/*",
            ],
            cwd=cache,
            credential=credential,
        )

    def _read_refs(self, cache: Path, remote: str) -> dict[str, str]:
        prefix = f"refs/reposync/{remote}/"
        output = self.git.run(
            ["for-each-ref", "--format=%(refname) %(objectname)", prefix], cwd=cache
        ).stdout
        refs: dict[str, str] = {}
        for line in output.splitlines():
            ref, oid = line.split(" ", 1)
            key = ref.removeprefix(prefix)
            if key.startswith("heads/") or key.startswith("tags/"):
                refs[key] = oid
        return refs

    def _plan(
        self,
        source: dict[str, str],
        target: dict[str, str],
    ) -> list[RefAction]:
        actions: list[RefAction] = []

        for key in sorted(set(source) | set(target)):
            source_oid, target_oid = source.get(key), target.get(key)
            ref = f"refs/{key}"

            if target_oid is None:
                actions.append(RefAction(ref, source_oid, None, "create"))
                continue
            if source_oid is None:
                actions.append(RefAction(ref, None, target_oid, "delete"))
                continue
            if source_oid == target_oid:
                continue
            actions.append(RefAction(ref, source_oid, target_oid, "overwrite"))
        return actions

    def _push_all(
        self, cache: Path, actions: list[RefAction], credential: Credential | None
    ) -> None:
        args = ["push", "--atomic", "--porcelain"]
        for action in actions:
            args.append(
                f"--force-with-lease={action.ref}:{action.expected_oid or ''}"
            )
        args.append("target")
        for action in actions:
            refspec = (
                f":{action.ref}"
                if action.source_oid is None
                else f"+{action.source_oid}:{action.ref}"
            )
            args.append(refspec)
        self.git.run(
            args,
            cwd=cache,
            credential=credential,
        )
