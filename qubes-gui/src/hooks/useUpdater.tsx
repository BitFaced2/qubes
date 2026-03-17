import { useState, useCallback, useEffect, useRef } from 'react';
import {
  checkForUpdate,
  downloadAndInstallUpdate,
  checkForUpdateSilently,
  UpdateStatus,
  UpdateProgress,
} from '../utils/updater';
import {
  isHeavyBundle,
  checkHeavyUpdate,
  getHeavyUpdateInfo,
  downloadAndInstallHeavyUpdate,
  formatBytes,
} from '../utils/heavy-updater';

export type HeavyUpdateStatus = 'idle' | 'downloading' | 'verifying' | 'installing' | 'restarting';

interface UseUpdaterReturn {
  // State
  updateAvailable: boolean;
  updateStatus: UpdateStatus | null;
  isChecking: boolean;
  isDownloading: boolean;
  downloadProgress: UpdateProgress | null;
  error: string | null;

  // Heavy bundle extras
  isHeavy: boolean;
  heavyStatus: HeavyUpdateStatus;
  updateSize: string | null; // Human-readable size (e.g. "680.2 MB")

  // Actions
  checkForUpdates: () => Promise<void>;
  installUpdate: () => Promise<void>;
  dismissUpdate: () => void;
}

export function useUpdater(checkOnMount: boolean = false): UseUpdaterReturn {
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [isChecking, setIsChecking] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<UpdateProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  // Heavy bundle state
  const [isHeavy, setIsHeavy] = useState(false);
  const [heavyStatus, setHeavyStatus] = useState<HeavyUpdateStatus>('idle');
  const [updateSize, setUpdateSize] = useState<string | null>(null);
  const heavyChecked = useRef(false);

  // Detect heavy bundle mode on mount
  useEffect(() => {
    if (!heavyChecked.current) {
      heavyChecked.current = true;
      isHeavyBundle().then(setIsHeavy);
    }
  }, []);

  // Check for updates (routes to appropriate updater)
  const checkForUpdates = useCallback(async () => {
    setIsChecking(true);
    setError(null);

    try {
      if (isHeavy) {
        // Heavy bundle: use custom updater
        const status = await checkHeavyUpdate();
        setUpdateStatus(status);
        setDismissed(false);

        // Fetch size info
        if (status.available) {
          const info = await getHeavyUpdateInfo();
          if (info?.size) {
            setUpdateSize(formatBytes(info.size));
          }
        }
      } else {
        // Light build: use Tauri updater
        const status = await checkForUpdate();
        setUpdateStatus(status);
        setDismissed(false);

        if (status.error) {
          setError(status.error);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check for updates');
    } finally {
      setIsChecking(false);
    }
  }, [isHeavy]);

  // Download and install update (routes to appropriate updater)
  const installUpdate = useCallback(async () => {
    if (!updateStatus?.available) return;

    setIsDownloading(true);
    setError(null);

    try {
      let success: boolean;
      if (isHeavy) {
        // Heavy bundle: custom download → verify → install → restart
        success = await downloadAndInstallHeavyUpdate(
          (progress) => {
            setDownloadProgress(progress);
          },
          (status) => {
            setHeavyStatus(status);
          },
        );
      } else {
        // Light build: Tauri updater
        success = await downloadAndInstallUpdate((progress) => {
          setDownloadProgress(progress);
        });
      }
      // If success=false (no update found at install time), reset state
      if (!success) {
        setError('Update download failed or no update available. Please try again.');
        setIsDownloading(false);
        setHeavyStatus('idle');
      }
      // If success=true, app will relaunch — we won't reach here
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to install update');
      setIsDownloading(false);
      setHeavyStatus('idle');
    }
  }, [updateStatus, isHeavy]);

  // Dismiss update notification
  const dismissUpdate = useCallback(() => {
    setDismissed(true);
  }, []);

  // Check on mount if requested (waits for heavy detection to complete)
  useEffect(() => {
    if (checkOnMount && heavyChecked.current) {
      const doCheck = async () => {
        if (isHeavy) {
          const status = await checkHeavyUpdate();
          setUpdateStatus(status);
          if (status.available) {
            const info = await getHeavyUpdateInfo();
            if (info?.size) {
              setUpdateSize(formatBytes(info.size));
            }
          }
        } else {
          const status = await checkForUpdateSilently();
          if (status) {
            setUpdateStatus(status);
          }
        }
      };
      doCheck();
    }
  }, [checkOnMount, isHeavy]);

  return {
    updateAvailable: updateStatus?.available === true && !dismissed,
    updateStatus,
    isChecking,
    isDownloading,
    downloadProgress,
    error,
    isHeavy,
    heavyStatus,
    updateSize,
    checkForUpdates,
    installUpdate,
    dismissUpdate,
  };
}
