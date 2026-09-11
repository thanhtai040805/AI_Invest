"use client"
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { authApi } from "./api"
import { tokenStore } from "./api/client"
type User = { id: string; email: string; displayName?: string | null; role?: string }
type Auth = { authed: boolean; loading: boolean; name: string; user: User | null; login: (email: string, password: string) => Promise<void>; register: (name: string, email: string, password: string) => Promise<void>; logout: () => Promise<void> }
const Ctx = createContext<Auth | null>(null)
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null); const [loading, setLoading] = useState(true)
  useEffect(() => { let active = true; authApi.me().then(value => { if (active) setUser(value) }).catch(() => {}).finally(() => { if (active) setLoading(false) }); return () => { active = false } }, [])
  const login = async (email: string, password: string) => { const result = await authApi.login(email, password); tokenStore.set(result.accessToken); setUser(result.user) }
  const register = async (name: string, email: string, password: string) => { const result = await authApi.register(email, password, name); tokenStore.set(result.accessToken); setUser(result.user) }
  const logout = async () => { try { await authApi.logout() } finally { tokenStore.clear(); setUser(null); window.location.assign("/") } }
  return <Ctx.Provider value={{ authed: !!user, loading, name: user?.displayName || user?.email?.split("@")[0] || "", user, login, register, logout }}>{children}</Ctx.Provider>
}
export const useAuth = () => { const value = useContext(Ctx); if (!value) throw new Error("AuthProvider is missing"); return value }
