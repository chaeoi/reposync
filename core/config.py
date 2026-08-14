from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


class ConfigError(ValueError):
    """Raised when the configuration is invalid."""


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_DURATION_PATTERN = re.compile(r"^(\d+)(s|m|h)?$")
_PLATFORM_URLS = {
    "github": "https://github.com/{repository}.git",
    "gitlab": "https://gitlab.com/{repository}.git",
    "gitee": "https://gitee.com/{repository}.git",
    "bitbucket": "https://bitbucket.org/{repository}.git",
    "codeberg": "https://codeberg.org/{repository}.git",
}
_KNOWN_PLATFORMS = {*_PLATFORM_URLS, "gitea", "custom"}


@dataclass(frozen=True)
class Credential:
    username: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class Endpoint:
    platform: str
    repository: str | None
    url: str
    credential_name: str | None
    credential: Credential | None = field(repr=False)


@dataclass(frozen=True)
class Repository:
    name: str
    source: Endpoint
    target: Endpoint


@dataclass(frozen=True)
class Config:
    version: int
    interval_seconds: int
    workdir: Path
    concurrency: int
    git_timeout_seconds: int
    repositories: tuple[Repository, ...]


def _expand_environment(value: Any, location: str = "config") -> Any:
    if isinstance(value, dict):
        return {key: _expand_environment(item, f"{location}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [
            _expand_environment(item, f"{location}[{index}]") for index, item in enumerate(value)
        ]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigError(f"{location}: environment variable {name!r} is not set")

    return _ENV_PATTERN.sub(replace, value)


def _duration(value: Any, location: str) -> int:
    text = str(value)
    match = _DURATION_PATTERN.fullmatch(text)
    if not match:
        raise ConfigError(f"{location}: expected a duration such as 30s, 5m, or 1h")
    amount = int(match.group(1))
    multiplier = {None: 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    seconds = amount * multiplier
    if seconds <= 0:
        raise ConfigError(f"{location}: duration must be greater than zero")
    return seconds


def _nonempty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}: expected a non-empty string")
    return value.strip()


def _credential_map(raw: Any) -> dict[str, Credential]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("credentials: expected a mapping")

    credentials: dict[str, Credential] = {}
    for name, value in raw.items():
        credential_name = _nonempty_string(name, "credentials: key")
        location = f"credentials.{credential_name}"
        if credential_name in credentials:
            raise ConfigError(f"{location}: duplicate credential name")
        if not isinstance(value, dict):
            raise ConfigError(f"{location}: expected a mapping")
        username = _nonempty_string(value.get("username"), f"{location}.username")
        password = _nonempty_string(value.get("password"), f"{location}.password")
        credentials[credential_name] = Credential(username=username, password=password)
    return credentials


def _endpoint(raw: Any, location: str, credentials: dict[str, Credential]) -> Endpoint:
    if not isinstance(raw, dict):
        raise ConfigError(f"{location}: expected a mapping")

    platform = _nonempty_string(raw.get("platform"), f"{location}.platform").lower()
    if platform not in _KNOWN_PLATFORMS:
        raise ConfigError(
            f"{location}.platform: unknown platform {platform!r}; "
            f"expected one of {', '.join(sorted(_KNOWN_PLATFORMS))}"
        )
    repository_value = raw.get("repository")
    repository = (
        _nonempty_string(repository_value, f"{location}.repository")
        if repository_value is not None
        else None
    )
    explicit_url = raw.get("url")

    if explicit_url is not None:
        url = _nonempty_string(explicit_url, f"{location}.url")
    elif platform == "gitea":
        if repository is None:
            raise ConfigError(f"{location}.repository: required for platform 'gitea'")
        base_url = _nonempty_string(raw.get("base_url"), f"{location}.base_url")
        url = f"{base_url.rstrip('/')}/{repository.removesuffix('.git')}.git"
    elif platform in _PLATFORM_URLS:
        if repository is None:
            raise ConfigError(f"{location}.repository: required for platform {platform!r}")
        url = _PLATFORM_URLS[platform].format(repository=repository.removesuffix(".git"))
    else:
        raise ConfigError(f"{location}.url: required for platform {platform!r}")

    parsed = urlsplit(url)
    if parsed.scheme in {"http", "https", "ssh", "git"} and not parsed.hostname:
        raise ConfigError(f"{location}.url: URL is missing a host")
    if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password):
        raise ConfigError(
            f"{location}.url: credentials must not be embedded in the URL; "
            "use the credentials section"
        )

    credential_name_value = raw.get("credential")
    credential_name = (
        _nonempty_string(credential_name_value, f"{location}.credential")
        if credential_name_value is not None
        else None
    )
    if credential_name is not None and credential_name not in credentials:
        raise ConfigError(f"{location}.credential: unknown credential {credential_name!r}")

    has_inline_username = "username" in raw
    has_inline_password = "password" in raw
    if credential_name is not None and (has_inline_username or has_inline_password):
        raise ConfigError(
            f"{location}: use either credential or inline username/password, not both"
        )
    if has_inline_username != has_inline_password:
        raise ConfigError(f"{location}: inline username and password must be provided together")
    inline_credential = None
    if has_inline_username:
        inline_credential = Credential(
            username=_nonempty_string(raw.get("username"), f"{location}.username"),
            password=_nonempty_string(raw.get("password"), f"{location}.password"),
        )

    return Endpoint(
        platform=platform,
        repository=repository,
        url=url,
        credential_name=credential_name,
        credential=(credentials.get(credential_name) if credential_name else inline_credential),
    )


