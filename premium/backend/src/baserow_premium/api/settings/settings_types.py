from baserow.api.settings.registries import SettingsDataType


class InstanceWideSettingsDataType(SettingsDataType):
    type = "instance_wide_licenses"

    def get_settings_data(self, request):
        """
        Someone who authenticates via the API should know beforehand if the related
        user has active licenses.
        """

        # All license types granted unconditionally.
        return {
            "premium": True,
            "enterprise": True,
        }
