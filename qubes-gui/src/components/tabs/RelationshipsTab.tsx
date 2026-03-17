import React, { useState, useEffect, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Qube } from '../../types';
import { GlassCard } from '../glass';
import { useAuth } from '../../hooks/useAuth';
import { useRelationshipUpdates } from '../../hooks/useRelationshipUpdates';
import { useQubeSelection } from '../../hooks/useQubeSelection';
import { NetworkGraph } from '../NetworkGraph';
import { RelationshipRadarChart } from '../RelationshipRadarChart';
import { RelationshipTimelineChart } from '../RelationshipTimelineChart';
import { MetricSection, ClearanceSelector, ClearanceEditModal, TraitManager } from '../relationships';

interface Relationship {
  entity_id: string;
  entity_name?: string;
  entity_type?: string;
  status: 'blocked' | 'enemy' | 'rival' | 'suspicious' | 'unmet' | 'stranger' | 'acquaintance' | 'friend' | 'close_friend' | 'best_friend';
  trust: number;
  has_met: boolean;
  is_best_friend: boolean;
  messages_sent: number;
  messages_received: number;
  collaborations_successful: number;
  collaborations_failed: number;
  first_contact?: number;
  last_interaction?: number;
  days_known: number;
  // Clearance System (v2)
  clearance_profile: 'none' | 'public' | 'professional' | 'social' | 'trusted' | 'inner_circle' | 'family';
  clearance_categories: string[];
  clearance_expires_at?: number;
  clearance_field_grants: string[];
  clearance_field_denials: string[];
  // Tags
  tags: string[];
  // Core Trust Metrics (5) - foundational earned qualities
  honesty: number;
  reliability: number;
  support: number;
  loyalty: number;
  respect: number;
  // Social Metrics - Positive (14)
  friendship: number;
  affection: number;
  engagement: number;
  depth: number;
  humor: number;
  understanding: number;
  compatibility: number;
  admiration: number;
  warmth: number;
  openness: number;
  patience: number;
  empowerment: number;
  responsiveness: number;
  expertise: number;
  // Social Metrics - Negative (10)
  antagonism: number;
  resentment: number;
  annoyance: number;
  distrust: number;
  rivalry: number;
  tension: number;
  condescension: number;
  manipulation: number;
  dismissiveness: number;
  betrayal: number;
  // Behavioral/Communication Metrics (6)
  verbosity: number;
  punctuality: number;
  emotional_stability: number;
  directness: number;
  energy_level: number;
  humor_style: number;
  // Additional stats
  collaborations?: number;
  response_time_avg?: number;
  interaction_frequency_per_day?: number;
  communication_style?: string;
  shared_experiences?: Array<any>;
  // Traits - AI-attributed personality/behavioral characteristics
  trait_scores?: Record<string, TraitScore>;
  trait_evolution?: TraitEvolutionEntry[];
  manual_trait_overrides?: Record<string, boolean>;
}

interface TraitScore {
  score: number;
  evidence_count: number;
  first_detected: number;
  last_updated: number;
  consistency: number;
  volatility: number;
  trend: 'rising' | 'stable' | 'falling';
  source: 'metric_derived' | 'ai_direct' | 'both';
  is_confident: boolean;
}

interface TraitEvolutionEntry {
  timestamp: number;
  trait: string;
  old_score: number;
  new_score: number;
  evaluation_index: number;
}

interface TraitDefinition {
  name: string;
  category: string;
  description: string;
  icon: string;
  color: string;
  polarity: 'positive' | 'negative' | 'neutral' | 'warning';
  is_warning?: boolean;
}

interface ClearanceProfile {
  name: string;
  level: number;
  description: string;
  categories: string[];
  fields: string[];
  excluded_fields: string[];
  icon: string;
  color: string;
}

// Default clearance profiles (fallback if API fails)
const DEFAULT_CLEARANCE_PROFILES: Record<string, ClearanceProfile> = {
  none: { name: 'none', level: 0, description: 'No access to owner information', categories: [], fields: [], excluded_fields: ['*'], icon: '🚫', color: '#666666' },
  public: { name: 'public', level: 1, description: 'Name and occupation only', categories: ['standard'], fields: ['name', 'nickname', 'occupation'], excluded_fields: [], icon: '🌐', color: '#888888' },
  professional: { name: 'professional', level: 2, description: 'Work-related information', categories: ['standard'], fields: ['name', 'nickname', 'occupation', 'employer', 'job_title'], excluded_fields: ['home_address', 'personal_phone'], icon: '💼', color: '#4a90e2' },
  social: { name: 'social', level: 3, description: 'Social and casual information', categories: ['standard', 'social'], fields: ['name', 'nickname', 'occupation', 'hobbies', 'interests'], excluded_fields: ['financial'], icon: '🎉', color: '#f5a623' },
  trusted: { name: 'trusted', level: 4, description: 'Extended personal details', categories: ['standard', 'social', 'personal'], fields: [], excluded_fields: ['financial', 'medical'], icon: '🤝', color: '#7ed321' },
  inner_circle: { name: 'inner_circle', level: 5, description: 'Nearly full access', categories: ['standard', 'social', 'personal', 'sensitive'], fields: [], excluded_fields: ['passwords'], icon: '💚', color: '#50e3c2' },
  family: { name: 'family', level: 6, description: 'Complete access to all information', categories: ['*'], fields: ['*'], excluded_fields: [], icon: '❤️', color: '#e91e63' },
};

