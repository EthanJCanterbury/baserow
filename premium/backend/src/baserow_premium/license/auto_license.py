"""
Auto-creates an enterprise license with a large seat count and a far-future
expiry so that all premium and enterprise features are unlocked without
needing a real signed license from Baserow's licensing servers.
"""

import json
import logging
from datetime import datetime, timezone

from django.db import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)

# The fake license payload - stored as plain JSON in the License.license field.
# decode_license() has been patched to accept plain JSON payloads directly.
ENTERPRISE_LICENSE_PAYLOAD = {
    "version": 1,
    "id": "enterprise-auto-00000000",
    "valid_from": "2020-01-01T00:00:00Z",
    "valid_through": "2099-12-31T23:59:59Z",
    "product_code": "enterprise",
    "seats": 9999999,
    "application_users": 9999999,
    "issued_on": "2024-01-01T00:00:00Z",
    "issued_to_email": "admin@localhost",
    "issued_to_name": "Self Hosted Enterprise",
    "instance_id": "__auto__",
}


def _build_license_payload() -> str:
    """
    Returns the JSON string to store in License.license.
    The instance_id is set to the real instance id of this installation so
    that any remaining instance_id checks elsewhere still pass.
    """

    payload = dict(ENTERPRISE_LICENSE_PAYLOAD)
    try:
        from baserow.core.handler import CoreHandler

        settings_object = CoreHandler().get_settings()
        payload["instance_id"] = settings_object.instance_id
    except Exception:
        payload["instance_id"] = "self-hosted"
    return json.dumps(payload)


def ensure_enterprise_license():
    """
    Ensures that at least one enterprise license exists in the database.
    If no licenses exist at all, creates a fake enterprise license.
    This is called from the admin licenses API view.
    """

    try:
        from baserow_premium.license.models import License

        if License.objects.exists():
            return  # Already have a license, nothing to do

        license_string = _build_license_payload()
        License.objects.create(
            license=license_string,
            cached_untrusted_instance_wide=True,
            last_check=datetime.now(tz=timezone.utc),
        )
        logger.info("Auto-created enterprise license.")
    except (OperationalError, ProgrammingError):
        # Database not ready yet (e.g., migrations haven't run)
        pass
    except Exception as e:
        logger.warning("Could not auto-create enterprise license: %s", e)
