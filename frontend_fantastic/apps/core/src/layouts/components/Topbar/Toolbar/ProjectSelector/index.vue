<script setup lang="ts">
import { onClickOutside } from '@vueuse/core'
import apiConductor from '@/api/modules/conductor'
import type { Project } from '@/api/modules/conductor'
import DirectoryPickerModal from '@/components/DirectoryPickerModal/index.vue'
import { useConductorProjectStore } from '@/store/modules/conductor/project'

const projectStore = useConductorProjectStore()
const projects = ref<Project[]>([])
const open = ref(false)
const wrapperRef = ref<HTMLElement | null>(null)

onClickOutside(wrapperRef, () => {
  if (!dirPickerOpen.value)
    open.value = false
})

// 'list' = browse/switch, 'new' = create form
const mode = ref<'list' | 'new'>('list')

// New project form
const newId = ref('')
const newDir = ref('')
const creating = ref(false)
const createError = ref('')
const newIdInput = ref<HTMLInputElement | null>(null)
const dirPickerOpen = ref(false)

// Delete
const confirmDeleteId = ref<string | null>(null)
const deleting = ref(false)

const visibleProjects = computed(() => {
  const map = new Map(projects.value.map(p => [p.id, p]))
  if (!map.has(projectStore.currentProject)) {
    map.set(projectStore.currentProject, { id: projectStore.currentProject, root_directory: projectStore.currentRootDirectory })
  }
  return [...map.values()]
})

onMounted(async () => {
  try {
    projects.value = await apiConductor.projects()
    projectStore.syncFromProjects(projects.value)
  }
  catch {}
})

watch(open, (v) => {
  if (!v) {
    mode.value = 'list'
    confirmDeleteId.value = null
  }
})

watch(mode, (v) => {
  if (v === 'new') {
    newId.value = ''
    newDir.value = ''
    createError.value = ''
    nextTick(() => newIdInput.value?.focus())
  }
})

function select(id: string) {
  projectStore.setProject(id)
  open.value = false
}

async function createProject() {
  const id = newId.value.trim()
  if (!id) return
  creating.value = true
  createError.value = ''
  try {
    const dir = newDir.value.trim() || null
    projectStore.setProject(id)
    await projectStore.saveRootDirectory(dir)
    const entry: Project = { id, root_directory: dir }
    if (!projects.value.find(p => p.id === id)) {
      projects.value = [entry, ...projects.value]
    }
    else {
      projects.value = projects.value.map(p => p.id === id ? entry : p)
    }
    open.value = false
  }
  catch {
    createError.value = 'Failed to create project.'
  }
  finally {
    creating.value = false
  }
}

async function deleteProject(id: string) {
  deleting.value = true
  try {
    await apiConductor.deleteProject(id)
    projects.value = projects.value.filter(p => p.id !== id)
    delete projectStore.rootDirectories[id]
    if (projectStore.currentProject === id)
      projectStore.setProject('default')
    confirmDeleteId.value = null
  }
  catch {
    faToast.error('Failed to delete project.')
  }
  finally {
    deleting.value = false
  }
}
</script>

