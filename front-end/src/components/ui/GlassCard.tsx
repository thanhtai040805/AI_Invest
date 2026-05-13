"use client";

import { cn } from "@/lib/utils";
import { useState, useRef, MouseEvent } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";

interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
  glow?: boolean;
  interactive?: boolean;
}

export function GlassCard({ children, className, glow = false, interactive = true }: GlassCardProps) {
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);

  function handleMouseMove({ currentTarget, clientX, clientY }: MouseEvent) {
    const { left, top } = currentTarget.getBoundingClientRect();
    mouseX.set(clientX - left);
    mouseY.set(clientY - top);
  }

  return (
    <div 
      onMouseMove={handleMouseMove}
      className={cn(
        "group relative glass-card p-lg rounded-2xl transition-all duration-500 overflow-hidden",
        interactive && "hover:translate-y-[-4px] hover:shadow-[0_20px_40px_rgba(0,0,0,0.4)]",
        glow && "border-primary/20",
        className
      )}
    >
      {/* Spotlight Effect */}
      <motion.div
        className="pointer-events-none absolute -inset-px rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300"
        style={{
          background: `radial-gradient(600px circle at ${mouseX}px ${mouseY}px, rgba(173, 198, 255, 0.08), transparent 40%)`,
        }}
      />
      
      {/* Animated Border Glow */}
      <div className="absolute inset-0 border border-white/5 group-hover:border-primary/30 transition-colors duration-500 rounded-2xl pointer-events-none" />

      <div className="relative z-10">
        {children}
      </div>
    </div>
  );
}
