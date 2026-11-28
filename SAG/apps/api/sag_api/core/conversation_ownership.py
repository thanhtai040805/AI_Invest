"""Stable ownership for conversations shared through the Continuum gateway."""

import hashlib
import json

from fastapi import Depends
from starlette.requests import Request

from sag_api.core.config import settings
from sag_api.core.deps import get_current_user
from sag_api.core.principal_assertion import VerifiedPrincipal, require_project_read_principal
from sag_api.db.models import User


def conversation_owner_key(user_id: str, principal: VerifiedPrincipal) -> str:
    identity = [user_id, principal.issuer, principal.organization_id, principal.tenant_id, principal.subject]
    if principal.issuer == "continuum-sag-session":
        if getattr(principal, "conversation_scope", None) == "all":
            identity.append(["all"])
        else:
            identity.append(sorted(principal.allowed_project_ids))
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


async def get_conversation_owner(
    request: Request,
    user: User = Depends(get_current_user),
) -> str:
    if settings.auth_mode == "local" and not request.headers.getlist("X-SAG-Principal-Assertion"):
        # Local identity partitions conversations only; data authorization still requires a signed assertion.
        local_identity = json.dumps(["sag-local", user.id], separators=(",", ":"))
        return hashlib.sha256(local_identity.encode()).hexdigest()
    principal = await require_project_read_principal(request)
    return conversation_owner_key(user.id, principal)
