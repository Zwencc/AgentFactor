<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { Persona, ProviderHealth } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ConductorProviders',
})

const loading = ref(false)
const { text } = useLanguage()
const providers = ref<ProviderHealth[]>([])
const personas = ref<Persona[]>([])

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [providerRes, personaRes] = await Promise.all([
      apiConductor.providers(),
      apiConductor.personas(),
    ])
    providers.value = providerRes
    personas.value = personaRes
  }
  finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="mx-auto p-6 max-w-7xl space-y-6">
    <div class="flex items-end justify-between">
      <div>
        <div class="text-sm text-muted-foreground">{{ text('Runtime capability and persona catalog', '运行环境、角色与可用能力') }}</div>
        <h1 class="mt-1 text-2xl font-semibold">{{ text('Providers', '运行环境') }}</h1>
      </div>
      <FaButton variant="outline" :loading="loading" @click="load">
        <FaIcon name="i-lucide:refresh-cw" class="mr-2" />
        {{ text('Refresh', '刷新') }}
      </FaButton>
    </div>

    <section class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <div v-for="provider in providers" :key="provider.key" class="border rounded-lg bg-background p-5">
        <div class="mb-4 flex items-center justify-between">
          <div>
            <div class="font-semibold">{{ provider.label }}</div>
            <div class="text-xs text-muted-foreground">{{ provider.key }}</div>
          </div>
          <FaIcon :name="provider.available ? 'i-lucide:check-circle-2' : 'i-lucide:circle-alert'" class="size-5" :class="provider.available ? 'text-green-600' : 'text-red-600'" />
        </div>
        <div class="text-sm">
          <div class="text-muted-foreground">{{ text('Binary', '可执行文件') }}</div>
          <code>{{ provider.binary }}</code>
        </div>
        <div v-if="provider.reason" class="mt-4 rounded-md bg-red-500/5 p-3 text-xs text-red-600">
          {{ provider.reason }}
        </div>
      </div>
    </section>

    <section class="border rounded-lg bg-background p-5">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-lg font-semibold">{{ text('Personas', '角色') }}</h2>
        <span class="text-sm text-muted-foreground">{{ personas.length }} {{ text('entries', '项') }}</span>
      </div>
      <div class="overflow-auto">
        <table class="w-full text-sm">
          <thead class="text-left text-muted-foreground">
            <tr>
              <th class="py-2">{{ text('Name', '名称') }}</th>
              <th class="py-2">{{ text('Default provider', '默认运行方') }}</th>
              <th class="py-2">{{ text('Source', '来源') }}</th>
              <th class="py-2">{{ text('Tags', '标签') }}</th>
              <th class="py-2">{{ text('Description', '描述') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="persona in personas" :key="persona.name" class="border-t">
              <td class="py-3 font-medium">{{ persona.name }}</td>
              <td class="py-3">{{ persona.default_provider }}</td>
              <td class="py-3">{{ persona.source }}</td>
              <td class="py-3">{{ persona.tags.join(', ') }}</td>
              <td class="py-3 text-muted-foreground">{{ persona.description }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
