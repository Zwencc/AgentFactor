<script setup lang="ts">
import { diffTwoObj, setSettings } from '@fantastic-admin/settings'
import { useClipboard } from '@vueuse/core'
import { useLanguage } from '@/i18n'
import eventBus from '@/utils/eventBus'

defineOptions({
  name: 'AppSetting',
})

const route = useRoute()
const appSettingsStore = useAppSettingsStore()
const appMenuStore = useAppMenuStore()
const settingsDefault = setSettings({})
const { language, setLanguage, text } = useLanguage()
const { copy, copied, isSupported } = useClipboard()

const isShow = ref(false)

const themeRadius = computed<number[]>({
  get() {
    return [appSettingsStore.settings.theme.radius]
  },
  set(value) {
    appSettingsStore.settings.theme.radius = value[0]
  },
})

watch(() => appSettingsStore.settings.menu.mode, (value) => {
  if (value === 'single') {
    appMenuStore.setActived(0)
  }
  else {
    appMenuStore.setActived(route.fullPath)
  }
})

onMounted(() => {
  eventBus.on('global-app-setting-toggle', () => {
    isShow.value = !isShow.value
  })
})

function handleCopy() {
  copy(JSON.stringify(diffTwoObj(settingsDefault, appSettingsStore.settings), null, 2))
}
</script>

<template>
  <FaModal
    v-model="isShow"
    :title="text('Application Settings', '应用设置')"
    :description="text('Runtime preferences for this local dashboard.', '本地控制台运行偏好设置。')"
    :footer="isSupported"
    :destroy-on-close="false"
    class="sm:max-w-4xl"
    content-class="bg-[var(--g-main-area-bg)] transition-background-color"
  >
    <div
      :class="{
        'columns-1': appSettingsStore.mode === 'mobile',
        'columns-2': appSettingsStore.mode === 'pc',
      }"
    >
      <FaPageMain :title="text('Language', '语言')" class="m-0 mb-4 break-inside-avoid light:border-none" title-class="font-bold" main-class="space-y-4">
        <div class="setting-item">
          <div class="label">
            {{ text('Interface Language', '界面语言') }}
          </div>
          <FaButtonGroup>
            <FaButton :variant="language === 'en' ? 'default' : 'outline'" size="sm" @click="setLanguage('en')">
              English
            </FaButton>
            <FaButton :variant="language === 'zh' ? 'default' : 'outline'" size="sm" @click="setLanguage('zh')">
              简体中文
            </FaButton>
          </FaButtonGroup>
        </div>
      </FaPageMain>

      <FaPageMain :title="text('Theme', '主题')" class="m-0 mb-4 break-inside-avoid light:border-none" title-class="font-bold" main-class="space-y-4">
        <div class="setting-item">
          <div class="label">
            {{ text('Color Scheme', '颜色方案') }}
          </div>
          <FaButtonGroup>
            <FaButton
              v-for="item in [
                { icon: 'i-ri:sun-line', value: 'light' },
                { icon: 'i-ri:moon-line', value: 'dark' },
                { icon: 'i-codicon:color-mode', value: '' },
              ]"
              :key="item.value || 'auto'"
              :variant="appSettingsStore.settings.theme.colorScheme === item.value ? 'default' : 'outline'"
              size="sm"
              @click="appSettingsStore.settings.theme.colorScheme = (item.value as any)"
            >
              <FaIcon :name="item.icon" />
            </FaButton>
          </FaButtonGroup>
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Radius', '圆角') }}
          </div>
          <FaSlider v-model="themeRadius" :min="0" :max="1" :step="0.25" class="w-1/2" />
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Color Amblyopia', '色弱模式') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.theme.colorAmblyopia" />
        </div>
      </FaPageMain>

      <FaPageMain v-if="appSettingsStore.mode === 'pc'" :title="text('Navigation', '导航菜单')" class="m-0 mb-4 break-inside-avoid light:border-none" title-class="font-bold" main-class="space-y-4">
        <div class="setting-item">
          <div class="label">
            {{ text('Menu Mode', '菜单模式') }}
          </div>
          <FaButtonGroup>
            <FaButton
              v-for="item in [
                { label: text('Side', '侧边栏'), value: 'side' },
                { label: text('Head', '顶部'), value: 'head' },
                { label: text('Single', '单栏'), value: 'single' },
              ]"
              :key="item.value"
              :variant="appSettingsStore.settings.menu.mode === item.value ? 'default' : 'outline'"
              size="sm"
              @click="appSettingsStore.settings.menu.mode = (item.value as any)"
            >
              {{ item.label }}
            </FaButton>
          </FaButtonGroup>
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Collapse Sidebar', '收起侧栏') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.menu.subMenuCollapse" />
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Sidebar Collapse Button', '侧栏收起按钮') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.menu.subMenuCollapseButton" />
        </div>
      </FaPageMain>

      <FaPageMain :title="text('Topbar', '顶部栏')" class="m-0 mb-4 break-inside-avoid light:border-none" title-class="font-bold" main-class="space-y-4">
        <div class="setting-item">
          <div class="label">
            {{ text('Tabbar', '标签栏') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.topbar.tabbar" />
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Toolbar', '工具栏') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.topbar.toolbar" />
        </div>
      </FaPageMain>

      <FaPageMain :title="text('Application', '应用')" class="m-0 mb-4 break-inside-avoid light:border-none" title-class="font-bold" main-class="space-y-4">
        <div class="setting-item">
          <div class="label">
            {{ text('Dynamic Title', '动态标题') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.app.dynamicTitle" />
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Mobile Access', '移动端访问') }}
          </div>
          <FaSwitch v-model="appSettingsStore.settings.app.mobile" />
        </div>
        <div class="setting-item">
          <div class="label">
            {{ text('Home Title', '主页标题') }}
          </div>
          <FaInput v-model="appSettingsStore.settings.app.home.title" />
        </div>
      </FaPageMain>
    </div>

    <template #footer>
      <div class="w-full">
        <div class="mb-2 rounded-lg bg-rose/20 px-4 py-2 text-center text-sm/6 c-rose">
          {{ text('Settings here are runtime-only. Copy the diff if you want to persist it in src/settings.ts.', '这里的设置仅在运行时生效。如需持久化，请复制配置差异并写入 src/settings.ts。') }}
        </div>
        <FaButton class="w-full" @click="handleCopy">
          <FaIcon :name="copied ? 'i-tabler:clipboard-check' : 'i-tabler:clipboard'" class="size-5" />
          {{ text('Copy Settings', '复制配置') }}
        </FaButton>
      </div>
    </template>
  </FaModal>
</template>

<style scoped>
.setting-item {
  --uno: flex items-center justify-between gap-4;

  .label {
    --uno: flex items-center flex-shrink-0 gap-2 text-sm;
  }
}
</style>
