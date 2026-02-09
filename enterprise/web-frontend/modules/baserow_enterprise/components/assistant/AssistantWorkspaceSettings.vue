<template>
  <div>
    <h2 class="box__title">{{ $t('assistantWorkspaceSettings.title') }}</h2>
    <p>{{ $t('assistantWorkspaceSettings.description') }}</p>
    <Error :error="error"></Error>
    <Alert v-if="success" ref="success" type="success">
      <template #title>{{
        $t('assistantWorkspaceSettings.changedTitle')
      }}</template>
      <p>{{ $t('assistantWorkspaceSettings.changedDescription') }}</p>
    </Alert>
    <div v-if="fetchLoading">
      <div class="loading"></div>
    </div>
    <form v-else @submit.prevent="updateSettings">
      <FormGroup
        small-label
        :label="$t('assistantWorkspaceSettings.aiTypeLabel')"
        class="margin-bottom-2"
      >
        <Dropdown
          v-model="selectedAiType"
          class="dropdown--floating"
          :fixed-items="true"
          :show-search="false"
          @change="onAiTypeChanged"
        >
          <DropdownItem
            :name="$t('assistantWorkspaceSettings.noSelection')"
            value=""
          />
          <DropdownItem
            v-for="aiType in availableAiTypes"
            :key="aiType.type"
            :name="aiType.name"
            :value="aiType.type"
          />
        </Dropdown>
      </FormGroup>

      <FormGroup
        v-if="selectedAiType"
        small-label
        :label="$t('assistantWorkspaceSettings.aiModelLabel')"
        class="margin-bottom-2"
      >
        <Dropdown
          v-model="selectedAiModel"
          class="dropdown--floating"
          :fixed-items="true"
          :show-search="false"
        >
          <DropdownItem
            v-for="model in availableModels"
            :key="model"
            :name="model"
            :value="model"
          />
        </Dropdown>
      </FormGroup>

      <div class="actions actions--right">
        <Button
          :disabled="updateLoading"
          :loading="updateLoading"
          icon="iconoir-edit-pencil"
        >
          {{ $t('assistantWorkspaceSettings.submitButton') }}
        </Button>
      </div>
    </form>
  </div>
</template>

<script>
import error from '@baserow/modules/core/mixins/error'

export default {
  name: 'AssistantWorkspaceSettings',
  mixins: [error],
  props: {
    workspace: {
      type: Object,
      required: true,
    },
  },
  data() {
    return {
      fetchLoading: false,
      updateLoading: false,
      success: false,
      selectedAiType: '',
      selectedAiModel: '',
    }
  },
  computed: {
    availableAiTypes() {
      const enabled = this.workspace.generative_ai_models_enabled || {}
      return Object.keys(enabled)
        .filter((type) => enabled[type].length > 0)
        .map((type) => {
          try {
            const modelType = this.$registry.get('generativeAIModel', type)
            return { type, name: modelType.getName() }
          } catch {
            return { type, name: type }
          }
        })
    },
    availableModels() {
      if (!this.selectedAiType) return []
      const enabled = this.workspace.generative_ai_models_enabled || {}
      return enabled[this.selectedAiType] || []
    },
  },
  async mounted() {
    await this.fetchSettings()
  },
  methods: {
    onAiTypeChanged() {
      // Auto-select first model when type changes
      const models = this.availableModels
      this.selectedAiModel = models.length > 0 ? models[0] : ''
    },
    async fetchSettings() {
      this.fetchLoading = true
      try {
        const { data } = await this.$client.get(
          `/assistant/settings/${this.workspace.id}/`
        )
        this.selectedAiType = data.ai_type || ''
        this.selectedAiModel = data.ai_model || ''
      } catch (e) {
        this.handleError(e)
      } finally {
        this.fetchLoading = false
      }
    },
    async updateSettings() {
      this.updateLoading = true
      this.hideError()
      this.success = false

      try {
        await this.$client.patch(
          `/assistant/settings/${this.workspace.id}/`,
          {
            ai_type: this.selectedAiType,
            ai_model: this.selectedAiModel,
          }
        )

        // Update local workspace data so sidebar reflects the change
        await this.$store.dispatch('workspace/forceUpdate', {
          workspace: this.workspace,
          values: {
            assistant_settings: {
              ai_type: this.selectedAiType,
              ai_model: this.selectedAiModel,
            },
          },
        })

        this.success = true
        this.$nextTick(() => {
          if (this.$refs.success) {
            this.$refs.success.$el.scrollIntoView({ behavior: 'smooth' })
          }
        })
      } catch (e) {
        this.handleError(e)
      } finally {
        this.updateLoading = false
      }
    },
  },
}
</script>
