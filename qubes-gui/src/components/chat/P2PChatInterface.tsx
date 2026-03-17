import React, { useState, useEffect, useRef, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { convertFileSrc } from '@tauri-apps/api/core';
import { GlassCard } from '../glass/GlassCard';
import { GlassButton } from '../glass/GlassButton';
import { Qube } from '../../types';
import { useAuth } from '../../hooks/useAuth';
import { Connection, ConnectionManager } from '../connections';
import { SupervisedChatSetup } from './SupervisedChatSetup';
import { SupervisedChatInterface } from './SupervisedChatInterface';

interface P2PChatInterfaceProps {
  selectedQubes: Qube[];  // Local qubes to participate
  allQubes: Qube[];       // All qubes owned by user (to detect local "remote" connections)
  onBack: () => void;     // Go back to mode selection
}

interface P2PMessage {
  block_number: number;
  creator_commitment: string;
  creator_name: string;
  content: {
    role: string;
    content: string;
  };
  timestamp: number;
  signatures: string[];
}

interface P2PSession {
  session_id: string;
  participants: Array<{
    commitment: string;
    name: string;
    is_local: boolean;
  }>;
  state: string;
}

const API_BASE = 'https://qube.cash/api/v2';

export const P2PChatInterface: React.FC<P2PChatInterfaceProps> = ({ selectedQubes, allQubes, onBack }) => {
  const { userId, password } = useAuth();

  // Sub-view: session setup vs connections/discovery management
  const [p2pSubView, setP2pSubView] = useState<'session' | 'connections'>('session');
  // Session type state
  const [sessionType, setSessionType] = useState<'standard' | 'supervised'>('standard');
  // Owner commitments for supervised sessions
  const [ownerCommitments, setOwnerCommitments] = useState<string[]>([]);

  // State
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedConnections, setSelectedConnections] = useState<string[]>([]);
  const [session, setSession] = useState<P2PSession | null>(null);
  const [messages, setMessages] = useState<P2PMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [respondingQubeName, setRespondingQubeName] = useState<string | null>(null);
  // Track P2P conversation ID (separate from hub session ID)
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationStarted, setConversationStarted] = useState(false);

  // Refs
  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const handleMessageRef = useRef<((data: any) => void) | null>(null);

  // Get the primary qube (first selected)
  const primaryQube = selectedQubes[0];
  const primaryCommitment = primaryQube?.commitment;

  // Helper to build remote connections array for backend
  const getRemoteConnectionsJson = useCallback(() => {
    const localCommitments = selectedQubes.map(q => q.commitment || '');
    const remoteConns = selectedConnections
      .filter(c => !localCommitments.includes(c))
      .map(commitment => {
        const conn = connections.find(c => c.commitment === commitment);
        return {
          commitment,
          name: conn?.name || 'Unknown',
          public_key: conn?.public_key || null,
        };
      });
    return JSON.stringify(remoteConns);
  }, [selectedQubes, selectedConnections, connections]);

  // Helper to get local qube IDs string
  const getLocalQubeIds = useCallback(() => {
    return selectedQubes.map(q => q.qube_id).join(',');
  }, [selectedQubes]);

  // Scroll to bottom
  const scrollToBottom = useCallback(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTo({
        top: scrollContainerRef.current.scrollHeight,
        behavior: 'smooth'
      });
    }
  }, []);

  // Auto-scroll on new messages
  useEffect(() => {
    scrollToBottom();
  }, [messages.length, scrollToBottom]);

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

  // Connect to session WebSocket
  const connectWebSocket = useCallback((sessionId: string) => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    const ws = new WebSocket(`wss://qube.cash/api/v2/conversation/ws/${sessionId}`);

    ws.onopen = () => {
      // Send auth message (server expects 'auth' type, not 'join')
      ws.send(JSON.stringify({
        type: 'auth',
        commitment: primaryCommitment,
        signature: '' // TODO: Sign with qube's private key
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Use ref to always call latest handler
        if (handleMessageRef.current) {
          handleMessageRef.current(data);
        }
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    };

    ws.onclose = () => {
      setWsConnected(false);
    };

    ws.onerror = (err) => {
      console.error('P2P WebSocket error:', err);
      setError('WebSocket connection error');
    };

    wsRef.current = ws;
  }, [primaryCommitment, primaryQube?.name]);

  // State for tracking AI responses
  const [processingResponse, setProcessingResponse] = useState(false);

  // Handle WebSocket messages - now using backend commands for responses
  const handleWebSocketMessage = useCallback(async (data: any) => {
    switch (data.type) {
      case 'auth_success':
        setWsConnected(true);
        break;

      case 'auth_failed':
        console.error('P2P WebSocket auth failed:', data.error);
        setError(`Authentication failed: ${data.error}`);
        break;

      case 'new_block':
      case 'block_finalized':
        // New message from a participant
        const block = data.block;
        const blockCreatorCommitment = block.creator_commitment;

        // Check if this is from one of our local Qubes (we already have it)
        const localCommitments = selectedQubes.map(q => q.commitment);
        if (localCommitments.includes(blockCreatorCommitment)) {
          // We created this block - just update display if needed
          setMessages(prev => {
            if (prev.some(m => m.timestamp === block.timestamp)) {
              return prev;
            }
            return [...prev, block];
          });
          break;
        }

        // Remote block - add to display
        setMessages(prev => {
          if (prev.some(m => m.block_number === block.block_number && m.timestamp === block.timestamp)) {
            return prev;
          }
          return [...prev, block];
        });

        // If conversation is started, inject the remote block and continue
        if (conversationId && !processingResponse && session && userId && password) {
          setProcessingResponse(true);
          setRespondingQubeName(primaryQube?.name || null);

          try {
            // Inject the remote block into local sessions
            await invoke('inject_p2p_block', {
              userId,
              conversationId,
              sessionId: session.session_id,
              blockData: JSON.stringify(block),
              fromCommitment: blockCreatorCommitment,
              localQubes: getLocalQubeIds(),
              remoteConnections: getRemoteConnectionsJson(),
              password,
            });

            // Continue conversation to get our local Qube's response
            await continueP2PConversation(conversationId);
          } catch (err) {
            console.error('Failed to handle remote block:', err);
          } finally {
            setProcessingResponse(false);
            setRespondingQubeName(null);
          }
        }
        break;

      case 'sync_state':
        // Full state sync (reconnection)
        if (data.session?.blocks) {
          setMessages(data.session.blocks);
        }
        break;

      case 'participant_joined':
        break;

      case 'participant_left':
        break;
    }
  }, [selectedQubes, conversationId, processingResponse, session, userId, password, primaryQube, getLocalQubeIds, getRemoteConnectionsJson]);

  // Keep the message handler ref updated
  useEffect(() => {
    handleMessageRef.current = handleWebSocketMessage;
  }, [handleWebSocketMessage]);

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  // Toggle connection selection
  const toggleConnection = (commitment: string) => {
    setSelectedConnections(prev =>
      prev.includes(commitment)
        ? prev.filter(c => c !== commitment)
        : [...prev, commitment]
    );
  };

  // Start P2P session
  const handleStartSession = async () => {
    if (!primaryQube || !userId || !password || selectedConnections.length === 0) {
      setError('Please select at least one connection');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const result = await invoke<{
        success: boolean;
        session_id?: string;
        participants?: any[];
        error?: string
      }>(
        'create_p2p_session',
        {
          userId,
          qubeId: primaryQube.qube_id,
          localQubes: selectedQubes.map(q => q.qube_id).join(','),
          remoteCommitments: selectedConnections.join(','),
          topic: '',
          password
        }
      );

      if (result.success && result.session_id) {
        // Get local commitments to avoid duplicates
        const localCommitments = selectedQubes.map(q => q.commitment || '');

        // Build participant list (deduplicated)
        const participants = [
          // Local qubes
          ...selectedQubes.map(q => ({
            commitment: q.commitment || '',
            name: q.name,
            is_local: true
          })),
          // Remote connections (exclude any that match local qubes)
          ...selectedConnections
            .filter(commitment => !localCommitments.includes(commitment))
            .map(commitment => {
              const conn = connections.find(c => c.commitment === commitment);
              return {
                commitment,
                name: conn?.name || 'Unknown',
                is_local: false
              };
            })
        ];

        setSession({
          session_id: result.session_id,
          participants,
          state: 'active'
        });

        // Connect to WebSocket
        connectWebSocket(result.session_id);
      } else {
        setError(result.error || 'Failed to create session');
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setIsLoading(false);
    }
  };

  // Start supervised session
  const handleStartSupervisedSession = async (
    selectedOwnerCommitments: string[],
    localQubeIds: string[]
  ) => {
    if (!primaryQube || !userId || !password || selectedOwnerCommitments.length === 0) {
      setError('Please select at least one owner');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Combine all remote commitments (owner commitments + any extra local qubes selected)
      const allRemoteCommitments = [...selectedOwnerCommitments];

      // Build local qubes list: primaryQube + any additional localQubeIds selected
      const allLocalQubeIds = [
        primaryQube.qube_id,
        ...localQubeIds.filter(id => id !== primaryQube.qube_id),
        ...selectedQubes.map(q => q.qube_id).filter(id => id !== primaryQube.qube_id),
      ].filter((id, idx, arr) => arr.indexOf(id) === idx);

      const result = await invoke<{
        success: boolean;
        session_id?: string;
        participants?: any[];
        error?: string;
      }>(
        'create_supervised_session',
        {
          userId,
          qubeId: primaryQube.qube_id,
          ownerCommitments: selectedOwnerCommitments.join(','),
          localQubes: allLocalQubeIds.join(','),
          remoteCommitments: allRemoteCommitments.join(','),
          topic: 'supervised',
          password,
        }
      );

      if (result.success && result.session_id) {
        const localCommitments = selectedQubes.map(q => q.commitment || '');

        const participants = [
          ...selectedQubes.map(q => ({
            commitment: q.commitment || '',
            name: q.name,
            is_local: true,
          })),
          ...selectedOwnerCommitments
            .filter(c => !localCommitments.includes(c))
            .map(commitment => {
              const conn = connections.find(c => c.commitment === commitment);
              return {
                commitment,
                name: conn?.name || 'Remote Owner',
                is_local: false,
              };
            }),
        ];

        setOwnerCommitments([
          // Local owner commitment (primary qube's commitment represents the local owner)
          primaryQube.commitment || '',
          ...selectedOwnerCommitments,
        ].filter((c, idx, arr) => c && arr.indexOf(c) === idx));

        setSession({
          session_id: result.session_id,
          participants,
          state: 'active',
        });

        connectWebSocket(result.session_id);
      } else {
        setError(result.error || 'Failed to create supervised session');
      }
    } catch (err) {
      // Fallback: if create_supervised_session command doesn't exist yet, use create_p2p_session
      console.warn('create_supervised_session not found, falling back to create_p2p_session:', err);
      try {
        const fallbackResult = await invoke<{
          success: boolean;
          session_id?: string;
          participants?: any[];
          error?: string;
        }>(
          'create_p2p_session',
          {
            userId,
            qubeId: primaryQube.qube_id,
            localQubes: selectedQubes.map(q => q.qube_id).join(','),
            remoteCommitments: selectedOwnerCommitments.join(','),
            topic: 'supervised',
            password,
          }
        );

        if (fallbackResult.success && fallbackResult.session_id) {
          const localCommitments = selectedQubes.map(q => q.commitment || '');
          const participants = [
            ...selectedQubes.map(q => ({
              commitment: q.commitment || '',
              name: q.name,
              is_local: true,
            })),
            ...selectedOwnerCommitments
              .filter(c => !localCommitments.includes(c))
              .map(commitment => {
                const conn = connections.find(c => c.commitment === commitment);
                return { commitment, name: conn?.name || 'Remote Owner', is_local: false };
              }),
          ];

          setOwnerCommitments([
            primaryQube.commitment || '',
            ...selectedOwnerCommitments,
          ].filter((c, idx, arr) => c && arr.indexOf(c) === idx));

          setSession({
            session_id: fallbackResult.session_id,
            participants,
            state: 'active',
          });

          connectWebSocket(fallbackResult.session_id);
        } else {
          setError(fallbackResult.error || 'Failed to create session');
        }
      } catch (fallbackErr) {
        setError(String(fallbackErr));
      }
    } finally {
      setIsLoading(false);
    }
  };

  // Send message using new backend commands (same logic as local multi-qube)
  const handleSendMessage = async () => {
    // Guard against double-sending (e.g., double-click, Enter key repeat)
    if (isLoading) return;

    if (!inputValue.trim() || !session || !primaryQube || !userId || !password) return;

    const messageContent = inputValue.trim();
    setInputValue('');
    setIsLoading(true);
    setRespondingQubeName(primaryQube.name);

    try {
      const localQubeIds = getLocalQubeIds();
      const remoteConnectionsJson = getRemoteConnectionsJson();

      if (!conversationStarted) {
        // First message - start the P2P conversation
        const result = await invoke<{
          success: boolean;
          conversation_id?: string;
          response?: any;
          state?: any;
          error?: string;
        }>('start_p2p_conversation', {
          userId,
          localQubes: localQubeIds,
          remoteConnections: remoteConnectionsJson,
          sessionId: session.session_id,
          initialPrompt: messageContent,
          password,
        });

        if (result.success) {
          setConversationId(result.conversation_id || null);
          setConversationStarted(true);

          // Add user message to display
          const userMessage: P2PMessage = {
            block_number: -1,
            creator_commitment: userId, // User's ID, not Qube's commitment
            creator_name: userId,
            content: { role: 'user', content: messageContent },
            timestamp: Date.now() / 1000,
            signatures: [],
          };
          setMessages(prev => [...prev, userMessage]);

          // Add AI response to display if we got one
          if (result.response?.message) {
            const aiMessage: P2PMessage = {
              block_number: result.response.timestamp || -2,
              creator_commitment: result.response.speaker_id || primaryCommitment || '',
              creator_name: result.response.speaker_name || primaryQube.name,
              content: { role: 'assistant', content: result.response.message },
              timestamp: result.response.timestamp || Date.now() / 1000,
              signatures: [],
            };
            setMessages(prev => [...prev, aiMessage]);

            // Submit response to hub for other participants
            await submitBlockToHub(session.session_id, aiMessage);

            // Continue conversation if there are more local Qubes
            if (selectedQubes.length > 1 || allQubes.some(q => selectedConnections.includes(q.commitment || ''))) {
              await continueP2PConversation(result.conversation_id || '');
            }
          }
        } else {
          throw new Error(result.error || 'Failed to start P2P conversation');
        }
      } else {
        // Subsequent message - inject user message
        const result = await invoke<{
          success: boolean;
          user_message?: any;
          qube_response?: any;
          state?: any;
          error?: string;
        }>('send_p2p_user_message', {
          userId,
          conversationId: conversationId || '',
          sessionId: session.session_id,
          message: messageContent,
          localQubes: localQubeIds,
          remoteConnections: remoteConnectionsJson,
          password,
        });

        if (result.success) {
          // Add user message to display
          const userMessage: P2PMessage = {
            block_number: result.user_message?.timestamp || -1,
            creator_commitment: userId,
            creator_name: userId,
            content: { role: 'user', content: messageContent },
            timestamp: result.user_message?.timestamp || Date.now() / 1000,
            signatures: [],
          };
          setMessages(prev => [...prev, userMessage]);

          // Add AI response if we got one
          if (result.qube_response?.message) {
            const aiMessage: P2PMessage = {
              block_number: result.qube_response.timestamp || -2,
              creator_commitment: result.qube_response.speaker_id || primaryCommitment || '',
              creator_name: result.qube_response.speaker_name || primaryQube.name,
              content: { role: 'assistant', content: result.qube_response.message },
              timestamp: result.qube_response.timestamp || Date.now() / 1000,
              signatures: [],
            };
            setMessages(prev => [...prev, aiMessage]);

            // Submit response to hub
            await submitBlockToHub(session.session_id, aiMessage);
          }
        } else {
          throw new Error(result.error || 'Failed to send message');
        }
      }
    } catch (err) {
      console.error('Failed to send message:', err);
      setError(String(err));
      setInputValue(messageContent); // Restore message on error
    } finally {
      setIsLoading(false);
      setRespondingQubeName(null);
    }
  };

  // Submit a block to the hub for remote participants
  const submitBlockToHub = async (sessionId: string, message: P2PMessage) => {
    try {
      await fetch(`${API_BASE}/conversation/sessions/${sessionId}/blocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          creator_commitment: message.creator_commitment,
          block_type: 'MESSAGE',
          content: message.content,
          content_hash: '',
          creator_signature: '',
          timestamp: Math.floor(message.timestamp),
        }),
      });
    } catch (err) {
      console.error('Failed to submit block to hub:', err);
    }
  };

  // Continue P2P conversation (get next Qube response)
  const continueP2PConversation = async (convId: string) => {
    if (!session || !userId || !password) return;

    try {
      const result = await invoke<{
        success: boolean;
        response?: any;
        state?: any;
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
        const aiMessage: P2PMessage = {
          block_number: result.response.timestamp || -3,
          creator_commitment: result.response.speaker_id || '',
          creator_name: result.response.speaker_name || 'Unknown',
          content: { role: 'assistant', content: result.response.message },
          timestamp: result.response.timestamp || Date.now() / 1000,
          signatures: [],
        };
        setMessages(prev => [...prev, aiMessage]);

        // Submit to hub
        await submitBlockToHub(session.session_id, aiMessage);
      }
    } catch (err) {
      console.error('Failed to continue P2P conversation:', err);
    }
  };

  // Leave session
  const handleLeaveSession = async () => {
    if (!session) return;

    try {
      await fetch(`${API_BASE}/conversation/sessions/${session.session_id}/leave`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          commitment: primaryCommitment
        })
      });
    } catch (err) {
      console.error('Error leaving session:', err);
    }

    // Close WebSocket
    if (wsRef.current) {
      wsRef.current.close();
    }

    // Reset state
    setSession(null);
    setMessages([]);
    setWsConnected(false);
    setConversationId(null);
    setConversationStarted(false);
  };

  // Handle Enter key
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Get avatar path
  const getAvatarPath = (qube: Qube): string => {
    if (qube.avatar_url) return qube.avatar_url;
    if (qube.avatar_local_path) return convertFileSrc(qube.avatar_local_path);
    return '';
  };

  // Find participant by commitment
  const getParticipant = (commitment: string) => {
    return session?.participants.find(p => p.commitment === commitment);
  };

  // Check if qube is minted
  const isMinted = primaryQube?.commitment && primaryQube.commitment !== 'pending_minting';

  if (!isMinted) {
    return (
      <div className="flex-1 flex flex-col gap-4 h-full p-4">
        <GlassCard className="p-6 text-center">
          <h2 className="text-xl font-display text-accent-warning mb-4">
            NFT Required for P2P Chat
          </h2>
          <p className="text-text-secondary mb-4">
            {primaryQube?.name || 'This Qube'} must be minted as an NFT before joining P2P conversations.
          </p>
          <GlassButton variant="secondary" onClick={onBack}>
            Go Back
          </GlassButton>
        </GlassCard>
      </div>
    );
  }

  // Session setup view
  if (!session) {
    return (
      <div className="flex-1 flex flex-col gap-4 h-full">
        {/* Header */}
        <GlassCard className="p-4 flex-shrink-0">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-display text-text-primary">
                P2P Network
              </h2>
              <p className="text-sm text-text-tertiary">
                {p2pSubView === 'session' ? 'Start a session or manage connections' : 'Discover, connect and manage your network'}
              </p>
            </div>
            <GlassButton variant="secondary" onClick={onBack}>
              Back to Local
            </GlassButton>
          </div>

          {/* Sub-view toggle: Session vs Connections */}
          <div className="flex gap-0 mt-4 rounded-lg overflow-hidden border border-glass-border">
            <button
              onClick={() => setP2pSubView('session')}
              className={`flex-1 px-4 py-2 text-sm font-medium transition-all ${
                p2pSubView === 'session'
                  ? 'bg-accent-secondary text-bg-primary'
                  : 'bg-glass-bg text-text-secondary hover:text-text-primary'
              }`}
            >
              Session
            </button>
            <button
              onClick={() => setP2pSubView('connections')}
              className={`flex-1 px-4 py-2 text-sm font-medium transition-all border-l border-glass-border ${
                p2pSubView === 'connections'
                  ? 'bg-accent-secondary text-bg-primary'
                  : 'bg-glass-bg text-text-secondary hover:text-text-primary'
              }`}
            >
              Connections
            </button>
          </div>

          {/* Session type toggle — only shown in Session sub-view */}
          {p2pSubView === 'session' && (
          <div className="flex gap-2 mt-3">
            <button
              onClick={() => setSessionType('standard')}
              className={`flex-1 px-4 py-2 rounded-l-lg text-sm font-medium transition-all ${
                sessionType === 'standard'
                  ? 'bg-accent-primary text-bg-primary'
                  : 'bg-glass-bg text-text-secondary border border-glass-border hover:text-text-primary'
              }`}
            >
              Qube P2P
            </button>
            <button
              onClick={() => setSessionType('supervised')}
              className={`flex-1 px-4 py-2 rounded-r-lg text-sm font-medium transition-all ${
                sessionType === 'supervised'
                  ? 'bg-accent-secondary/80 text-bg-primary'
                  : 'bg-glass-bg text-text-secondary border border-glass-border hover:text-text-primary'
              }`}
            >
              Supervised
            </button>
          </div>
          )}
        </GlassCard>

        {/* Connections sub-view — full ConnectionManager */}
        {p2pSubView === 'connections' && (
          <div className="flex-1 overflow-hidden">
            <ConnectionManager qubes={allQubes} selectedQubeOverride={primaryQube} />
          </div>
        )}

        {/* Session sub-view wrapper */}
        {p2pSubView === 'session' && (<>

        {/* Supervised setup */}
        {sessionType === 'supervised' && (
          <SupervisedChatSetup
            selectedQubes={selectedQubes}
            allQubes={allQubes}
            onStartSession={handleStartSupervisedSession}
            onBack={() => setSessionType('standard')}
            loading={isLoading}
          />
        )}

        {/* Standard P2P setup — only show when standard is selected */}
        {sessionType === 'standard' && (<>

        {/* Local Participants */}
        <GlassCard className="p-4 flex-shrink-0">
          <h3 className="text-lg font-display text-text-primary mb-3">
            Local Participants
          </h3>
          <div className="flex gap-4">
            {selectedQubes.map(qube => (
              <div key={qube.qube_id} className="flex items-center gap-2">
                <img
                  src={getAvatarPath(qube)}
                  alt={qube.name}
                  className="w-10 h-10 rounded-full object-cover border-2"
                  style={{ borderColor: qube.favorite_color }}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
                <span className="text-text-primary" style={{ color: qube.favorite_color }}>
                  {qube.name}
                </span>
              </div>
            ))}
          </div>
        </GlassCard>

        {/* Connection Selection */}
        <GlassCard className="p-4 flex-1 overflow-y-auto">
          <h3 className="text-lg font-display text-text-primary mb-3">
            Invite Connections ({selectedConnections.length} selected)
          </h3>

          {(() => {
            // Filter out connections that are already local participants
            const localCommitments = selectedQubes.map(q => q.commitment || '');
            const remoteConnections = connections.filter(
              conn => !localCommitments.includes(conn.commitment)
            );

            if (connections.length === 0) {
              return (
                <div className="text-center py-8">
                  <p className="text-text-secondary mb-2">No connections yet</p>
                  <p className="text-sm text-text-tertiary mb-3">
                    Discover and connect with other Qubes first.
                  </p>
                  <button
                    onClick={() => setP2pSubView('connections')}
                    className="px-4 py-2 bg-accent-secondary/20 border border-accent-secondary rounded-lg text-accent-secondary hover:bg-accent-secondary/30 transition-colors text-sm"
                  >
                    Open Connections
                  </button>
                </div>
              );
            }

            if (remoteConnections.length === 0) {
              return (
                <div className="text-center py-8">
                  <p className="text-text-secondary mb-2">All connections are already local participants</p>
                  <p className="text-sm text-text-tertiary">
                    P2P Network mode is for chatting with Qubes owned by other users.
                    Since all your connections are local, use <strong>Local mode</strong> instead!
                  </p>
                  <button
                    onClick={onBack}
                    className="mt-4 px-4 py-2 bg-accent-primary/20 border border-accent-primary rounded-lg text-accent-primary hover:bg-accent-primary/30 transition-colors"
                  >
                    Switch to Local Mode
                  </button>
                </div>
              );
            }

            return (
              <div className="space-y-2">
                {remoteConnections.map(conn => (
                  <div
                    key={conn.commitment}
                    className={`p-3 rounded-lg border-2 cursor-pointer transition-all ${
                      selectedConnections.includes(conn.commitment)
                        ? 'bg-accent-primary/20 border-accent-primary'
                        : 'bg-bg-tertiary border-glass-border hover:border-accent-primary/50'
                    }`}
                    onClick={() => toggleConnection(conn.commitment)}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-text-primary font-medium">{conn.name}</p>
                        <p className="text-xs text-text-tertiary font-mono">
                          {conn.commitment.substring(0, 16)}...
                        </p>
                      </div>
                      {selectedConnections.includes(conn.commitment) && (
                        <span className="text-accent-primary text-xl">✓</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}
        </GlassCard>

        {/* Error */}
        {error && (
          <div className="p-3 bg-accent-danger/20 border border-accent-danger/50 rounded-lg">
            <p className="text-accent-danger text-sm">{error}</p>
          </div>
        )}

        {/* Start Button */}
        <GlassCard className="p-4 flex-shrink-0">
          <GlassButton
            variant="primary"
            onClick={handleStartSession}
            disabled={isLoading || selectedConnections.length === 0}
            className="w-full"
          >
            {isLoading ? 'Creating Session...' : 'Start P2P Conversation'}
          </GlassButton>
        </GlassCard>
        </>)}
        </>)}
      </div>
    );
  }

  // Active session view — supervised mode
  if (sessionType === 'supervised' && session) {
    return (
      <SupervisedChatInterface
        selectedQubes={selectedQubes}
        allQubes={allQubes}
        session={session}
        ownerCommitments={ownerCommitments}
        onLeave={handleLeaveSession}
      />
    );
  }

  // Active session view — standard P2P
  return (
    <div className="flex-1 flex flex-col gap-4 h-full">
      {/* Session Header */}
      <GlassCard className="p-4 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            {/* Connection status */}
            <div className={`w-3 h-3 rounded-full ${wsConnected ? 'bg-accent-success animate-pulse' : 'bg-accent-danger'}`} />

            {/* Participants */}
            <div className="flex -space-x-2">
              {session.participants.slice(0, 5).map((p, i) => (
                <div
                  key={p.commitment}
                  className="w-8 h-8 rounded-full bg-bg-tertiary border-2 border-bg-primary flex items-center justify-center text-xs text-text-primary"
                  title={p.name}
                  style={{ zIndex: 5 - i }}
                >
                  {p.name[0]}
                </div>
              ))}
              {session.participants.length > 5 && (
                <div className="w-8 h-8 rounded-full bg-bg-tertiary border-2 border-bg-primary flex items-center justify-center text-xs text-text-tertiary">
                  +{session.participants.length - 5}
                </div>
              )}
            </div>

            <div>
              <p className="text-text-primary font-medium">
                P2P Session
              </p>
              <p className="text-xs text-text-tertiary">
                {session.participants.length} participants • {messages.length} messages
              </p>
            </div>
          </div>

          <GlassButton variant="danger" size="sm" onClick={handleLeaveSession}>
            Leave
          </GlassButton>
        </div>
      </GlassCard>

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        className="flex-1 p-4 overflow-y-auto bg-bg-secondary/30 backdrop-blur-md border border-accent-primary/20 rounded-xl"
      >
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-text-tertiary text-center">
              {wsConnected ? 'Waiting for messages...' : 'Connecting...'}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((msg) => {
              const participant = getParticipant(msg.creator_commitment);
              const isLocal = participant?.is_local;
              const isMe = msg.creator_commitment === primaryCommitment;

              return (
                <div
                  key={msg.block_number}
                  className={`flex ${isMe ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[70%] rounded-lg p-3 border-2 ${
                      isMe
                        ? 'bg-accent-primary/20 border-accent-primary'
                        : isLocal
                          ? 'bg-accent-secondary/20 border-accent-secondary'
                          : 'bg-bg-tertiary border-glass-border'
                    }`}
                  >
                    {/* Sender */}
                    <div className="flex items-center gap-2 mb-2">
                      <p className={`text-sm font-medium ${
                        isMe ? 'text-accent-primary' :
                        isLocal ? 'text-accent-secondary' : 'text-text-primary'
                      }`}>
                        {msg.creator_name || participant?.name || 'Unknown'}
                        {isLocal && !isMe && ' (local)'}
                        {!isLocal && ' (remote)'}
                      </p>
                    </div>

                    {/* Content */}
                    <div className="whitespace-pre-wrap break-words text-text-primary">
                      {msg.content?.content || ''}
                    </div>

                    {/* Metadata */}
                    <p className="text-text-tertiary text-xs mt-1">
                      Block #{msg.block_number} • {msg.signatures?.length || 0} signature(s)
                    </p>
                  </div>
                </div>
              );
            })}

            {/* AI Processing Indicator */}
            {processingResponse && (
              <div className="flex justify-start">
                <div className="max-w-[70%] rounded-lg p-3 border-2 bg-accent-secondary/20 border-accent-secondary">
                  <div className="flex items-center gap-2 mb-2">
                    <p className="text-sm font-medium text-accent-secondary">
                      {respondingQubeName || primaryQube?.name || 'Your Qube'} is thinking...
                    </p>
                  </div>
                  <div className="flex gap-1">
                    <span className="w-2 h-2 bg-accent-secondary rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-2 h-2 bg-accent-secondary rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-2 h-2 bg-accent-secondary rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-accent-danger/20 border border-accent-danger/50 rounded-lg">
          <p className="text-accent-danger text-sm">{error}</p>
        </div>
      )}

      {/* Input */}
      <GlassCard className="p-4 flex-shrink-0">
        <div className="flex gap-2">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Type your message..."
            className="flex-1 bg-bg-secondary text-text-primary placeholder-text-tertiary rounded-lg px-4 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-accent-primary/50"
            rows={1}
            disabled={isLoading || !wsConnected}
          />
          <GlassButton
            variant="primary"
            onClick={handleSendMessage}
            disabled={!inputValue.trim() || isLoading || !wsConnected}
          >
            Send
          </GlassButton>
        </div>
        <p className="text-xs text-text-tertiary mt-2">
          {wsConnected
            ? `Connected to session ${session.session_id.substring(0, 8)}...`
            : 'Connecting to P2P session...'
          }
        </p>
      </GlassCard>
    </div>
  );
};