interface RelationshipsTabProps {
  qubes: Qube[];
}

// Stable empty array to avoid infinite loops with Zustand selectors
const EMPTY_ARRAY: string[] = [];

export const RelationshipsTab: React.FC<RelationshipsTabProps> = ({ qubes }) => {
  const { userId, password } = useAuth();
  const selectedQubeIds = useQubeSelection((state) => state.selectionByTab[state.currentTab] ?? EMPTY_ARRAY);
  const selectedQubeId = selectedQubeIds.length > 0 ? selectedQubeIds[0] : null;
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedRelationships, setExpandedRelationships] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'grid' | 'network'>('grid');
  const [timelineData, setTimelineData] = useState<Record<string, any[]>>({});

  // Clearance/Trait metadata
  const [clearanceProfiles, setClearanceProfiles] = useState<Record<string, ClearanceProfile>>({});
  const [traitDefinitions, setTraitDefinitions] = useState<Record<string, TraitDefinition>>({});

  // Expanded sections per relationship (for Level 2 disclosure)
  const [showDetailedMetrics, setShowDetailedMetrics] = useState<Set<string>>(new Set());

  // Modal state
  const [editingClearance, setEditingClearance] = useState<string | null>(null);

  const loadRelationships = useCallback(async (qubeId: string) => {
    try {
      setLoading(true);
      const params: any = {
        userId,
        qubeId,
      };

      // Only include password if it exists
      if (password) {
        params.password = password;
      }

      const result = await invoke<{
        success: boolean;
        relationships: Relationship[];
        stats: any;
        error?: string;
      }>('get_qube_relationships', params);

      if (result.success) {
        setRelationships(result.relationships);
      } else {
        console.error('Failed to load relationships:', result.error);
        setRelationships([]);
      }
    } catch (error) {
      console.error('Exception loading relationships:', error);
      setRelationships([]);
    } finally {
      setLoading(false);
    }
  }, [userId, password]);

  const loadTimelineData = useCallback(async (qubeId: string, entityId: string) => {
    try {
      const params: any = {
        userId,
        qubeId,
        entityId,
      };

      // Only include password if it exists
      if (password) {
        params.password = password;
      }

      const result = await invoke<{
        success: boolean;
        timeline: Array<{
          block_number: number;
          timestamp: number;
          trust: number;
          compatibility: number;
        }>;
        error?: string;
      }>('get_relationship_timeline', params);

      if (result.success && result.timeline.length > 0) {
        // Format timeline data for the chart
        const formattedData = result.timeline.map(point => ({
          date: new Date(point.timestamp * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
          trust: point.trust,
          compatibility: point.compatibility,
        }));

        setTimelineData(prev => ({
          ...prev,
          [entityId]: formattedData
        }));

        return formattedData;
      } else {
        return null;
      }
    } catch (error) {
      console.error('❌ Exception loading timeline:', error);
      return null;
    }
  }, [userId, password]);

  // WebSocket connection for real-time updates (disabled by default)
  const { isConnected } = useRelationshipUpdates({
    onUpdate: (update) => {
      // Only reload if the update is for the currently selected qube
      if (update.data?.qube_id === selectedQubeId && selectedQubeId) {
        loadRelationships(selectedQubeId);
      }
    },
    autoConnect: false, // Disabled until WebSocket server is running
  });

  // Load relationships when qube is selected from roster
  useEffect(() => {
    if (selectedQubeId) {
      loadRelationships(selectedQubeId);
    }
  }, [selectedQubeId, loadRelationships]);

  // Load timeline data for all relationships
  useEffect(() => {
    if (selectedQubeId && relationships.length > 0) {
      // Load timeline for each relationship
      relationships.forEach(rel => {
        if (!timelineData[rel.entity_id]) {
          loadTimelineData(selectedQubeId, rel.entity_id);
        }
      });
    }
  }, [selectedQubeId, relationships, timelineData, loadTimelineData]);

  // Load clearance/trait metadata when qube is selected
  useEffect(() => {
    const loadMetadata = async () => {
      if (!selectedQubeId || !userId) return;

      try {
        const [profilesResult, traitsResult] = await Promise.all([
          invoke<{ success: boolean; profiles?: Record<string, ClearanceProfile> }>('get_clearance_profiles', {
            userId,
            qubeId: selectedQubeId,
          }).catch((e) => {
            console.error('Failed to load clearance profiles:', e);
            return { success: false, profiles: undefined };
          }),
          invoke<{ success: boolean; traits?: Record<string, TraitDefinition> }>('get_trait_definitions', {
            userId,
            qubeId: selectedQubeId,
          }).catch(() => ({ success: false, traits: undefined })),
        ]);

        if (profilesResult.success && profilesResult.profiles) {
          setClearanceProfiles(profilesResult.profiles);
        }
        if (traitsResult.success && traitsResult.traits) setTraitDefinitions(traitsResult.traits);
      } catch (error) {
        console.error('Failed to load metadata:', error);
      }
    };

    loadMetadata();
  }, [selectedQubeId, userId]);

  const selectedQube = qubes.find(q => q.qube_id === selectedQubeId);

  const toggleRelationship = (entityId: string) => {
    setExpandedRelationships(prev => {
      const newSet = new Set(prev);
      if (newSet.has(entityId)) {
        newSet.delete(entityId);
      } else {
        newSet.add(entityId);
      }
      return newSet;
    });
  };

  const toggleDetailedMetrics = (entityId: string) => {
    setShowDetailedMetrics(prev => {
      const newSet = new Set(prev);
      if (newSet.has(entityId)) {
        newSet.delete(entityId);
      } else {
        newSet.add(entityId);
      }
      return newSet;
    });
  };

  // Helper to update local relationship data after trait/clearance changes
  const updateRelationshipTraitOverride = (entityId: string, trait: string, visible: boolean) => {
    setRelationships(prev => prev.map(rel =>
      rel.entity_id === entityId
        ? { ...rel, manual_trait_overrides: { ...rel.manual_trait_overrides, [trait]: visible } }
        : rel
    ));
  };

  const updateRelationshipClearance = (entityId: string, profile: string) => {
    setRelationships(prev => prev.map(rel =>
      rel.entity_id === entityId ? { ...rel, clearance_profile: profile as Relationship['clearance_profile'] } : rel
    ));
  };

  // Metric section definitions for progressive disclosure
  const getMetricSections = (rel: Relationship) => ({
    coreTrust: {
      title: 'Core Trust',
      icon: '🔒',
      metrics: [
        { key: 'honesty', label: 'Honesty', value: rel.honesty, color: '#2196F3' },
        { key: 'reliability', label: 'Reliability', value: rel.reliability, color: '#4CAF50' },
        { key: 'support', label: 'Support', value: rel.support, color: '#ff6347' },
        { key: 'loyalty', label: 'Loyalty', value: rel.loyalty, color: '#dc143c' },
        { key: 'respect', label: 'Respect', value: rel.respect, color: '#ba55d3' },
      ],
    },
    emotionalBond: {
      title: 'Emotional Bond',
      icon: '💖',
      metrics: [
        { key: 'friendship', label: 'Friendship', value: rel.friendship, color: '#ff69b4' },
        { key: 'affection', label: 'Affection', value: rel.affection, color: '#ff1493' },
        { key: 'warmth', label: 'Warmth', value: rel.warmth, color: '#ffa07a' },
        { key: 'understanding', label: 'Understanding', value: rel.understanding, color: '#ff8c69' },
      ],
    },
    personalGrowth: {
      title: 'Personal Growth',
      icon: '🌱',
      metrics: [
        { key: 'admiration', label: 'Admiration', value: rel.admiration, color: '#ffd700' },
        { key: 'empowerment', label: 'Empowerment', value: rel.empowerment, color: '#32cd32' },
        { key: 'openness', label: 'Openness', value: rel.openness, color: '#87ceeb' },
        { key: 'patience', label: 'Patience', value: rel.patience, color: '#9370db' },
      ],
    },
    connectionQuality: {
      title: 'Connection Quality',
      icon: '✨',
      metrics: [
        { key: 'engagement', label: 'Engagement', value: rel.engagement, color: '#00bcd4' },
        { key: 'depth', label: 'Depth', value: rel.depth, color: '#3f51b5' },
        { key: 'humor', label: 'Humor', value: rel.humor, color: '#ffeb3b' },
        { key: 'compatibility', label: 'Compatibility', value: rel.compatibility, color: '#20b2aa' },
        { key: 'responsiveness', label: 'Responsiveness', value: rel.responsiveness, color: '#00ff7f' },
        { key: 'expertise', label: 'Expertise', value: rel.expertise, color: '#9C27B0' },
      ],
    },
    negativeDynamics: {
      title: 'Negative Dynamics',
      icon: '⚠️',
      metrics: [
        { key: 'antagonism', label: 'Antagonism', value: rel.antagonism, color: '#dc143c' },
        { key: 'resentment', label: 'Resentment', value: rel.resentment, color: '#b22222' },
        { key: 'annoyance', label: 'Annoyance', value: rel.annoyance, color: '#ff6347' },
        { key: 'distrust', label: 'Distrust', value: rel.distrust, color: '#8b0000' },
        { key: 'rivalry', label: 'Rivalry', value: rel.rivalry, color: '#daa520' },
        { key: 'tension', label: 'Tension', value: rel.tension, color: '#ff4500' },
        { key: 'condescension', label: 'Condescension', value: rel.condescension, color: '#cd5c5c' },
        { key: 'manipulation', label: 'Manipulation', value: rel.manipulation, color: '#8b0000' },
        { key: 'dismissiveness', label: 'Dismissiveness', value: rel.dismissiveness, color: '#ff6347' },
        { key: 'betrayal', label: 'Betrayal', value: rel.betrayal, color: '#800000' },
      ].filter(m => m.value > 0), // Only show non-zero
    },
  });

  const hasNegativeDynamics = (rel: Relationship): boolean => {
    return rel.antagonism > 0 || rel.resentment > 0 || rel.annoyance > 0 ||
      rel.distrust > 0 || rel.rivalry > 0 || rel.tension > 0 ||
      rel.condescension > 0 || rel.manipulation > 0 ||
      rel.dismissiveness > 0 || rel.betrayal > 0;
  };

  // Filter out self-relationships and deduplicate by Qube identity
  // In P2P mode, the same Qube might have relationships recorded with both qube_id and commitment
  const filteredRelationships = (() => {
    if (!selectedQube) return relationships;

    // First filter out self-relationships
    const nonSelfRelationships = relationships.filter(rel =>
      rel.entity_id !== selectedQube.qube_id &&
      rel.entity_id !== selectedQube.commitment
    );

    // Deduplicate: if two relationships point to the same Qube (one by qube_id, one by commitment),
    // merge them by keeping the one with higher trust/more interactions
    const seenQubeIds = new Map<string, Relationship>(); // canonical qube_id -> best relationship

    for (const rel of nonSelfRelationships) {
      // Try to find the canonical Qube this relationship points to
      const qube = qubes.find(q => q.qube_id === rel.entity_id || q.commitment === rel.entity_id);
      const canonicalId = qube?.qube_id || rel.entity_id;

      const existing = seenQubeIds.get(canonicalId);
      if (!existing) {
        seenQubeIds.set(canonicalId, rel);
      } else {
        // Keep the relationship with more data (higher trust, more messages)
        const existingScore = existing.trust + existing.messages_sent + existing.messages_received;
        const newScore = rel.trust + rel.messages_sent + rel.messages_received;
        if (newScore > existingScore) {
          seenQubeIds.set(canonicalId, rel);
        }
      }
    }

    return Array.from(seenQubeIds.values());
  })();

  // Group relationships by status
  const groupedRelationships = filteredRelationships.reduce((groups, rel) => {
    const status = rel.status;
    if (!groups[status]) {
      groups[status] = [];
    }
    groups[status].push(rel);
    return groups;
  }, {} as Record<string, Relationship[]>);

  // Status order and display config (positive to negative)
  const statusConfig = [
    { status: 'owner', label: 'Owner', icon: '👑', color: '#ffd700' },
    { status: 'best_friend', label: 'Best Friend', icon: '💖', color: '#ff69b4' },
    { status: 'close_friend', label: 'Close Friends', icon: '💕', color: '#ff1493' },
    { status: 'friend', label: 'Friends', icon: '💚', color: '#00ff88' },
    { status: 'acquaintance', label: 'Acquaintances', icon: '👋', color: '#ffaa00' },
    { status: 'stranger', label: 'Strangers', icon: '🤝', color: '#888888' },
    { status: 'unmet', label: 'Unmet', icon: '❓', color: '#666666' },
    { status: 'suspicious', label: 'Suspicious', icon: '🤨', color: '#ff8800' },
    { status: 'rival', label: 'Rivals', icon: '⚔️', color: '#ff6600' },
    { status: 'enemy', label: 'Enemies', icon: '💢', color: '#ff3333' },
    { status: 'blocked', label: 'Blocked', icon: '🚫', color: '#cc0000' },
  ];

  const getTrustColor = (trust: number): string => {
    if (trust >= 75) return '#00ff88';
    if (trust >= 50) return '#ffaa00';
    if (trust >= 25) return '#ff8800';
    return '#ff3366';
  };

  const formatDate = (timestamp?: number): string => {
    if (!timestamp) return 'Never';
    return new Date(timestamp * 1000).toLocaleDateString();
  };

  const getRelationshipDuration = (firstContact?: number): string => {
    if (!firstContact) return 'N/A';
    const days = Math.floor((Date.now() / 1000 - firstContact) / 86400);
    if (days === 0) return 'Today';
    if (days === 1) return '1 day';
    if (days < 30) return `${days} days`;
    const months = Math.floor(days / 30);
    if (months === 1) return '1 month';
    if (months < 12) return `${months} months`;
    const years = Math.floor(months / 12);
    return years === 1 ? '1 year' : `${years} years`;
  };

  const formatResponseTime = (seconds?: number): string => {
    if (!seconds || seconds === 0) return 'N/A';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  };

  const ProgressBar = ({ value, max = 100, color }: { value: number; max?: number; color?: string }) => {
    const percentage = (value / max) * 100;
    const barColor = color || getTrustColor(value);

    return (
      <div className="w-full bg-glass-bg rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full transition-all duration-300"
          style={{
            width: `${Math.min(percentage, 100)}%`,
            backgroundColor: barColor
          }}
        />
      </div>
    );
  };

  const getEntityNameColor = (rel: Relationship): string => {
    // Human names stay white
    if (rel.entity_type === 'human') {
      return 'text-text-primary';
    }

    // Qube names use their favorite color
    // Check by qube_id first, then by commitment (for P2P relationships)
    const qube = qubes.find(q => q.qube_id === rel.entity_id || q.commitment === rel.entity_id);
    if (qube?.favorite_color) {
      return ''; // Return empty to use inline style
    }

    return 'text-text-primary';
  };

  const getEntityNameStyle = (rel: Relationship): React.CSSProperties => {
    if (rel.entity_type === 'qube') {
      const qube = qubes.find(q => q.qube_id === rel.entity_id || q.commitment === rel.entity_id);
      if (qube?.favorite_color) {
        return { color: qube.favorite_color };
      }
    }
    return {};
  };

  // Get display name for a relationship - shows "Name (ID)" for Qubes
  const getEntityDisplayName = (rel: Relationship): string => {
    // For human entities, just show the name/id
    if (rel.entity_type === 'human') {
      return rel.entity_name || rel.entity_id;
    }

    // For Qube entities, try to find full info
    // Check by qube_id first, then by commitment (for P2P relationships)
    const qube = qubes.find(q => q.qube_id === rel.entity_id || q.commitment === rel.entity_id);

    if (qube) {
      // Found the qube - show "Name (short_id)"
      const shortId = qube.qube_id.substring(0, 8);
      return `${qube.name} (${shortId})`;
    }

    // If entity_name is available, use it with the ID
    if (rel.entity_name) {
      // Truncate entity_id if it's too long (commitments are 64 chars)
      const shortId = rel.entity_id.length > 8 ? rel.entity_id.substring(0, 8) + '...' : rel.entity_id;
      return `${rel.entity_name} (${shortId})`;
    }

    // Fallback: just show truncated ID
    return rel.entity_id.length > 12 ? rel.entity_id.substring(0, 12) + '...' : rel.entity_id;
  };

  // Get timeline data - only show real data from snapshots
  const generateTimelineData = (rel: Relationship) => {
    // Check if we have real timeline data loaded
    if (timelineData[rel.entity_id] && timelineData[rel.entity_id].length > 0) {
      return timelineData[rel.entity_id];
    }

    // Load real data in the background
    if (selectedQubeId) {
      loadTimelineData(selectedQubeId, rel.entity_id);
    }

    // Return empty array if no real data available
    // Timeline chart will show "No timeline data available" message
    return [];
  };

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Header */}
      <div className="p-6 pb-4 flex-shrink-0">
        <div className="flex items-center justify-between">
          {/* Title with avatar, name, and count badge */}
          <div className="flex items-center gap-3">
            {selectedQube ? (
              <>
                {/* Qube Avatar */}
                {selectedQube.avatar_url ? (
                  <img
                    src={selectedQube.avatar_url}
                    alt={selectedQube.name}
                    className="w-10 h-10 rounded-lg object-cover"
                    style={{ border: `2px solid ${selectedQube.favorite_color}` }}
                  />
                ) : (
                  <div
                    className="w-10 h-10 rounded-lg flex items-center justify-center text-lg font-bold"
                    style={{
                      background: `${selectedQube.favorite_color}30`,
                      border: `2px solid ${selectedQube.favorite_color}`,
                      color: selectedQube.favorite_color,
                    }}
                  >
                    {selectedQube.name[0]}
                  </div>
                )}
                {/* Name and Relationships */}
                <h1 className="text-2xl font-display text-text-primary">
                  <span style={{ color: selectedQube.favorite_color }}>{selectedQube.name}'s</span> Relationships
                </h1>
                {/* Count Badge */}
                <span
                  className="px-3 py-1 rounded-full text-lg font-semibold"
                  style={{
                    background: `${selectedQube.favorite_color}30`,
                    color: selectedQube.favorite_color,
                    border: `1px solid ${selectedQube.favorite_color}50`,
                  }}
                >
                  {filteredRelationships.length}
                </span>
              </>
            ) : (
              <h1 className="text-2xl font-display text-text-primary">
                Relationships
              </h1>
            )}
          </div>

          {/* Right side: Connection status + View toggle */}
          <div className="flex items-center gap-4">
            {/* WebSocket Connection Status */}
            <div className="flex items-center gap-2 text-xs">
              <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-gray-500'} ${isConnected ? 'animate-pulse' : ''}`} />
              <span className="text-text-tertiary">
                {isConnected ? 'Live updates' : 'Offline'}
              </span>
            </div>

            {/* View Mode Toggle */}
            {selectedQube && (
              <div className="flex gap-2">
                <button
                  onClick={() => setViewMode('grid')}
                  className={`px-3 py-1.5 rounded text-xs transition-colors ${
                    viewMode === 'grid'
                      ? 'bg-accent-primary text-white'
                      : 'bg-glass-bg text-text-tertiary hover:text-text-primary'
                  }`}
                >
                  📋 Grid
                </button>
                <button
                  onClick={() => setViewMode('network')}
                  className={`px-3 py-1.5 rounded text-xs transition-colors ${
                    viewMode === 'network'
                      ? 'bg-accent-primary text-white'
                      : 'bg-glass-bg text-text-tertiary hover:text-text-primary'
                  }`}
                >
                  🌐 Network
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden px-6 pb-6">
        {!selectedQubeId ? (
          <GlassCard className="p-12 text-center">
            <div className="text-6xl mb-4">👈</div>
            <h2 className="text-2xl font-display text-text-primary mb-2">
              Select a Qube
            </h2>
            <p className="text-text-secondary">
              Choose a qube from the roster to view its relationships
            </p>
          </GlassCard>
        ) : loading ? (
          <GlassCard className="p-12 text-center">
            <p className="text-text-secondary">Loading relationships...</p>
          </GlassCard>
        ) : filteredRelationships.length === 0 ? (
          <GlassCard className="p-8 text-center">
            <div className="text-4xl mb-3">💬</div>
            <h3 className="text-lg font-display text-text-primary mb-1">
              No Relationships Yet
            </h3>
            <p className="text-sm text-text-secondary">
              Start conversations to build relationships
            </p>
          </GlassCard>
        ) : viewMode === 'network' ? (
          <div className="w-full" style={{ height: 'calc(100vh - 250px)' }}>
            <NetworkGraph
              relationships={filteredRelationships}
              centerQube={selectedQube!}
              allQubes={qubes}
              onNodeClick={(entityId) => {
                // Optionally expand that relationship in grid view or show details
                setExpandedRelationships(new Set([entityId]));
                setViewMode('grid');
              }}
            />
          </div>
        ) : (
          <div className="space-y-4 overflow-y-auto">
            {statusConfig.map(({ status, label, icon, color }) => {
              const group = groupedRelationships[status] || [];
              if (group.length === 0) return null;

              return (
                <div key={status}>
                  <div className="flex items-center gap-2 mb-2 px-2">
                    <span className="text-xl">{icon}</span>
                    <h3 className="text-sm font-semibold text-text-primary">
                      {label}
                    </h3>
                    <span className="text-xs text-text-tertiary">
                      ({group.length})
                    </span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-start">
                    {group.map((rel, index) => {
                      // Use a unique key combining entity_id and index to ensure uniqueness
                      const uniqueKey = `${rel.entity_id}-${index}`;
                      const isExpanded = expandedRelationships.has(uniqueKey);
                      const statusIcon = statusConfig.find(s => s.status === rel.status)?.icon || '🤝';
                      // Check if this is a self-relationship (the owner viewing themselves)
                      const isSelf = rel.entity_id === userId;

                      return (
                        <GlassCard
                          key={uniqueKey}
                          className="overflow-hidden"
                          style={isSelf ? {
                            boxShadow: '0 0 12px rgba(255, 191, 0, 0.3), 0 0 24px rgba(255, 191, 0, 0.15)',
                            border: '1px solid rgba(255, 191, 0, 0.4)',
                          } : undefined}
                        >
                          {/* Collapsible Header */}
                          <div
                            className="flex items-center justify-between p-3 cursor-pointer hover:bg-white/5 transition-colors"
                            onClick={() => toggleRelationship(uniqueKey)}
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="text-xl flex-shrink-0">{statusIcon}</span>
                              <h3
                                className={`text-base font-display truncate ${getEntityNameColor(rel)}`}
                                style={getEntityNameStyle(rel)}
                              >
                                {getEntityDisplayName(rel)}
                              </h3>
                              {/* Self-relationship badge */}
                              {isSelf && (
                                <span className="flex-shrink-0 px-1.5 py-0.5 text-[10px] font-semibold rounded bg-amber-500/20 text-amber-400 border border-amber-500/30">
                                  👤 You
                                </span>
                              )}
                            </div>
                            <span className="text-lg text-text-tertiary flex-shrink-0">
                              {isExpanded ? '▼' : '▶'}
                            </span>
                          </div>

                          {/* Expandable Content */}
                          {isExpanded && (
                            <div className="px-3 pb-3 space-y-3 text-xs max-h-[600px] overflow-y-auto">
                              {/* Level 1: Quick Summary - Always visible when expanded */}

                              {/* Trust & Compatibility */}
                              <div className="pb-3 border-b border-glass-border/50">
                                <div className="flex items-center justify-between mb-2">
                                  <span className="text-text-tertiary font-semibold">Overall Trust:</span>
                                  <span className="text-text-primary font-semibold">
                                    {Math.round(rel.trust)}/100
                                  </span>
                                </div>
                                <ProgressBar value={rel.trust} color="#4CAF50" />

                                <div className="flex items-center justify-between mt-3 mb-1">
                                  <span className="text-text-tertiary font-semibold">Compatibility:</span>
                                  <span className="text-text-primary font-semibold">
                                    {Math.round(rel.compatibility)}/100
                                  </span>
                                </div>
                                <ProgressBar value={rel.compatibility} color="#00aaff" />

                                {rel.is_best_friend && (
                                  <div className="mt-3 text-center">
                                    <span className="text-xs bg-pink-500/20 text-pink-300 px-2 py-1 rounded">
                                      ⭐ Best Friend
                                    </span>
                                  </div>
                                )}
                              </div>

                              {/* Traits - AI-detected (or Owner badge for self) */}
                              <div className="pt-3 border-t border-glass-border/50">
                                {isSelf ? (
                                  <div className="space-y-2">
                                    <h4 className="text-text-secondary font-semibold text-xs flex items-center gap-1">
                                      🧬 Role
                                    </h4>
                                    <div className="flex items-center gap-1.5">
                                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-amber-500/20 text-amber-400 border border-amber-500/30">
                                        👑 Owner
                                      </span>
                                    </div>
                                  </div>
                                ) : (
                                  <TraitManager
                                    entityId={rel.entity_id}
                                    traitScores={rel.trait_scores || {}}
                                    traitDefinitions={traitDefinitions}
                                    manualOverrides={rel.manual_trait_overrides || {}}
                                    qubeId={selectedQubeId!}
                                    userId={userId || ''}
                                    password={password || ''}
                                    onTraitOverride={(trait, visible) => updateRelationshipTraitOverride(rel.entity_id, trait, visible)}
                                    disabled={!password}
                                    maxDisplay={6}
                                  />
                                )}
                              </div>

                              {/* Clearance - Interactive */}
                              <div className="pt-3 border-t border-glass-border/50">
                                <ClearanceSelector
                                  entityId={rel.entity_id}
                                  currentProfile={rel.clearance_profile}
                                  profiles={{ ...DEFAULT_CLEARANCE_PROFILES, ...clearanceProfiles }}
                                  qubeId={selectedQubeId!}
                                  userId={userId || ''}
                                  password={password || ''}
                                  onClearanceChange={(profile) => updateRelationshipClearance(rel.entity_id, profile)}
                                  onOpenAdvanced={() => setEditingClearance(rel.entity_id)}
                                  disabled={!password}
                                />
                                {/* Field overrides display */}
                                {rel.clearance_field_grants && rel.clearance_field_grants.length > 0 && (
                                  <div className="text-xs mt-2">
                                    <span className="text-green-400">+ Extra access: </span>
                                    <span className="text-text-tertiary">{rel.clearance_field_grants.join(', ')}</span>
                                  </div>
                                )}
                                {rel.clearance_field_denials && rel.clearance_field_denials.length > 0 && (
                                  <div className="text-xs mt-1">
                                    <span className="text-red-400">- Restricted: </span>
                                    <span className="text-text-tertiary">{rel.clearance_field_denials.join(', ')}</span>
                                  </div>
                                )}
                              </div>

                              {/* Level 2 Toggle: Show Detailed Metrics */}
                              <button
                                onClick={(e) => { e.stopPropagation(); toggleDetailedMetrics(uniqueKey); }}
                                className="w-full flex items-center justify-center gap-2 py-2 text-text-tertiary hover:text-text-primary transition-colors border-t border-glass-border/50"
                              >
                                <span className="text-xs">
                                  {showDetailedMetrics.has(uniqueKey) ? 'Hide Detailed Metrics' : 'Show Detailed Metrics'}
                                </span>
                                <span className={`text-xs transition-transform ${showDetailedMetrics.has(uniqueKey) ? 'rotate-180' : ''}`}>
                                  ▼
                                </span>
                              </button>

                              {/* Level 2: Detailed Metrics - Collapsible sections */}
                              {showDetailedMetrics.has(uniqueKey) && (
                                <div className="space-y-1">
                                  {/* Radar Chart */}
                                  <div className="pt-2">
                                    <h4 className="text-text-secondary font-semibold mb-2 text-center text-xs">
                                      Core Trust Profile
                                    </h4>
                                    <RelationshipRadarChart
                                      reliability={rel.reliability}
                                      honesty={rel.honesty}
                                      loyalty={rel.loyalty}
                                      respect={rel.respect}
                                      expertise={rel.expertise}
                                      color={getEntityNameStyle(rel).color || '#4A90E2'}
                                    />
                                  </div>

                                  {/* Metric Sections */}
                                  {(() => {
                                    const sections = getMetricSections(rel);
                                    return (
                                      <>
                                        <MetricSection {...sections.coreTrust} />
                                        <MetricSection {...sections.emotionalBond} />
                                        <MetricSection {...sections.personalGrowth} />
                                        <MetricSection {...sections.connectionQuality} />
                                        {hasNegativeDynamics(rel) && (
                                          <MetricSection {...sections.negativeDynamics} />
                                        )}
                                      </>
                                    );
                                  })()}

                                  {/* Timeline Chart */}
                                  {timelineData[rel.entity_id] && timelineData[rel.entity_id].length > 0 && (
                                    <div className="pt-3 border-t border-glass-border/50">
                                      <h4 className="text-text-secondary font-semibold mb-2 text-center text-xs">
                                        Relationship Progression
                                      </h4>
                                      <RelationshipTimelineChart
                                        data={timelineData[rel.entity_id]}
                                        color={getEntityNameStyle(rel).color || '#4A90E2'}
                                      />
                                    </div>
                                  )}

                                  {/* Communication & Collaboration Stats */}
                                  <div className="pt-2 border-t border-glass-border/50">
                                    <button
                                      onClick={(e) => e.stopPropagation()}
                                      className="w-full flex items-center justify-between py-2 text-left hover:bg-white/5 transition-colors"
                                    >
                                      <div className="flex items-center gap-2">
                                        <span>💬</span>
                                        <span className="text-text-secondary font-semibold text-xs">
                                          Communication & Stats
                                        </span>
                                      </div>
                                    </button>
                                    <div className="pb-2 space-y-2">
                                      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Sent:</span>
                                          <span className="text-text-primary">{rel.messages_sent}</span>
                                        </div>
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Received:</span>
                                          <span className="text-text-primary">{rel.messages_received}</span>
                                        </div>
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Success:</span>
                                          <span className="text-green-400">{rel.collaborations_successful}</span>
                                        </div>
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Failed:</span>
                                          <span className="text-red-400">{rel.collaborations_failed}</span>
                                        </div>
                                      </div>
                                    </div>
                                  </div>

                                  {/* Relationship Insights */}
                                  <div className="pt-2 border-t border-glass-border/50">
                                    <button
                                      onClick={(e) => e.stopPropagation()}
                                      className="w-full flex items-center justify-between py-2 text-left hover:bg-white/5 transition-colors"
                                    >
                                      <div className="flex items-center gap-2">
                                        <span>📊</span>
                                        <span className="text-text-secondary font-semibold text-xs">
                                          Insights
                                        </span>
                                      </div>
                                    </button>
                                    <div className="pb-2 space-y-1">
                                      <div className="flex justify-between">
                                        <span className="text-text-tertiary">Duration:</span>
                                        <span className="text-text-primary">{getRelationshipDuration(rel.first_contact)}</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-text-tertiary">First Contact:</span>
                                        <span className="text-text-primary">{formatDate(rel.first_contact)}</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-text-tertiary">Last Interaction:</span>
                                        <span className="text-text-primary">{formatDate(rel.last_interaction)}</span>
                                      </div>
                                      {rel.interaction_frequency_per_day !== undefined && rel.interaction_frequency_per_day > 0 && (
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Daily Interactions:</span>
                                          <span className="text-text-primary">{rel.interaction_frequency_per_day.toFixed(1)}</span>
                                        </div>
                                      )}
                                      {rel.response_time_avg !== undefined && rel.response_time_avg > 0 && (
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Avg Response:</span>
                                          <span className="text-text-primary">{formatResponseTime(rel.response_time_avg)}</span>
                                        </div>
                                      )}
                                      {rel.communication_style && rel.communication_style !== 'unknown' && (
                                        <div className="flex justify-between">
                                          <span className="text-text-tertiary">Style:</span>
                                          <span className="text-text-primary capitalize">{rel.communication_style}</span>
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </GlassCard>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Clearance Edit Modal */}
      {editingClearance && (() => {
        const rel = relationships.find(r => r.entity_id === editingClearance);
        if (!rel) return null;
        return (
          <ClearanceEditModal
            isOpen={true}
            entityId={editingClearance}
            entityName={rel.entity_name || editingClearance}
            currentProfile={rel.clearance_profile}
            currentFieldGrants={rel.clearance_field_grants || []}
            currentFieldDenials={rel.clearance_field_denials || []}
            qubeId={selectedQubeId!}
            userId={userId || ''}
            password={password || ''}
            onClose={() => setEditingClearance(null)}
            onSave={(profile, grants, denials) => {
              setRelationships(prev => prev.map(r =>
                r.entity_id === editingClearance
                  ? { ...r, clearance_profile: profile as Relationship['clearance_profile'], clearance_field_grants: grants, clearance_field_denials: denials }
                  : r
              ));
              setEditingClearance(null);
            }}
          />
        );
      })()}
    </div>
  );
};
