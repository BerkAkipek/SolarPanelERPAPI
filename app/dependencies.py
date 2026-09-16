"""Request transaction and organization scope shared by API modules."""
from typing import Annotated

from fastapi import Depends, Header, Path
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import DomainError
from app.models import Organization

DatabaseSession = Annotated[Session, Depends(get_session, scope="function")]
PathID = Annotated[int, Path(gt=0, le=9223372036854775807)]


def organization_scope(
    session: DatabaseSession,
    x_organization_id: Annotated[int, Header(gt=0, le=9223372036854775807)],
) -> int:
    if session.get(Organization, x_organization_id) is None:
        raise DomainError(404, "organization_not_found", "Organization was not found.")
    return x_organization_id


OrganizationID = Annotated[int, Depends(organization_scope)]
