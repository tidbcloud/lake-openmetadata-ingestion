from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from lake_openmetadata_ingestion import cli
from lake_openmetadata_ingestion.lake_client import LakeClient
from lake_openmetadata_ingestion.config import OpenMetadataConfig
from lake_openmetadata_ingestion.om_client import OpenMetadataClient
from test_token_file_and_stdout import init_bot_cfg


def test_query_and_exec_log_only_fingerprints(caplog):
    client = LakeClient.__new__(LakeClient)
    client._conn = Mock()
    row = Mock()
    row.values.return_value = (1,)
    client._conn.query_iter.return_value = [row]
    sql = "SELECT 'private-lineage-key', 'synthetic-password'"
    with caplog.at_level(logging.DEBUG):
        assert list(client.query(sql)) == [(1,)]
        client.exec(sql)
    assert "sha256=" in caplog.text
    assert "private-lineage-key" not in caplog.text
    assert "synthetic-password" not in caplog.text
    client._conn.query_iter.assert_called_once_with(sql)
    client._conn.exec.assert_called_once_with(sql)


def test_custom_ca_does_not_enable_proxy_environment():
    client = OpenMetadataClient("https://om.example/api", "jwt", tls_ca_bundle="/ca.pem", require_tls=True)
    assert client._s.verify == "/ca.pem"
    assert client._s.trust_env is False
    assert OpenMetadataClient("https://om.example/api", "jwt")._s.verify is True


@pytest.mark.parametrize("value", [False, True, "", 0])
def test_ca_setting_cannot_disable_certificate_verification(value):
    with pytest.raises(ValueError, match="tls_ca_bundle"):
        OpenMetadataClient("https://om.example/api", "jwt", tls_ca_bundle=value)


def test_environment_false_is_not_treated_as_true():
    assert OpenMetadataConfig("http://localhost/api", "jwt", require_tls="false").require_tls is False


def test_plaintext_warning_does_not_log_url_credentials(caplog):
    with pytest.raises(ValueError, match="without credentials"):
        OpenMetadataClient("http://user:private-password@om.example/api?token=private-token", "jwt", require_tls=False)
    assert "private-password" not in caplog.text
    assert "private-token" not in caplog.text


def test_login_uses_the_same_ca_and_strict_policy(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"accessToken": "jwt"}
    observed = []

    def post(session, url, **kwargs):
        settings = session.merge_environment_settings(url, {}, None, None, None)
        observed.append(settings)
        assert kwargs["allow_redirects"] is False
        return response

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:8080")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/environment-ca.pem")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr("lake_openmetadata_ingestion.om_client.requests.Session.post", post)
    assert OpenMetadataClient.login_basic("https://om.example/api", "user", "secret",
                                         tls_ca_bundle="/ca.pem", require_tls=True) == "jwt"
    assert observed[0]["verify"] == "/ca.pem"
    assert not observed[0]["proxies"]
    with pytest.raises(ValueError, match="require_tls"):
        OpenMetadataClient.login_basic("http://om.example/api", "user", "secret", require_tls=True)
    assert len(observed) == 1


@pytest.mark.parametrize("host", ["http://localhost/api", "http://om.example/api"])
def test_strict_transport_rejects_plaintext_before_credentials_are_sent(host):
    with pytest.raises(ValueError, match="require_tls"):
        OpenMetadataClient(host, "jwt", require_tls=True)
    # Existing explicit HTTP configurations remain accepted until the operator opts in.
    assert OpenMetadataClient(host, "jwt", require_tls=False)._s.verify is True


def test_init_bot_forwards_transport_settings_on_both_paths(init_bot_cfg, monkeypatch, tmp_path):
    import argparse

    cfg = init_bot_cfg
    cfg.openmetadata.tls_ca_bundle = "/ca.pem"
    cfg.openmetadata.require_tls = True
    monkeypatch.delenv("OM_ADMIN_TOKEN")
    monkeypatch.setenv("OM_ADMIN_EMAIL", "user")
    monkeypatch.setenv("OM_ADMIN_PASSWORD", "secret")
    client = Mock()
    client.login_basic.return_value = "admin-token"
    monkeypatch.setattr(cli, "OpenMetadataClient", client)
    cli.cmd_init_bot(cfg, argparse.Namespace(write_token=str(tmp_path / "token"), print_token=False))
    assert client.login_basic.call_args.kwargs == {"tls_ca_bundle": "/ca.pem", "require_tls": True}
    assert client.call_args.kwargs == {"tls_ca_bundle": "/ca.pem", "require_tls": True}
