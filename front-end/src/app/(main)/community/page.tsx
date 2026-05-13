"use client";

import { motion } from 'framer-motion';
import { useCommunityStore } from '@/stores/useCommunityStore';
import { useStockStore } from '@/stores/useStockStore';
import { useUIStore } from '@/stores/useUIStore';
import { Skeleton } from 'boneyard-js/react';
import { cn } from '@/lib/utils';

export default function Page() {
  const { posts, insights, likePost } = useCommunityStore();
  const { stocks } = useStockStore();
  const { isLoading } = useUIStore();

  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="selection:bg-primary/30 min-h-screen"
    >
      <div className="max-w-7xl mx-auto p-lg grid grid-cols-1 lg:grid-cols-12 gap-lg">
        
        {/* Main Feed */}
        <div className="lg:col-span-8 space-y-lg">
          
          {/* AI Insights Section */}
          <Skeleton name="community-insights" loading={isLoading}>
            {insights && (
              <section className="glass-card bg-primary/5 rounded-xl p-lg relative overflow-hidden border border-primary/20 shadow-xl">
                <div className="absolute top-0 right-0 p-lg opacity-10">
                  <span className="material-symbols-outlined text-[100px]">psychology</span>
                </div>
                <div className="flex items-center gap-sm mb-md">
                  <span className="material-symbols-outlined text-primary">auto_awesome</span>
                  <h2 className="font-title-lg text-primary uppercase tracking-tighter">{insights.title}</h2>
                </div>
                <p className="text-on-surface-variant mb-lg max-w-2xl leading-relaxed">
                  {insights.content}
                </p>
                <div className="flex flex-wrap gap-md">
                  <span className="bg-secondary/20 text-secondary border border-secondary/30 px-md py-xs rounded-full text-[10px] font-bold uppercase">Xu hướng: {insights.trend}</span>
                  <span className="bg-primary/20 text-primary border border-primary/30 px-md py-xs rounded-full text-[10px] font-bold uppercase">Hot: {insights.hotSector}</span>
                  <span className="bg-white/5 text-on-surface px-md py-xs rounded-full text-[10px] font-bold uppercase font-data-mono">Vol: +{insights.volumeChange}%</span>
                </div>
              </section>
            )}
          </Skeleton>

          {/* Posts List */}
          <div className="space-y-md">
            <Skeleton name="community-posts" loading={isLoading}>
              {posts.map((post) => (
                <article key={post.id} className="glass-card rounded-xl p-xl space-y-md hover:bg-white/[0.03] transition-all border border-white/5 shadow-lg">
                  <div className="flex justify-between items-start">
                    <div className="flex gap-md">
                      <div className="w-12 h-12 rounded-full overflow-hidden border-2 border-primary/20 bg-primary/10 flex items-center justify-center font-bold">
                        {post.author.avatar.length > 30 ? (
                           <img alt="Avatar" className="w-full h-full object-cover" src={post.author.avatar} />
                        ) : post.author.name.substring(0, 1)}
                      </div>
                      <div>
                        <div className="flex items-center gap-xs">
                          <span className="font-bold text-sm">{post.author.name}</span>
                          <span className="bg-primary/10 text-primary text-[8px] px-xs py-[1px] rounded border border-primary/20 font-black uppercase">
                            {post.author.rank}
                          </span>
                        </div>
                        <p className="text-on-surface-variant text-[10px] opacity-60 font-medium">
                          {post.timestamp} • {post.strategy || 'Phân tích chung'}
                        </p>
                      </div>
                    </div>
                    <button className="text-on-surface-variant opacity-40 hover:opacity-100 transition-all">
                      <span className="material-symbols-outlined">more_horiz</span>
                    </button>
                  </div>

                  <p className="text-sm leading-relaxed text-on-surface/90">{post.content}</p>

                  {post.symbol && (
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-md">
                      <div className="bg-white/5 rounded-xl p-md border border-white/5">
                        <p className="text-on-surface-variant text-[8px] font-black uppercase tracking-widest mb-xs opacity-40">MÃ</p>
                        <p className="font-data-mono text-xl font-bold text-primary">{post.symbol}</p>
                      </div>
                      {post.pnl && (
                        <div className="bg-white/5 rounded-xl p-md border border-white/5">
                          <p className="text-on-surface-variant text-[8px] font-black uppercase tracking-widest mb-xs opacity-40">LỢI NHUẬN</p>
                          <p className="font-data-mono text-xl font-bold text-secondary">+{post.pnl}%</p>
                        </div>
                      )}
                      {post.status && (
                        <div className="bg-white/5 rounded-xl p-md border border-white/5">
                          <p className="text-on-surface-variant text-[8px] font-black uppercase tracking-widest mb-xs opacity-40">TRẠNG THÁI</p>
                          <p className="font-data-mono text-lg font-bold opacity-80">{post.status}</p>
                        </div>
                      )}
                    </div>
                  )}

                  {post.image && post.image.length > 30 && (
                    <div className="w-full h-56 rounded-2xl overflow-hidden border border-white/10 shadow-2xl relative group">
                       <img className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700" src={post.image} alt="Chart" />
                       <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent flex items-end p-lg">
                          <span className="text-[10px] font-bold text-secondary uppercase tracking-widest">Phân tích kỹ thuật chuyên sâu</span>
                       </div>
                    </div>
                  )}

                  <div className="flex items-center gap-xl pt-lg border-t border-white/5">
                    <button 
                      onClick={() => likePost(post.id)}
                      className="flex items-center gap-xs text-on-surface-variant hover:text-secondary transition-all group"
                    >
                      <span className="material-symbols-outlined text-[18px] group-active:scale-125 transition-transform">thumb_up</span>
                      <span className="text-[10px] font-bold">{post.likes}</span>
                    </button>
                    <button className="flex items-center gap-xs text-on-surface-variant hover:text-primary transition-all">
                      <span className="material-symbols-outlined text-[18px]">chat_bubble</span>
                      <span className="text-[10px] font-bold">{post.commentCount}</span>
                    </button>
                    <button className="flex items-center gap-xs text-on-surface-variant hover:text-on-surface transition-all ml-auto">
                      <span className="material-symbols-outlined text-[18px]">share</span>
                    </button>
                  </div>
                </article>
              ))}
            </Skeleton>
          </div>
        </div>

        {/* Sidebar */}
        <div className="lg:col-span-4 space-y-lg">
          
          {/* Active Stocks Sidebar */}
          <section className="glass-card rounded-xl p-lg border border-white/5">
            <h3 className="font-bold text-sm mb-xl uppercase tracking-widest opacity-60">Thị Trường Sôi Động</h3>
            <div className="space-y-sm">
              <Skeleton name="community-stocks" loading={isLoading}>
                {stocks.slice(0, 4).map((stock) => (
                  <div key={stock.symbol} className="flex items-center justify-between p-md hover:bg-white/[0.03] rounded-xl transition-all border border-transparent hover:border-white/5">
                    <div className="flex items-center gap-md">
                      <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center font-bold text-primary border border-primary/10">{stock.symbol}</div>
                      <div>
                        <p className="font-bold text-sm">{stock.name}</p>
                        <p className="text-[9px] opacity-40 font-bold uppercase tracking-widest">HOSE</p>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="font-data-mono font-bold text-sm">{stock.price.toFixed(1)}</p>
                      <p className={cn("text-[10px] font-bold font-data-mono", stock.changePercent >= 0 ? "text-secondary" : "text-error")}>
                        {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent}%
                      </p>
                    </div>
                  </div>
                ))}
              </Skeleton>
            </div>
          </section>

          {/* Top Experts Sidebar */}
          <section className="glass-card rounded-xl p-lg border border-white/5">
            <h3 className="font-bold text-sm mb-xl uppercase tracking-widest opacity-60">Chuyên Gia Top Đầu</h3>
            <div className="space-y-md">
              <Skeleton name="community-experts" loading={isLoading}>
                {[
                  { name: 'Trần Minh Quân', rate: '88%', rank: 'Elite' },
                  { name: 'Lê Thu Hà', rate: '82%', rank: 'Pro' }
                ].map((expert, i) => (
                  <div key={i} className="flex items-center gap-md p-xs">
                    <div className="relative">
                      <div className="w-12 h-12 rounded-full border-2 border-primary/20 bg-primary/5 flex items-center justify-center font-bold">
                        {expert.name.substring(0, 1)}
                      </div>
                      <div className="absolute -bottom-1 -right-1 bg-secondary text-white rounded-full w-5 h-5 flex items-center justify-center text-[8px] font-bold border-2 border-[#0a0a0a]">
                        {i + 1}
                      </div>
                    </div>
                    <div className="flex-1">
                      <p className="font-bold text-xs">{expert.name}</p>
                      <p className="text-[10px] opacity-40 font-medium">Thắng: {expert.rate}</p>
                    </div>
                    <button className="bg-primary/10 text-primary px-4 py-1.5 rounded-full text-[10px] font-bold uppercase hover:bg-primary hover:text-white transition-all">
                      Theo dõi
                    </button>
                  </div>
                ))}
              </Skeleton>
            </div>
          </section>
        </div>

      </div>
    </motion.div>
  );
}
