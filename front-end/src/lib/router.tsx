"use client"
import NextLink from "next/link"
import { usePathname, useRouter as useNextRouter } from "next/navigation"
import type { ReactNode } from "react"
export function RouterProvider({ children }: { children: ReactNode }) { return children }
export function useRouter() { const router = useNextRouter(); return { path: usePathname(), navigate: (to: string) => router.push(to) } }
export function Link({ to, className, children, onClick, title }: { to: string; className?: string; children: ReactNode; onClick?: () => void; title?: string }) { return <NextLink href={to} className={className} title={title} onClick={() => onClick?.()}>{children}</NextLink> }
