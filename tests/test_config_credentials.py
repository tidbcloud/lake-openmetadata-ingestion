"""Config-file credential handling: unset ${VAR} and world-readable config files are reported.

Helix 00f853b1: ``config.py`` turned an unset ``${VAR}`` into an empty credential silently, so a DSN
such as ``lake://${LAKE_USER}:${LAKE_PASSWORD}@host`` became a connection with an empty password and
nothing said so.
"""

from __future__ import annotations

import logging
import pytest

from lake_openmetadata_ingestion.config import Config, _expand_env

MINIMAL = (
    "lake:\n  dsn: lake://{user}:{password}@h:443/?warehouse=w\n"
    "openmetadata:\n  host: https://om.example.com/api\n  jwt_token: t\n"
    "service:\n  name: lake_prod\n"
)


def test_missing_env_var_is_reported(monkeypatch):
    monkeypatch.delenv("LAKE_PASSWORD", raising=False)
    monkeypatch.delenv("LAKE_USER", raising=False)
    missing: list[str] = []
    expanded = _expand_env("lake://${LAKE_USER}:${LAKE_PASSWORD}@h:443/", missing)
    assert expanded == "lake://:@h:443/"
    assert sorted(missing) == ["LAKE_PASSWORD", "LAKE_USER"]


def test_config_load_warns_about_unset_credentials(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("LAKE_PASSWORD", raising=False)
    monkeypatch.delenv("LAKE_USER", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        MINIMAL.format(user="${LAKE_USER}", password="${LAKE_PASSWORD}")
    )
    cfg_file.chmod(0o600)

    with pytest.raises(ValueError, match="unset environment variables") as caught:
        Config.load(cfg_file)
    assert "LAKE_PASSWORD" in str(caught.value)


def test_config_load_warns_when_the_file_is_group_or_world_readable(tmp_path, caplog):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL.format(user="u", password="p"))
    cfg_file.chmod(0o644)

    with pytest.raises(ValueError, match="chmod 600"):
        Config.load(cfg_file)


def test_config_load_is_quiet_for_a_private_file_with_all_vars_set(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setenv("LAKE_USER", "u")
    monkeypatch.setenv("LAKE_PASSWORD", "p")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        MINIMAL.format(user="${LAKE_USER}", password="${LAKE_PASSWORD}")
    )
    cfg_file.chmod(0o600)

    with caplog.at_level(logging.WARNING):
        cfg = Config.load(cfg_file)

    assert cfg.lake.dsn == "lake://u:p@h:443/?warehouse=w"
    assert caplog.text == ""


def test_config_symlink_is_rejected(tmp_path):
    target = tmp_path / 'private.yaml'
    target.write_text(MINIMAL.format(user='u', password='p'))
    target.chmod(0o600)
    link = tmp_path / 'link.yaml'
    link.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        Config.load(link)


def test_tls_is_required_by_default():
    from lake_openmetadata_ingestion.config import LakeConfig
    from lake_openmetadata_ingestion.om_client import OpenMetadataClient
    with pytest.raises(ValueError, match='require_tls'):
        LakeConfig('http://localhost:8000')
    with pytest.raises(ValueError, match='require_tls'):
        OpenMetadataClient('http://localhost:8585/api', 'jwt')
