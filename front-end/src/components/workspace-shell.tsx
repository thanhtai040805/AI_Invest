"use client"
import type { ReactNode } from "react"
import { Shell } from "./Shell"
import { WorkspaceGate } from "./workspace-gate"
export function WorkspaceShell({ children }: { children: ReactNode }) { return <WorkspaceGate><Shell>{children}</Shell></WorkspaceGate> }
