<script setup lang="ts">
import apiConductor from '@/api/modules/conductor'
import type { DirectoryEntry, DirectoryListing } from '@/api/modules/conductor'
import { useLanguage } from '@/i18n'

const props = defineProps<{
  open: boolean
  modelValue?: string | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  'update:modelValue': [value: string]
}>()

const { text } = useLanguage()
const loading = ref(false)
const listing = ref<DirectoryListing | null>(null)
const selectedDir = ref<DirectoryEntry | null>(null)

const newFolderMode = ref(false)
const newFolderName = ref('')
const newFolderError = ref('')
const creating = ref(false)
const newFolderInput = ref<HTMLInputElement | null>(null)

watch(() => props.open, async (v) => {
  if (v) {
    selectedDir.value = null
    newFolderMode.value = false
    newFolderName.value = ''
    newFolderError.value = ''
    await load(props.modelValue || undefined)
  }
})

async function load(path?: string) {
  loading.value = true
  selectedDir.value = null
  newFolderMode.value = false
  newFolderName.value = ''
  newFolderError.value = ''
  try {
    listing.value = await apiConductor.directories(path)
  }
  finally {
    loading.value = false
  }
}

function clickDir(dir: DirectoryEntry) {
  selectedDir.value = dir.path === selectedDir.value?.path ? null : dir
}

function enterDir(dir: DirectoryEntry) {
  load(dir.path)
}

function confirm() {
  const path = selectedDir.value?.path ?? listing.value?.path
  if (path) {
    emit('update:modelValue', path)
    emit('update:open', false)
  }
}

function close() {
  emit('update:open', false)
}

function openNewFolder() {
  newFolderMode.value = true
  newFolderName.value = ''
  newFolderError.value = ''
  nextTick(() => newFolderInput.value?.focus())
}

function cancelNewFolder() {
  newFolderMode.value = false
  newFolderName.value = ''
  newFolderError.value = ''
}

async function confirmNewFolder() {
  const name = newFolderName.value.trim()
  if (!name) return
  if (!listing.value?.path) return
  creating.value = true
  newFolderError.value = ''
  try {
    await apiConductor.createDirectory(listing.value.path, name)
    await load(listing.value.path)
  }
  catch (err: any) {
    newFolderError.value = err?.response?.data?.detail ?? text('Failed to create directory.', '创建目录失败。')
  }
  finally {
    creating.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-150"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="transition duration-100"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="open"
        class="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-4"
        @click.self="close"
      >
        <div class="w-full max-w-lg rounded-xl border bg-background shadow-2xl">
          <!-- Header -->
          <div class="flex items-center justify-between border-b px-4 py-3">
            <div class="min-w-0">
              <div class="font-medium">{{ text('Select directory', '选择目录') }}</div>
              <div class="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                <span v-if="selectedDir" class="text-primary">{{ selectedDir.path }}</span>
                <span v-else>{{ listing?.path || '—' }}</span>
              </div>
            </div>
            <button class="ml-2 shrink-0 rounded p-1 hover:bg-muted/60" @click="close">
              <FaIcon name="i-lucide:x" class="size-4" />
            </button>
          </div>

          <!-- Shortcuts + nav + new folder -->
          <div class="flex flex-wrap items-center gap-2 border-b px-4 py-2">
            <FaButton
              v-for="sc in listing?.shortcuts ?? []"
              :key="sc.path"
              size="sm"
              variant="outline"
              @click="load(sc.path)"
            >
              {{ sc.label }}
            </FaButton>
            <FaButton
              size="sm"
              variant="outline"
              :disabled="!listing?.parent"
              @click="load(listing?.parent || undefined)"
            >
              <FaIcon name="i-lucide:corner-up-left" class="mr-1.5 size-3.5" />
              {{ text('Up', '上一级') }}
            </FaButton>
            <FaButton
              size="sm"
              variant="outline"
              :disabled="!listing?.path"
              @click="openNewFolder"
            >
              <FaIcon name="i-lucide:folder-plus" class="mr-1.5 size-3.5" />
              {{ text('New folder', '新建文件夹') }}
            </FaButton>
          </div>

          <!-- New folder input row -->
          <div v-if="newFolderMode" class="border-b px-4 py-2">
            <div class="flex items-center gap-2">
              <FaIcon name="i-lucide:folder-plus" class="shrink-0 text-amber-500 size-4" />
              <input
                ref="newFolderInput"
                v-model="newFolderName"
                class="min-w-0 flex-1 rounded-md border bg-muted/40 px-2.5 py-1.5 text-sm outline-none focus:border-primary"
                :placeholder="text('Folder name', '文件夹名称')"
                @keydown.enter.prevent="confirmNewFolder"
                @keydown.escape.prevent="cancelNewFolder"
              >
              <FaButton size="sm" :loading="creating" @click="confirmNewFolder">
                {{ text('Create', '创建') }}
              </FaButton>
              <FaButton size="sm" variant="outline" :disabled="creating" @click="cancelNewFolder">
                {{ text('Cancel', '取消') }}
              </FaButton>
            </div>
            <div v-if="newFolderError" class="mt-1.5 text-xs text-destructive">
              {{ newFolderError }}
            </div>
          </div>

          <!-- Directory list -->
          <div class="max-h-72 overflow-auto p-3">
            <div v-if="loading" class="py-10 text-center text-sm text-muted-foreground">
              {{ text('Loading…', '读取中…') }}
            </div>
            <div v-else-if="(listing?.children ?? []).length === 0" class="py-10 text-center text-sm text-muted-foreground">
              {{ text('No subdirectories.', '无子目录。') }}
            </div>
            <div v-else class="grid gap-1.5 sm:grid-cols-2">
              <button
                v-for="dir in listing?.children ?? []"
                :key="dir.path"
                class="min-w-0 rounded-md border p-2.5 text-left transition"
                :class="selectedDir?.path === dir.path
                  ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
                  : 'bg-background hover:border-primary/50 hover:bg-muted/30'"
                @click="clickDir(dir)"
                @dblclick="enterDir(dir)"
              >
                <div class="flex min-w-0 items-center gap-2">
                  <FaIcon
                    name="i-lucide:folder"
                    class="shrink-0"
                    :class="selectedDir?.path === dir.path ? 'text-primary' : 'text-amber-500'"
                  />
                  <span class="truncate text-sm font-medium">{{ dir.name }}</span>
                  <FaIcon
                    v-if="selectedDir?.path === dir.path"
                    name="i-lucide:check"
                    class="ml-auto shrink-0 size-3.5 text-primary"
                  />
                </div>
                <div class="mt-0.5 truncate font-mono text-xs text-muted-foreground">{{ dir.path }}</div>
              </button>
            </div>
          </div>

          <!-- Footer -->
          <div class="flex items-center justify-between border-t px-4 py-3">
            <div class="text-xs text-muted-foreground">
              <span v-if="selectedDir">{{ text('Selected', '已选择') }}: <span class="font-medium text-foreground">{{ selectedDir.name }}</span></span>
              <span v-else>{{ text('Click to select · Double-click to enter', '单击选择 · 双击进入') }}</span>
            </div>
            <div class="flex gap-2">
              <FaButton variant="outline" size="sm" @click="close">
                {{ text('Cancel', '取消') }}
              </FaButton>
              <FaButton size="sm" :disabled="!listing?.path" @click="confirm">
                <FaIcon name="i-lucide:check" class="mr-1.5" />
                {{ selectedDir ? text('Use selected', '使用所选') : text('Use current', '使用当前') }}
              </FaButton>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
