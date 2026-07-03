import apiConductor from '@/api/modules/conductor'

const STORE_VERSION = 2

export const useConductorProjectStore = defineStore(
  'conductorProject',
  () => {
    const _version = ref(0)
    const currentProject = ref('default')
    const rootDirectories = ref<Record<string, string | null>>({})

    const currentRootDirectory = computed(
      () => rootDirectories.value[currentProject.value] ?? null,
    )

    // Migrate / clear stale data from older store versions on first load
    if (_version.value < STORE_VERSION) {
      rootDirectories.value = {}
      _version.value = STORE_VERSION
    }

    function setProject(id: string) {
      currentProject.value = id.trim() || 'default'
    }

    function setRootDirectory(dir: string | null) {
      rootDirectories.value[currentProject.value] = dir
    }

    async function saveRootDirectory(dir: string | null) {
      setRootDirectory(dir)
      await apiConductor.upsertProject(currentProject.value, dir)
    }

    function syncFromProjects(projects: { id: string, root_directory: string | null }[]) {
      for (const p of projects) {
        // Only fill in from server if we don't already have a local value
        if (rootDirectories.value[p.id] === undefined) {
          rootDirectories.value[p.id] = p.root_directory
        }
      }
    }

    return {
      _version,
      currentProject,
      rootDirectories,
      currentRootDirectory,
      setProject,
      setRootDirectory,
      saveRootDirectory,
      syncFromProjects,
    }
  },
  { persist: true },
)
