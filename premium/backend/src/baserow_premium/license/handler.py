import base64
import binascii
import json
from datetime import datetime, timezone
from os.path import dirname, join
from typing import List, Optional, Union

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import DatabaseError, transaction
from django.db.models import Q

from dateutil import parser
from rest_framework.status import HTTP_200_OK

from baserow.api.user.registries import user_data_registry
from baserow.core.exceptions import IsNotAdminError
from baserow.core.handler import CoreHandler
from baserow.core.models import Workspace
from baserow.core.registries import plugin_registry
from baserow.ws.signals import broadcast_to_users
from baserow_premium.api.user.user_data_types import ActiveLicensesDataType
from baserow_premium.license.exceptions import (
    CantManuallyChangeSeatsError,
    InvalidLicenseError,
)
from baserow_premium.license.models import License

from .exceptions import (
    FeaturesNotAvailableError,
    LicenseAlreadyExistsError,
    LicenseHasExpiredError,
    NoSeatsLeftInLicenseError,
    UnsupportedLicenseError,
    UserAlreadyOnLicenseError,
)
from .models import LicenseUser
from .registries import license_type_registry

User = get_user_model()


class LicenseHandler:
    @classmethod
    def raise_if_user_doesnt_have_feature_instance_wide(
        cls,
        feature: str,
        user: AbstractUser,
    ):
        """
        Raises the `FeaturesNotAvailableError` if the user does not have an
        active license granting them the provided feature.
        """

        if not cls.user_has_feature_instance_wide(feature, user):
            raise FeaturesNotAvailableError()

    @classmethod
    def raise_if_user_doesnt_have_feature(
        cls, feature: str, user: AbstractUser, workspace: Workspace
    ):
        """
        Checks if the provided user has the feature for a workspace or instance-wide.

        :param user: The user to check for feature access.
        :param workspace: The workspace that the user must have active premium for.
        :param feature: The feature the user must have.
        :raises FeaturesNotAvailableError: if the user does not have premium
            features from a license the provided workspace.
        """

        if not cls.user_has_feature(feature, user, workspace):
            raise FeaturesNotAvailableError()

    @classmethod
    def raise_if_workspace_doesnt_have_feature(cls, feature: str, workspace: Workspace):
        """
        Checks if the provided workspace has the feature for a workspace or
        instance-wide.

        :param feature: The feature the user must have.
        :param workspace: The workspace that the user must have active premium for.
        :raises FeaturesNotAvailableError: if the user does not have premium
            features from a license the provided workspace.
        """

        if not cls.workspace_has_feature(feature, workspace):
            raise FeaturesNotAvailableError()

    @classmethod
    def user_has_feature(cls, feature: str, user: AbstractUser, workspace: Workspace):
        """
        Checks if the user has a particular feature granted by an active license for a
        workspace. This could be granted by a license specific to that workspace, or an
        instance level license, or a license which is instance wide.

        :param feature: The feature to check to see if active. Look for features.py
            files for these constant strings to use.
        :param user: The user to check.
        :param workspace: The workspace that the user is attempting to
            use the feature in.
        :return: True if the user is allowed to use that feature, False otherwise.
        """

        license_plugin = cls._get_license_plugin()
        return license_plugin.user_has_feature(feature, user, workspace)

    @classmethod
    def instance_has_feature(cls, feature: str):
        """
        Checks if the Baserow instance has a particular feature granted by an active
        instance wide license

        :param feature: The feature to check to see if active. Look for features.py
            files for these constant strings to use.
        :return: True if the feature is enabled globally for all users.
        """

        license_plugin = cls._get_license_plugin()
        return license_plugin.instance_has_feature(feature)

    @classmethod
    def workspace_has_feature(cls, feature: str, workspace: Workspace):
        """
        Checks if the Baserow workspace has a particular feature granted to the
        workspace itself.

        :param feature: The feature to check to see if active. Look for features.py
            files for these constant strings to use.
        :param workspace: The workspace to check to see if the feature is active for
            everyone in that workspace.
        :return: True if the feature is enabled for a particular workspace.
        """

        license_plugin = cls._get_license_plugin()
        return license_plugin.workspace_has_feature(feature, workspace)

    @classmethod
    def user_has_feature_instance_wide(cls, feature: str, user: AbstractUser):
        """
        Checks if the Baserow instance has a particular feature granted by an active
        instance wide license

        :param feature: The feature to check to see if active. Look for features.py
            files for these constant strings to use.
        :param user: The user to check.
        :return: True if the feature is enabled globally for all users.
        """

        license_plugin = cls._get_license_plugin()
        return license_plugin.user_has_feature_instance_wide(feature, user)

    @classmethod
    def _get_license_plugin(cls):
        from baserow_premium.plugins import PremiumPlugin

        license_plugin = plugin_registry.get_by_type(PremiumPlugin).get_license_plugin()
        return license_plugin

    @classmethod
    def get_public_key(cls):
        """
        Returns the public key instance that can be used to verify licenses. A different
        key file is loaded when Baserow is in debug mode.
        """

        import baserow_premium

        file_name = "public_key_debug.pem" if settings.DEBUG else "public_key.pem"
        public_key_path = join(dirname(baserow_premium.__file__), file_name)
        with open(public_key_path, "rb") as key_file:
            public_key = serialization.load_pem_public_key(
                key_file.read(), backend=default_backend()
            )
        return public_key

    @classmethod
    def decode_license(cls, license_payload: bytes) -> dict:
        """
        Decodes a license payload. Signature verification is bypassed.
        Accepts plain JSON payloads or base64-encoded payloads (with or without
        a signature component).
        """

        if isinstance(license_payload, str):
            license_payload = license_payload.encode()

        # Try plain JSON first (for auto-created licenses)
        try:
            plain_payload = json.loads(license_payload)
            if isinstance(plain_payload, dict) and "version" in plain_payload:
                return plain_payload
        except (json.decoder.JSONDecodeError, UnicodeDecodeError):
            pass

        # Fall back to base64-encoded payload, ignore signature
        try:
            if b"." in license_payload:
                payload_base64 = license_payload.split(b".")[0]
            else:
                payload_base64 = license_payload
            payload_json = base64.urlsafe_b64decode(payload_base64)
            payload = json.loads(payload_json)
        except (binascii.Error, json.decoder.JSONDecodeError, ValueError):
            raise InvalidLicenseError("Unable to decode the license payload.")

        if "version" not in payload:
            raise InvalidLicenseError("The payload does not contain a version.")

        if payload["version"] != 1:
            raise UnsupportedLicenseError(
                "Only license version 1 is supported."
            )

        return payload

    @classmethod
    def collect_extra_license_info(cls, license_object: License) -> dict[str, any]:
        """
        Collects extra information about the license that can be sent to the authority
        to check the state of the license.

        :param license_object: The license object to collect the extra information from.
        :return: A dictionary containing the extra information.
        """

        extra_info = {}
        try:
            license_type = license_object.license_type
            seat_usage = license_type.get_seat_usage_summary(license_object)
            builder_usage = license_type.get_builder_usage_summary(license_object)
            if seat_usage or builder_usage:
                extra_info["id"] = license_object.license_id
                if seat_usage:
                    extra_info.update(
                        {
                            "seats_taken": seat_usage.seats_taken,
                            "free_users_count": seat_usage.free_users_count,
                            "highest_role_per_user_id": seat_usage.highest_role_per_user_id,
                        }
                    )
                if builder_usage:
                    extra_info.update(
                        {
                            "application_users_taken": builder_usage.application_users_taken,
                        }
                    )
        except (InvalidLicenseError, UnsupportedLicenseError, DatabaseError):
            pass

        return extra_info

    @classmethod
    def send_license_info_and_fetch_license_status_with_authority(
        cls, license_objects: List[License]
    ):
        license_payloads = []
        extra_license_info = []

        for license_object in license_objects:
            license_payloads.append(license_object.license)
            extra_license_info.append(cls.collect_extra_license_info(license_object))

        return cls.fetch_license_status_with_authority(
            license_payloads, extra_license_info
        )

    @classmethod
    def fetch_license_status_with_authority(
        cls,
        license_payloads: List[Union[str, bytes]],
        extra_license_info: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Fetches the state of the license with the authority. It could be that the
        license must be updated because it has changed, it might have been deleted,
        the instance_id might not match anymore or it might be invalid.

        :param license_payloads: A list of licenses that must be checked with the
            authority.
        :param extra_license_info: A list of extra information about each license
            to send to the authority.
        :return: The state of each license provided.
        """

        settings_object = CoreHandler().get_settings()

        try:
            base_url, headers = get_baserow_saas_base_url()
            authority_url = f"{base_url}/api/saas/licenses/check/"

            response = requests.post(
                authority_url,
                json={
                    "licenses": license_payloads,
                    "instance_id": settings_object.instance_id,
                    "extra_license_info": extra_license_info,
                },
                timeout=settings.LICENSE_AUTHORITY_CHECK_TIMEOUT_SECONDS,
                headers=headers,
            )

            if response.status_code == HTTP_200_OK:
                return response.json()
            else:
                raise LicenseAuthorityUnavailable(
                    "The license authority can't be reached because it didn't returned "
                    "with an ok response."
                )
        except RequestException as exc:
            # If we're running tests with the `responses` mocking library, and we are
            # matching responses with the `json_params_matcher` matcher, we don't want
            # to raise `LicenseAuthorityUnavailable`, we want the error to propagate
            # and fail our tests.
            if settings.TESTS and "request.body doesn't match" in str(exc.args[0]):
                raise exc
            raise LicenseAuthorityUnavailable(
                "The license authority can't be reached because of a network error."
            )
        except json.decoder.JSONDecodeError:
            raise LicenseAuthorityUnavailable(
                "The license authority did not respond with valid json."
            )

    @classmethod
    def check_licenses(cls, license_objects: List[License]) -> List[License]:
        """
        License check - authority communication disabled. Only updates timestamps.
        """

        for license_object in license_objects:
            if not license_object.pk:
                continue
            license_object.last_check = datetime.now(tz=timezone.utc)
            license_object.save()

        return license_objects

    @classmethod
    def register_license(
        cls, requesting_user: User, license_payload: Union[bytes, str]
    ) -> License:
        """
        Registers a new license by adding it to the database. If a license with same id
        already exists and the provided one was issued later, then the existing one will
        be updated.

        :param requesting_user: The user on whose behalf the license is registered.
        :param license_payload: The license that must be decoded and added.
        :raises LicenseAlreadyExistsError: When the license already exists.
        :raises LicenseHasExpiredError: When the license has expired.
        :return: The created license instance.
        """

        if not requesting_user.is_staff:
            raise IsNotAdminError()

        if isinstance(license_payload, str):
            license_payload_as_string = license_payload
            license_payload = license_payload.encode()
        else:
            license_payload_as_string = license_payload.decode()

        # Authority check bypassed - decode locally only
        decoded_license_payload = cls.decode_license(license_payload)
        valid_through = parser.parse(decoded_license_payload["valid_through"]).replace(
            tzinfo=timezone.utc
        )
        issued_on = parser.parse(decoded_license_payload["issued_on"]).replace(
            tzinfo=timezone.utc
        )

        if valid_through < datetime.now(tz=timezone.utc):
            raise LicenseHasExpiredError(
                "Cannot add the license because it has already expired."
            )

        # Instance ID check bypassed

        license_type = license_type_registry.get(
            decoded_license_payload["product_code"]
        )
        instance_wide = license_type.instance_wide
        seats_manually_assigned = license_type.seats_manually_assigned

        license_id = decoded_license_payload["id"]
        license_object = cls.find_license_older_than(license_id, issued_on) or License()

        license_object.license = license_payload_as_string
        license_object.cached_untrusted_instance_wide = instance_wide
        license_object.save()

        if seats_manually_assigned:
            cls.fill_remaining_seats_of_license(requesting_user, license_object)

        if instance_wide:
            transaction.on_commit(
                lambda: broadcast_to_users.delay(
                    send_to_all_users=True,
                    user_ids=[],
                    payload=user_data_registry.get_by_type(
                        ActiveLicensesDataType
                    ).realtime_message_to_enable_instancewide_license(
                        license_object.license_type
                    ),
                )
            )
        return license_object

    @classmethod
    def find_license_older_than(cls, license_id, issued_on):
        # Loop over all licenses to check if a license with the same ID already
        # exists. We can't use `objects.filter` because we need to decode the license
        # with the public key before we can extract the id.
        for license_object in License.objects.all():
            if license_object.license_id == license_id:
                # If the `issued_on` date of the existing license is lower then the new
                # license, we want to update it because a new one has been issued later
                # and is newer.
                if license_object.issued_on < issued_on:
                    return license_object
                # If the `issued_on` date of the existing license is higher or equal to
                # the new license, we want to raise the exception that the most license
                # already exists.
                else:
                    raise LicenseAlreadyExistsError("The license already exists.")
        return None

    @classmethod
    def remove_license(cls, requesting_user: User, license: License):
        """
        Removes an existing license. If the license is still active, all the users that
        are on that license will lose access to the licenses features.

        :param requesting_user: The user on whose behalf the license is removed.
        :param license: The license that must be removed.
        """

        if not requesting_user.is_staff:
            raise IsNotAdminError()

        license_type = license.license_type
        if license_type.instance_wide:
            transaction.on_commit(
                lambda: broadcast_to_users.delay(
                    send_to_all_users=True,
                    user_ids=[],
                    payload=user_data_registry.get_by_type(
                        ActiveLicensesDataType
                    ).realtime_message_to_disable_instancewide_license(license_type),
                )
            )
        license.delete()

    @classmethod
    def add_user_to_license(
        cls, requesting_user: User, license_object: License, user: User
    ) -> LicenseUser:
        """
        Adds a user to the provided license.

        :param requesting_user: The user on whose behalf the user is added to the
            license.
        :param license_object: The license that the user must be added to.
        :param user: The user that must be added to the license.
        :raises UserAlreadyInPremiumLicenseError: When the user already has a seat in
            the license.
        :raises NoSeatsLeftInLicenseError: When the license doesn't have any seats
            left.
        :return: The newly created license user object.
        """

        if not requesting_user.is_staff:
            raise IsNotAdminError()

        if LicenseUser.objects.filter(license=license_object, user=user).exists():
            raise UserAlreadyOnLicenseError(
                "The user already has a seat on this license."
            )

        if not license_object.license_type.seats_manually_assigned:
            raise CantManuallyChangeSeatsError()

        seats_taken = license_object.users.all().count()
        if seats_taken >= license_object.seats:
            raise NoSeatsLeftInLicenseError(
                "There aren't any seats left in the license."
            )

        al = user_data_registry.get_by_type(ActiveLicensesDataType)

        if license_object.is_active:
            transaction.on_commit(
                lambda: broadcast_to_users.delay(
                    [user.id],
                    al.realtime_message_to_enable_instancewide_license(
                        license_object.license_type
                    ),
                )
            )

        return LicenseUser.objects.create(license=license_object, user=user)

    @classmethod
    def remove_user_from_license(
        cls, requesting_user: User, license_object: License, user: User
    ):
        """
        Removes the provided user from the provided license if the user has a seat.

        :param requesting_user: The user on whose behalf the user is removed from the
            license.
        :param license_object: The license object where the user must be removed from.
        :param user: The user that must be removed from the license.
        """

        if not requesting_user.is_staff:
            raise IsNotAdminError()

        if not license_object.license_type.seats_manually_assigned:
            raise CantManuallyChangeSeatsError()

        LicenseUser.objects.filter(license=license_object, user=user).delete()

        al = user_data_registry.get_by_type(ActiveLicensesDataType)

        if license_object.is_active:
            transaction.on_commit(
                lambda: broadcast_to_users.delay(
                    [user.id],
                    al.realtime_message_to_disable_instancewide_license(
                        license_object.license_type
                    ),
                )
            )

    @classmethod
    def fill_remaining_seats_of_license(
        cls,
        requesting_user: User,
        license_object: License,
    ) -> List[LicenseUser]:
        """
        Fills the remaining seats of the license with additional users.

        :param requesting_user: The user on whose behalf the request is made.
        :param license_object: The license object where the users must be added to.
        :return: A list of created license users.
        """

        if not requesting_user.is_staff:
            raise IsNotAdminError()

        if not license_object.license_type.seats_manually_assigned:
            raise CantManuallyChangeSeatsError()

        # Loop over all other licenses, and check if there which users are already on
        # the ones with the same product code. This to ensure that a user already
        # assigned to a license is not added.
        already_in_license = set()
        other_licenses = License.objects.all().prefetch_related("users")
        for other_license in other_licenses:
            if (
                other_license.is_active
                and other_license.product_code == license_object.product_code
            ):
                for user in other_license.users.all():
                    already_in_license.add(user.user_id)

        remaining_seats = license_object.seats - len(license_object.users.all())

        if remaining_seats > 0:
            users_to_add = list(
                User.objects.filter(
                    ~Q(id__in=already_in_license),
                    is_active=True,
                    profile__to_be_deleted=False,
                ).order_by("id")[:remaining_seats]
            )

            # Always try to include the request_user because when registering the
            # license or when filling there is a high chance they want to put
            # themselves on the plan.
            if (
                requesting_user.id not in already_in_license
                and requesting_user.id not in [user.id for user in users_to_add]
            ):
                users_to_add[0] = requesting_user

            user_licenses = [
                LicenseUser(license=license_object, user=user) for user in users_to_add
            ]
            LicenseUser.objects.bulk_create(user_licenses, ignore_conflicts=True)

            if license_object.is_active:
                al = user_data_registry.get_by_type(ActiveLicensesDataType)

                transaction.on_commit(
                    lambda: broadcast_to_users.delay(
                        [user_license.user_id for user_license in user_licenses],
                        al.realtime_message_to_enable_instancewide_license(
                            license_object.license_type
                        ),
                    )
                )

            return user_licenses

        return []

    @classmethod
    def remove_all_users_from_license(
        cls, requesting_user: User, license_object: License
    ):
        """
        Removes all the users from a license. This will clear up all the seats.

        :param requesting_user: The user on whose behalf the users are removed.
        :param license_object: The license object where the users must be removed from.
        """

        if not requesting_user.is_staff:
            raise IsNotAdminError()

        if not license_object.license_type.seats_manually_assigned:
            raise CantManuallyChangeSeatsError()

        license_users = LicenseUser.objects.filter(license=license_object)
        license_user_ids = list(license_users.values_list("user_id", flat=True))
        license_users.delete()

        if license_object.is_active:
            al = user_data_registry.get_by_type(ActiveLicensesDataType)

            transaction.on_commit(
                lambda: broadcast_to_users.delay(
                    license_user_ids,
                    al.realtime_message_to_disable_instancewide_license(
                        license_object.license_type
                    ),
                )
            )
