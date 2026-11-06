from unittest.mock import Mock
import pytest
from lake_openmetadata_ingestion.bot_bootstrap import bootstrap_bot
from lake_openmetadata_ingestion.config import BotConfig

ROLE = '00000000-0000-0000-0000-000000000001'


def test_bot_uses_explicit_role_and_never_creates_global_policy():
    om = Mock()
    om._req.return_value = {'id': ROLE, 'name': 'ScopedByAdministrator'}
    om.upsert_bot_user.return_value = {'id': 'user-id', 'name': 'bot'}
    om.generate_bot_token.return_value = 'test-token'
    result = bootstrap_bot(om, 'bot', '7', 'example.com', role_id=ROLE)
    om._req.assert_called_once_with('GET', f'/v1/roles/{ROLE}')
    om.upsert_policy.assert_not_called()
    om.upsert_role.assert_not_called()
    assert om.upsert_bot_user.call_args.args[2] == [ROLE]
    assert result.role == 'ScopedByAdministrator'


def test_missing_role_cannot_grant_any_permission():
    om = Mock()
    with pytest.raises(ValueError, match='role_id'):
        bootstrap_bot(om, 'bot', '7', 'example.com', role_id='')
    assert om.mock_calls == []


def test_unlimited_tokens_are_rejected():
    with pytest.raises(ValueError, match='token_expiry'):
        BotConfig(token_expiry='Unlimited')


def test_demo_secret_bootstrap_is_private_and_refuses_overwrite(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / 'docker' / 'bootstrap-env.py'
    script = tmp_path / 'bootstrap-env.py'
    shutil.copy(source, script)
    first = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert first.returncode == 0
    target = tmp_path / '.env'
    assert target.stat().st_mode & 0o777 == 0o600
    values = dict(line.split('=', 1) for line in target.read_text().splitlines())
    for value in values.values():
        assert value not in first.stdout + first.stderr
    old = target.read_bytes()
    second = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert second.returncode != 0
    assert target.read_bytes() == old


def test_demo_jwt_keys_are_unique_and_readable_as_der(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / 'docker' / 'bootstrap-env.py'
    public_keys = []
    for name in ('first', 'second'):
        directory = tmp_path / name
        directory.mkdir()
        script = directory / 'bootstrap-env.py'
        shutil.copy(source, script)
        subprocess.run([sys.executable, str(script)], check=True, capture_output=True)
        private = directory / 'jwt-keys' / 'private_key.der'
        public = directory / 'jwt-keys' / 'public_key.der'
        assert private.stat().st_mode & 0o777 == 0o600
        result = subprocess.run(['openssl', 'pkey', '-inform', 'DER', '-in', str(private), '-pubout', '-outform', 'DER'], check=True, capture_output=True)
        assert result.stdout == public.read_bytes()
        public_keys.append(result.stdout)
    assert public_keys[0] != public_keys[1]
