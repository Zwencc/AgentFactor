<script setup lang="ts">
import { useSlots } from '@/slots'
import LanguageSwitch from './LanguageSwitch/index.vue'
import Tools from './tools.vue'

defineOptions({
  name: 'ToolbarRightSide',
})
</script>

<template>
  <div class="flex items-center gap-1">
    <LanguageSwitch />
    <Tools mode="right-side" />
    <Component :is="useSlots('toolbar-end')" />
  </div>
</template>
