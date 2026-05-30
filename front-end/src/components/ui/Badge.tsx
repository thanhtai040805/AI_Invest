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
      "bg-primary/10 text-primary border-primary/20 shadow-sm shadow-primary/5",
    secondary:
      "bg-secondary/10 text-secondary border-secondary/20 shadow-sm shadow-secondary/5",
    success:
      "bg-secondary/10 text-secondary border-secondary/20 shadow-sm shadow-secondary/5",
    tertiary:
      "bg-tertiary/10 text-tertiary border-tertiary/20 shadow-sm shadow-tertiary/5",
    error:
      "bg-error/10 text-error border-error/20 shadow-sm shadow-error/5",
    outline:
      "bg-white/[0.02] text-on-surface-variant border-white/10 hover:border-white/20 hover:text-on-surface",
  };

  const dotColors = {
    primary: "bg-primary shadow-[0_0_8px_rgba(232,169,64,0.5)]",
    secondary: "bg-secondary shadow-[0_0_8px_rgba(45,189,126,0.5)]",
    success: "bg-secondary shadow-[0_0_8px_rgba(45,189,126,0.5)]",
    tertiary: "bg-tertiary shadow-[0_0_8px_rgba(123,188,238,0.5)]",
    error: "bg-error shadow-[0_0_8px_rgba(248,113,113,0.5)]",
    outline: "bg-white/40",
  };

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-bold border uppercase tracking-widest transition-all duration-300 backdrop-blur-md select-none",
        variantStyles[variant],
        className
      )}
    >
      {dot && (
        <span
          className={cn(
            "w-1.5 h-1.5 rounded-full animate-pulse shrink-0",
            dotColors[variant]
          )}
        />
      )}
      <span className="font-outfit leading-none">{children}</span>
    </span>
  );
}
