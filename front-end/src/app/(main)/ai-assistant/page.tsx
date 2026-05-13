"use client";

import { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { ChatMessage as ChatMessageComponent } from '@/components/feature/ai/ChatMessage';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { ChatMessage } from '@/types/chat';
import { Skeleton } from 'boneyard-js/react';

export default function Page() {
  const { messages, addMessage, isTyping, setTyping } = useChatStore();
  const { isLoading } = useUIStore();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleSend = () => {
    if (!input.trim()) return;
    
    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      type: 'text',
      timestamp: new Date().toISOString()
    };
    
    addMessage(userMsg);
    setInput('');
    setTyping(true);
    
    // Simulate AI response
    setTimeout(() => {
      const assistantMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `Tôi đã nhận được yêu cầu về "${input}". Hệ thống đang xử lý phân tích dữ liệu thời gian thực cho bạn...`,
        type: 'text',
        timestamp: new Date().toISOString()
      };
      addMessage(assistantMsg);
      setTyping(false);
    }, 1500);
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="font-body-lg text-body-lg selection:bg-primary/30 min-h-full flex flex-col items-center"
    >
      <div className="w-full max-w-4xl pt-xl flex flex-col items-center">
        <div className="relative mb-lg">
          <div className="w-24 h-24 rounded-full aura-glow animate-pulse flex items-center justify-center">
            <span className="material-symbols-outlined text-display-lg text-on-primary">smart_toy</span>
          </div>
          <div className="absolute -bottom-2 -right-2 bg-secondary text-on-secondary text-[10px] font-bold px-2 py-1 rounded-full shadow-lg">LIVE</div>
        </div>
        <h1 className="font-headline-lg text-headline-lg text-primary">AIInvest Aura</h1>
        <p className="font-body-lg text-on-surface-variant opacity-80 mt-xs text-center">Hệ thống phân tích chứng khoán thời thực bậc nhất.</p>
      </div>

      <div className="flex-1 w-full max-w-4xl overflow-y-auto px-lg py-xl space-y-xl no-scrollbar">
        <Skeleton name="chat-history" loading={isLoading}>
          {messages.map((msg) => (
            <ChatMessageComponent key={msg.id} role={msg.role} content={msg.content} type={msg.type as any} />
          ))}
          {isTyping && (
            <div className="flex gap-md opacity-40 animate-pulse">
               <span className="material-symbols-outlined">more_horiz</span>
               <span className="text-xs font-bold uppercase tracking-widest">Aura đang trả lời...</span>
            </div>
          )}
        </Skeleton>
        <div ref={messagesEndRef} />
      </div>

      <div className="w-full max-w-4xl p-lg space-y-md mb-lg sticky bottom-0 bg-background/80 backdrop-blur-md">
        <div className="flex flex-wrap gap-sm justify-center">
          {['Phân tích mã VNM', 'Dự báo VN-Index', 'So sánh ngành Thép'].map(tag => (
            <button 
              key={tag} 
              onClick={() => { setInput(tag); }}
              className="px-md py-sm rounded-full glass-panel hover:border-primary/50 text-on-surface-variant text-xs font-title-md transition-all"
            >
              {tag}
            </button>
          ))}
        </div>

        <div className="relative group">
          <div className="absolute inset-0 bg-primary/10 blur-xl group-focus-within:bg-primary/20 transition-all rounded-3xl"></div>
          <div className="relative flex items-center bg-[#050505] border border-white/10 rounded-3xl p-2 px-md shadow-2xl focus-within:border-primary transition-all">
            <button className="material-symbols-outlined text-on-surface-variant hover:text-primary transition-colors p-2">attach_file</button>
            <input 
              className="flex-1 bg-transparent border-none focus:ring-0 text-on-surface font-body-lg py-4 px-md outline-none" 
              placeholder="Hỏi Aura về bất kỳ mã chứng khoán nào..." 
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            />
            <div className="flex items-center gap-sm pr-sm">
              <button className="material-symbols-outlined text-on-surface-variant hover:text-secondary transition-colors p-2" style={{"fontVariationSettings": "'FILL' 1"}}>mic</button>
              <button 
                onClick={handleSend}
                className="w-12 h-12 rounded-2xl aura-glow flex items-center justify-center hover:scale-105 active:scale-95 transition-all shadow-lg"
              >
                <span className="material-symbols-outlined text-on-primary">send</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
