import { computed, ref } from 'vue'

export type Language = 'en' | 'zh'

const STORAGE_KEY = 'agentfactor-language'

const storedLanguage = localStorage.getItem(STORAGE_KEY)
const initialLanguage = storedLanguage === 'en' ? 'en' : 'zh'
const language = ref<Language>(initialLanguage)

export function setLanguage(value: Language) {
  language.value = value
  localStorage.setItem(STORAGE_KEY, value)
  document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en'
}

export function text(en: string, zh: string) {
  return language.value === 'zh' ? zh : en
}

export function useLanguage() {
  const isChinese = computed(() => language.value === 'zh')

  return {
    language,
    isChinese,
    setLanguage,
    text,
  }
}

setLanguage(language.value)
