"use client";

import { cn } from "@/lib/utils";
import { motion } from "framer-motion";

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'primary' | 'secondary' | 'tertiary' | 'error' | 'outline' | 'success';
  className?: string;
  dot?: boolean;
}

export function Badge({ children, variant = 'primary', className, dot = false }: BadgeProps) {
  const variants = {
    primary: "bg-primary/10 text-primary border-primary/20 shadow-[0_0_8px_rgba(173,198,255,0.1)]",
    secondary: "bg-secondary/10 text-secondary border-secondary/20 shadow-[0_0_8px_rgba(52,211,153,0.1)]",
    success: "bg-secondary/10 text-secondary border-secondary/20 shadow-[0_0_8px_rgba(52,211,153,0.1)]",
    tertiary: "bg-tertiary/10 text-tertiary border-tertiary/20 shadow-[0_0_8px_rgba(255,214,140,0.1)]",
    error: "bg-error/10 text-error border-error/20 shadow-[0_0_8px_rgba(248,113,113,0.1)]",
    outline: "bg-white/5 text-on-surface-variant border-white/10"
  };

  return (
    <motion.span 
      whileHover={{ scale: 1.05 }}
      className={cn(
        "px-2.5 py-1 rounded-full text-[10px] font-bold border uppercase tracking-widest inline-flex items-center gap-1.5 transition-all duration-300",
        variants[variant === 'success' ? 'secondary' : variant],
        className
      )}
    >
      {dot && (
        <span className={cn(
          "w-1.5 h-1.5 rounded-full animate-pulse",
          variant === 'primary' ? 'bg-primary' : 
          variant === 'secondary' || variant === 'success' ? 'bg-secondary' : 
          variant === 'error' ? 'bg-error' : 'bg-on-surface-variant'
        )}></span>
      )}
      {children}
    </motion.span>
  );
}
