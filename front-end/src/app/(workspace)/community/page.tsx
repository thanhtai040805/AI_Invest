"use client"

import { useEffect, useMemo, useRef, useState } from "react"
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

interface ApiCommunityPost {
  id?: string | number
  author?: { displayName?: string }
  authorId?: string
  createdAt?: string
  content?: string
  taggedSymbols?: string[] | string
  likesCount?: number
  commentsCount?: number
  _count?: { reactions?: number; comments?: number }
}
interface ApiExpert { id?: string | number; displayName?: string; name?: string; rank?: string; postCount?: number }
interface ApiPostsResponse { posts?: ApiCommunityPost[]; nextCursor?: string | null }
interface ApiCommunitySnapshot { stocks?: { symbol?: string; change_pct?: number | null; changePercent?: number | null }[] }

function Avatar({ name, ai }: { name: string; ai?: boolean }) {
  const initials = ai ? "◈" : name.split(" ").slice(-2).map((w) => w[0]).join("")
  return <div className={`w-9 h-9 rounded-full grid place-items-center text-[12px] font-semibold shrink-0 ${ai ? "bg-mineral/12 text-mineral border border-mineral/25" : "bg-teal/12 text-teal border border-teal/25"}`}>{initials}</div>
}

function PostCard({ p, onReact, reacting }: { p: CommunityPost; onReact: (id: string) => void; reacting: boolean }) {
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
          <div className="text-[11.5px] text-secondary">{p.role || "Thành viên"} · {p.time}</div>
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
          <button type="button" onClick={() => onReact(p.id)} disabled={reacting} className="hover:text-ink disabled:opacity-50">♡ {p.likes}</button>
          <span>✎ {p.comments} bình luận</span>
          {p.signal && <Link to={`/stock/${p.signal.symbol}`}><Button variant="quiet">Xem tín hiệu</Button></Link>}
        </div>
      </div>
    </article>
  )
}

