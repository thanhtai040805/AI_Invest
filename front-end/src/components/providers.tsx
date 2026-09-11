"use client"
import { useEffect, type ReactNode } from "react"
import { AuthProvider, useAuth } from "@/lib/auth"
import { getSocket } from "@/lib/socket"
export function Providers({ children }: { children: ReactNode }) { return <AuthProvider><SocketLifecycle>{children}</SocketLifecycle></AuthProvider> }
function SocketLifecycle({ children }: { children: ReactNode }) { const { authed } = useAuth(); useEffect(() => { const socket = getSocket(); if (authed) socket.connect(); else socket.disconnect(); return () => { socket.disconnect() } }, [authed]); return children }
