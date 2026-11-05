from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

from .bot_bootstrap import bootstrap_bot, validate_role_id
from .config import Config
from .lake_client import LakeClient
from .lineage_sync import LineageSync
from .metadata_sync import MetadataSync
from .om_client import OpenMetadataClient
from .secure_write import token_fingerprint, write_secret_file

# Where the bot JWT goes when the operator does not pass --write-token. Never stdout by default: on
# the documented cron/Airflow deployment stdout is the job-log stream, which is retained far longer
# than the token and readable by anyone with CI/operator access.
DEFAULT_TOKEN_PATH = ".state/bot_token"


def _clients(cfg: Config) -> tuple[LakeClient, OpenMetadataClient]:
    return (
        LakeClient(cfg.lake.dsn, include_stages=cfg.stages.enabled),
        OpenMetadataClient(cfg.openmetadata.host, cfg.openmetadata.jwt_token, cfg.openmetadata.timeout_seconds,
                           tls_ca_bundle=cfg.openmetadata.tls_ca_bundle, require_tls=cfg.openmetadata.require_tls),
    )


def cmd_init_bot(cfg: Config, args: argparse.Namespace) -> int:
    """Needs an admin credential: OM_ADMIN_TOKEN, or OM_ADMIN_EMAIL + OM_ADMIN_PASSWORD (basic auth)."""
    validate_role_id(cfg.bot.role_id)
    om_cfg = cfg.openmetadata
    admin_token = os.environ.get("OM_ADMIN_TOKEN")
    if not admin_token:
        email = os.environ.get("OM_ADMIN_EMAIL") or input("OM admin email: ")
        password = os.environ.get("OM_ADMIN_PASSWORD") or getpass.getpass("OM admin password: ")
        admin_token = OpenMetadataClient.login_basic(
            om_cfg.host, email, password, om_cfg.timeout_seconds,
            tls_ca_bundle=om_cfg.tls_ca_bundle, require_tls=om_cfg.require_tls)
    om = OpenMetadataClient(om_cfg.host, admin_token, om_cfg.timeout_seconds,
                            tls_ca_bundle=om_cfg.tls_ca_bundle, require_tls=om_cfg.require_tls)

    res = bootstrap_bot(om, cfg.bot.name, cfg.bot.token_expiry, cfg.bot.email_domain, role_id=cfg.bot.role_id)
    print(f"bot={res.bot_name} user_id={res.bot_user_id} role={res.role} policy={res.policy}")
    # getattr: cmd_init_bot is called directly by embedders/tests with a partial Namespace.
    if getattr(args, "print_token", False):
        # Explicit opt-in, for tooling that consumes stdout: the token ends up in whatever captures
        # stdout (job log, journald, mail spool), so say so on stderr.
        print("OM_JWT_TOKEN=" + res.token)
        print(
            "warning: the bot JWT was printed to stdout and may persist in this job's log; "
            "prefer --write-token",
            file=sys.stderr,
        )
    else:
        path = write_secret_file(getattr(args, "write_token", None) or DEFAULT_TOKEN_PATH, res.token)
        print(
            f"token written to {path} (mode 600, sha256:{token_fingerprint(res.token)}); "
            f"export OM_JWT_TOKEN=$(cat {path})"
        )
    return 0


def cmd_init_service(cfg: Config) -> int:
    _, om = _clients(cfg)
    svc = om.upsert_custom_database_service(
        cfg.service.name, cfg.service.display_name, cfg.service.description, cfg.service.icon_url
    )
    print(f"service {svc['fullyQualifiedName']} ({svc['serviceType']}) id={svc['id']}")
    return 0


def cmd_metadata(cfg: Config) -> int:
    db, om = _clients(cfg)
    stats = MetadataSync(cfg, db, om).run()
    print(
        f"metadata: databases={stats.databases} schemas={stats.schemas} tables={stats.tables} "
        f"failed={stats.failed} deleted={stats.deleted}"
    )
    for err in stats.errors[:20]:
        print(f"  ! {err}")
    return 1 if stats.failed and not stats.tables else 0


def cmd_lineage(cfg: Config, full: bool) -> int:
    db, om = _clients(cfg)
    stats = LineageSync(cfg, db, om).run(full=full)
    print(
        f"lineage: rows={stats.rows} edges={stats.edges} written={stats.written} "
        f"skipped_missing={stats.skipped_missing} pruned={stats.pruned} watermark={stats.watermark}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lake-om")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    bot = sub.add_parser(
        "init-bot",
        help="(admin) provision a bot with an explicit administrator-managed role and write its JWT",
    )
    token_out = bot.add_mutually_exclusive_group()
    token_out.add_argument(
        "--write-token",
        metavar="PATH",
        help=f"write the JWT to PATH (mode 600); defaults to {DEFAULT_TOKEN_PATH}",
    )
    token_out.add_argument(
        "--print-token",
        action="store_true",
        help="print the JWT to stdout (it will be retained in this job's log)",
    )
    sub.add_parser("init-service", help="create/update the CustomDatabase service in OM")
    sub.add_parser("metadata", help="full sync of catalogs/databases/tables/columns")
    lin = sub.add_parser("lineage", help="incremental sync of lineage_history (use --full to ignore the watermark)")
    lin.add_argument("--full", action="store_true")
    both = sub.add_parser("all", help="init-service + metadata + lineage")
    both.add_argument("--full", action="store_true")

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load(args.config, initializing_bot=args.cmd == "init-bot")

    if args.cmd == "init-bot":
        return cmd_init_bot(cfg, args)
    if args.cmd == "init-service":
        return cmd_init_service(cfg)
    if args.cmd == "metadata":
        return cmd_metadata(cfg)
    if args.cmd == "lineage":
        return cmd_lineage(cfg, args.full)
    rc = cmd_init_service(cfg)
    rc |= cmd_metadata(cfg)
    rc |= cmd_lineage(cfg, args.full)
    return rc


if __name__ == "__main__":
    sys.exit(main())
