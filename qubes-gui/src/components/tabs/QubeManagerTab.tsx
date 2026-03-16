import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import Cropper from 'react-easy-crop';
import type { Area } from 'react-easy-crop';
import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { emit } from '@tauri-apps/api/event';
import { open as openShell } from '@tauri-apps/plugin-shell';
import { readTextFile } from '@tauri-apps/plugin-fs';
import { open as openDialog, save as saveDialog } from '@tauri-apps/plugin-dialog';
import { Qube, Tab } from '../../types';
import { GlassCard, GlassButton } from '../glass';
import DarkSelect from '../DarkSelect';
import { WalletSecurityModal } from '../dialogs/WalletSecurityModal';
import { QubeSettingsModal } from '../dialogs/QubeSettingsModal';
import { useAuth } from '../../hooks/useAuth';
import { useQubeOrder } from '../../hooks/useQubeOrder';
import { useQubeSelection } from '../../hooks/useQubeSelection';
import { useWalletCache } from '../../hooks/useWalletCache';
import { useWallet } from '../../contexts/WalletContext';
import WalletConnectButton from '../WalletConnectButton';
import { useModels } from '../../hooks/useModels';
import { useChainState } from '../../contexts/ChainStateContext';
import { useVoiceLibrary } from '../../contexts/VoiceLibraryContext';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  rectSortingStrategy,
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

interface QubeManagerTabProps {
  qubes: Qube[];
  onCreateQube: () => void;
  onEditQube: (qube: Qube) => void;
  onDeleteQube: (qube: Qube) => void;
  onUpdateQubeConfig: (qubeId: string, updates: { ai_model?: string; voice_model?: string; tts_enabled?: boolean; evaluation_model?: string }) => Promise<void>;
}

