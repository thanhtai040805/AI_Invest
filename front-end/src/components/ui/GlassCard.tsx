"use client";

import { cn } from "@/lib/utils";
import { MouseEvent, useCallback } from "react";
import { motion, useMotionValue } from "framer-motion";

interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
  glow?: boolean;
  interactive?: boolean;
}

export function GlassCard({
  children,
  className,
  glow = false,
  interactive = true,
}: GlassCardProps) {
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);

  const handleMouseMove = useCallback(
    ({ currentTarget, clientX, clientY }: MouseEvent) => {
      const { left, top } = currentTarget.getBoundingClientRect();
      mouseX.set(clientX - left);
      mouseY.set(clientY - top);
    },
    [mouseX, mouseY]
  );

  return (
    <div
      onMouseMove={handleMouseMove}
      className={cn(
        "group relative glass-card rounded-xl overflow-hidden transition-all duration-300",
        interactive && "hover:border-white/10 hover:shadow-[0_24px_48px_rgba(0,0,0,0.45)]",
        glow && "border-[#e8a940]/12 shadow-[0_0_32px_rgba(232,169,64,0.05)]",
        className
      )}
    >
      {/* Spotlight refraction on hover */}
      <motion.div
        className="pointer-events-none absolute -inset-px rounded-xl opacity-0 group-hover:opacity-100 transition-opacity duration-500"
        style={{
          background: `radial-gradient(350px circle at ${mouseX}px ${mouseY}px, rgba(232,169,64,0.06), transparent 55%)`,
        }}
      />

      {/* Top-edge highlight – simulates physical light */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/7 to-transparent pointer-events-none" />

      <div className="relative z-10">{children}</div>
    </div>
  );
}
