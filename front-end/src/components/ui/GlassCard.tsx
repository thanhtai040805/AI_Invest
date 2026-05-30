"use client";

import { cn } from "@/lib/utils";
import { MouseEvent, useCallback, useRef } from "react";

/* ─────────────────────────────────────────────
   GlassCard — spotlight border + inner refraction
   Uses CSS custom props for the spotlight position
   (avoids Framer motion value in a container that
   is not an isolated leaf — performance safe)
───────────────────────────────────────────── */

interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
  /** Amber glow on the border */
  glow?: boolean;
  /** Disable spotlight effect (e.g. for static display cards) */
  interactive?: boolean;
  as?: "div" | "article" | "section";
}

export function GlassCard({
  children,
  className,
  glow = false,
  interactive = true,
  as: Tag = "div",
}: GlassCardProps) {
  const ref = useRef<HTMLDivElement>(null);

  const handleMouseMove = useCallback((e: MouseEvent<HTMLDivElement>) => {
    if (!interactive || !ref.current) return;
    const { left, top } = ref.current.getBoundingClientRect();
    ref.current.style.setProperty("--mouse-x", `${e.clientX - left}px`);
    ref.current.style.setProperty("--mouse-y", `${e.clientY - top}px`);
  }, [interactive]);

  return (
    <div
      ref={ref}
      onMouseMove={handleMouseMove}
      className={cn(
        // Base glass
        "group relative glass-card rounded-xl overflow-hidden transition-all duration-300",
        // Spotlight border (CSS-only, no JS per-frame)
        interactive && [
          "before:pointer-events-none before:absolute before:-inset-px before:rounded-xl before:opacity-0",
          "before:transition-opacity before:duration-400 before:content-['']",
          "hover:before:opacity-100",
          "[--mouse-x:50%] [--mouse-y:50%]",
          "before:[background:radial-gradient(300px_circle_at_var(--mouse-x)_var(--mouse-y),rgba(232,169,64,0.12),transparent_55%)]",
        ],
        // Hover border lift
        interactive && "hover:border-white/[0.09] hover:shadow-[0_20px_48px_rgba(0,0,0,0.5)]",
        // Optional amber glow
        glow && "border-[#e8a940]/14 shadow-[0_0_32px_rgba(232,169,64,0.06)]",
        className
      )}
    >
      {/* Physical top-edge highlight */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, rgba(255,255,255,0.07) 40%, rgba(255,255,255,0.07) 60%, transparent)",
        }}
      />

      {/* Content */}
      <div className="relative z-10">{children}</div>
    </div>
  );
}
