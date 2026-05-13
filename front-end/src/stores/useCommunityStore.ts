import { create } from 'zustand';
import { Post, MarketInsight } from '@/types/community';

interface CommunityState {
  posts: Post[];
  insights: MarketInsight | null;
  addPost: (post: Post) => void;
  likePost: (postId: string) => void;
  setInsights: (insights: MarketInsight) => void;
}

export const useCommunityStore = create<CommunityState>((set) => ({
  posts: [
    {
      id: 'p1',
      author: {
        id: 'u1',
        name: 'Quốc Huy Investor',
        avatar: 'https://lh3.googleusercontent.com/...',
        rank: 'Elite',
      },
      content: 'Đã gia tăng tỷ trọng HPG sau khi vượt cản chéo. Kỳ vọng vào phục hồi ngành thép trong Q3.',
      timestamp: '2 giờ trước',
      strategy: 'Chiến lược Tăng trưởng',
      symbol: 'HPG',
      pnl: 15.42,
      status: 'Đang giữ',
      likes: 128,
      commentCount: 42,
    },
    {
      id: 'p2',
      author: {
        id: 'u2',
        name: 'Minh Anh Trading',
        avatar: 'https://lh3.googleusercontent.com/...',
        rank: 'Pro',
      },
      content: 'VHM đang cho tín hiệu tạo đáy 2. Khối ngoại bắt đầu quay lại mua ròng.',
      timestamp: '4 giờ trước',
      symbol: 'VHM',
      image: 'https://lh3.googleusercontent.com/...',
      likes: 85,
      commentCount: 15,
    },
  ],
  insights: {
    id: 'i1',
    title: 'AI Nhận Định Thị Trường',
    content: 'Dòng tiền đang đổ mạnh vào nhóm cổ phiếu Ngân hàng (VCB, BID) và Bất động sản (VHM).',
    trend: 'TĂNG',
    hotSector: 'HOSE',
    volumeChange: 12.5,
  },
  addPost: (post) => set((state) => ({ posts: [post, ...state.posts] })),
  likePost: (postId) => set((state) => ({
    posts: state.posts.map(p => p.id === postId ? { ...p, likes: p.likes + 1 } : p)
  })),
  setInsights: (insights) => set({ insights }),
}));
