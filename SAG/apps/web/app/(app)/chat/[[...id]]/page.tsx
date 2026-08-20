"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { useParams, usePathname, useRouter } from "next/navigation";

import { DEFAULT_AGENT_AVATAR } from "@/lib/branding";
import { useApp } from "@/components/features/app-shell";
import {
  useConversationRuntime,
  useConversationSession,
} from "@/components/features/chat/conversation-provider";
import { ConversationPanel } from "@/components/features/chat/conversation-panel";
import { PetHeadAvatar } from "@/components/features/pet-head-avatar";

/** Điểm vào chính của hội thoại; dữ liệu phiên dùng chung với mini Q&A, chỉ giữ vỏ workspace đầy đủ. */
export default function ChatPage() {
  const t = useTranslations("ChatPage");
  const { id } = useParams<{ id?: string | string[] }>();
  const pathname = usePathname();
  const router = useRouter();
  const { agent, appMode } = useApp();
  const runtime = useConversationRuntime();
  const routeThreadId =
    (Array.isArray(id) ? id[0] : id) ?? pathname.match(/^\/chat\/([^/]+)/)?.[1] ?? null;
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const preferredDraftRef = React.useRef<string | null>(null);
  const session = useConversationSession(sessionId);

  React.useEffect(() => {
    if (routeThreadId) {
      preferredDraftRef.current = null;
      setSessionId(runtime.forThread(routeThreadId, { activate: true }));
      return;
    }
    const index = runtime.getIndexSnapshot();
    const next =
      preferredDraftRef.current ??
      index.activeRunSessionId ??
      index.activeSessionId ??
      runtime.createDraft({ activate: true });
    preferredDraftRef.current = null;
    runtime.activate(next);
    setSessionId(next);
  }, [routeThreadId, runtime]);

  React.useEffect(() => {
    const onNewChat = () => {
      const next = runtime.createDraft({ activate: true });
      preferredDraftRef.current = next;
      setSessionId(next);
      if (window.location.pathname !== "/chat") router.push("/chat");
    };
    window.addEventListener("sag:new-chat", onNewChat);
    return () => window.removeEventListener("sag:new-chat", onNewChat);
  }, [router, runtime]);

  React.useEffect(() => {
    const threadId = session?.threadId;
    if (!threadId || routeThreadId) return;
    const nextPath = `/chat/${threadId}`;
    if (window.location.pathname === nextPath) return;
    // Không kích hoạt gỡ bỏ route, đảm bảo câu trả lời streaming sau khi tạo thread vẫn do cùng một runtime quản lý.
    window.history.replaceState(window.history.state, "", nextPath);
    window.dispatchEvent(new Event("sag:pathchange"));
  }, [routeThreadId, session?.threadId]);

  const glyph = agent?.avatar || DEFAULT_AGENT_AVATAR;
  const avatarNode = React.useMemo(
    () => <PetHeadAvatar face={glyph} size="sm" className="mt-0.5" />,
    [glyph],
  );
  const heroNode = React.useMemo(
    () => <PetHeadAvatar face={glyph} size="lg" />,
    [glyph],
  );

  if (!agent || !sessionId || !session) return null;

  return (
    <div className="h-full min-h-0">
      <ConversationPanel
        key={sessionId}
        sessionId={sessionId}
        active={appMode === "normal"}
        avatarNode={avatarNode}
        heroNode={heroNode}
        emptyTitle={agent.name}
        suggestions={[
          t("suggestionSummary"),
          t("suggestionConclusions"),
          t("suggestionTimeline"),
        ]}
        emptyHint={agent.persona?.greeting || t("emptyHint")}
        placeholder={t("placeholder", { name: agent.name })}
      />
    </div>
  );
}
