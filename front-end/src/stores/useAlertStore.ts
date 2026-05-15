import { create } from "zustand";
import { persist } from "zustand/middleware";
import { socketClient } from "@/services/socket";

export interface Alert {
  id: string;
  symbol: string;
  type: "Price" | "Indicator" | "News";
  condition: "Above" | "Below" | "Crosses";
  value: string;
  status: "Active" | "Triggered" | "Paused";
  createdAt: string;
}

interface AlertState {
  alerts: Alert[];
  addAlert: (alert: Omit<Alert, "id" | "status" | "createdAt">) => void;
  updateAlert: (id: string, updates: Partial<Alert>) => void;
  removeAlert: (id: string) => void;
  checkPriceAlerts: (symbol: string, currentPrice: number) => void;
}

export const useAlertStore = create<AlertState>()(
  persist(
    (set, get) => ({
      alerts: [],
      addAlert: (alertData) =>
        set((state) => ({
          alerts: [
            {
              ...alertData,
              id: Date.now().toString(),
              status: "Active",
              createdAt: new Date().toISOString(),
            },
            ...state.alerts,
          ],
        })),
      updateAlert: (id, updates) =>
        set((state) => ({
          alerts: state.alerts.map((a) => (a.id === id ? { ...a, ...updates } : a)),
        })),
      removeAlert: (id) =>
        set((state) => ({
          alerts: state.alerts.filter((a) => a.id !== id),
        })),
      checkPriceAlerts: (symbol, currentPrice) => {
        const { alerts, updateAlert } = get();
        alerts.forEach((alert) => {
          if (alert.status !== "Active" || alert.symbol !== symbol || alert.type !== "Price") return;
          
          const targetPrice = parseFloat(alert.value);
          let triggered = false;
          
          if (alert.condition === "Above" && currentPrice >= targetPrice) {
            triggered = true;
          } else if (alert.condition === "Below" && currentPrice <= targetPrice) {
            triggered = true;
          }

          if (triggered) {
            updateAlert(alert.id, { status: "Triggered" });
            // Dispatch a custom event or show a notification
            if (typeof window !== "undefined") {
              const event = new CustomEvent("alert-triggered", { detail: { alert, currentPrice } });
              window.dispatchEvent(event);
            }
          }
        });
      },
    }),
    {
      name: "aiinvest-alerts",
    }
  )
);
