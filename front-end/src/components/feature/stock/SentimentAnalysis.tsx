"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { motion } from "framer-motion";

export function SentimentAnalysis() {
  return (
    <GlassCard className="bg-gradient-to-br from-white/[0.03] to-transparent border-white/5">
      <div className="flex justify-between items-center mb-lg">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Chỉ số Tâm lý AI</h3>
        <div className="px-2 py-0.5 rounded bg-secondary/10 text-secondary text-[9px] font-bold">TÍCH CỰC</div>
      </div>
      
      <div className="space-y-xl">
        {/* Sentiment Meter */}
        <div className="relative h-20 flex items-center justify-center">
           <svg className="w-32 h-16" viewBox="0 0 100 50">
              <path d="M10 50 A40 40 0 0 1 90 50" fill="none" stroke="currentColor" strokeWidth="8" className="text-white/5" />
              <motion.path 
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 0.75 }}
                transition={{ duration: 1.5 }}
                d="M10 50 A40 40 0 0 1 90 50" 
                fill="none" 
                stroke="var(--secondary)" 
                strokeWidth="8" 
                strokeLinecap="round"
                className="drop-shadow-[0_0_8px_rgba(16,185,129,0.5)]"
              />
           </svg>
           <div className="absolute bottom-0 text-center">
              <span className="text-2xl font-bold font-data-mono">75</span>
              <span className="text-[10px] opacity-40 ml-1">/100</span>
           </div>
        </div>

        {/* Breakdown */}
        <div className="grid grid-cols-2 gap-md">
           {[
             { label: 'Tin tức', val: 82, color: 'secondary' },
             { label: 'Social', val: 65, color: 'primary' },
           ].map((s, i) => (
             <div key={i} className="space-y-1">
                <div className="flex justify-between text-[9px] font-bold opacity-60 uppercase">
                   <span>{s.label}</span>
                   <span>{s.val}%</span>
                </div>
                <div className="w-full h-1 bg-white/5 rounded-full overflow-hidden">
                   <div 
                     className={`h-full bg-${s.color}`} 
                     style={{ width: `${s.val}%` }} 
                   />
                </div>
             </div>
           ))}
        </div>

        {/* AI Insight */}
        <div className="p-md rounded-xl bg-primary/5 border border-primary/10">
           <p className="text-[11px] leading-relaxed italic opacity-80">
              &quot;Dòng tiền từ khối ngoại đang có dấu hiệu xoay vòng sang nhóm ngành của cổ phiếu này, tạo lực đỡ tâm lý mạnh mẽ cho các phiên tới.&quot;
           </p>
        </div>
      </div>
    </GlassCard>
  );
}
