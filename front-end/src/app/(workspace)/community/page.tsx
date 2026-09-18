"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { Button, Panel, PanelHead, Pill, PercentChange, SectionEyebrow, fmt } from "@/components/ui"
import { communityApi, marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

interface CommunityPost {
  id: string
  author: string
  firm?: string
  role?: string
  verified?: boolean
  ai?: boolean
  time: string
  body: string
  cashtags: string[]
  likes: number
  comments: number
  evidence?: string[]
  signal?: { symbol: string; entry: number; target: number; stop: number }
}

function Avatar({ name, ai }: { name: string; ai?: boolean }) {
  const initials = ai ? "◈" : name.split(" ").slice(-2).map((w) => w[0]).join("")
  return <div className={`w-9 h-9 rounded-full grid place-items-center text-[12px] font-semibold shrink-0 ${ai ? "bg-mineral/12 text-mineral border border-mineral/25" : "bg-teal/12 text-teal border border-teal/25"}`}>{initials}</div>
}

function PostCard({ p }: { p: CommunityPost }) {
  return (
    <article className="bg-surface border border-line rounded-[10px] p-5">
      <div className="flex items-center gap-3 mb-3">
        <Avatar name={p.author} ai={p.ai} />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[13.5px] font-semibold text-ink">{p.author}</span>
            {p.verified && <span className="text-mineral text-[12px]" title="Đã xác thực">✓</span>}
            {p.ai && <Pill tone="mineral">Trí tuệ nhân tạo</Pill>}
          </div>
          <div className="text-[11.5px] text-muted">{p.firm ? `${p.role} · ${p.firm}` : p.role} · {p.time}</div>
        </div>
      </div>
      <p className={`text-[14px] leading-relaxed ${p.ai ? "text-ink" : "text-secondary"}`}>{p.body}</p>

      {p.evidence && (
        <div className="mt-3 border border-line rounded-[8px] p-3 bg-paper">
          <SectionEyebrow>Dẫn chứng</SectionEyebrow>
          <ul className="space-y-1 text-[12.5px] text-secondary">{p.evidence.map((e, i) => <li key={i}>· {e}</li>)}</ul>
        </div>
      )}
      {p.signal && (
        <div className="mt-3 grid grid-cols-3 divide-x divide-line border border-line rounded-[8px]">
          {[["Vùng mua", p.signal.entry, "text-ink"], ["Mục tiêu", p.signal.target, "text-gain"], ["Chặn lỗ", p.signal.stop, "text-loss"]].map(([l, v, c]) => (
            <div key={l as string} className="px-4 py-2.5 text-center">
              <div className="text-[10px] uppercase tracking-wide text-muted">{l as string}</div>
              <div className={`tnum font-mono text-[14px] mt-0.5 ${c}`}>{fmt(v as number)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 mt-3.5 pt-3.5 border-t border-line">
        <div className="flex gap-1.5">{p.cashtags.map((t) => <Link key={t} to={`/stock/${t}`} className="text-[12px] font-mono text-mineral hover:underline">${t}</Link>)}</div>
        <div className="ml-auto flex items-center gap-4 text-[12px] text-muted">
          <button className="hover:text-secondary">♡ {p.likes}</button>
          <button className="hover:text-secondary">✎ {p.comments}</button>
          {p.signal && <Link to={`/stock/${p.signal.symbol}`}><Button variant="quiet">Xem tín hiệu</Button></Link>}
        </div>
      </div>
    </article>
  )
}

export default function Community() {
  const resource = useResource(() => Promise.all([
    communityApi.posts().catch(() => null),
    marketApi.snapshot().catch(() => null),
    communityApi.experts().catch(() => []),
  ]), [])

  const { postList, trendingStocks, topExperts } = useMemo(() => {
    const [postsRes, snapRes, expertsRes] = (resource.data || []) as [any, any, any]
    const rawPosts = Array.isArray(postsRes?.posts) ? postsRes.posts : []
    const rawStocks = Array.isArray(snapRes?.stocks) ? snapRes.stocks : []
    const rawExperts = Array.isArray(expertsRes) ? expertsRes : []

    const apiPosts: CommunityPost[] = rawPosts.map((r: any) => ({
      id: String(r.id),
      author: String(r.author?.displayName || (r.authorId?.includes("ai") ? "AI Intelligence Bot" : "Nhà đầu tư")),
      firm: "AIInvest Network",
      role: r.authorId?.includes("ai") ? "AI Intelligence" : "Analyst",
      verified: true,
      ai: Boolean(r.authorId?.includes("ai")),
      time: String(r.createdAt || "").slice(11, 16) || "08:45",
      body: String(r.content || ""),
      cashtags: r.taggedSymbols ? (Array.isArray(r.taggedSymbols) ? r.taggedSymbols : String(r.taggedSymbols).split(",")).map((s: string) => s.trim()) : ["VNINDEX"],
      likes: Number(r.likesCount || r._count?.reactions || 0),
      comments: Number(r.commentsCount || r._count?.comments || 0),
    }))

    const trending = rawStocks.slice(0, 6).map((s: any) => ({
      symbol: String(s.symbol),
      changePct: Number(Number(s.change_pct ?? 0).toFixed(2)),
    }))

    return {
      postList: apiPosts,
      trendingStocks: trending,
      topExperts: rawExperts,
    }
  }, [resource.data])

  return (
    <Page title="Cộng đồng" sub="Mạng lưới nhà đầu tư chuyên nghiệp — luận điểm, dẫn chứng và tín hiệu chuẩn mực." actions={<Button variant="primary">Đăng bài</Button>}>
      {trendingStocks.length > 0 && (
        <div className="mb-4 bg-surface border border-line rounded-[10px] px-4 py-3 flex items-center gap-4 overflow-x-auto">
          <span className="text-[11px] uppercase tracking-wide text-muted shrink-0">Xu hướng</span>
          {trendingStocks.map((s) => (
            <Link key={s.symbol} to={`/stock/${s.symbol}`} className="flex items-center gap-1.5 shrink-0">
              <span className="font-mono text-[13px] font-medium text-ink">${s.symbol}</span>
              <PercentChange value={s.changePct} className="text-[11px]" arrow={false} />
            </Link>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
        <div className="space-y-4">
          <Panel>
            <div className="flex gap-3">
              <div className="w-9 h-9 rounded-full bg-teal/15 text-teal grid place-items-center text-[12px] font-semibold border border-teal/25 shrink-0">Tôi</div>
              <div className="flex-1">
                <textarea placeholder="Nhận định của bạn về thị trường hôm nay?" rows={2} className="w-full resize-none bg-transparent outline-none text-[14px] text-ink placeholder:text-muted" />
                <div className="flex items-center gap-2 mt-2 pt-2 border-t border-line">
                  <button className="text-[12px] text-muted hover:text-secondary">Biểu đồ</button>
                  <button className="text-[12px] text-muted hover:text-secondary">Gắn mã CP</button>
                  <button className="text-[12px] text-muted hover:text-secondary">Mẫu luận điểm</button>
                  <Button variant="secondary" className="ml-auto">AI Trợ lý viết</Button>
                  <Button variant="primary">Đăng bài</Button>
                </div>
              </div>
            </div>
          </Panel>
          {postList.length === 0 ? (
            <Panel className="text-center py-8 text-secondary text-[13px]">
              Chưa có bài viết nào. Hãy là người đầu tiên chia sẻ phân tích!
            </Panel>
          ) : (
            postList.map((p) => <PostCard key={p.id} p={p} />)
          )}
        </div>

        <div className="space-y-4">
          <Panel>
            <PanelHead title="Chuyên gia & Nhà phân tích" />
            <div className="space-y-3">
              {topExperts.map((b: any, i: number) => (
                <div key={b.id || i} className="flex items-center gap-2.5">
                  <span className="text-[11px] font-mono text-muted w-4">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-medium text-ink truncate">{b.displayName || b.name}</div>
                    <div className="text-[11px] text-muted">Hạng: {b.rank || "Thành viên"}</div>
                  </div>
                  <span className="tnum font-mono text-[12px] text-gain">{b.postCount ?? 0} bài</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </Page>
  )
}
