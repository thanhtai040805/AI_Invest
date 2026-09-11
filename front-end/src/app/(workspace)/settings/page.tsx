"use client"

import { useState } from "react"
import { Page } from "@/components/Shell"
import {
  Panel,
  PanelHead,
} from "@/components/ui"

export default function Settings() {
  const [active, setActive] = useState("AI Preferences")
  const sections = [
    "Profile",
    "Security",
    "Trading",
    "Risk Preferences",
    "AI Preferences",
    "Notifications",
    "Appearance",
    "Language",
    "Privacy",
    "Connected Brokers",
    "Subscription",
    "Advanced",
  ]
  return (
    <Page title="Settings" sub="Configure your workspace.">
      <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4">
        <Panel className="h-fit">
          <div className="space-y-0.5">
            {sections.map((s) => (
              <button
                key={s}
                onClick={() => setActive(s)}
                className={`w-full text-left h-8 px-2.5 rounded-[6px] text-[13px] ${active === s ? "bg-soft text-ink font-medium" : "text-secondary hover:bg-soft/60"}`}
              >
                {s}
              </button>
            ))}
          </div>
        </Panel>
        <Panel>
          <PanelHead title={active} />
          {active === "AI Preferences" ? (
            <div className="space-y-5">
              {[
                ["Signal frequency", ["Low", "Balanced", "High"]],
                ["Explanation depth", ["Concise", "Standard", "Detailed"]],
                [
                  "Risk sensitivity",
                  ["Conservative", "Moderate", "Aggressive"],
                ],
                ["Research complexity", ["Simple", "Standard", "Advanced"]],
              ].map(([l, opts]) => (
                <div key={l as string}>
                  <div className="text-[13px] text-ink mb-2">{l as string}</div>
                  <div className="flex gap-1 bg-soft rounded-[8px] p-1 w-fit">
                    {(opts as string[]).map((o, i) => (
                      <button
                        key={o}
                        className={`px-3 h-7 rounded-[6px] text-[12px] ${i === 1 ? "bg-surface text-ink shadow-sm" : "text-muted"}`}
                      >
                        {o}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <p className="text-[12px] text-muted pt-2 border-t border-line">
                AI settings tune how intelligence is surfaced. They do not
                guarantee investment outcomes.
              </p>
            </div>
          ) : (
            <p className="text-[13.5px] text-secondary">
              Manage your {active.toLowerCase()} settings here.
            </p>
          )}
        </Panel>
      </div>
    </Page>
  )
}