export const QubeManagerTab: React.FC<QubeManagerTabProps> = ({
  qubes,
  onCreateQube,
  onEditQube,
  onDeleteQube,
  onUpdateQubeConfig,
}) => {
  const { userId, password: masterPassword, dataDir } = useAuth();
  const wallet = useWallet();

  // Check Pinata configuration on mount
  useEffect(() => {
    const checkPinataConfig = async () => {
      if (!userId || !masterPassword) return;
      try {
        const result = await invoke<{ providers: string[] }>('get_configured_api_keys', {
          userId,
          password: masterPassword,
        });
        setPinataConfigured(result.providers.includes('pinata_jwt'));
      } catch (error) {
        console.error('Failed to check Pinata configuration:', error);
        setPinataConfigured(false);
      }
    };
    checkPinataConfig();
  }, [userId, masterPassword]);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const { orderByUser, setQubeOrder, getQubeOrder } = useQubeOrder();
  const { toggleSelection, setCurrentTab, getSelectedQubeIds, selectionByTab, currentTab, switchTabAndSelect } = useQubeSelection();

  // Get selected qube IDs for the current tab
  const selectedQubeIds = selectionByTab[currentTab] || [];

  const [selectedQubeForSync, setSelectedQubeForSync] = useState<Qube | null>(null); // used by transfer
  const [isTransferring, setIsTransferring] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [showTransferModal, setShowTransferModal] = useState(false);
  const [showImportFromWalletModal, setShowImportFromWalletModal] = useState(false);

  // Transfer modal state
  const [transferRecipientAddress, setTransferRecipientAddress] = useState('');
  const [transferRecipientPublicKey, setTransferRecipientPublicKey] = useState('');
  const [isResolvingPublicKey, setIsResolvingPublicKey] = useState(false);
  const [transferConfirmed, setTransferConfirmed] = useState(false);
  const [transferStep, setTransferStep] = useState<'idle' | 'preparing' | 'signing' | 'finalizing'>('idle');
  const [transferPendingId, setTransferPendingId] = useState<string | null>(null);

  // Import from wallet modal state (WalletConnect-based, no WIF)
  const [isScanning, setIsScanning] = useState(false);
  const [importProgress, setImportProgress] = useState<{ current: number; total: number; name: string } | null>(null);

  // Account backup state
  const [showBackupModal, setShowBackupModal] = useState(false);
  const [isBackingUp, setIsBackingUp] = useState(false);

  // IPFS backup all state
  const [showIpfsBackupAllModal, setShowIpfsBackupAllModal] = useState(false);
  const [isIpfsBackingUpAll, setIsIpfsBackingUpAll] = useState(false);
  const [lastIpfsSyncTime, setLastIpfsSyncTime] = useState<Date | null>(null);
  const [ipfsSyncPrefs, setIpfsSyncPrefs] = useState({
    on_anchor: false,
    periodic: false,
    interval_min: 15,
  });

  // Account restore (merge) state
  const [showRestoreModal, setShowRestoreModal] = useState(false);
  const [restoreFilePath, setRestoreFilePath] = useState('');
  const [restorePassword, setRestorePassword] = useState('');
  const [isRestoring, setIsRestoring] = useState(false);
  // Restore mode
  const [restoreMode, setRestoreMode] = useState<'local' | 'ipfs'>('local');
  // IPFS restore state
  const [ipfsInputMode, setIpfsInputMode] = useState<'jwt' | 'cid'>('jwt');
  const [ipfsPinataJwt, setIpfsPinataJwt] = useState('');
  const [ipfsDirectCid, setIpfsDirectCid] = useState('');
  const [ipfsBackupList, setIpfsBackupList] = useState<Array<{cid: string; name: string; date: string; size_bytes: number}>>([]);
  const [ipfsSelectedCid, setIpfsSelectedCid] = useState('');
  const [ipfsListError, setIpfsListError] = useState<string | null>(null);
  const [isListingBackups, setIsListingBackups] = useState(false);
  // WC 2FA for restore
  const [restoreWalletSig, setRestoreWalletSig] = useState('');
  const [restoreWcAddress, setRestoreWcAddress] = useState('');
  const [isSigningRestoreWc, setIsSigningRestoreWc] = useState(false);

  // Pinata configuration check
  const [pinataConfigured, setPinataConfigured] = useState<boolean | null>(null);

  // Total balance widget state
  const { wallets: walletCache, setBalance: setCachedBalanceGlobal } = useWalletCache();
  const [isRefreshingAll, setIsRefreshingAll] = useState(false);
  const [showSweepAllModal, setShowSweepAllModal] = useState(false);
  const [sweepAllAddress, setSweepAllAddress] = useState('');
  const [isSweepingAll, setIsSweepingAll] = useState(false);
  const [sweepAllResult, setSweepAllResult] = useState<string | null>(null);

  const totalSats = qubes.reduce((sum, q) => sum + (walletCache[q.qube_id]?.balance ?? 0), 0);
  const hasAnyBalance = qubes.some((q) => (walletCache[q.qube_id]?.balance ?? 0) > 0);

  const handleRefreshAllBalances = async () => {
    if (isRefreshingAll || !userId || !masterPassword) return;
    setIsRefreshingAll(true);
    try {
      await Promise.all(
        qubes.filter((q) => q.wallet_address).map(async (q) => {
          try {
            const result = await invoke<{ success?: boolean; balance_sats?: number }>('get_wallet_info', {
              userId, qubeId: q.qube_id, password: masterPassword,
            });
            if (result.balance_sats !== undefined) setCachedBalanceGlobal(q.qube_id, result.balance_sats);
          } catch { /* ignore per-qube errors */ }
        })
      );
    } finally {
      setIsRefreshingAll(false);
    }
  };

  const handleSweepAll = async () => {
    if (!sweepAllAddress || !userId || !masterPassword) return;
    setIsSweepingAll(true);
    setSweepAllResult(null);
    let totalSwept = 0;
    const errors: string[] = [];
    for (const q of qubes.filter((q) => q.wallet_address && (walletCache[q.qube_id]?.balance ?? 0) > 0)) {
      try {
        const result = await invoke<{ success: boolean; swept_sats: number; error?: string }>('sweep_qube_wallet', {
          userId, qubeId: q.qube_id, sweepAddress: sweepAllAddress, password: masterPassword,
        });
        if (result.success) { totalSwept += result.swept_sats; setCachedBalanceGlobal(q.qube_id, 0); }
        else errors.push(`${q.name}: ${result.error}`);
      } catch (err) { errors.push(`${q.name}: ${String(err)}`); }
    }
    setSweepAllResult(errors.length === 0
      ? `✓ Swept ${(totalSwept / 1e8).toFixed(8)} BCH from ${qubes.length} qubes`
      : `Swept ${(totalSwept / 1e8).toFixed(8)} BCH. Errors: ${errors.join('; ')}`);
    setIsSweepingAll(false);
  };
  const [walletQubes, setWalletQubes] = useState<Array<{
    qube_id: string;
    qube_name: string;
    category_id: string;
    ipfs_cid: string;
    chain_length: number;
    sync_timestamp: number;
    already_imported?: boolean;
  }>>([]);

  // Reset qube state (new save slot)
  const [qubeToReset, setQubeToReset] = useState<Qube | null>(null);
  const [isResetting, setIsResetting] = useState(false);

  // Set up drag-and-drop sensors
  // Using distance activation constraint to distinguish click from drag
  // This allows avatar click to flip the card while drag still works for reordering
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8, // Must move 8px before drag activates
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Get ordered qubes based on stored order
  const orderedQubes = useMemo(() => {
    if (!userId) return qubes;

    const savedOrder = getQubeOrder(userId);
    if (!savedOrder || savedOrder.length === 0) return qubes;

    // Create a map for quick lookup
    const qubeMap = new Map(qubes.map(q => [q.qube_id, q]));

    // Start with qubes in saved order
    const ordered: Qube[] = [];
    const processedIds = new Set<string>();

    for (const id of savedOrder) {
      const qube = qubeMap.get(id);
      if (qube) {
        ordered.push(qube);
        processedIds.add(id);
      }
    }

    // Add any new qubes not in the saved order
    for (const qube of qubes) {
      if (!processedIds.has(qube.qube_id)) {
        ordered.push(qube);
      }
    }

    return ordered;
  }, [qubes, userId, orderByUser]);

  // Initialize saved order for new users or when first qubes are added
  useEffect(() => {
    if (userId && qubes.length > 0) {
      const currentOrder = getQubeOrder(userId);

      // Only set initial order if we have no saved order at all
      if (currentOrder.length === 0) {
        setQubeOrder(userId, qubes.map(q => q.qube_id));
      }
    }
  }, [qubes.length, userId]);

  const filteredQubes = orderedQubes.filter(qube =>
    qube.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    qube.ai_model.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id && userId) {
      // Work with the full ordered list, not the filtered one
      const oldIndex = orderedQubes.findIndex(q => q.qube_id === active.id);
      const newIndex = orderedQubes.findIndex(q => q.qube_id === over.id);

      const newOrder = arrayMove(orderedQubes, oldIndex, newIndex);
      setQubeOrder(userId, newOrder.map(q => q.qube_id));
    }
  };

  const handleSelectQube = (qube: Qube) => {
    // Switch to Chat tab (id='dashboard') and select the qube in a single atomic operation
    switchTabAndSelect('dashboard', qube.qube_id);
  };

  // Handle reset qube confirmation and execution
  const handleResetQube = async () => {
    if (!qubeToReset || !userId) return;

    setIsResetting(true);
    try {
      const result = await invoke<{ success: boolean; error?: string }>('reset_qube', {
        userId,
        qubeId: qubeToReset.qube_id,
        password: masterPassword,
      });

      if (result.success) {
        console.log(`Qube ${qubeToReset.name} reset to fresh state`);
        // Emit event to refresh qube list
        await emit('qube-reset', { qubeId: qubeToReset.qube_id });
        // Refresh page to show updated state
        window.location.reload();
      } else {
        console.error(`Failed to reset qube: ${result.error}`);
        alert(`Failed to reset qube: ${result.error}`);
      }
    } catch (error) {
      console.error('Reset qube error:', error);
      alert(`Failed to reset qube: ${error}`);
    } finally {
      setIsResetting(false);
      setQubeToReset(null);
    }
  };

  // Construct avatar path from chain folder
  const getAvatarPath = (qube: Qube): string => {
    // Priority 1: IPFS URL from backend
    if (qube.avatar_url) return qube.avatar_url;

    // Priority 2: Local file path via Tauri convertFileSrc
    if (qube.avatar_local_path) {
      return convertFileSrc(qube.avatar_local_path);
    }

    // Priority 3: Construct path from qube info (fallback for older qubes)
    const projectRoot = 'C:/Users/bit_f/Projects/Qubes';
    const filePath = `${projectRoot}/data/users/${userId}/qubes/${qube.name}_${qube.qube_id}/chain/${qube.qube_id}_avatar.png`;
    return convertFileSrc(filePath);
  };

  // Backup entire account
  const handleBackupAccount = async () => {
    if (!masterPassword) return;

    try {
      // Default to backups/ folder next to the app binary (portable),
      // fallback to the Qubes data directory
      const filename = `${userId || 'qubes'}-backup.qube-backup`;
      let defaultPath = filename;
      try {
        const bundleDir = await invoke<string>('get_bundle_dir');
        defaultPath = `${bundleDir}/backups/${filename}`;
      } catch {
        if (dataDir) defaultPath = `${dataDir}/backups/${filename}`;
      }

      const filePath = await saveDialog({
        title: 'Save Account Backup',
        defaultPath,
        filters: [{ name: 'Qube Backup', extensions: ['qube-backup'] }],
      });
      if (!filePath) return;

      setIsBackingUp(true);

      // Clean up incomplete qube directories before backup
      try {
        const cleanup = await invoke<{ success: boolean; deleted_count?: number }>('cleanup_incomplete_qubes', {
          userId,
          masterPassword: masterPassword,
        });
        if (cleanup.success && cleanup.deleted_count && cleanup.deleted_count > 0) {
          console.log(`Cleaned up ${cleanup.deleted_count} incomplete qube directories`);
        }
      } catch (e) {
        console.warn('Cleanup before backup failed (non-fatal):', e);
      }

      const result = await invoke<{ success: boolean; file_path?: string; qube_count?: number; error?: string }>('export_account_backup', {
        userId,
        exportPath: filePath,
        exportPassword: masterPassword,
        masterPassword: masterPassword,
      });

      if (result.success) {
        alert(`Account backup saved!\n\n${result.qube_count} Qube(s) exported to:\n${result.file_path}`);
        setShowBackupModal(false);
      } else {
        alert(`Backup failed: ${result.error}`);
      }
    } catch (error) {
      console.error('Backup failed:', error);
      alert(`Backup failed: ${String(error)}`);
    } finally {
      setIsBackingUp(false);
    }
  };

  const handleIpfsBackupAll = async (silent = false) => {
    if (!userId || !masterPassword) return;
    setIsIpfsBackingUpAll(true);

    // Try to get wallet signature silently (non-blocking)
    let walletSigForBackup = '';
    try {
      const { getSession: wcGetSession, signMessage: wcSignMessage } = await import('../../services/walletConnect');
      const session = wcGetSession();
      if (session) {
        walletSigForBackup = await wcSignMessage('qubes-backup-key:v1', 'Qubes is syncing your backup to IPFS');
      }
    } catch (e) {
      console.warn('[IPFS Backup] No wallet sig, using master-password-only backup:', e);
    }

    try {
      const result = await invoke<{ success: boolean; ipfs_cid?: string; qube_count?: number; error?: string }>('export_account_backup_ipfs', {
        userId,
        exportPassword: masterPassword,
        masterPassword,
        walletSig: walletSigForBackup,
      });
      if (result.success) {
        setLastIpfsSyncTime(new Date());
        setShowIpfsBackupAllModal(false);
        if (!silent) {
          alert(`All Qubes backed up to IPFS!\n\n${result.qube_count} Qube(s) uploaded.\nCID: ${result.ipfs_cid}\n\nYou can restore using your Pinata account or this CID.`);
        }
      } else if (!silent) {
        alert(`IPFS backup failed: ${result.error}`);
      }
    } catch (error) {
      if (!silent) alert(`IPFS backup failed: ${String(error)}`);
    } finally {
      setIsIpfsBackingUpAll(false);
    }
  };

  // Reset restore modal state
  const resetRestoreState = () => {
    setRestoreFilePath('');
    setRestorePassword('');
    setRestoreMode('local');
    setIpfsInputMode('jwt');
    setIpfsPinataJwt('');
    setIpfsDirectCid('');
    setIpfsBackupList([]);
    setIpfsSelectedCid('');
    setIpfsListError(null);
    setRestoreWalletSig('');
    setRestoreWcAddress('');
  };

  // Restore (merge) qubes from a backup file
  const handleRestoreFromBackup = async () => {
    if (!restoreFilePath) { alert('Please select a backup file'); return; }
    if (!restorePassword) { alert('Please enter the backup password'); return; }

    try {
      setIsRestoring(true);
      const result = await invoke<{ success: boolean; imported_count?: number; skipped_count?: number; user_id?: string; error?: string }>('import_account_backup', {
        userId,
        importPath: restoreFilePath,
        importPassword: restorePassword,
        masterPassword: masterPassword,
        walletSig: restoreWalletSig || null,
      });

      if (result.success) {
        const imported = result.imported_count || 0;
        const skipped = result.skipped_count || 0;
        alert(`Restore complete!\n\n${imported} Qube(s) imported${skipped > 0 ? `\n${skipped} already existed (skipped)` : ''}`);
        setShowRestoreModal(false);
        resetRestoreState();
        if (imported > 0) {
          window.dispatchEvent(new CustomEvent('qube-created'));
        }
      } else {
        alert(`Restore failed: ${result.error}`);
      }
    } catch (error) {
      console.error('Restore failed:', error);
      alert(`Restore failed: ${String(error)}`);
    } finally {
      setIsRestoring(false);
    }
  };

  const handleListBackupsForRestore = async () => {
    if (!ipfsPinataJwt.trim()) return;
    setIsListingBackups(true);
    setIpfsListError(null);
    setIpfsBackupList([]);
    setIpfsSelectedCid('');
    try {
      const result = await invoke<{ success: boolean; backups?: Array<{cid: string; name: string; date: string; size_bytes: number}>; error?: string }>(
        'list_account_backups_pinata',
        { userId: '_restore', pinataJwt: ipfsPinataJwt.trim() }
      );
      if (result.success && result.backups) {
        if (result.backups.length === 0) {
          setIpfsListError('No Qubes backups found in this Pinata account.');
        } else {
          setIpfsBackupList(result.backups);
          setIpfsSelectedCid(result.backups[0].cid);
        }
      } else {
        setIpfsListError(result.error || 'Failed to list backups');
      }
    } catch (err) {
      setIpfsListError(`${err}`);
    } finally {
      setIsListingBackups(false);
    }
  };

  const handleIpfsRestore = async () => {
    const cidToRestore = ipfsInputMode === 'cid' ? ipfsDirectCid.trim() : ipfsSelectedCid;
    if (!cidToRestore || !restorePassword) return;
    setIsRestoring(true);
    try {
      const result = await invoke<{ success: boolean; imported_count?: number; skipped_count?: number; error?: string }>(
        'import_account_backup_ipfs',
        {
          userId,
          ipfsCid: cidToRestore,
          importPassword: restorePassword,
          masterPassword,
          walletSig: restoreWalletSig || null,
        }
      );
      if (result.success) {
        alert(`Restored from IPFS! ${result.imported_count} Qube(s) imported, ${result.skipped_count} skipped.`);
        setShowRestoreModal(false);
        resetRestoreState();
      } else {
        alert(`IPFS Restore failed: ${result.error}`);
      }
    } catch (err) {
      alert(`IPFS Restore error: ${err}`);
    } finally {
      setIsRestoring(false);
    }
  };

  const handleSignRestoreWc = async () => {
    setIsSigningRestoreWc(true);
    try {
      const { getSession, signMessage: wcSignMessage } = await import('../../services/walletConnect');
      const session = await getSession();
      if (session) {
        const sig = await wcSignMessage('qubes-backup-key:v1', 'Qubes needs to verify wallet ownership to decrypt your backup', session.topic);
        setRestoreWalletSig(sig);
        setRestoreWcAddress(wallet.address || '');
      }
    } catch (err) {
      console.error('WC sign failed:', err);
    } finally {
      setIsSigningRestoreWc(false);
    }
  };

  // Load IPFS sync preferences from settings
  useEffect(() => {
    if (!userId) return;
    invoke<{ auto_sync_ipfs_on_anchor: boolean; auto_sync_ipfs_periodic: boolean; auto_sync_ipfs_interval: number }>(
      'get_block_preferences', { userId }
    ).then(prefs => {
      setIpfsSyncPrefs({
        on_anchor: prefs.auto_sync_ipfs_on_anchor,
        periodic: prefs.auto_sync_ipfs_periodic,
        interval_min: prefs.auto_sync_ipfs_interval ?? 15,
      });
    }).catch(() => {});
  }, [userId]);

  // Periodic IPFS sync — interval controlled by Settings → auto_sync_ipfs_interval
  useEffect(() => {
    if (!userId || !masterPassword || pinataConfigured !== true || !ipfsSyncPrefs.periodic) return;
    const interval = setInterval(() => {
      handleIpfsBackupAll(true);
    }, ipfsSyncPrefs.interval_min * 60 * 1000);
    return () => clearInterval(interval);
  }, [userId, masterPassword, pinataConfigured, ipfsSyncPrefs.periodic, ipfsSyncPrefs.interval_min]);

  // Sync to IPFS after every anchor — controlled by Settings → auto_sync_ipfs_on_anchor
  useEffect(() => {
    if (!userId || !masterPassword || pinataConfigured !== true || !ipfsSyncPrefs.on_anchor) return;
    let unlisten: (() => void) | undefined;
    import('@tauri-apps/api/event').then(({ listen }) => {
      listen<{ qube_id: string }>('anchor-complete', () => {
        handleIpfsBackupAll(true);
      }).then(fn => { unlisten = fn; });
    });
    return () => { unlisten?.(); };
  }, [userId, masterPassword, pinataConfigured, ipfsSyncPrefs.on_anchor]);

  // Transfer handler - opens transfer modal
  const handleTransferClick = () => {
    const selectedId = selectedQubeIds[0];
    const qubeToTransfer = selectedId ? qubes.find(q => q.qube_id === selectedId) : null;

    if (!qubeToTransfer) {
      alert('Please select a Qube to transfer');
      return;
    }

    // Check if Qube has NFT (required for transfer)
    if (!qubeToTransfer.nft_category_id) {
      alert('This Qube does not have an NFT. Please mint an NFT first before transferring.');
      return;
    }

    // Check if Pinata is configured (required for IPFS upload during transfer)
    if (pinataConfigured === false) {
      alert('Pinata API key not configured.\n\nTo transfer your Qube, you need to add your Pinata JWT in:\nSettings → API Keys → Pinata IPFS\n\nGet a free API key at: https://app.pinata.cloud/developers/api-keys');
      return;
    }

    setSelectedQubeForSync(qubeToTransfer);
    setShowTransferModal(true);
  };

  // Import from Wallet handler - opens import modal
  const handleImportFromWalletClick = () => {
    setShowImportFromWalletModal(true);
  };

  // Resolve recipient public key from address
  const handleResolvePublicKey = async () => {
    if (!transferRecipientAddress || !userId) return;

    setIsResolvingPublicKey(true);
    try {
      const result = await invoke<{ success: boolean; public_key?: string; found: boolean; error?: string }>('resolve_public_key', {
        userId,
        address: transferRecipientAddress,
      });

      if (result.found && result.public_key) {
        setTransferRecipientPublicKey(result.public_key);
      } else {
        alert('Could not find public key for this address.\n\nThe recipient may not have spent from this address yet.\nPlease ask them for their public key directly.');
      }
    } catch (error) {
      alert(`Failed to resolve public key: ${error}`);
    } finally {
      setIsResolvingPublicKey(false);
    }
  };

  // Execute transfer — 3-step WalletConnect flow (no WIF)
  const handleExecuteTransfer = async () => {
    if (!selectedQubeForSync || !userId || !masterPassword) {
      alert('Missing required data');
      return;
    }
    if (!transferRecipientAddress || !transferRecipientPublicKey) {
      alert('Please fill in recipient address and public key');
      return;
    }
    if (!wallet.connected || !wallet.address) {
      alert('Please connect your wallet via WalletConnect first. Your wallet signs the NFT transfer — no private key needed.');
      return;
    }
    if (!transferConfirmed) {
      alert('Please confirm that you understand this action is irreversible');
      return;
    }

    setIsTransferring(true);
    try {
      // Step 1: Prepare — sync to IPFS, re-encrypt for recipient, build unsigned tx
      setTransferStep('preparing');
      const prepResult = await invoke<{
        success: boolean;
        wc_transaction?: string;
        pending_transfer_id?: string;
        category_id?: string;
        error?: string;
      }>('prepare_transfer_wc', {
        userId,
        qubeId: selectedQubeForSync.qube_id,
        recipientAddress: transferRecipientAddress,
        recipientPublicKey: transferRecipientPublicKey,
        senderAddress: wallet.address,
        masterPassword,
      });

      if (!prepResult.success || !prepResult.wc_transaction || !prepResult.pending_transfer_id) {
        alert(`Transfer preparation failed: ${prepResult.error}`);
        return;
      }

      setTransferPendingId(prepResult.pending_transfer_id);

      // Step 2: Sign — send unsigned tx to wallet via WalletConnect
      setTransferStep('signing');
      const signResult = await wallet.signTransaction(prepResult.wc_transaction);

      // Step 3: Finalize — delete local data, refresh IPFS backup
      setTransferStep('finalizing');
      const finalResult = await invoke<{
        success: boolean;
        transfer_txid?: string;
        recipient_address?: string;
        error?: string;
      }>('finalize_transfer_wc', {
        userId,
        pendingTransferId: prepResult.pending_transfer_id,
        transferTxid: signResult.signedTransactionHash,
        masterPassword,
      });

      if (finalResult.success) {
        alert(`Transfer successful!\n\nTransaction: ${signResult.signedTransactionHash}\nRecipient: ${finalResult.recipient_address}\n\n${selectedQubeForSync.name} has been transferred and removed from your device.`);
        setShowTransferModal(false);
        resetTransferState();
        window.dispatchEvent(new CustomEvent('qube-deleted', { detail: { qube_id: selectedQubeForSync.qube_id } }));
        // Refresh IPFS backup silently so transferred Qube is removed from backup
        handleIpfsBackupAll(true);
      } else {
        alert(`Transfer finalize failed: ${finalResult.error}\n\nThe NFT was already sent (txid: ${signResult.signedTransactionHash}). Your local data may still exist.`);
      }
    } catch (error) {
      alert(`Transfer failed: ${String(error)}`);
    } finally {
      setIsTransferring(false);
      setTransferStep('idle');
    }
  };

  // Reset transfer modal state
  const resetTransferState = () => {
    setSelectedQubeForSync(null);
    setTransferRecipientAddress('');
    setTransferRecipientPublicKey('');
    setTransferConfirmed(false);
    setTransferStep('idle');
    setTransferPendingId(null);
  };

  // Scan wallet for Qubes (uses WalletConnect address)
  const handleScanWallet = async () => {
    if (!wallet.address || !userId) return;

    setIsScanning(true);
    setWalletQubes([]);

    try {
      const result = await invoke<{
        success: boolean;
        qubes?: Array<{
          qube_id: string;
          qube_name: string;
          category_id: string;
          ipfs_cid: string;
          chain_length: number;
          sync_timestamp: number;
          already_imported?: boolean;
        }>;
        error?: string;
      }>('scan_wallet', {
        userId,
        walletAddress: wallet.address,
      });

      if (result.success && result.qubes) {
        setWalletQubes(result.qubes);
        if (result.qubes.length === 0) {
          alert('No Qubes found for this wallet address.');
        }
      } else {
        alert(`Scan failed: ${result.error}`);
      }
    } catch (error) {
      alert(`Scan failed: ${error}`);
    } finally {
      setIsScanning(false);
    }
  };

  // Bulk import all Qubes from wallet (no WIF — password-based decryption)
  const handleBulkImport = async () => {
    const importable = walletQubes.filter(q => !q.already_imported);
    if (!importable.length || !userId || !masterPassword) return;

    setIsImporting(true);
    setImportProgress(null);
    let imported = 0;
    let failed = 0;
    const errors: string[] = [];

    try {
      for (let i = 0; i < importable.length; i++) {
        const q = importable[i];
        setImportProgress({ current: i + 1, total: importable.length, name: q.qube_name });

        try {
          const result = await invoke<{
            success: boolean;
            qube_id?: string;
            qube_name?: string;
            qube_dir?: string;
            error?: string;
          }>('import_from_wallet', {
            userId,
            walletWif: '',  // Empty = password-based decryption
            categoryId: q.category_id,
            password: masterPassword,
          });

          if (result.success) {
            imported++;
            window.dispatchEvent(new CustomEvent('qube-created', { detail: { qube_id: result.qube_id } }));
          } else {
            failed++;
            errors.push(`${q.qube_name}: ${result.error}`);
          }
        } catch (err) {
          failed++;
          errors.push(`${q.qube_name}: ${err}`);
        }
      }

      const summary = [`Imported ${imported} of ${importable.length} Qube(s).`];
      if (failed > 0) summary.push(`\n${failed} failed:\n${errors.join('\n')}`);
      alert(summary.join(''));

      if (imported > 0) {
        setShowImportFromWalletModal(false);
        resetImportState();
      }
    } catch (error) {
      alert(`Import failed: ${error}`);
    } finally {
      setIsImporting(false);
      setImportProgress(null);
    }
  };

  // Reset import modal state
  const resetImportState = () => {
    setWalletQubes([]);
    setImportProgress(null);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {/* Controls */}
      <div className="flex items-center gap-4 mb-6">
        {/* View Toggle */}
        <div className="flex gap-2">
          <button
            onClick={() => setViewMode('grid')}
            className={`px-4 py-2 rounded-lg font-medium text-sm transition-all ${
              viewMode === 'grid'
                ? 'bg-accent-primary/10 text-accent-primary border border-accent-primary/30'
                : 'text-text-secondary hover:text-text-primary hover:bg-bg-tertiary'
            }`}
          >
            🔲 Grid
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`px-4 py-2 rounded-lg font-medium text-sm transition-all ${
              viewMode === 'list'
                ? 'bg-accent-primary/10 text-accent-primary border border-accent-primary/30'
                : 'text-text-secondary hover:text-text-primary hover:bg-bg-tertiary'
            }`}
          >
            ☰ List
          </button>
        </div>

        {/* Search */}
        <input
          type="text"
          placeholder="Search qubes..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="flex-1 max-w-md px-4 py-2 bg-glass-bg backdrop-blur-glass border border-glass-border rounded-lg text-text-primary placeholder-text-tertiary focus:outline-none focus:ring-2 focus:ring-accent-primary/50"
        />

        {/* Stats */}
        <div className="text-text-tertiary text-sm">
          {filteredQubes.length} of {qubes.length} qubes
        </div>

        {/* Action Buttons */}
        <div className="ml-auto flex gap-2">
          <GlassButton
            variant="secondary"
            onClick={handleImportFromWalletClick}
            disabled={isImporting}
            title="Scan your connected wallet for Qube NFTs"
          >
            {isImporting ? 'Scanning...' : 'Scan Wallet'}
          </GlassButton>
          <GlassButton
            variant="secondary"
            onClick={handleTransferClick}
            disabled={isTransferring || selectedQubeIds.length === 0}
            title={
              pinataConfigured === false
                ? '⚠️ Pinata API key required - Configure in Settings → API Keys'
                : selectedQubeIds.length === 0
                ? 'Select a Qube to transfer'
                : 'Transfer selected Qube to new owner'
            }
          >
            {isTransferring ? 'Transferring...' : 'Transfer'}
          </GlassButton>
          <GlassButton
            variant="secondary"
            onClick={() => setShowIpfsBackupAllModal(true)}
            title={pinataConfigured === false ? '⚠️ Pinata API key required — add in Settings → API Keys' : `Backup all Qubes to IPFS${lastIpfsSyncTime ? ` · Last: ${lastIpfsSyncTime.toLocaleTimeString()}` : ''}`}
            disabled={isIpfsBackingUpAll || pinataConfigured === false}
          >
            {isIpfsBackingUpAll ? '☁ Syncing...' : '☁ IPFS Backup'}
          </GlassButton>
          <GlassButton
            variant="secondary"
            onClick={() => setShowBackupModal(true)}
            title="Backup your entire account and all Qubes to an encrypted file"
          >
            Backup
          </GlassButton>
          <GlassButton
            variant="secondary"
            onClick={() => setShowRestoreModal(true)}
            title="Import Qubes from a backup file — existing Qubes won't be affected"
          >
            Restore
          </GlassButton>
          {/* Total Balance Widget */}
          <div className="flex items-stretch border border-glass-border rounded-lg overflow-hidden bg-glass-bg backdrop-blur-glass">
            <div className="px-3 py-1 flex flex-col justify-center min-w-[120px]">
              <span className="text-[9px] text-text-tertiary uppercase tracking-wider">Total Balance</span>
              <span className={`font-mono text-xs font-bold ${hasAnyBalance ? 'text-accent-primary' : 'text-text-disabled'}`}>
                {isRefreshingAll ? '...' : `${(totalSats / 1e8).toFixed(8)} BCH`}
              </span>
            </div>
            <button
              onClick={handleRefreshAllBalances}
              disabled={isRefreshingAll}
              title="Refresh all balances"
              className="px-2 border-l border-glass-border text-text-tertiary hover:text-accent-primary transition-colors disabled:opacity-40"
            >
              {isRefreshingAll ? '⟳' : '↻'}
            </button>
            {!showSweepAllModal ? (
              <button
                onClick={() => { if (hasAnyBalance) setShowSweepAllModal(true); }}
                disabled={!hasAnyBalance || isSweepingAll}
                title={hasAnyBalance ? 'Sweep all balances to one address' : 'No balance to sweep'}
                className={`px-2 border-l border-glass-border text-xs font-medium transition-colors ${
                  hasAnyBalance
                    ? 'text-accent-primary hover:bg-accent-primary/10 cursor-pointer'
                    : 'text-text-disabled cursor-not-allowed'
                }`}
              >
                ↓ Sweep
              </button>
            ) : (
              <div className="flex items-center gap-1 px-2 border-l border-glass-border">
                <input
                  type="text"
                  value={sweepAllAddress}
                  onChange={(e) => setSweepAllAddress(e.target.value)}
                  placeholder="bitcoincash:q..."
                  className="bg-transparent text-[10px] text-text-primary placeholder:text-text-disabled font-mono focus:outline-none w-40"
                  autoFocus
                />
                <button
                  onClick={handleSweepAll}
                  disabled={!sweepAllAddress || isSweepingAll}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-accent-primary/20 text-accent-primary disabled:opacity-40"
                >
                  {isSweepingAll ? '...' : '✓'}
                </button>
                <button
                  onClick={() => { setShowSweepAllModal(false); setSweepAllAddress(''); setSweepAllResult(null); }}
                  className="text-[10px] text-text-tertiary hover:text-text-primary"
                >✕</button>
              </div>
            )}
          </div>
          {sweepAllResult && (
            <div className={`text-xs px-2 py-1 rounded ${sweepAllResult.startsWith('✓') ? 'text-green-400 bg-green-400/10' : 'text-red-400 bg-red-400/10'}`}>
              {sweepAllResult}
            </div>
          )}

          <GlassButton variant="primary" onClick={onCreateQube}>
            + New Qube
          </GlassButton>
        </div>
      </div>

      {/* Pinata Configuration Warning */}
      {pinataConfigured === false && (
        <div className="mb-4 p-4 bg-yellow-500/20 border border-yellow-500/50 rounded-lg flex items-center gap-3">
          <span className="text-2xl">⚠️</span>
          <div className="flex-1">
            <p className="text-text-primary font-medium">Pinata API Key Required</p>
            <p className="text-text-secondary text-sm">
              To sync or transfer Qubes to IPFS, add your Pinata JWT in Settings → API Keys.{' '}
              <a
                href="https://app.pinata.cloud/developers/api-keys"
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent-primary hover:underline"
              >
                Get a free API key →
              </a>
            </p>
          </div>
        </div>
      )}

      {/* Empty State */}
      {qubes.length === 0 ? (
        <GlassCard className="p-12 text-center">
          <div className="text-6xl mb-4">🤖</div>
          <h2 className="text-2xl font-display text-text-primary mb-2">
            No Qubes Yet
          </h2>
          <p className="text-text-secondary mb-6">
            Create your first Qube to get started with AI conversations
          </p>
          <GlassButton variant="primary" onClick={onCreateQube}>
            + Create Your First Qube
          </GlassButton>
        </GlassCard>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={filteredQubes.map(q => q.qube_id)}
            strategy={rectSortingStrategy}
          >
            {/* Grid View */}
            {viewMode === 'grid' && (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                {filteredQubes.map((qube, index) => (
                  <SortableQubeCard
                    key={qube.qube_id}
                    qube={qube}
                    allQubes={qubes}
                    onEdit={() => onEditQube(qube)}
                    onDelete={() => onDeleteQube(qube)}
                    onReset={() => setQubeToReset(qube)}
                    onSelect={() => handleSelectQube(qube)}
                    onUpdateConfig={onUpdateQubeConfig}
                    getAvatarPath={getAvatarPath}
                    setCurrentTab={setCurrentTab}
                    toggleSelection={toggleSelection}
                    isSelected={selectedQubeIds.includes(qube.qube_id)}
                  />
                ))}
              </div>
            )}

            {/* List View */}
            {viewMode === 'list' && (
              <div className="space-y-3">
                {filteredQubes.map((qube) => (
                  <SortableQubeListItem
                    key={qube.qube_id}
                    qube={qube}
                    allQubes={qubes}
                    onEdit={() => onEditQube(qube)}
                    onDelete={() => onDeleteQube(qube)}
                    onReset={() => setQubeToReset(qube)}
                    onSelect={() => handleSelectQube(qube)}
                    onUpdateConfig={onUpdateQubeConfig}
                    getAvatarPath={getAvatarPath}
                    setCurrentTab={setCurrentTab}
                    toggleSelection={toggleSelection}
                    isSelected={selectedQubeIds.includes(qube.qube_id)}
                  />
                ))}
              </div>
            )}

            {/* No Results */}
            {filteredQubes.length === 0 && (
              <div className="text-center py-12">
                <p className="text-text-tertiary">
                  No qubes match "{searchQuery}"
                </p>
              </div>
            )}
          </SortableContext>
        </DndContext>
      )}

      {/* Reset Confirmation Modal */}
      {qubeToReset && (
        <div className="fixed inset-0 bg-black/95 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="glass-card p-6 max-w-md w-full mx-4">
            <h2 className="text-xl font-bold text-text-primary mb-2">Reset {qubeToReset.name}?</h2>
            <p className="text-accent-warning text-sm mb-4 font-semibold">
              This will reset the qube to a fresh state (like a new save slot).
            </p>
            <p className="text-text-secondary text-sm mb-4">
              The following will be DELETED:
            </p>
            <ul className="text-text-secondary text-sm mb-4 list-disc list-inside space-y-1">
              <li>All conversation blocks (except genesis)</li>
              <li>All relationships and evaluations</li>
              <li>All skill progress and history</li>
              <li>Semantic search index</li>
              <li>Snapshots and audio cache</li>
            </ul>
            <p className="text-text-secondary text-sm mb-6">
              The genesis block, NFT info, and cryptographic identity will be preserved.
            </p>

            <div className="flex gap-3">
              <button
                onClick={() => setQubeToReset(null)}
                disabled={isResetting}
                className="flex-1 px-4 py-2 bg-surface-secondary text-text-secondary rounded-lg hover:bg-surface-tertiary transition-all"
              >
                Cancel
              </button>
              <button
                onClick={handleResetQube}
                disabled={isResetting}
                className="flex-1 px-4 py-2 bg-accent-warning/20 text-accent-warning rounded-lg hover:bg-accent-warning/30 transition-all font-medium"
              >
                {isResetting ? 'Resetting...' : 'Reset Qube'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Transfer Modal */}
      {showTransferModal && selectedQubeForSync && (
        <div className="fixed inset-0 bg-black/95 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="glass-card p-6 max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-bold text-text-primary mb-2">Transfer {selectedQubeForSync.name}</h2>
            <p className="text-red-400 text-sm mb-4 font-semibold">
              WARNING: This action is IRREVERSIBLE. Your local copy will be permanently deleted.
            </p>

            {/* Recipient Address */}
            <div className="mb-4">
              <label className="block text-text-secondary text-sm mb-1">Recipient BCH Address</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={transferRecipientAddress}
                  onChange={(e) => setTransferRecipientAddress(e.target.value)}
                  placeholder="bitcoincash:qz..."
                  className="flex-1 bg-surface-secondary border border-border-primary rounded-lg px-3 py-2 text-text-primary text-sm"
                />
                <GlassButton
                  variant="secondary"
                  onClick={handleResolvePublicKey}
                  disabled={!transferRecipientAddress || isResolvingPublicKey}
                  title="Try to find public key from blockchain"
                >
                  {isResolvingPublicKey ? '...' : 'Lookup'}
                </GlassButton>
              </div>
            </div>

            {/* Recipient Public Key */}
            <div className="mb-4">
              <label className="block text-text-secondary text-sm mb-1">
                Recipient Public Key <span className="text-text-tertiary">(66 hex chars)</span>
              </label>
              <input
                type="text"
                value={transferRecipientPublicKey}
                onChange={(e) => setTransferRecipientPublicKey(e.target.value)}
                placeholder="02 or 03 followed by 64 hex characters"
                className="w-full bg-surface-secondary border border-border-primary rounded-lg px-3 py-2 text-text-primary text-sm font-mono"
              />
              <p className="text-text-tertiary text-xs mt-1">
                Click "Lookup" to auto-resolve, or ask recipient for their public key
              </p>
            </div>

            {/* WalletConnect — signs the NFT transfer */}
            <div className="mb-4">
              <label className="block text-text-secondary text-sm mb-2">Sign with Wallet</label>
              <div className="p-3 bg-glass-bg border border-glass-border rounded-lg">
                <WalletConnectButton />
                {wallet.connected && wallet.address && (
                  <p className="text-accent-primary text-xs mt-2">
                    Connected: {wallet.address.slice(0, 28)}...
                  </p>
                )}
                {!wallet.connected && (
                  <p className="text-text-tertiary text-xs mt-2">
                    Connect your BCH wallet — it will sign the NFT transfer. No private key needed.
                  </p>
                )}
              </div>
            </div>

            {/* Transfer step progress */}
            {isTransferring && (
              <div className="mb-4 p-3 bg-accent-primary/10 border border-accent-primary/30 rounded-lg">
                <p className="text-accent-primary text-sm">
                  {transferStep === 'preparing' && '1/3 Syncing to IPFS and re-encrypting for recipient...'}
                  {transferStep === 'signing' && '2/3 Waiting for wallet signature...'}
                  {transferStep === 'finalizing' && '3/3 Finalizing transfer and cleaning up...'}
                </p>
              </div>
            )}

            {/* Confirmation Checkbox */}
            <div className="mb-6 p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={transferConfirmed}
                  onChange={(e) => setTransferConfirmed(e.target.checked)}
                  className="mt-1"
                />
                <span className="text-text-secondary text-sm">
                  I understand that transferring <strong>{selectedQubeForSync.name}</strong> will permanently delete
                  my local copy. The only way to recover this Qube will be to import it from the recipient's wallet.
                </span>
              </label>
            </div>

            {/* Actions */}
            <div className="flex gap-3 justify-end">
              <GlassButton
                variant="secondary"
                onClick={() => {
                  setShowTransferModal(false);
                  resetTransferState();
                }}
                disabled={isTransferring}
              >
                Cancel
              </GlassButton>
              <GlassButton
                variant="primary"
                onClick={handleExecuteTransfer}
                disabled={isTransferring || !transferConfirmed || !transferRecipientAddress || !transferRecipientPublicKey || !wallet.connected}
                className="bg-red-600 hover:bg-red-700"
              >
                {transferStep === 'preparing' ? 'Preparing...' : transferStep === 'signing' ? 'Sign in Wallet...' : transferStep === 'finalizing' ? 'Finalizing...' : 'Transfer Qube'}
              </GlassButton>
            </div>
          </div>
        </div>
      )}

      {/* Import from Wallet Modal */}
      {showImportFromWalletModal && (
        <div className="fixed inset-0 bg-black/95 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="glass-card p-6 max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-bold text-text-primary mb-4">Scan Wallet</h2>
            <p className="text-text-secondary text-sm mb-4">
              Connect your wallet to scan for Qubes and import them all at once.
              Your master password decrypts the data — no private key needed.
            </p>

            {/* Step 1: Connect Wallet */}
            <div className="mb-4 p-4 bg-glass-bg border border-glass-border rounded-lg">
              <label className="block text-text-secondary text-sm mb-2">Connect Wallet</label>
              <WalletConnectButton />
              {wallet.connected && wallet.address && !isScanning && walletQubes.length === 0 && !importProgress && (
                <GlassButton
                  variant="secondary"
                  className="mt-3"
                  onClick={handleScanWallet}
                >
                  Scan for Qubes
                </GlassButton>
              )}
              {isScanning && (
                <p className="text-text-tertiary text-sm mt-2 animate-pulse">Scanning wallet for Qubes...</p>
              )}
            </div>

            {/* Step 2: Found Qubes */}
            {walletQubes.length > 0 && (
              <div className="mb-4">
                <label className="block text-text-secondary text-sm mb-2">
                  Found {walletQubes.length} Qube(s) in wallet
                  {walletQubes.every(q => q.already_imported) && (
                    <span className="text-text-tertiary"> — all already imported</span>
                  )}
                </label>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {walletQubes.map((q) => (
                    <div
                      key={q.qube_id}
                      className={`p-3 rounded-lg border ${
                        q.already_imported
                          ? 'border-border-primary/50 bg-surface-secondary/50 opacity-60'
                          : 'border-border-primary bg-surface-secondary'
                      }`}
                    >
                      <div className="flex justify-between items-start">
                        <div>
                          <p className="text-text-primary font-medium">
                            {q.qube_name}
                            {q.already_imported && (
                              <span className="text-text-tertiary text-xs ml-2">(already imported)</span>
                            )}
                          </p>
                          <p className="text-text-tertiary text-xs">{q.qube_id}</p>
                        </div>
                        <div className="text-right text-xs text-text-tertiary">
                          <p>{q.chain_length || 1} block{(q.chain_length || 1) !== 1 ? 's' : ''}</p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Import progress */}
            {importProgress && (
              <div className="mb-4 p-3 bg-accent-primary/10 border border-accent-primary/30 rounded-lg">
                <p className="text-accent-primary text-sm">
                  Importing {importProgress.current} of {importProgress.total}: {importProgress.name}...
                </p>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 justify-end">
              <GlassButton
                variant="secondary"
                onClick={() => {
                  setShowImportFromWalletModal(false);
                  resetImportState();
                }}
                disabled={isImporting}
              >
                Cancel
              </GlassButton>
              {walletQubes.length > 0 && (
                <GlassButton
                  variant="primary"
                  onClick={handleBulkImport}
                  disabled={isImporting || walletQubes.filter(q => !q.already_imported).length === 0}
                  loading={isImporting}
                >
                  {isImporting
                    ? 'Importing...'
                    : walletQubes.filter(q => !q.already_imported).length > 0
                      ? `Import (${walletQubes.filter(q => !q.already_imported).length})`
                      : 'All Imported'
                  }
                </GlassButton>
              )}
            </div>
          </div>
        </div>
      )}

      {/* IPFS Backup All Modal */}
      {showIpfsBackupAllModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50">
          <GlassCard variant="elevated" className="w-full max-w-md p-6 mx-4">
            <h2 className="text-xl font-bold text-text-primary mb-2">☁ IPFS Backup</h2>
            <p className="text-text-secondary text-sm mb-2">
              Upload all Qubes + account credentials to IPFS via Pinata. Each Qube gets its own folder. One CID to restore everything.
            </p>
            <p className="text-text-tertiary text-xs mb-6">
              Encrypted with your master password. Also runs automatically every 30 minutes.
            </p>
            {isIpfsBackingUpAll && (
              <div className="mb-4 p-3 bg-accent-primary/10 border border-accent-primary/30 rounded-lg">
                <p className="text-accent-primary text-sm">Uploading to IPFS...</p>
              </div>
            )}
            {lastIpfsSyncTime && (
              <p className="text-text-tertiary text-xs mb-4">Last sync: {lastIpfsSyncTime.toLocaleString()}</p>
            )}
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowIpfsBackupAllModal(false)}
                className="px-4 py-2 rounded-lg border border-glass-border text-text-secondary hover:text-text-primary transition-colors text-sm"
                disabled={isIpfsBackingUpAll}
              >
                Cancel
              </button>
              <button
                onClick={() => handleIpfsBackupAll(false)}
                disabled={isIpfsBackingUpAll}
                className="px-4 py-2 rounded-lg bg-accent-primary/20 border border-accent-primary/50 text-accent-primary hover:bg-accent-primary/30 transition-colors text-sm font-medium disabled:opacity-50"
              >
                {isIpfsBackingUpAll ? 'Uploading...' : 'Backup Now'}
              </button>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Account Backup Modal */}
      {showBackupModal && (
        <div className="fixed inset-0 bg-black/95 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="glass-card p-6 max-w-md w-full mx-4">
            <h2 className="text-xl font-bold text-text-primary mb-4">Backup</h2>
            <p className="text-text-secondary text-sm mb-4">
              Export your entire account (credentials + all Qubes) to a portable <code>.qube-backup</code> file.
              Encrypted with your master password.
            </p>
            <div className="flex gap-3 mt-4">
              <GlassButton
                variant="secondary"
                onClick={() => setShowBackupModal(false)}
                disabled={isBackingUp}
              >
                Cancel
              </GlassButton>
              <GlassButton
                variant="primary"
                onClick={handleBackupAccount}
                disabled={isBackingUp}
                loading={isBackingUp}
              >
                {isBackingUp ? 'Backing up...' : 'Create Backup'}
              </GlassButton>
            </div>
          </div>
        </div>
      )}

      {/* Restore (Merge) Modal */}
      {showRestoreModal && (
        <div className="fixed inset-0 bg-black/95 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="glass-card p-6 max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xl font-bold text-text-primary">Restore from Backup</h2>
              <button
                onClick={() => { resetRestoreState(); setShowRestoreModal(false); }}
                className="text-text-tertiary hover:text-text-primary transition-colors text-lg leading-none"
              >
                ✕
              </button>
            </div>

            {/* Mode toggle tabs */}
            <div className="flex gap-1 mb-4 p-1 bg-bg-secondary rounded-lg">
              <button
                onClick={() => setRestoreMode('local')}
                className={`flex-1 py-1.5 px-3 rounded-md text-sm font-medium transition-all ${
                  restoreMode === 'local'
                    ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/30'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                Local File
              </button>
              <button
                onClick={() => setRestoreMode('ipfs')}
                className={`flex-1 py-1.5 px-3 rounded-md text-sm font-medium transition-all ${
                  restoreMode === 'ipfs'
                    ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/30'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                IPFS
              </button>
            </div>

            <div className="space-y-3">
              {restoreMode === 'local' ? (
                <>
                  <p className="text-text-secondary text-sm">
                    Import Qubes from a <code>.qube-backup</code> file into your account.
                    Qubes that already exist will be skipped.
                  </p>
                  <div>
                    <label className="block text-sm text-text-secondary mb-1">Backup File</label>
                    <button
                      onClick={async () => {
                        const path = await openDialog({
                          multiple: false,
                          filters: [{ name: 'Qube Backup', extensions: ['qube-backup'] }],
                        });
                        if (path && typeof path === 'string') setRestoreFilePath(path);
                      }}
                      className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm text-left hover:border-accent-primary/50 transition-colors truncate"
                    >
                      {restoreFilePath ? restoreFilePath.split(/[/\\]/).pop() : 'Choose .qube-backup file...'}
                    </button>
                  </div>
                  <div>
                    <label className="block text-sm text-text-secondary mb-1">Master Password</label>
                    <input
                      type="password"
                      value={restorePassword}
                      onChange={(e) => setRestorePassword(e.target.value)}
                      placeholder="Master password used to encrypt the backup"
                      className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm"
                    />
                  </div>
                  {/* WC 2FA */}
                  <div className="p-3 border border-glass-border rounded-lg bg-glass-bg/20">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-xs text-text-tertiary uppercase tracking-wide">Optional: Wallet 2FA</span>
                      {restoreWalletSig && (
                        <span className="text-xs text-accent-success">✓ {restoreWcAddress.slice(0, 16)}...</span>
                      )}
                    </div>
                    {!restoreWalletSig ? (
                      <>
                        <p className="text-[10px] text-text-tertiary mb-2">Required only if your backup was created with wallet authentication.</p>
                        {wallet.connected ? (
                          <GlassButton variant="secondary" onClick={handleSignRestoreWc} disabled={isSigningRestoreWc} className="w-full text-sm">
                            {isSigningRestoreWc ? 'Signing...' : '🔗 Sign with Connected Wallet'}
                          </GlassButton>
                        ) : (
                          <p className="text-[10px] text-text-tertiary">Connect your wallet first using the WalletConnect button.</p>
                        )}
                      </>
                    ) : (
                      <GlassButton variant="secondary" onClick={() => { setRestoreWalletSig(''); setRestoreWcAddress(''); }} className="text-xs">
                        Clear Signature
                      </GlassButton>
                    )}
                  </div>
                  <div className="flex gap-3 pt-1">
                    <GlassButton
                      variant="secondary"
                      onClick={() => { resetRestoreState(); setShowRestoreModal(false); }}
                      disabled={isRestoring}
                    >
                      Cancel
                    </GlassButton>
                    <GlassButton
                      variant="primary"
                      onClick={handleRestoreFromBackup}
                      disabled={isRestoring || !restoreFilePath || !restorePassword}
                      loading={isRestoring}
                    >
                      {isRestoring ? 'Restoring...' : 'Restore'}
                    </GlassButton>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-text-secondary text-sm">
                    Restore Qubes from an IPFS backup using your Pinata account or a direct CID.
                  </p>
                  {/* IPFS sub-mode toggle */}
                  <div className="flex gap-1 p-1 bg-bg-secondary rounded-lg">
                    <button
                      onClick={() => setIpfsInputMode('jwt')}
                      className={`flex-1 py-1 px-2 rounded-md text-xs font-medium transition-all ${
                        ipfsInputMode === 'jwt'
                          ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/30'
                          : 'text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      Pinata Account
                    </button>
                    <button
                      onClick={() => setIpfsInputMode('cid')}
                      className={`flex-1 py-1 px-2 rounded-md text-xs font-medium transition-all ${
                        ipfsInputMode === 'cid'
                          ? 'bg-accent-primary/20 text-accent-primary border border-accent-primary/30'
                          : 'text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      Direct CID
                    </button>
                  </div>

                  {ipfsInputMode === 'jwt' ? (
                    <div className="space-y-2">
                      <div>
                        <label className="block text-sm text-text-secondary mb-1">Pinata JWT</label>
                        <div className="flex gap-2">
                          <input
                            type="password"
                            value={ipfsPinataJwt}
                            onChange={(e) => setIpfsPinataJwt(e.target.value)}
                            placeholder="eyJ..."
                            className="flex-1 px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm"
                          />
                          <GlassButton
                            variant="secondary"
                            onClick={handleListBackupsForRestore}
                            disabled={isListingBackups || !ipfsPinataJwt.trim()}
                            loading={isListingBackups}
                          >
                            {isListingBackups ? 'Listing...' : 'List Backups'}
                          </GlassButton>
                        </div>
                      </div>
                      {ipfsListError && (
                        <p className="text-xs text-accent-danger">{ipfsListError}</p>
                      )}
                      {ipfsBackupList.length > 0 && (
                        <div className="space-y-1 max-h-36 overflow-y-auto">
                          {ipfsBackupList.map((backup) => (
                            <label key={backup.cid} className="flex items-start gap-2 p-2 rounded-lg border border-glass-border bg-glass-bg/20 cursor-pointer hover:border-accent-primary/40 transition-colors">
                              <input
                                type="radio"
                                name="ipfs-backup"
                                value={backup.cid}
                                checked={ipfsSelectedCid === backup.cid}
                                onChange={() => setIpfsSelectedCid(backup.cid)}
                                className="mt-0.5"
                              />
                              <div className="min-w-0 flex-1">
                                <p className="text-sm text-text-primary truncate">{backup.name}</p>
                                <p className="text-[10px] text-text-tertiary">{backup.date} · {(backup.size_bytes / 1024).toFixed(1)} KB</p>
                              </div>
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div>
                      <label className="block text-sm text-text-secondary mb-1">IPFS CID</label>
                      <input
                        type="text"
                        value={ipfsDirectCid}
                        onChange={(e) => setIpfsDirectCid(e.target.value)}
                        placeholder="Qm... or bafyb..."
                        className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm font-mono"
                      />
                    </div>
                  )}

                  <div>
                    <label className="block text-sm text-text-secondary mb-1">Master Password</label>
                    <input
                      type="password"
                      value={restorePassword}
                      onChange={(e) => setRestorePassword(e.target.value)}
                      placeholder="Master password used to encrypt the backup"
                      className="w-full px-3 py-2 bg-glass-bg border border-glass-border rounded-lg text-text-primary text-sm"
                    />
                  </div>

                  {/* WC 2FA */}
                  <div className="p-3 border border-glass-border rounded-lg bg-glass-bg/20">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-xs text-text-tertiary uppercase tracking-wide">Optional: Wallet 2FA</span>
                      {restoreWalletSig && (
                        <span className="text-xs text-accent-success">✓ {restoreWcAddress.slice(0, 16)}...</span>
                      )}
                    </div>
                    {!restoreWalletSig ? (
                      <>
                        <p className="text-[10px] text-text-tertiary mb-2">Required only if your backup was created with wallet authentication.</p>
                        {wallet.connected ? (
                          <GlassButton variant="secondary" onClick={handleSignRestoreWc} disabled={isSigningRestoreWc} className="w-full text-sm">
                            {isSigningRestoreWc ? 'Signing...' : '🔗 Sign with Connected Wallet'}
                          </GlassButton>
                        ) : (
                          <p className="text-[10px] text-text-tertiary">Connect your wallet first using the WalletConnect button.</p>
                        )}
                      </>
                    ) : (
                      <GlassButton variant="secondary" onClick={() => { setRestoreWalletSig(''); setRestoreWcAddress(''); }} className="text-xs">
                        Clear Signature
                      </GlassButton>
                    )}
                  </div>

                  <div className="flex gap-3 pt-1">
                    <GlassButton
                      variant="secondary"
                      onClick={() => { resetRestoreState(); setShowRestoreModal(false); }}
                      disabled={isRestoring}
                    >
                      Cancel
                    </GlassButton>
                    <GlassButton
                      variant="primary"
                      onClick={handleIpfsRestore}
                      disabled={isRestoring || !restorePassword || (ipfsInputMode === 'jwt' ? !ipfsSelectedCid : !ipfsDirectCid.trim())}
                      loading={isRestoring}
                    >
                      {isRestoring ? 'Restoring...' : 'Restore from IPFS'}
                    </GlassButton>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// Qube Card Component (Grid View)
interface QubeCardProps {
  qube: Qube;
  allQubes: Qube[];  // For wallet security whitelist selection
  onEdit: () => void;
  onDelete: () => void;
  onReset?: () => void;  // Reset qube to fresh state (new save slot)
  onSelect: () => void;
  onUpdateConfig: (qubeId: string, updates: { ai_model?: string; voice_model?: string; favorite_color?: string; tts_enabled?: boolean; evaluation_model?: string }) => Promise<void>;
  getAvatarPath: (qube: Qube) => string;
  dragHandleProps?: React.HTMLAttributes<HTMLDivElement>;
  setCurrentTab: (tab: Tab) => void;
  toggleSelection: (qubeId: string, isCtrl: boolean, isShift: boolean) => void;
  isSelected?: boolean;
}

// Helper component for truncated, clickable blockchain fields
interface BlockchainLinkProps {
  value: string;
  type: 'transaction' | 'address' | 'hash' | 'ipfs' | 'other';
  network?: string;
}

const BlockchainLink: React.FC<BlockchainLinkProps> = ({ value, type, network = 'mainnet' }) => {
  const truncate = (str: string, startChars: number = 10, endChars: number = 10) => {
    if (str.length <= startChars + endChars + 3) return str;
    return `${str.substring(0, startChars)}...${str.substring(str.length - endChars)}`;
  };

  const getUrl = () => {
    const chain = network === 'mainnet' ? 'bitcoin-cash' : 'bitcoin-cash/testnet';

    switch (type) {
      case 'transaction':
        return `https://blockchair.com/${chain}/transaction/${value}`;
      case 'address':
        // Remove bitcoincash: prefix if present
        const cleanAddress = value.replace('bitcoincash:', '');
        return `https://blockchair.com/${chain}/address/${cleanAddress}`;
      case 'hash':
        // For hashes, we can't directly link to blockchair, but we can try
        return `https://blockchair.com/${chain}/transaction/${value}`;
      case 'ipfs':
        // For IPFS URIs, link to ipfs.io gateway
        const cid = value.replace('ipfs://', '');
        return `https://ipfs.io/ipfs/${cid}`;
      default:
        return null;
    }
  };

  const handleClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const url = getUrl();
    if (url) {
      try {
        await openShell(url);
      } catch (error) {
        console.error('Failed to open URL:', error);
      }
    }
  };

  return (
    <span
      onClick={handleClick}
      className="text-text-primary font-mono bg-bg-tertiary/30 rounded px-1.5 py-0.5 leading-tight cursor-pointer hover:bg-accent-primary/20 hover:text-accent-primary transition-all inline-block"
      title={`Click to view: ${value}`}
    >
      {truncate(value)}
    </span>
  );
};

const QubeCard: React.FC<QubeCardProps> = ({ qube, allQubes, onEdit, onDelete, onReset, onSelect, onUpdateConfig, getAvatarPath, dragHandleProps, setCurrentTab, toggleSelection, isSelected = false }) => {
  const { userId, password: masterPassword } = useAuth();
  const { getWalletData, setBalance: setCachedBalance } = useWalletCache();
  const { invalidateCache, loadChainState } = useChainState();
  const { voiceLibrary: customVoices } = useVoiceLibrary();

  // Fetch models from backend (all models from ModelRegistry)
  const {
    providers: dynamicProviders,
    models: dynamicModels,
    defaults: dynamicDefaults,
    fetchModels,
    isLoaded: modelsLoaded,
    getModelsForProvider: getModelsFromHook,
    getDefaultModel: getDefaultFromHook,
  } = useModels();

  // Fetch models on mount
  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  // Custom voices are now managed by VoiceLibraryContext - no need for local loading

  // Per-Qube IPFS backup state
  const [showQubeIpfsModal, setShowQubeIpfsModal] = useState(false);
  const [isQubeIpfsSyncing, setIsQubeIpfsSyncing] = useState(false);

  // Wallet Security state
  const [walletSecurityModalOpen, setWalletSecurityModalOpen] = useState(false);
  const [walletSecurity, setWalletSecurity] = useState<{
    addresses_with_keys: string[];
    whitelists: Record<string, string[]>;
  }>({
    addresses_with_keys: [],
    whitelists: {},
  });

  // Check if this qube's NFT address (z) has a stored key
  const hasStoredKey = qube.recipient_address
    ? walletSecurity.addresses_with_keys.includes(qube.recipient_address)
    : false;

  // Load wallet security when blockchain side is shown
  const loadWalletSecurity = async () => {
    if (!userId || !masterPassword) return;
    try {
      const result = await invoke<{
        success: boolean;
        addresses_with_keys: string[];
        whitelists: Record<string, string[]>;
      }>('get_wallet_security', { userId, password: masterPassword });
      if (result.success) {
        setWalletSecurity({
          addresses_with_keys: result.addresses_with_keys || [],
          whitelists: result.whitelists || {},
        });
      }
    } catch (e) {
      console.error('Failed to load wallet security:', e);
    }
  };

  // Refresh wallet security after modal saves
  const handleWalletSecuritySave = () => {
    loadWalletSecurity();
  };

  const handleQubeIpfsBackup = async () => {
    if (!userId || !masterPassword) return;
    setIsQubeIpfsSyncing(true);

    let walletSigForBackup = '';
    try {
      const { getSession: wcGetSession, signMessage: wcSignMessage } = await import('../../services/walletConnect');
      const session = wcGetSession();
      if (session) walletSigForBackup = await wcSignMessage('qubes-backup-key:v1', 'Qubes is syncing your Qube to IPFS');
    } catch (e) {
      console.warn('[IPFS Backup] No wallet sig:', e);
    }

    try {
      const result = await invoke<{ success: boolean; ipfs_cid?: string; error?: string }>('sync_qube_to_ipfs_backup', {
        userId,
        qubeId: qube.qube_id,
        exportPassword: masterPassword,
        masterPassword,
        walletSig: walletSigForBackup,
      });
      if (result.success) {
        alert(`${qube.name} backed up to IPFS!\n\nCID: ${result.ipfs_cid}`);
        setShowQubeIpfsModal(false);
      } else {
        alert(`IPFS backup failed: ${result.error}`);
      }
    } catch (err) {
      alert(`IPFS backup failed: ${err}`);
    } finally {
      setIsQubeIpfsSyncing(false);
    }
  };

  // Get cached wallet data for this qube
  const cachedWalletData = getWalletData(qube.qube_id);

  // Static fallback data while useModels hook is being debugged
  const providers = [
    { value: 'openai', label: 'OpenAI' },
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'google', label: 'Google' },
    { value: 'perplexity', label: 'Perplexity' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'venice', label: 'Venice (Private)' },
    { value: 'nanogpt', label: 'NanoGPT (Pay-per-prompt)' },
    { value: 'ollama', label: 'Ollama (Local)' },
  ];

  // Static fallback models by provider (matches backend ModelRegistry)
  const fallbackModels: Record<string, { value: string; label: string }[]> = {
    openai: [
      { value: 'gpt-5.2', label: 'GPT-5.2' },
      { value: 'gpt-5.2-chat-latest', label: 'GPT-5.2 Instant' },
      { value: 'gpt-5.2-codex', label: 'GPT-5.2 Codex' },
      { value: 'gpt-5.1', label: 'GPT-5.1' },
      { value: 'gpt-5.1-chat-latest', label: 'GPT-5.1 Instant' },
      { value: 'gpt-5-turbo', label: 'GPT-5 Turbo' },
      { value: 'gpt-5', label: 'GPT-5' },
      { value: 'gpt-5-mini', label: 'GPT-5 Mini' },
      { value: 'gpt-4.1', label: 'GPT-4.1' },
      { value: 'gpt-4.1-mini', label: 'GPT-4.1 Mini' },
      { value: 'gpt-4o', label: 'GPT-4o' },
      { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
      { value: 'o4', label: 'o4 (Reasoning)' },
      { value: 'o4-mini', label: 'o4-mini (Reasoning)' },
      { value: 'o3-mini', label: 'o3-mini (Reasoning)' },
      { value: 'o1', label: 'o1 (Reasoning)' },
    ],
    anthropic: [
      { value: 'claude-sonnet-4-5-20250929', label: 'Claude Sonnet 4.5' },
      { value: 'claude-opus-4-1-20250805', label: 'Claude Opus 4.1' },
      { value: 'claude-opus-4-20250514', label: 'Claude Opus 4' },
      { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4' },
      { value: 'claude-3-7-sonnet-20250219', label: 'Claude 3.7 Sonnet' },
      { value: 'claude-3-5-haiku-20241022', label: 'Claude 3.5 Haiku' },
      { value: 'claude-3-haiku-20240307', label: 'Claude 3 Haiku' },
    ],
    google: [
      { value: 'gemini-3-pro-preview', label: 'Gemini 3 Pro' },
      { value: 'gemini-3-flash-preview', label: 'Gemini 3 Flash' },
      { value: 'gemini-3-pro-image-preview', label: 'Gemini 3 Pro Vision' },
      { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
      { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
      { value: 'gemini-2.5-flash-preview-09-2025', label: 'Gemini 2.5 Flash Preview' },
      { value: 'gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash Lite' },
      { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
      { value: 'gemini-1.5-pro', label: 'Gemini 1.5 Pro' },
    ],
    perplexity: [
      { value: 'sonar-pro', label: 'Sonar Pro' },
      { value: 'sonar', label: 'Sonar' },
      { value: 'sonar-reasoning-pro', label: 'Sonar Reasoning Pro' },
      { value: 'sonar-reasoning', label: 'Sonar Reasoning' },
      { value: 'sonar-deep-research', label: 'Sonar Deep Research' },
    ],
    deepseek: [
      { value: 'deepseek-chat', label: 'DeepSeek Chat (V3.2)' },
      { value: 'deepseek-reasoner', label: 'DeepSeek Reasoner (R1)' },
    ],
    venice: [
      { value: 'venice-uncensored', label: 'Venice Uncensored' },
      { value: 'llama-3.3-70b', label: 'Llama 3.3 70B' },
      { value: 'llama-3.2-3b', label: 'Llama 3.2 3B (Fast)' },
      { value: 'qwen3-235b-a22b-instruct-2507', label: 'Qwen3 235B Instruct' },
      { value: 'qwen3-235b-a22b-thinking-2507', label: 'Qwen3 235B Thinking' },
      { value: 'qwen3-next-80b', label: 'Qwen3 Next 80B' },
      { value: 'qwen3-coder-480b-a35b-instruct', label: 'Qwen3 Coder 480B' },
      { value: 'qwen3-4b', label: 'Qwen3 4B (Fast)' },
      { value: 'mistral-31-24b', label: 'Mistral 3.1 24B' },
      { value: 'claude-opus-45', label: 'Claude Opus 4.5 (Venice)' },
      { value: 'openai-gpt-52', label: 'GPT-5.2 (Venice)' },
      { value: 'openai-gpt-oss-120b', label: 'GPT OSS 120B (Venice)' },
      { value: 'venice/gemini-3-pro', label: 'Gemini 3 Pro (Venice)' },
      { value: 'venice/gemini-3-flash', label: 'Gemini 3 Flash (Venice)' },
      { value: 'grok-41-fast', label: 'Grok 4.1 Fast' },
      { value: 'grok-code-fast-1', label: 'Grok Code Fast' },
      { value: 'zai-org-glm-4.7', label: 'GLM 4.7' },
      { value: 'kimi-k2-thinking', label: 'Kimi K2 Thinking' },
      { value: 'minimax-m21', label: 'MiniMax M2.1' },
      { value: 'deepseek-v3.2', label: 'DeepSeek V3.2 (Venice)' },
      { value: 'google-gemma-3-27b-it', label: 'Gemma 3 27B' },
      { value: 'hermes-3-llama-3.1-405b', label: 'Hermes 3 Llama 405B' },
    ],
    nanogpt: [
      { value: 'nanogpt/gpt-4o', label: 'GPT-4o (NanoGPT)' },
      { value: 'nanogpt/gpt-4o-mini', label: 'GPT-4o Mini (NanoGPT)' },
      { value: 'nanogpt/claude-3-5-sonnet', label: 'Claude 3.5 Sonnet (NanoGPT)' },
      { value: 'nanogpt/claude-3-haiku', label: 'Claude 3 Haiku (NanoGPT)' },
      { value: 'nanogpt/llama-3.1-70b', label: 'Llama 3.1 70B (NanoGPT)' },
      { value: 'nanogpt/llama-3.1-8b', label: 'Llama 3.1 8B (NanoGPT)' },
      { value: 'nanogpt/mistral-large', label: 'Mistral Large (NanoGPT)' },
      { value: 'nanogpt/mixtral-8x7b', label: 'Mixtral 8x7B (NanoGPT)' },
    ],
    ollama: [
      { value: 'llama3.3:70b', label: 'Llama 3.3 70B' },
      { value: 'llama3.2', label: 'Llama 3.2' },
      { value: 'llama3.2:1b', label: 'Llama 3.2 1B' },
      { value: 'llama3.2:3b', label: 'Llama 3.2 3B' },
      { value: 'llama3.2-vision:11b', label: 'Llama 3.2 Vision 11B' },
      { value: 'llama3.2-vision:90b', label: 'Llama 3.2 Vision 90B' },
      { value: 'qwen3:235b', label: 'Qwen3 235B' },
      { value: 'qwen3:30b', label: 'Qwen3 30B' },
      { value: 'qwen2.5:7b', label: 'Qwen 2.5 7B' },
      { value: 'deepseek-r1:8b', label: 'DeepSeek R1 8B' },
      { value: 'phi4:14b', label: 'Phi-4 14B' },
      { value: 'gemma2:9b', label: 'Gemma 2 9B' },
      { value: 'mistral:7b', label: 'Mistral 7B' },
      { value: 'codellama:7b', label: 'CodeLlama 7B' },
    ],
  };

  const fallbackDefaults: Record<string, string> = {
    openai: 'gpt-5.2',
    anthropic: 'claude-sonnet-4-5-20250929',
    google: 'gemini-3-flash-preview',
    perplexity: 'sonar',
    deepseek: 'deepseek-chat',
    venice: 'venice-uncensored',
    nanogpt: 'nanogpt/gpt-4o-mini',
    ollama: 'deepseek-r1:8b',
  };

  // Use dynamic models from hook when loaded, fallback to static list otherwise
  const getModelsForProvider = (provider: string) => {
    if (modelsLoaded) {
      return getModelsFromHook(provider);
    }
    return fallbackModels[provider] || [];
  };
  const getDefaultModel = (provider: string) => {
    if (modelsLoaded) {
      return getDefaultFromHook(provider);
    }
    return fallbackDefaults[provider] || '';
  };

  // Use dynamic providers when loaded
  const availableProviders = modelsLoaded && dynamicProviders.length > 0 ? dynamicProviders : providers;

  // Infer provider from model if provider is unknown - MUST be defined before useState that uses it
  const inferProvider = (model: string): string => {
    if (model.startsWith('nanogpt/')) return 'nanogpt'; // NanoGPT models use nanogpt/ prefix
    if (model.startsWith('venice/')) return 'venice'; // Venice models use venice/ prefix
    if (model.startsWith('gpt-') || model.startsWith('o')) return 'openai';
    if (model.startsWith('claude-')) return 'anthropic';
    if (model.startsWith('gemini-')) return 'google';
    if (model.startsWith('sonar')) return 'perplexity';
    if (model.includes(':')) return 'ollama'; // Ollama models use colon notation (must check before deepseek)
    if (model.startsWith('deepseek-')) return 'deepseek'; // Matches deepseek-chat, deepseek-reasoner, etc.
    // Venice models (specific names without prefix)
    if (['venice-uncensored', 'llama-3.3-70b', 'qwen3-235b-a22b-instruct-2507', 'qwen3-4b', 'mistral-31-24b', 'claude-opus-45', 'openai-gpt-52', 'grok-41-fast'].includes(model)) return 'venice';
    return 'openai'; // Default fallback
  };

  // State declarations
  const [flipState, setFlipState] = useState(0); // 0 = front, 1 = blockchain, 2 = visualizer
  const [flipScale, setFlipScale] = useState(1); // 1 = full width, 0 = collapsed (mid-flip)
  const [showAvatarCropper, setShowAvatarCropper] = useState(false);
  const [avatarSrc, setAvatarSrc] = useState<string | null>(null);
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [croppedAreaPixels, setCroppedAreaPixels] = useState<Area | null>(null);
  const [savingAvatar, setSavingAvatar] = useState(false);
  const [avatarCacheBust, setAvatarCacheBust] = useState(Date.now());
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const [isEditingModel, setIsEditingModel] = useState(false);
  const [providerSelectedInEdit, setProviderSelectedInEdit] = useState(false); // Track if provider was selected during edit
  const [modelSelectedInEdit, setModelSelectedInEdit] = useState(false); // Track if model was selected during edit

  // Visualizer settings state
  const [visualizerSettings, setVisualizerSettings] = useState({
    enabled: false,
    waveform_style: 1,
    color_theme: 'qube-color',
    gradient_style: 'gradient-dark',
    sensitivity: 50,
    animation_smoothness: 'medium',
    audio_offset_ms: 0,
    frequency_range: 20,
    output_monitor: 0
  });
  const [loadingVisualizerSettings, setLoadingVisualizerSettings] = useState(false);
  const [savingVisualizerSettings, setSavingVisualizerSettings] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error'; isExiting?: boolean } | null>(null);

  // Model Control state
  const [modelLocked, setModelLocked] = useState(false);
  const [lockedToModel, setLockedToModel] = useState<string | null>(null);
  const [revolverMode, setRevolverMode] = useState(false);
  const [revolverModePool, setRevolverModePool] = useState<string[]>([]);
  const [autonomousMode, setAutonomousMode] = useState(false);  // Manual mode is default
  const [autonomousModePool, setAutonomousModePool] = useState<string[]>([]);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [modelPreferences, setModelPreferences] = useState<Record<string, { model: string; reason?: string }>>({});
  const [loadingModelPrefs, setLoadingModelPrefs] = useState(false);
  const [availableMonitors, setAvailableMonitors] = useState<Array<{id: number; name: string}>>([]);
  const [isEditingVoice, setIsEditingVoice] = useState(false);
  const [voiceProviderSelectedInEdit, setVoiceProviderSelectedInEdit] = useState(false); // Track if voice provider was selected during edit
  const [voiceSelectedInEdit, setVoiceSelectedInEdit] = useState(false); // Track if voice was selected during edit
  const [isEditingColor, setIsEditingColor] = useState(false);
  const [isEditingEvalModel, setIsEditingEvalModel] = useState(false);
  const [selectedModel, setSelectedModel] = useState(qube.ai_model);
  const [selectedVoice, setSelectedVoice] = useState(qube.voice_model || '');
  const [selectedVoiceProvider, setSelectedVoiceProvider] = useState(() => {
    // Parse voice provider from voice_model (e.g., "gemini:Puck" -> "gemini")
    const voiceModel = qube.voice_model || '';
    return voiceModel.includes(':') ? voiceModel.split(':')[0] : 'openai';
  });
  const [selectedColor, setSelectedColor] = useState(qube.favorite_color);
  const [selectedProvider, setSelectedProvider] = useState(qube.ai_provider);
  const [selectedEvalModel, setSelectedEvalModel] = useState((qube as any).evaluation_model || 'llama3.2');
  const [selectedEvalProvider, setSelectedEvalProvider] = useState(() => {
    const evalModel = (qube as any).evaluation_model || 'llama3.2';
    return inferProvider(evalModel);
  });

  // Wallet balance state - initialize from cache if available
  const [walletBalance, setWalletBalance] = useState<number | null>(cachedWalletData?.balance ?? null);  // P2SH wallet
  const [walletBalanceLoading, setWalletBalanceLoading] = useState(false);
  const [walletBalanceError, setWalletBalanceError] = useState<string | null>(cachedWalletData?.error ?? null);
  const [showSweepModal, setShowSweepModal] = useState(false);
  const [sweepAddress, setSweepAddress] = useState('');
  const [sweepPassword, setSweepPassword] = useState('');
  const [isSweeping, setIsSweeping] = useState(false);
  const [sweepResult, setSweepResult] = useState<string | null>(null);

  const handleSweep = async () => {
    if (!sweepAddress || !userId || !masterPassword) return;
    setIsSweeping(true);
    setSweepResult(null);
    try {
      const result = await invoke<{ success: boolean; swept_sats: number; error?: string }>('sweep_qube_wallet', {
        userId,
        qubeId: qube.qube_id,
        sweepAddress,
        password: sweepPassword || masterPassword,
      });
      if (result.success) {
        const bch = (result.swept_sats / 1e8).toFixed(8);
        setSweepResult(`✓ Swept ${bch} BCH`);
        setWalletBalance(0);
        setCachedBalance(qube.qube_id, 0);
        setSweepAddress('');
      } else {
        setSweepResult(`✗ ${result.error || 'Sweep failed'}`);
      }
    } catch (err) {
      setSweepResult(`✗ ${String(err)}`);
    } finally {
      setIsSweeping(false);
    }
  };

  // Custom voices now come from VoiceLibraryContext (see useVoiceLibrary hook above)

  // Format BCH amount for display (always show 8 decimal places)
  const formatBCH = (sats: number) => {
    const bch = sats / 100_000_000;
    return bch.toFixed(8);
  };

  // Fetch wallet balance when flipping to blockchain side
  useEffect(() => {
    const fetchWalletBalance = async () => {
      // Only fetch if:
      // 1. We're on the blockchain side (flipState === 1)
      // 2. Qube has a wallet address
      // 3. We haven't already loaded the balance (local state or cache)
      // 4. We have credentials
      if (flipState !== 1 || !qube.wallet_address || walletBalance !== null || !userId || !masterPassword) {
        return;
      }

      setWalletBalanceLoading(true);
      setWalletBalanceError(null);

      try {
        const result = await invoke<{
          success: boolean;
          balance_sats?: number;
          error?: string;
        }>('get_wallet_info', {
          userId,
          qubeId: qube.qube_id,
          password: masterPassword,
        });

        if (result.success) {
          const balance = result.balance_sats ?? 0;

          // Update local state
          setWalletBalance(balance);

          // Update cache
          setCachedBalance(qube.qube_id, balance);
        } else {
          setWalletBalanceError(result.error || 'Failed to fetch balance');
        }
      } catch (error) {
        console.error('Failed to fetch wallet balance:', error);
        setWalletBalanceError('Failed to fetch balance');
      } finally {
        setWalletBalanceLoading(false);
      }
    };

    fetchWalletBalance();
  }, [flipState, qube.wallet_address, qube.qube_id, userId, masterPassword, walletBalance, setCachedBalance]);

  // Load wallet security when flipping to blockchain side
  useEffect(() => {
    if (flipState === 1 && qube.recipient_address && userId && masterPassword) {
      loadWalletSecurity();
    }
  }, [flipState, qube.recipient_address, userId, masterPassword]);

  // Load model preferences on mount
  useEffect(() => {
    const loadModelPreferences = async () => {
      if (!userId) return;
      try {
        setLoadingModelPrefs(true);
        const result = await invoke<{
          success: boolean;
          preferences: Record<string, { model: string; reason?: string }>;
          model_locked: boolean;
          locked_to: string | null;
          revolver_mode: boolean;
          revolver_mode_pool: string[];
          autonomous_mode: boolean;
          autonomous_mode_pool: string[];
        }>('get_model_preferences', {
          userId,
          qubeId: qube.qube_id,
          password: masterPassword,
        });
        if (result.success) {
          setModelLocked(result.model_locked);
          setLockedToModel(result.locked_to);
          setRevolverMode(result.revolver_mode);
          setRevolverModePool(result.revolver_mode_pool || []);
          // Default to autonomous mode if neither locked nor revolver
          setAutonomousMode(result.autonomous_mode ?? (!result.model_locked && !result.revolver_mode));
          setAutonomousModePool(result.autonomous_mode_pool || []);
          setModelPreferences(result.preferences || {});
        }
      } catch (error) {
        console.error('Failed to load model preferences:', error);
      } finally {
        setLoadingModelPrefs(false);
      }
    };
    loadModelPreferences();
  }, [userId, qube.qube_id, masterPassword]);

  // Toggle model lock (mutually exclusive with revolver mode)
  const handleToggleModelLock = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!userId || loadingModelPrefs) return;
    try {
      const newLocked = !modelLocked;
      const result = await invoke<{
        success: boolean;
        locked: boolean;
        locked_to: string | null;
        error?: string;
      }>('set_model_lock', {
        userId,
        qubeId: qube.qube_id,
        locked: newLocked,
        modelName: newLocked ? qube.ai_model : null // Lock to current model
      });
      if (result.success) {
        setModelLocked(result.locked);
        setLockedToModel(result.locked_to);
        // Lock disables revolver mode (mutually exclusive)
        if (result.locked) {
          setRevolverMode(false);
        }
        // Invalidate chain state cache and refresh
        invalidateCache(qube.qube_id);
        await loadChainState(qube.qube_id, true);
      } else if (result.error) {
        console.error('Model lock error:', result.error);
      }
    } catch (error) {
      console.error('Failed to toggle model lock:', error);
    }
  };

  // Toggle revolver mode (mutually exclusive with model lock)
  const handleToggleRevolverMode = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!userId || loadingModelPrefs) return;
    try {
      const newEnabled = !revolverMode;
      const result = await invoke<{
        success: boolean;
        revolver_mode: boolean;
        error?: string;
      }>('set_revolver_mode', {
        userId,
        qubeId: qube.qube_id,
        enabled: newEnabled
      });
      if (result.success) {
        setRevolverMode(result.revolver_mode);
        // Revolver disables lock mode (mutually exclusive)
        if (result.revolver_mode) {
          setModelLocked(false);
          setLockedToModel(null);
        }
        // Invalidate chain state cache and refresh
        invalidateCache(qube.qube_id);
        await loadChainState(qube.qube_id, true);
      } else if (result.error) {
        console.error('Revolver mode error:', result.error);
      }
    } catch (error) {
      console.error('Failed to toggle revolver mode:', error);
    }
  };

  const handleFlip = () => {
    if (flipScale !== 1) return; // already animating
    setFlipScale(0); // collapse
    setTimeout(() => {
      setFlipState(prev => (prev + 1) % 3);
      setFlipScale(1); // expand
    }, 200);
  };

  const handleAvatarFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setAvatarSrc(reader.result as string);
      setCrop({ x: 0, y: 0 });
      setZoom(1);
      setShowAvatarCropper(true);
    };
    reader.readAsDataURL(file);
    // Reset input so same file can be re-selected
    e.target.value = '';
  };

  const onCropComplete = useCallback((_: Area, croppedPixels: Area) => {
    setCroppedAreaPixels(croppedPixels);
  }, []);

  const getCroppedImageBase64 = async (imageSrc: string, pixelCrop: Area): Promise<string> => {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.addEventListener('load', () => resolve(img));
      img.addEventListener('error', reject);
      img.src = imageSrc;
    });
    const canvas = document.createElement('canvas');
    canvas.width = pixelCrop.width;
    canvas.height = pixelCrop.height;
    const ctx = canvas.getContext('2d')!;
    ctx.drawImage(image, pixelCrop.x, pixelCrop.y, pixelCrop.width, pixelCrop.height, 0, 0, pixelCrop.width, pixelCrop.height);
    // Return base64 without the data URL prefix
    return canvas.toDataURL('image/png').split(',')[1];
  };

  const handleSaveAvatar = async () => {
    if (!avatarSrc || !croppedAreaPixels) return;
    setSavingAvatar(true);
    try {
      const base64 = await getCroppedImageBase64(avatarSrc, croppedAreaPixels);
      // Save to temp file via Tauri
      const tempPath = await invoke<string>('save_cropped_avatar', { base64Data: base64 });
      // Update avatar in backend
      const result = await invoke<{ success: boolean; error?: string }>('update_qube_avatar', {
        userId,
        qubeId: qube.qube_id,
        avatarPath: tempPath,
      });
      if (result.success) {
        setShowAvatarCropper(false);
        setAvatarSrc(null);
        setAvatarCacheBust(Date.now()); // Force img reload
      } else {
        alert(`Failed to update avatar: ${result.error}`);
      }
    } catch (err) {
      alert(`Error: ${String(err)}`);
    } finally {
      setSavingAvatar(false);
    }
  };

  const loadVisualizerSettings = async () => {
    try {
      setLoadingVisualizerSettings(true);
      const result = await invoke('get_visualizer_settings', {
        userId,
        qubeId: qube.qube_id,
        password: masterPassword
      });
      if (result) {
        // Merge with defaults to handle missing fields (like output_monitor)
        setVisualizerSettings({
          ...visualizerSettings,
          ...(result as any),
          // Ensure output_monitor has a default if missing
          output_monitor: (result as any).output_monitor ?? 0
        });
      }
    } catch (error) {
      console.error('Failed to load visualizer settings:', error);
    } finally {
      setLoadingVisualizerSettings(false);
    }
  };

  const saveVisualizerSettings = async () => {
    try {
      setSavingVisualizerSettings(true);
      await invoke('save_visualizer_settings', {
        userId,
        qubeId: qube.qube_id,
        settings: JSON.stringify(visualizerSettings),
        password: masterPassword
      });
      // Invalidate chain state cache and refresh so other components see the update
      invalidateCache(qube.qube_id);
      await loadChainState(qube.qube_id, true);
      setToast({ message: 'Settings Saved', type: 'success' });
    } catch (error) {
      console.error('Failed to save visualizer settings:', error);
      setToast({ message: `Error: ${String(error)}`, type: 'error' });
    } finally {
      setSavingVisualizerSettings(false);
    }
  };

  const resetVisualizerSettings = () => {
    setVisualizerSettings({
      enabled: false,
      waveform_style: 1,
      color_theme: 'qube-color',
      gradient_style: 'gradient-dark',
      sensitivity: 50,
      animation_smoothness: 'medium',
      audio_offset_ms: 0,
      frequency_range: 20,
      output_monitor: 0
    });
  };

  // Load available monitors
  const loadAvailableMonitors = async () => {
    try {
      const result: any = await invoke('get_available_monitors');
      if (result && result.monitors) {
        setAvailableMonitors(result.monitors);
      }
    } catch (error) {
      console.error('Failed to load monitors:', error);
      // Fallback to hardcoded list
      setAvailableMonitors([
        { id: 1, name: 'External Monitor 1' },
        { id: 2, name: 'External Monitor 2' },
        { id: 3, name: 'External Monitor 3' }
      ]);
    }
  };

  // Handle output monitor change
  const handleOutputMonitorChange = async (newMonitorIndex: number) => {
    const oldMonitorIndex = visualizerSettings.output_monitor;

    // Update settings
    const newSettings = { ...visualizerSettings, output_monitor: newMonitorIndex };
    setVisualizerSettings(newSettings);

    // Save to database
    try {
      const settingsJson = JSON.stringify(newSettings);

      await invoke('save_visualizer_settings', {
        userId,
        qubeId: qube.qube_id,
        settings: settingsJson,
        password: masterPassword
      });

      // Invalidate chain state cache and refresh
      invalidateCache(qube.qube_id);
      await loadChainState(qube.qube_id, true);

      // Emit event to notify other components (like ChatInterface) to reload settings
      await emit('visualizer-settings-changed', { qubeId: qube.qube_id });
    } catch (error) {
      console.error('Failed to save visualizer settings:', error);
    }

    // Close existing visualizer window if it's currently open
    // The window will be recreated when TTS plays (handled by ChatInterface)
    if (oldMonitorIndex > 0) {
      try {
        await invoke('close_visualizer_window');
      } catch (error) {
        console.error('Failed to close visualizer window:', error);
      }
    }

    // Don't create window here - it will be created automatically when TTS plays
  };

  // Auto-hide toast after 2 seconds with fade-out
  useEffect(() => {
    if (toast && !toast.isExiting) {
      const fadeTimer = setTimeout(() => {
        setToast({ ...toast, isExiting: true });
      }, 1800);
      return () => clearTimeout(fadeTimer);
    } else if (toast && toast.isExiting) {
      const removeTimer = setTimeout(() => {
        setToast(null);
      }, 200);
      return () => clearTimeout(removeTimer);
    }
  }, [toast]);

  // Load monitors on component mount
  useEffect(() => {
    loadAvailableMonitors();

    // Cleanup: Close visualizer window on unmount
    return () => {
      if (visualizerSettings.output_monitor > 0) {
        invoke('close_visualizer_window').catch(() => {
          // Silently fail
        });
      }
    };
  }, [visualizerSettings.output_monitor]);

  // Calculate trust color based on trust score (0-100)
  const getTrustColor = (trust: number): string => {
    if (trust >= 75) return '#00ff88'; // High trust - green
    if (trust >= 50) return '#ffaa00'; // Medium trust - yellow/orange
    if (trust >= 25) return '#ff8800'; // Low-medium trust - orange
    return '#ff3366'; // Low trust - red
  };

  // Format model ID to display name
  const formatModelName = (modelId: string): string => {
    // First, search through fallbackModels to find the label
    for (const providerModels of Object.values(fallbackModels)) {
      const found = providerModels.find(m => m.value === modelId);
      if (found) return found.label;
    }

    // Fallback: Try to make it readable using patterns
    if (modelId.startsWith('gpt-')) {
      return modelId.toUpperCase().replace('GPT-', 'GPT-');
    }
    if (modelId.startsWith('claude-')) {
      // claude-sonnet-4-5-20250929 -> Claude Sonnet 4.5
      const parts = modelId.replace('claude-', '').split('-');
      const name = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
      if (parts.length >= 3) {
        return `Claude ${name} ${parts[1]}.${parts[2]}`;
      }
      return `Claude ${name}`;
    }
    if (modelId.startsWith('gemini-')) {
      // gemini-2.5-pro -> Gemini 2.5 Pro
      return modelId
        .split('-')
        .map(p => p.charAt(0).toUpperCase() + p.slice(1))
        .join(' ');
    }
    if (modelId.startsWith('sonar')) {
      // sonar-pro -> Sonar Pro
      return modelId
        .split('-')
        .map(p => p.charAt(0).toUpperCase() + p.slice(1))
        .join(' ');
    }
    if (modelId.startsWith('deepseek-')) {
      // deepseek-chat -> DeepSeek Chat
      return modelId
        .split('-')
        .map(p => p.charAt(0).toUpperCase() + p.slice(1))
        .join(' ');
    }

    // Default: return as-is
    return modelId;
  };

  // Format voice ID to display name
  const formatVoiceName = (voiceId: string): string => {
    if (!voiceId) return 'None';

    // Fallback: format "provider:voice" to "Provider: Voice"
    if (voiceId.includes(':')) {
      const [provider, voice] = voiceId.split(':');

      // Special case for custom voices - look up the name
      if (provider === 'custom' && customVoices[voice]) {
        return `✨ ${customVoices[voice].name}`;
      }

      // Use alias if available, otherwise capitalize
      const voiceName = VOICE_NAME_ALIASES[voice] || voice.charAt(0).toUpperCase() + voice.slice(1);

      // Special case for OpenAI
      const providerName = provider === 'openai' ? 'OpenAI' : provider.charAt(0).toUpperCase() + provider.slice(1);
      return `${providerName}: ${voiceName}`;
    }

    return voiceId;
  };

  // Get the correct provider (infer if unknown)
  const getCorrectProvider = () => {
    return qube.ai_provider === 'unknown' || !qube.ai_provider
      ? inferProvider(qube.ai_model)
      : qube.ai_provider;
  };

  // Reset selections when qube changes
  React.useEffect(() => {
    console.log('[VOICE_DEBUG] qube changed, syncing state:', { qubeId: qube.qube_id, voice_model: qube.voice_model });
    setSelectedModel(qube.ai_model);
    setSelectedVoice(qube.voice_model || '');
    // Always infer provider from model name (don't trust ai_provider field)
    setSelectedProvider(inferProvider(qube.ai_model));
    // Parse voice provider from voice_model
    const voiceModel = qube.voice_model || '';
    setSelectedVoiceProvider(voiceModel.includes(':') ? voiceModel.split(':')[0] : 'openai');
  }, [qube.ai_model, qube.voice_model]);

  // Also reset when entering edit mode (always infer provider from current model)
  React.useEffect(() => {
    if (isEditingModel) {
      setSelectedModel(qube.ai_model);
      setSelectedProvider(inferProvider(qube.ai_model));
    }
  }, [isEditingModel]);

  // Reset voice selections when entering voice edit mode
  React.useEffect(() => {
    if (isEditingVoice) {
      console.log('[VOICE_DEBUG] Edit mode entered, resetting to qube.voice_model:', qube.voice_model);
      setSelectedVoice(qube.voice_model || '');
      const voiceModel = qube.voice_model || '';
      setSelectedVoiceProvider(voiceModel.includes(':') ? voiceModel.split(':')[0] : 'openai');
    }
  }, [isEditingVoice]);

  // Reset evaluation model selections when qube changes
  React.useEffect(() => {
    const evalModel = (qube as any).evaluation_model || 'llama3.2';
    setSelectedEvalModel(evalModel);
    setSelectedEvalProvider(inferProvider(evalModel));
  }, [qube.qube_id, (qube as any).evaluation_model]);

  // Reset evaluation model when entering edit mode (always infer provider from current model)
  React.useEffect(() => {
    if (isEditingEvalModel) {
      const evalModel = (qube as any).evaluation_model || 'llama3.2';
      setSelectedEvalModel(evalModel);
      setSelectedEvalProvider(inferProvider(evalModel));
    }
  }, [isEditingEvalModel]);

  // Load visualizer settings when qube changes
  React.useEffect(() => {
    if (qube.qube_id) {
      loadVisualizerSettings();
    }
  }, [qube.qube_id]);

  // Model options now come from useModels hook via getModelsForProvider()

  // Helper function to get voice gender labels
  const getVoiceGender = (voiceId: string): string => {
    const voiceName = voiceId.includes(':') ? voiceId.split(':')[1].toLowerCase() : voiceId.toLowerCase();

    // Gemini voices
    const geminiMale = ['achernar', 'algenib', 'alnilam', 'charon', 'fenrir', 'gacrux', 'iapetus', 'orus', 'puck', 'rasalgethi', 'sadachbia', 'sadaltager', 'umbriel', 'zephyr'];
    const geminiFemale = ['achird', 'algieba', 'aoede', 'autonoe', 'callirrhoe', 'despina', 'enceladus', 'erinome', 'kore', 'laomedeia', 'leda', 'pulcherrima', 'schedar', 'sulafat', 'vindemiatrix', 'zubenelgenubi'];

    // OpenAI voices (9 total for tts-1 model)
    const openaiMale = ['ash', 'echo', 'fable', 'onyx'];
    const openaiFemale = ['alloy', 'coral', 'nova', 'sage', 'shimmer'];

    // Google Cloud TTS voices (Standard, WaveNet, Neural2, Studio, Chirp)
    const googleMale = [
      'standard-a', 'standard-b', 'standard-d', 'standard-i', 'standard-j',
      'wavenet-a', 'wavenet-b', 'wavenet-d', 'wavenet-i', 'wavenet-j',
      'neural2-a', 'neural2-d', 'neural2-i', 'neural2-j',
      'studio-q', 'chirp-hd-d'
    ];
    const googleFemale = [
      'standard-c', 'standard-e', 'standard-f', 'standard-g', 'standard-h',
      'wavenet-c', 'wavenet-e', 'wavenet-f', 'wavenet-g', 'wavenet-h',
      'neural2-c', 'neural2-e', 'neural2-f', 'neural2-g', 'neural2-h',
      'studio-o', 'chirp-hd-f'
    ];

    // Qwen3-TTS voices
    const qwen3Male = ['dylan', 'eric', 'ryan', 'aiden', 'uncle_fu'];
    const qwen3Female = ['vivian', 'serena', 'ono_anna', 'sohee'];

    // Kokoro TTS voices (pattern: first char = lang, second char = gender)
    const kokoroFemale = voiceName.length > 1 && voiceName[1] === 'f';
    const kokoroMale = voiceName.length > 1 && voiceName[1] === 'm';

    if (geminiMale.includes(voiceName)) return ' (male)';
    if (geminiFemale.includes(voiceName)) return ' (female)';
    if (openaiMale.includes(voiceName)) return ' (male)';
    if (openaiFemale.includes(voiceName)) return ' (female)';
    if (googleMale.includes(voiceName)) return ' (male)';
    if (googleFemale.includes(voiceName)) return ' (female)';
    if (qwen3Male.includes(voiceName)) return ' (male)';
    if (qwen3Female.includes(voiceName)) return ' (female)';
    if (kokoroFemale) return ' (female)';
    if (kokoroMale) return ' (male)';

    return '';
  };

  // Voice options organized by provider
  const voiceOptions: Record<string, { label: string; value: string }[]> = {
    gemini: [
      { label: `Achernar${getVoiceGender('gemini:achernar')}`, value: 'gemini:achernar' },
      { label: `Achird${getVoiceGender('gemini:achird')}`, value: 'gemini:achird' },
      { label: `Algenib${getVoiceGender('gemini:algenib')}`, value: 'gemini:algenib' },
      { label: `Algieba${getVoiceGender('gemini:algieba')}`, value: 'gemini:algieba' },
      { label: `Alnilam${getVoiceGender('gemini:alnilam')}`, value: 'gemini:alnilam' },
      { label: `Aoede${getVoiceGender('gemini:aoede')}`, value: 'gemini:aoede' },
      { label: `Autonoe${getVoiceGender('gemini:autonoe')}`, value: 'gemini:autonoe' },
      { label: `Callirrhoe${getVoiceGender('gemini:callirrhoe')}`, value: 'gemini:callirrhoe' },
      { label: `Charon${getVoiceGender('gemini:charon')}`, value: 'gemini:charon' },
      { label: `Despina${getVoiceGender('gemini:despina')}`, value: 'gemini:despina' },
      { label: `Enceladus${getVoiceGender('gemini:enceladus')}`, value: 'gemini:enceladus' },
      { label: `Erinome${getVoiceGender('gemini:erinome')}`, value: 'gemini:erinome' },
      { label: `Fenrir${getVoiceGender('gemini:fenrir')}`, value: 'gemini:fenrir' },
      { label: `Gacrux${getVoiceGender('gemini:gacrux')}`, value: 'gemini:gacrux' },
      { label: `Iapetus${getVoiceGender('gemini:iapetus')}`, value: 'gemini:iapetus' },
      { label: `Kore${getVoiceGender('gemini:kore')}`, value: 'gemini:kore' },
      { label: `Laomedeia${getVoiceGender('gemini:laomedeia')}`, value: 'gemini:laomedeia' },
      { label: `Leda${getVoiceGender('gemini:leda')}`, value: 'gemini:leda' },
      { label: `Orus${getVoiceGender('gemini:orus')}`, value: 'gemini:orus' },
      { label: `Puck${getVoiceGender('gemini:puck')}`, value: 'gemini:puck' },
      { label: `Pulcherrima${getVoiceGender('gemini:pulcherrima')}`, value: 'gemini:pulcherrima' },
      { label: `Rasalgethi${getVoiceGender('gemini:rasalgethi')}`, value: 'gemini:rasalgethi' },
      { label: `Sadachbia${getVoiceGender('gemini:sadachbia')}`, value: 'gemini:sadachbia' },
      { label: `Sadaltager${getVoiceGender('gemini:sadaltager')}`, value: 'gemini:sadaltager' },
      { label: `Schedar${getVoiceGender('gemini:schedar')}`, value: 'gemini:schedar' },
      { label: `Sulafat${getVoiceGender('gemini:sulafat')}`, value: 'gemini:sulafat' },
      { label: `Umbriel${getVoiceGender('gemini:umbriel')}`, value: 'gemini:umbriel' },
      { label: `Vindemiatrix${getVoiceGender('gemini:vindemiatrix')}`, value: 'gemini:vindemiatrix' },
      { label: `Zephyr${getVoiceGender('gemini:zephyr')}`, value: 'gemini:zephyr' },
      { label: `Zubenelgenubi${getVoiceGender('gemini:zubenelgenubi')}`, value: 'gemini:zubenelgenubi' },
    ],
    openai: [
      { label: `Alloy${getVoiceGender('openai:alloy')}`, value: 'openai:alloy' },
      { label: `Ash${getVoiceGender('openai:ash')}`, value: 'openai:ash' },
      { label: `Coral${getVoiceGender('openai:coral')}`, value: 'openai:coral' },
      { label: `Echo${getVoiceGender('openai:echo')}`, value: 'openai:echo' },
      { label: `Fable${getVoiceGender('openai:fable')}`, value: 'openai:fable' },
      { label: `Nova${getVoiceGender('openai:nova')}`, value: 'openai:nova' },
      { label: `Onyx${getVoiceGender('openai:onyx')}`, value: 'openai:onyx' },
      { label: `Sage${getVoiceGender('openai:sage')}`, value: 'openai:sage' },
      { label: `Shimmer${getVoiceGender('openai:shimmer')}`, value: 'openai:shimmer' },
    ],
    google: [
      // Neural2 voices (High Quality)
      { label: `Neural2-A${getVoiceGender('google:Neural2-A')}`, value: 'google:en-US-Neural2-A' },
      { label: `Neural2-C${getVoiceGender('google:Neural2-C')}`, value: 'google:en-US-Neural2-C' },
      { label: `Neural2-D${getVoiceGender('google:Neural2-D')}`, value: 'google:en-US-Neural2-D' },
      { label: `Neural2-E${getVoiceGender('google:Neural2-E')}`, value: 'google:en-US-Neural2-E' },
      { label: `Neural2-F${getVoiceGender('google:Neural2-F')}`, value: 'google:en-US-Neural2-F' },
      { label: `Neural2-G${getVoiceGender('google:Neural2-G')}`, value: 'google:en-US-Neural2-G' },
      { label: `Neural2-H${getVoiceGender('google:Neural2-H')}`, value: 'google:en-US-Neural2-H' },
      { label: `Neural2-I${getVoiceGender('google:Neural2-I')}`, value: 'google:en-US-Neural2-I' },
      { label: `Neural2-J${getVoiceGender('google:Neural2-J')}`, value: 'google:en-US-Neural2-J' },
      // WaveNet voices (High Quality)
      { label: `Wavenet-A${getVoiceGender('google:Wavenet-A')}`, value: 'google:en-US-Wavenet-A' },
      { label: `Wavenet-B${getVoiceGender('google:Wavenet-B')}`, value: 'google:en-US-Wavenet-B' },
      { label: `Wavenet-C${getVoiceGender('google:Wavenet-C')}`, value: 'google:en-US-Wavenet-C' },
      { label: `Wavenet-D${getVoiceGender('google:Wavenet-D')}`, value: 'google:en-US-Wavenet-D' },
      { label: `Wavenet-E${getVoiceGender('google:Wavenet-E')}`, value: 'google:en-US-Wavenet-E' },
      { label: `Wavenet-F${getVoiceGender('google:Wavenet-F')}`, value: 'google:en-US-Wavenet-F' },
      { label: `Wavenet-G${getVoiceGender('google:Wavenet-G')}`, value: 'google:en-US-Wavenet-G' },
      { label: `Wavenet-H${getVoiceGender('google:Wavenet-H')}`, value: 'google:en-US-Wavenet-H' },
      { label: `Wavenet-I${getVoiceGender('google:Wavenet-I')}`, value: 'google:en-US-Wavenet-I' },
      { label: `Wavenet-J${getVoiceGender('google:Wavenet-J')}`, value: 'google:en-US-Wavenet-J' },
      // Studio voices (Premium)
      { label: `Studio-O${getVoiceGender('google:Studio-O')}`, value: 'google:en-US-Studio-O' },
      { label: `Studio-Q${getVoiceGender('google:Studio-Q')}`, value: 'google:en-US-Studio-Q' },
      // Chirp-HD voices
      { label: `Chirp-HD-D${getVoiceGender('google:Chirp-HD-D')}`, value: 'google:en-US-Chirp-HD-D' },
      { label: `Chirp-HD-F${getVoiceGender('google:Chirp-HD-F')}`, value: 'google:en-US-Chirp-HD-F' },
      // Standard voices (Budget)
      { label: `Standard-A${getVoiceGender('google:Standard-A')}`, value: 'google:en-US-Standard-A' },
      { label: `Standard-B${getVoiceGender('google:Standard-B')}`, value: 'google:en-US-Standard-B' },
      { label: `Standard-C${getVoiceGender('google:Standard-C')}`, value: 'google:en-US-Standard-C' },
      { label: `Standard-D${getVoiceGender('google:Standard-D')}`, value: 'google:en-US-Standard-D' },
      { label: `Standard-E${getVoiceGender('google:Standard-E')}`, value: 'google:en-US-Standard-E' },
      { label: `Standard-F${getVoiceGender('google:Standard-F')}`, value: 'google:en-US-Standard-F' },
      { label: `Standard-G${getVoiceGender('google:Standard-G')}`, value: 'google:en-US-Standard-G' },
      { label: `Standard-H${getVoiceGender('google:Standard-H')}`, value: 'google:en-US-Standard-H' },
      { label: `Standard-I${getVoiceGender('google:Standard-I')}`, value: 'google:en-US-Standard-I' },
      { label: `Standard-J${getVoiceGender('google:Standard-J')}`, value: 'google:en-US-Standard-J' },
    ],
    elevenlabs: [
      { label: 'Default', value: 'elevenlabs:default' },
    ],
    qwen3: [
      { label: `Vivian${getVoiceGender('qwen3:Vivian')}`, value: 'qwen3:Vivian' },
      { label: `Serena${getVoiceGender('qwen3:Serena')}`, value: 'qwen3:Serena' },
      { label: `Dylan${getVoiceGender('qwen3:Dylan')}`, value: 'qwen3:Dylan' },
      { label: `Eric${getVoiceGender('qwen3:Eric')}`, value: 'qwen3:Eric' },
      { label: `Ryan${getVoiceGender('qwen3:Ryan')}`, value: 'qwen3:Ryan' },
      { label: `Aiden${getVoiceGender('qwen3:Aiden')}`, value: 'qwen3:Aiden' },
      { label: `Ono Anna${getVoiceGender('qwen3:Ono_Anna')}`, value: 'qwen3:Ono_Anna' },
      { label: `Sohee${getVoiceGender('qwen3:Sohee')}`, value: 'qwen3:Sohee' },
      { label: `Uncle Fu${getVoiceGender('qwen3:Uncle_Fu')}`, value: 'qwen3:Uncle_Fu' },
    ],
    kokoro: [
      // American English
      { label: `Heart${getVoiceGender('kokoro:af_heart')}`, value: 'kokoro:af_heart' },
      { label: `Bella${getVoiceGender('kokoro:af_bella')}`, value: 'kokoro:af_bella' },
      { label: `Nova${getVoiceGender('kokoro:af_nova')}`, value: 'kokoro:af_nova' },
      { label: `Sarah${getVoiceGender('kokoro:af_sarah')}`, value: 'kokoro:af_sarah' },
      { label: `Adam${getVoiceGender('kokoro:am_adam')}`, value: 'kokoro:am_adam' },
      { label: `Michael${getVoiceGender('kokoro:am_michael')}`, value: 'kokoro:am_michael' },
      // British English
      { label: `Emma (UK)${getVoiceGender('kokoro:bf_emma')}`, value: 'kokoro:bf_emma' },
      { label: `Lily (UK)${getVoiceGender('kokoro:bf_lily')}`, value: 'kokoro:bf_lily' },
      { label: `George (UK)${getVoiceGender('kokoro:bm_george')}`, value: 'kokoro:bm_george' },
      { label: `Daniel (UK)${getVoiceGender('kokoro:bm_daniel')}`, value: 'kokoro:bm_daniel' },
      // Japanese
      { label: `Alpha (JP)${getVoiceGender('kokoro:jf_alpha')}`, value: 'kokoro:jf_alpha' },
      { label: `Kumo (JP)${getVoiceGender('kokoro:jm_kumo')}`, value: 'kokoro:jm_kumo' },
      // Mandarin Chinese
      { label: `Xiaoxiao (ZH)${getVoiceGender('kokoro:zf_xiaoxiao')}`, value: 'kokoro:zf_xiaoxiao' },
      { label: `Yunxi (ZH)${getVoiceGender('kokoro:zm_yunxi')}`, value: 'kokoro:zm_yunxi' },
      // Spanish / French
      { label: `Dora (ES)${getVoiceGender('kokoro:ef_dora')}`, value: 'kokoro:ef_dora' },
      { label: `Siwis (FR)${getVoiceGender('kokoro:ff_siwis')}`, value: 'kokoro:ff_siwis' },
    ],
  };

  // Build voice provider options including custom voices if available
  const customVoiceCount = Object.keys(customVoices).length;
  const voiceProviderOptions = [
    ...(customVoiceCount > 0 ? [{ label: `Custom Voices (local)`, value: 'custom' }] : []),
    { label: 'Kokoro (local)', value: 'kokoro' },
    { label: 'Qwen3 (local)', value: 'qwen3' },
    { label: 'Google Cloud (API)', value: 'google' },
    { label: 'Google Gemini (API)', value: 'gemini' },
    { label: 'OpenAI (API)', value: 'openai' },
    { label: 'ElevenLabs (API)', value: 'elevenlabs' },
  ];

  // Add custom voices to voiceOptions
  const customVoiceOptions = Object.entries(customVoices).map(([voiceId, voice]) => ({
    label: `${voice.name} (${voice.voice_type})`,
    value: `custom:${voiceId}`,
  }));

  // Merge custom voices into voiceOptions
  const voiceOptionsWithCustom: Record<string, Array<{ label: string; value: string }>> = {
    ...voiceOptions,
    custom: customVoiceOptions,
  };

  const defaultVoices: Record<string, string> = {
    custom: customVoiceCount > 0 ? `custom:${Object.keys(customVoices)[0]}` : '',
    kokoro: 'kokoro:af_heart',
    qwen3: 'qwen3:Vivian',
    google: 'google:en-US-Neural2-A',
    gemini: 'gemini:puck',
    openai: 'openai:alloy',
    elevenlabs: 'elevenlabs:default',
  };

  // Use dynamic providers from useModels hook (providers is already available from the hook)

  const handleProviderChange = (newProvider: string) => {
    setSelectedProvider(newProvider);
    setProviderSelectedInEdit(true); // Collapse provider dropdown, expand model dropdown
    setModelSelectedInEdit(false); // Reset model selection state when provider changes
    // Auto-select default model for new provider
    const defaultModel = getDefaultModel(newProvider);
    if (defaultModel) {
      setSelectedModel(defaultModel);
    }
  };

  const handleVoiceProviderChange = (newVoiceProvider: string) => {
    setSelectedVoiceProvider(newVoiceProvider);
    setVoiceProviderSelectedInEdit(true); // Collapse provider dropdown, expand voice dropdown
    setVoiceSelectedInEdit(false); // Reset voice selection state when provider changes
    // Auto-select default voice for new provider
    const defaultVoice = defaultVoices[newVoiceProvider];
    if (defaultVoice) {
      setSelectedVoice(defaultVoice);
    }
  };

  const handleSaveModel = async () => {
    try {
      await onUpdateConfig(qube.qube_id, { ai_model: selectedModel });

      // Auto-switch evaluation model to Mistral 7B if main model is now Llama 3.2
      // and evaluation model is also Llama 3.2 (to avoid evaluating with the same model)
      const currentEvalModel = (qube as any).evaluation_model || 'llama3.2';
      if (selectedModel === 'llama3.2' && currentEvalModel === 'llama3.2') {
        await onUpdateConfig(qube.qube_id, { evaluation_model: 'mistral:7b' });
      }

      setIsEditingModel(false);

      // Emit events for Debug Inspector and other components
      emit('qube-model-changed', { qubeId: qube.qube_id, newModel: selectedModel });
      emit('qube-settings-changed', { qubeId: qube.qube_id });
    } catch (error) {
      console.error('Failed to update model:', error);
      alert(`Failed to update model: ${error}`);
    }
  };

  const handleSaveVoice = async () => {
    console.log('[VOICE_DEBUG] handleSaveVoice called', { qubeId: qube.qube_id, selectedVoice, currentVoiceModel: qube.voice_model });
    try {
      console.log('[VOICE_DEBUG] Calling onUpdateConfig...', { voice_model: selectedVoice });
      await onUpdateConfig(qube.qube_id, { voice_model: selectedVoice });
      console.log('[VOICE_DEBUG] onUpdateConfig completed successfully');
      setIsEditingVoice(false);
      // Emit event for Debug Inspector
      emit('qube-settings-changed', { qubeId: qube.qube_id });
    } catch (error) {
      console.error('[VOICE_DEBUG] Failed to update voice:', error);
      alert(`Failed to update voice: ${error}`);
    }
  };

  const handleSaveColor = async () => {
    try {
      await onUpdateConfig(qube.qube_id, { favorite_color: selectedColor });
      setIsEditingColor(false);
      // Emit event for Debug Inspector (favorite_color is in system prompt)
      emit('qube-settings-changed', { qubeId: qube.qube_id });
    } catch (error) {
      console.error('Failed to update color:', error);
      alert(`Failed to update color: ${error}`);
    }
  };

  const handleEvalProviderChange = (newProvider: string) => {
    setSelectedEvalProvider(newProvider);
    // Auto-select default model for new provider
    const defaultModel = getDefaultModel(newProvider);
    if (defaultModel) {
      setSelectedEvalModel(defaultModel);
    }
  };

  const handleSaveEvalModel = async () => {
    try {
      await onUpdateConfig(qube.qube_id, { evaluation_model: selectedEvalModel });
      setIsEditingEvalModel(false);
    } catch (error) {
      console.error('Failed to update evaluation model:', error);
      alert(`Failed to update evaluation model: ${error}`);
    }
  };

  const statusColors = {
    active: 'bg-[#00ff88]', // Neon dark green
    inactive: 'bg-[#ff3366]', // Neon red
    busy: 'bg-accent-warning',
  };

  // Format birth timestamp as readable date
  const birthDate = qube.birth_timestamp
    ? new Date(qube.birth_timestamp * 1000).toLocaleDateString()
    : 'Unknown';

  const handleToggleTTS = async () => {
    try {
      const newTTSState = !qube.tts_enabled;
      await onUpdateConfig(qube.qube_id, { tts_enabled: newTTSState });
      // Emit event for Debug Inspector
      emit('qube-settings-changed', { qubeId: qube.qube_id });
    } catch (error) {
      console.error('Failed to toggle TTS:', error);
      alert(`Failed to toggle TTS: ${error}`);
    }
  };

  return (
    <div className="relative w-full" style={{ padding: '8px', margin: '-8px' }}>
      {/* Selection outline - outside overflow-hidden so it's not clipped */}
      <div
        className="absolute inset-2 rounded-xl pointer-events-none"
        style={{
          outline: isSelected ? `3px solid ${qube.favorite_color || '#00ff88'}` : undefined,
          outlineOffset: '2px',
          boxShadow: isSelected ? `0 0 25px ${qube.favorite_color || '#00ff88'}70` : undefined,
          zIndex: isSelected ? 5 : undefined,
        }}
      />
      <div className="relative w-full overflow-hidden rounded-xl" style={{ height: '600px' }}>
        <div
          className="relative w-full h-full"
          style={{
            transition: 'transform 0.2s ease-in-out',
            transform: `scaleX(${flipScale})`,
          }}
        >
        {/* FRONT SIDE */}
        <div
          className="absolute w-full h-full"
          style={{
            opacity: flipState === 0 ? 1 : 0,
            pointerEvents: flipState === 0 ? 'auto' : 'none',
            transition: 'opacity 0.3s ease-in-out',
            zIndex: flipState === 0 ? 2 : 1,
          }}
        >
          <GlassCard
            variant="interactive"
            className="p-6 relative h-full flex flex-col cursor-pointer"
            onClick={(e: React.MouseEvent) => {
              // Toggle selection on card click
              toggleSelection(qube.qube_id, e.ctrlKey || e.metaKey, e.shiftKey);
            }}
            style={{
              // Use box-shadows for mode inner border and glows
              // Manual = no outline, Revolver = green, Autonomous = purple
              boxShadow: [
                // Inner border for mode (inset shadow)
                revolverMode ? 'inset 0 0 0 4px rgba(34, 197, 94, 0.8)' : null,  // green-500
                autonomousMode ? 'inset 0 0 0 4px rgba(168, 85, 247, 0.8)' : null,  // purple-500
                // Glow effects for mode
                revolverMode ? '0 0 20px rgba(34, 197, 94, 0.5)' : null,
                autonomousMode ? '0 0 20px rgba(168, 85, 247, 0.5)' : null,
              ].filter(Boolean).join(', ') || undefined,
            }}
          >
            {/* TTS Button - Top Left Corner */}
            <button
              onClick={(e) => { e.stopPropagation(); handleToggleTTS(); }}
              className="absolute top-3 left-3 text-2xl hover:scale-110 transition-transform cursor-pointer z-10"
              title={qube.tts_enabled ? "TTS Enabled - Click to disable" : "TTS Disabled - Click to enable"}
            >
              {qube.tts_enabled ? '🔊' : '🔇'}
            </button>

            {/* Settings Gear - Top Right Corner */}
            <button
              onClick={(e) => { e.stopPropagation(); setShowSettingsModal(true); }}
              className={`absolute top-3 right-3 text-2xl hover:scale-110 transition-all cursor-pointer z-10 ${
                (modelLocked || revolverMode) ? 'drop-shadow-[0_0_6px_rgba(255,255,255,0.6)]' : 'opacity-60 hover:opacity-100'
              }`}
              title="Model settings (Lock / Revolver mode)"
            >
              ⚙️
            </button>

            {/* Hidden file input for avatar change */}
            <input
              ref={avatarInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={handleAvatarFileSelect}
            />

            {/* Avatar - Click to flip, drag to reorder */}
            <div className="flex flex-col items-center mb-4">
              <div
                {...dragHandleProps}
                onClick={handleFlip}
                className="cursor-pointer mb-3 hover:scale-105 transition-transform"
                title="Click to flip • Drag to reorder"
              >
                <img
                  src={`${getAvatarPath(qube)}?v=${avatarCacheBust}`}
                  alt={`${qube.name} avatar`}
                  className="w-48 h-48 rounded-xl object-cover shadow-lg"
                  style={{
                    border: `2px solid ${qube.favorite_color || '#00ff88'}`,
                    boxShadow: `0 0 15px ${qube.favorite_color || '#00ff88'}40`,
                  }}
                  onError={(e) => {
                    const target = e.target as HTMLImageElement;
                    target.style.display = 'none';
                    const fallback = target.nextElementSibling as HTMLElement;
                    if (fallback) fallback.style.display = 'flex';
                  }}
                />
                <div
                  className="w-48 h-48 rounded-xl flex items-center justify-center text-8xl font-display font-bold shadow-lg"
                  style={{
                    background: `linear-gradient(135deg, ${qube.favorite_color || '#00ff88'}40, ${qube.favorite_color || '#00ff88'}20)`,
                    color: qube.favorite_color || '#00ff88',
                    border: `2px solid ${qube.favorite_color || '#00ff88'}`,
                    boxShadow: `0 0 15px ${qube.favorite_color || '#00ff88'}40`,
                    display: 'none',
                  }}
                >
                  {qube.name.charAt(0).toUpperCase()}
                </div>
              </div>

              <h3 className="text-2xl font-bold text-text-primary text-center mb-1">
                {qube.name}
              </h3>
              <p className="text-base text-text-tertiary font-mono mb-1">{qube.qube_id}</p>
            </div>

      {/* Stats */}
      <div className="space-y-2 mb-4 text-sm flex-1">
        {/* Blockchain */}
        {qube.home_blockchain && (
          <div className="flex justify-between items-center">
            <span className="text-text-tertiary">Blockchain:</span>
            <span className="text-text-primary font-medium flex items-center gap-1.5">
              {qube.home_blockchain === 'bitcoincash' && (
                <img
                  src="/bitcoin_cash_logo.svg"
                  alt="BCH"
                  className="w-4 h-4"
                />
              )}
              {qube.home_blockchain === 'bitcoincash' ? 'Bitcoin Cash' : qube.home_blockchain}
            </span>
          </div>
        )}

        {/* Model - Editable */}
        <div className="flex justify-between items-start gap-2">
          <span className="text-text-tertiary mt-1">Main Model:</span>
          {isEditingModel ? (
            <div className="flex flex-col gap-1 flex-1">
              <div className="flex gap-1 items-center">
                <DarkSelect
                  value={selectedProvider}
                  onChange={(v) => handleProviderChange(v)}
                  options={availableProviders}
                  expanded={!providerSelectedInEdit}
                  maxVisible={6}
                  className="flex-1"
                />
              </div>
              <div className="flex gap-1 items-center">
                <DarkSelect
                  value={selectedModel}
                  onChange={(v) => { setSelectedModel(v); setModelSelectedInEdit(true); }}
                  options={getModelsForProvider(selectedProvider)}
                  expanded={providerSelectedInEdit && !modelSelectedInEdit}
                  maxVisible={8}
                  className="flex-1"
                />
                <button
                  onClick={handleSaveModel}
                  className="px-2 py-1 bg-accent-success/20 text-accent-success rounded hover:bg-accent-success/30 transition-all"
                  title="Save"
                >
                  ✓
                </button>
                <button
                  onClick={() => {
                    setSelectedModel(qube.ai_model);
                    setSelectedProvider(qube.ai_provider);
                    setProviderSelectedInEdit(false);
                    setModelSelectedInEdit(false);
                    setIsEditingModel(false);
                  }}
                  className="px-2 py-1 bg-accent-danger/20 text-accent-danger rounded hover:bg-accent-danger/30 transition-all"
                  title="Cancel"
                >
                  ✕
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-1 items-center">
              <span className="text-text-primary font-medium">{formatModelName(qube.ai_model)}</span>
              <button
                onClick={(e) => { e.stopPropagation(); setProviderSelectedInEdit(false); setModelSelectedInEdit(false); setIsEditingModel(true); }}
                className="px-1 text-accent-primary hover:text-accent-primary/70 transition-colors"
                title="Edit model"
              >
                ✎
              </button>
            </div>
          )}
        </div>

        {/* Voice - Editable */}
        <div className="flex justify-between items-start gap-2">
          <span className="text-text-tertiary mt-1">Voice:</span>
          {isEditingVoice ? (
            <div className="flex flex-col gap-1 flex-1">
              <div className="flex gap-1 items-center">
                <DarkSelect
                  value={selectedVoiceProvider}
                  onChange={(v) => handleVoiceProviderChange(v)}
                  options={voiceProviderOptions}
                  expanded={!voiceProviderSelectedInEdit}
                  maxVisible={6}
                  className="flex-1"
                />
              </div>
              <div className="flex gap-1 items-center">
                <DarkSelect
                  value={selectedVoice}
                  onChange={(v) => {
                    console.log('[VOICE_DEBUG] Voice dropdown changed:', v);
                    setSelectedVoice(v);
                    setVoiceSelectedInEdit(true);
                  }}
                  options={voiceOptionsWithCustom[selectedVoiceProvider] || []}
                  expanded={voiceProviderSelectedInEdit && !voiceSelectedInEdit}
                  maxVisible={10}
                  className="flex-1"
                />
                <button
                  onClick={handleSaveVoice}
                  className="px-2 py-1 bg-accent-success/20 text-accent-success rounded hover:bg-accent-success/30 transition-all"
                  title="Save"
                >
                  ✓
                </button>
                <button
                  onClick={() => {
                    setSelectedVoice(qube.voice_model || '');
                    // Reset voice provider when canceling
                    const voiceModel = qube.voice_model || '';
                    setSelectedVoiceProvider(voiceModel.includes(':') ? voiceModel.split(':')[0] : 'openai');
                    setVoiceProviderSelectedInEdit(false);
                    setVoiceSelectedInEdit(false);
                    setIsEditingVoice(false);
                  }}
                  className="px-2 py-1 bg-accent-danger/20 text-accent-danger rounded hover:bg-accent-danger/30 transition-all"
                  title="Cancel"
                >
                  ✕
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-1 items-center">
              <span className="text-text-primary font-medium">{formatVoiceName(qube.voice_model || '')}</span>
              <button
                onClick={() => { setVoiceProviderSelectedInEdit(false); setVoiceSelectedInEdit(false); setIsEditingVoice(true); }}
                className="px-1 text-accent-primary hover:text-accent-primary/70 transition-colors"
                title="Edit voice"
              >
                ✎
              </button>
            </div>
          )}
        </div>

        {/* Color - Editable */}
        <div className="flex justify-between items-center">
          <span className="text-text-tertiary">Color:</span>
          {isEditingColor ? (
            <div className="flex gap-1 items-center">
              <input
                type="color"
                value={selectedColor}
                onChange={(e) => setSelectedColor(e.target.value)}
                className="w-8 h-8 rounded cursor-pointer border border-glass-border"
              />
              <input
                type="text"
                value={selectedColor}
                onChange={(e) => setSelectedColor(e.target.value)}
                className="px-2 py-1 bg-bg-tertiary border border-glass-border rounded text-xs text-text-primary font-mono w-20 focus:outline-none focus:ring-1 focus:ring-accent-primary/50"
                placeholder="#00ff88"
              />
              <button
                onClick={handleSaveColor}
                className="px-2 py-1 bg-accent-success/20 text-accent-success rounded hover:bg-accent-success/30 transition-all"
                title="Save"
              >
                ✓
              </button>
              <button
                onClick={() => {
                  setSelectedColor(qube.favorite_color);
                  setIsEditingColor(false);
                }}
                className="px-2 py-1 bg-accent-danger/20 text-accent-danger rounded hover:bg-accent-danger/30 transition-all"
                title="Cancel"
              >
                ✕
              </button>
            </div>
          ) : (
            <div className="flex gap-1 items-center">
              <div
                className="w-3 h-3 rounded-full"
                style={{ backgroundColor: qube.favorite_color }}
              />
              <span className="text-text-primary font-medium text-sm font-mono">{qube.favorite_color}</span>
              <button
                onClick={() => setIsEditingColor(true)}
                className="px-1 text-accent-primary hover:text-accent-primary/70 transition-colors"
                title="Edit color"
              >
                ✎
              </button>
            </div>
          )}
        </div>

        {/* Evaluation Model - Editable */}
        <div className="flex justify-between items-start gap-2">
          <span className="text-text-tertiary mt-1">Evaluation Model:</span>
          {isEditingEvalModel ? (
            <div className="flex flex-col gap-1 flex-1">
              <div className="flex gap-1 items-center">
                <DarkSelect
                  value={selectedEvalProvider}
                  onChange={(v) => handleEvalProviderChange(v)}
                  options={availableProviders}
                  className="flex-1"
                />
              </div>
              <div className="flex gap-1 items-center">
                <DarkSelect
                  value={selectedEvalModel}
                  onChange={(v) => setSelectedEvalModel(v)}
                  options={getModelsForProvider(selectedEvalProvider)}
                  expanded
                  maxVisible={8}
                  className="flex-1"
                />
                <button
                  onClick={handleSaveEvalModel}
                  className="px-2 py-1 bg-accent-success/20 text-accent-success rounded hover:bg-accent-success/30 transition-all"
                  title="Save"
                >
                  ✓
                </button>
                <button
                  onClick={() => {
                    setSelectedEvalModel((qube as any).evaluation_model || 'llama3.2');
                    setSelectedEvalProvider(inferProvider((qube as any).evaluation_model || 'llama3.2'));
                    setIsEditingEvalModel(false);
                  }}
                  className="px-2 py-1 bg-accent-danger/20 text-accent-danger rounded hover:bg-accent-danger/30 transition-all"
                  title="Cancel"
                >
                  ✕
                </button>
              </div>
            </div>
          ) : (
            <div className="flex gap-1 items-center">
              <span className="text-text-primary font-medium">{formatModelName((qube as any).evaluation_model || 'llama3.2')}</span>
              <button
                onClick={() => setIsEditingEvalModel(true)}
                className="px-1 text-accent-primary hover:text-accent-primary/70 transition-colors"
                title="Edit evaluation model"
              >
                ✎
              </button>
            </div>
          )}
        </div>

        {qube.creator && (
          <div className="flex justify-between">
            <span className="text-text-tertiary">Creator:</span>
            <span className="text-text-primary font-medium">{qube.creator}</span>
          </div>
        )}
        {qube.memory_blocks_count !== undefined && (
          <div className="flex justify-between">
            <span className="text-text-tertiary">Blocks:</span>
            <span className="text-text-primary font-medium">
              {qube.memory_blocks_count.toLocaleString()}
            </span>
          </div>
        )}

      </div>

            {/* Actions */}
            <div className="flex gap-2 flex-shrink-0 mt-auto">
              <button
                onClick={(e) => { e.stopPropagation(); onSelect(); }}
                className="flex-1 px-4 py-2 bg-accent-primary/10 text-accent-primary rounded-lg hover:bg-accent-primary/20 transition-all text-sm font-medium"
              >
                Chat
              </button>
              {onReset && (
                <button
                  onClick={(e) => { e.stopPropagation(); onReset(); }}
                  className="flex-1 px-4 py-2 bg-accent-warning/10 text-accent-warning rounded-lg hover:bg-accent-warning/20 transition-all text-sm font-medium"
                >
                  Reset
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); setShowQubeIpfsModal(true); }}
                className="px-2 py-1 text-xs rounded border border-accent-secondary/40 text-accent-secondary hover:bg-accent-secondary/10 transition-colors"
                title="Backup this Qube to IPFS"
              >
                ☁ IPFS
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(); }}
                className="flex-1 px-4 py-2 bg-accent-danger/10 text-accent-danger rounded-lg hover:bg-accent-danger/20 transition-all text-sm font-medium"
              >
                Delete
              </button>
            </div>
          </GlassCard>
        </div>

        {/* BLOCKCHAIN SIDE (flipState 1) */}
        <div
          className="absolute w-full h-full"
          style={{
            opacity: flipState === 1 ? 1 : 0,
            pointerEvents: flipState === 1 ? 'auto' : 'none',
            transition: 'opacity 0.3s ease-in-out',
            zIndex: flipState === 1 ? 2 : 1,
          }}
        >
          <GlassCard variant="interactive" className="p-6 relative h-full flex flex-col overflow-hidden">
            {/* Flip Button - Top Left Corner (same position as front) */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleFlip();
              }}
              className="absolute top-3 left-3 text-2xl hover:scale-110 transition-transform cursor-pointer z-10"
              title="Flip to relationship stats"
            >
              🔄
            </button>

            {/* Change Avatar button - Top Right on blockchain side */}
            <button
              onClick={(e) => { e.stopPropagation(); avatarInputRef.current?.click(); }}
              className="absolute top-3 right-3 text-xl hover:scale-110 transition-transform cursor-pointer z-10 opacity-60 hover:opacity-100"
              title="Change avatar photo"
            >
              📷
            </button>

            {/* Avatar crop modal */}
            {showAvatarCropper && avatarSrc && (
              <div
                className="fixed inset-0 z-50 flex items-center justify-center"
                style={{ background: 'rgba(0,0,0,0.85)' }}
                onClick={(e) => { if (e.target === e.currentTarget) setShowAvatarCropper(false); }}
              >
                <div className="bg-bg-secondary rounded-2xl p-6 w-96 flex flex-col gap-4" style={{ border: `1px solid ${qube.favorite_color || '#00ff88'}40` }}>
                  <h3 className="text-lg font-bold text-text-primary text-center">Crop Avatar</h3>
                  <div className="relative w-full rounded-xl overflow-hidden" style={{ height: 300 }}>
                    <Cropper
                      image={avatarSrc}
                      crop={crop}
                      zoom={zoom}
                      aspect={1}
                      cropShape="rect"
                      onCropChange={setCrop}
                      onZoomChange={setZoom}
                      onCropComplete={onCropComplete}
                    />
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-text-tertiary">Zoom</span>
                    <input
                      type="range" min={1} max={3} step={0.01} value={zoom}
                      onChange={(e) => setZoom(Number(e.target.value))}
                      className="flex-1"
                      style={{ accentColor: qube.favorite_color || '#00ff88' }}
                    />
                  </div>
                  <div className="flex gap-3">
                    <button
                      onClick={() => setShowAvatarCropper(false)}
                      className="flex-1 py-2 rounded-lg text-sm text-text-secondary border border-border-primary hover:bg-bg-tertiary transition-colors"
                    >Cancel</button>
                    <button
                      onClick={handleSaveAvatar}
                      disabled={savingAvatar}
                      className="flex-1 py-2 rounded-lg text-sm font-medium transition-colors"
                      style={{ background: qube.favorite_color || '#00ff88', color: '#000' }}
                    >{savingAvatar ? 'Saving...' : 'Save Photo'}</button>
                  </div>
                </div>
              </div>
            )}

            {/* Header */}
            <div className="text-center mb-3">
              <h3 className="text-xl font-bold text-text-primary">{qube.name}</h3>
              <p className="text-xs text-text-tertiary">Blockchain Data</p>
            </div>

            {/* Wallet Balance */}
            {qube.wallet_address && (
              <div className="mb-3">
                <div
                  className="p-2 rounded-lg text-center relative overflow-hidden"
                  style={{
                    backgroundColor: `${qube.favorite_color}15`,
                    border: `1px solid ${qube.favorite_color}40`,
                    boxShadow: `0 0 15px ${qube.favorite_color}20`
                  }}
                >
                  <span className="text-text-tertiary text-[10px] block mb-0.5 uppercase tracking-wide">Wallet</span>
                  {walletBalanceLoading ? (
                    <span className="text-text-tertiary text-sm animate-pulse">...</span>
                  ) : (
                    <span
                      className="font-mono text-sm font-bold block"
                      style={{ color: qube.favorite_color, textShadow: `0 0 10px ${qube.favorite_color}60` }}
                    >
                      {formatBCH(walletBalance || 0)} BCH
                    </span>
                  )}
                </div>

                {/* Sweep button — active only when balance > 0 */}
                {!showSweepModal ? (
                  <button
                    onClick={() => { if ((walletBalance ?? 0) > 0) setShowSweepModal(true); }}
                    disabled={(walletBalance ?? 0) === 0 || walletBalanceLoading}
                    className={`w-full mt-1.5 py-1 rounded text-[10px] font-medium transition-colors ${
                      (walletBalance ?? 0) > 0
                        ? 'bg-accent-primary/20 text-accent-primary hover:bg-accent-primary/30 cursor-pointer'
                        : 'bg-glass-bg text-text-disabled cursor-not-allowed'
                    }`}
                  >
                    ↓ Sweep Funds
                  </button>
                ) : (
                  <div className="mt-2 p-2 rounded-lg bg-bg-primary/60 border border-glass-border text-xs">
                    <input
                      type="text"
                      value={sweepAddress}
                      onChange={(e) => setSweepAddress(e.target.value)}
                      placeholder="bitcoincash:q..."
                      className="w-full bg-bg-primary border border-glass-border rounded px-2 py-1 text-text-primary placeholder:text-text-disabled font-mono text-[10px] mb-1 focus:outline-none"
                    />
                    <div className="flex gap-1">
                      <button
                        onClick={handleSweep}
                        disabled={!sweepAddress || isSweeping}
                        className="flex-1 py-0.5 rounded bg-accent-primary/20 text-accent-primary hover:bg-accent-primary/30 disabled:opacity-40 disabled:cursor-not-allowed font-medium"
                      >
                        {isSweeping ? 'Sweeping...' : 'Confirm Sweep'}
                      </button>
                      <button
                        onClick={() => { setShowSweepModal(false); setSweepAddress(''); setSweepResult(null); }}
                        className="px-2 py-0.5 rounded bg-glass-bg text-text-tertiary hover:text-text-primary"
                      >
                        ✕
                      </button>
                    </div>
                    {sweepResult && (
                      <p className={`mt-1 text-[10px] ${sweepResult.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>
                        {sweepResult}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Blockchain Data - Compact Two-Column Grid */}
            <div className="flex-1 overflow-y-auto pr-1 custom-scrollbar">
              {/* Basic Info Row */}
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs mb-2">
                {qube.home_blockchain && (
                  <>
                    <span className="text-text-tertiary">Chain:</span>
                    <span className="text-text-primary font-medium text-right">
                      {qube.home_blockchain === 'bitcoincash' ? 'BCH' : qube.home_blockchain}
                    </span>
                  </>
                )}
                {qube.birth_timestamp && (
                  <>
                    <span className="text-text-tertiary">Born:</span>
                    <span className="text-text-primary font-mono text-right">
                      {new Date(qube.birth_timestamp * 1000).toLocaleDateString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        year: '2-digit'
                      })}
                    </span>
                  </>
                )}
              </div>

              {/* Addresses Section */}
              {(qube.recipient_address || qube.wallet_address) && (
                <div className="mb-2 pt-2 border-t border-glass-border/30">
                  <div className="text-[10px] text-text-tertiary uppercase tracking-wider mb-1 flex items-center gap-1">
                    <span>📍</span> Addresses
                  </div>
                  <div className="space-y-0.5 text-xs">
                    {qube.recipient_address && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">NFT (z):</span>
                        <BlockchainLink value={qube.recipient_address} type="address" network={qube.network} />
                      </div>
                    )}
                    {qube.wallet_address && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">Wallet (p):</span>
                        <BlockchainLink value={qube.wallet_address} type="address" network={qube.network} />
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* NFT Identity Section */}
              {(qube.nft_category_id || qube.mint_txid || qube.commitment || qube.public_key) && (
                <div className="mb-2 pt-2 border-t border-glass-border/30">
                  <div className="text-[10px] text-text-tertiary uppercase tracking-wider mb-1 flex items-center gap-1">
                    <span>🎫</span> NFT Identity
                  </div>
                  <div className="space-y-0.5 text-xs">
                    {qube.nft_category_id && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">Category:</span>
                        <BlockchainLink value={qube.nft_category_id} type="hash" network={qube.network} />
                      </div>
                    )}
                    {qube.mint_txid && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">Mint TX:</span>
                        <BlockchainLink value={qube.mint_txid} type="transaction" network={qube.network} />
                      </div>
                    )}
                    {qube.commitment && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">Commit:</span>
                        <BlockchainLink value={qube.commitment} type="hash" network={qube.network} />
                      </div>
                    )}
                    {qube.public_key && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">Pubkey:</span>
                        <BlockchainLink value={qube.public_key} type="other" network={qube.network} />
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* IPFS Section */}
              {(qube.avatar_ipfs_cid || qube.bcmr_uri) && (
                <div className="pt-2 border-t border-glass-border/30">
                  <div className="text-[10px] text-text-tertiary uppercase tracking-wider mb-1 flex items-center gap-1">
                    <span>🌐</span> IPFS
                  </div>
                  <div className="space-y-0.5 text-xs">
                    {qube.avatar_ipfs_cid && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">Avatar:</span>
                        <BlockchainLink value={qube.avatar_ipfs_cid} type="ipfs" />
                      </div>
                    )}
                    {qube.bcmr_uri && (
                      <div className="flex justify-between items-center">
                        <span className="text-text-tertiary">BCMR:</span>
                        <BlockchainLink value={qube.bcmr_uri} type="ipfs" />
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Private Key Button - show if NFT address exists */}
              {qube.recipient_address && (
                <div className="pt-3 border-t border-glass-border/30">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setWalletSecurityModalOpen(true);
                    }}
                    className="w-full py-2.5 px-4 bg-accent-primary/10 hover:bg-accent-primary/20 border border-accent-primary/40 rounded-lg flex items-center justify-center gap-2 text-sm font-medium transition-all hover:shadow-lg hover:shadow-accent-primary/10"
                  >
                    <span>🔑</span>
                    <span className="text-accent-primary">
                      {hasStoredKey ? 'Private Key ✓' : 'Private Key'}
                    </span>
                  </button>
                </div>
              )}
            </div>

            {/* Flip hint */}
            <div className="text-center pt-2 border-t border-glass-border/50 flex-shrink-0">
              <p className="text-[10px] text-text-tertiary">
                Click 🔄 to see relationships
              </p>
            </div>
          </GlassCard>
        </div>

        {/* VISUALIZER SETTINGS SIDE (flipState 2) */}
        <div
          className="absolute w-full h-full"
          style={{
            opacity: flipState === 2 ? 1 : 0,
            pointerEvents: flipState === 2 ? 'auto' : 'none',
            transition: 'opacity 0.3s ease-in-out',
            zIndex: flipState === 2 ? 2 : 1,
          }}
        >
          <GlassCard variant="interactive" className="p-6 relative h-full flex flex-col overflow-hidden">
            {/* Flip Button - Top Left Corner (same position as front) */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleFlip();
              }}
              className="absolute top-3 left-3 text-2xl hover:scale-110 transition-transform cursor-pointer z-10"
              title="Flip to front"
            >
              🔄
            </button>

            {/* Visualizer Toggle - Top Right Corner */}
            <button
              onClick={async (e) => {
                e.stopPropagation();
                const newEnabled = !visualizerSettings.enabled;
                setVisualizerSettings({ ...visualizerSettings, enabled: newEnabled });

                // Save to backend immediately
                try {
                  await invoke('save_visualizer_settings', {
                    userId,
                    qubeId: qube.qube_id,
                    settings: JSON.stringify({ ...visualizerSettings, enabled: newEnabled }),
                    password: masterPassword
                  });
                  // Invalidate chain state cache and refresh
                  invalidateCache(qube.qube_id);
                  await loadChainState(qube.qube_id, true);
                } catch (error) {
                  console.error('Failed to save visualizer toggle:', error);
                }
              }}
              className="absolute top-3 right-3 text-2xl hover:scale-110 transition-transform cursor-pointer z-10"
              title={visualizerSettings.enabled ? "Visualizer Enabled (V to toggle, 1-9/0/- to switch styles)" : "Visualizer Disabled (V to toggle)"}
            >
              {visualizerSettings.enabled ? '🎵' : '🚫'}
            </button>

            {/* Header */}
            <div className="text-center mb-4">
              <h3 className="text-2xl font-bold text-text-primary mb-1">{qube.name}</h3>
              <p className="text-xs text-text-tertiary">Audio Visualizer Settings</p>
            </div>

            {/* Settings Form */}
            <div className="flex-1 overflow-y-auto mb-4 pr-2 space-y-4">
              {loadingVisualizerSettings ? (
                <div className="text-center text-text-tertiary text-sm py-8">Loading settings...</div>
              ) : (
                <>
                  {/* Waveform Style */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center gap-1.5">
                      🎨 Waveform Style (F1-F11)
                    </label>
                    <DarkSelect
                      value={String(visualizerSettings.waveform_style)}
                      onChange={(v) => setVisualizerSettings({ ...visualizerSettings, waveform_style: parseInt(v) as any })}
                      options={[
                        { value: '1', label: '1. Classic Bars' },
                        { value: '2', label: '2. Symmetric Bars' },
                        { value: '3', label: '3. Smooth Waveform' },
                        { value: '4', label: '4. Radial Spectrum' },
                        { value: '5', label: '5. Dot Matrix' },
                        { value: '6', label: '6. Polygon Morph' },
                        { value: '7', label: '7. Water Ripples' },
                        { value: '8', label: '8. Spectrum Rings' },
                        { value: '9', label: '9. VU Meters' },
                        { value: '10', label: '10. Avatar Glow' },
                        { value: '11', label: '11. Avatar Spectrum' },
                        { value: '12', label: '12. Avatar Cosmic' },
                      ]}
                    />
                  </div>

                  {/* Color Theme */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center gap-1.5">
                      🌈 Color Theme
                    </label>
                    <DarkSelect
                      value={visualizerSettings.color_theme}
                      onChange={(v) => setVisualizerSettings({ ...visualizerSettings, color_theme: v as any })}
                      options={[
                        { value: 'qube-color', label: 'Qube Color' },
                        { value: 'rainbow', label: 'Rainbow' },
                        { value: 'neon-cyan', label: 'Neon Cyan' },
                        { value: 'electric-purple', label: 'Electric Purple' },
                        { value: 'matrix-green', label: 'Matrix Green' },
                        { value: 'fire', label: 'Fire' },
                        { value: 'ice', label: 'Ice' },
                      ]}
                    />
                  </div>

                  {/* Gradient Style (only for qube-color) */}
                  {visualizerSettings.color_theme === 'qube-color' && (
                    <div>
                      <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center gap-1.5">
                        🎭 Gradient Style
                      </label>
                      <DarkSelect
                        value={visualizerSettings.gradient_style}
                        onChange={(v) => setVisualizerSettings({ ...visualizerSettings, gradient_style: v as any })}
                        options={[
                          { value: 'solid', label: 'Solid Color' },
                          { value: 'gradient-dark', label: 'Gradient to Dark' },
                          { value: 'gradient-complementary', label: 'Gradient to Complementary' },
                          { value: 'gradient-analogous', label: 'Gradient to Similar Colors' },
                        ]}
                      />
                    </div>
                  )}

                  {/* Sensitivity */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center justify-between">
                      <span className="flex items-center gap-1.5">🎚️ Sensitivity</span>
                      <span className="font-mono text-accent-primary">{visualizerSettings.sensitivity}%</span>
                    </label>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      value={visualizerSettings.sensitivity}
                      onChange={(e) => setVisualizerSettings({ ...visualizerSettings, sensitivity: parseInt(e.target.value) })}
                      className="w-full h-2 bg-white/20 rounded-lg appearance-none cursor-pointer"
                      style={{
                        accentColor: qube.favorite_color || '#00ff88'
                      }}
                    />
                  </div>

                  {/* Animation Smoothness */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center gap-1.5">
                      ⚡ Animation Smoothness
                    </label>
                    <DarkSelect
                      value={visualizerSettings.animation_smoothness}
                      onChange={(v) => setVisualizerSettings({ ...visualizerSettings, animation_smoothness: v as any })}
                      options={[
                        { value: 'low', label: 'Low (30fps - Better performance)' },
                        { value: 'medium', label: 'Medium (45fps - Balanced)' },
                        { value: 'high', label: 'High (60fps - Smooth)' },
                        { value: 'ultra', label: 'Ultra (60fps+ - Maximum quality)' },
                      ]}
                    />
                  </div>

                  {/* Audio Offset */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center justify-between">
                      <span className="flex items-center gap-1.5">⏱️ Audio Offset (Bluetooth sync)</span>
                      <span className="font-mono text-accent-primary">{visualizerSettings.audio_offset_ms > 0 ? '+' : ''}{visualizerSettings.audio_offset_ms}ms</span>
                    </label>
                    <input
                      type="range"
                      min="-500"
                      max="500"
                      step="10"
                      value={visualizerSettings.audio_offset_ms}
                      onChange={(e) => setVisualizerSettings({ ...visualizerSettings, audio_offset_ms: parseInt(e.target.value) })}
                      className="w-full h-2 bg-white/20 rounded-lg appearance-none cursor-pointer"
                      style={{
                        accentColor: qube.favorite_color || '#00ff88'
                      }}
                    />
                  </div>

                  {/* Frequency Range */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block flex items-center justify-between">
                      <span className="flex items-center gap-1.5">📊 Frequency Range</span>
                      <span className="font-mono text-accent-primary">{visualizerSettings.frequency_range}%</span>
                    </label>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      value={visualizerSettings.frequency_range}
                      onChange={(e) => setVisualizerSettings({ ...visualizerSettings, frequency_range: parseInt(e.target.value) })}
                      className="w-full h-2 bg-white/20 rounded-lg appearance-none cursor-pointer"
                      style={{
                        accentColor: qube.favorite_color || '#00ff88'
                      }}
                    />
                    <p className="text-[10px] text-text-tertiary mt-1">
                      Controls how much of the frequency spectrum to visualize (lower = bass/vocals, higher = full range)
                    </p>
                  </div>

                  {/* Output Monitor */}
                  <div>
                    <label className="text-xs font-semibold text-text-primary mb-2 block">
                      🖥️ Output Monitor
                    </label>
                    <DarkSelect
                      value={String(visualizerSettings.output_monitor)}
                      onChange={(v) => handleOutputMonitorChange(parseInt(v))}
                      options={[
                        { value: '0', label: 'Main Window (Overlay)' },
                        ...availableMonitors.map((monitor) => ({
                          value: String(monitor.id),
                          label: monitor.name,
                        })),
                      ]}
                    />
                    <p className="text-[10px] text-text-tertiary mt-1">
                      Choose where to display the visualizer (external monitor opens a dedicated fullscreen window)
                    </p>
                  </div>
                </>
              )}
            </div>

            {/* Action Buttons */}
            <div className="flex-shrink-0 border-t border-glass-border pt-3 flex gap-2">
              <button
                onClick={saveVisualizerSettings}
                disabled={savingVisualizerSettings || loadingVisualizerSettings}
                className="flex-1 px-4 py-2 rounded text-xs font-semibold bg-accent-primary/20 border border-accent-primary/40 text-accent-primary hover:bg-accent-primary/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {savingVisualizerSettings ? 'Saving...' : '💾 Save Settings'}
              </button>
              <button
                onClick={resetVisualizerSettings}
                disabled={savingVisualizerSettings || loadingVisualizerSettings}
                className="px-4 py-2 rounded text-xs font-semibold bg-white/5 border border-white/10 text-text-secondary hover:bg-white/10 hover:text-text-primary transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                title="Reset to defaults"
              >
                🔄 Reset
              </button>
            </div>
          </GlassCard>
        </div>
      </div>
      </div>

      {/* Toast Notification */}
      {toast && (
        <div
          className="fixed top-1/2 left-1/2 z-50 px-6 py-3 rounded-lg shadow-2xl border backdrop-blur-md"
          style={{
            backgroundColor: toast.type === 'success' ? 'rgba(0, 255, 136, 0.15)' : 'rgba(255, 51, 102, 0.15)',
            borderColor: toast.type === 'success' ? '#00ff88' : '#ff3366',
            opacity: toast.isExiting ? 0 : 1,
            transform: toast.isExiting
              ? 'translate(-50%, -50%) scale(0.95)'
              : 'translate(-50%, -50%) scale(1)',
            transition: 'opacity 0.2s ease-out, transform 0.2s ease-out'
          }}
        >
          <div className="flex items-center gap-2">
            <span className="text-lg">{toast.type === 'success' ? '✓' : '✕'}</span>
            <span
              className="font-medium text-sm"
              style={{
                color: toast.type === 'success' ? '#00ff88' : '#ff3366'
              }}
            >
              {toast.message}
            </span>
          </div>
        </div>
      )}

      {/* Wallet Security Modal - outside rotating container */}
      <WalletSecurityModal
        isOpen={walletSecurityModalOpen}
        qube={qube}
        qubes={allQubes}
        walletSecurity={walletSecurity}
        userId={userId || ''}
        password={masterPassword || ''}
        onClose={() => setWalletSecurityModalOpen(false)}
        onSave={handleWalletSecuritySave}
      />

      {/* Model Settings Modal */}
      <QubeSettingsModal
        isOpen={showSettingsModal}
        qubeId={qube.qube_id}
        qubeName={qube.name}
        currentModel={qube.ai_model}
        modelLocked={modelLocked}
        lockedToModel={lockedToModel}
        revolverMode={revolverMode}
        revolverModePool={revolverModePool}
        autonomousMode={autonomousMode}
        autonomousModePool={autonomousModePool}
        onClose={() => setShowSettingsModal(false)}
        onUpdate={(updates) => {
          setModelLocked(updates.modelLocked);
          setLockedToModel(updates.lockedToModel);
          setRevolverMode(updates.revolverMode);
          setRevolverModePool(updates.revolverModePool);
          setAutonomousMode(updates.autonomousMode);
          setAutonomousModePool(updates.autonomousModePool);
        }}
      />

      {/* Per-Qube IPFS Backup Modal */}
      {showQubeIpfsModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50" onClick={(e) => e.stopPropagation()}>
          <div className="bg-bg-secondary border border-glass-border rounded-xl p-6 w-full max-w-sm mx-4">
            <h3 className="text-lg font-bold text-text-primary mb-2">☁ Backup {qube.name} to IPFS</h3>
            <p className="text-text-secondary text-sm mb-1">
              Uploads this Qube to IPFS, encrypted with your master password.
            </p>
            <p className="text-text-tertiary text-xs mb-5">
              Stored as <code>qubes/{qube.name}_{qube.qube_id}.enc</code>
            </p>
            {isQubeIpfsSyncing && (
              <div className="mb-4 p-2 bg-accent-primary/10 border border-accent-primary/30 rounded text-accent-primary text-sm">
                Uploading to IPFS...
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowQubeIpfsModal(false)}
                className="px-3 py-1.5 rounded border border-glass-border text-text-secondary hover:text-text-primary text-sm transition-colors"
                disabled={isQubeIpfsSyncing}
              >
                Cancel
              </button>
              <button
                onClick={handleQubeIpfsBackup}
                disabled={isQubeIpfsSyncing}
                className="px-3 py-1.5 rounded bg-accent-secondary/20 border border-accent-secondary/50 text-accent-secondary hover:bg-accent-secondary/30 text-sm transition-colors disabled:opacity-50"
              >
                {isQubeIpfsSyncing ? 'Uploading...' : 'Backup to IPFS'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// Helper function to format model names (used by both components)
const formatModelDisplay = (modelId: string): string => {
  // Common model mappings
  const modelMap: Record<string, string> = {
    // OpenAI - GPT 5.x series
    'gpt-5.2': 'GPT-5.2',
    'gpt-5.2-chat-latest': 'GPT-5.2 Instant',
    'gpt-5.2-codex': 'GPT-5.2 Codex',
    'gpt-5.1': 'GPT-5.1',
    'gpt-5.1-chat-latest': 'GPT-5.1 Instant',
    'gpt-5-turbo': 'GPT-5 Turbo',
    'gpt-5': 'GPT-5',
    'gpt-5-mini': 'GPT-5 Mini',
    'gpt-4.1': 'GPT-4.1',
    'gpt-4.1-mini': 'GPT-4.1 Mini',
    'o4': 'GPT-O4',
    'o4-mini': 'GPT-O4 Mini',
    'o3-mini': 'GPT-O3 Mini',
    'o1': 'GPT-O1',
    'gpt-4o': 'GPT-4o',
    'gpt-4o-mini': 'GPT-4o Mini',
    // Anthropic
    'claude-sonnet-4-5-20250929': 'Claude Sonnet 4.5',
    'claude-opus-4-1-20250805': 'Claude Opus 4.1',
    'claude-opus-4-20250514': 'Claude Opus 4',
    'claude-sonnet-4-20250514': 'Claude Sonnet 4',
    'claude-3-7-sonnet-20250219': 'Claude 3.7 Sonnet',
    'claude-3-5-haiku-20241022': 'Claude 3.5 Haiku',
    'claude-3-haiku-20240307': 'Claude 3 Haiku',
    // Google - Gemini 3.x and 2.5
    'gemini-3-pro-preview': 'Gemini 3 Pro',
    'gemini-3-flash-preview': 'Gemini 3 Flash',
    'gemini-3-pro-image-preview': 'Gemini 3 Pro Vision',
    'gemini-2.5-pro': 'Gemini 2.5 Pro',
    'gemini-2.5-flash': 'Gemini 2.5 Flash',
    'gemini-2.5-flash-preview-09-2025': 'Gemini 2.5 Flash Preview',
    'gemini-2.5-flash-lite': 'Gemini 2.5 Flash Lite',
    'gemini-2.0-flash': 'Gemini 2.0 Flash',
    'gemini-1.5-pro': 'Gemini 1.5 Pro',
    // Perplexity
    'sonar': 'Sonar (Fast)',
    'sonar-pro': 'Sonar Pro',
    'sonar-reasoning': 'Sonar Reasoning',
    'sonar-reasoning-pro': 'Sonar Reasoning Pro',
    'sonar-deep-research': 'Sonar Deep Research',
    // DeepSeek
    'deepseek-chat': 'DeepSeek Chat (V3.2)',
    'deepseek-reasoner': 'DeepSeek Reasoner (R1)',
    // Venice
    'venice-uncensored': 'Venice Uncensored',
    'llama-3.3-70b': 'Llama 3.3 70B',
    'llama-3.2-3b': 'Llama 3.2 3B',
    'qwen3-235b-a22b-instruct-2507': 'Qwen3 235B Instruct',
    'qwen3-235b-a22b-thinking-2507': 'Qwen3 235B Thinking',
    'qwen3-next-80b': 'Qwen3 Next 80B',
    'qwen3-coder-480b-a35b-instruct': 'Qwen3 Coder 480B',
    'qwen3-4b': 'Venice Small',
    'mistral-31-24b': 'Venice Medium',
    'claude-opus-45': 'Claude Opus 4.5 (Venice)',
    'openai-gpt-52': 'GPT-5.2 (Venice)',
    'openai-gpt-oss-120b': 'GPT OSS 120B (Venice)',
    'venice/gemini-3-pro': 'Gemini 3 Pro (Venice)',
    'venice/gemini-3-flash': 'Gemini 3 Flash (Venice)',
    'grok-41-fast': 'Grok 4.1 Fast',
    'grok-code-fast-1': 'Grok Code Fast 1',
    'zai-org-glm-4.7': 'GLM 4.7',
    'kimi-k2-thinking': 'Kimi K2 Thinking',
    'minimax-m21': 'MiniMax M2.1',
    'deepseek-v3.2': 'DeepSeek V3.2',
    'google-gemma-3-27b-it': 'Gemma 3 27B',
    'hermes-3-llama-3.1-405b': 'Hermes 3 405B',
    'dolphin-2.9.3-mistral-7b': 'Venice Uncensored',
    // NanoGPT
    'nanogpt/gpt-4o': 'GPT-4o (NanoGPT)',
    'nanogpt/gpt-4o-mini': 'GPT-4o Mini (NanoGPT)',
    'nanogpt/claude-3-5-sonnet': 'Claude 3.5 Sonnet (NanoGPT)',
    'nanogpt/claude-3-haiku': 'Claude 3 Haiku (NanoGPT)',
    'nanogpt/llama-3.1-70b': 'Llama 3.1 70B (NanoGPT)',
    'nanogpt/llama-3.1-8b': 'Llama 3.1 8B (NanoGPT)',
    'nanogpt/mistral-large': 'Mistral Large (NanoGPT)',
    'nanogpt/mixtral-8x7b': 'Mixtral 8x7B (NanoGPT)',
    // Ollama
    'llama3.3:70b': 'Llama 3.3 70B',
    'llama3.2': 'Llama 3.2',
    'llama3.2:1b': 'Llama 3.2 1B',
    'llama3.2:3b': 'Llama 3.2 3B',
    'llama3.2-vision:11b': 'Llama 3.2 Vision 11B',
    'llama3.2-vision:90b': 'Llama 3.2 Vision 90B',
    'qwen3:235b': 'Qwen 3 235B',
    'qwen3:30b': 'Qwen 3 30B',
    'qwen2.5:7b': 'Qwen 2.5 7B',
    'deepseek-r1:8b': 'DeepSeek R1 8B',
    'phi4:14b': 'Phi 4 14B',
    'gemma2:9b': 'Gemma 2 9B',
    'mistral:7b': 'Mistral 7B',
    'codellama:7b': 'CodeLlama 7B',
  };

  if (modelMap[modelId]) return modelMap[modelId];

  // Fallback formatting
  if (modelId.startsWith('llama')) {
    return modelId.split(':').map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
  }

  return modelId;
};

// Voice name aliases for cleaner display (e.g., "bm_george" → "George")
const VOICE_NAME_ALIASES: Record<string, string> = {
  // Kokoro voices
  'af_heart': 'Heart',
  'af_bella': 'Bella',
  'af_nova': 'Nova',
  'af_sarah': 'Sarah',
  'am_adam': 'Adam',
  'am_michael': 'Michael',
  'bf_emma': 'Emma',
  'bf_lily': 'Lily',
  'bm_george': 'George',
  'bm_daniel': 'Daniel',
  'jf_alpha': 'Alpha',
  'jm_kumo': 'Kumo',
  'zf_xiaoxiao': 'Xiaoxiao',
  'zm_yunxi': 'Yunxi',
  'ef_dora': 'Dora',
  'ff_siwis': 'Siwis',
  // Qwen3 voices
  'Vivian': 'Vivian',
  'Serena': 'Serena',
  'Dylan': 'Dylan',
  'Eric': 'Eric',
  'Ryan': 'Ryan',
  'Aiden': 'Aiden',
  'Ono_Anna': 'Ono Anna',
  'Uncle_Fu': 'Uncle Fu',
  'Sohee': 'Sohee',
};

// Helper function to format voice names (used by both components)
const formatVoiceDisplay = (voiceId: string): string => {
  if (!voiceId) return 'None';

  // Fallback: format "provider:voice" to "Provider: Voice"
  if (voiceId.includes(':')) {
    const [provider, voice] = voiceId.split(':');

    // Special case for custom voices
    if (provider === 'custom') {
      // Voice ID is the custom voice key - display with sparkle
      return `✨ Custom: ${voice}`;
    }

    // Use alias if available, otherwise capitalize
    const voiceName = VOICE_NAME_ALIASES[voice] || voice.charAt(0).toUpperCase() + voice.slice(1);

    // Special case for OpenAI
    const providerName = provider === 'openai' ? 'OpenAI' : provider.charAt(0).toUpperCase() + provider.slice(1);
    return `${providerName}: ${voiceName}`;
  }

  return voiceId;
};

// Sortable wrapper for QubeCard
const SortableQubeCard: React.FC<QubeCardProps> = (props) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.qube.qube_id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      <QubeCard {...props} dragHandleProps={{ ...attributes, ...listeners }} />
    </div>
  );
};

// Sortable wrapper for QubeListItem
const SortableQubeListItem: React.FC<QubeCardProps> = (props) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.qube.qube_id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      <QubeListItem {...props} dragHandleProps={{ ...attributes, ...listeners }} />
    </div>
  );
};

// Qube List Item Component (List View)
const QubeListItem: React.FC<QubeCardProps> = ({ qube, allQubes, onEdit, onDelete, onReset, onSelect, getAvatarPath, dragHandleProps, setCurrentTab, toggleSelection, isSelected = false }) => {
  const statusColors = {
    active: 'bg-[#00ff88]', // Neon dark green
    inactive: 'bg-[#ff3366]', // Neon red
    busy: 'bg-accent-warning',
  };

  const birthDate = qube.birth_timestamp
    ? new Date(qube.birth_timestamp * 1000).toLocaleDateString()
    : 'Unknown';

  return (
    <GlassCard
      variant="interactive"
      className="p-4 cursor-pointer"
      onClick={(e: React.MouseEvent) => {
        // Toggle selection on row click
        toggleSelection(qube.qube_id, e.ctrlKey || e.metaKey, e.shiftKey);
      }}
      style={{
        border: isSelected ? `3px solid ${qube.favorite_color || '#00ff88'}` : undefined,
        boxShadow: isSelected ? `0 0 20px ${qube.favorite_color || '#00ff88'}60` : undefined,
      }}
    >
      <div className="flex items-center gap-4">
        {/* Avatar */}
        <div
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing flex-shrink-0"
          title="Drag to reorder"
        >
          <img
            src={getAvatarPath(qube)}
            alt={`${qube.name} avatar`}
            className="w-16 h-16 rounded-xl object-cover shadow-lg"
            style={{
              border: `2px solid ${qube.favorite_color || '#00ff88'}`,
              boxShadow: `0 0 15px ${qube.favorite_color || '#00ff88'}40`,
            }}
            onError={(e) => {
              const target = e.target as HTMLImageElement;
              target.style.display = 'none';
              const fallback = target.nextElementSibling as HTMLElement;
              if (fallback) fallback.style.display = 'flex';
            }}
          />
          <div
            className="w-16 h-16 rounded-xl flex items-center justify-center text-2xl font-display font-bold shadow-lg"
            style={{
              background: `linear-gradient(135deg, ${qube.favorite_color || '#00ff88'}40, ${qube.favorite_color || '#00ff88'}20)`,
              color: qube.favorite_color || '#00ff88',
              border: `2px solid ${qube.favorite_color || '#00ff88'}`,
              boxShadow: `0 0 15px ${qube.favorite_color || '#00ff88'}40`,
              display: 'none',
            }}
          >
            {qube.name.charAt(0).toUpperCase()}
          </div>
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-bold text-text-primary mb-1">{qube.name}</h3>
          <p className="text-xs text-text-tertiary font-mono mb-1">{qube.qube_id}</p>
          <div className="flex gap-4 text-xs text-text-tertiary items-center">
            <span>Model: {formatModelDisplay(qube.ai_model)}</span>
            {qube.voice_model && <span>Voice: {formatVoiceDisplay(qube.voice_model)}</span>}
            <span className="flex items-center gap-1">
              Color:
              <span
                className="inline-block w-3 h-3 rounded-full"
                style={{ backgroundColor: qube.favorite_color || '#00ff88' }}
              />
              <span className="font-mono">{qube.favorite_color || '#00ff88'}</span>
            </span>
            {qube.creator && <span>By: {qube.creator}</span>}
          </div>
        </div>

        {/* Stats */}
        <div className="flex gap-6 text-sm">
          {qube.memory_blocks_count !== undefined && (
            <div className="text-center">
              <div className="text-text-tertiary text-xs mb-1">Blocks</div>
              <div className="text-text-primary font-medium">
                {qube.memory_blocks_count.toLocaleString()}
              </div>
            </div>
          )}
          {qube.home_blockchain && (
            <div className="text-center">
              <div className="text-text-tertiary text-xs mb-1">Blockchain</div>
              <div className="text-text-primary font-medium text-xs">
                {qube.home_blockchain === 'bitcoincash' ? 'Bitcoin Cash' : qube.home_blockchain}
              </div>
            </div>
          )}
          <div className="text-center">
            <div className="text-text-tertiary text-xs mb-1">Status</div>
            <div className="flex items-center gap-1">
              <div className={`w-2 h-2 rounded-full ${statusColors[qube.status]}`} />
              <span className="text-text-primary capitalize text-xs">{qube.status}</span>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex-shrink-0 flex gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); onSelect(); }}
            className="px-4 py-2 bg-accent-primary/10 text-accent-primary rounded-lg hover:bg-accent-primary/20 transition-all text-sm font-medium"
          >
            Chat
          </button>
          {onReset && (
            <button
              onClick={(e) => { e.stopPropagation(); onReset(); }}
              className="px-4 py-2 bg-accent-warning/10 text-accent-warning rounded-lg hover:bg-accent-warning/20 transition-all text-sm font-medium"
            >
              Reset
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="px-4 py-2 bg-accent-danger/10 text-accent-danger rounded-lg hover:bg-accent-danger/20 transition-all text-sm font-medium"
          >
            Delete
          </button>
        </div>
      </div>
    </GlassCard>
  );
};
