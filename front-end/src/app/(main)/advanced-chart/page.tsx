"use client";

import { motion } from 'framer-motion';
import { PageHeader } from '@/components/layout/PageHeader';
import { SmartInsights } from '@/components/feature/stock/SmartInsights';
import PriceChart from '@/components/feature/stock/PriceChart';

export default function Page() {
  return (
    <div className="flex h-screen overflow-hidden">
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="flex-1 flex flex-col overflow-hidden"
      >
        <div className="px-xl pt-lg flex justify-between items-center bg-surface/30 backdrop-blur border-b border-white/5">
          <div className="flex items-center gap-xl py-sm">
            <div>
              <div className="flex items-center gap-sm">
                <h2 className="font-headline-lg text-[20px] text-on-surface">HPG:HOSE</h2>
                <span className="text-secondary font-data-mono">+1.25%</span>
              </div>
              <p className="text-[10px] text-on-surface-variant uppercase tracking-widest">Hoa Phat Group Joint Stock Company</p>
            </div>
            <div className="h-8 w-px bg-white/10"></div>
            <div className="flex gap-lg">
              <div>
                <p className="text-[10px] text-on-surface-variant font-label-caps">LAST PRICE</p>
                <p className="font-data-mono text-on-surface">28.450</p>
              </div>
              <div>
                <p className="text-[10px] text-on-surface-variant font-label-caps">VOLUME</p>
                <p className="font-data-mono text-on-surface">12.4M</p>
              </div>
            </div>
          </div>
          <div className="flex gap-md">
            <button className="px-lg py-sm rounded-lg bg-secondary text-on-secondary font-bold text-sm shadow-lg shadow-secondary/20">BUY</button>
            <button className="px-lg py-sm rounded-lg bg-error text-on-error font-bold text-sm shadow-lg shadow-error/20">SELL</button>
          </div>
        </div>

        <div className="flex-1 bg-[#050505] relative p-4 flex flex-col">
          <PriceChart height={750} />
          
          <div className="absolute bottom-12 left-1/2 -translate-x-1/2 flex justify-center pointer-events-none z-30">
             <div className="glass-card p-xs rounded-full flex gap-xs pointer-events-auto border border-white/10 shadow-2xl">
                {['M1', 'M5', 'M15', 'H1', 'H4', 'D1', 'W1'].map(t => (
                  <button key={t} className={`px-md py-xs rounded-full text-xs font-data-mono transition-all ${t === 'H4' ? 'bg-primary text-on-primary' : 'hover:bg-white/5'}`}>
                    {t}
                  </button>
                ))}
             </div>
          </div>
        </div>
      </motion.div>

      <SmartInsights />
    </div>
  );
}
