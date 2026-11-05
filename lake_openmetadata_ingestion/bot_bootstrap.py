"""Provision a bot using an explicitly supplied, administrator-managed role.

Never create or broaden an authorization policy. Re-running rotates the JWT.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .om_client import OpenMetadataClient

logger = logging.getLogger(__name__)

@dataclass
class BotResult:
    bot_name: str
    bot_user_id: str
    role: str
    policy: str
    token: str


def validate_role_id(role_id: str) -> str:
    import uuid
    try:
        return str(uuid.UUID(role_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("bot.role_id must identify an administrator-provisioned, scoped OM role") from None


def bootstrap_bot(om_admin: OpenMetadataClient, name: str, token_expiry: str, email_domain: str,
                  *, role_id: str) -> BotResult:
    role_id = validate_role_id(role_id)
    if token_expiry not in ("OneHour", "1", "7", "30"):
        raise ValueError("Bot tokens must expire within 30 days")
    role = om_admin._req("GET", f"/v1/roles/{role_id}")
    if role.get("id") != role_id:
        raise ValueError("OpenMetadata returned an unexpected role")
    user = om_admin.upsert_bot_user(name, f"{name}@{email_domain}", [role_id], token_expiry)
    om_admin.upsert_bot(name, user["name"], "Bot used by lake-openmetadata-ingestion to push TiDB Cloud Lake metadata and lineage.")
    token = om_admin.generate_bot_token(user["id"], token_expiry)
    return BotResult(bot_name=name, bot_user_id=user["id"], role=role["name"], policy="administrator-managed", token=token)
