'use client';

import { useEffect, useRef, useState } from 'react';
import { MessageBubble } from './message-bubble';
import { Citations, Citation } from './citations';
import { ChatInput } from './chat-input';
import { Sparkles } from 'lucide-react';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  citations?: Citation[];
}

interface ChatContainerProps {
  messages: Message[];
  isLoading?: boolean;
  onSendMessage?: (message: string) => void;
}

export function ChatContainer({
  messages,
  isLoading,
  onSendMessage,
}: ChatContainerProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [hasScrolled, setHasScrolled] = useState(false);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = (content: string) => {
    onSendMessage?.(content);
    setHasScrolled(false);
  };

  return (
    <div className="flex-1 flex flex-col bg-zinc-950 overflow-hidden">
      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          // Empty State
          <div className="h-full flex flex-col items-center justify-center px-4 text-center py-8">
            <div className="space-y-8 max-w-2xl bg-zinc-900 rounded-2xl border border-zinc-800 p-6 shadow-md">
              <div className="space-y-4">
                <div className="w-16 h-16 rounded-full bg-blue-500/20 flex items-center justify-center mx-auto shadow-lg shadow-blue-500/10">
                  <Sparkles className="w-8 h-8 text-blue-400" />
                </div>
                <div>
                  <h2 className="text-3xl font-bold text-zinc-100 mb-3">
                    Welcome to Domain-Adaptive RAG
                  </h2>
                  <p className="text-zinc-400 text-sm leading-relaxed">
                    Upload documents and ask questions about financial, medical, or general topics. Get intelligent responses powered by retrieval-augmented generation.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-5 w-full">
                <div className="bg-zinc-900 rounded-xl border border-zinc-800 shadow-md hover:bg-zinc-800 hover:border-zinc-700 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 group p-5 cursor-default">
                  <div className="flex flex-col items-center gap-3">
                    <div className="p-3 rounded-lg bg-blue-500/10 group-hover:bg-blue-500/15 transition-all duration-200">
                      <span className="text-2xl">📄</span>
                    </div>
                    <p className="text-sm font-semibold text-zinc-100">
                      Upload Files
                    </p>
                    <p className="text-xs text-zinc-400 leading-relaxed">
                      Add PDFs and documents to analyze
                    </p>
                  </div>
                </div>

                <div className="bg-zinc-900 rounded-xl border border-zinc-800 shadow-md hover:bg-zinc-800 hover:border-zinc-700 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 group p-5 cursor-default">
                  <div className="flex flex-col items-center gap-3">
                    <div className="p-3 rounded-lg bg-blue-500/10 group-hover:bg-blue-500/15 transition-all duration-200">
                      <span className="text-2xl">🎯</span>
                    </div>
                    <p className="text-sm font-semibold text-zinc-100">
                      Select Domain
                    </p>
                    <p className="text-xs text-zinc-400 leading-relaxed">
                      Choose Financial, Medical, or General
                    </p>
                  </div>
                </div>

                <div className="bg-zinc-900 rounded-xl border border-zinc-800 shadow-md hover:bg-zinc-800 hover:border-zinc-700 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 group p-5 cursor-default">
                  <div className="flex flex-col items-center gap-3">
                    <div className="p-3 rounded-lg bg-blue-500/10 group-hover:bg-blue-500/15 transition-all duration-200">
                      <span className="text-2xl">✨</span>
                    </div>
                    <p className="text-sm font-semibold text-zinc-100">
                      Ask Questions
                    </p>
                    <p className="text-xs text-zinc-400 leading-relaxed">
                      Get answers with verified sources
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          // Messages List
          <div className="max-w-4xl mx-auto w-full px-4 py-8 space-y-1">
            {messages.map((message, index) => (
              <div
                key={message.id}
                className="animate-in fade-in slide-in-from-bottom-4 duration-300"
                style={{ animationDelay: `${index * 100}ms` }}
              >
                <MessageBubble
                  role={message.role}
                  content={message.content}
                  timestamp={message.timestamp}
                  isLoading={isLoading && message === messages[messages.length - 1]}
                />
                {message.role === 'assistant' && message.citations && (
                  <div className="max-w-2xl ml-11 animate-in fade-in slide-in-from-top-2 duration-300" style={{ animationDelay: `${index * 100 + 150}ms` }}>
                    <Citations citations={message.citations} />
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input Area */}
      <ChatInput onSendMessage={handleSendMessage} isLoading={isLoading} />
    </div>
  );
}
