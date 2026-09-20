'use client';

import { CheckCheck } from 'lucide-react';

interface MessageBubbleProps {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: Date;
  isLoading?: boolean;
}

export function MessageBubble({
  role,
  content,
  timestamp,
  isLoading,
}: MessageBubbleProps) {
  const isUser = role === 'user';

  return (
    <div
      className={`flex gap-3 mb-6 transition-all duration-200 ${
        isUser ? 'justify-end' : 'justify-start'
      }`}
    >
      {!isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-blue-500 flex items-center justify-center shadow-md">
          <span className="text-xs font-bold text-white">AI</span>
        </div>
      )}

      <div
        className={`max-w-2xl px-5 py-4 rounded-lg transition-all duration-200 ${
          isUser
            ? 'bg-zinc-800 text-zinc-100 rounded-br-none shadow-md hover:shadow-lg hover:-translate-y-0.5'
            : 'bg-zinc-900 border border-zinc-800 text-zinc-100 rounded-bl-none shadow-md hover:shadow-lg hover:border-zinc-700 hover:-translate-y-0.5'
        }`}
      >
        {/* ✅ FIX: Only render text when content exists */}
        {content.length > 0 && (
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-zinc-100">
            {content}
          </p>
        )}

        {/* ✅ FIX: Show timestamp only when NOT streaming */}
        {timestamp && !isLoading && (
          <p
            className={`text-xs mt-2 opacity-70 ${
              isUser ? 'text-zinc-300' : 'text-zinc-500'
            }`}
          >
            {timestamp.toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </p>
        )}

        {/* ✅ FIX: Show loader ONLY when no content yet */}
        {isLoading && content.length === 0 && (
          <div className="flex gap-1 mt-3">
            <div
              className="w-2 h-2 rounded-full bg-current animate-bounce"
              style={{ animationDelay: '0ms' }}
            />
            <div
              className="w-2 h-2 rounded-full bg-current animate-bounce"
              style={{ animationDelay: '150ms' }}
            />
            <div
              className="w-2 h-2 rounded-full bg-current animate-bounce"
              style={{ animationDelay: '300ms' }}
            />
          </div>
        )}
      </div>

      {isUser && (
        <div className="flex-shrink-0 pt-1 text-muted-foreground opacity-70">
          <CheckCheck className="w-4 h-4" />
        </div>
      )}
    </div>
  );
}