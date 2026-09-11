"use client"

import { Page } from "@/components/Shell"
import {
  Button,
  Panel,
  PanelHead,
  SectionEyebrow,
} from "@/components/ui"

export function Notifications() {
  const groups = {
    Market: ["VN-Index closed +0.72%"],
    Portfolio: ["HPG position up 16.1%"],
    AI: ["New accumulation signal on MBB"],
    Community: ["Nguyễn Minh Anh published a thesis"],
    Broker: ["A broker you follow posted a signal"],
  }
  return (
    <Page
      title="Notifications"
      sub="Grouped by source."
      actions={<Button variant="ghost">Mark all read</Button>}
    >
      <div className="space-y-4">
        {Object.entries(groups).map(([g, items]) => (
          <Panel key={g}>
            <SectionEyebrow>{g}</SectionEyebrow>
            {items.map((n, i) => (
              <div key={i} className="flex items-center gap-3 py-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-mineral" />
                <span className="text-[13px] text-secondary">{n}</span>
                <span className="ml-auto text-[11px] text-muted">2h</span>
              </div>
            ))}
          </Panel>
        ))}
      </div>
    </Page>
  )
}

export function Activity() {
  const events = [
    "Reviewed AI insight on HPG",
    "Saved thesis: MBB credit growth",
    "Executed buy order · FPT",
    "Followed Nguyễn Minh Anh",
    "Triggered alert: HPG volume",
    "Viewed research: Banking sector",
  ]
  return (
    <Page title="Activity" sub="Your timeline across AIInvest.">
      <Panel>
        <div className="space-y-0">
          {events.map((e, i) => (
            <div
              key={i}
              className="flex gap-4 py-3 border-b border-line last:border-0"
            >
              <span className="tnum text-[11px] text-muted w-10">{i + 1}h</span>
              <span className="w-1.5 h-1.5 rounded-full bg-mineral mt-1.5" />
              <span className="text-[13px] text-secondary">{e}</span>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  )
}

export default function Help() {
  return (
    <Page
      title="Help & Methodology"
      sub="Guides, glossary, and how AIInvest reasons."
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          ["Research methodology", "How theses are constructed from evidence."],
          [
            "Risk methodology",
            "How expected shortfall and the drawdown protocol work.",
          ],
          [
            "AI methodology",
            "How signals move from observation to interpretation.",
          ],
          ["Glossary", "Giá trần, giá sàn, khối ngoại, thanh khoản, and more."],
          ["Guides", "Getting started with the workspace."],
          ["Contact support", "Reach the AIInvest team."],
        ].map(([t, d]) => (
          <Panel key={t}>
            <div className="text-[15px] font-semibold text-ink">{t}</div>
            <p className="text-[13px] text-muted mt-1.5 leading-relaxed">{d}</p>
          </Panel>
        ))}
      </div>
      <Panel className="mt-4">
        <PanelHead
          title="Message an administrator"
          sub="Send a question about your account, data, or a trading workflow."
        />
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3">
          <input
            aria-label="Message to administrator"
            placeholder="Describe what you need help with…"
            className="h-10 rounded-[7px] border border-line bg-paper px-3 text-[13px] text-ink outline-none placeholder:text-muted focus:border-mineral"
          />
          <Button variant="primary">Send message</Button>
        </div>
      </Panel>
    </Page>
  )
}
