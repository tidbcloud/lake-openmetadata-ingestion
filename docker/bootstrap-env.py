#!/usr/bin/env python3
"""Create local-demo secrets without printing them or overwriting existing state."""
import base64
import os
import secrets
from pathlib import Path


def main():
    target = Path(__file__).resolve().parent / '.env'
    data = 'MYSQL_ROOT_PASSWORD=' + secrets.token_hex(32) + '\n'
    data += 'MYSQL_PASSWORD=' + secrets.token_hex(32) + '\n'
    data += 'AIRFLOW_DB_PASSWORD=' + secrets.token_hex(32) + '\n'
    data += 'FERNET_KEY=' + base64.urlsafe_b64encode(os.urandom(32)).decode() + '\n'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    print('Created private local-demo configuration. Keep it for the lifetime of the database volume.')


if __name__ == '__main__':
    main()
