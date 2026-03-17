import { check } from '@tauri-apps/plugin-updater';
import { relaunch } from '@tauri-apps/plugin-process';
import { getVersion } from '@tauri-apps/api/app';

export interface UpdateStatus {
  available: boolean;
  currentVersion: string;
  newVersion?: string;
  error?: string;
}

export interface UpdateProgress {
  downloaded: number;
  total: number;
}

/**
 * Get the current app version
 */
async function getCurrentVersion(): Promise<string> {
  try {
    return await getVersion();
  } catch {
    return '0.1.0'; // Fallback
  }
}

/**
 * Check if an update is available
 */
export async function checkForUpdate(): Promise<UpdateStatus> {
  const currentVersion = await getCurrentVersion();

  try {
    const update = await check();

    if (update) {
      return {
        available: true,
        currentVersion: update.currentVersion,
        newVersion: update.version,
      };
    }

    return {
      available: false,
      currentVersion,
    };
  } catch (error) {
    console.error('Failed to check for updates:', error);
    return {
      available: false,
      currentVersion,
      error: error instanceof Error ? error.message : 'Failed to check for updates',
    };
  }
}

/**
 * Download and install an update
 * @param onProgress - Callback for download progress
 */
export async function downloadAndInstallUpdate(
  onProgress?: (progress: UpdateProgress) => void
): Promise<boolean> {
  try {
    const update = await check();

    if (!update) {
      return false;
    }

    // Track download progress
    let totalSize = 0;
    let downloadedSize = 0;

    // Download the update with progress tracking
    await update.downloadAndInstall((event) => {
      switch (event.event) {
        case 'Started':
          totalSize = (event.data as { contentLength?: number }).contentLength || 0;
          break;
        case 'Progress':
          downloadedSize += event.data.chunkLength;
          if (onProgress && totalSize > 0) {
            onProgress({
              downloaded: downloadedSize,
              total: totalSize,
            });
          }
          break;
        case 'Finished':
          break;
      }
    });

    await relaunch();

    return true;
  } catch (error) {
    console.error('Failed to download/install update:', error);
    throw error;
  }
}

/**
 * Check for updates silently on app startup
 * Returns update status (always includes currentVersion)
 */
export async function checkForUpdateSilently(): Promise<UpdateStatus | null> {
  try {
    const status = await checkForUpdate();
    // Always return status so we have currentVersion
    return status;
  } catch (error) {
    // Silently fail on startup check
    console.warn('Silent update check failed:', error);
    return null;
  }
}
