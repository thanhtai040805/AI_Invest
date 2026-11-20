"use client"

import { Page } from "@/components/Shell"
import { ResearchChatWorkspace } from "@/components/research-chat/ResearchChatWorkspace"

export default function ResearchChatPage() {
  return (
    <Page
      title="Chat AI Phân Tích Doanh Nghiệp"
      meta="Pha 1 Verification Gate · SAG v2 Zero-Mental-Math & Strict PIT"
      variant="compact"
    >
      <ResearchChatWorkspace />
    </Page>
  )
}
