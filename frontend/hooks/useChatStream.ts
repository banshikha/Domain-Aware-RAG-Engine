'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import { Citation } from '@/components/citations';

// Uses env variable in production, falls back to localhost for dev
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:3000';

export interface StreamingMessage {
  id: string;
  role: 'assistant';
  content: string;
  timestamp: Date;
  citations?: Citation[];
  isStreaming?: boolean;
}

interface UseChatStreamReturn {
  streamingMessage: StreamingMessage | null;
  isLoading: boolean;
  error: string | null;
  sendMessage: (query: string, domain: string) => Promise<void>;
  cancelStream: () => void;
}

export function useChatStream(
  onMessageComplete: (message: StreamingMessage) => void
): UseChatStreamReturn {
  const [streamingMessage, setStreamingMessage] = useState<StreamingMessage | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  const cancelStream = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cancelStream();
    };
  }, [cancelStream]);

  const sendMessage = useCallback(async (query: string, domain: string) => {
    cancelStream();

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const msgId = `ai-${Date.now()}`;

    let accumulatedContent = '';
    let citations: Citation[] = [];

    setError(null);

    const initialMsg: StreamingMessage = {
      id: msgId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      citations: [],
      isStreaming: true,
    };

    setStreamingMessage(initialMsg);
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE}/api/v1/chat/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, domain }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;

          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;

          let event: {
            type: string;
            content?: string;
            citations?: Array<{ id: string; snippet: string; score: number }>;
          };

          try {
            event = JSON.parse(jsonStr);
          } catch {
            continue;
          }

          if (event.type === 'metadata' && event.citations) {
            citations = event.citations.map((c) => ({
              id: c.id,
              title: c.id,
              snippet: c.snippet,
              relevance: c.score,
              source: c.id,
            }));

            setStreamingMessage((prev) =>
              prev ? { ...prev, citations } : prev
            );
          } else if (event.type === 'token' && event.content) {
            accumulatedContent += event.content;
            const snapshot = accumulatedContent;

            setStreamingMessage((prev) =>
              prev ? { ...prev, content: snapshot } : prev
            );
          } else if (event.type === 'done') {
            const completedMessage: StreamingMessage = {
              id: msgId,
              role: 'assistant',
              content: accumulatedContent,
              timestamp: new Date(),
              citations,
              isStreaming: false,
            };

            setStreamingMessage(null);
            setIsLoading(false);
            onMessageComplete(completedMessage);
            return;
          }
        }
      }

      const completedMessage: StreamingMessage = {
        id: msgId,
        role: 'assistant',
        content: accumulatedContent,
        timestamp: new Date(),
        citations,
        isStreaming: false,
      };

      setStreamingMessage(null);
      setIsLoading(false);
      onMessageComplete(completedMessage);

    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        setStreamingMessage(null);
        setIsLoading(false);
        return;
      }

      console.error('Stream error:', err);

      setError('Something went wrong. Please try again.');
      setStreamingMessage(null);
      setIsLoading(false);
    }
  }, [cancelStream, onMessageComplete]);

  return {
    streamingMessage,
    isLoading,
    error,
    sendMessage,
    cancelStream,
  };
}