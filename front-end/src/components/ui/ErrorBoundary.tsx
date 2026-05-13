"use client";

import React, { Component, ErrorInfo, ReactNode } from "react";
import { GlassCard } from "./GlassCard";

interface Props {
  children?: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false
  };

  public static getDerivedStateFromError(_: Error): State {
    return { hasError: true };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught error:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="flex items-center justify-center min-h-[400px] p-xl">
          <GlassCard className="max-w-md w-full p-xl border-error/20 bg-error/5 text-center space-y-lg">
            <div className="w-16 h-16 rounded-full bg-error/20 flex items-center justify-center text-error mx-auto">
               <span className="material-symbols-outlined text-[32px]">warning</span>
            </div>
            <div className="space-y-sm">
               <h3 className="text-xl font-bold">Đã có lỗi xảy ra</h3>
               <p className="text-sm opacity-60">Chúng tôi không thể tải dữ liệu thị trường lúc này. Vui lòng thử lại sau hoặc liên hệ bộ phận hỗ trợ.</p>
            </div>
            <button 
              onClick={() => window.location.reload()}
              className="px-xl py-2 bg-error text-white rounded-xl font-bold hover:bg-error/80 transition-all"
            >
              Tải lại trang
            </button>
          </GlassCard>
        </div>
      );
    }

    return this.props.children;
  }
}
