import axios from 'axios'

let lastNetworkErrorAt = 0

const api = axios.create({
  baseURL: (import.meta.env.DEV && import.meta.env.VITE_ENABLE_PROXY)
    ? '/proxy/'
    : import.meta.env.VITE_APP_API_BASEURL,
  timeout: 1000 * 60,
  responseType: 'json',
})

api.interceptors.response.use(
  response => Promise.resolve(response.data),
  (error) => {
    const detail = error.response?.data?.detail
    const config = error.config as any
    if (!config?.silent) {
      const isNetworkError = !error.response
      const now = Date.now()
      if (!isNetworkError || now - lastNetworkErrorAt > 30000) {
        if (isNetworkError) {
          lastNetworkErrorAt = now
        }
        faToast.error('请求失败', {
          description: typeof detail === 'string'
            ? detail
            : isNetworkError
              ? '后台服务暂时不可用，请确认服务已启动后再刷新。'
              : error.message,
        })
      }
    }
    return Promise.reject(error)
  },
)

export default api
