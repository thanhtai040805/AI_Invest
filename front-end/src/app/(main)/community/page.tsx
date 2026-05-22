"use client";

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { useCommunityStore } from '@/stores/useCommunityStore';
import { useStockStore } from '@/stores/useStockStore';
import { Skeleton } from 'boneyard-js/react';
import { cn } from '@/lib/utils';

export default function Page() {
  const { posts, insights, experts, likePost, fetchPosts, fetchInsights, fetchTopExperts, loading } = useCommunityStore();
  const { stocks } = useStockStore();
  const [fetched, setFetched] = useState(false);

  useEffect(() => {
    const init = async () => {
      await Promise.all([fetchPosts(), fetchInsights(), fetchTopExperts()]);
      setFetched(true);
    };
    init();
  }, [fetchPosts, fetchInsights, fetchTopExperts]);

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
          <Skeleton name="community-insights" loading={!fetched}>
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
                  <span className="bg-white/5 text-on-surface px-md py-xs rounded-full text-[10px] font-bold uppercase font-data-mono">Vol: {insights.volumeChange >= 0 ? '+' : ''}{insights.volumeChange}%</span>
                </div>
              </section>
            )}
          </Skeleton>

          {/* Posts List */}
          <div className="space-y-md">
            <Skeleton name="community-posts" loading={loading}>
              {posts.length === 0 && !loading && (
                <div className="glass-card rounded-xl p-xl text-center text-on-surface-variant opacity-60">
                  <span className="material-symbols-outlined text-4xl mb-sm">forum</span>
                  <p className="text-sm">Chưa có bài viết nào. Hãy là người đầu tiên!</p>
                </div>
              )}
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
                          {post.timestamp}{post.symbol ? ` • ${post.symbol}` : ''}
                        </p>
                      </div>
                    </div>
                    <button className="text-on-surface-variant opacity-40 hover:opacity-100 transition-all">
                      <span className="material-symbols-outlined">more_horiz</span>
                    </button>
                  </div>

                  <p className="text-sm leading-relaxed text-on-surface/90">{post.content}</p>

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
          {/* Top Experts Sidebar */}
          <section className="glass-card rounded-xl p-lg border border-white/5">
            <h3 className="font-bold text-sm mb-xl uppercase tracking-widest opacity-60">Chuyên Gia Top Đầu</h3>
            <div className="space-y-md">
              <Skeleton name="community-experts" loading={!fetched}>
                {experts.length === 0 && (
                  <p className="text-on-surface-variant text-xs opacity-40 text-center py-md">Chưa có chuyên gia</p>
                )}
                {experts.map((expert, i) => (
                  <div key={expert.id} className="flex items-center gap-md p-xs">
                    <div className="relative">
                      <div className="w-12 h-12 rounded-full border-2 border-primary/20 bg-primary/5 flex items-center justify-center font-bold">
                        {expert.displayName.substring(0, 1)}
                      </div>
                      <div className="absolute -bottom-1 -right-1 bg-secondary text-white rounded-full w-5 h-5 flex items-center justify-center text-[8px] font-bold border-2 border-[#0a0a0a]">
                        {i + 1}
                      </div>
                    </div>
                    <div className="flex-1">
                      <p className="font-bold text-xs">{expert.displayName}</p>
                      <p className="text-[10px] opacity-40 font-medium">{expert.postCount} bài • {expert.reactionCount} thích</p>
                    </div>
                    <span className="bg-primary/10 text-primary text-[8px] px-xs py-[1px] rounded border border-primary/20 font-black uppercase">
                      {expert.rank}
                    </span>
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
