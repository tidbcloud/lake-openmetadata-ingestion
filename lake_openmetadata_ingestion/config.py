from __future__ import annotations

import logging
import os
import re
import stat
from urllib.parse import urlsplit, parse_qsl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any, missing: list[str] | None = None) -> Any:
    """Expand ${VAR} placeholders; record (never invent) the ones that are not set.

    A missing variable used to become the empty string silently, which turns e.g.
    ``lake://${LAKE_USER}:${LAKE_PASSWORD}@host`` into a DSN with an empty password. Keep the
    expansion behaviour (a missing optional var must not be fatal) but make it loud.
    """
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in os.environ:
                if missing is not None:
                    missing.append(name)
                return ""
            return os.environ[name]

        return _ENV_REF.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, missing) for v in value]
    return value


def _read_private_config(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not os.path.samestat(info, path.stat()) or not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o077):
            raise ValueError("Config target must be a regular private file; chmod 600 it")
        with os.fdopen(fd, "r", closefd=False) as stream:
            text = stream.read(1024 * 1024 + 1)
        if len(text) > 1024 * 1024:
            raise ValueError("Config exceeds 1 MiB")
        return text
    finally:
        os.close(fd)


@dataclass
class LakeConfig:
    dsn: str
    require_tls: bool = True

    def __post_init__(self):
        parts = urlsplit(self.dsn)
        if not parts.hostname or parts.scheme not in ("lake", "https", "http"):
            raise ValueError("An explicit Lake endpoint is required")
        if not isinstance(self.require_tls, bool):
            raise ValueError("lake.require_tls must be true or false")
        modes = [v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() == "sslmode"]
        if len(modes) > 1 or (modes and modes[0] not in ("require", "disable")):
            raise ValueError("Invalid Lake sslmode")
        if self.require_tls and (parts.scheme == "http" or modes == ["disable"]):
            raise ValueError("Lake require_tls requires TLS; plaintext is an explicit local-development opt-out")


@dataclass
class OpenMetadataConfig:
    host: str
    jwt_token: str
    timeout_seconds: int = 30
    tls_ca_bundle: str | None = None
    require_tls: bool = True

    def __post_init__(self):
        # Environment interpolation produces strings; do not treat the string "false" as true.
        if isinstance(self.require_tls, str) and self.require_tls.lower() in ("true", "false"):
            self.require_tls = self.require_tls.lower() == "true"
        if not isinstance(self.require_tls, bool):
            raise ValueError("openmetadata.require_tls must be true or false")


@dataclass
class BotConfig:
    name: str = "LakeIngestionBot"
    # OneHour | 1 | 7 | 30 | 60 | 90 | Unlimited (days)
    token_expiry: str = "7"
    role_id: str = ""

    def __post_init__(self):
        if self.token_expiry not in ("OneHour", "1", "7", "30"):
            raise ValueError("bot.token_expiry must be OneHour, 1, 7 or 30 days")
    email_domain: str = "open-metadata.org"


@dataclass
class ServiceConfig:
    name: str
    display_name: str = "TiDB Cloud Lake"
    description: str = ""
    icon_url: str = ""


@dataclass
class MetadataConfig:
    catalogs: list[str] = field(default_factory=list)
    exclude_databases: list[str] = field(
        default_factory=lambda: ["system", "information_schema", "system_history"]
    )
    mark_deleted: bool = False


@dataclass
class StagesConfig:
    """TiDB Cloud Lake named stages are tenant-level; OM only has Table (tableType=Stage) under a schema.

    When enabled, stages are mounted as `<service>.<database>.<schema>.<stage>` and stage<->table
    lineage edges are synced (COPY INTO both directions, CTAS/DML reading `@stage`).
    """

    enabled: bool = False
    database: str = "default"
    schema: str = "stages"


@dataclass
class LineageConfig:
    state_file: str = ".state/lineage_watermark.json"
    include_kinds: list[str] = field(default_factory=lambda: ["CTAS", "DML", "CREATE_VIEW"])
    lookback_seconds: int = 120
    prune_view_upstreams: bool = True
    page_size: int = 5000


@dataclass
class Config:
    lake: LakeConfig
    openmetadata: OpenMetadataConfig
    service: ServiceConfig
    metadata: MetadataConfig
    lineage: LineageConfig
    bot: BotConfig
    stages: StagesConfig

    @classmethod
    def load(cls, path: str | Path, *, initializing_bot: bool = False) -> "Config":
        path = Path(path)
        missing: list[str] = []
        parsed = yaml.safe_load(_read_private_config(path)) or {}
        if initializing_bot and isinstance(parsed.get("openmetadata"), dict):
            parsed["openmetadata"]["jwt_token"] = ""
        raw = _expand_env(parsed, missing)
        if missing:
            raise ValueError("Config references unset environment variables: " + ", ".join(sorted(set(missing))))
        return cls(
            lake=LakeConfig(**raw["lake"]),
            openmetadata=OpenMetadataConfig(**raw["openmetadata"]),
            service=ServiceConfig(**raw["service"]),
            metadata=MetadataConfig(**raw.get("metadata", {})),
            lineage=LineageConfig(**raw.get("lineage", {})),
            bot=BotConfig(**raw.get("bot", {})),
            stages=StagesConfig(**raw.get("stages", {})),
        )
