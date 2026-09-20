'use client';

import { useState } from 'react';
import { Briefcase, Activity, Globe } from 'lucide-react';

export type Domain = 'financial' | 'medical' | 'general';

interface DomainSelectorProps {
  onDomainChange?: (domain: Domain) => void;
}

const domains = [
  {
    id: 'financial' as const,
    label: 'Financial',
    icon: Briefcase,
    description: 'Investment, banking, markets',
  },
  {
    id: 'medical' as const,
    label: 'Medical',
    icon: Activity,
    description: 'Healthcare, diagnosis, wellness',
  },
  {
    id: 'general' as const,
    label: 'General',
    icon: Globe,
    description: 'All other topics',
  },
];

export function DomainSelector({ onDomainChange }: DomainSelectorProps) {
  const [selected, setSelected] = useState<Domain>('general');

  const handleSelect = (domain: Domain) => {
    setSelected(domain);
    onDomainChange?.(domain);
  };

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        {domains.map(({ id, label, icon: Icon, description }) => (
          <button
            key={id}
            onClick={() => handleSelect(id)}
            className={`w-full text-left px-4 py-3 rounded-lg border-2 transition-all duration-200 group ${
              selected === id
                ? 'bg-blue-500/10 border-blue-500/50 text-zinc-100 shadow-md shadow-blue-500/10'
                : 'bg-transparent border-transparent text-zinc-300 hover:border-zinc-700 hover:bg-zinc-800/40'
            }`}
          >
            <div className="flex items-start gap-3">
              <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 transition-colors duration-200 ${
                selected === id ? 'text-blue-400' : 'text-zinc-500 group-hover:text-zinc-300'
              }`} />
              <div className="flex-1">
                <p className={`text-sm font-semibold transition-colors duration-200 ${
                  selected === id ? 'text-zinc-100' : 'text-zinc-300'
                }`}>{label}</p>
                <p className={`text-xs transition-colors duration-200 ${
                  selected === id ? 'text-zinc-400' : 'text-zinc-500'
                }`}>{description}</p>
              </div>
              {selected === id && (
                <div className="w-2 h-2 rounded-full bg-blue-400 flex-shrink-0 mt-1.5 animate-pulse" />
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
