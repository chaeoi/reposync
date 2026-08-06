from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reposync.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def _config_file(self, content: str) -> Path:
        temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        temporary.write(content)
        temporary.close()
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        return Path(temporary.name)

    def test_loads_multiple_jobs_gitea_and_both_credential_styles(self) -> None:
        path = self._config_file(
            """
version: 1
interval: 2m
workdir: ${TEST_WORKDIR}
credentials:
  github:
    username: ${TEST_USER:-x-access-token}
    password: ${TEST_TOKEN}
repositories:
  - name: api
    source:
      platform: github
      repository: acme/api
      credential: github
    target:
      platform: gitlab
      repository: backup/api
      credential: github
  - name: web
    source:
      platform: custom
      url: ssh://git@example.test/acme/web.git
    target:
      platform: gitea
      base_url: https://gitea.example.test
      repository: backup/web
      username: gitea-user
      password: direct-password
"""
        )
        with patch.dict(
            os.environ,
            {"TEST_WORKDIR": "/tmp/reposync-test", "TEST_TOKEN": "secret"},
            clear=False,
        ):
            config = load_config(path)

        self.assertEqual(config.interval_seconds, 120)
        self.assertEqual(len(config.repositories), 2)
        self.assertEqual(
            config.repositories[0].source.url, "https://github.com/acme/api.git"
        )
        self.assertEqual(
            config.repositories[1].target.url,
            "https://gitea.example.test/backup/web.git",
        )
        self.assertEqual(
            config.repositories[1].target.credential.username, "gitea-user"
        )
        self.assertEqual(
            config.repositories[1].target.credential.password, "direct-password"
        )

    def test_reports_missing_environment_variable_without_printing_a_secret(self) -> None:
        path = self._config_file(
            """
credentials:
  primary:
    username: user
    password: ${DEFINITELY_MISSING_REPOSYNC_TOKEN}
repositories:
  - name: demo
    source: {platform: custom, url: /tmp/source.git}
    target: {platform: custom, url: /tmp/target.git}
"""
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConfigError, "DEFINITELY_MISSING_REPOSYNC_TOKEN"):
                load_config(path)

    def test_rejects_credentials_embedded_in_url(self) -> None:
        path = self._config_file(
            """
repositories:
  - name: demo
    source: {platform: custom, url: "https://user:secret@example.test/a.git"}
    target: {platform: custom, url: /tmp/target.git}
"""
        )
        with self.assertRaisesRegex(ConfigError, "must not be embedded"):
            load_config(path)

    def test_rejects_incomplete_inline_credentials(self) -> None:
        path = self._config_file(
            """
repositories:
  - name: demo
    source: {platform: custom, url: /tmp/source.git}
    target:
      platform: custom
      url: https://example.test/demo.git
      username: user
"""
        )
        with self.assertRaisesRegex(ConfigError, "must be provided together"):
            load_config(path)

    def test_rejects_duplicate_repository_names(self) -> None:
        path = self._config_file(
            """
repositories:
  - name: demo
    source: {platform: custom, url: /tmp/source-a.git}
    target: {platform: custom, url: /tmp/target-a.git}
  - name: demo
    source: {platform: custom, url: /tmp/source-b.git}
    target: {platform: custom, url: /tmp/target-b.git}
"""
        )
        with self.assertRaisesRegex(ConfigError, "duplicate repository name"):
            load_config(path)

    def test_rejects_old_bidirectional_keys(self) -> None:
        path = self._config_file(
            """
repositories:
  - name: demo
    left: {platform: custom, url: /tmp/left.git}
    right: {platform: custom, url: /tmp/right.git}
"""
        )
        with self.assertRaisesRegex(ConfigError, "use source/target"):
            load_config(path)

    def test_rejects_old_override_policy(self) -> None:
        path = self._config_file(
            """
repositories:
  - name: demo
    conflict: left
    source: {platform: custom, url: /tmp/source.git}
    target: {platform: custom, url: /tmp/target.git}
"""
        )
        with self.assertRaisesRegex(ConfigError, "force-mirror mode"):
            load_config(path)

    def test_rejects_partial_mirror_filters(self) -> None:
        path = self._config_file(
            """
repositories:
  - name: demo
    branches: [main]
    source: {platform: custom, url: /tmp/source.git}
    target: {platform: custom, url: /tmp/target.git}
"""
        )
        with self.assertRaisesRegex(ConfigError, "all branches and tags"):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
