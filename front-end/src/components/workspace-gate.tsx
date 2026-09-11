"use client"
import { useEffect, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
export function WorkspaceGate({ children }: { children: ReactNode }) { const { authed, loading } = useAuth(); const router = useRouter(); useEffect(() => { if (!loading && !authed) router.replace("/login") }, [authed, loading, router]); if (loading || !authed) return <div className="min-h-dvh bg-paper p-6"><div className="mx-auto h-12 max-w-6xl animate-pulse rounded-lg bg-soft" /></div>; return children }
