import { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { GlassCard, GlassButton, GlassInput } from '../glass';
import { useAuth } from '../../hooks/useAuth';
import { useChainState } from '../../contexts/ChainStateContext';
import { useUpdater } from '../../hooks/useUpdater';
import { useQubeSelection } from '../../hooks/useQubeSelection';
import { VoiceSettingsPanel } from '../settings/VoiceSettingsPanel';
import DarkSelect from '../DarkSelect';
import { useCelebration } from '../../contexts/CelebrationContext';

interface APIKeyStatus {
  provider: string;
  configured: boolean;
  masked_value?: string;
  validated?: boolean;
  validation_message?: string;
}

interface ValidationResult {
  valid: boolean;
  message: string;
  details?: any;
}

interface BlockPreferences {
  individual_auto_anchor: boolean;
  individual_anchor_threshold: number;
  group_auto_anchor: boolean;
  group_anchor_threshold: number;
  auto_sync_ipfs_on_anchor: boolean;
  auto_sync_ipfs_periodic: boolean;
  auto_sync_ipfs_interval: number;
}

interface RelationshipSettings {
  difficulty: 'quick' | 'normal' | 'long' | 'extreme';
  description: string;
}

interface DifficultyPreset {
  name: string;
  description: string;
  min_interactions: {
    acquaintance: number;
    friend: number;
    close_friend: number;
    best_friend: number;
  };
}

interface DecisionConfig {
  trust_threshold: number;
  expertise_threshold: number;
  collaboration_threshold: number;
  confidence_threshold: number;
  humility_threshold: number;
  metric_influence: number;
  validation_strictness: number;
  max_antagonism: number;
  max_distrust: number;
  max_betrayal: number;
  enable_auto_temperature: boolean;
  enable_validation_layer: boolean;
  enable_metric_tools: boolean;
  auto_thresholds: boolean;
}

interface MemoryConfig {
  recall_threshold: number;
  max_recalls: number;
  decay_rate: number;
  semantic_weight: number;
  keyword_weight: number;
  temporal_weight: number;
  relationship_weight: number;
}

interface RelayPeer {
  multiaddr: string;
  label: string;
  operator: string;
  builtin: boolean;
  online: boolean | null;
  latency_ms: number | null;
}

interface RelayStatus {
  running: boolean;
  peer_id: string | null;
  multiaddr: string | null;
  peer_count: number;
  online_peers: number;
}

interface RelayPreferences {
  relay_enabled: boolean;
  relay_listen_port: number;
  relay_max_connections: number;
  relay_retention_days: number;
  relay_custom_peers: string[];
  p2pd_binary_path: string | null;
}

interface EndpointPreferences {
  fulcrum_nodes: string[];
  nostr_relays: string[];
}

export const SettingsTab: React.FC = () => {
  const { userId, password, autoLockEnabled, autoLockTimeout, setAutoLockSettings } = useAuth();
  const { invalidateCache, loadChainState } = useChainState();
  const { settings: celebrationSettings, updateSettings: updateCelebrationSettings } = useCelebration();

  const {
    updateAvailable,
    updateStatus,
    isChecking,
    isDownloading,
    downloadProgress,
    error: updateError,
    isHeavy,
    heavyStatus,
    updateSize,
    checkForUpdates,
    installUpdate,
    dismissUpdate,
  } = useUpdater(true); // Check for updates on mount

  // Get selected qube from roster for per-qube settings
  const { selectionByTab } = useQubeSelection();
  const selectedQubeIdForSettings = selectionByTab.settings?.[0] || null;

  const [apiKeys, setApiKeys] = useState<Record<string, string>>({
    openai: '',
    anthropic: '',
    google: '',
    deepseek: '',
    perplexity: '',
    venice: '',
    nanogpt: '',
    pinata_jwt: '',
    elevenlabs: '',
    deepgram: '',
  });

  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [loadingKeys, setLoadingKeys] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [validatingKey, setValidatingKey] = useState<string | null>(null);
  const [keyStatuses, setKeyStatuses] = useState<Record<string, APIKeyStatus>>({});

  // Block preferences state
  const [blockPreferences, setBlockPreferences] = useState<BlockPreferences>({
    individual_auto_anchor: true,
    individual_anchor_threshold: 10,
    group_auto_anchor: true,
    group_anchor_threshold: 5,
    auto_sync_ipfs_on_anchor: true,
    auto_sync_ipfs_periodic: true,
    auto_sync_ipfs_interval: 15,
  });
  const [loadingPreferences, setLoadingPreferences] = useState(true);
  const [savingPreferences, setSavingPreferences] = useState(false);

  // Relationship settings state
  const [relationshipSettings, setRelationshipSettings] = useState<RelationshipSettings>({
    difficulty: 'long',
    description: 'Relationships take years to develop, making them truly meaningful'
  });
  const [loadingRelationshipSettings, setLoadingRelationshipSettings] = useState(true);
  const [savingRelationshipSettings, setSavingRelationshipSettings] = useState(false);
  const [difficultyPresets, setDifficultyPresets] = useState<Record<string, DifficultyPreset>>({});

  // Local input state (for typing without triggering API calls)
  const [individualThresholdInput, setIndividualThresholdInput] = useState('10');
  const [groupThresholdInput, setGroupThresholdInput] = useState('5');

  // Google TTS credentials path state
  const [googleTTSPath, setGoogleTTSPath] = useState('');
  const [loadingGoogleTTSPath, setLoadingGoogleTTSPath] = useState(true);
  const [savingGoogleTTSPath, setSavingGoogleTTSPath] = useState(false);

  // Decision intelligence config state
  const [decisionConfig, setDecisionConfig] = useState<DecisionConfig>({
    trust_threshold: 70,
    expertise_threshold: 60,
    collaboration_threshold: 65,
    confidence_threshold: 60,
    humility_threshold: 70,
    metric_influence: 70,
    validation_strictness: 50,
    max_antagonism: 30,
    max_distrust: 40,
    max_betrayal: 10,
    enable_auto_temperature: true,
    enable_validation_layer: true,
    enable_metric_tools: true,
    auto_thresholds: false,
  });
  const [loadingDecisionConfig, setLoadingDecisionConfig] = useState(true);
  const [savingDecisionConfig, setSavingDecisionConfig] = useState(false);

  // Trust personality state
  const [availableQubes, setAvailableQubes] = useState<Array<{ qube_id: string; name: string }>>([]);
  const [selectedQubeForTrust, setSelectedQubeForTrust] = useState<string>('');
  const [trustPersonality, setTrustPersonality] = useState<'cautious' | 'balanced' | 'social' | 'analytical'>('balanced');
  const [loadingTrustPersonality, setLoadingTrustPersonality] = useState(false);
  const [savingTrustPersonality, setSavingTrustPersonality] = useState(false);

  // Memory recall config state
  const [memoryConfig, setMemoryConfig] = useState<MemoryConfig>({
    recall_threshold: 15.0,
    max_recalls: 5,
    decay_rate: 0.1,
    semantic_weight: 0.4,
    keyword_weight: 0.3,
    temporal_weight: 0.15,
    relationship_weight: 0.1,
  });
  // Local state for smooth slider dragging (synced on release)
  const [localMemoryConfig, setLocalMemoryConfig] = useState<MemoryConfig>({
    recall_threshold: 15.0,
    max_recalls: 5,
    decay_rate: 0.1,
    semantic_weight: 0.4,
    keyword_weight: 0.3,
    temporal_weight: 0.15,
    relationship_weight: 0.1,
  });
  const [loadingMemoryConfig, setLoadingMemoryConfig] = useState(true);
  const [savingMemoryConfig, setSavingMemoryConfig] = useState(false);
  const [showAdvancedRecall, setShowAdvancedRecall] = useState(false);

  // Change password state
  const [changePwOld, setChangePwOld] = useState('');
  const [changePwNew, setChangePwNew] = useState('');
  const [changePwConfirm, setChangePwConfirm] = useState('');
  const [isChangingPw, setIsChangingPw] = useState(false);
  const [changePwError, setChangePwError] = useState<string | null>(null);
  const [changePwSuccess, setChangePwSuccess] = useState<string | null>(null);

  // Collapsible panel state (all collapsed by default)
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, boolean>>({
    apiKeys: true,
    googleTTS: true,
    blockSettings: true,
    blockRecall: true,
    relationshipDifficulty: true,
    trustPersonality: true,
    voiceSettings: true,
    gpuAcceleration: true,
    localModels: true,
    decisionIntelligence: true,
    security: true,
    celebrationSettings: true,
    softwareUpdates: true,
    endpoints: true,
  });

  // Relay Node state
  const [relayStatus, setRelayStatus] = useState<RelayStatus>({ running: false, peer_id: null, multiaddr: null, peer_count: 0, online_peers: 0 });
  const [relayPeers, setRelayPeers] = useState<RelayPeer[]>([]);
  const [relayPrefs, setRelayPrefs] = useState<RelayPreferences>({ relay_enabled: true, relay_listen_port: 0, relay_max_connections: 50, relay_retention_days: 7, relay_custom_peers: [], p2pd_binary_path: null });
  const [relayLoading, setRelayLoading] = useState(false);
  const [relayBundleStatus, setRelayBundleStatus] = useState<string | null>(null);
  const [newRelayPeer, setNewRelayPeer] = useState('');

  // Endpoints state — pre-filled with well-known defaults (overwritten by saved prefs on load)
  const [endpointPrefs, setEndpointPrefs] = useState<EndpointPreferences>({
    fulcrum_nodes: [
      'wss://bch.imaginary.cash:50004',
      'wss://electroncash.de:50004',
      'wss://bch.loping.net:50004',
      'wss://blackie.c3-soft.com:50004',
      'wss://electroncash.dk:50002',
      'wss://electron.jochen-hoenicke.de:51002',
      'wss://bitcoincash.network:50004',
      'wss://bch.soul-dev.com:50002',
    ],
    nostr_relays: [
      'wss://relay.damus.io',
      'wss://nos.lol',
      'wss://relay.nostr.band',
      'wss://relay.snort.social',
      'wss://nostr.wine',
      'wss://relay.current.fyi',
      'wss://relay.primal.net',
      'wss://nostr-pub.wellorder.net',
    ],
  });
  const [endpointsDirty, setEndpointsDirty] = useState(false);
  const [savingEndpoints, setSavingEndpoints] = useState(false);
  const [endpointSaveMsg, setEndpointSaveMsg] = useState<string | null>(null);

  const loadRelayData = async () => {
    try {
      const [statusRes, peersRes] = await Promise.all([
        invoke<any>('get_relay_status', { userId }),
        invoke<any>('get_relay_peers', { userId }),
      ]);
      if (statusRes?.success !== false) setRelayStatus(statusRes);
      if (peersRes?.success !== false && peersRes?.peers) setRelayPeers(peersRes.peers);
    } catch {}
  };

  const handleStartRelay = async () => {
    setRelayLoading(true);
    try {
      const res = await invoke<any>('init_relay_node', { userId, password });
      if (res?.success !== false) { setRelayStatus({ ...relayStatus, running: true, ...res }); }
    } catch {}
    setRelayLoading(false);
    await loadRelayData();
  };

  const handleStopRelay = async () => {
    setRelayLoading(true);
    try { await invoke('stop_relay_node', { userId }); setRelayStatus(s => ({ ...s, running: false, peer_id: null, multiaddr: null })); } catch {}
    setRelayLoading(false);
  };

  const handleAddRelayPeer = async () => {
    if (!newRelayPeer.trim()) return;
    await invoke('add_relay_peer', { userId, multiaddr: newRelayPeer.trim() });
    setNewRelayPeer('');
    await loadRelayData();
  };

  const handleRemoveRelayPeer = async (multiaddr: string) => {
    await invoke('remove_relay_peer', { userId, multiaddr });
    await loadRelayData();
  };

  const handleCheckBundleUpdate = async () => {
    setRelayBundleStatus('Checking...');
    try {
      const res = await invoke<any>('check_relay_bundle_update', { userId });
      setRelayBundleStatus(res?.update_available ? `Update available: ${res.latest_version}` : `Up to date (${res?.current_version || 'none'})`);
    } catch (e: any) { setRelayBundleStatus(`Error: ${e}`); }
  };

  const handleUpdateBundle = async () => {
    setRelayBundleStatus('Updating...');
    try {
      const res = await invoke<any>('update_relay_bundle', { userId });
      setRelayBundleStatus(res?.message || (res?.success ? 'Updated!' : 'Failed'));
    } catch (e: any) { setRelayBundleStatus(`Error: ${e}`); }
  };

  const loadEndpoints = async () => {
    try {
      const res = await invoke<any>('get_endpoint_preferences', { userId });
      if (res?.endpoints) setEndpointPrefs(res.endpoints);
    } catch {}
  };

  const handleSaveEndpoints = async () => {
    setSavingEndpoints(true);
    setEndpointSaveMsg(null);
    try {
      const prefs_json = JSON.stringify(endpointPrefs);
      await invoke('update_endpoint_preferences', { userId, password, prefsJson: prefs_json });
      setEndpointsDirty(false);
      setEndpointSaveMsg('Saved — changes apply on page reload');
    } catch (e: any) { setEndpointSaveMsg(`Error: ${e}`); }
    setSavingEndpoints(false);
  };

  const handleResetEndpoints = async () => {
    try {
      const res = await invoke<any>('reset_endpoint_preferences', { userId, password });
      if (res?.endpoints) { setEndpointPrefs(res.endpoints); setEndpointsDirty(false); setEndpointSaveMsg('Reset to defaults — changes apply on page reload'); }
    } catch {}
  };

  const handleChangePassword = async () => {
    setChangePwError(null);
    setChangePwSuccess(null);

    if (changePwNew !== changePwConfirm) {
      setChangePwError('New passwords do not match.');
      return;
    }
    if (changePwNew.length < 8) {
      setChangePwError('New password must be at least 8 characters.');
      return;
    }
    if (changePwOld === changePwNew) {
      setChangePwError('New password must be different from current password.');
      return;
    }

    setIsChangingPw(true);
    try {
      const result = await invoke<{ success: boolean; re_encrypted_count?: number; error?: string }>('change_master_password', {
        userId,
        oldPassword: changePwOld,
        newPassword: changePwNew,
      });

      if (result.success) {
        setChangePwSuccess(`Password changed successfully. ${result.re_encrypted_count} items re-encrypted.`);
        setChangePwOld('');
        setChangePwNew('');
        setChangePwConfirm('');
      } else {
        setChangePwError(result.error || 'Password change failed.');
      }
    } catch (err) {
      setChangePwError(`${err}`);
    } finally {
      setIsChangingPw(false);
    }
  };

  const togglePanel = (panel: string) => {
    setCollapsedPanels(prev => ({ ...prev, [panel]: !prev[panel] }));
  };

  // GPU Acceleration state
  const [gpuStatus, setGpuStatus] = useState<{
    success: boolean;
    gpu_detected: boolean;
    gpu_name?: string;
    gpu_vram_gb?: number;
    driver_version?: string;
    cuda_available: boolean;
    torch_version?: string;
    torch_cuda_version?: string;
    torch_device: string;
    upgrade_available: boolean;
    is_frozen: boolean;
  } | null>(null);
  const [gpuInstalling, setGpuInstalling] = useState(false);
  const [gpuInstallProgress, setGpuInstallProgress] = useState<{
    phase?: string;
    total_bytes?: number;
    downloaded_bytes?: number;
    speed_mbps?: number;
    eta_seconds?: number;
    status?: string;
    error?: string;
  }>({});
  const [gpuUninstalling, setGpuUninstalling] = useState(false);
  const [gpuError, setGpuError] = useState<string | null>(null);

  // Local Models state
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [isPullingModel, setIsPullingModel] = useState<string | null>(null);
  const [pullProgress, setPullProgress] = useState<{ status: string; completed?: number; total?: number } | null>(null);
  const [newModelInput, setNewModelInput] = useState('');
  const [ttsModelStatus, setTtsModelStatus] = useState<{
    kokoro_installed: boolean;
    sentence_transformers_installed: boolean;
    whisper_installed: boolean;
    models_dir: string;
  } | null>(null);
  const [isUpdatingTts, setIsUpdatingTts] = useState(false);
  const [ttsUpdateResult, setTtsUpdateResult] = useState<string | null>(null);
  const pullListenerRef = useRef<(() => void) | null>(null);

  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  };

  const providerLabels: Record<string, string> = {
    openai: 'OpenAI',
    anthropic: 'Anthropic (Claude)',
    google: 'Google AI (Gemini)',
    deepseek: 'DeepSeek',
    perplexity: 'Perplexity',
    venice: 'Venice (Private AI)',
    nanogpt: 'NanoGPT (Pay-per-prompt)',
    pinata_jwt: 'Pinata IPFS',
    elevenlabs: 'ElevenLabs (TTS)',
    deepgram: 'Deepgram (STT)',
  };

  const providerPlaceholders: Record<string, string> = {
    openai: 'sk-...',
    anthropic: 'sk-ant-...',
    google: 'AIza...',
    deepseek: 'sk-...',
    perplexity: 'pplx-...',
    venice: '...',
    nanogpt: '...',
    pinata_jwt: 'eyJ...',
    elevenlabs: '...',
    deepgram: '...',
  };

  // Load API keys on mount
  useEffect(() => {
    loadConfiguredKeys();
  }, [userId]);

  // Load block preferences on mount
  useEffect(() => {
    loadBlockPreferences();
  }, [userId]);

  // Load relationship settings on mount
  useEffect(() => {
    loadRelationshipSettings();
  }, [userId]);

  // Load Google TTS path on mount
  useEffect(() => {
    loadGoogleTTSPath();
  }, [userId]);

  // Load decision config on mount
  useEffect(() => {
    loadDecisionConfig();
  }, [userId]);

  // Load available qubes on mount
  useEffect(() => {
    loadAvailableQubes();
  }, [userId]);

  // Load trust personality when selected qube changes
  useEffect(() => {
    if (selectedQubeForTrust) {
      loadTrustPersonality(selectedQubeForTrust);
    }
  }, [selectedQubeForTrust]);

  // Load memory config on mount
  useEffect(() => {
    loadMemoryConfig();
  }, [userId]);

  // Check GPU acceleration status on mount
  useEffect(() => {
    if (userId) {
      checkGpuAcceleration();
    }
  }, [userId]);

  // Load endpoints on mount
  useEffect(() => {
    if (userId) loadEndpoints();
  }, [userId]);

  // Listen for Ollama pull progress events
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    listen<{ model: string; status: string; completed?: number; total?: number }>('ollama-pull-progress', (event) => {
      if (event.payload.status === 'done') {
        setPullProgress(null);
        setIsPullingModel(null);
        // Refresh model list after pull
        checkOllamaModels();
      } else {
        setPullProgress({ status: event.payload.status, completed: event.payload.completed, total: event.payload.total });
      }
    }).then((fn) => {
      unlisten = fn;
      pullListenerRef.current = fn;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  const checkOllamaModels = async () => {
    try {
      const result = await invoke<{ running: boolean; models: string[] }>('check_ollama_status');
      if (result.running) {
        setOllamaModels(result.models);
      }
    } catch (err) {
      console.error('Failed to check Ollama models:', err);
    }
  };

  const checkTtsModels = async () => {
    if (!userId) return;
    try {
      const result = await invoke<any>('check_local_tts_models', { userId });
      setTtsModelStatus(result);
    } catch (err) {
      console.error('Failed to check TTS models:', err);
    }
  };

  const handlePullModel = async (modelName: string) => {
    if (isPullingModel) return;
    setIsPullingModel(modelName);
    setPullProgress({ status: 'starting...' });
    try {
      await invoke('pull_ollama_model', { modelName });
    } catch (err) {
      setIsPullingModel(null);
      setPullProgress(null);
      alert(`Failed to pull ${modelName}: ${err}`);
    }
  };

  const handleUpdateAllOllamaModels = async () => {
    if (isPullingModel || ollamaModels.length === 0) return;
    for (const model of ollamaModels) {
      setIsPullingModel(model);
      setPullProgress({ status: 'starting...' });
      try {
        await invoke('pull_ollama_model', { modelName: model });
      } catch (err) {
        console.error(`Failed to pull ${model}:`, err);
      }
    }
  };

  const handleUpdateTtsModels = async () => {
    if (!userId || isUpdatingTts) return;
    setIsUpdatingTts(true);
    setTtsUpdateResult(null);
    try {
      const result = await invoke<{ success: boolean; updated: string[]; errors: string[] }>('update_local_tts_models', { userId });
      if (result.success) {
        setTtsUpdateResult(`Updated: ${result.updated.join(', ') || 'already up to date'}`);
      } else {
        setTtsUpdateResult(`Errors: ${result.errors.join('; ')}`);
      }
      await checkTtsModels();
    } catch (err) {
      setTtsUpdateResult(`Failed: ${err}`);
    } finally {
      setIsUpdatingTts(false);
    }
  };

  const checkGpuAcceleration = async () => {
    try {
      const result = await invoke<any>('check_gpu_acceleration', { userId });
      if (result.success) {
        setGpuStatus(result);
      }
    } catch (err) {
      console.error('Failed to check GPU acceleration:', err);
    }
  };

  const handleInstallGpu = async () => {
    setGpuInstalling(true);
    setGpuInstallProgress({});
    setGpuError(null);

    try {
      const result = await invoke<{ success: boolean; install_id?: string; error?: string }>('install_gpu_acceleration', {
        userId,
      });

      if (result.success && result.install_id) {
        const pollProgress = async () => {
          try {
            const progress = await invoke<any>('get_gpu_install_progress', {
              userId,
              installId: result.install_id,
            });

            if (progress.success) {
              setGpuInstallProgress(progress);

              if (progress.status === 'completed') {
                setGpuInstalling(false);
                await checkGpuAcceleration();
              } else if (progress.status === 'failed') {
                setGpuError(progress.error || 'GPU installation failed');
                setGpuInstalling(false);
              } else {
                setTimeout(pollProgress, 1000);
              }
            }
          } catch {
            setGpuInstalling(false);
          }
        };
        pollProgress();
      } else {
        setGpuError(result.error || 'Failed to start GPU installation');
        setGpuInstalling(false);
      }
    } catch (err) {
      setGpuError(err instanceof Error ? err.message : 'Failed to install GPU acceleration');
      setGpuInstalling(false);
    }
  };

  const handleUninstallGpu = async () => {
    if (!confirm('Revert to CPU-only PyTorch? You can re-install GPU acceleration later.')) return;

    setGpuUninstalling(true);
    try {
      const result = await invoke<{ success: boolean; error?: string }>('uninstall_gpu_acceleration', { userId });
      if (result.success) {
        await checkGpuAcceleration();
      } else {
        setGpuError(result.error || 'Failed to uninstall GPU acceleration');
      }
    } catch (err) {
      setGpuError(err instanceof Error ? err.message : 'Failed to uninstall');
    } finally {
      setGpuUninstalling(false);
    }
  };

  const loadGoogleTTSPath = async () => {
    try {
      setLoadingGoogleTTSPath(true);
      const result = await invoke<{ path: string | null }>('get_google_tts_path', {
        userId,
      });
      setGoogleTTSPath(result.path || '');
    } catch (error) {
      console.error('Failed to load Google TTS path:', error);
    } finally {
      setLoadingGoogleTTSPath(false);
    }
  };

  const handleSaveGoogleTTSPath = async () => {
    try {
      setSavingGoogleTTSPath(true);
      await invoke('set_google_tts_path', {
        userId,
        path: googleTTSPath.trim() === '' ? 'none' : googleTTSPath,
      });
      alert('✅ Google TTS credentials path saved');
    } catch (error) {
      console.error('Failed to save Google TTS path:', error);
      alert(`❌ Error saving path: ${String(error)}`);
    } finally {
      setSavingGoogleTTSPath(false);
    }
  };

  const loadDecisionConfig = async () => {
    try {
      setLoadingDecisionConfig(true);
      const result = await invoke<{ success: boolean; config: DecisionConfig }>('get_decision_config', {
        userId,
      });
      if (result.success && result.config) {
        setDecisionConfig(result.config);
      }
    } catch (error) {
      console.error('Failed to load decision config:', error);
    } finally {
      setLoadingDecisionConfig(false);
    }
  };

  const handleDecisionConfigChange = async (updates: Partial<DecisionConfig>) => {
    try {
      setSavingDecisionConfig(true);
      const newConfig = { ...decisionConfig, ...updates };
      setDecisionConfig(newConfig);

      await invoke('update_decision_config', {
        userId,
        configJson: JSON.stringify(updates),
      });
    } catch (error) {
      console.error('Failed to update decision config:', error);
      alert(`❌ Error updating config: ${String(error)}`);
      // Reload to get the correct state
      await loadDecisionConfig();
    } finally {
      setSavingDecisionConfig(false);
    }
  };

  const loadMemoryConfig = async () => {
    try {
      setLoadingMemoryConfig(true);
      const result = await invoke<{ success: boolean; config: MemoryConfig }>('get_memory_config', {
        userId,
      });
      if (result.success && result.config) {
        setMemoryConfig(result.config);
        setLocalMemoryConfig(result.config); // Sync local state
      }
    } catch (error) {
      console.error('Failed to load memory config:', error);
    } finally {
      setLoadingMemoryConfig(false);
    }
  };

  // Update local state while dragging (no API call)
  const handleMemoryConfigSliderChange = (updates: Partial<MemoryConfig>) => {
    setLocalMemoryConfig(prev => ({ ...prev, ...updates }));
  };

  // Save to backend when slider is released
  const handleMemoryConfigSliderRelease = async () => {
    // Find what changed between memoryConfig and localMemoryConfig
    const updates: Partial<MemoryConfig> = {};
    for (const key of Object.keys(localMemoryConfig) as (keyof MemoryConfig)[]) {
      if (localMemoryConfig[key] !== memoryConfig[key]) {
        (updates as any)[key] = localMemoryConfig[key];
      }
    }

    if (Object.keys(updates).length === 0) return; // Nothing changed

    try {
      setSavingMemoryConfig(true);
      setMemoryConfig(localMemoryConfig); // Update main state

      await invoke('update_memory_config', {
        userId,
        configJson: JSON.stringify(updates),
      });
    } catch (error) {
      console.error('Failed to update memory config:', error);
      alert(`❌ Error updating config: ${String(error)}`);
      // Reload to get the correct state
      await loadMemoryConfig();
    } finally {
      setSavingMemoryConfig(false);
    }
  };

  const loadAvailableQubes = async () => {
    try {
      const qubes = await invoke<any[]>('list_qubes', { userId, password });
      setAvailableQubes(qubes.map(q => ({
        qube_id: q.qube_id,
        name: q.name
      })));
      if (qubes.length > 0) {
        setSelectedQubeForTrust(qubes[0].qube_id);
      }
    } catch (error) {
      console.error('Failed to load qubes:', error);
    }
  };

  const loadTrustPersonality = async (qubeId: string) => {
    if (!qubeId || !password) return;
    try {
      setLoadingTrustPersonality(true);
      const result = await invoke<{ trust_profile: string }>('get_trust_personality', {
        userId,
        qubeId,
        password
      });
      setTrustPersonality(result.trust_profile as any || 'balanced');
    } catch (error) {
      console.error('Failed to load trust personality:', error);
      setTrustPersonality('balanced');
    } finally {
      setLoadingTrustPersonality(false);
    }
  };

  const handleTrustPersonalityChange = async (personality: 'cautious' | 'balanced' | 'social' | 'analytical') => {
    if (!selectedQubeForTrust) return;
    try {
      setSavingTrustPersonality(true);
      setTrustPersonality(personality);

      await invoke('update_trust_personality', {
        userId,
        qubeId: selectedQubeForTrust,
        trustProfile: personality
      });

      // Invalidate and refresh chain state cache
      invalidateCache(selectedQubeForTrust);
      await loadChainState(selectedQubeForTrust, true);

      alert(`✅ Trust personality updated to "${personality}"`);
    } catch (error) {
      console.error('Failed to update trust personality:', error);
      alert(`❌ Error updating trust personality: ${String(error)}`);
      // Reload to get correct state
      await loadTrustPersonality(selectedQubeForTrust);
    } finally {
      setSavingTrustPersonality(false);
    }
  };

  const loadConfiguredKeys = async () => {
    try {
      setLoadingKeys(true);
      const result = await invoke<{ providers: string[] }>('get_configured_api_keys', {
        userId,
        password,
      });

      const statuses: Record<string, APIKeyStatus> = {};
      for (const provider of Object.keys(apiKeys)) {
        statuses[provider] = {
          provider,
          configured: result.providers.includes(provider),
          validated: undefined,
        };
      }

      setKeyStatuses(statuses);
    } catch (error) {
      console.error('Failed to load configured keys:', error);
    } finally {
      setLoadingKeys(false);
    }
  };

  const handleKeyChange = (provider: string, value: string) => {
    console.log(`[API Key] handleKeyChange called for ${provider}, value length: ${value?.length || 0}`);
    setApiKeys(prev => ({ ...prev, [provider]: value }));

    // Clear validation status when user changes the key
    setKeyStatuses(prev => ({
      ...prev,
      [provider]: {
        ...prev[provider],
        validated: undefined,
        validation_message: undefined,
      },
    }));
  };

  const toggleShowKey = (provider: string) => {
    setShowKeys(prev => ({ ...prev, [provider]: !prev[provider] }));
  };

  const handleSaveKey = async (provider: string) => {
    const apiKey = apiKeys[provider];
    if (!apiKey || !apiKey.trim()) {
      alert('Please enter an API key');
      return;
    }

    try {
      setSavingKey(provider);
      const result = await invoke<{ success: boolean; error?: string }>('save_api_key', {
        userId,
        provider,
        apiKey,
        password,
      });

      if (result.success) {
        // Update status
        setKeyStatuses(prev => ({
          ...prev,
          [provider]: {
            ...prev[provider],
            configured: true,
          },
        }));

        // Clear input field
        setApiKeys(prev => ({ ...prev, [provider]: '' }));

        alert(`✅ ${providerLabels[provider]} API key saved successfully`);
      } else {
        alert(`❌ Failed to save API key: ${result.error}`);
      }
    } catch (error) {
      console.error('Failed to save API key:', error);
      alert(`❌ Error saving API key: ${String(error)}`);
    } finally {
      setSavingKey(null);
    }
  };

  const handleValidateKey = async (provider: string) => {
    const apiKey = apiKeys[provider];
    const isConfigured = keyStatuses[provider]?.configured;

    // If input field is empty but key is configured, test the saved key
    if ((!apiKey || !apiKey.trim()) && !isConfigured) {
      alert('Please enter an API key to validate');
      return;
    }

    try {
      setValidatingKey(provider);

      // If testing a saved key (input is empty), pass password to decrypt
      const testingSavedKey = !apiKey || !apiKey.trim();

      const result = await invoke<ValidationResult>('validate_api_key', {
        userId,
        provider,
        apiKey: apiKey || '__SAVED__', // Use placeholder to indicate testing saved key
        password: testingSavedKey ? password : undefined, // Pass password only for saved keys
      });

      setKeyStatuses(prev => ({
        ...prev,
        [provider]: {
          ...prev[provider],
          validated: result.valid,
          validation_message: result.message,
        },
      }));

      if (result.valid) {
        alert(`✅ ${providerLabels[provider]}: ${result.message}`);
      } else {
        alert(`❌ ${providerLabels[provider]}: ${result.message}`);
      }
    } catch (error) {
      console.error('Failed to validate API key:', error);
      alert(`❌ Error validating API key: ${String(error)}`);

      setKeyStatuses(prev => ({
        ...prev,
        [provider]: {
          ...prev[provider],
          validated: false,
          validation_message: String(error),
        },
      }));
    } finally {
      setValidatingKey(null);
    }
  };

  const handleDeleteKey = async (provider: string) => {
    if (!confirm(`Are you sure you want to delete the ${providerLabels[provider]} API key?`)) {
      return;
    }

    try {
      const result = await invoke<{ success: boolean; error?: string }>('delete_api_key', {
        userId,
        provider,
        password,
      });

      if (result.success) {
        setKeyStatuses(prev => ({
          ...prev,
          [provider]: {
            ...prev[provider],
            configured: false,
            validated: undefined,
            validation_message: undefined,
          },
        }));

        alert(`✅ ${providerLabels[provider]} API key deleted`);
      } else {
        alert(`❌ Failed to delete API key: ${result.error}`);
      }
    } catch (error) {
      console.error('Failed to delete API key:', error);
      alert(`❌ Error deleting API key: ${String(error)}`);
    }
  };

  const loadBlockPreferences = async () => {
    try {
      setLoadingPreferences(true);
      const prefs = await invoke<BlockPreferences>('get_block_preferences', {
        userId,
      });
      setBlockPreferences(prefs);
      // Update local input state
      setIndividualThresholdInput(prefs.individual_anchor_threshold.toString());
      setGroupThresholdInput(prefs.group_anchor_threshold.toString());
    } catch (error) {
      console.error('Failed to load block preferences:', error);
    } finally {
      setLoadingPreferences(false);
    }
  };

  const handleToggleChange = async (
    field: 'individual_auto_anchor' | 'group_auto_anchor' | 'auto_sync_ipfs_on_anchor' | 'auto_sync_ipfs_periodic',
    value: boolean
  ) => {
    try {
      setSavingPreferences(true);

      // Optimistically update UI
      setBlockPreferences(prev => ({ ...prev, [field]: value }));

      const result = await invoke<BlockPreferences>('update_block_preferences', {
        userId,
        individualAutoAnchor: field === 'individual_auto_anchor' ? value : undefined,
        groupAutoAnchor: field === 'group_auto_anchor' ? value : undefined,
        autoSyncIpfsOnAnchor: field === 'auto_sync_ipfs_on_anchor' ? value : undefined,
        autoSyncIpfsPeriodic: field === 'auto_sync_ipfs_periodic' ? value : undefined,
      });

      // Update with server response
      setBlockPreferences(result);

      // Invalidate cache for all qubes since this is a user-level preference
      for (const qube of availableQubes) {
        invalidateCache(qube.qube_id);
      }
      // Force refresh for all qubes
      for (const qube of availableQubes) {
        loadChainState(qube.qube_id, true);
      }
    } catch (error) {
      console.error('Failed to update preference:', error);
      alert(`❌ Error updating preference: ${String(error)}`);
      await loadBlockPreferences();
    } finally {
      setSavingPreferences(false);
    }
  };

  const handleThresholdBlur = async (
    field: 'individual_anchor_threshold' | 'group_anchor_threshold',
    inputValue: string
  ) => {
    const parsed = parseInt(inputValue) || 5;
    const value = Math.max(5, Math.min(50, parsed));

    // Don't save if value hasn't changed
    if (value === blockPreferences[field]) {
      return;
    }

    try {
      setSavingPreferences(true);

      const result = await invoke<BlockPreferences>('update_block_preferences', {
        userId,
        individualAnchorThreshold: field === 'individual_anchor_threshold' ? value : undefined,
        groupAnchorThreshold: field === 'group_anchor_threshold' ? value : undefined,
      });

      // Update with server response
      setBlockPreferences(result);
      setIndividualThresholdInput(result.individual_anchor_threshold.toString());
      setGroupThresholdInput(result.group_anchor_threshold.toString());

      // Invalidate cache for all qubes since this is a user-level preference
      for (const qube of availableQubes) {
        invalidateCache(qube.qube_id);
      }
      // Force refresh for all qubes
      for (const qube of availableQubes) {
        loadChainState(qube.qube_id, true);
      }
    } catch (error) {
      console.error('Failed to update preference:', error);
      alert(`❌ Error updating preference: ${String(error)}`);
      await loadBlockPreferences();
    } finally {
      setSavingPreferences(false);
    }
  };

  const loadRelationshipSettings = async () => {
    try {
      setLoadingRelationshipSettings(true);

      // Load current difficulty setting
      const settings = await invoke<RelationshipSettings>('get_relationship_difficulty', {
        userId,
      });
      setRelationshipSettings(settings);

      // Load all difficulty presets for display
      const presets = await invoke<Record<string, DifficultyPreset>>('get_difficulty_presets');
      setDifficultyPresets(presets);
    } catch (error) {
      console.error('Failed to load relationship settings:', error);
      // Set default if error
      setRelationshipSettings({
        difficulty: 'long',
        description: 'Relationships take years to develop, making them truly meaningful'
      });
    } finally {
      setLoadingRelationshipSettings(false);
    }
  };

  const handleDifficultyChange = async (difficulty: 'quick' | 'normal' | 'long' | 'extreme') => {
    try {
      setSavingRelationshipSettings(true);

      const result = await invoke<RelationshipSettings>('set_relationship_difficulty', {
        userId,
        difficulty,
      });

      setRelationshipSettings(result);
      alert(`✅ Relationship difficulty updated to "${result.description}"`);
    } catch (error) {
      console.error('Failed to update relationship difficulty:', error);
      alert(`❌ Error updating difficulty: ${String(error)}`);
      await loadRelationshipSettings();
    } finally {
      setSavingRelationshipSettings(false);
    }
  };

  if (loadingKeys) {
    return (
      <div className="p-6">
        <GlassCard className="p-6">
          <p className="text-text-secondary">Loading settings...</p>
        </GlassCard>
      </div>
    );
  }

  return (
    <div className="p-4">
      <div className="max-w-6xl mx-auto space-y-4">
        {/* Header */}
        <div>
          <h1 className="text-xl font-display text-text-primary mb-1">
            Settings
          </h1>
          <p className="text-sm text-text-secondary">
            Configure API keys and global preferences
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Left Column */}
          <div>
            {/* API Keys Section */}
            <GlassCard className="p-4">
              <button
                onClick={() => togglePanel('apiKeys')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🔑 API Keys
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.apiKeys ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.apiKeys && (
                <>
              <p className="text-xs text-text-tertiary mb-3 mt-2">
                Your API keys are encrypted with your master password and stored securely.
              </p>

              <div className="space-y-3 mb-4">
                {Object.keys(apiKeys).map((provider) => {
                  const status = keyStatuses[provider];
                  const isValidated = status?.validated === true;
                  const isInvalid = status?.validated === false;
                  const isConfigured = status?.configured;

                  return (
                    <div key={provider} className="border-b border-white/10 last:border-0 pb-3 last:pb-0">
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="flex items-center gap-1.5">
                          <label className="text-sm text-text-primary font-medium">
                            {providerLabels[provider]}
                          </label>
                          {isConfigured && (
                            <span className="text-[10px] bg-accent-success/20 text-accent-success px-1.5 py-0.5 rounded">
                              ✓
                            </span>
                          )}
                          {isValidated && (
                            <span className="text-[10px] bg-accent-success/20 text-accent-success px-1.5 py-0.5 rounded">
                              Valid
                            </span>
                          )}
                          {isInvalid && (
                            <span className="text-[10px] bg-accent-danger/20 text-accent-danger px-1.5 py-0.5 rounded">
                              Invalid
                            </span>
                          )}
                        </div>

                        {isConfigured && (
                          <button
                            onClick={() => handleDeleteKey(provider)}
                            className="text-xs text-accent-danger hover:text-accent-danger/80 transition-colors"
                          >
                            Delete
                          </button>
                        )}
                      </div>

                      <div className="flex gap-1.5">
                        <div className="flex-1 relative">
                          <GlassInput
                            type={showKeys[provider] ? 'text' : 'password'}
                            value={apiKeys[provider]}
                            onChange={(e) => handleKeyChange(provider, e.target.value)}
                            placeholder={providerPlaceholders[provider]}
                            className="w-full text-sm h-8"
                          />
                          <button
                            onClick={() => toggleShowKey(provider)}
                            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-primary transition-colors text-xs"
                            title={showKeys[provider] ? 'Hide' : 'Show'}
                          >
                            {showKeys[provider] ? '👁️' : '🔒'}
                          </button>
                        </div>

                        <GlassButton
                          onClick={() => handleValidateKey(provider)}
                          disabled={(!apiKeys[provider] && !isConfigured) || validatingKey === provider}
                          variant="secondary"
                          size="sm"
                          className="px-3 h-8 text-xs"
                        >
                          {validatingKey === provider ? '...' : 'Test'}
                        </GlassButton>

                        <GlassButton
                          onClick={() => handleSaveKey(provider)}
                          disabled={!apiKeys[provider] || savingKey === provider}
                          size="sm"
                          className="px-3 h-8 text-xs"
                        >
                          {savingKey === provider ? '...' : 'Save'}
                        </GlassButton>
                      </div>
                      {status?.validation_message && (
                        <p className={`text-[10px] mt-1 ${isInvalid ? 'text-accent-danger' : 'text-accent-success'}`}>
                          {status.validation_message}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Get API Keys Links - Horizontal */}
              <div className="pt-3 border-t border-white/10">
                <p className="text-[10px] text-text-tertiary mb-2">Get API keys:</p>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px]">
                  <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    OpenAI
                  </a>
                  <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    Anthropic
                  </a>
                  <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    Google
                  </a>
                  <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    DeepSeek
                  </a>
                  <a href="https://www.perplexity.ai/settings/api" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    Perplexity
                  </a>
                  <a href="https://venice.ai/settings/api" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    Venice
                  </a>
                  <a href="https://nano-gpt.com/api" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    NanoGPT
                  </a>
                  <a href="https://app.pinata.cloud/developers/api-keys" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">
                    Pinata
                  </a>
                </div>
              </div>
                </>
              )}
            </GlassCard>

            {/* Google Cloud TTS Credentials */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('googleTTS')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🎙️ Google Cloud TTS
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.googleTTS ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.googleTTS && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Path to your Google Cloud service account JSON credentials file. Optional - only needed for Google Cloud TTS (30+ voices).
              </p>
              {loadingGoogleTTSPath ? (
                <p className="text-xs text-text-tertiary">Loading...</p>
              ) : (
                <div className="space-y-2">
                  <GlassInput
                    type="text"
                    value={googleTTSPath}
                    onChange={(e) => setGoogleTTSPath(e.target.value)}
                    placeholder="C:/path/to/your-service-account-key.json"
                    className="w-full text-xs"
                  />
                  <div className="flex gap-2">
                    <GlassButton
                      onClick={handleSaveGoogleTTSPath}
                      disabled={savingGoogleTTSPath}
                      size="sm"
                      className="flex-1 text-xs h-7"
                    >
                      {savingGoogleTTSPath ? 'Saving...' : 'Save Path'}
                    </GlassButton>
                    <GlassButton
                      onClick={() => {
                        setGoogleTTSPath('');
                        handleSaveGoogleTTSPath();
                      }}
                      disabled={savingGoogleTTSPath || !googleTTSPath}
                      variant="secondary"
                      size="sm"
                      className="text-xs h-7 px-3"
                    >
                      Clear
                    </GlassButton>
                  </div>
                  <p className="text-[10px] text-text-tertiary">
                    Get your service account JSON from:{' '}
                    <a
                      href="https://console.cloud.google.com/iam-admin/serviceaccounts"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent-primary hover:underline"
                    >
                      Google Cloud Console
                    </a>
                  </p>
                </div>
              )}
                </>
              )}
            </GlassCard>

            {/* Block Preferences */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('blockSettings')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  ⚓ Anchor &amp; Backup
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.blockSettings ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.blockSettings && (
                <>
              {loadingPreferences ? (
                <p className="text-xs text-text-tertiary">Loading...</p>
              ) : (
                <div className="space-y-4 mt-3">

                  {/* BCH Anchor */}
                  <div className="border border-white/10 rounded-lg p-3 space-y-3">
                    <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wide">⚓ Auto-Anchor</h3>

                    {/* Individual Chat */}
                    <div className="space-y-2">
                      <p className="text-[10px] text-text-tertiary font-medium">Individual Chat</p>
                      <label className="flex items-center justify-between text-xs">
                        <span className="text-text-secondary">Auto-anchor enabled</span>
                        <input
                          type="checkbox"
                          checked={blockPreferences.individual_auto_anchor}
                          onChange={(e) => handleToggleChange('individual_auto_anchor', e.target.checked)}
                          disabled={savingPreferences}
                          className="w-4 h-4 rounded bg-surface-secondary border-border-subtle accent-accent-primary"
                        />
                      </label>
                      {blockPreferences.individual_auto_anchor && (
                        <label className="flex items-center justify-between text-xs">
                          <span className="text-text-secondary">Anchor every</span>
                          <div className="flex items-center gap-1">
                            <input
                              type="number"
                              min="5"
                              max="50"
                              value={individualThresholdInput}
                              onChange={(e) => setIndividualThresholdInput(e.target.value)}
                              onBlur={(e) => handleThresholdBlur('individual_anchor_threshold', e.target.value)}
                              disabled={savingPreferences}
                              className="w-14 h-6 px-2 text-xs rounded bg-white/90 border border-border-subtle text-gray-900 focus:outline-none focus:border-accent-primary"
                            />
                            <span className="text-text-tertiary text-[10px]">blocks (~{Math.round(parseInt(individualThresholdInput||'10')/2)} msgs)</span>
                          </div>
                        </label>
                      )}
                    </div>

                    {/* Group Chat */}
                    <div className="space-y-2 pt-1 border-t border-white/5">
                      <p className="text-[10px] text-text-tertiary font-medium pt-1">Group Chat</p>
                      <label className="flex items-center justify-between text-xs">
                        <span className="text-text-secondary">Auto-anchor enabled</span>
                        <input
                          type="checkbox"
                          checked={blockPreferences.group_auto_anchor}
                          onChange={(e) => handleToggleChange('group_auto_anchor', e.target.checked)}
                          disabled={savingPreferences}
                          className="w-4 h-4 rounded bg-surface-secondary border-border-subtle accent-accent-primary"
                        />
                      </label>
                      {blockPreferences.group_auto_anchor && (
                        <label className="flex items-center justify-between text-xs">
                          <span className="text-text-secondary">Anchor every</span>
                          <div className="flex items-center gap-1">
                            <input
                              type="number"
                              min="5"
                              max="50"
                              value={groupThresholdInput}
                              onChange={(e) => setGroupThresholdInput(e.target.value)}
                              onBlur={(e) => handleThresholdBlur('group_anchor_threshold', e.target.value)}
                              disabled={savingPreferences}
                              className="w-14 h-6 px-2 text-xs rounded bg-white/90 border border-border-subtle text-gray-900 focus:outline-none focus:border-accent-primary"
                            />
                            <span className="text-text-tertiary text-[10px]">blocks (~{Math.round(parseInt(groupThresholdInput||'5')/2)} msgs)</span>
                          </div>
                        </label>
                      )}
                    </div>
                  </div>

                  {/* IPFS Backup */}
                  <div className="border border-white/10 rounded-lg p-3 space-y-3">
                    <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wide">☁️ IPFS Backup (Pinata)</h3>

                    {/* After anchor */}
                    <div className="space-y-1">
                      <label className="flex items-center justify-between text-xs">
                        <span className="text-text-secondary">After each anchor</span>
                        <input
                          type="checkbox"
                          checked={blockPreferences.auto_sync_ipfs_on_anchor}
                          onChange={(e) => handleToggleChange('auto_sync_ipfs_on_anchor', e.target.checked)}
                          disabled={savingPreferences}
                          className="w-4 h-4 rounded bg-surface-secondary border-border-subtle accent-accent-primary"
                        />
                      </label>
                      <p className="text-[10px] text-text-tertiary">
                        Upload to Pinata immediately after every auto-anchor (new CID replaces old)
                      </p>
                    </div>

                    {/* Periodic */}
                    <div className="space-y-1 pt-1 border-t border-white/5">
                      <label className="flex items-center justify-between text-xs pt-1">
                        <span className="text-text-secondary">Periodic background sync</span>
                        <input
                          type="checkbox"
                          checked={blockPreferences.auto_sync_ipfs_periodic}
                          onChange={(e) => handleToggleChange('auto_sync_ipfs_periodic', e.target.checked)}
                          disabled={savingPreferences}
                          className="w-4 h-4 rounded bg-surface-secondary border-border-subtle accent-accent-primary"
                        />
                      </label>
                      <label className="flex items-center justify-between text-xs">
                        <span className="text-text-secondary pl-0">Sync every</span>
                        <select
                          value={blockPreferences.auto_sync_ipfs_interval}
                          onChange={async (e) => {
                            const interval = parseInt(e.target.value);
                            setSavingPreferences(true);
                            setBlockPreferences(prev => ({ ...prev, auto_sync_ipfs_interval: interval }));
                            try {
                              const result = await invoke<BlockPreferences>('update_block_preferences', {
                                userId,
                                autoSyncIpfsInterval: interval,
                              });
                              setBlockPreferences(result);
                            } catch (error) {
                              console.error('Failed to update sync interval:', error);
                              alert(`Error updating sync interval: ${String(error)}`);
                              await loadBlockPreferences();
                            } finally {
                              setSavingPreferences(false);
                            }
                          }}
                          disabled={savingPreferences || !blockPreferences.auto_sync_ipfs_periodic}
                          className="bg-surface-secondary border border-border-subtle rounded px-2 py-1 text-xs text-text-primary disabled:opacity-40"
                        >
                          <option value={5}>5 minutes</option>
                          <option value={15}>15 minutes</option>
                          <option value={30}>30 minutes</option>
                          <option value={60}>60 minutes</option>
                        </select>
                      </label>
                    </div>
                  </div>

                </div>
              )}
                </>
              )}
            </GlassCard>

            {/* Block Recall Settings */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('blockRecall')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🔮 Block Recall
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.blockRecall ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.blockRecall && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Configure how memories are recalled and injected into conversations.
              </p>

              {loadingMemoryConfig ? (
                <p className="text-xs text-text-tertiary">Loading...</p>
              ) : (
                <div className="space-y-3">
                  {/* Basic Settings */}
                  <div className="space-y-2">
                    {/* Recall Threshold */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <label className="text-xs text-text-secondary">Recall Threshold</label>
                        <span className="text-xs text-text-primary font-mono">{localMemoryConfig.recall_threshold.toFixed(1)}</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="1"
                        value={localMemoryConfig.recall_threshold}
                        onChange={(e) => handleMemoryConfigSliderChange({ recall_threshold: parseFloat(e.target.value) })}
                        onPointerUp={handleMemoryConfigSliderRelease}
                        onTouchEnd={handleMemoryConfigSliderRelease}
                        className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                      />
                      <p className="text-[9px] text-text-tertiary">Minimum relevance score (0-100) to recall a memory</p>
                    </div>

                    {/* Max Recalls */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <label className="text-xs text-text-secondary">Max Recalls</label>
                        <span className="text-xs text-text-primary font-mono">{localMemoryConfig.max_recalls}</span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="20"
                        step="1"
                        value={localMemoryConfig.max_recalls}
                        onChange={(e) => handleMemoryConfigSliderChange({ max_recalls: parseInt(e.target.value) })}
                        onPointerUp={handleMemoryConfigSliderRelease}
                        onTouchEnd={handleMemoryConfigSliderRelease}
                        className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                      />
                      <p className="text-[9px] text-text-tertiary">Maximum memories to inject per query</p>
                    </div>

                    {/* Decay Rate */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <label className="text-xs text-text-secondary">Temporal Decay Rate</label>
                        <span className="text-xs text-text-primary font-mono">{localMemoryConfig.decay_rate.toFixed(2)}</span>
                      </div>
                      <input
                        type="range"
                        min="0.01"
                        max="1"
                        step="0.01"
                        value={localMemoryConfig.decay_rate}
                        onChange={(e) => handleMemoryConfigSliderChange({ decay_rate: parseFloat(e.target.value) })}
                        onPointerUp={handleMemoryConfigSliderRelease}
                        onTouchEnd={handleMemoryConfigSliderRelease}
                        className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                      />
                      <p className="text-[9px] text-text-tertiary">
                        {localMemoryConfig.decay_rate <= 0.2 ? 'Slow decay - older memories stay relevant longer' :
                         localMemoryConfig.decay_rate >= 0.8 ? 'Fast decay - strongly favors recent memories' :
                         'Moderate decay - balanced recency preference'}
                      </p>
                    </div>
                  </div>

                  {/* Advanced Settings Toggle */}
                  <button
                    onClick={() => setShowAdvancedRecall(!showAdvancedRecall)}
                    className="flex items-center gap-2 text-xs text-accent-primary hover:text-accent-primary/80 transition-colors"
                  >
                    <span className={`transform transition-transform ${showAdvancedRecall ? 'rotate-90' : ''}`}>▶</span>
                    Advanced Recall Settings
                  </button>

                  {/* Advanced Settings (Collapsible) */}
                  {showAdvancedRecall && (
                    <div className="space-y-2 pl-4 border-l-2 border-white/10">
                      <p className="text-[9px] text-text-tertiary mb-2">
                        Scoring weights determine how different factors contribute to relevance. Should sum to ~1.0.
                      </p>

                      {/* Semantic Weight */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <label className="text-[10px] text-text-secondary">Semantic Weight</label>
                          <span className="text-[10px] text-text-primary font-mono">{localMemoryConfig.semantic_weight.toFixed(2)}</span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={localMemoryConfig.semantic_weight}
                          onChange={(e) => handleMemoryConfigSliderChange({ semantic_weight: parseFloat(e.target.value) })}
                          onPointerUp={handleMemoryConfigSliderRelease}
                          onTouchEnd={handleMemoryConfigSliderRelease}
                          className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                        />
                        <p className="text-[8px] text-text-tertiary">Meaning/context similarity via embeddings</p>
                      </div>

                      {/* Keyword Weight */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <label className="text-[10px] text-text-secondary">Keyword Weight</label>
                          <span className="text-[10px] text-text-primary font-mono">{localMemoryConfig.keyword_weight.toFixed(2)}</span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={localMemoryConfig.keyword_weight}
                          onChange={(e) => handleMemoryConfigSliderChange({ keyword_weight: parseFloat(e.target.value) })}
                          onPointerUp={handleMemoryConfigSliderRelease}
                          onTouchEnd={handleMemoryConfigSliderRelease}
                          className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                        />
                        <p className="text-[8px] text-text-tertiary">Exact word/phrase matches</p>
                      </div>

                      {/* Temporal Weight */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <label className="text-[10px] text-text-secondary">Temporal Weight</label>
                          <span className="text-[10px] text-text-primary font-mono">{localMemoryConfig.temporal_weight.toFixed(2)}</span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={localMemoryConfig.temporal_weight}
                          onChange={(e) => handleMemoryConfigSliderChange({ temporal_weight: parseFloat(e.target.value) })}
                          onPointerUp={handleMemoryConfigSliderRelease}
                          onTouchEnd={handleMemoryConfigSliderRelease}
                          className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                        />
                        <p className="text-[8px] text-text-tertiary">Recency boost for newer memories</p>
                      </div>

                      {/* Relationship Weight */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <label className="text-[10px] text-text-secondary">Relationship Weight</label>
                          <span className="text-[10px] text-text-primary font-mono">{localMemoryConfig.relationship_weight.toFixed(2)}</span>
                        </div>
                        <input
                          type="range"
                          min="0"
                          max="1"
                          step="0.05"
                          value={localMemoryConfig.relationship_weight}
                          onChange={(e) => handleMemoryConfigSliderChange({ relationship_weight: parseFloat(e.target.value) })}
                          onPointerUp={handleMemoryConfigSliderRelease}
                          onTouchEnd={handleMemoryConfigSliderRelease}
                          className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                        />
                        <p className="text-[8px] text-text-tertiary">Boost for memories involving close relationships</p>
                      </div>

                      {/* Weight Sum Indicator */}
                      <div className="mt-2 pt-2 border-t border-white/10">
                        <div className="flex items-center justify-between">
                          <span className="text-[9px] text-text-tertiary">Total Weight Sum:</span>
                          <span className={`text-[10px] font-mono ${
                            Math.abs((localMemoryConfig.semantic_weight + localMemoryConfig.keyword_weight + localMemoryConfig.temporal_weight + localMemoryConfig.relationship_weight) - 1) < 0.05
                              ? 'text-accent-success'
                              : 'text-accent-warning'
                          }`}>
                            {(localMemoryConfig.semantic_weight + localMemoryConfig.keyword_weight + localMemoryConfig.temporal_weight + localMemoryConfig.relationship_weight).toFixed(2)}
                          </span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
                </>
              )}
            </GlassCard>

            {/* Custom Voices */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('voiceSettings')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🎤 Custom Voices
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.voiceSettings ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.voiceSettings && (
                <>
                  <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                    Create unique voices for your Qubes using AI voice design, cloning, or presets.
                  </p>

                  <VoiceSettingsPanel
                    selectedQubeId={selectedQubeIdForSettings}
                    selectedQubeName={availableQubes.find(q => q.qube_id === selectedQubeIdForSettings)?.name}
                  />
                </>
              )}
            </GlassCard>

            {/* GPU Acceleration */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('gpuAcceleration')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  {gpuStatus?.cuda_available ? '⚡' : '🖥️'} GPU Acceleration
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.gpuAcceleration ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.gpuAcceleration && (
                <>
                  <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                    Use your NVIDIA GPU to accelerate local TTS and voice generation.
                  </p>

                  {gpuError && (
                    <div className="p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-400 mb-3">
                      {gpuError}
                      <button onClick={() => setGpuError(null)} className="ml-2 underline">Dismiss</button>
                    </div>
                  )}

                  {gpuStatus ? (
                    <div className="space-y-3">
                      {/* GPU Hardware Info */}
                      <div className="bg-white/5 rounded p-2.5 space-y-1.5">
                        <div className="flex items-center justify-between">
                          <span className="text-text-secondary text-[10px]">GPU Hardware</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                            gpuStatus.gpu_detected
                              ? 'bg-green-500/20 text-green-400'
                              : 'bg-white/10 text-text-tertiary'
                          }`}>
                            {gpuStatus.gpu_detected ? 'Detected' : 'Not Found'}
                          </span>
                        </div>
                        {gpuStatus.gpu_detected && (
                          <>
                            <div className="flex items-center justify-between">
                              <span className="text-text-tertiary text-[10px]">Name</span>
                              <span className="text-text-primary text-[10px]">{gpuStatus.gpu_name}</span>
                            </div>
                            {gpuStatus.gpu_vram_gb != null && (
                              <div className="flex items-center justify-between">
                                <span className="text-text-tertiary text-[10px]">VRAM</span>
                                <span className="text-text-primary text-[10px]">{gpuStatus.gpu_vram_gb} GB</span>
                              </div>
                            )}
                            {gpuStatus.driver_version && (
                              <div className="flex items-center justify-between">
                                <span className="text-text-tertiary text-[10px]">Driver</span>
                                <span className="text-text-primary text-[10px]">v{gpuStatus.driver_version}</span>
                              </div>
                            )}
                          </>
                        )}
                      </div>

                      {/* PyTorch Status */}
                      <div className="bg-white/5 rounded p-2.5 space-y-1.5">
                        <div className="flex items-center justify-between">
                          <span className="text-text-secondary text-[10px]">PyTorch</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                            gpuStatus.cuda_available
                              ? 'bg-green-500/20 text-green-400'
                              : 'bg-yellow-500/20 text-yellow-400'
                          }`}>
                            {gpuStatus.cuda_available ? 'GPU (CUDA)' : 'CPU Only'}
                          </span>
                        </div>
                        {gpuStatus.torch_version && (
                          <div className="flex items-center justify-between">
                            <span className="text-text-tertiary text-[10px]">Version</span>
                            <span className="text-text-primary text-[10px]">{gpuStatus.torch_version}</span>
                          </div>
                        )}
                        {gpuStatus.torch_cuda_version && (
                          <div className="flex items-center justify-between">
                            <span className="text-text-tertiary text-[10px]">CUDA Version</span>
                            <span className="text-text-primary text-[10px]">{gpuStatus.torch_cuda_version}</span>
                          </div>
                        )}
                      </div>

                      {/* Download GPU Acceleration */}
                      {gpuStatus.upgrade_available && !gpuInstalling && (
                        <div className="space-y-2">
                          <p className="text-text-tertiary text-[10px]">
                            Your NVIDIA GPU can accelerate Kokoro TTS and Qwen3 voice generation.
                            Download CUDA PyTorch (~2 GB) to enable GPU acceleration.
                          </p>
                          <button
                            onClick={handleInstallGpu}
                            className="w-full py-2 rounded bg-accent-primary/20 border border-accent-primary/40 text-accent-primary hover:bg-accent-primary/30 text-xs font-medium"
                          >
                            Download GPU Acceleration (~2 GB)
                          </button>
                        </div>
                      )}

                      {/* Install Progress */}
                      {gpuInstalling && (
                        <div className="bg-white/5 rounded p-2 space-y-1.5">
                          <p className="text-[10px] text-text-secondary">
                            {gpuInstallProgress.phase === 'torch' && 'Downloading CUDA PyTorch...'}
                            {gpuInstallProgress.phase === 'torchaudio' && 'Downloading CUDA torchaudio...'}
                            {gpuInstallProgress.phase === 'extracting' && 'Extracting files...'}
                            {gpuInstallProgress.phase === 'verifying' && 'Verifying installation...'}
                            {gpuInstallProgress.phase === 'pip install' && 'Installing via pip...'}
                            {!gpuInstallProgress.phase && 'Preparing...'}
                          </p>
                          <div className="w-full bg-white/10 rounded-full h-2">
                            <div
                              className={`h-2 rounded-full transition-all duration-300 ${
                                !gpuInstallProgress.total_bytes ? 'bg-accent-primary/50 animate-pulse w-full' : 'bg-accent-primary'
                              }`}
                              style={gpuInstallProgress.total_bytes
                                ? { width: `${Math.round(((gpuInstallProgress.downloaded_bytes || 0) / gpuInstallProgress.total_bytes) * 100)}%` }
                                : undefined
                              }
                            />
                          </div>
                          <div className="flex items-center justify-between text-[10px] text-text-tertiary">
                            <span>
                              {gpuInstallProgress.total_bytes
                                ? `${formatBytes(gpuInstallProgress.downloaded_bytes || 0)} / ${formatBytes(gpuInstallProgress.total_bytes)}`
                                : gpuInstallProgress.phase === 'extracting' ? 'Extracting...' : 'Calculating...'
                              }
                            </span>
                            <span>
                              {(gpuInstallProgress.speed_mbps || 0) > 0 && `${gpuInstallProgress.speed_mbps} MB/s`}
                              {(gpuInstallProgress.eta_seconds || 0) > 0 && (gpuInstallProgress.speed_mbps || 0) > 0 && ' • '}
                              {(gpuInstallProgress.eta_seconds || 0) > 60
                                ? `${Math.floor((gpuInstallProgress.eta_seconds || 0) / 60)}m left`
                                : (gpuInstallProgress.eta_seconds || 0) > 0
                                  ? `${Math.round(gpuInstallProgress.eta_seconds || 0)}s left`
                                  : ''
                              }
                            </span>
                          </div>
                        </div>
                      )}

                      {/* GPU Active */}
                      {gpuStatus.cuda_available && !gpuInstalling && (
                        <div className="space-y-2">
                          <p className="text-green-400/70 text-[10px]">
                            GPU acceleration is active. Kokoro TTS and Qwen3 voice generation
                            will use your {gpuStatus.gpu_name} for faster processing.
                          </p>
                          {gpuStatus.is_frozen && (
                            <button
                              onClick={handleUninstallGpu}
                              disabled={gpuUninstalling}
                              className="py-1.5 px-3 rounded bg-white/5 border border-white/10 text-text-tertiary hover:text-red-400 hover:border-red-400/30 text-[10px] disabled:opacity-50"
                            >
                              {gpuUninstalling ? 'Reverting...' : 'Revert to CPU Only'}
                            </button>
                          )}
                        </div>
                      )}

                      {/* No GPU */}
                      {!gpuStatus.gpu_detected && (
                        <p className="text-text-tertiary text-[10px]">
                          No NVIDIA GPU detected. TTS runs on CPU which works well for
                          Kokoro's lightweight 82M model. An NVIDIA GPU with 4+ GB VRAM
                          is recommended for Qwen3 voice design and cloning.
                        </p>
                      )}

                      {/* Restart reminder */}
                      {gpuInstallProgress.status === 'completed' && (
                        <div className="p-2 rounded-lg bg-green-500/10 border border-green-500/30">
                          <p className="text-green-400 text-xs font-medium">GPU acceleration installed!</p>
                          <p className="text-green-400/70 text-[10px]">Restart Qubes to activate.</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-text-tertiary">Checking GPU status...</p>
                  )}
                </>
              )}
            </GlassCard>

            {/* Local Models */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => {
                  togglePanel('localModels');
                  if (collapsedPanels.localModels) {
                    checkOllamaModels();
                    checkTtsModels();
                  }
                }}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  📦 Local Models
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.localModels ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.localModels && (
                <>
                  <p className="text-[10px] text-text-tertiary mb-4 mt-2">
                    Update AI models (Ollama) and voice/embedding models (Kokoro TTS, Sentence Transformers).
                  </p>

                  {/* Ollama Models */}
                  <div className="mb-4">
                    <h3 className="text-sm font-medium text-text-secondary mb-2">🤖 AI Models (Ollama)</h3>
                    {ollamaModels.length > 0 ? (
                      <div className="space-y-2">
                        {ollamaModels.map((model) => (
                          <div key={model} className="flex items-center justify-between bg-bg-primary/40 rounded px-3 py-2">
                            <div>
                              <span className="text-xs text-text-primary font-mono">{model}</span>
                              <span className="ml-2 text-[10px] text-green-400">✓ installed</span>
                            </div>
                            <GlassButton
                              variant="secondary"
                              onClick={() => handlePullModel(model)}
                              disabled={isPullingModel !== null}
                            >
                              {isPullingModel === model ? 'Updating...' : 'Update'}
                            </GlassButton>
                          </div>
                        ))}
                        <GlassButton
                          variant="primary"
                          onClick={handleUpdateAllOllamaModels}
                          disabled={isPullingModel !== null}
                          className="w-full mt-2"
                        >
                          {isPullingModel ? `Updating ${isPullingModel}...` : 'Update All Ollama Models'}
                        </GlassButton>
                      </div>
                    ) : (
                      <div className="text-xs text-text-tertiary italic">No Ollama models installed yet.</div>
                    )}

                    {/* Pull progress */}
                    {pullProgress && (
                      <div className="mt-3 p-3 bg-bg-primary/60 rounded border border-glass-border">
                        <div className="flex justify-between text-xs text-text-secondary mb-1">
                          <span>{pullProgress.status}</span>
                          {pullProgress.total && pullProgress.total > 0 && (
                            <span>
                              {formatBytes(pullProgress.completed ?? 0)} / {formatBytes(pullProgress.total)}
                            </span>
                          )}
                        </div>
                        {pullProgress.total && pullProgress.total > 0 && (
                          <div className="w-full bg-bg-quaternary rounded-full h-1.5">
                            <div
                              className="bg-accent-primary h-1.5 rounded-full transition-all"
                              style={{ width: `${Math.min(100, ((pullProgress.completed ?? 0) / pullProgress.total) * 100)}%` }}
                            />
                          </div>
                        )}
                      </div>
                    )}

                    {/* Download new model */}
                    <div className="mt-3">
                      <p className="text-[10px] text-text-tertiary mb-1">Download a new model (e.g. llama3.2:3b)</p>
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={newModelInput}
                          onChange={(e) => setNewModelInput(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter' && newModelInput.trim()) { handlePullModel(newModelInput.trim()); setNewModelInput(''); } }}
                          placeholder="model:tag"
                          className="flex-1 bg-bg-primary/60 border border-glass-border rounded px-3 py-1.5 text-xs text-text-primary placeholder:text-text-disabled focus:outline-none focus:border-accent-primary"
                          disabled={isPullingModel !== null}
                        />
                        <GlassButton
                          variant="primary"
                          onClick={() => { if (newModelInput.trim()) { handlePullModel(newModelInput.trim()); setNewModelInput(''); } }}
                          disabled={isPullingModel !== null || !newModelInput.trim()}
                        >
                          Download
                        </GlassButton>
                      </div>
                    </div>
                  </div>

                  {/* Voice & Embedding Models */}
                  <div>
                    <h3 className="text-sm font-medium text-text-secondary mb-2">🎙️ Voice & Embedding Models</h3>
                    {ttsModelStatus ? (
                      <div className="space-y-2 mb-3">
                        <div className="flex items-center justify-between bg-bg-primary/40 rounded px-3 py-2">
                          <span className="text-xs text-text-primary">Kokoro TTS 82M</span>
                          <span className={`text-[10px] ${ttsModelStatus.kokoro_installed ? 'text-green-400' : 'text-red-400'}`}>
                            {ttsModelStatus.kokoro_installed ? '✓ installed' : '✗ missing'}
                          </span>
                        </div>
                        <div className="flex items-center justify-between bg-bg-primary/40 rounded px-3 py-2">
                          <span className="text-xs text-text-primary">Sentence Transformers</span>
                          <span className={`text-[10px] ${ttsModelStatus.sentence_transformers_installed ? 'text-green-400' : 'text-red-400'}`}>
                            {ttsModelStatus.sentence_transformers_installed ? '✓ installed' : '✗ missing'}
                          </span>
                        </div>
                        <div className="flex items-center justify-between bg-bg-primary/40 rounded px-3 py-2">
                          <span className="text-xs text-text-primary">Whisper STT</span>
                          <span className={`text-[10px] ${ttsModelStatus.whisper_installed ? 'text-green-400' : 'text-red-400'}`}>
                            {ttsModelStatus.whisper_installed ? '✓ installed' : '✗ missing'}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <div className="text-xs text-text-tertiary italic mb-3">Click to check model status...</div>
                    )}

                    <GlassButton
                      variant="secondary"
                      onClick={handleUpdateTtsModels}
                      disabled={isUpdatingTts}
                      className="w-full"
                    >
                      {isUpdatingTts ? 'Re-downloading...' : 'Re-download Voice Models'}
                    </GlassButton>

                    {ttsUpdateResult && (
                      <p className={`text-xs mt-2 ${ttsUpdateResult.startsWith('Updated') ? 'text-green-400' : 'text-red-400'}`}>
                        {ttsUpdateResult}
                      </p>
                    )}
                  </div>
                </>
              )}
            </GlassCard>
          </div>

          {/* Right Column */}
          <div>
            {/* Relationship Difficulty */}
            <GlassCard className="p-4">
              <button
                onClick={() => togglePanel('relationshipDifficulty')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  💞 Relationship Difficulty
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.relationshipDifficulty ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.relationshipDifficulty && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Global setting that affects all qubes equally. Determines how quickly relationships build.
              </p>

              {loadingRelationshipSettings ? (
                <p className="text-xs text-text-tertiary">Loading...</p>
              ) : (
                <div className="space-y-3">
                  {/* Current Setting */}
                  <div className="bg-white/5 rounded p-2 border border-white/10">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-text-primary">Current:</span>
                      <span className="text-xs text-accent-primary font-semibold uppercase">
                        {relationshipSettings.difficulty}
                      </span>
                    </div>
                    <p className="text-[10px] text-text-secondary italic">
                      {relationshipSettings.description}
                    </p>
                  </div>

                  {/* Difficulty Buttons */}
                  <div className="grid grid-cols-2 gap-2">
                    {(['quick', 'normal', 'long', 'extreme'] as const).map((difficulty) => (
                      <button
                        key={difficulty}
                        onClick={() => handleDifficultyChange(difficulty)}
                        disabled={savingRelationshipSettings || relationshipSettings.difficulty === difficulty}
                        className={`
                          px-3 py-2 rounded text-xs font-medium transition-all
                          ${relationshipSettings.difficulty === difficulty
                            ? 'bg-accent-primary/20 text-accent-primary border-2 border-accent-primary/40'
                            : 'bg-white/5 text-text-secondary border border-white/10 hover:bg-white/10 hover:text-text-primary'
                          }
                          disabled:opacity-50 disabled:cursor-not-allowed
                        `}
                      >
                        {difficulty === 'quick' && '⚡ Quick'}
                        {difficulty === 'normal' && '⚖️ Normal'}
                        {difficulty === 'long' && '🏔️ Long'}
                        {difficulty === 'extreme' && '🔥 Extreme'}
                      </button>
                    ))}
                  </div>

                  {/* Current Preset Stats (if available) */}
                  {difficultyPresets[relationshipSettings.difficulty] && (
                    <div className="border-t border-white/10 pt-3">
                      <h3 className="text-[10px] font-medium text-text-primary mb-2">
                        Required Interactions:
                      </h3>
                      <div className="grid grid-cols-2 gap-1.5 text-[10px]">
                        <div className="flex justify-between">
                          <span className="text-text-tertiary">Acquaintance:</span>
                          <span className="text-text-primary font-mono">
                            {difficultyPresets[relationshipSettings.difficulty].min_interactions.acquaintance}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-tertiary">Friend:</span>
                          <span className="text-text-primary font-mono">
                            {difficultyPresets[relationshipSettings.difficulty].min_interactions.friend}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-tertiary">Close Friend:</span>
                          <span className="text-text-primary font-mono">
                            {difficultyPresets[relationshipSettings.difficulty].min_interactions.close_friend}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-tertiary">Best Friend:</span>
                          <span className="text-text-primary font-mono">
                            {difficultyPresets[relationshipSettings.difficulty].min_interactions.best_friend}
                          </span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
                </>
              )}
            </GlassCard>

            {/* Trust Personality */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('trustPersonality')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🎭 Trust Personality
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.trustPersonality ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.trustPersonality && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Configure how each Qube evaluates trust in relationships.
              </p>

              {/* Qube Selector */}
                <div className="mb-3">
                  <label className="text-[10px] text-text-secondary mb-1 block">Select Qube:</label>
                  <DarkSelect
                    value={selectedQubeForTrust}
                    onChange={(v) => setSelectedQubeForTrust(v)}
                    disabled={loadingTrustPersonality}
                    options={availableQubes.map((q) => ({
                      value: q.qube_id,
                      label: `${q.name} (${q.qube_id})`,
                    }))}
                  />
                </div>

                {/* Trust Personality Buttons */}
                {loadingTrustPersonality ? (
                  <p className="text-xs text-text-tertiary">Loading...</p>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {(['cautious', 'balanced', 'social', 'analytical'] as const).map((personality) => {
                      const descriptions = {
                        cautious: 'Trust slowly, prioritize reliability & honesty',
                        balanced: 'Equal weight to all trust components',
                        social: 'Value communication & responsiveness',
                        analytical: 'Prioritize expertise & competence'
                      };
                      const icons = {
                        cautious: '🛡️',
                        balanced: '⚖️',
                        social: '💬',
                        analytical: '🔬'
                      };

                      const isSelected = trustPersonality === personality;

                      return (
                        <button
                          key={personality}
                          onClick={() => handleTrustPersonalityChange(personality)}
                          disabled={savingTrustPersonality}
                          className={`
                            px-3 py-2 rounded text-xs transition-all
                            ${isSelected
                              ? 'bg-accent-primary/20 border-2 border-accent-primary/40 text-accent-primary font-semibold'
                              : 'bg-white/5 border border-white/10 text-text-secondary hover:bg-white/10 hover:text-text-primary'
                            }
                            disabled:opacity-50 disabled:cursor-not-allowed
                          `}
                          title={descriptions[personality]}
                        >
                          <div className="flex items-center justify-center gap-1.5">
                            <span>{icons[personality]}</span>
                            <span className="capitalize">{personality}</span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Description of Selected Personality */}
                <div className="mt-3 bg-white/5 rounded p-2 border border-white/10">
                  <p className="text-[10px] text-text-secondary italic">
                    {trustPersonality === 'cautious' && 'This Qube will build trust slowly, emphasizing reliability and honesty over speed.'}
                    {trustPersonality === 'balanced' && 'This Qube gives equal weight to all trust components for balanced relationship growth.'}
                    {trustPersonality === 'social' && 'This Qube values good communication and quick responses in building trust.'}
                    {trustPersonality === 'analytical' && 'This Qube prioritizes demonstrated expertise and competence when evaluating trust.'}
                  </p>
                </div>
                </>
              )}
            </GlassCard>

            {/* Decision Intelligence */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('decisionIntelligence')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🧠 Decision Intelligence
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.decisionIntelligence ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.decisionIntelligence && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Configure how Qubes use relationship and self-evaluation metrics to make better decisions.
              </p>

              {loadingDecisionConfig ? (
                  <p className="text-xs text-text-tertiary">Loading...</p>
                ) : (
                  <div className="space-y-4">
                    {/* Thresholds Section */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <h4 className="text-xs font-medium text-text-primary">Decision Thresholds (0-100)</h4>
                        {decisionConfig.auto_thresholds && (
                          <span className="text-[9px] text-accent-primary bg-accent-primary/10 px-2 py-0.5 rounded">
                            AUTO MODE
                          </span>
                        )}
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Trust: {decisionConfig.trust_threshold}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.trust_threshold}
                            onChange={(e) => handleDecisionConfigChange({ trust_threshold: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig || decisionConfig.auto_thresholds}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
                          />
                        </div>

                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Expertise: {decisionConfig.expertise_threshold}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.expertise_threshold}
                            onChange={(e) => handleDecisionConfigChange({ expertise_threshold: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig || decisionConfig.auto_thresholds}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
                          />
                        </div>

                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Collaboration: {decisionConfig.collaboration_threshold}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.collaboration_threshold}
                            onChange={(e) => handleDecisionConfigChange({ collaboration_threshold: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig || decisionConfig.auto_thresholds}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
                          />
                        </div>

                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Confidence: {decisionConfig.confidence_threshold}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.confidence_threshold}
                            onChange={(e) => handleDecisionConfigChange({ confidence_threshold: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig || decisionConfig.auto_thresholds}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
                          />
                        </div>
                      </div>

                      {decisionConfig.auto_thresholds && (
                        <p className="text-[9px] text-text-tertiary italic">
                          Thresholds are auto-calculated from self-evaluation metrics and update with each snapshot
                        </p>
                      )}
                    </div>

                    {/* Influence Levels Section */}
                    <div className="space-y-2 border-t border-white/10 pt-3">
                      <h4 className="text-xs font-medium text-text-primary">Influence Levels (0-100%)</h4>

                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Metric Influence: {decisionConfig.metric_influence}%</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.metric_influence}
                            onChange={(e) => handleDecisionConfigChange({ metric_influence: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                          />
                        </div>

                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Validation Strictness: {decisionConfig.validation_strictness}%</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.validation_strictness}
                            onChange={(e) => handleDecisionConfigChange({ validation_strictness: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                          />
                          <p className="text-[9px] text-text-tertiary italic">
                            {decisionConfig.validation_strictness >= 80 ? 'Hard: Can block actions' :
                             decisionConfig.validation_strictness >= 40 ? 'Medium: Warnings + confidence reduction' :
                             'Soft: Warnings only'}
                          </p>
                        </div>
                      </div>
                    </div>

                    {/* Negative Metric Tolerances Section */}
                    <div className="space-y-2 border-t border-white/10 pt-3">
                      <h4 className="text-xs font-medium text-text-primary">Negative Metric Tolerances (0-100)</h4>

                      <div className="grid grid-cols-3 gap-2">
                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Max Antagonism: {decisionConfig.max_antagonism}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.max_antagonism}
                            onChange={(e) => handleDecisionConfigChange({ max_antagonism: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                          />
                        </div>

                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Max Distrust: {decisionConfig.max_distrust}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.max_distrust}
                            onChange={(e) => handleDecisionConfigChange({ max_distrust: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                          />
                        </div>

                        <div className="space-y-1">
                          <label className="text-[10px] text-text-secondary">Max Betrayal: {decisionConfig.max_betrayal}</label>
                          <input
                            type="range"
                            min="0"
                            max="100"
                            value={decisionConfig.max_betrayal}
                            onChange={(e) => handleDecisionConfigChange({ max_betrayal: parseInt(e.target.value) })}
                            disabled={savingDecisionConfig}
                            className="w-full h-1.5 bg-white/10 rounded-lg appearance-none cursor-pointer accent-accent-primary"
                          />
                        </div>
                      </div>
                    </div>

                    {/* Feature Toggles Section */}
                    <div className="space-y-2 border-t border-white/10 pt-3">
                      <h4 className="text-xs font-medium text-text-primary">Feature Toggles</h4>

                      <div className="space-y-1.5">
                        <label className="flex items-center space-x-2 text-[10px] text-text-secondary cursor-pointer">
                          <input
                            type="checkbox"
                            checked={decisionConfig.enable_validation_layer}
                            onChange={(e) => handleDecisionConfigChange({ enable_validation_layer: e.target.checked })}
                            disabled={savingDecisionConfig}
                            className="rounded border-white/20 bg-white/5 text-accent-primary focus:ring-accent-primary focus:ring-offset-0"
                          />
                          <span>Enable Validation Layer (pre-flight checks)</span>
                        </label>

                        <label className="flex items-center space-x-2 text-[10px] text-text-secondary cursor-pointer">
                          <input
                            type="checkbox"
                            checked={decisionConfig.enable_metric_tools}
                            onChange={(e) => handleDecisionConfigChange({ enable_metric_tools: e.target.checked })}
                            disabled={savingDecisionConfig}
                            className="rounded border-white/20 bg-white/5 text-accent-primary focus:ring-accent-primary focus:ring-offset-0"
                          />
                          <span>Enable Metric Tools (decision context, capability check)</span>
                        </label>

                        <label className="flex items-center space-x-2 text-[10px] text-text-secondary cursor-pointer">
                          <input
                            type="checkbox"
                            checked={decisionConfig.enable_auto_temperature}
                            onChange={(e) => handleDecisionConfigChange({ enable_auto_temperature: e.target.checked })}
                            disabled={savingDecisionConfig}
                            className="rounded border-white/20 bg-white/5 text-accent-primary focus:ring-accent-primary focus:ring-offset-0"
                          />
                          <span>Enable Auto Temperature Adjustment</span>
                        </label>

                        <label className="flex items-center space-x-2 text-[10px] text-text-secondary cursor-pointer">
                          <input
                            type="checkbox"
                            checked={decisionConfig.auto_thresholds}
                            onChange={(e) => handleDecisionConfigChange({ auto_thresholds: e.target.checked })}
                            disabled={savingDecisionConfig}
                            className="rounded border-white/20 bg-white/5 text-accent-primary focus:ring-accent-primary focus:ring-offset-0"
                          />
                          <span>Auto-Adjust Thresholds (from self-evaluation)</span>
                        </label>
                      </div>
                    </div>
                  </div>
                )}
                </>
              )}
            </GlassCard>

            {/* Security Settings */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('security')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🔐 Security
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.security ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.security && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Configure security features to protect your Qubes and data.
              </p>

              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-medium text-text-primary">Auto-Lock</h3>
                    <p className="text-[10px] text-text-tertiary">
                      Lock the app after a period of inactivity
                    </p>
                  </div>
                  <button
                    onClick={() => setAutoLockSettings(!autoLockEnabled, autoLockTimeout)}
                    className={`w-12 h-6 rounded-full transition-colors duration-200 relative ${
                      autoLockEnabled ? 'bg-accent-primary' : 'bg-white/20'
                    }`}
                  >
                    <div className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                      autoLockEnabled ? 'translate-x-7' : 'translate-x-1'
                    }`} />
                  </button>
                </div>

                {autoLockEnabled && (
                  <div className="pl-4 border-l-2 border-accent-primary/30">
                    <label className="text-xs text-text-secondary block mb-2">
                      Lock after inactivity:
                    </label>
                    <div className="grid grid-cols-4 gap-2">
                      {[5, 10, 15, 30].map((minutes) => (
                        <button
                          key={minutes}
                          onClick={() => setAutoLockSettings(autoLockEnabled, minutes)}
                          className={`px-3 py-2 rounded text-xs font-medium transition-all ${
                            autoLockTimeout === minutes
                              ? 'bg-accent-primary/20 text-accent-primary border-2 border-accent-primary/40'
                              : 'bg-white/5 text-text-secondary border border-white/10 hover:bg-white/10'
                          }`}
                        >
                          {minutes} min
                        </button>
                      ))}
                    </div>
                    <p className="text-[9px] text-text-tertiary mt-2 italic">
                      You'll need to enter your password to unlock
                    </p>
                  </div>
                )}

                {/* Change Master Password */}
                <div className="pt-4 border-t border-white/10">
                  <h3 className="text-sm font-medium text-text-primary mb-1">Change Master Password</h3>
                  <p className="text-[10px] text-text-tertiary mb-3">
                    Re-encrypts all account data and Qube keys with the new password.
                  </p>
                  <div className="space-y-2">
                    <input
                      type="password"
                      placeholder="Current password"
                      value={changePwOld}
                      onChange={(e) => setChangePwOld(e.target.value)}
                      className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm"
                    />
                    <input
                      type="password"
                      placeholder="New password (min 8 characters)"
                      value={changePwNew}
                      onChange={(e) => setChangePwNew(e.target.value)}
                      className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm"
                    />
                    <input
                      type="password"
                      placeholder="Confirm new password"
                      value={changePwConfirm}
                      onChange={(e) => setChangePwConfirm(e.target.value)}
                      className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm"
                    />
                    {changePwError && (
                      <p className="text-accent-danger text-xs">{changePwError}</p>
                    )}
                    {changePwSuccess && (
                      <p className="text-green-400 text-xs">{changePwSuccess}</p>
                    )}
                    <GlassButton
                      variant="primary"
                      size="sm"
                      onClick={handleChangePassword}
                      disabled={isChangingPw || !changePwOld || !changePwNew || !changePwConfirm}
                      loading={isChangingPw}
                    >
                      {isChangingPw ? 'Changing...' : 'Change Password'}
                    </GlassButton>
                  </div>
                </div>
              </div>
                </>
              )}
            </GlassCard>

            {/* Celebration Settings */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('celebrationSettings')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  &#127881; Celebrations
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.celebrationSettings ? '' : 'rotate-180'}`}>
                  &#9660;
                </span>
              </button>

              {!collapsedPanels.celebrationSettings && (
                <>
                  <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                    Configure visual and audio feedback for XP gains and level-ups.
                  </p>

                  <div className="space-y-4">
                    {/* Master toggle */}
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-medium text-text-primary">Enable Celebrations</h3>
                        <p className="text-[10px] text-text-tertiary">
                          Show visual feedback for XP and level-ups
                        </p>
                      </div>
                      <button
                        onClick={() => updateCelebrationSettings({ enabled: !celebrationSettings.enabled })}
                        className={`w-12 h-6 rounded-full transition-colors duration-200 relative ${
                          celebrationSettings.enabled ? 'bg-accent-primary' : 'bg-white/20'
                        }`}
                      >
                        <div className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                          celebrationSettings.enabled ? 'translate-x-7' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    {/* Sub-settings (only shown when enabled) */}
                    {celebrationSettings.enabled && (
                      <div className="pl-4 border-l-2 border-accent-primary/30 space-y-3">
                        {/* XP Toasts */}
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-text-secondary">XP Gain Notifications</span>
                          <button
                            onClick={() => updateCelebrationSettings({ xpToasts: !celebrationSettings.xpToasts })}
                            className={`w-10 h-5 rounded-full transition-colors duration-200 relative ${
                              celebrationSettings.xpToasts ? 'bg-accent-primary/80' : 'bg-white/20'
                            }`}
                          >
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                              celebrationSettings.xpToasts ? 'translate-x-5' : 'translate-x-0.5'
                            }`} />
                          </button>
                        </div>

                        {/* Level-up modals */}
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-text-secondary">Level-Up Celebrations</span>
                          <button
                            onClick={() => updateCelebrationSettings({ levelUpModals: !celebrationSettings.levelUpModals })}
                            className={`w-10 h-5 rounded-full transition-colors duration-200 relative ${
                              celebrationSettings.levelUpModals ? 'bg-accent-primary/80' : 'bg-white/20'
                            }`}
                          >
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                              celebrationSettings.levelUpModals ? 'translate-x-5' : 'translate-x-0.5'
                            }`} />
                          </button>
                        </div>

                        {/* Unlock animations */}
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-text-secondary">Skill Unlock Animations</span>
                          <button
                            onClick={() => updateCelebrationSettings({ unlockAnimations: !celebrationSettings.unlockAnimations })}
                            className={`w-10 h-5 rounded-full transition-colors duration-200 relative ${
                              celebrationSettings.unlockAnimations ? 'bg-accent-primary/80' : 'bg-white/20'
                            }`}
                          >
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                              celebrationSettings.unlockAnimations ? 'translate-x-5' : 'translate-x-0.5'
                            }`} />
                          </button>
                        </div>

                        {/* Confetti */}
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-text-secondary">Confetti Effects</span>
                          <button
                            onClick={() => updateCelebrationSettings({ confetti: !celebrationSettings.confetti })}
                            className={`w-10 h-5 rounded-full transition-colors duration-200 relative ${
                              celebrationSettings.confetti ? 'bg-accent-primary/80' : 'bg-white/20'
                            }`}
                          >
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                              celebrationSettings.confetti ? 'translate-x-5' : 'translate-x-0.5'
                            }`} />
                          </button>
                        </div>

                        {/* Sounds */}
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-text-secondary">Sound Effects</span>
                          <button
                            onClick={() => updateCelebrationSettings({ sounds: !celebrationSettings.sounds })}
                            className={`w-10 h-5 rounded-full transition-colors duration-200 relative ${
                              celebrationSettings.sounds ? 'bg-accent-primary/80' : 'bg-white/20'
                            }`}
                          >
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                              celebrationSettings.sounds ? 'translate-x-5' : 'translate-x-0.5'
                            }`} />
                          </button>
                        </div>

                        {/* Reduced motion */}
                        <div className="flex items-center justify-between pt-2 border-t border-white/10">
                          <div>
                            <span className="text-xs text-text-secondary">Reduced Motion</span>
                            <p className="text-[9px] text-text-tertiary">Disable animations for accessibility</p>
                          </div>
                          <button
                            onClick={() => updateCelebrationSettings({ reducedMotion: !celebrationSettings.reducedMotion })}
                            className={`w-10 h-5 rounded-full transition-colors duration-200 relative ${
                              celebrationSettings.reducedMotion ? 'bg-accent-primary/80' : 'bg-white/20'
                            }`}
                          >
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                              celebrationSettings.reducedMotion ? 'translate-x-5' : 'translate-x-0.5'
                            }`} />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </>
              )}
            </GlassCard>

            {/* Software Updates Section */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => togglePanel('softwareUpdates')}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary">
                  🔄 Software Updates
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.softwareUpdates ? '' : 'rotate-180'}`}>
                  ▼
                </span>
              </button>

              {!collapsedPanels.softwareUpdates && (
                <>
              <p className="text-[10px] text-text-tertiary mb-3 mt-2">
                Keep Qubes up to date with the latest features and security fixes.
              </p>

              <div className="space-y-3">
                {/* Update Available Banner */}
                {updateAvailable && updateStatus && (
                  <div className="bg-accent-primary/10 border border-accent-primary/30 rounded-lg p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-accent-primary">
                          Update Available!
                        </p>
                        <p className="text-[10px] text-text-secondary">
                          Version {updateStatus.newVersion} is ready to install
                          {updateSize && ` (${updateSize} download)`}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <GlassButton
                          onClick={dismissUpdate}
                          variant="secondary"
                          size="sm"
                          className="text-xs h-7 px-3"
                        >
                          Later
                        </GlassButton>
                        <GlassButton
                          onClick={installUpdate}
                          disabled={isDownloading}
                          size="sm"
                          className="text-xs h-7 px-3"
                        >
                          {isDownloading
                            ? (isHeavy && heavyStatus !== 'idle' && heavyStatus !== 'downloading'
                                ? heavyStatus === 'verifying' ? 'Verifying...'
                                : heavyStatus === 'installing' ? 'Installing...'
                                : heavyStatus === 'restarting' ? 'Restarting...'
                                : 'Updating...'
                              : 'Downloading...')
                            : 'Install Now'}
                        </GlassButton>
                      </div>
                    </div>
                    {isDownloading && downloadProgress && downloadProgress.total > 0 && (
                      <div className="mt-2">
                        <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-accent-primary transition-all duration-300"
                            style={{
                              width: `${(downloadProgress.downloaded / downloadProgress.total) * 100}%`
                            }}
                          />
                        </div>
                        {isHeavy && (
                          <p className="text-[9px] text-text-tertiary mt-1 text-right">
                            {Math.round((downloadProgress.downloaded / downloadProgress.total) * 100)}%
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Current Version & Check Button */}
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs text-text-secondary">Current Version</p>
                    <p className="text-sm font-mono text-text-primary">
                      {updateStatus?.currentVersion || 'Unknown'}
                    </p>
                  </div>
                  <GlassButton
                    onClick={checkForUpdates}
                    disabled={isChecking}
                    variant="secondary"
                    size="sm"
                    className="text-xs h-8 px-4"
                  >
                    {isChecking ? 'Checking...' : 'Check for Updates'}
                  </GlassButton>
                </div>

                {/* Error Display */}
                {updateError && (
                  <p className="text-[10px] text-accent-danger">
                    Error: {updateError}
                  </p>
                )}

                {/* No Update Available Message */}
                {updateStatus && !updateStatus.available && !updateError && !isChecking && (
                  <p className="text-[10px] text-accent-success">
                    ✓ You're running the latest version
                  </p>
                )}
              </div>
                </>
              )}
            </GlassCard>
            {/* =========================================================== */}
            {/* Endpoints Panel (relay node + network endpoints combined)   */}
            {/* =========================================================== */}
            <GlassCard className="p-4 mt-4">
              <button
                onClick={() => { togglePanel('endpoints'); if (collapsedPanels.endpoints) { loadRelayData(); loadEndpoints(); } }}
                className="w-full flex items-center justify-between text-left"
              >
                <h2 className="text-lg font-display text-text-primary flex items-center gap-2">
                  🔌 Endpoints
                  <span className={`inline-block w-2 h-2 rounded-full ${relayStatus.running ? 'bg-green-400 shadow-[0_0_6px_#4ade80]' : 'bg-red-500'}`} />
                </h2>
                <span className={`text-text-tertiary transition-transform ${collapsedPanels.endpoints ? '' : 'rotate-180'}`}>▼</span>
              </button>

              {!collapsedPanels.endpoints && (
                <>
                  {/* ── RELAY NODE ─────────────────────────────────────── */}
                  <div className="border border-white/10 rounded-lg p-3 mt-4 mb-4">
                    <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wide mb-3 flex items-center gap-2">
                      📡 Relay Node
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${relayStatus.running ? 'bg-green-400 shadow-[0_0_6px_#4ade80] animate-pulse' : 'bg-red-500'}`} />
                      <span className="text-[10px] font-normal normal-case text-text-tertiary">{relayStatus.running ? `Running — ${relayStatus.online_peers}/${relayStatus.peer_count} peers` : 'Stopped'}</span>
                    </h3>

                    {relayStatus.running && relayStatus.peer_id && (
                      <p className="text-[10px] font-mono text-text-tertiary mb-2 truncate">Peer ID: {relayStatus.peer_id}</p>
                    )}

                    {/* LOCAL RELAY controls */}
                    <div className="mb-3">
                      <label className="flex items-center gap-2 mb-2 cursor-pointer">
                        <input type="checkbox" className="w-3 h-3 accent-accent-primary" checked={relayPrefs.relay_enabled}
                          onChange={e => setRelayPrefs(p => ({ ...p, relay_enabled: e.target.checked }))} />
                        <span className="text-xs text-text-secondary">Enable relay node</span>
                      </label>

                      <div className="mb-2">
                        <p className="text-[10px] text-text-tertiary mb-1">p2pd binary path <span className="text-text-tertiary/60">(leave blank = use bundled)</span></p>
                        <input
                          type="text"
                          className="w-full bg-black/40 border border-white/10 rounded px-2 py-1 text-xs font-mono text-text-primary placeholder-text-tertiary/40 focus:outline-none focus:border-accent-primary/50"
                          placeholder="embedded (default)"
                          value={relayPrefs.p2pd_binary_path || ''}
                          onChange={e => setRelayPrefs(p => ({ ...p, p2pd_binary_path: e.target.value || null }))}
                        />
                      </div>

                      <div className="flex gap-3 mb-2 flex-wrap text-[10px] text-text-tertiary">
                        <label className="flex items-center gap-1">Port <input type="number" className="w-16 bg-black/40 border border-white/10 rounded px-1 py-0.5 text-xs font-mono text-text-primary" value={relayPrefs.relay_listen_port} onChange={e => setRelayPrefs(p => ({ ...p, relay_listen_port: parseInt(e.target.value) || 0 }))} /></label>
                        <label className="flex items-center gap-1">Max conn <input type="number" className="w-14 bg-black/40 border border-white/10 rounded px-1 py-0.5 text-xs font-mono text-text-primary" value={relayPrefs.relay_max_connections} onChange={e => setRelayPrefs(p => ({ ...p, relay_max_connections: parseInt(e.target.value) || 50 }))} /></label>
                        <label className="flex items-center gap-1">Retention <input type="number" className="w-10 bg-black/40 border border-white/10 rounded px-1 py-0.5 text-xs font-mono text-text-primary" value={relayPrefs.relay_retention_days} onChange={e => setRelayPrefs(p => ({ ...p, relay_retention_days: parseInt(e.target.value) || 7 }))} /> days</label>
                      </div>

                      <div className="flex gap-2">
                        <GlassButton variant="primary" size="sm" onClick={handleStartRelay} disabled={relayLoading || relayStatus.running} className="text-xs">▶ Start</GlassButton>
                        <GlassButton variant="secondary" size="sm" onClick={handleStopRelay} disabled={relayLoading || !relayStatus.running} className="text-xs">■ Stop</GlassButton>
                      </div>
                    </div>

                    {/* SEED RELAYS */}
                    <div className="border-t border-white/5 pt-3 mb-3">
                      <p className="text-[10px] text-text-tertiary uppercase tracking-wide mb-2">Seed Relays (BitFaced-operated)</p>
                      <div className="space-y-1">
                        {[
                          { label: 'US-East', id: 'QmRelayUS' },
                          { label: 'EU', id: 'QmRelayEU' },
                          { label: 'Asia', id: 'QmRelayAS' },
                        ].map((seed) => {
                          const live = relayPeers.find(p => p.builtin && p.label?.includes(seed.label));
                          return (
                            <div key={seed.id} className="flex items-center gap-2">
                              <span className={`w-2 h-2 rounded-full flex-shrink-0 ${live?.online === true ? 'bg-green-400' : live?.online === false ? 'bg-red-500' : 'bg-white/20'}`} />
                              <span className="text-[10px] text-text-secondary">BitFaced {seed.label}</span>
                              {live?.latency_ms != null && <span className="text-[10px] text-text-tertiary ml-auto">{live.latency_ms}ms</span>}
                              {live?.online === false && <span className="text-[10px] text-red-400 ml-auto">offline</span>}
                              {!live && <span className="text-[10px] text-text-tertiary/40 ml-auto">start relay to ping</span>}
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* CUSTOM RELAYS */}
                    <div className="border-t border-white/5 pt-3 mb-3">
                      <p className="text-[10px] text-text-tertiary uppercase tracking-wide mb-2">Custom Relays</p>
                      <div className="space-y-1 mb-2">
                        {relayPeers.filter(p => !p.builtin).map((peer, i) => (
                          <div key={i} className="flex items-center gap-2">
                            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${peer.online === true ? 'bg-green-400' : peer.online === false ? 'bg-red-500' : 'bg-white/20'}`} />
                            <span className="text-[10px] font-mono text-text-secondary truncate flex-1">{peer.multiaddr}</span>
                            <button onClick={() => handleRemoveRelayPeer(peer.multiaddr)} className="text-[10px] text-red-400 hover:text-red-300">Remove</button>
                          </div>
                        ))}
                        {relayPeers.filter(p => !p.builtin).length === 0 && (
                          <p className="text-[10px] text-text-tertiary/40 italic">No custom relays added</p>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <input type="text" className="flex-1 bg-black/40 border border-white/10 rounded px-2 py-1 text-xs font-mono text-text-primary placeholder-text-tertiary/40 focus:outline-none focus:border-accent-primary/50"
                          placeholder="/ip4/.../tcp/4001/p2p/Qm..." value={newRelayPeer} onChange={e => setNewRelayPeer(e.target.value)}
                          onKeyDown={e => e.key === 'Enter' && handleAddRelayPeer()} />
                        <GlassButton variant="secondary" size="sm" onClick={handleAddRelayPeer} className="text-xs">Add</GlassButton>
                      </div>
                    </div>

                    {/* RELAY BUNDLE */}
                    <div className="border-t border-white/5 pt-3">
                      <p className="text-[10px] text-text-tertiary uppercase tracking-wide mb-2">Relay Bundle</p>
                      <p className="text-[10px] text-text-tertiary/60 mb-2">Update p2pd binary + relay list independently from the main app.</p>
                      <div className="flex gap-2 items-center">
                        <GlassButton variant="secondary" size="sm" onClick={handleCheckBundleUpdate} className="text-xs">Check Update</GlassButton>
                        <GlassButton variant="primary" size="sm" onClick={handleUpdateBundle} className="text-xs">Update Bundle</GlassButton>
                      </div>
                      {relayBundleStatus && <p className="text-[10px] text-text-tertiary mt-2">{relayBundleStatus}</p>}
                    </div>
                  </div>

                  {/* ── NETWORK ENDPOINTS ──────────────────────────────── */}
                  <div className="font-mono space-y-4">
                    <p className="text-green-400/60 text-xs">// NETWORK ENDPOINTS</p>

                    <div>
                      <p className="text-[10px] uppercase tracking-widest text-green-400/70 mb-1">FULCRUM / ELECTRUM NODES</p>
                      <textarea
                        className="w-full bg-black/40 border border-green-900/60 rounded px-3 py-2 text-xs font-mono text-green-400 placeholder-green-900/50 focus:outline-none focus:border-green-700 resize-none"
                        rows={5}
                        placeholder={"wss://bch.imaginary.cash:50004\nwss://electroncash.de:50004"}
                        value={endpointPrefs.fulcrum_nodes.join('\n')}
                        onChange={e => { setEndpointPrefs(p => ({ ...p, fulcrum_nodes: e.target.value.split('\n').filter(l => l.trim()) })); setEndpointsDirty(true); }}
                      />
                      <p className="text-[9px] text-green-900/80 mt-0.5">one wss:// URL per line</p>
                    </div>

                    <div>
                      <p className="text-[10px] uppercase tracking-widest text-green-400/70 mb-1">NOSTR RELAYS</p>
                      <textarea
                        className="w-full bg-black/40 border border-green-900/60 rounded px-3 py-2 text-xs font-mono text-green-400 placeholder-green-900/50 focus:outline-none focus:border-green-700 resize-none"
                        rows={4}
                        placeholder={"wss://relay.damus.io\nwss://nos.lol"}
                        value={endpointPrefs.nostr_relays.join('\n')}
                        onChange={e => { setEndpointPrefs(p => ({ ...p, nostr_relays: e.target.value.split('\n').filter(l => l.trim()) })); setEndpointsDirty(true); }}
                      />
                      <p className="text-[9px] text-green-900/80 mt-0.5">one wss:// URL per line — used as Nostr transport fallback</p>
                    </div>
                  </div>

                  {/* RESET / CANCEL / SAVE */}
                  <div className="flex items-center justify-between mt-5 pt-4 border-t border-white/5">
                    <div>
                      {endpointSaveMsg && <p className="text-[10px] text-text-tertiary">{endpointSaveMsg}</p>}
                      {!endpointSaveMsg && <p className="text-[10px] text-text-tertiary/40">changes apply on page reload</p>}
                    </div>
                    <div className="flex gap-2">
                      <GlassButton variant="danger" size="sm" onClick={handleResetEndpoints} disabled={savingEndpoints} className="text-xs">RESET</GlassButton>
                      <GlassButton variant="secondary" size="sm" onClick={() => { setEndpointsDirty(false); setEndpointSaveMsg(null); loadEndpoints(); }} disabled={savingEndpoints} className="text-xs">CANCEL</GlassButton>
                      <GlassButton variant="primary" size="sm" onClick={handleSaveEndpoints} disabled={savingEndpoints || !endpointsDirty} className="text-xs font-semibold">SAVE</GlassButton>
                    </div>
                  </div>
                </>
              )}
            </GlassCard>

          </div>
        </div>
      </div>
    </div>
  );
};
