"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { useSymbolSearch } from "@/hooks/useMarketData";
import { useDebounce } from "@/hooks/useDebounce"; // Need to create this if it doesn't exist
import { cn } from "@/lib/utils";

export function SymbolSearch({ className }: { className?: string }) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const debouncedQuery = useDebounce(query, 300);
  const { data: results, isLoading } = useSymbolSearch(debouncedQuery);
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSelect = (symbol: string) => {
    setIsOpen(false);
    setQuery("");
    router.push(`/stock/${symbol}`);
  };

  return (
    <div ref={containerRef} className={cn("relative z-50", className)}>
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 material-symbols-outlined text-on-surface-variant opacity-60">
          search
        </span>
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder="Search symbol, company..."
          className="w-full bg-white/5 border border-white/10 rounded-2xl py-2 pl-10 pr-4 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors"
        />
        {isLoading && query && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        )}
      </div>

      <AnimatePresence>
        {isOpen && query && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="absolute top-full left-0 right-0 mt-2 bg-surface-container-highest/90 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-2xl"
          >
            {results && results.length > 0 ? (
              <ul className="max-h-[300px] overflow-y-auto py-2 scrollbar-thin">
                {results.map((item: { symbol: string; companyName?: string; exchange?: string }) => (
                  <li key={item.symbol}>
                    <button
                      onClick={() => handleSelect(item.symbol)}
                      className="w-full text-left px-4 py-2 hover:bg-white/5 transition-colors flex items-center justify-between"
                    >
                      <div>
                        <div className="font-bold text-on-surface">{item.symbol}</div>
                        <div className="text-xs text-on-surface-variant line-clamp-1">
                          {item.companyName || "Unknown Company"}
                        </div>
                      </div>
                      <div className="text-xs font-data-mono text-on-surface-variant opacity-60">
                        {item.exchange || "HOSE"}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : !isLoading ? (
              <div className="p-4 text-center text-sm text-on-surface-variant">
                No symbols found for &quot;{query}&quot;
              </div>
            ) : null}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
