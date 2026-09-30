"use client"

import { useParams } from "next/navigation"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { Button, EmptyState, Panel, PanelHead, Pill } from "@/components/ui"
import { communityApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

interface ApiExpert {
  id?: string | number
  displayName?: string
  rank?: string
  postCount?: number
  reactionCount?: number
}

function countText(value: unknown): string {
  const number = value == null ? NaN : Number(value)
  return Number.isFinite(number) ? number.toLocaleString("vi-VN") : "—"
}

function BrokerProfile({ id }: { id: string }) {
  const expertsRes = useResource(() => communityApi.experts(), [])
  const experts = Array.isArray(expertsRes.data) ? expertsRes.data as ApiExpert[] : []
  const expert = experts.find((item) => String(item.id) === id)

  return (
    <Page
      title={expert?.displayName || "Hồ sơ cộng đồng"}
      sub="Thông tin thành viên được trả về từ bảng xếp hạng cộng đồng."
      actions={<Link to="/community"><Button variant="secondary">Quay lại cộng đồng</Button></Link>}
    >
      {expertsRes.loading ? (
        <Panel className="py-10 text-center text-[13px] text-muted">Đang tải hồ sơ cộng đồng…</Panel>
      ) : expertsRes.error ? (
        <Panel><p role="alert" className="text-[13px] text-loss">Không tải được dữ liệu hồ sơ: {expertsRes.error.message}</p></Panel>
      ) : !expert ? (
        <EmptyState
          title="Không tìm thấy hồ sơ"
          body="API cộng đồng hiện chỉ trả về năm thành viên đứng đầu. Hồ sơ này không nằm trong danh sách hiện có."
          action={<Link to="/community"><Button variant="primary">Mở cộng đồng</Button></Link>}
        />
      ) : (
        <Panel>
          <div className="flex flex-wrap items-center gap-3 border-b border-line pb-4">
            <div className="grid h-14 w-14 place-items-center rounded-full border border-teal/25 bg-teal/12 text-[18px] font-semibold text-teal">
              {expert.displayName?.split(/\s+/).slice(-2).map((word) => word[0]).join("").toUpperCase() || "—"}
            </div>
            <div>
              <h2 className="text-[17px] font-semibold text-ink">{expert.displayName || "Thành viên"}</h2>
              <p className="mt-0.5 text-[12px] text-secondary">Thành viên cộng đồng AIInvest</p>
            </div>
            {expert.rank && <Pill tone="neutral">{expert.rank}</Pill>}
          </div>

          <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="rounded-lg border border-line p-4">
              <div className="text-[11px] uppercase tracking-wide text-muted">Bài viết</div>
              <div className="mt-1 font-mono text-[22px] font-semibold text-ink">{countText(expert.postCount)}</div>
            </div>
            <div className="rounded-lg border border-line p-4">
              <div className="text-[11px] uppercase tracking-wide text-muted">Lượt tương tác</div>
              <div className="mt-1 font-mono text-[22px] font-semibold text-ink">{countText(expert.reactionCount)}</div>
            </div>
          </div>

          <p className="mt-4 text-[12px] leading-relaxed text-secondary">
            Hồ sơ API hiện không cung cấp danh mục đầu tư, hiệu suất đã kiểm chứng, chứng chỉ hành nghề hoặc kênh nhắn tin.
          </p>
        </Panel>
      )}
    </Page>
  )
}

export default function BrokerProfilePage() {
  const { id } = useParams<{ id: string }>()
  return <BrokerProfile id={id} />
}
