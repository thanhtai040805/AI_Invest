import { create } from 'zustand';
import { ChatMessage } from '@/types/chat';

interface ChatState {
  messages: ChatMessage[];
  isTyping: boolean;
  addMessage: (message: ChatMessage) => void;
  clearChat: () => void;
  setTyping: (isTyping: boolean) => void;
  updateMessage: (id: string, newContent: string) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [
    { 
      id: '1',
      role: 'assistant', 
      content: 'Chào bạn! Dựa trên dữ liệu thị trường hiện tại và danh mục ưu tiên của bạn, tôi đã thực hiện một phân tích nhanh về mã VNM (Vinamilk). Dưới đây là nhận định chuyên sâu:', 
      type: 'analysis',
      timestamp: new Date().toISOString()
    }
  ],
  isTyping: false,
  addMessage: (message) => set((state) => ({ 
    messages: [...state.messages, message] 
  })),
  clearChat: () => set({ messages: [] }),
  setTyping: (isTyping) => set({ isTyping }),
  updateMessage: (id, newContent) => set((state) => ({
    messages: state.messages.map(msg => 
      msg.id === id ? { ...msg, content: newContent } : msg
    )
  })),
}));
