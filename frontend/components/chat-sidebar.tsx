'use client';

import { Plus, Trash2 } from 'lucide-react';
import { DomainSelector, Domain } from './domain-selector';
import { FileUpload } from './file-upload';
import { Button } from '@/components/ui/button';

interface ChatSidebarProps {
  onNewChat?: () => void;
  onClearChat?: () => void;
  onDomainChange?: (domain: Domain) => void;
  onFilesSelected?: (files: File[]) => void; // ✅ ADDED
}

export function ChatSidebar({
  onNewChat,
  onClearChat,
  onDomainChange,
  onFilesSelected, // ✅ ADDED
}: ChatSidebarProps) {
  return (
    <div className="w-full md:w-80 bg-zinc-900 text-zinc-100 flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-zinc-800">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-blue-500 flex items-center justify-center shadow-sm">
            <span className="text-sm font-bold text-white">RA</span>
          </div>
          <div className="flex-1">
            <h1 className="text-sm font-bold text-zinc-100">
              RAG Assistant
            </h1>
            <p className="text-xs text-zinc-500">Powered by AI</p>
          </div>
        </div>

        <Button
          onClick={onNewChat}
          className="w-full bg-blue-600 text-white hover:bg-blue-700 gap-2 shadow-md hover:shadow-lg hover:-translate-y-0.5 transition-all duration-200"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* Domain Section */}
        <div className="space-y-3">
          <div className="flex items-center justify-between px-2">
            <h3 className="text-xs font-bold text-zinc-100 uppercase tracking-widest">
              Domain
            </h3>
          </div>
          <div className="rounded-xl bg-zinc-900 p-4 border border-zinc-800 shadow-md hover:shadow-lg hover:border-zinc-700 transition-all duration-200">
            <DomainSelector onDomainChange={onDomainChange} />
          </div>
        </div>

        {/* Upload Section */}
        <div className="space-y-3">
          <h3 className="text-xs font-bold text-zinc-100 uppercase tracking-widest px-2">
            Documents
          </h3>
          <div className="rounded-xl bg-zinc-900 p-4 border border-zinc-800 shadow-md hover:shadow-lg hover:border-zinc-700 transition-all duration-200">
            {/* ✅ FIX: pass callback down */}
            <FileUpload onFilesSelected={onFilesSelected} />
          </div>
        </div>

        {/* Quick Tips */}
        <div className="mt-8 space-y-3">
          <h3 className="text-xs font-bold text-zinc-100 uppercase tracking-widest px-2">
            Quick Start
          </h3>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-3 shadow-md">
            <div className="flex gap-3 text-xs">
              <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-500/20 flex items-center justify-center">
                <span className="text-xs font-bold text-blue-400">1</span>
              </div>
              <p className="text-zinc-300 pt-0.5">Upload PDFs and documents</p>
            </div>
            <div className="flex gap-3 text-xs">
              <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-500/20 flex items-center justify-center">
                <span className="text-xs font-bold text-blue-400">2</span>
              </div>
              <p className="text-zinc-300 pt-0.5">Select your domain</p>
            </div>
            <div className="flex gap-3 text-xs">
              <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-500/20 flex items-center justify-center">
                <span className="text-xs font-bold text-blue-400">3</span>
              </div>
              <p className="text-zinc-300 pt-0.5">Review citations</p>
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-zinc-800 p-4 space-y-2">
        <Button
          onClick={onClearChat}
          variant="outline"
          className="w-full border-zinc-800 text-zinc-100 hover:bg-zinc-800 gap-2 shadow-md hover:shadow-lg transition-all duration-200"
        >
          <Trash2 className="w-4 h-4" />
          Clear Chat
        </Button>
      </div>
    </div>
  );
}