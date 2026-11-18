"use client"
import type { ReactNode } from "react"
import type { ApiProblem } from "@/lib/api/client"
import { Button, EmptyState, Panel } from "./ui"
export function DataState({ loading, error, empty, retry, children }: { loading: boolean; error: ApiProblem | null; empty: boolean; retry: () => void; children: ReactNode }) {
  if (loading) {
    return (
      <div className="space-y-4">
        <div className="rounded-[10px] border border-line bg-surface p-5">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="space-y-2">
                <div className="relative h-3 w-20 overflow-hidden rounded bg-soft"><span className="skeleton-shimmer" /></div>
                <div className="relative h-6 w-28 overflow-hidden rounded bg-soft"><span className="skeleton-shimmer" /></div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-[10px] border border-line bg-surface p-5 space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="relative h-10 overflow-hidden rounded-[7px] bg-soft"><span className="skeleton-shimmer" /></div>
          ))}
        </div>
      </div>
    )
  }
  if (error) return <Panel><EmptyState title="Không tải được dữ liệu" body={error.message} action={<Button onClick={retry}>Thử lại</Button>} /></Panel>
  if (empty) return <Panel><EmptyState title="Chưa có dữ liệu" body="Cơ sở dữ liệu chưa có bản ghi phù hợp. AIInvest không hiển thị dữ liệu mẫu." action={<Button onClick={retry}>Tải lại</Button>} /></Panel>
  return children
}
