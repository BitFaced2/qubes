import React from 'react';
import { Tab } from '../../types';
import { useQubeSelection } from '../../hooks/useQubeSelection';

interface TabConfig {
  id: Tab;
  label: string;
}

const TABS: TabConfig[] = [
  { id: 'qubes', label: 'Dashboard' },
  { id: 'dashboard', label: 'Chat' },
  { id: 'blocks', label: 'Blocks' },
  { id: 'relationships', label: 'Relationships' },
  { id: 'skills', label: 'Skills' },
  { id: 'games', label: 'Games' },
  { id: 'economy', label: 'Wallets' },
  { id: 'settings', label: 'Settings' },
];

export const TabBar: React.FC = () => {
  const { currentTab, setCurrentTab } = useQubeSelection();

  return (
    <div className="h-20 flex items-center justify-evenly overflow-x-auto [&::-webkit-scrollbar]:hidden bg-bg-secondary border-b border-glass-border">
      {TABS.map((tab) => {
        const isActive = currentTab === tab.id;

        return (
          <button
            key={tab.id}
            onClick={() => setCurrentTab(tab.id)}
            className={`
              relative px-6 py-2
              font-display text-4xl tracking-wider
              transition-all duration-300
              ${
                isActive
                  ? 'text-accent-primary scale-110'
                  : 'text-accent-primary/40 hover:text-accent-primary/70 hover:scale-105'
              }
            `}
            style={
              isActive
                ? {
                    textShadow: '0 0 20px rgba(0, 255, 136, 0.8), 0 0 40px rgba(0, 255, 136, 0.4)',
                  }
                : undefined
            }
          >
            {tab.label}
            {isActive && (
              <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-accent-primary via-accent-secondary to-accent-primary animate-pulse" />
            )}
          </button>
        );
      })}
    </div>
  );
};
