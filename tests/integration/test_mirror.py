from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from reposync.config import Config, Endpoint, Repository
from reposync.services.mirror import RepositorySynchronizer


def git(*args: str, cwd: Path | None = None) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {process.stderr}")
    return process.stdout.strip()


class SynchronizerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.git"
        self.target = self.root / "target.git"
        git("init", "--bare", str(self.source))
        git("init", "--bare", str(self.target))
        self._create_initial_commit()

        source_endpoint = Endpoint("custom", None, str(self.source), None, None)
        target_endpoint = Endpoint("custom", None, str(self.target), None, None)
        self.repository = Repository("demo", source_endpoint, target_endpoint)
        self.config = Config(
            version=1,
            interval_seconds=300,
            workdir=self.root / "cache",
            concurrency=2,
            git_timeout_seconds=30,
            repositories=(self.repository,),
        )

    def _create_initial_commit(self) -> None:
        work = self.root / "seed"
        git("init", str(work))
        self._configure_author(work)
        (work / "README.md").write_text("initial\n", encoding="utf-8")
        git("add", "README.md", cwd=work)
        git("commit", "-m", "initial", cwd=work)
        git("branch", "-M", "main", cwd=work)
        git("remote", "add", "origin", str(self.source), cwd=work)
        git("push", "origin", "main", cwd=work)

    def _configure_author(self, work: Path) -> None:
        git("config", "user.name", "RepoSync Test", cwd=work)
        git("config", "user.email", "reposync@example.test", cwd=work)

    def _clone(self, remote: Path, name: str) -> Path:
        work = self.root / name
        git("clone", "--branch", "main", str(remote), str(work))
        self._configure_author(work)
        return work

    def _commit_and_push(self, remote: Path, name: str, filename: str) -> str:
        work = self._clone(remote, name)
        (work / filename).write_text(f"{name}\n", encoding="utf-8")
        git("add", filename, cwd=work)
        git("commit", "-m", f"update {filename}", cwd=work)
        git("push", "origin", "main", cwd=work)
        return git("rev-parse", "HEAD", cwd=work)

    def _oid(self, remote: Path, ref: str = "refs/heads/main") -> str:
        return git("rev-parse", ref, cwd=remote)

    def _ref_exists(self, remote: Path, ref: str) -> bool:
        process = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=remote,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return process.returncode == 0

    def _refs(self, remote: Path) -> dict[str, str]:
        output = git(
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads",
            "refs/tags",
            cwd=remote,
        )
        return dict(line.split(" ", 1) for line in output.splitlines())

    def test_target_is_created_and_then_matches_source_after_update(self) -> None:
        with RepositorySynchronizer(self.config) as synchronizer:
            first = synchronizer.sync_one(self.repository)
            self.assertEqual(first.changed, 1)
            self.assertEqual(self._refs(self.source), self._refs(self.target))

            source_oid = self._commit_and_push(
                self.source, "source-update", "source.txt"
            )
            second = synchronizer.sync_one(self.repository)
            self.assertEqual(second.changed, 1)
            self.assertEqual(self._oid(self.source), source_oid)
            self.assertEqual(self._refs(self.source), self._refs(self.target))

    def test_dry_run_does_not_push(self) -> None:
        with RepositorySynchronizer(self.config) as synchronizer:
            result = synchronizer.sync_one(self.repository, dry_run=True)
        self.assertEqual(result.changed, 1)
        self.assertFalse(self._ref_exists(self.target, "refs/heads/main"))

    def test_source_overwrites_target_that_is_ahead(self) -> None:
        with RepositorySynchronizer(self.config) as synchronizer:
            synchronizer.sync_one(self.repository)
            source_oid = self._oid(self.source)
            self._commit_and_push(self.target, "target-ahead", "target.txt")

            result = synchronizer.sync_one(self.repository)
            self.assertEqual(result.changed, 1)
            self.assertEqual(self._oid(self.source), source_oid)
            self.assertEqual(self._refs(self.source), self._refs(self.target))

    def test_source_overwrites_divergence_and_creates_new_source_refs_atomically(self) -> None:
        with RepositorySynchronizer(self.config) as synchronizer:
            synchronizer.sync_one(self.repository)
            source_work = self._clone(self.source, "source-diverged")
            target_work = self._clone(self.target, "target-diverged")

            (source_work / "source.txt").write_text("source\n", encoding="utf-8")
            git("add", "source.txt", cwd=source_work)
            git("commit", "-m", "source diverged", cwd=source_work)
            git("push", "origin", "main", cwd=source_work)
            git("branch", "feature", cwd=source_work)
            git("push", "origin", "feature", cwd=source_work)

            (target_work / "target.txt").write_text("target\n", encoding="utf-8")
            git("add", "target.txt", cwd=target_work)
            git("commit", "-m", "target diverged", cwd=target_work)
            git("push", "origin", "main", cwd=target_work)

            synchronizer.sync_one(self.repository)

        self.assertEqual(self._refs(self.source), self._refs(self.target))

    def test_source_deletes_target_only_refs(self) -> None:
        with RepositorySynchronizer(self.config) as synchronizer:
            synchronizer.sync_one(self.repository)
            target_work = self._clone(self.target, "target-extra")
            git("branch", "target-only", cwd=target_work)
            git("push", "origin", "target-only", cwd=target_work)
            self.assertTrue(self._ref_exists(self.target, "refs/heads/target-only"))

            synchronizer.sync_one(self.repository)

        self.assertFalse(self._ref_exists(self.target, "refs/heads/target-only"))
        self.assertEqual(self._refs(self.source), self._refs(self.target))

    def test_source_tag_overwrites_different_target_tag(self) -> None:
        with RepositorySynchronizer(self.config) as synchronizer:
            synchronizer.sync_one(self.repository)
            source_work = self._clone(self.source, "source-tag")
            target_work = self._clone(self.target, "target-tag")
            git("tag", "-a", "stable", "-m", "source tag", cwd=source_work)
            git("push", "origin", "stable", cwd=source_work)
            git("tag", "-a", "stable", "-m", "target tag", cwd=target_work)
            git("push", "origin", "stable", cwd=target_work)

            synchronizer.sync_one(self.repository)

        self.assertEqual(self._refs(self.source), self._refs(self.target))


if __name__ == "__main__":
    unittest.main()
