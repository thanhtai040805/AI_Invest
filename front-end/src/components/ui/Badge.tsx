"use client";

import { cn } from "@/lib/utils";

interface BadgeProps {
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "tertiary" | "error" | "outline" | "success";
  className?: string;
  dot?: boolean;
}

export function Badge({
  children,
  variant = "primary",
  className,
  dot = false,
}: BadgeProps) {
  const variantStyles = {
    primary:
      "bg-[#e8a940]/10 text-[#e8a940] border-[#e8a940]/20",
    secondary:
      "bg-[#2dbd7e]/10 text-[#2dbd7e] border-[#2dbd7e]/20",
    success:
      "bg-[#2dbd7e]/10 text-[#2dbd7e] border-[#2dbd7e]/20",
    tertiary:
      "bg-[#7bbcee]/10 text-[#7bbcee] border-[#7bbcee]/20",
    error:
      "bg-rose-500/10 text-rose-400 border-rose-500/20",
    outline:
      "bg-white/4 text-white/50 border-white/10",
  };

  const dotColors = {
    primary: "bg-[#e8a940]",
    secondary: "bg-[#2dbd7e]",
    success: "bg-[#2dbd7e]",
    tertiary: "bg-[#7bbcee]",
    error: "bg-rose-400",
    outline: "bg-white/40",
  };

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-semibold border uppercase tracking-widest transition-all duration-200",
        variantStyles[variant],
        className
      )}
    >
      {dot && (
        <span
          className={cn("w-1.5 h-1.5 rounded-full animate-pulse shrink-0", dotColors[variant])}
        />
      )}
      {children}
    </span>
  );
}
