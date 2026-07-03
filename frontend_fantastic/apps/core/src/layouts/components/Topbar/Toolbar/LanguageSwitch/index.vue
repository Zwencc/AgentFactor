<script setup lang="ts">
import { useLanguage } from '@/i18n'

defineOptions({
  name: 'ToolbarLanguageSwitch',
})

const { language, setLanguage } = useLanguage()
</script>

<template>
  <div class="flex items-center gap-0.5 rounded-md border border-border px-1 py-0.5 text-xs">
    <button
      class="rounded px-1.5 py-0.5 transition-colors"
      :class="language === 'en' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
      @click="setLanguage('en')"
    >
      EN
    </button>
    <button
      class="rounded px-1.5 py-0.5 transition-colors"
      :class="language === 'zh' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
      @click="setLanguage('zh')"
    >
      中
    </button>
  </div>
</template>
