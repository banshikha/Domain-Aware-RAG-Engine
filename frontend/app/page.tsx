'use client';

import { useState, useCallback, useMemo, useEffect } from 'react';
import { useTheme } from 'next-themes';
import { ChatContainer, Message } from '@/components/chat-container';
import { ChatSidebar } from '@/components/chat-sidebar';
import { Domain } from '@/components/domain-selector';
import { Citation } from '@/components/citations';
import { Menu, X, Moon, Sun } from 'lucide-react';
import { useChatStream, StreamingMessage } from '@/hooks/useChatStream';

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<Domain>('general');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const { theme, setTheme } = useTheme();

  // ✅ FIX: hydration-safe theme rendering
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // Called by the hook when a streamed response is fully complete
  const handleMessageComplete = useCallback((message: StreamingMessage) => {
    const completedMsg: Message = {
      id: message.id,
      role: 'assistant',
      content: message.content,
      timestamp: message.timestamp,
      citations: message.citations ?? [],
    };
    setMessages((prev) => [...prev, completedMsg]);
  }, []);

  const { streamingMessage, isLoading, sendMessage, cancelStream } =
    useChatStream(handleMessageComplete);

  const handleSendMessage = useCallback(
    (content: string) => {
      const userMessage: Message = {
        id: `user-${Date.now()}`, // ✅ FIXED
        role: 'user',
        content,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, userMessage]);
      sendMessage(content, selectedDomain);
    },
    [selectedDomain, sendMessage]
  );

  const handleNewChat = () => {
    cancelStream();
    setMessages([]);
  };

  const handleClearChat = () => {
    cancelStream();
    setMessages([]);
  };

  const handleDomainChange = (domain: Domain) => {
    setSelectedDomain(domain);
  };

  // ✅ useMemo (already correct)
  const displayMessages: Message[] = useMemo(() => {
    return streamingMessage
      ? [
          ...messages,
          {
            id: streamingMessage.id,
            role: 'assistant' as const,
            content: streamingMessage.content,
            timestamp: streamingMessage.timestamp,
            citations: streamingMessage.citations ?? [],
          },
        ]
      : messages;
  }, [messages, streamingMessage]);

  return (
    <div className="flex h-screen bg-zinc-950 flex-col md:flex-row">
      {/* Mobile Header */}
      <div className="md:hidden border-b border-zinc-800 bg-zinc-900 px-4 py-3 flex items-center justify-between">
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
        >
          {mobileMenuOpen ? (
            <X className="w-5 h-5 text-zinc-100" />
          ) : (
            <Menu className="w-5 h-5 text-zinc-100" />
          )}
        </button>

        <span className="text-sm font-semibold text-zinc-100">
          Domain-Adaptive RAG
        </span>

        <button
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
        >
          {mounted ? (
            theme === 'dark' ? (
              <Sun className="w-5 h-5 text-zinc-100" />
            ) : (
              <Moon className="w-5 h-5 text-zinc-100" />
            )
          ) : (
            <div className="w-5 h-5" />
          )}
        </button>
      </div>

      {/* Sidebar */}
      <div
        className={`${
          mobileMenuOpen ? 'block' : 'hidden'
        } md:block md:w-80 border-r border-zinc-800 bg-zinc-900 text-zinc-100 flex flex-col h-[calc(100vh-56px)] md:h-screen absolute md:relative left-0 top-14 md:top-0 w-full md:w-80 z-40`}
      >
        <ChatSidebar
          onNewChat={handleNewChat}
          onClearChat={handleClearChat}
          onDomainChange={handleDomainChange}
        />
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Desktop Theme Toggle */}
        <div className="hidden md:flex items-center justify-end gap-2 px-4 py-3 border-b border-zinc-800 bg-zinc-900/50">
          <button
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            className="p-2 hover:bg-zinc-800 rounded-lg transition-colors group"
            title={
                mounted
                  ? `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`
                  : 'Switch theme'
              }
          >
            {mounted ? (
              theme === 'dark' ? (
                <Sun className="w-5 h-5 text-zinc-100 group-hover:text-blue-400" />
              ) : (
                <Moon className="w-5 h-5 text-zinc-100 group-hover:text-blue-400" />
              )
            ) : (
              <div className="w-5 h-5" />
            )}
          </button>
        </div>

        <ChatContainer
          messages={displayMessages}
          isLoading={isLoading}
          onSendMessage={handleSendMessage}
        />
      </div>

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 bg-black/50 md:hidden z-30"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}
    </div>
  );
}