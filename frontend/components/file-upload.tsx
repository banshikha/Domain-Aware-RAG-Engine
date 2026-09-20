'use client';

import { useState, useRef } from 'react';
import { Upload, File, X, CheckCircle2, AlertCircle } from 'lucide-react';

// ✅ NOTE: If your Python backend is running on 8000 (as shown in your logs), 
// and you are NOT using a Next.js proxy, you may need to change this to http://localhost:8000
const API_BASE = 'http://localhost:8000';

export type UploadState = 'idle' | 'uploading' | 'success' | 'error';

interface FileUploadProps {
  onFilesSelected?: (files: File[]) => void;
}

export function FileUpload({ onFilesSelected }: FileUploadProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [state, setState] = useState<UploadState>('idle');
  
  // 🚀 THE FIX: State to track which domain bucket to use
  const [selectedDomain, setSelectedDomain] = useState<string>('general');
  
  const dragRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current?.classList.add('border-primary', 'bg-primary/5');
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current?.classList.remove('border-primary', 'bg-primary/5');
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current?.classList.remove('border-primary', 'bg-primary/5');

    const droppedFiles = Array.from(e.dataTransfer.files);
    processFiles(droppedFiles);
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || []);
    processFiles(selectedFiles);
  };

  const processFiles = async (newFiles: File[]) => {
    if (newFiles.length === 0) return;

    setFiles((prev) => [...prev, ...newFiles]);
    setState('uploading');

    try {
      await Promise.all(
        newFiles.map(async (file) => {
          const formData = new FormData();
          formData.append('file', file);
          
          // Attach the selected domain to the payload (fallback)
          formData.append('domain', selectedDomain);

          const response = await fetch(`${API_BASE}/api/v1/ingest`, {
            method: 'POST',
            // 🚀 THE PROXY BYPASS: Send the domain as a custom header!
            // Next.js/proxies will forward this header directly to Python untouched.
            headers: {
              'X-Domain': selectedDomain
            },
            body: formData,
          });

          if (!response.ok) {
            throw new Error(`Upload failed for ${file.name}: ${response.status}`);
          }
        })
      );

      setState('success');
      onFilesSelected?.(newFiles);

      setTimeout(() => {
        setState('idle');
      }, 2000);
    } catch (err) {
      console.error('File upload error:', err);
      setState('error');

      setTimeout(() => {
        setState('idle');
      }, 2500);
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-4">
      
      {/* Dropdown UI for Domain Selection */}
      <div className="flex flex-col space-y-1.5">
        <label htmlFor="domain-select" className="text-sm font-medium text-zinc-300">
          Select Knowledge Domain
        </label>
        <select
          id="domain-select"
          value={selectedDomain}
          onChange={(e) => setSelectedDomain(e.target.value)}
          className="bg-zinc-900 border border-zinc-700 text-zinc-100 p-3 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all duration-200 cursor-pointer"
        >
          <option value="general">General (Standard AI)</option>
          <option value="financial">Financial (FinBERT Analyzer)</option>
          <option value="medical">Medical (Clinical AI)</option>
        </select>
      </div>

      {/* Drag and Drop Area */}
      <div
        ref={dragRef}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className="border-2 border-dashed border-zinc-700 rounded-lg p-6 text-center cursor-pointer transition-all duration-200 hover:border-blue-500 hover:bg-blue-500/5 group"
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          onChange={handleFileInput}
          accept=".pdf,.doc,.docx,.txt,.md"
          className="hidden"
        />

        <div className="flex flex-col items-center gap-3">
          {state === 'idle' && (
            <>
              <div className="p-3 rounded-lg bg-zinc-800 group-hover:bg-blue-500/10 transition-all duration-200">
                <Upload className="w-6 h-6 text-zinc-500 group-hover:text-blue-400 transition-colors duration-200" />
              </div>
              <div>
                <p className="text-sm font-semibold text-zinc-100">
                  Drag files here or click to browse
                </p>
                <p className="text-xs text-zinc-500 mt-1">
                  PDF, DOC, DOCX, TXT, MD (max 100MB each)
                </p>
              </div>
            </>
          )}

          {state === 'uploading' && (
            <>
              <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
              <p className="text-sm font-semibold text-zinc-100">Uploading...</p>
            </>
          )}

          {state === 'success' && (
            <>
              <div className="p-3 rounded-lg bg-green-500/10">
                <CheckCircle2 className="w-6 h-6 text-green-500" />
              </div>
              <p className="text-sm font-semibold text-zinc-100">
                {files.length} file{files.length !== 1 ? 's' : ''} uploaded
              </p>
            </>
          )}

          {state === 'error' && (
            <>
              <div className="p-3 rounded-lg bg-red-500/10">
                <AlertCircle className="w-6 h-6 text-red-500" />
              </div>
              <p className="text-sm font-semibold text-red-500">Upload failed</p>
            </>
          )}
        </div>
      </div>

      {/* Uploaded Files List */}
      {files.length > 0 && (
        <div className="space-y-2">
          {files.map((file, index) => (
            <div
              key={`${file.name}-${file.size}`}
              className="flex items-center gap-3 p-3 bg-zinc-900 border border-zinc-800 rounded-lg transition-all duration-200 hover:bg-zinc-800 hover:border-zinc-700 group/file"
            >
              <div className="p-1.5 rounded-md bg-blue-500/10">
                <File className="w-4 h-4 text-blue-400 flex-shrink-0" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-zinc-100 truncate">
                  {file.name}
                </p>
                <p className="text-xs text-zinc-500">
                  {(file.size / 1024 / 1024).toFixed(2)} MB
                </p>
              </div>
              <button
                onClick={() => removeFile(index)}
                className="flex-shrink-0 p-2 hover:bg-zinc-700 rounded transition-all duration-200 group-hover/file:opacity-100 opacity-60"
              >
                <X className="w-4 h-4 text-zinc-500 hover:text-zinc-300 transition-colors duration-200" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}