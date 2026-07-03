<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { LLMProviderConfig } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'AIReviewSettings',
})

const { text } = useLanguage()
const loading = ref(false)
const saving = ref(false)
const testing = reactive<Record<number, boolean>>({})
const providers = ref<LLMProviderConfig[]>([])

const form = reactive({
  name: '',
  provider_type: 'openai_compatible',
  base_url: '',
  api_key: '',
  model: '',
  is_active: false,
})

onMounted(loadProviders)

async function loadProviders() {
  loading.value = true
  try {
    providers.value = await apiConductor.llmProviders()
  }
  finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.provider_type = 'openai_compatible'
  form.base_url = ''
  form.api_key = ''
  form.model = ''
  form.is_active = providers.value.length === 0
}

async function createProvider() {
  if (!form.name.trim() || !form.base_url.trim() || !form.api_key.trim() || !form.model.trim()) {
    faToast.warning(text('Please fill in all provider fields.', '请填写完整 Provider 信息。'))
    return
  }
  saving.value = true
  try {
    await apiConductor.createLlmProvider({
      name: form.name.trim(),
      provider_type: form.provider_type,
      base_url: form.base_url.trim(),
      api_key: form.api_key,
      model: form.model.trim(),
      is_active: form.is_active || providers.value.length === 0,
    })
    faToast.success(text('Provider saved.', 'Provider 已保存。'))
    resetForm()
    await loadProviders()
  }
  finally {
    saving.value = false
  }
}

async function activateProvider(provider: LLMProviderConfig) {
  await apiConductor.activateLlmProvider(provider.id)
  faToast.success(text('Active review model updated.', '审查模型已切换。'))
  await loadProviders()
}

async function deleteProvider(provider: LLMProviderConfig) {
  await apiConductor.deleteLlmProvider(provider.id)
  faToast.success(text('Provider deleted.', 'Provider 已删除。'))
  await loadProviders()
}

async function testProvider(provider: LLMProviderConfig) {
  testing[provider.id] = true
  try {
    const result = await apiConductor.testLlmProvider(provider.id)
    if (result.ok)
      faToast.success(text('Connection succeeded.', '连接测试成功。'))
    else
      faToast.error(result.error || text('Connection failed.', '连接测试失败。'))
  }
  finally {
    testing[provider.id] = false
  }
}

function providerTypeLabel(type: string) {
  if (type === 'openai_compatible')
    return 'OpenAI Compatible'
  if (type === 'anthropic')
    return 'Anthropic'
  return type
}
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6 p-6">
    <div class="flex items-end justify-between gap-4">
      <div>
        <div class="text-sm text-muted-foreground">
          {{ text('Configure the model used by semantic verifier checks', '配置语义审查使用的模型') }}
        </div>
        <h1 class="mt-1 text-2xl font-semibold">
          {{ text('AI Review Providers', 'AI 审查 Provider') }}
        </h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="loadProviders">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <section class="rounded-xl border bg-background p-4">
      <div class="mb-4 flex items-center gap-2 font-medium">
        <FaIcon name="i-lucide:plus-circle" />
        {{ text('Add Provider', '添加 Provider') }}
      </div>
      <div class="grid gap-3 md:grid-cols-2">
        <label class="grid gap-1 text-sm">
          <span class="text-xs text-muted-foreground">{{ text('Name', '名称') }}</span>
          <input v-model="form.name" class="h-9 rounded-lg border bg-background px-3 outline-none focus:border-primary" placeholder="DeepSeek V3">
        </label>
        <label class="grid gap-1 text-sm">
          <span class="text-xs text-muted-foreground">{{ text('Provider Type', '类型') }}</span>
          <select v-model="form.provider_type" class="h-9 rounded-lg border bg-background px-3 outline-none focus:border-primary">
            <option value="openai_compatible">OpenAI Compatible</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </label>
        <label class="grid gap-1 text-sm">
          <span class="text-xs text-muted-foreground">Base URL</span>
          <input v-model="form.base_url" class="h-9 rounded-lg border bg-background px-3 font-mono text-sm outline-none focus:border-primary" placeholder="https://api.deepseek.com/v1">
        </label>
        <label class="grid gap-1 text-sm">
          <span class="text-xs text-muted-foreground">Model</span>
          <input v-model="form.model" class="h-9 rounded-lg border bg-background px-3 font-mono text-sm outline-none focus:border-primary" placeholder="deepseek-chat">
        </label>
        <label class="grid gap-1 text-sm md:col-span-2">
          <span class="text-xs text-muted-foreground">API Key</span>
          <input v-model="form.api_key" type="password" class="h-9 rounded-lg border bg-background px-3 font-mono text-sm outline-none focus:border-primary" placeholder="sk-...">
        </label>
      </div>
      <div class="mt-4 flex items-center justify-between gap-3">
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.is_active" type="checkbox" class="size-4">
          {{ text('Set as active review model', '设为当前审查模型') }}
        </label>
        <FaButton :loading="saving" @click="createProvider">
          <FaIcon name="i-lucide:save" class="mr-2" />
          {{ text('Save Provider', '保存 Provider') }}
        </FaButton>
      </div>
    </section>

    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <div class="font-medium">{{ text('Configured Providers', '已配置 Provider') }}</div>
        <div class="text-xs text-muted-foreground">
          {{ providers.length }} {{ text('providers', '个 Provider') }}
        </div>
      </div>

      <div v-if="loading" class="flex justify-center rounded-xl border bg-background py-12">
        <FaIcon name="i-lucide:loader-2" class="animate-spin text-2xl text-muted-foreground" />
      </div>
      <div v-else-if="!providers.length" class="rounded-xl border bg-background p-8 text-center text-muted-foreground">
        {{ text('No providers configured yet.', '尚未配置 Provider。') }}
      </div>
      <div v-else class="grid gap-3 md:grid-cols-2">
        <div
          v-for="provider in providers"
          :key="provider.id"
          class="rounded-xl border bg-background p-4"
          :class="provider.is_active ? 'border-primary' : ''"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <div class="truncate font-medium">{{ provider.name }}</div>
                <span v-if="provider.is_active" class="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
                  {{ text('Active', '当前') }}
                </span>
              </div>
              <div class="mt-1 text-xs text-muted-foreground">
                {{ providerTypeLabel(provider.provider_type) }}
              </div>
            </div>
            <FaIcon name="i-lucide:key-round" :class="provider.api_key_set ? 'text-emerald-500' : 'text-muted-foreground'" />
          </div>

          <div class="mt-3 grid gap-2 text-sm">
            <div>
              <div class="text-xs text-muted-foreground">Base URL</div>
              <div class="truncate font-mono">{{ provider.base_url }}</div>
            </div>
            <div>
              <div class="text-xs text-muted-foreground">Model</div>
              <div class="truncate font-mono">{{ provider.model }}</div>
            </div>
          </div>

          <div class="mt-4 flex flex-wrap justify-end gap-2">
            <FaButton size="sm" variant="outline" :loading="testing[provider.id]" @click="testProvider(provider)">
              <FaIcon name="i-lucide:plug" class="mr-1.5" />
              {{ text('Test', '测试') }}
            </FaButton>
            <FaButton v-if="!provider.is_active" size="sm" variant="outline" @click="activateProvider(provider)">
              <FaIcon name="i-lucide:radio" class="mr-1.5" />
              {{ text('Activate', '激活') }}
            </FaButton>
            <FaButton size="sm" variant="outline" @click="deleteProvider(provider)">
              <FaIcon name="i-lucide:trash-2" class="mr-1.5" />
              {{ text('Delete', '删除') }}
            </FaButton>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