<template>
  <div ref="wrapperRef" class="relative">
    <!-- Topbar chip -->
    <button
      class="flex h-8 items-center gap-1.5 rounded-md border bg-background px-2.5 text-sm font-medium transition-colors hover:border-primary"
      @click="open = !open"
    >
      <FaIcon name="i-lucide:folder-open" class="size-3.5 text-muted-foreground" />
      <span class="max-w-28 truncate">{{ projectStore.currentProject }}</span>
      <FaIcon
        v-if="projectStore.currentRootDirectory"
        name="i-lucide:map-pin"
        class="size-3 text-emerald-500"
      />
      <FaIcon name="i-lucide:chevron-down" class="size-3 text-muted-foreground" />
    </button>

    <!-- Dropdown -->
    <div
      v-if="open"
      class="absolute right-0 top-10 z-50 w-80 overflow-hidden rounded-xl border bg-background shadow-xl"
    >
      <!-- ── LIST MODE ── -->
      <template v-if="mode === 'list'">
        <!-- Header -->
        <div class="flex items-center justify-between border-b px-4 py-3">
          <span class="text-sm font-semibold">Projects</span>
          <button
            class="flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground transition hover:bg-primary/90"
            @click="mode = 'new'"
          >
            <FaIcon name="i-lucide:plus" class="size-3" />
            New project
          </button>
        </div>

        <!-- Project list -->
        <div class="max-h-72 overflow-y-auto py-1">
          <template v-for="p in visibleProjects" :key="p.id">
            <!-- Confirm delete -->
            <div
              v-if="confirmDeleteId === p.id"
              class="mx-2 my-1 rounded-lg border border-destructive/30 bg-destructive/5 p-3"
            >
              <p class="mb-0.5 text-sm font-medium text-destructive">
                Delete "{{ p.id }}"?
              </p>
              <p class="mb-3 text-xs text-muted-foreground">
                All work items, edges, generation history, and blueprint files will be permanently removed.
              </p>
              <div class="flex gap-2">
                <button
                  class="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-destructive py-1.5 text-xs font-medium text-destructive-foreground transition hover:bg-destructive/90 disabled:opacity-50"
                  :disabled="deleting"
                  @click="deleteProject(p.id)"
                >
                  <FaIcon v-if="deleting" name="i-lucide:loader-2" class="size-3 animate-spin" />
                  {{ deleting ? 'Deleting…' : 'Delete permanently' }}
                </button>
                <button
                  class="rounded-md border px-3 py-1.5 text-xs transition hover:bg-muted/50 disabled:opacity-50"
                  :disabled="deleting"
                  @click="confirmDeleteId = null"
                >
                  Cancel
                </button>
              </div>
            </div>

            <!-- Normal row -->
            <div
              v-else
              class="group mx-1 flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors hover:bg-muted/50"
              :class="p.id === projectStore.currentProject ? 'bg-primary/5' : ''"
              @click="select(p.id)"
            >
              <!-- Check / placeholder -->
              <div class="mt-0.5 shrink-0">
                <FaIcon
                  v-if="p.id === projectStore.currentProject"
                  name="i-lucide:check"
                  class="size-3.5 text-primary"
                />
                <span v-else class="block size-3.5" />
              </div>

              <!-- Name + dir -->
              <div class="min-w-0 flex-1">
                <div
                  class="truncate text-sm"
                  :class="p.id === projectStore.currentProject ? 'font-semibold text-primary' : 'font-medium'"
                >
                  {{ p.id }}
                </div>
                <div
                  v-if="p.root_directory"
                  class="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground"
                >
                  <FaIcon name="i-lucide:map-pin" class="size-2.5 shrink-0 text-emerald-500" />
                  <span class="truncate font-mono">{{ p.root_directory }}</span>
                </div>
                <div v-else class="mt-0.5 text-xs text-muted-foreground/50">
                  No root directory
                </div>
              </div>

              <!-- Delete -->
              <button
                class="mt-0.5 shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-100"
                title="Delete project"
                @click.stop="confirmDeleteId = p.id"
              >
                <FaIcon name="i-lucide:trash-2" class="size-3.5" />
              </button>
            </div>
          </template>

          <div v-if="visibleProjects.length === 0" class="px-4 py-6 text-center text-sm text-muted-foreground">
            No projects yet
          </div>
        </div>
      </template>

      <!-- ── NEW PROJECT MODE ── -->
      <template v-else>
        <!-- Header -->
        <div class="flex items-center justify-between border-b px-4 py-3">
          <span class="text-sm font-semibold">New Project</span>
          <button
            class="rounded p-1 text-muted-foreground transition hover:bg-muted/50 hover:text-foreground"
            @click="mode = 'list'"
          >
            <FaIcon name="i-lucide:x" class="size-4" />
          </button>
        </div>

        <!-- Form body -->
        <div class="space-y-4 p-4">
          <!-- Project ID -->
          <div>
            <label class="mb-1.5 block text-xs font-medium text-muted-foreground">Project ID</label>
            <input
              ref="newIdInput"
              v-model="newId"
              class="w-full rounded-lg border bg-muted/30 px-3 py-2 text-sm outline-none focus:border-primary focus:bg-background"
              placeholder="e.g. my-project"
              autocomplete="off"
              @keydown.enter.prevent="createProject"
              @keydown.escape.prevent="mode = 'list'"
            >
          </div>

          <!-- Root directory -->
          <div>
            <label class="mb-1.5 block text-xs font-medium text-muted-foreground">
              Root Directory
              <span class="ml-1 font-normal text-muted-foreground/60">(optional)</span>
            </label>
            <div class="flex gap-1.5">
              <input
                v-model="newDir"
                class="min-w-0 flex-1 rounded-lg border bg-muted/30 px-3 py-2 font-mono text-xs outline-none focus:border-primary focus:bg-background"
                placeholder="/path/to/project"
                @keydown.enter.prevent="createProject"
                @keydown.escape.prevent="mode = 'list'"
              >
              <button
                class="flex shrink-0 items-center justify-center rounded-lg border px-2.5 transition hover:border-primary hover:bg-muted/40"
                @click="dirPickerOpen = true"
              >
                <FaIcon name="i-lucide:folder-open" class="size-3.5" />
              </button>
            </div>
            <p class="mt-1.5 flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
              <FaIcon name="i-lucide:lock" class="size-3 shrink-0" />
              Cannot be changed after creation
            </p>
          </div>

          <!-- Error -->
          <p v-if="createError" class="text-xs text-destructive">
            {{ createError }}
          </p>
        </div>

        <!-- Footer -->
        <div class="flex gap-2 border-t p-3">
          <button
            class="flex-1 rounded-lg border py-2 text-sm transition hover:bg-muted/50"
            @click="mode = 'list'"
          >
            Cancel
          </button>
          <button
            class="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
            :disabled="!newId.trim() || creating"
            @click="createProject"
          >
            <FaIcon v-if="creating" name="i-lucide:loader-2" class="size-3.5 animate-spin" />
            <FaIcon v-else name="i-lucide:plus" class="size-3.5" />
            {{ creating ? 'Creating…' : 'Create Project' }}
          </button>
        </div>
      </template>
    </div>

    <DirectoryPickerModal
      v-model:open="dirPickerOpen"
      v-model="newDir"
    />
  </div>
</template>
