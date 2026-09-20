'use client';

import { useState } from 'react';
import { ChevronDown, FileText } from 'lucide-react';

export interface Citation {
  id: string;
  title: string;
  snippet: string;
  relevance: number;
  source: string;
}

interface CitationsProps {
  citations: Citation[];
}

export function Citations({ citations }: CitationsProps) {
  const [expanded, setExpanded] = useState(false);

  if (!citations || citations.length === 0) {
    return null;
  }

  return (
    <div className="mt-7 pt-7 border-t border-zinc-800">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm font-bold text-zinc-100 hover:text-blue-400 transition-colors duration-200 group"
      >
        <ChevronDown
          className={`w-4 h-4 transition-all duration-200 group-hover:text-blue-400 ${expanded ? 'rotate-180' : ''}`}
        />
        <span>Sources</span>
        <span className="text-xs font-semibold text-zinc-500 bg-zinc-800 px-2 py-0.5 rounded-full">({citations.length})</span>
      </button>

      {expanded && (
        <div className="mt-5 space-y-3 animate-in fade-in slide-in-from-top-2 duration-200">
          {citations.map((citation, index) => (
            <div
              key={citation.id}
              className="bg-zinc-900 rounded-xl border border-zinc-800 shadow-md hover:bg-zinc-800 hover:border-zinc-700 hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200 p-5 group/citation"
              style={{ animationDelay: `${index * 50}ms` }}
            >
              <div className="flex items-start gap-4">
                <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center group-hover/citation:bg-blue-500/30 transition-all duration-200 shadow-sm">
                  <FileText className="w-5 h-5 text-blue-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-bold text-zinc-100 group-hover/citation:text-blue-400 transition-colors duration-200 truncate">
                    {citation.title}
                  </h4>
                  <p className="text-xs text-zinc-400 mt-2 line-clamp-2 leading-relaxed">
                    {citation.snippet}
                  </p>
                  <div className="flex items-center gap-3 mt-4 flex-wrap">
                    <span className="text-xs font-semibold text-zinc-400 bg-zinc-800 px-3 py-1.5 rounded-md">
                      {citation.source}
                    </span>
                    <div className="flex-1 min-w-[80px] flex items-center gap-2">
                      <div className="flex-1 bg-zinc-800 rounded-full h-2 overflow-hidden">
                        <div
                          className="bg-gradient-to-r from-blue-600 via-blue-500 to-blue-400 h-full rounded-full transition-all duration-200"
                          style={{ width: `${citation.relevance * 100}%` }}
                        />
                      </div>
                      <span className="text-xs font-bold text-blue-400 whitespace-nowrap">
                        {Math.round(citation.relevance * 100)}%
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
