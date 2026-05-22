import { create } from 'zustand';
import { communityAPI } from '@/services/api';
import { Post, MarketInsight } from '@/types/community';

interface Expert {
  id: string;
  displayName: string;
  winRate: number;
  reactionCount: number;
  postCount: number;
  rank: 'Elite' | 'Pro' | 'Member';
}

interface CommunityState {
  posts: Post[];
  insights: MarketInsight | null;
  experts: Expert[];
  loading: boolean;
  nextCursor: string | null;
  fetchPosts: () => Promise<void>;
  fetchInsights: () => Promise<void>;
  fetchTopExperts: () => Promise<void>;
  likePost: (postId: string) => Promise<void>;
  addPost: (content: string, taggedSymbols?: string[]) => Promise<void>;
}

function mapPost(raw: any): Post {
  return {
    id: raw.id,
    author: {
      id: raw.author?.id || 'unknown',
      name: raw.author?.displayName || 'Ẩn danh',
      avatar: '',
      rank: 'Member',
    },
    content: raw.content,
    timestamp: new Date(raw.createdAt).toLocaleString('vi-VN'),
    symbol: raw.taggedSymbols?.[0] || undefined,
    likes: raw._count?.reactions || 0,
    commentCount: raw._count?.comments || 0,
  };
}

export const useCommunityStore = create<CommunityState>((set, get) => ({
  posts: [],
  insights: null,
  experts: [],
  loading: false,
  nextCursor: null,

  fetchPosts: async () => {
    set({ loading: true });
    try {
      const res = await communityAPI.getPosts({ limit: 20 });
      set({
        posts: res.posts.map(mapPost),
        nextCursor: res.nextCursor,
        loading: false,
      });
    } catch {
      set({ loading: false });
    }
  },

  fetchInsights: async () => {
    try {
      const data = await communityAPI.getInsights();
      set({ insights: data });
    } catch {
      // keep existing insights or null
    }
  },

  fetchTopExperts: async () => {
    try {
      const data = await communityAPI.getTopExperts();
      set({ experts: data });
    } catch {
      set({ experts: [] });
    }
  },

  likePost: async (postId: string) => {
    set((state) => ({
      posts: state.posts.map((p) =>
        p.id === postId ? { ...p, likes: p.likes + 1 } : p
      ),
    }));
    try {
      await communityAPI.toggleReaction(postId);
    } catch {
      set((state) => ({
        posts: state.posts.map((p) =>
          p.id === postId ? { ...p, likes: p.likes - 1 } : p
        ),
      }));
    }
  },

  addPost: async (content: string, taggedSymbols?: string[]) => {
    try {
      const raw = await communityAPI.createPost({ content, taggedSymbols });
      const post = mapPost(raw);
      set((state) => ({ posts: [post, ...state.posts] }));
    } catch {
      // handled by caller
    }
  },
}));
