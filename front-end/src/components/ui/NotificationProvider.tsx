"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useAlertStore, Alert } from "@/stores/useAlertStore";
import { socketClient } from "@/services/socket";
import { cn } from "@/lib/utils";

interface Toast {
  id: string;
  alert: Alert;
  price?: number;
}

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const checkPriceAlerts = useAlertStore((s) => s.checkPriceAlerts);

  const addToast = (alert: Alert, price?: number) => {
    const id = Math.random().toString(36).substring(7);
    setToasts((prev) => [...prev, { id, alert, price }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  };

  useEffect(() => {
    // 1. Listen for backend triggered alerts
    const unsubSocket = socketClient.subscribeAlerts((data) => {
      const d = data as { alert?: Alert; price?: number };
      if (d?.alert) addToast(d.alert, d.price);
    });

    // 2. Listen for frontend triggered alerts
    const handleFrontendAlert = (e: Event) => {
      const { alert, currentPrice } = (e as CustomEvent<{ alert: Alert; currentPrice: number }>).detail;
      addToast(alert, currentPrice);
    };

    window.addEventListener("alert-triggered", handleFrontendAlert);

    // 3. Listen to market updates to check frontend alerts
    const unsubMarket = socketClient.subscribeSnapshot((data) => {
      if (Array.isArray(data)) {
        data.forEach((quote: { symbol: string; price: number }) => {
          if (quote.symbol && quote.price) {
            checkPriceAlerts(quote.symbol, quote.price);
          }
        });
      }
    });

    return () => {
      unsubSocket();
      unsubMarket();
      window.removeEventListener("alert-triggered", handleFrontendAlert);
    };
  }, [checkPriceAlerts, addToast]);

  return (
    <>
      {children}
      <div className="fixed bottom-xl right-xl z-[9999] flex flex-col gap-md items-end">
        <AnimatePresence>
          {toasts.map((toast) => (
            <motion.div
              key={toast.id}
              initial={{ opacity: 0, x: 50, scale: 0.9 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.2 } }}
              className="bg-surface-container-highest/90 backdrop-blur-2xl border border-primary/20 rounded-2xl p-lg shadow-2xl flex items-center gap-lg min-w-[300px]"
            >
              <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center text-primary">
                <span className="material-symbols-outlined">notifications_active</span>
              </div>
              <div className="flex-1">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-black text-primary uppercase">{toast.alert.symbol}</span>
                  <span className="text-[10px] font-bold opacity-40">JUST NOW</span>
                </div>
                <p className="text-xs font-bold text-on-surface">
                  Price {toast.alert.condition} {toast.alert.value}
                </p>
                {toast.price && (
                  <p className="text-[10px] text-secondary font-mono mt-1">
                    Current: {toast.price.toLocaleString()}
                  </p>
                )}
              </div>
              <button 
                onClick={() => setToasts(prev => prev.filter(t => t.id !== toast.id))}
                className="opacity-40 hover:opacity-100 transition-opacity"
              >
                <span className="material-symbols-outlined text-sm">close</span>
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </>
  );
}
