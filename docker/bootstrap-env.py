#!/usr/bin/env python3
"""Generate per-installation local-demo secrets. Existing keys are never replaced."""
import base64
import os
from pathlib import Path
import secrets
import subprocess


def main():
    directory = Path(__file__).resolve().parent
    target = directory / '.env'
    key_dir = directory / 'jwt-keys'
    if target.exists() or key_dir.exists():
        raise SystemExit('Existing demo secrets found; refuse to overwrite them')
    key_dir.mkdir(mode=0o700)
    env_fd = None
    env_created = False
    try:
        env_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        env_created = True
        private = key_dir / 'private_key.der'
        public = key_dir / 'public_key.der'
        pem = key_dir / 'private.pem'
        # Precreate all secret outputs with restrictive modes before openssl.
        for path in (private, public, pem):
            path.touch(mode=0o600, exist_ok=False)
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(pem)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['openssl', 'pkcs8', '-topk8', '-nocrypt', '-in', str(pem), '-outform', 'DER', '-out', str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['openssl', 'pkey', '-in', str(pem), '-pubout', '-outform', 'DER', '-out', str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pem.unlink()
        values = {name: secrets.token_hex(32) for name in ('MYSQL_ROOT_PASSWORD', 'MYSQL_PASSWORD', 'AIRFLOW_DB_PASSWORD')}
        values['FERNET_KEY'] = base64.urlsafe_b64encode(os.urandom(32)).decode()
        values['JWT_KEY_ID'] = secrets.token_hex(16)
        values['RSA_PRIVATE_KEY_FILE_PATH'] = '/opt/openmetadata/jwt/private_key.der'
        values['RSA_PUBLIC_KEY_FILE_PATH'] = '/opt/openmetadata/jwt/public_key.der'
        with os.fdopen(env_fd, 'w') as stream:
            env_fd = None
            stream.write(''.join(k + '=' + v + '\n' for k, v in values.items()))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if env_fd is not None:
            os.close(env_fd)
        if env_created:
            target.unlink(missing_ok=True)
        for path in key_dir.iterdir():
            path.unlink()
        key_dir.rmdir()
        raise
    print('Created private local-demo configuration and unique JWT signing keys. Keep them with the database volume.')


if __name__ == '__main__':
    main()
