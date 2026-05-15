"use client";

import { motion, AnimatePresence } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { useState } from "react";
import { cn } from "@/lib/utils";

interface Alert {
  id: string;
  symbol: string;
  type: 'Price' | 'Indicator' | 'News';
  condition: string;
  value: string;
  status: 'Active' | 'Triggered' | 'Paused';
  createdAt: string;
}

import { useAlertStore } from "@/stores/useAlertStore";

export default function AlertsPage() {
  const { alerts, addAlert, updateAlert, removeAlert } = useAlertStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [newPrice, setNewPrice] = useState("");
  const [newCondition, setNewCondition] = useState<"Above" | "Below">("Above");

  const handleCreate = () => {
    if (!newSymbol || !newPrice) return;
    addAlert({
      symbol: newSymbol.toUpperCase(),
      type: "Price",
      condition: newCondition,
      value: newPrice,
    });
    setShowCreate(false);
    setNewSymbol("");
    setNewPrice("");
  };

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[#050505]">
      {/* HEADER */}
      <div className="h-16 border-b border-white/5 flex items-center justify-between px-xl bg-[#0a0a0a]">
        <div className="flex items-center gap-xl">
           <h1 className="text-xl font-black text-primary tracking-tighter uppercase">Smart Alert System</h1>
           <Badge variant="outline" className="text-[10px] font-black">{alerts.filter(a => a.status === 'Active').length} ACTIVE</Badge>
        </div>
        <button 
          onClick={() => setShowCreate(true)}
          className="bg-primary text-white px-6 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest hover:shadow-lg hover:shadow-primary/20 transition-all"
        >
           + Create Alert
        </button>
      </div>

      <AnimatePresence>
        {showCreate && (
          <motion.div 
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="px-xl py-md bg-[#0a0a0a] border-b border-white/5 overflow-hidden flex gap-md items-center"
          >
            <input 
              className="bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-xs" 
              placeholder="Symbol (e.g. VNM)" 
              value={newSymbol}
              onChange={e => setNewSymbol(e.target.value)}
            />
            <select 
              className="bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-xs"
              value={newCondition}
              onChange={e => setNewCondition(e.target.value as "Above" | "Below")}
            >
              <option value="Above">Above</option>
              <option value="Below">Below</option>
            </select>
            <input 
              type="number"
              className="bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-xs" 
              placeholder="Price" 
              value={newPrice}
              onChange={e => setNewPrice(e.target.value)}
            />
            <button 
              onClick={handleCreate}
              className="bg-secondary text-[#000] px-4 py-2 rounded-xl text-xs font-bold"
            >
              Save
            </button>
            <button 
              onClick={() => setShowCreate(false)}
              className="text-on-surface-variant px-4 py-2 text-xs"
            >
              Cancel
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex-1 overflow-y-auto no-scrollbar p-xl grid grid-cols-12 gap-xl">
        
        {/* LEFT: ALERT LIST */}
        <div className="col-span-8 space-y-md">
           <AnimatePresence>
              {alerts.map((alert) => (
                <motion.div
                  layout
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  key={alert.id}
                >
                   <GlassCard className="p-lg flex items-center justify-between border-white/5 hover:border-primary/20 transition-all bg-[#0a0a0a]">
                      <div className="flex items-center gap-xl">
                         <div className={cn(
                           "w-12 h-12 rounded-2xl flex items-center justify-center",
                           alert.status === 'Active' ? "bg-primary/10 text-primary" : alert.status === 'Triggered' ? "bg-secondary/10 text-secondary" : "bg-white/5 opacity-40"
                         )}>
                            <span className="material-symbols-outlined text-xl">
                               {alert.type === 'Price' ? 'notifications' : alert.type === 'Indicator' ? 'monitoring' : 'article'}
                            </span>
                         </div>
                         <div className="space-y-1">
                            <div className="flex items-center gap-md">
                               <span className="text-sm font-black text-primary">{alert.symbol}</span>
                               <Badge variant="outline" className="text-[8px] opacity-60">{alert.type}</Badge>
                            </div>
                            <p className="text-[11px] font-bold opacity-80">{alert.condition} <span className="text-secondary">{alert.value}</span></p>
                         </div>
                      </div>
                      
                      <div className="flex items-center gap-xl">
                         <div className="text-right">
                            <p className="text-[9px] font-black opacity-30 uppercase">Status</p>
                            <span className={cn(
                              "text-[10px] font-bold",
                              alert.status === 'Active' ? "text-primary" : alert.status === 'Triggered' ? "text-secondary" : "opacity-40"
                            )}>
                               {alert.status.toUpperCase()}
                            </span>
                         </div>
                         <div className="flex gap-md">
                            <button 
                              onClick={() => updateAlert(alert.id, { status: alert.status === 'Paused' ? 'Active' : 'Paused' })}
                              className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center opacity-40 hover:opacity-100 hover:bg-white/10 transition-all"
                            >
                               <span className="material-symbols-outlined text-sm">{alert.status === 'Paused' ? 'play_arrow' : 'pause'}</span>
                            </button>
                            <button 
                              onClick={() => removeAlert(alert.id)}
                              className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center opacity-40 hover:opacity-100 hover:text-error transition-all"
                            >
                               <span className="material-symbols-outlined text-sm">delete</span>
                            </button>
                         </div>
                      </div>
                   </GlassCard>
                </motion.div>
              ))}
           </AnimatePresence>
        </div>

        {/* RIGHT: CONFIG & LOGS */}
        <div className="col-span-4 space-y-xl">
           <GlassCard className="p-xl bg-[#0a0a0a]">
              <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest mb-xl">Alert Analytics</h3>
              <div className="space-y-lg">
                 <div className="flex justify-between items-end">
                    <span className="text-[10px] font-bold opacity-40">Monthly Triggers</span>
                    <span className="text-xl font-black text-secondary">42</span>
                 </div>
                 <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                    <div className="h-full bg-secondary" style={{ width: '65%' }} />
                 </div>
                 <p className="text-[10px] opacity-40 leading-relaxed italic">"Hầu hết các cảnh báo của bạn được kích hoạt bởi các vùng kháng cự mạnh của nhóm VN30."</p>
              </div>
           </GlassCard>

           <div className="space-y-md">
              <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest ml-md">Recent Triggers</h3>
              {[1, 2, 3].map(i => (
                <div key={i} className="p-md bg-white/[0.02] border-l-2 border-secondary rounded-r-lg space-y-1">
                   <div className="flex justify-between">
                      <span className="text-[10px] font-black text-secondary">FPT BREAKOUT</span>
                      <span className="text-[8px] opacity-40">2m ago</span>
                   </div>
                   <p className="text-[10px] opacity-60">Price crossed above 120.5 with high volume.</p>
                </div>
              ))}
           </div>
        </div>

      </div>
    </div>
  );
}
