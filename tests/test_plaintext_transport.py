"""The OM client warns when the configured endpoint sends the bot JWT over plaintext HTTP.

Helix e2e309bd: ``openmetadata.host`` is an unvalidated free string shipped as ``http://`` in the
example config, and every request carries the privileged bot JWT (and, on ``init-bot``, the admin
password). Changing the scheme is an operator decision, but the client should say so out loud.
"""

from __future__ import annotations

import logging

from lake_openmetadata_ingestion.om_client import OpenMetadataClient


def test_plaintext_remote_host_warns(caplog):
    with caplog.at_level(logging.WARNING):
        OpenMetadataClient("http://om.example.com/api", "jwt", require_tls=False)
    assert "plaintext http" in caplog.text


def test_plaintext_loopback_host_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        OpenMetadataClient("http://localhost:8585/api", "jwt", require_tls=False)
    assert "plaintext http" not in caplog.text


def test_https_host_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        OpenMetadataClient("https://om.example.com/api", "jwt")
    assert "plaintext http" not in caplog.text


def test_token_is_sent_as_a_bearer_header_and_never_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        client = OpenMetadataClient("https://om.example.com/api", "secret-jwt-value")
    assert client._s.headers["Authorization"] == "Bearer secret-jwt-value"
    assert "secret-jwt-value" not in caplog.text
