"use client";

import { motion } from 'framer-motion';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { AutoPilotStats, RiskScore, BacktestWidget, BacktestHistory } from '@/components/feature/stock/AutoPilotModules';

export default function Page() {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="pb-xl space-y-lg px-xl pt-lg"
    >
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-md border-b border-white/5 pb-lg">
        <div className="flex items-center gap-md">
          <div className="w-10 h-10 rounded-xl bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] border border-[#e8a940]/20">
            <span className="material-symbols-outlined text-[20px]">smart_toy</span>
          </div>
          <div>
            <h1 className="text-2xl font-black text-[#e8a940] tracking-tighter uppercase leading-none">Auto-Pilot Trading</h1>
            <p className="text-xs text-on-surface-variant mt-1">Hệ thống giao dịch tự động vận hành bởi mạng thần kinh nhân tạo Aura AI.</p>
          </div>
        </div>
        <div className="flex items-center gap-md">
          <Badge variant="secondary" dot className="px-3 py-1 text-[10px] font-bold uppercase tracking-wider">AURA ACTIVE</Badge>
        </div>
      </div>

      <section className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
        <div className="lg:col-span-8 flex flex-col gap-lg">
          <GlassCard className="relative overflow-hidden flex flex-col justify-between min-h-[280px] border-white/5">
            <div className="absolute -top-24 -right-24 w-64 h-64 bg-[#e8a940]/10 blur-[100px] rounded-full" />
            <div className="relative z-10 p-2">
              <div className="flex items-center gap-sm mb-lg">
                <span className="material-symbols-outlined text-[#e8a940] text-sm">shield_with_heart</span>
                <h2 className="font-label-caps text-[10px] text-on-surface-variant tracking-[0.2em] opacity-45 uppercase font-bold">TỔNG QUAN TÀI SẢN ỦY THÁC</h2>
              </div>
              <div className="flex flex-col md:flex-row md:items-end gap-xl">
                <div>
                  <p className="font-label-caps text-[9px] text-on-surface-variant opacity-45 mb-2 uppercase font-bold">TỔNG TÀI SẢN ĐANG QUẢN LÝ</p>
                  <p className="text-4xl font-black font-data-mono text-on-surface tracking-tight leading-none">
                    2.485.000.000 <span className="text-lg opacity-40 font-bold">VND</span>
                  </p>
                </div>
                <div className="pb-1">
                  <div className="flex items-center gap-1 text-[#2dbd7e] font-black text-sm font-data-mono">
                    <span className="material-symbols-outlined text-[16px]">trending_up</span>
                    <span>+14.2%</span>
                  </div>
                  <p className="text-[9px] text-on-surface-variant opacity-45 uppercase font-bold tracking-wider font-data-mono mt-1">Lợi nhuận ròng (30D)</p>
                </div>
              </div>
            </div>
            
            <div className="mt-xl pt-xl border-t border-white/5">
              <AutoPilotStats />
            </div>
          </GlassCard>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-lg">
            <GlassCard className="flex flex-col gap-lg border-white/5">
              <div className="flex justify-between items-center pb-2 border-b border-white/5">
                <div className="flex items-center gap-xs">
                  <span className="material-symbols-outlined text-[#e8a940] text-sm">psychology</span>
                  <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Tâm lý & Hành vi (Insights)</h3>
                </div>
                <span className="material-symbols-outlined text-[#e8a940] text-sm">lightbulb</span>
              </div>
              <p className="text-xs text-on-surface-variant leading-relaxed italic opacity-85">
                &ldquo;Chỉ số tâm lý thị trường từ các nguồn tin tài chính CafeF & Vietstock cho thấy dòng tiền thông minh đang dịch chuyển tích lũy mạnh vào nhóm cổ phiếu Ngân hàng và Bất động sản KCN.&rdquo;
              </p>
              <div className="space-y-sm mt-auto">
                <div className="h-2 w-full bg-white/5 rounded-full overflow-hidden">
                  <motion.div initial={{ width: 0 }} animate={{ width: '92%' }} className="h-full bg-[#e8a940] shadow-[0_0_10px_rgba(232,169,64,0.3)]" />
                </div>
                <div className="flex justify-between font-data-mono text-[9px] opacity-45 uppercase font-bold">
                  <span>Mức độ tự tin AI</span>
                  <span>92%</span>
                </div>
              </div>
            </GlassCard>

            <GlassCard className="flex flex-col gap-lg border-white/5">
              <div className="flex items-center gap-xs pb-2 border-b border-white/5">
                <span className="material-symbols-outlined text-[#e8a940] text-sm">target</span>
                <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Chiến lược đang kích hoạt</h3>
              </div>
              <div className="space-y-sm my-auto">
                {['Banking Sector Focus', 'Blue Chip Accumulation'].map((s, i) => (
                  <div key={i} className="flex items-center justify-between p-3 rounded-xl bg-white/[0.01] border border-white/5 hover:bg-white/[0.03] transition-colors">
                    <span className="text-xs font-bold text-on-surface">{s}</span>
                    <span className="material-symbols-outlined text-[#2dbd7e] text-sm">check_circle</span>
                  </div>
                ))}
              </div>
            </GlassCard>
          </div>
        </div>

        <div className="lg:col-span-4 flex flex-col gap-lg">
          <RiskScore score={8.5} />
          
          <BacktestWidget />
          <BacktestHistory />

          <GlassCard className="flex flex-col gap-lg overflow-hidden border-white/5">
            <div className="flex justify-between items-center pb-2 border-b border-white/5">
              <div className="flex items-center gap-xs">
                <span className="material-symbols-outlined text-[#e8a940] text-sm">assignment</span>
                <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Nhật ký hoạt động (Logs)</h3>
              </div>
              <Badge variant="outline" className="text-[8px] tracking-wider uppercase font-bold border-white/10">LIVE</Badge>
            </div>
            
            <div className="space-y-lg max-h-[220px] overflow-y-auto pr-2 no-scrollbar font-data-mono">
              {[
                { symbol: 'VNM', action: 'BUY', price: '68,200', time: '14:22', color: 'text-[#2dbd7e]' },
                { symbol: 'HPG', action: 'HOLD', price: 'Volume Flow', time: '11:05', color: 'text-[#7bbcee]' },
                { symbol: 'TCB', action: 'SELL', price: '34,150', time: 'Yesterday', color: 'text-[#f87171]' }
              ].map((log, i) => (
                <div key={i} className="flex justify-between items-center group cursor-pointer border-b border-white/[0.01] pb-2 last:border-0">
                  <div className="flex items-center gap-md">
                    <div className="w-9 h-9 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center font-bold text-xs text-[#e8a940] group-hover:bg-[#e8a940] group-hover:text-black transition-all">
                      {log.symbol}
                    </div>
                    <div>
                      <p className="text-xs font-black">
                        <span className={log.color}>{log.action}</span> {log.symbol}
                      </p>
                      <p className="text-[9px] opacity-45">{log.price}</p>
                    </div>
                  </div>
                  <span className="text-[9px] opacity-45">{log.time}</span>
                </div>
              ))}
            </div>
            
            <button className="w-full py-3 bg-white/5 hover:bg-white/10 border border-white/5 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all mt-auto active:scale-98">
              XEM TOÀN BỘ HOẠT ĐỘNG
            </button>
          </GlassCard>
        </div>
      </section>
    </motion.div>
  );
}
