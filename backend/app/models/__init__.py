"""SQLAlchemy declarative base + model re-exports."""

from app.models.access_request import (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING_ADMIN,
    STATUS_PENDING_EMAIL_CONFIRM,
    AccessRequest,
)
from app.models.api_key import ApiKey
from app.models.base import Base
from app.models.email_throttle import EmailThrottle
from app.models.entity_snapshot import EntitySnapshot
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.models.event import (
    ALL_ENTITY_TYPES,
    ALL_OPS,
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_EXTRACTION_ENTITY,
    ENTITY_TYPE_HMO_ITEM_OVERRIDE,
    ENTITY_TYPE_MARC_RECORD,
    ENTITY_TYPE_WIKIBASE_ITEM,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    OP_REVERT,
    OP_SNAPSHOT,
    ProjectEvent,
    ProjectSnapshot,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.inference_cache import InferenceCache
from app.models.invitation import Invitation
from app.models.item_override import WikidataItemOverride
from app.models.password_reset import PasswordResetToken
from app.models.project import (
    ALL_PROJECT_ROLES,
    PROJECT_ROLE_EDITOR,
    PROJECT_ROLE_OWNER,
    PROJECT_ROLE_VIEWER,
    Membership,
    Project,
)
from app.models.rdf_artifact import RdfArtifact
from app.models.run import (
    RUN_STATUS_FAILED,
    RUN_STATUS_PENDING,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    AuthorityMatch,
    Run,
    RunRecord,
)
from app.models.run_job import RunJob
from app.models.saved_query import SavedQuery
from app.models.session import Session
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, User
from app.models.wikibase_entity_mapping import (
    ENTITY_KIND_CLASS,
    ENTITY_KIND_INSTANCE,
    ENTITY_KIND_PROPERTY,
    WikibaseEntityMapping,
)
from app.models.wikibase_cloud_write import WikibaseCloudWrite
from app.models.wikibase_user_access import WikibaseUserAccess
from app.models.wikidata_studio_cache import WikidataStudioCache

__all__ = [
    "ALL_ENTITY_TYPES",
    "ALL_OPS",
    "ALL_PROJECT_ROLES",
    "AccessRequest",
    "ApiKey",
    "AuthorityMatch",
    "Base",
    "ENTITY_KIND_CLASS",
    "ENTITY_KIND_INSTANCE",
    "ENTITY_KIND_PROPERTY",
    "ENTITY_TYPE_AUTHORITY_MATCH",
    "ENTITY_TYPE_EXTRACTION_ENTITY",
    "ENTITY_TYPE_HMO_ITEM_OVERRIDE",
    "ENTITY_TYPE_MARC_RECORD",
    "ENTITY_TYPE_WIKIBASE_ITEM",
    "ENTITY_TYPE_WIKIDATA_OVERRIDE",
    "EmailThrottle",
    "EntitySnapshot",
    "HmoStudioItemCache",
    "HmoStudioItemOverride",
    "ExtractionApproval",
    "InferenceCache",
    "Invitation",
    "Membership",
    "OP_CREATE",
    "OP_PATCH",
    "OP_REVERT",
    "OP_SNAPSHOT",
    "PROJECT_ROLE_EDITOR",
    "PROJECT_ROLE_OWNER",
    "PROJECT_ROLE_VIEWER",
    "PasswordResetToken",
    "Project",
    "ProjectEvent",
    "ProjectSnapshot",
    "WikidataItemOverride",
    "WikidataStudioCache",
    "ROLE_ADMIN",
    "ROLE_EDITOR",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_PENDING",
    "RUN_STATUS_RUNNING",
    "RUN_STATUS_SUCCEEDED",
    "Run",
    "RunJob",
    "RdfArtifact",
    "RunRecord",
    "SavedQuery",
    "STATUS_APPROVED",
    "STATUS_DENIED",
    "STATUS_PENDING_ADMIN",
    "STATUS_PENDING_EMAIL_CONFIRM",
    "Session",
    "User",
    "WikibaseEntityMapping",
    "WikibaseCloudWrite",
]
