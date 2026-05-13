"use client";

import { GlassCard } from "@/components/ui/GlassCard";

const news = [
  { time: "10:42 AM", content: "Khối ngoại quay lại mua ròng mạnh các mã Bluechip như VNM, FPT.", priority: true },
  { time: "09:15 AM", content: "Lãi suất huy động rục rịch tăng, dòng tiền thông minh hướng về cổ phiếu.", priority: false },
  { time: "TRƯỚC GIỜ MỞ CỬA", content: "Dow Jones tăng điểm mạnh tạo đà tâm lý tích cực cho VN-Index hôm nay.", priority: false },
];

export function MarketNews() {
  return (
    <GlassCard className="flex flex-col max-h-[300px]">
      <h3 className="font-title-md text-title-md mb-md">Market News</h3>
      <div className="overflow-y-auto space-y-md no-scrollbar">
        {news.map((item, i) => (
          <div key={i} className={cn(
            "border-l-2 pl-md py-xs",
            item.priority ? "border-primary" : "border-white/10"
          )}>
            <span className="text-[10px] font-label-caps text-on-surface-variant">{item.time}</span>
            <p className="font-body-sm text-body-sm leading-tight hover:text-primary transition-colors cursor-pointer">
              {item.content}
            </p>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}

import { cn } from "@/lib/utils";