export default function Community() {
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const [draft, setDraft] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [reactingId, setReactingId] = useState("")
  const [actionMessage, setActionMessage] = useState("")
  const [olderPosts, setOlderPosts] = useState<ApiCommunityPost[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [feedError, setFeedError] = useState("")
  const resource = useResource(() => Promise.all([
    communityApi.posts().catch(() => null),
    marketApi.snapshot().catch(() => null),
    communityApi.experts().catch(() => []),
  ]), [])

  useEffect(() => {
    const postsRes = (resource.data?.[0] ?? null) as ApiPostsResponse | null
    // A refresh returns a new first page, so discard pages loaded from its previous cursor.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOlderPosts([])
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNextCursor(postsRes?.nextCursor ?? null)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFeedError("")
  }, [resource.data])

  const { postList, trendingStocks, topExperts } = useMemo(() => {
    const [postsRes, snapRes, expertsRes] = (resource.data || []) as [ApiPostsResponse | null, ApiCommunitySnapshot | null, ApiExpert[] | null]
    const rawPosts = Array.isArray(postsRes?.posts) ? postsRes.posts : []
    const rawStocks = Array.isArray(snapRes?.stocks) ? snapRes.stocks : []
    const rawExperts = Array.isArray(expertsRes) ? expertsRes : []

    const seenPostIds = new Set<string>()
    const apiPosts: CommunityPost[] = [...rawPosts, ...olderPosts]
      .filter((post) => {
        const id = String(post.id)
        if (seenPostIds.has(id)) return false
        seenPostIds.add(id)
        return true
      })
      .map((r) => ({
        id: String(r.id),
        author: String(r.author?.displayName || (r.authorId?.includes("ai") ? "AI Intelligence Bot" : "Nhà đầu tư")),
        role: r.authorId?.includes("ai") ? "AI" : "Thành viên",
        verified: false,
        ai: Boolean(r.authorId?.includes("ai")),
        time: r.createdAt ? new Date(r.createdAt).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" }) : "Chưa có thời gian",
        body: String(r.content || ""),
        cashtags: r.taggedSymbols ? (Array.isArray(r.taggedSymbols) ? r.taggedSymbols : String(r.taggedSymbols).split(",")).map((s: string) => s.trim()).filter(Boolean) : [],
        likes: Number(r.likesCount || r._count?.reactions || 0),
        comments: Number(r.commentsCount || r._count?.comments || 0),
      }))

    const trending = rawStocks.slice(0, 6).map((s) => {
      const rawChange = s.changePercent ?? s.change_pct
      const change = rawChange == null ? NaN : Number(rawChange)
      return {
        symbol: String(s.symbol),
        changePct: Number.isFinite(change) ? Number(change.toFixed(2)) : null,
      }
    })

    return {
      postList: apiPosts,
      trendingStocks: trending,
      topExperts: rawExperts,
    }
  }, [resource.data, olderPosts])

  const loadMorePosts = async () => {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    setFeedError("")
    try {
      const response = await communityApi.posts({ limit: 20, cursor: nextCursor }) as ApiPostsResponse
      setOlderPosts((previous) => [...previous, ...(response.posts ?? [])])
      setNextCursor(response.nextCursor ?? null)
    } catch (error) {
      setFeedError(error instanceof Error ? error.message : "Không thể tải thêm bài viết.")
    } finally {
      setLoadingMore(false)
    }
  }

  const submitPost = async () => {
    const content = draft.trim()
    if (!content) {
      setActionMessage("Nhập nội dung trước khi đăng bài.")
      composerRef.current?.focus()
      return
    }
    setSubmitting(true)
    setActionMessage("")
    try {
      const taggedSymbols = [...new Set(Array.from(content.matchAll(/\$([A-Z0-9.-]{1,16})\b/g), match => match[1]))]
      await communityApi.createPost({ content, taggedSymbols })
      setDraft("")
      setActionMessage("Đã đăng bài.")
      await resource.reload()
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Không thể đăng bài.")
    } finally {
      setSubmitting(false)
    }
  }

  const reactToPost = async (id: string) => {
    setReactingId(id)
    setActionMessage("")
    try {
      await communityApi.react(id)
      await resource.reload()
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Không thể cập nhật lượt thích.")
    } finally {
      setReactingId("")
    }
  }

  return (
    <Page title="Cộng đồng" sub="Chia sẻ luận điểm, dẫn chứng và góc nhìn thị trường." actions={<Button variant="primary" onClick={() => composerRef.current?.focus()}>Viết bài</Button>}>
      {actionMessage && <p role="status" className="mb-3 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-secondary">{actionMessage}</p>}
      {trendingStocks.length > 0 && (
        <div className="mb-4 bg-surface border border-line rounded-[10px] px-4 py-3 flex items-center gap-4 overflow-x-auto">
          <span className="text-[11px] uppercase tracking-wide text-muted shrink-0">Xu hướng</span>
          {trendingStocks.map((s) => (
            <Link key={s.symbol} to={`/stock/${s.symbol}`} className="flex items-center gap-1.5 shrink-0">
              <span className="font-mono text-[13px] font-medium text-ink">${s.symbol}</span>
              {s.changePct != null ? <PercentChange value={s.changePct} className="text-[11px]" arrow={false} /> : <span className="text-xs text-muted">—</span>}
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
                <textarea ref={composerRef} value={draft} onChange={event => setDraft(event.target.value)} placeholder="Chia sẻ luận điểm hoặc góc nhìn thị trường… Dùng $HPG để gắn mã." rows={3} className="w-full resize-y bg-transparent outline-none text-[14px] text-ink placeholder:text-muted" />
                <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-line">
                  <span className="text-[11px] text-secondary">Mã có dạng $HPG được tự gắn vào bài viết.</span>
                  <Button variant="primary" className="ml-auto" onClick={() => void submitPost()} disabled={submitting}>{submitting ? "Đang đăng…" : "Đăng bài"}</Button>
                </div>
              </div>
            </div>
          </Panel>
          {postList.length === 0 && !resource.loading && resource.data?.[0] == null ? (
            <Panel className="text-center py-8 text-secondary text-[13px]">
              Không tải được bài viết cộng đồng. <button type="button" className="underline" onClick={() => void resource.reload()}>Thử lại</button>
            </Panel>
          ) : postList.length === 0 ? (
            <Panel className="text-center py-8 text-secondary text-[13px]">
              {resource.loading ? "Đang tải bài viết…" : "Chưa có bài viết nào. Hãy là người đầu tiên chia sẻ phân tích!"}
            </Panel>
          ) : (
            postList.map((p) => <PostCard key={p.id} p={p} onReact={id => void reactToPost(id)} reacting={reactingId === p.id} />)
          )}
          {feedError && <p role="alert" className="text-sm text-loss">{feedError}</p>}
          {nextCursor && <Button variant="secondary" onClick={() => void loadMorePosts()} disabled={loadingMore}>{loadingMore ? "Đang tải…" : "Tải bài viết cũ hơn"}</Button>}
        </div>

        <div className="space-y-4">
          <Panel>
            <PanelHead title="Chuyên gia & Nhà phân tích" />
            <div className="space-y-3">
              {topExperts.map((b, i) => (
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
