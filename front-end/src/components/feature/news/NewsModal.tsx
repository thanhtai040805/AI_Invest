"use client";

import { useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";

interface PDFLink {
  url: string;
  name: string;
  pageCount?: number;
}

interface NewsDetail {
  id: string;
  newsId: string;
  symbol: string;
  title: string;
  url: string;
  content: string | null;
  articleContent: string | null;
  articlePdfText: string | null;
  articlePdfLinks: string | null;
  publishDate: string;
  sentimentLabel: string | null;
  sentimentScore: number | null;
}

interface NewsModalProps {
  news: NewsDetail | null;
  onClose: () => void;
}

function formatDate(dateString: string) {
  try {
    return new Date(dateString).toLocaleString("vi-VN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateString;
  }
}

export function NewsModal({ news, onClose }: NewsModalProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (news) {
      document.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [news, handleKeyDown]);

  let pdfLinks: PDFLink[] = [];
  if (news?.articlePdfLinks) {
    try {
      pdfLinks = JSON.parse(news.articlePdfLinks);
    } catch {
      pdfLinks = [];
    }
  }

  const sentimentColor =
    (news?.sentimentScore ?? 0) > 0
      ? "text-[#2dbd7e]"
      : (news?.sentimentScore ?? 0) < 0
        ? "text-[#f87171]"
        : "text-white/60";

  return (
    <AnimatePresence>
      {news && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Modal */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            className="relative w-full max-w-3xl max-h-[85vh] overflow-y-auto bg-[#0f0f11] border border-white/[0.08] rounded-2xl shadow-2xl"
          >
            {/* Close button */}
            <button
              onClick={onClose}
              className="sticky top-4 float-right mr-4 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-white/5 border border-white/10 hover:bg-white/10 text-white/60 hover:text-white transition-all"
            >
              ✕
            </button>

            <div className="p-6 pt-8 space-y-5">
              {/* Header */}
              <div className="space-y-2 pr-10">
                <div className="flex items-center gap-3">
                  <span className="px-2.5 py-1 text-[10px] font-extrabold rounded border border-[#e8a940]/20 bg-[#e8a940]/10 text-[#e8a940] font-data-mono">
                    {news.symbol}
                  </span>
                  <span className={cn("text-xs font-bold font-data-mono", sentimentColor)}>
                    {news.sentimentLabel || "NEUTRAL"} ({news.sentimentScore != null ? (news.sentimentScore > 0 ? "+" : "") + news.sentimentScore.toFixed(2) : "0.00"})
                  </span>
                  <span className="text-[10px] text-white/30 font-data-mono">
                    {formatDate(news.publishDate)}
                  </span>
                </div>
                <h2 className="text-xl font-bold text-white leading-tight">
                  {news.title}
                </h2>
              </div>

              {/* Article Content */}
              {news.articleContent && (
                <div className="bg-black/40 border border-white/[0.06] rounded-xl p-5">
                  <div className="text-[10px] font-bold text-white/40 uppercase tracking-widest mb-3 font-data-mono">
                    NỘI DUNG BÀI VIẾT
                  </div>
                  <div
                    className="prose prose-invert prose-sm max-w-none text-white/80 leading-relaxed [&_img]:rounded-lg [&_img]:max-w-full [&_img]:h-auto [&_a]:text-[#e8a940] [&_a]:underline [&_p]:mb-3 [&_figure]:my-4"
                    dangerouslySetInnerHTML={{ __html: news.articleContent }}
                  />
                </div>
              )}

              {/* Plain text fallback */}
              {!news.articleContent && news.content && (
                <div className="bg-black/40 border border-white/[0.06] rounded-xl p-5">
                  <div className="text-[10px] font-bold text-white/40 uppercase tracking-widest mb-3 font-data-mono">
                    NỘI DUNG BÀI VIẾT
                  </div>
                  <p className="text-sm text-white/70 leading-relaxed whitespace-pre-line">
                    {news.content}
                  </p>
                </div>
              )}

              {/* PDF Section */}
              {(pdfLinks.length > 0 || news.articlePdfText) && (
                <div className="bg-black/40 border border-white/[0.06] rounded-xl p-5">
                  <div className="flex items-center gap-2 mb-3">
                    <Badge variant="tertiary" className="rounded font-data-mono font-black text-[9px] tracking-widest">
                      PDF
                    </Badge>
                    <span className="text-[10px] font-bold text-white/40 uppercase tracking-widest font-data-mono">
                      TÀI LIỆU ĐÍNH KÈM
                    </span>
                  </div>

                  {/* PDF Links */}
                  {pdfLinks.length > 0 && (
                    <div className="space-y-2 mb-4">
                      {pdfLinks.map((pdf, i) => (
                        <a
                          key={i}
                          href={pdf.url}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-3 px-4 py-3 bg-white/[0.02] border border-white/[0.06] hover:border-[#e8a940]/30 rounded-lg text-sm text-white/70 hover:text-[#e8a940] transition-all group"
                        >
                          <span className="text-lg opacity-40 group-hover:opacity-80">📄</span>
                          <span className="flex-1 truncate">{pdf.name || `Tai lieu ${i + 1}`}</span>
                          {pdf.pageCount && (
                            <span className="text-[10px] text-white/30">{pdf.pageCount} trang</span>
                          )}
                          <span className="text-[10px] text-[#e8a940] opacity-0 group-hover:opacity-100 transition-opacity">
                            MỞ →
                          </span>
                        </a>
                      ))}
                    </div>
                  )}

                  {/* Extracted PDF Text */}
                  {news.articlePdfText && (
                    <div>
                      <div className="text-[10px] font-bold text-white/30 uppercase tracking-widest mb-2 font-data-mono">
                        NỘI DUNG TRÍCH XUẤT TỪ PDF
                      </div>
                      <div className="text-xs text-white/60 leading-relaxed max-h-60 overflow-y-auto bg-black/30 rounded-lg p-4 font-mono whitespace-pre-wrap">
                        {news.articlePdfText.slice(0, 3000)}
                        {news.articlePdfText.length > 3000 && (
                          <span className="text-[#e8a940] block mt-2 text-[10px]">
                            ... (hiển thị 3000/{news.articlePdfText.length} ký tự)
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Original Source */}
              <a
                href={news.url}
                target="_blank"
                rel="noreferrer"
                className="block w-full text-center bg-[#e8a940]/10 border border-[#e8a940]/30 hover:bg-[#e8a940]/25 text-[#e8a940] py-3 rounded-xl text-xs font-bold font-data-mono tracking-wider uppercase transition-all"
              >
                [ MỞ TRANG GỐC ]
              </a>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
