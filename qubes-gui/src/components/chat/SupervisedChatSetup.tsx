import React, { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { GlassCard } from '../glass/GlassCard';
import { GlassButton } from '../glass/GlassButton';
import { Qube } from '../../types';
import { useAuth } from '../../hooks/useAuth';
import { Connection } from '../connections';

interface SupervisedChatSetupProps {
  selectedQubes: Qube[];   // local qubes (for Contact ID and qube selection)
  allQubes: Qube[];
  onStartSession: (selectedOwnerCommitments: string[], localQubeIds: string[]) => void;
  onBack: () => void;      // back to standard P2P
  loading: boolean;
}

export const SupervisedChatSetup: React.FC<SupervisedChatSetupProps> = ({
  selectedQubes,
  allQubes,
  onStartSession,
  onBack,
  loading,
}) => {
  const { userId } = useAuth();

  const primaryQube = selectedQubes[0];
  const contactId = primaryQube?.commitment || '';

  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedOwnerCommitments, setSelectedOwnerCommitments] = useState<string[]>([]);
  const [selectedLocalQubeIds, setSelectedLocalQubeIds] = useState<string[]>([]);
  const [inviteInput, setInviteInput] = useState('');
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);

  // Fetch connections for primary qube
  useEffect(() => {
    const fetchConnections = async () => {
      if (!primaryQube || !userId) return;

      try {
        const result = await invoke<{ success: boolean; connections?: Connection[]; error?: string }>(
          'get_connections',
          { userId, qubeId: primaryQube.qube_id }
        );

        if (result.success && result.connections) {
          setConnections(result.connections);
        }
      } catch (err) {
        console.error('Failed to fetch connections:', err);
      }
    };

    fetchConnections();
  }, [primaryQube, userId]);

  const handleCopyContactId = async () => {
    if (!contactId) return;
    try {
      await navigator.clipboard.writeText(contactId);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const handleInvite = () => {
    const value = inviteInput.trim();
    if (!value) return;

    setInviteError(null);

    // Detect: 64 hex chars = qube commitment
    if (/^[0-9a-fA-F]{64}$/.test(value)) {
      if (!selectedOwnerCommitments.includes(value)) {
        setSelectedOwnerCommitments(prev => [...prev, value]);
      }
      setInviteInput('');
    } else {
      // BCH address — for future BCMR lookup; for now note it for the user
      setInviteError('BCH address lookup is coming in Phase 3. Paste a 64-character Qube commitment for now.');
    }
  };

  const toggleOwnerCommitment = (commitment: string) => {
    setSelectedOwnerCommitments(prev =>
      prev.includes(commitment)
        ? prev.filter(c => c !== commitment)
        : [...prev, commitment]
    );
  };

  const toggleLocalQube = (qubeId: string) => {
    setSelectedLocalQubeIds(prev =>
      prev.includes(qubeId)
        ? prev.filter(id => id !== qubeId)
        : [...prev, qubeId]
    );
  };

  // Filter connections: exclude local qubes
  const localCommitments = selectedQubes.map(q => q.commitment || '');
  const remoteConnections = connections.filter(
    conn => !localCommitments.includes(conn.commitment)
  );

  // Minted qubes available for the "Your Qubes" section
  const mintedQubes = allQubes.filter(q => q.commitment && q.commitment !== 'pending_minting');

  const canStart = selectedOwnerCommitments.length > 0;

  return (
    <div className="flex-1 flex flex-col gap-4 h-full overflow-y-auto">
      {/* Card 1: Your Contact ID */}
      <GlassCard className="p-4 flex-shrink-0">
        <h3 className="text-lg font-display text-text-primary mb-2">Your Contact ID</h3>
        <p className="text-xs text-text-tertiary mb-3">
          Share this with the other owner so they can find you.
        </p>
        <div className="flex items-center gap-2">
          <span className="flex-1 text-xs font-mono text-text-secondary bg-bg-tertiary rounded px-3 py-2 truncate">
            {contactId || 'No minted Qube selected'}
          </span>
          <GlassButton
            variant="secondary"
            size="sm"
            onClick={handleCopyContactId}
            disabled={!contactId}
          >
            {copyFeedback ? 'Copied!' : 'Copy'}
          </GlassButton>
        </div>
      </GlassCard>

      {/* Card 2: Invite an Owner */}
      <GlassCard className="p-4 flex-shrink-0">
        <h3 className="text-lg font-display text-text-primary mb-2">Invite an Owner</h3>

        {/* Direct commitment/address input */}
        <div className="flex gap-2 mb-3">
          <input
            type="text"
            value={inviteInput}
            onChange={e => setInviteInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleInvite(); }}
            placeholder="Paste BCH address or Qube commitment"
            className="flex-1 bg-bg-secondary text-text-primary placeholder-text-tertiary rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent-secondary/50"
          />
          <GlassButton variant="secondary" size="sm" onClick={handleInvite}>
            Invite
          </GlassButton>
        </div>

        {inviteError && (
          <p className="text-accent-warning text-xs mb-2">{inviteError}</p>
        )}

        {/* Selected from invite input */}
        {selectedOwnerCommitments.filter(c => !remoteConnections.find(r => r.commitment === c)).length > 0 && (
          <div className="mb-3">
            <p className="text-xs text-text-tertiary mb-1">Added via commitment:</p>
            {selectedOwnerCommitments
              .filter(c => !remoteConnections.find(r => r.commitment === c))
              .map(commitment => (
                <div
                  key={commitment}
                  className="flex items-center justify-between p-2 rounded-lg bg-accent-secondary/10 border border-accent-secondary/30 mb-1"
                >
                  <span className="text-xs font-mono text-text-secondary truncate">
                    {commitment.substring(0, 16)}...
                  </span>
                  <button
                    onClick={() => toggleOwnerCommitment(commitment)}
                    className="text-accent-danger text-xs hover:text-accent-danger/80 ml-2 flex-shrink-0"
                  >
                    Remove
                  </button>
                </div>
              ))}
          </div>
        )}

        {/* Connections list */}
        {remoteConnections.length > 0 ? (
          <div>
            <p className="text-xs text-text-tertiary mb-2">Your connections:</p>
            <div className="space-y-2">
              {remoteConnections.map(conn => (
                <div
                  key={conn.commitment}
                  className={`p-3 rounded-lg border-2 cursor-pointer transition-all ${
                    selectedOwnerCommitments.includes(conn.commitment)
                      ? 'bg-accent-secondary/20 border-accent-secondary'
                      : 'bg-bg-tertiary border-glass-border hover:border-accent-secondary/50'
                  }`}
                  onClick={() => toggleOwnerCommitment(conn.commitment)}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-text-primary font-medium text-sm">{conn.name}</p>
                      <p className="text-xs text-text-tertiary font-mono">
                        {conn.commitment.substring(0, 16)}...
                      </p>
                    </div>
                    {selectedOwnerCommitments.includes(conn.commitment) && (
                      <span className="text-accent-secondary text-xl">✓</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-sm text-text-tertiary">
            No connections yet — paste a commitment above to invite an owner directly.
          </p>
        )}
      </GlassCard>

      {/* Card 3: Your Qubes (optional) */}
      <GlassCard className="p-4 flex-shrink-0">
        <h3 className="text-lg font-display text-text-primary mb-1">Your Qubes (optional)</h3>
        <p className="text-xs text-text-tertiary mb-3">
          Add your Qubes to the session. Leave unchecked for an owner-only DM.
        </p>
        {mintedQubes.length > 0 ? (
          <div className="space-y-2">
            {mintedQubes.map(qube => (
              <label
                key={qube.qube_id}
                className="flex items-center gap-3 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={selectedLocalQubeIds.includes(qube.qube_id)}
                  onChange={() => toggleLocalQube(qube.qube_id)}
                  className="w-4 h-4 rounded"
                />
                <span
                  className="text-sm font-medium"
                  style={{ color: qube.favorite_color }}
                >
                  {qube.name}
                </span>
                <span className="text-xs text-text-tertiary font-mono">
                  {qube.commitment?.substring(0, 8)}...
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-sm text-text-tertiary">No minted Qubes available to add.</p>
        )}
      </GlassCard>

      {/* Start Button + Back */}
      <GlassCard className="p-4 flex-shrink-0">
        <div className="flex gap-2">
          <GlassButton variant="secondary" onClick={onBack} className="flex-shrink-0">
            Back
          </GlassButton>
          <GlassButton
            variant="primary"
            onClick={() => onStartSession(selectedOwnerCommitments, selectedLocalQubeIds)}
            disabled={loading || !canStart}
            className="flex-1"
          >
            {loading ? 'Creating Session...' : 'Start Supervised Session'}
          </GlassButton>
        </div>
        {!canStart && (
          <p className="text-xs text-text-tertiary mt-2 text-center">
            Select at least one owner to invite before starting.
          </p>
        )}
      </GlassCard>
    </div>
  );
};
