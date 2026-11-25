"use client"

import { Fragment, useMemo, type ReactNode } from "react"
import {
  Brain,
  Briefcase,
  Calculator,
  CheckCircle2,
  FileText,
  History,
  Network,
  ShieldAlert,
  Table2,
} from "lucide-react"
import type { LinkedSessionItem } from "@/lib/api/researchChat"
import {
  parseMarkdownBlocks,
  type AuditBadgeToken,
  type InlineToken,
  type MarkdownBlock,
} from "./markdownParser"

function AuditBadgeButton({
  badge,
  linkedSessions,
  onClick,
  onSelectLinkedSession,
}: {
  badge: AuditBadgeToken
  linkedSessions?: LinkedSessionItem[]
  onClick?: (badge: AuditBadgeToken) => void
  onSelectLinkedSession?: (sessionId: string) => void
}) {
  if (badge.kind === "unverified") {
    return (
      <span
        className="mx-0.5 inline-flex items-center gap-1 rounded-[5px] border border-loss/35 bg-loss/10 px-1.5 py-0.5 font-mono text-[11px] font-medium text-loss align-baseline"
        title="Trích dẫn chưa được kiểm chứng trên văn bản gốc"
      >
        <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span>{badge.label}</span>
      </span>
    )
  }

  const isUnverifiedMemoryBadge =
    (badge.kind === "memory" || badge.kind === "portfolio" || badge.kind === "session") &&
    badge.verified === false

  const toneMap: Record<AuditBadgeToken["kind"], string> = {
    citation:
      badge.verified === false
        ? "border-loss/35 bg-loss/10 text-loss hover:bg-loss/15"
        : "border-teal/30 bg-teal/10 text-teal hover:bg-teal/18",
    table: "border-mineral/30 bg-mineral/10 text-mineral hover:bg-mineral/18",
    calculator: "border-gold/35 bg-gold/12 text-warning hover:bg-gold/20",
    kg:
      badge.tier === "CONFLICT_FLAG"
        ? "border-warning/40 bg-warning/12 text-warning hover:bg-warning/20"
        : "border-mineral/30 bg-mineral/10 text-mineral hover:bg-mineral/18",
    memory: isUnverifiedMemoryBadge
      ? "border-warning/45 bg-warning/14 text-warning hover:bg-warning/20"
      : "border-ceiling/30 bg-ceiling/8 text-ceiling hover:bg-ceiling/14",
    portfolio: isUnverifiedMemoryBadge
      ? "border-warning/45 bg-warning/14 text-warning hover:bg-warning/20"
      : "border-gain/35 bg-gain/10 text-gain hover:bg-gain/16",
    session: isUnverifiedMemoryBadge
      ? "border-warning/45 bg-warning/14 text-warning hover:bg-warning/20"
      : "border-floor/35 bg-floor/10 text-floor hover:bg-floor/16",
    unverified: "border-loss/35 bg-loss/10 text-loss",
  }

  const Icon =
    badge.kind === "citation"
      ? badge.verified === false
        ? ShieldAlert
        : FileText
      : badge.kind === "table"
        ? Table2
        : badge.kind === "calculator"
          ? Calculator
          : badge.kind === "memory"
            ? isUnverifiedMemoryBadge
              ? ShieldAlert
              : Brain
            : badge.kind === "portfolio"
              ? isUnverifiedMemoryBadge
                ? ShieldAlert
                : Briefcase
              : badge.kind === "session"
                ? isUnverifiedMemoryBadge
                  ? ShieldAlert
                  : History
                : Network

  const matchedLinkedSession =
    badge.kind === "session" && badge.verified !== false && badge.refId && Array.isArray(linkedSessions)
      ? linkedSessions.find(
          (item) => item.badge_id?.toUpperCase() === badge.refId?.toUpperCase(),
        )
      : undefined

  const title =
    badge.kind === "portfolio"
      ? isUnverifiedMemoryBadge
        ? "Trạng thái danh mục chưa được xác thực trong lượt này"
        : "Danh mục trực tiếp từ AIInvest Core DB"
      : badge.kind === "memory"
        ? isUnverifiedMemoryBadge
          ? "Ký ức chưa được xác thực trong lượt này — nhấn để kiểm tra ở tab Ký ức"
          : "Nhấn để mở tab Ký ức và xem chi tiết ký ức này"
        : badge.kind === "session"
          ? isUnverifiedMemoryBadge
            ? "Phiên liên kết chưa được xác thực trong lượt này"
            : matchedLinkedSession?.session_id
              ? `Nhấn để chuyển tới phiên liên kết: ${matchedLinkedSession.session_title || matchedLinkedSession.session_id.slice(0, 8)}`
              : "Nhấn để xem phiên liên kết ở tab Ký ức"
          : "Nhấn để mở và tô sáng bằng chứng tương ứng ở cột Inspector bên phải"

  const handlePress = () => {
    if (
      badge.kind === "session" &&
      badge.verified !== false &&
      matchedLinkedSession?.session_id &&
      onSelectLinkedSession
    ) {
      onSelectLinkedSession(matchedLinkedSession.session_id)
      return
    }
    onClick?.(badge)
  }

  return (
    <button
      type="button"
      onClick={handlePress}
      title={title}
      aria-label={`${badge.label} — ${title}`}
      className={`mx-0.5 inline-flex items-center gap-1 rounded-[5px] border px-1.5 py-[1px] font-mono text-[11px] font-medium align-baseline transition-colors duration-[120ms] cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-mineral ${toneMap[badge.kind]}`}
    >
      <Icon className="h-2.5 w-2.5 shrink-0" aria-hidden="true" />
      <span>{badge.label}</span>
      {badge.kind === "citation" && badge.verified !== false && !badge.label.includes("✓") && (
        <CheckCircle2 className="h-2.5 w-2.5 shrink-0" aria-hidden="true" />
      )}
    </button>
  )
}

