import React, { useState, useEffect, useRef, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { GlassCard } from '../glass/GlassCard';
import { GlassButton } from '../glass/GlassButton';
import { Qube, SupervisedMessage } from '../../types';
import { useAuth } from '../../hooks/useAuth';

interface SupervisedChatInterfaceProps {
  selectedQubes: Qube[];
  allQubes: Qube[];
  session: {
    session_id: string;
    participants: Array<{ commitment: string; name: string; is_local: boolean }>;
  };
  ownerCommitments: string[];  // which commitments are "owners" vs "qubes"
  onLeave: () => void;
}

const API_BASE = 'https://qube.cash/api/v2';

export const SupervisedChatInterface: React.FC<SupervisedChatInterfaceProps> = ({
  selectedQubes,
  allQubes,
  session,
  ownerCommitments,
  onLeave,
}) => {
  const { userId, password } = useAuth();

  const primaryQube = selectedQubes[0];
  const primaryCommitment = primaryQube?.commitment || '';

  // --- State ---
  const [messages, setMessages] = useState<SupervisedMessage[]>([]);
  const [ownerInput, setOwnerInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [respondingQubeName, setRespondingQubeName] = useState<string | null>(null);
  const [processingResponse, setProcessingResponse] = useState(false);
  const [alsoAskQubes, setAlsoAskQubes] = useState(false);
  const [pausedBanner, setPausedBanner] = useState<string | null>(null);
  const [showQubePicker, setShowQubePicker] = useState(false);
  const [activeLocalQubeIds, setActiveLocalQubeIds] = useState<string[]>(
    selectedQubes.map(q => q.qube_id)
  );

  // ownerPresence: tracks is_in_session per commitment
  const [ownerPresence, setOwnerPresence] = useState<Map<string, boolean>>(() => {
    const m = new Map<string, boolean>();
    ownerCommitments.forEach(c => m.set(c, true));
    return m;
  });

  // Conversation tracking for Qube messages
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationStarted, setConversationStarted] = useState(false);

  // Refs
  const wsRef = useRef<WebSocket | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const handleMessageRef = useRef<((data: any) => void) | null>(null);

  // Auto-scroll
  const scrollToBottom = useCallback(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTo({
        top: scrollContainerRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages.length, scrollToBottom]);

  // --- Helpers ---
  const getLocalQubeIds = useCallback(() => {
    return activeLocalQubeIds.join(',');
  }, [activeLocalQubeIds]);

  const getRemoteConnectionsJson = useCallback(() => {
    // For supervised, remote connections are the owner commitments that are not local
    const localCommitments = selectedQubes.map(q => q.commitment || '');
    const remoteOwners = ownerCommitments.filter(c => !localCommitments.includes(c));
    return JSON.stringify(
      remoteOwners.map(c => ({
        commitment: c,
        name: session.participants.find(p => p.commitment === c)?.name || 'Unknown',
        public_key: null,
      }))
    );
  }, [selectedQubes, ownerCommitments, session.participants]);

  const isOwnerPresent = useCallback(
    (commitment: string) => ownerPresence.get(commitment) !== false,
    [ownerPresence]
  );

  const lookupQube = useCallback(
    (commitment: string): Qube | undefined => allQubes.find(q => q.commitment === commitment),
    [allQubes]
  );

  // --- Submit block to hub ---
  const submitBlockToHub = useCallback(async (message: SupervisedMessage) => {
    if (!message.block_number) return;
    try {
      await fetch(`${API_BASE}/conversation/sessions/${session.session_id}/blocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: session.session_id,
          creator_commitment: message.sender_commitment,
          block_type: 'MESSAGE',
          content: { role: 'assistant', content: message.content },
          content_hash: '',
          creator_signature: '',
          timestamp: Math.floor(message.timestamp),
        }),
      });
    } catch (err) {
      console.error('Failed to submit block to hub:', err);
    }
  }, [session.session_id]);

  // --- Continue P2P conversation (Qube response) ---
  const continueP2PConversation = useCallback(async (convId: string) => {
    if (!userId || !password) return;

    try {
      const result = await invoke<{
        success: boolean;
        response?: any;
        error?: string;
      }>('continue_p2p_conversation', {
        userId,
        conversationId: convId,
        sessionId: session.session_id,
        localQubes: getLocalQubeIds(),
        remoteConnections: getRemoteConnectionsJson(),
        password,
      });

      if (result.success && result.response?.message) {
        const aiMessage: SupervisedMessage = {
          id: crypto.randomUUID(),
          sender_type: 'qube',
          sender_commitment: result.response.speaker_id || '',
          sender_name: result.response.speaker_name || 'Unknown',
          content: result.response.message,
          timestamp: result.response.timestamp || Date.now() / 1000,
          block_number: result.response.timestamp || undefined,
        };
        setMessages(prev => [...prev, aiMessage]);
        await submitBlockToHub(aiMessage);
      }
    } catch (err) {
      console.error('Failed to continue P2P conversation:', err);
    }
  }, [userId, password, session.session_id, getLocalQubeIds, getRemoteConnectionsJson, submitBlockToHub]);

  // --- WebSocket message handler ---
  const handleWebSocketMessage = useCallback(async (data: any) => {
    switch (data.type) {
      case 'auth_success':
        setWsConnected(true);
        break;

      case 'auth_failed':
        setError(`Authentication failed: ${data.error}`);
        break;

      case 'new_block':
      case 'block_finalized': {
        const block = data.block;
        const blockCreator = block.creator_commitment;
        const localCommitments = selectedQubes.map(q => q.commitment);

        const msg: SupervisedMessage = {
          id: `block-${block.block_number}-${block.timestamp}`,
          sender_type: 'qube',
          sender_commitment: blockCreator,
          sender_name: block.creator_name || 'Unknown',
          content: block.content?.content || '',
          timestamp: block.timestamp,
          block_number: block.block_number,
        };

        setMessages(prev => {
          if (prev.some(m => m.id === msg.id)) return prev;
          return [...prev, msg];
        });

        // If from a local qube, nothing more to do
        if (localCommitments.includes(blockCreator)) break;

        // Remote block — check if the sender's owner is still in session
        // Find which owner "owns" this qube by checking ownerPresence
        // For simplicity: if the block's owner commitment is not in ownerPresence,
        // we assume the qube is supervised by a present owner and proceed.
        const senderOwnerCommitment = ownerCommitments.find(oc => oc === blockCreator);
        if (senderOwnerCommitment && !isOwnerPresent(senderOwnerCommitment)) {
          // Suppress response — owner has left
          break;
        }

        if (conversationId && !processingResponse && userId && password) {
          setProcessingResponse(true);
          setRespondingQubeName(primaryQube?.name || null);
          try {
            await invoke('inject_p2p_block', {
              userId,
              conversationId,
              sessionId: session.session_id,
              blockData: JSON.stringify(block),
              fromCommitment: blockCreator,
              localQubes: getLocalQubeIds(),
              remoteConnections: getRemoteConnectionsJson(),
              password,
            });
            await continueP2PConversation(conversationId);
          } catch (err) {
            console.error('Failed to handle remote block:', err);
          } finally {
            setProcessingResponse(false);
            setRespondingQubeName(null);
          }
        }
        break;
      }

      case 'owner_message': {
        const ownerMsg: SupervisedMessage = {
          id: data.msg_id || crypto.randomUUID(),
          sender_type: 'owner',
          sender_commitment: data.commitment,
          sender_name: session.participants.find(p => p.commitment === data.commitment)?.name || 'Owner',
          content: data.content,
          timestamp: data.timestamp || Date.now() / 1000,
        };
        setMessages(prev => {
          if (prev.some(m => m.id === ownerMsg.id)) return prev;
          return [...prev, ownerMsg];
        });
        break;
      }

      case 'participant_left': {
        const leftCommitment = data.commitment;
        if (ownerCommitments.includes(leftCommitment)) {
          setOwnerPresence(prev => {
            const next = new Map(prev);
            next.set(leftCommitment, false);
            return next;
          });
          const leaverName =
            session.participants.find(p => p.commitment === leftCommitment)?.name || 'An owner';
          setPausedBanner(`${leaverName} left the session. Their Qubes are paused.`);
        }
        break;
      }

      case 'participant_joined': {
        const joinedCommitment = data.commitment;
        if (ownerCommitments.includes(joinedCommitment)) {
          setOwnerPresence(prev => {
            const next = new Map(prev);
            next.set(joinedCommitment, true);
            return next;
          });
          setPausedBanner(null);
        }
        break;
      }

      case 'sync_state':
        if (data.session?.blocks) {
          const synced: SupervisedMessage[] = data.session.blocks.map((b: any) => ({
            id: `block-${b.block_number}-${b.timestamp}`,
            sender_type: ownerCommitments.includes(b.creator_commitment) ? 'owner' : 'qube' as const,
            sender_commitment: b.creator_commitment,
            sender_name: b.creator_name || 'Unknown',
            content: b.content?.content || '',
            timestamp: b.timestamp,
            block_number: b.block_number,
          }));
          setMessages(synced);
        }
        break;

      default:
        break;
    }
  }, [
    selectedQubes,
    ownerCommitments,
    conversationId,
    processingResponse,
    userId,
    password,
    primaryQube,
    session,
    isOwnerPresent,
    getLocalQubeIds,
    getRemoteConnectionsJson,
    continueP2PConversation,
  ]);

  useEffect(() => {
    handleMessageRef.current = handleWebSocketMessage;
  }, [handleWebSocketMessage]);

  // --- Connect WebSocket ---
  useEffect(() => {
    const ws = new WebSocket(`wss://qube.cash/api/v2/conversation/ws/${session.session_id}`);

    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'auth',
        commitment: primaryCommitment,
        signature: '',
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (handleMessageRef.current) handleMessageRef.current(data);
      } catch (err) {
        console.error('Failed to parse WS message:', err);
      }
    };

    ws.onclose = () => setWsConnected(false);
    ws.onerror = () => setError('WebSocket connection error');

    wsRef.current = ws;

    return () => ws.close();
  }, [session.session_id, primaryCommitment]);

  // --- Send owner message ---
  const handleSendOwnerMessage = async () => {
    const text = ownerInput.trim();
    if (!text || isLoading) return;

    setOwnerInput('');
    setIsLoading(true);

    // Add optimistically to own thread
    const localMsg: SupervisedMessage = {
      id: crypto.randomUUID(),
      sender_type: 'owner',
      sender_commitment: primaryCommitment,
      sender_name: primaryQube?.name || 'Me',
      content: text,
      timestamp: Date.now() / 1000,
    };
    setMessages(prev => [...prev, localMsg]);

    // Broadcast via WebSocket
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'owner_message',
        commitment: primaryCommitment,
        content: text,
        timestamp: Date.now() / 1000,
        msg_id: localMsg.id,
      }));
    }

    // Optionally also ask local Qubes
    if (alsoAskQubes && userId && password) {
      try {
        if (!conversationStarted) {
          const result = await invoke<{
            success: boolean;
            conversation_id?: string;
            response?: any;
            error?: string;
          }>('start_p2p_conversation', {
            userId,
            localQubes: getLocalQubeIds(),
            remoteConnections: getRemoteConnectionsJson(),
            sessionId: session.session_id,
            initialPrompt: text,
            password,
          });

          if (result.success) {
            setConversationId(result.conversation_id || null);
            setConversationStarted(true);

            if (result.response?.message) {
              const aiMsg: SupervisedMessage = {
                id: crypto.randomUUID(),
                sender_type: 'qube',
                sender_commitment: result.response.speaker_id || primaryCommitment,
                sender_name: result.response.speaker_name || primaryQube?.name || 'Qube',
                content: result.response.message,
                timestamp: result.response.timestamp || Date.now() / 1000,
                block_number: result.response.timestamp || undefined,
              };
              setMessages(prev => [...prev, aiMsg]);
              await submitBlockToHub(aiMsg);

              if (selectedQubes.length > 1) {
                await continueP2PConversation(result.conversation_id || '');
              }
            }
          }
        } else {
          const result = await invoke<{
            success: boolean;
            qube_response?: any;
            error?: string;
          }>('send_p2p_user_message', {
            userId,
            conversationId: conversationId || '',
            sessionId: session.session_id,
            message: text,
            localQubes: getLocalQubeIds(),
            remoteConnections: getRemoteConnectionsJson(),
            password,
          });

          if (result.success && result.qube_response?.message) {
            const aiMsg: SupervisedMessage = {
              id: crypto.randomUUID(),
              sender_type: 'qube',
              sender_commitment: result.qube_response.speaker_id || primaryCommitment,
              sender_name: result.qube_response.speaker_name || primaryQube?.name || 'Qube',
              content: result.qube_response.message,
              timestamp: result.qube_response.timestamp || Date.now() / 1000,
              block_number: result.qube_response.timestamp || undefined,
            };
            setMessages(prev => [...prev, aiMsg]);
            await submitBlockToHub(aiMsg);
          }
        }
      } catch (err) {
        console.error('Failed to ask Qubes:', err);
      }
    }

    setIsLoading(false);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendOwnerMessage();
    }
  };

  // --- Toggle local Qube in session ---
  const toggleActiveQube = (qubeId: string) => {
    setActiveLocalQubeIds(prev =>
      prev.includes(qubeId)
        ? prev.filter(id => id !== qubeId)
        : [...prev, qubeId]
    );
  };

  // Qubes available to add (all minted, not already in activeLocalQubeIds)
  const mintedQubes = allQubes.filter(q => q.commitment && q.commitment !== 'pending_minting');

  // Group participants by owner
  const localOwnerCommitments = selectedQubes.map(q => q.commitment || '');

  return (
    <div className="flex-1 flex flex-col gap-4 h-full">
      {/* Header */}
      <GlassCard className="p-4 flex-shrink-0">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-4 flex-1 min-w-0">
            {/* WS status */}
            <div
              className={`w-3 h-3 rounded-full flex-shrink-0 ${
                wsConnected ? 'bg-accent-success animate-pulse' : 'bg-accent-danger'
              }`}
            />

            {/* Owner presence badges */}
            <div className="flex items-center gap-3 flex-wrap">
              {ownerCommitments.map(commitment => {
                const participant = session.participants.find(p => p.commitment === commitment);
                const isLocal = localOwnerCommitments.includes(commitment);
                const inSession = isOwnerPresent(commitment);
                const qubesForOwner = isLocal
                  ? activeLocalQubeIds
                      .map(id => allQubes.find(q => q.qube_id === id))
                      .filter(Boolean) as Qube[]
                  : [];

                return (
                  <div key={commitment} className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary">
                      {participant?.name || commitment.substring(0, 8) + '...'}
                    </span>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        inSession
                          ? 'bg-accent-success/20 text-accent-success border border-accent-success/30'
                          : 'bg-accent-danger/20 text-accent-danger border border-accent-danger/30'
                      }`}
                    >
                      {inSession ? 'in session' : 'left'}
                    </span>
                    {/* Qube chips */}
                    {qubesForOwner.map(q => (
                      <span
                        key={q.qube_id}
                        className="text-xs px-2 py-0.5 rounded-full border font-medium"
                        style={{
                          borderColor: q.favorite_color,
                          color: q.favorite_color,
                          backgroundColor: `${q.favorite_color}20`,
                        }}
                      >
                        {q.name}
                      </span>
                    ))}
                    {/* [+] button to add/remove local qubes */}
                    {isLocal && (
                      <div className="relative">
                        <button
                          onClick={() => setShowQubePicker(p => !p)}
                          className="w-6 h-6 rounded-full bg-glass-bg border border-glass-border text-text-secondary hover:text-text-primary text-xs flex items-center justify-center"
                          title="Add/remove Qubes"
                        >
                          +
                        </button>
                        {showQubePicker && (
                          <div className="absolute left-0 top-8 z-50 bg-bg-primary border border-glass-border rounded-lg p-3 shadow-xl min-w-[180px]">
                            <p className="text-xs text-text-tertiary mb-2">Toggle Qubes:</p>
                            {mintedQubes.map(q => (
                              <label
                                key={q.qube_id}
                                className="flex items-center gap-2 cursor-pointer py-1"
                              >
                                <input
                                  type="checkbox"
                                  checked={activeLocalQubeIds.includes(q.qube_id)}
                                  onChange={() => toggleActiveQube(q.qube_id)}
                                  className="w-3 h-3"
                                />
                                <span
                                  className="text-sm"
                                  style={{ color: q.favorite_color }}
                                >
                                  {q.name}
                                </span>
                              </label>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <GlassButton variant="danger" size="sm" onClick={onLeave} className="flex-shrink-0">
            Leave
          </GlassButton>
        </div>
      </GlassCard>

      {/* Paused banner */}
      {pausedBanner && (
        <div className="px-4 py-2 bg-accent-warning/20 border border-accent-warning/50 rounded-lg text-accent-warning text-sm text-center flex-shrink-0">
          {pausedBanner}
        </div>
      )}

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        className="flex-1 p-4 overflow-y-auto bg-bg-secondary/30 backdrop-blur-md border border-accent-secondary/20 rounded-xl"
        onClick={() => showQubePicker && setShowQubePicker(false)}
      >
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-text-tertiary text-center">
              {wsConnected ? 'Session active. Send a message to get started.' : 'Connecting...'}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map(msg => {
              const isMe = msg.sender_commitment === primaryCommitment;
              const isOwnerMsg = msg.sender_type === 'owner';
              const qubeForMsg = isOwnerMsg ? undefined : lookupQube(msg.sender_commitment);

              return (
                <div
                  key={msg.id}
                  className={`flex ${isMe ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[70%] rounded-lg p-3 border-2 ${
                      isMe
                        ? 'bg-accent-primary/20 border-accent-primary'
                        : isOwnerMsg
                          ? 'bg-glass-bg border-glass-border'
                          : 'bg-bg-tertiary'
                    }`}
                    style={
                      !isMe && !isOwnerMsg && qubeForMsg
                        ? { borderColor: qubeForMsg.favorite_color }
                        : undefined
                    }
                  >
                    {/* Sender label */}
                    <div className="flex items-center gap-2 mb-1">
                      {isOwnerMsg && (
                        <span className="text-xs font-semibold text-text-secondary bg-glass-bg px-1.5 py-0.5 rounded">
                          Owner
                        </span>
                      )}
                      <p
                        className={`text-sm font-medium ${
                          isMe
                            ? 'text-accent-primary'
                            : isOwnerMsg
                              ? 'text-text-primary'
                              : 'text-text-secondary'
                        }`}
                        style={
                          !isMe && !isOwnerMsg && qubeForMsg
                            ? { color: qubeForMsg.favorite_color }
                            : undefined
                        }
                      >
                        {msg.sender_name}
                      </p>
                    </div>

                    {/* Content */}
                    <div className="whitespace-pre-wrap break-words text-text-primary">
                      {msg.content}
                    </div>

                    {/* Footer */}
                    {!isOwnerMsg && msg.block_number !== undefined && (
                      <p className="text-text-tertiary text-xs mt-1">
                        Block #{msg.block_number}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Qube thinking indicator */}
            {processingResponse && (
              <div className="flex justify-start">
                <div className="max-w-[70%] rounded-lg p-3 border-2 bg-accent-secondary/20 border-accent-secondary">
                  <p className="text-sm font-medium text-accent-secondary mb-1">
                    {respondingQubeName || primaryQube?.name || 'Qube'} is thinking...
                  </p>
                  <div className="flex gap-1">
                    <span className="w-2 h-2 bg-accent-secondary rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-2 h-2 bg-accent-secondary rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-2 h-2 bg-accent-secondary rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-accent-danger/20 border border-accent-danger/50 rounded-lg flex-shrink-0">
          <p className="text-accent-danger text-sm">{error}</p>
        </div>
      )}

      {/* Input */}
      <GlassCard className="p-4 flex-shrink-0">
        <div className="flex gap-2 mb-2">
          <textarea
            value={ownerInput}
            onChange={e => setOwnerInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Type a message as owner..."
            className="flex-1 bg-bg-secondary text-text-primary placeholder-text-tertiary rounded-lg px-4 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-accent-secondary/50"
            rows={1}
            disabled={isLoading || !wsConnected}
          />
          <GlassButton
            variant="primary"
            onClick={handleSendOwnerMessage}
            disabled={!ownerInput.trim() || isLoading || !wsConnected}
          >
            Send
          </GlassButton>
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={alsoAskQubes}
            onChange={e => setAlsoAskQubes(e.target.checked)}
            className="w-4 h-4 rounded"
            disabled={activeLocalQubeIds.length === 0}
          />
          <span className="text-xs text-text-secondary">
            Also ask your Qubes
            {activeLocalQubeIds.length === 0 && ' (no Qubes in session)'}
          </span>
        </label>
        <p className="text-xs text-text-tertiary mt-1">
          {wsConnected
            ? `Supervised session ${session.session_id.substring(0, 8)}...`
            : 'Connecting to session...'}
        </p>
      </GlassCard>
    </div>
  );
};
