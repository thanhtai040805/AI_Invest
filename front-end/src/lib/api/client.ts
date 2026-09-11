import axios, { AxiosError, InternalAxiosRequestConfig } from "axios"
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001/api/v1"
const TOKEN_KEY = "aiinvest_access_token"
export interface ApiProblem { code: string; message: string; details?: unknown; status: number }
export const tokenStore = { get: () => typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY), set: (token: string) => localStorage.setItem(TOKEN_KEY, token), clear: () => localStorage.removeItem(TOKEN_KEY) }
export const apiClient = axios.create({ baseURL: API_BASE, timeout: 30_000, withCredentials: true })
const refreshClient = axios.create({ baseURL: API_BASE, timeout: 15_000, withCredentials: true })
let refreshing: Promise<string> | null = null
apiClient.interceptors.request.use(request => { const token = tokenStore.get(); if (token) request.headers.Authorization = `Bearer ${token}`; return request })
apiClient.interceptors.response.use(response => response, async (error: AxiosError) => {
  const request = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined
  if (error.response?.status === 401 && request && !request._retried && !request.url?.includes("/auth/")) {
    request._retried = true
    refreshing ??= refreshClient.post("/auth/refresh").then(({ data }) => { tokenStore.set(data.accessToken); return data.accessToken }).finally(() => { refreshing = null })
    try { request.headers.Authorization = `Bearer ${await refreshing}`; return apiClient(request) } catch { tokenStore.clear() }
  }
  const body = error.response?.data as Record<string, unknown> | undefined
  return Promise.reject({ code: String(body?.code || `HTTP_${error.response?.status || 0}`), message: String(body?.message || body?.error || "Không thể kết nối dịch vụ."), details: body?.details, status: error.response?.status || 0 } satisfies ApiProblem)
})