interface RenderBadgeCallbacks {
  linkedSessions?: LinkedSessionItem[]
  onBadgeClick?: (badge: AuditBadgeToken) => void
  onSelectLinkedSession?: (sessionId: string) => void
}

function renderInline(
  tokens: InlineToken[],
  cb: RenderBadgeCallbacks,
  keyPrefix = "in",
): ReactNode[] {
  return tokens.map((tok, idx) => {
    const key = `${keyPrefix}-${idx}`
    switch (tok.type) {
      case "text":
        return <Fragment key={key}>{tok.text}</Fragment>
      case "bold":
        return (
          <strong key={key} className="font-semibold text-ink">
            {renderInline(tok.children, cb, `${key}-b`)}
          </strong>
        )
      case "italic":
        return (
          <em key={key} className="italic text-ink-2">
            {renderInline(tok.children, cb, `${key}-i`)}
          </em>
        )
      case "code":
        return (
          <code
            key={key}
            className="rounded bg-soft px-1.5 py-0.5 font-mono text-[11.5px] text-ink border border-line/70"
          >
            {tok.text}
          </code>
        )
      case "link":
        return (
          <a
            key={key}
            href={tok.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-mineral underline decoration-mineral/40 underline-offset-2 hover:text-ink"
          >
            {tok.label}
          </a>
        )
      case "badge":
        return (
          <AuditBadgeButton
            key={key}
            badge={tok}
            linkedSessions={cb.linkedSessions}
            onClick={cb.onBadgeClick}
            onSelectLinkedSession={cb.onSelectLinkedSession}
          />
        )
    }
  })
}

function renderBlock(
  block: MarkdownBlock,
  idx: number,
  cb: RenderBadgeCallbacks,
): ReactNode {
  const key = `blk-${idx}`
  switch (block.type) {
    case "hr":
      return <hr key={key} className="my-3 border-line" />
    case "heading": {
      const cls =
        block.level === 1
          ? "text-[16px] font-semibold text-ink mt-4 mb-2 tracking-tight"
          : block.level === 2
            ? "text-[14.5px] font-semibold text-ink mt-3.5 mb-1.5 tracking-tight"
            : "text-[13.5px] font-semibold text-ink mt-3 mb-1"
      return (
        <div key={key} className={cls}>
          {renderInline(block.tokens, cb, `${key}-h`)}
        </div>
      )
    }
    case "paragraph":
      return (
        <p key={key} className="text-[13.5px] leading-[1.65] text-ink/90 my-2 first:mt-0 last:mb-0">
          {renderInline(block.tokens, cb, `${key}-p`)}
        </p>
      )
    case "blockquote":
      return (
        <blockquote
          key={key}
          className="my-2.5 border-l-2 border-mineral/50 bg-soft/60 pl-3 pr-2.5 py-2 rounded-r-[6px] text-[13px] text-secondary"
        >
          {block.blocks.map((inner, bIdx) => renderBlock(inner, bIdx, cb))}
        </blockquote>
      )
    case "code":
      return (
        <pre
          key={key}
          className="my-2.5 overflow-x-auto rounded-[8px] border border-line bg-paper p-3 font-mono text-[12px] leading-relaxed text-ink"
        >
          <code>{block.content}</code>
        </pre>
      )
    case "list": {
      const Tag = block.ordered ? "ol" : "ul"
      return (
        <Tag
          key={key}
          className={`my-2 space-y-1.5 pl-5 text-[13.5px] leading-[1.6] text-ink/90 ${
            block.ordered ? "list-decimal" : "list-disc"
          }`}
        >
          {block.items.map((itemTokens, itemIdx) => (
            <li key={`${key}-li-${itemIdx}`} className="pl-0.5">
              {renderInline(itemTokens, cb, `${key}-li-${itemIdx}`)}
            </li>
          ))}
        </Tag>
      )
    }
    case "table":
      return (
        <div key={key} className="my-3 overflow-x-auto rounded-[8px] border border-line bg-surface">
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="border-b border-line bg-soft/75">
                {block.headers.map((cell, cIdx) => (
                  <th
                    key={`${key}-th-${cIdx}`}
                    className="px-3 py-2 text-left font-semibold text-ink whitespace-nowrap"
                  >
                    {renderInline(cell, cb, `${key}-th-${cIdx}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {block.rows.map((row, rIdx) => (
                <tr key={`${key}-tr-${rIdx}`} className="hover:bg-soft/35">
                  {row.map((cell, cIdx) => (
                    <td key={`${key}-td-${rIdx}-${cIdx}`} className="px-3 py-1.5 text-secondary align-top">
                      {renderInline(cell, cb, `${key}-td-${rIdx}-${cIdx}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
  }
}

export function MarkdownCitationView({
  content,
  linkedSessions,
  onBadgeClick,
  onSelectLinkedSession,
}: {
  content: string
  linkedSessions?: LinkedSessionItem[]
  onBadgeClick?: (badge: AuditBadgeToken) => void
  onSelectLinkedSession?: (sessionId: string) => void
}) {
  const blocks = useMemo(() => parseMarkdownBlocks(content || ""), [content])
  const cb = useMemo(
    () => ({ linkedSessions, onBadgeClick, onSelectLinkedSession }),
    [linkedSessions, onBadgeClick, onSelectLinkedSession],
  )

  if (!content.trim()) {
    return null
  }

  return <div className="min-w-0 break-words">{blocks.map((b, i) => renderBlock(b, i, cb))}</div>
}