def load_config(path: str | Path) -> Config:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"could not read configuration file {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config: expected a mapping at the document root")
    raw = _expand_environment(raw)

    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ConfigError(f"version: unsupported configuration version {version!r}")

    credentials = _credential_map(raw.get("credentials"))
    repositories_raw = raw.get("repositories")
    if not isinstance(repositories_raw, list) or not repositories_raw:
        raise ConfigError("repositories: expected a non-empty list")

    repositories: list[Repository] = []
    names: set[str] = set()
    for index, item in enumerate(repositories_raw):
        location = f"repositories[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{location}: expected a mapping")
        name = _nonempty_string(item.get("name"), f"{location}.name")
        if name in names:
            raise ConfigError(f"{location}.name: duplicate repository name {name!r}")
        names.add(name)

        if "left" in item or "right" in item:
            raise ConfigError(f"{location}: left/right is no longer supported; use source/target")
        for obsolete_key in ("conflict", "deletion_policy"):
            if obsolete_key in item:
                raise ConfigError(
                    f"{location}.{obsolete_key}: no longer supported in force-mirror mode"
                )
        for scope_key in ("branches", "tags"):
            if scope_key in item:
                raise ConfigError(
                    f"{location}.{scope_key}: filtering is not supported; "
                    "all branches and tags are always mirrored"
                )

        source = _endpoint(item.get("source"), f"{location}.source", credentials)
        target = _endpoint(item.get("target"), f"{location}.target", credentials)
        if source.url == target.url:
            raise ConfigError(f"{location}: source and target resolve to the same URL")

        repositories.append(
            Repository(
                name=name,
                source=source,
                target=target,
            )
        )

    concurrency = raw.get("concurrency", 4)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise ConfigError("concurrency: expected an integer greater than zero")

    return Config(
        version=version,
        interval_seconds=_duration(raw.get("interval", "5m"), "interval"),
        workdir=Path(
            _nonempty_string(raw.get("workdir", "/var/lib/reposync"), "workdir")
        ).expanduser(),
        concurrency=concurrency,
        git_timeout_seconds=_duration(raw.get("git_timeout", "10m"), "git_timeout"),
        repositories=tuple(repositories),
    )
