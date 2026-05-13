export type MessageRole = 'user' | 'assistant';
export type MessageType = 'text' | 'analysis' | 'suggestion';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  type: MessageType;
  timestamp: string;
  metadata?: {
    symbol?: string;
    chartData?: any;
    recommendation?: string;
  };
}

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  lastUpdate: string;
}
