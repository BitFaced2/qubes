import { useState, useEffect, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { GlassCard, GlassButton } from '../glass';
import { useAuth } from '../../hooks/useAuth';
import { useQubeSelection } from '../../hooks/useQubeSelection';
import { Qube } from '../../types';
import { PendingRequests } from './PendingRequests';
import { ConnectionsList } from './ConnectionsList';
import { DiscoveryBrowser } from './DiscoveryBrowser';

interface ConnectionManagerProps {
  qubes: Qube[];
  selectedQubeOverride?: Qube;  // When provided, bypasses tab-based selection
}

export interface PendingIntroduction {
  relay_id: string;
  from_commitment: string;
  from_name: string;
  conversation_id: string;
  block_hash: string;
}

export interface Connection {
  commitment: string;
  name: string;
  accepted_at: string;
  qube_id?: string;
  is_online?: boolean;
  public_key?: string;
}

type TabType = 'connections' | 'pending' | 'discover';

// Stable empty array to avoid infinite loops with Zustand selectors
const EMPTY_ARRAY: string[] = [];

export const ConnectionManager: React.FC<ConnectionManagerProps> = ({ qubes, selectedQubeOverride }) => {
  const { userId, password } = useAuth();
  const selectedQubeIds = useQubeSelection((state) => state.selectionByTab['connections'] ?? EMPTY_ARRAY);
  const [activeTab, setActiveTab] = useState<TabType>('connections');
  const [connections, setConnections] = useState<Connection[]>([]);
  const [pendingIntroductions, setPendingIntroductions] = useState<PendingIntroduction[]>([]);
  const [onlineQubes, setOnlineQubes] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Get selected qube: use override if provided, otherwise tab-based selection
  const selectedQube = selectedQubeOverride ?? qubes.find(q => selectedQubeIds.includes(q.qube_id));
  const isMinted = selectedQube?.nft_category_id && selectedQube.nft_category_id !== 'pending_minting';

  // Fetch connections for selected qube
  const fetchConnections = useCallback(async () => {
    if (!selectedQube || !userId) return;

    try {
      const result = await invoke<{ success: boolean; connections?: Connection[]; error?: string }>(
        'get_connections',
        { userId, qubeId: selectedQube.qube_id }
      );

      if (result.success && result.connections) {
        setConnections(result.connections);
      }
    } catch (err) {
      console.error('Failed to fetch connections:', err);
    }
  }, [selectedQube, userId]);

  // Fetch pending introductions
  const fetchPendingIntroductions = useCallback(async () => {
    if (!selectedQube || !userId || !password || !isMinted) return;

    try {
      const result = await invoke<{ success: boolean; pending?: PendingIntroduction[]; error?: string }>(
        'get_pending_introductions',
        { userId, qubeId: selectedQube.qube_id, password }
      );

      if (result.success && result.pending) {
        setPendingIntroductions(result.pending);
      }
    } catch (err) {
      console.error('Failed to fetch pending introductions:', err);
    }
  }, [selectedQube, userId, password, isMinted]);

  // Fetch online qubes
  const fetchOnlineQubes = useCallback(async () => {
    if (!userId) return;

    try {
      const result = await invoke<{ success: boolean; online?: string[]; error?: string }>(
        'get_online_qubes',
        { userId }
      );

      if (result.success && result.online) {
        setOnlineQubes(result.online);
      }
    } catch (err) {
      console.error('Failed to fetch online qubes:', err);
    }
  }, [userId]);

  // Initial fetch and polling
  useEffect(() => {
    fetchConnections();
    fetchPendingIntroductions();
    fetchOnlineQubes();

    // Poll for updates every 30 seconds
    const interval = setInterval(() => {
      fetchPendingIntroductions();
      fetchOnlineQubes();
    }, 30000);

    return () => clearInterval(interval);
  }, [fetchConnections, fetchPendingIntroductions, fetchOnlineQubes]);

  // Handle accepting introduction
  const handleAcceptIntroduction = async (relayId: string) => {
    if (!selectedQube || !userId || !password) return;

    setLoading(true);
    setError(null);

    try {
      const result = await invoke<{ success: boolean; from_name?: string; error?: string }>(
        'accept_introduction',
        { userId, qubeId: selectedQube.qube_id, relayId, password }
      );

      if (result.success) {
        // Refresh lists
        await fetchConnections();
        await fetchPendingIntroductions();
      } else {
        setError(result.error || 'Failed to accept introduction');
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  // Handle rejecting introduction
  const handleRejectIntroduction = async (relayId: string) => {
    if (!selectedQube || !userId || !password) return;

    setLoading(true);
    setError(null);

    try {
      const result = await invoke<{ success: boolean; error?: string }>(
        'reject_introduction',
        { userId, qubeId: selectedQube.qube_id, relayId, password }
      );

      if (result.success) {
        await fetchPendingIntroductions();
      } else {
        setError(result.error || 'Failed to reject introduction');
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  // Handle sending introduction
  const handleSendIntroduction = async (toCommitment: string, message: string) => {
    if (!selectedQube || !userId || !password) return;

    setLoading(true);
    setError(null);

    try {
      const result = await invoke<{ success: boolean; relay_id?: string; error?: string }>(
        'send_introduction',
        { userId, qubeId: selectedQube.qube_id, toCommitment, message, password }
      );

      if (result.success) {
        // Show success message
        setActiveTab('pending');
      } else {
        setError(result.error || 'Failed to send introduction');
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!selectedQube) {
    return (
      <div className="p-6">
        <GlassCard className="p-6 text-center">
          <p className="text-text-secondary">
            Select a Qube from the roster to manage connections
          </p>
        </GlassCard>
      </div>
    );
  }

  if (!isMinted) {
    return (
      <div className="p-6">
        <GlassCard className="p-6 text-center">
          <h2 className="text-xl font-display text-accent-warning mb-4">
            NFT Required
          </h2>
          <p className="text-text-secondary mb-4">
            {selectedQube.name} must be minted as an NFT before connecting with other Qubes.
          </p>
          <p className="text-sm text-text-tertiary">
            Go to the Qubes tab and click "Mint NFT" to get started.
          </p>
        </GlassCard>
      </div>
    );
  }

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-display text-text-primary">
            Connections for {selectedQube.name}
          </h2>
          <p className="text-sm text-text-tertiary">
            {connections.length} connection{connections.length !== 1 ? 's' : ''} |{' '}
            {pendingIntroductions.length} pending request{pendingIntroductions.length !== 1 ? 's' : ''}
          </p>
        </div>

        <GlassButton
          onClick={() => {
            fetchConnections();
            fetchPendingIntroductions();
            fetchOnlineQubes();
          }}
          disabled={loading}
        >
          Refresh
        </GlassButton>
      </div>

      {/* Error display */}
      {error && (
        <div className="mb-4 p-3 bg-accent-danger/20 border border-accent-danger/50 rounded-lg">
          <p className="text-accent-danger text-sm">{error}</p>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 mb-4">
        <GlassButton
          variant={activeTab === 'connections' ? 'primary' : 'secondary'}
          onClick={() => setActiveTab('connections')}
          className="flex-1"
        >
          Connections ({connections.length})
        </GlassButton>
        <GlassButton
          variant={activeTab === 'pending' ? 'primary' : 'secondary'}
          onClick={() => setActiveTab('pending')}
          className="flex-1"
        >
          Pending ({pendingIntroductions.length})
        </GlassButton>
        <GlassButton
          variant={activeTab === 'discover' ? 'primary' : 'secondary'}
          onClick={() => setActiveTab('discover')}
          className="flex-1"
        >
          Discover
        </GlassButton>
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'connections' && (
          <ConnectionsList
            connections={connections}
            onlineQubes={onlineQubes}
          />
        )}

        {activeTab === 'pending' && (
          <PendingRequests
            pending={pendingIntroductions}
            onAccept={handleAcceptIntroduction}
            onReject={handleRejectIntroduction}
            loading={loading}
            qubeId={selectedQube.qube_id}
          />
        )}

        {activeTab === 'discover' && (
          <DiscoveryBrowser
            onSendIntroduction={handleSendIntroduction}
            onlineQubes={onlineQubes}
            existingConnections={connections.map(c => c.commitment)}
            ownCommitment={selectedQube.commitment || ''}
            loading={loading}
            qubeId={selectedQube.qube_id}
          />
        )}
      </div>
    </div>
  );
};
