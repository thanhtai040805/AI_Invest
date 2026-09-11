"use client"
import type { ReactNode } from "react"
import type { ApiProblem } from "@/lib/api/client"
import { Button, EmptyState, Panel } from "./ui"
export function DataState({ loading, error, empty, retry, children }: { loading: boolean; error: ApiProblem | null; empty: boolean; retry: () => void; children: ReactNode }) {
  if (loading) return <Panel><div className="space-y-3">{[1,2,3,4].map(i => <div key={i} className="h-12 animate-pulse rounded-[7px] bg-soft" />)}</div></Panel>
  if (error) return <Panel><EmptyState title="Không tải được dữ liệu" body={error.message} action={<Button onClick={retry}>Thử lại</Button>} /></Panel>
  if (empty) return <Panel><EmptyState title="Chưa có dữ liệu" body="Cơ sở dữ liệu chưa có bản ghi phù hợp. AIInvest không hiển thị dữ liệu mẫu." action={<Button onClick={retry}>Tải lại</Button>} /></Panel>
  return children
}
