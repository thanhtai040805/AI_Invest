"use client";

import { cn } from "@/lib/utils";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { motion } from "framer-motion";

interface ChatMessageProps {
  role: 'assistant' | 'user';
  content: string;
  type?: 'text' | 'analysis' | 'suggestion';
}

export function ChatMessage({ role, content, type = 'text' }: ChatMessageProps) {
  const isAssistant = role === 'assistant';

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className={cn(
        "flex gap-lg items-start w-full",
        !isAssistant ? 'flex-row-reverse' : ''
      )}
    >
      {/* Avatar Section */}
      <div className="relative shrink-0">
        <div className={cn(
          "w-10 h-10 rounded-2xl flex items-center justify-center transition-all duration-500 shadow-2xl",
          isAssistant ? "aura-glow bg-primary text-on-primary" : "bg-white/5 border border-white/10 text-primary"
        )}>
          <span className="material-symbols-outlined text-[20px]" style={{ fontVariationSettings: isAssistant ? "'FILL' 1" : "" }}>
            {isAssistant ? 'bolt' : 'person'}
          </span>
        </div>
        {isAssistant && (
           <span className="absolute -bottom-1 -right-1 w-3 h-3 bg-secondary rounded-full border-2 border-[#050505] animate-pulse"></span>
        )}
      </div>
      
      {/* Content Section */}
      <div className={cn("max-w-[85%] lg:max-w-[70%]", !isAssistant ? "text-right" : "text-left")}>
        <div className={cn(
          "relative group",
          isAssistant ? "text-on-surface" : "text-on-primary"
        )}>
          {/* Bubble Background */}
          <div className={cn(
            "px-xl py-lg rounded-3xl shadow-2xl transition-all duration-500",
            isAssistant 
              ? "glass-card bg-surface-container-low/40 backdrop-blur-2xl border-white/5 group-hover:border-primary/20" 
              : "bg-primary border border-primary/20 shadow-primary/10 rounded-tr-none"
          )}>
            <p className="font-body-lg whitespace-pre-wrap leading-relaxed opacity-90">{content}</p>
            
            {/* Analysis Data Blocks */}
            {type === 'analysis' && isAssistant && (
              <motion.div 
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                className="grid grid-cols-1 md:grid-cols-2 gap-md mt-xl"
              >
                <div className="p-xl rounded-2xl bg-white/[0.03] border border-white/5 hover:border-primary/30 transition-all group/item">
                  <div className="flex justify-between items-start mb-lg">
                    <div className="flex flex-col">
                      <h4 className="font-data-mono text-primary text-sm font-bold tracking-wider">VNM:HOSE</h4>
                      <span className="text-[10px] text-on-surface-variant font-label-caps opacity-60">VINAMILK CORP</span>
                    </div>
                    <Badge variant="secondary" dot>MUA</Badge>
                  </div>
                  <div className="space-y-md">
                    <div className="flex justify-between items-end">
                      <span className="text-xs text-on-surface-variant font-medium">Technical Score</span>
                      <span className="text-xl font-data-mono font-bold text-secondary">82/100</span>
                    </div>
                    <div className="w-full bg-white/5 h-1.5 rounded-full overflow-hidden">
                      <motion.div 
                        initial={{ width: 0 }}
                        animate={{ width: "82%" }}
                        className="bg-secondary h-full shadow-[0_0_10px_rgba(52,211,153,0.4)]"
                      />
                    </div>
                  </div>
                </div>
                
                <div className="p-xl rounded-2xl bg-white/[0.03] border border-white/5 hover:border-tertiary/30 transition-all">
                  <div className="flex items-center gap-sm mb-lg text-tertiary">
                    <span className="material-symbols-outlined text-[20px]">insights</span>
                    <h4 className="font-label-caps text-xs font-bold tracking-[0.2em]">MARKET PULSE</h4>
                  </div>
                  <div className="grid grid-cols-2 gap-md">
                    <div>
                      <p className="text-[9px] text-on-surface-variant uppercase mb-1">Sentiment</p>
                      <p className="font-data-mono text-secondary font-bold text-sm">+12.5%</p>
                    </div>
                    <div>
                      <p className="text-[9px] text-on-surface-variant uppercase mb-1">Vol 24H</p>
                      <p className="font-data-mono text-on-surface font-bold text-sm">1.2M</p>
                    </div>
                  </div>
                </div>
              </motion.div>
            )}
          </div>
          
          {/* Timestamp or Status */}
          <span className={cn(
            "text-[9px] font-label-caps tracking-widest opacity-30 mt-2 block",
            !isAssistant ? "mr-1" : "ml-1"
          )}>
            {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
      </div>
    </motion.div>
  );
}
