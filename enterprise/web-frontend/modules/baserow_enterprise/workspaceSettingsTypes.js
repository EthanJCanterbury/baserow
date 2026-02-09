import { SettingsType } from '@baserow/modules/core/settingsTypes'
import AssistantWorkspaceSettings from '@baserow_enterprise/components/assistant/AssistantWorkspaceSettings'

export class AssistantWorkspaceSettingsType extends SettingsType {
  static getType() {
    return 'assistant'
  }

  getIconClass() {
    return 'iconoir-sparks'
  }

  getName() {
    const { $i18n: i18n } = this.app
    return i18n.t('assistantWorkspaceSettings.tabTitle')
  }

  getComponent() {
    return AssistantWorkspaceSettings
  }

  getOrder() {
    return 60
  }
}
