from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    display_name: str
    domain: str
    status: str
    default_backend: Optional[str] = None
    notes: str = ""

    def to_dict(self):
        return asdict(self)


TARGET_SERVICE_SPECS = {
    "gitea": ServiceSpec(
        service_id="gitea",
        display_name="Gitea",
        domain="software_development",
        status="implemented",
        default_backend="gitea",
        notes="Primary code-hosting deployment target and current implemented backend.",
    ),
    "rocketchat": ServiceSpec(
        service_id="rocketchat",
        display_name="Rocket.Chat",
        domain="team_communication",
        status="planned",
        default_backend="rocketchat",
        notes="Planned chat and channel-management service.",
    ),
    "owncloud": ServiceSpec(
        service_id="owncloud",
        display_name="ownCloud",
        domain="file_management",
        status="planned",
        default_backend="owncloud",
        notes="Planned file-sharing and permission-management service.",
    ),
    "nocodb": ServiceSpec(
        service_id="nocodb",
        display_name="NocoDB",
        domain="database_table",
        status="planned",
        default_backend="nocodb",
        notes="Planned structured table CRUD service.",
    ),
    "zammad": ServiceSpec(
        service_id="zammad",
        display_name="Zammad",
        domain="customer_support",
        status="planned",
        default_backend="zammad",
        notes="Planned customer-support ticketing service.",
    ),
    "erpnext": ServiceSpec(
        service_id="erpnext",
        display_name="ERPNext",
        domain="finance_accounting",
        status="planned",
        default_backend="erpnext",
        notes="Planned finance and enterprise workflow service.",
    ),
    "openemr": ServiceSpec(
        service_id="openemr",
        display_name="OpenEMR",
        domain="healthcare",
        status="planned",
        default_backend="openemr",
        notes="Planned healthcare records and scheduling service.",
    ),
    "discourse": ServiceSpec(
        service_id="discourse",
        display_name="Discourse",
        domain="content_community",
        status="planned",
        default_backend="discourse",
        notes="Planned forum and community-moderation service.",
    ),
}


COMPAT_SERVICE_SPECS = {}


ALL_SERVICE_SPECS = {
    **TARGET_SERVICE_SPECS,
    **COMPAT_SERVICE_SPECS,
}


def get_service_spec(service_id):
    return ALL_SERVICE_SPECS.get(service_id)


def list_target_service_specs():
    return list(TARGET_SERVICE_SPECS.values())


def list_runtime_service_specs():
    return list(COMPAT_SERVICE_SPECS.values())


def list_all_service_specs():
    return list(ALL_SERVICE_SPECS.values())


def build_service_summary(include_compat=True):
    specs = list_target_service_specs()
    if include_compat:
        specs += list_runtime_service_specs()
    return [spec.to_dict() for spec in specs]
