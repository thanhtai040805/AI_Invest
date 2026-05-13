export interface Author {
  id: string;
  name: string;
  avatar: string;
  rank: 'Elite' | 'Pro' | 'Member';
  winRate?: number;
}

export interface Comment {
  id: string;
  author: Author;
  content: string;
  likes: number;
  timestamp: string;
}

export interface Post {
  id: string;
  author: Author;
  content: string;
  timestamp: string;
  strategy?: string;
  symbol?: string;
  pnl?: number;
  status?: string;
  image?: string;
  likes: number;
  commentCount: number;
  comments?: Comment[];
}

export interface MarketInsight {
  id: string;
  title: string;
  content: string;
  trend: 'TĂNG' | 'GIẢM' | 'ĐI NGANG';
  hotSector: string;
  volumeChange: number;
}
