"use client"

import { type ReactNode } from "react"
import { useRouter } from "@/lib/router"

export function Logo({ onClick }: { onClick?: () => void }) {
  return (
    <button onClick={onClick} className="flex items-center gap-2.5">
      <span className="grid h-8 w-8 place-items-center rounded-[8px] bg-ink font-serif text-[17px] leading-none text-paper">
        A
      </span>
      <span className="text-[17px] font-semibold tracking-tight text-ink">
        AIInvest
      </span>
    </button>
  )
}

export function AuthShell({
  title,
  sub,
  children,
  footer,
}: {
  title: string
  sub: string
  children: ReactNode
  footer: ReactNode
}) {
  const { navigate } = useRouter()
  return (
    <div className="min-h-full grid grid-cols-1 bg-paper lg:grid-cols-[1fr_1.1fr]">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-ink p-10 text-paper lg:flex">
        <button
          onClick={() => navigate("/")}
          className="flex w-fit items-center gap-2.5"
        >
          <span className="grid h-8 w-8 place-items-center rounded-[8px] bg-paper font-serif text-[17px] text-ink">
            A
          </span>
          <span className="text-[17px] font-semibold">AIInvest</span>
        </button>
        <p className="max-w-sm font-serif text-[28px] leading-snug">
          See what changed, understand why, and decide with the evidence in
          front of you.
        </p>
        <span className="text-[11px] text-paper/50">
          Institutional-grade research · calm by design
        </span>
      </div>
      <div className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Logo onClick={() => navigate("/")} />
          </div>
          <h1 className="text-[26px] font-semibold tracking-tight text-ink">
            {title}
          </h1>
          <p className="mt-1.5 text-[13.5px] text-muted">{sub}</p>
          <div className="mt-7">{children}</div>
          <div className="mt-6 text-[13px] text-secondary">{footer}</div>
        </div>
      </div>
    </div>
  )
}

export function Field({
  label,
  type = "text",
  placeholder,
  value,
  onChange,
  error,
}: {
  label: string
  type?: string
  placeholder?: string
  value: string
  onChange: (v: string) => void
  error?: string
}) {
  return (
    <div>
      <label className="text-[12.5px] font-medium text-secondary">
        {label}
      </label>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={`mt-1.5 h-10 w-full rounded-[8px] border px-3 text-[14px] text-ink outline-none placeholder:text-disabled focus:border-mineral ${error ? "border-loss" : "border-line-strong"}`}
      />
      {error && <p className="mt-1 text-[11.5px] text-loss">{error}</p>}
    </div>
  )
}
