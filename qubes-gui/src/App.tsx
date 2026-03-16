import { useEffect, useState, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { listen } from '@tauri-apps/api/event';
import { QubeRoster } from './components/roster/QubeRoster';
import { TabBar } from './components/tabs/TabBar';
import { TabContent } from './components/tabs/TabContent';
import { LoginScreen } from './components/auth/LoginScreen';
import { AppImageErrorScreen } from './components/AppImageErrorScreen';
import { LockScreen } from './components/LockScreen';
import { ExitConfirmDialog } from './components/dialogs/ExitConfirmDialog';
import { PromptDebugModal } from './components/dialogs/PromptDebugModal';
import { SetupWizard } from './components/wizard';
import { useAuth } from './hooks/useAuth';
import { useAutoLock } from './hooks/useAutoLock';
import { AudioProvider } from './contexts/AudioContext';
import { ChainStateProvider } from './contexts/ChainStateContext';
import { VoiceLibraryProvider } from './contexts/VoiceLibraryContext';
import { CelebrationProvider } from './contexts/CelebrationContext';
import { WalletProvider } from './contexts/WalletContext';
import { CelebrationOverlay } from './components/celebrations';
import { Qube } from './types';
import { useWalletCache } from './hooks/useWalletCache';

// Check if we're in dev mode
const isDev = import.meta.env.DEV;

// Total balance widget displayed in the title bar
interface TotalBalanceWidgetProps {
  qubes: Qube[];
  userId: string;
  password: string;
}

function TotalBalanceWidget({ qubes, userId, password }: TotalBalanceWidgetProps) {
  const { wallets, setBalance } = useWalletCache();
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Sum all cached balances (in satoshis) — reactive to wallets store changes
  const totalSats = qubes.reduce((sum, qube) => {
    const data = wallets[qube.qube_id];
    return sum + (data?.balance ?? 0);
  }, 0);

  const hasAnyBalance = qubes.some((qube) => wallets[qube.qube_id]?.balance !== undefined);

  const handleRefresh = async () => {
    if (isRefreshing || !userId || !password) return;
    setIsRefreshing(true);
    try {
      const qubesWithWallet = qubes.filter((q) => q.wallet_address);
      await Promise.all(
        qubesWithWallet.map(async (qube) => {
          try {
            const result = await invoke<{ success?: boolean; balance_sats?: number }>('get_wallet_info', {
              userId,
              qubeId: qube.qube_id,
              password,
            });
            if (result.balance_sats !== undefined && result.balance_sats !== null) {
              setBalance(qube.qube_id, result.balance_sats);
            }
          } catch (err) {
            console.warn(`Failed to refresh balance for ${qube.qube_id}:`, err);
          }
        })
      );
    } finally {
      setIsRefreshing(false);
    }
  };

  const displayBalance = hasAnyBalance
    ? (totalSats / 1e8).toFixed(8) + ' BCH'
    : '-.-------- BCH';

  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className="text-text-tertiary font-mono">{displayBalance}</span>
      <button
        onClick={handleRefresh}
        disabled={isRefreshing}
        title="Refresh balances"
        className="text-text-tertiary hover:text-accent-primary transition-colors disabled:opacity-40"
      >
        {isRefreshing ? '⟳' : '↻'}
      </button>
    </div>
  );
}

interface AuthResponse {
  success: boolean;
  user_id?: string;
  data_dir?: string;
  error?: string;
}

interface FirstRunResponse {
  is_first_run: boolean;
  users: string[];
  legacy_data_dir?: string;
  legacy_users?: string[];
}

function App() {
  const { isAuthenticated, isLocked, userId, password, login, logout } = useAuth();

  // Initialize auto-lock (monitors activity and locks after timeout)
  useAutoLock();

  const [qubes, setQubes] = useState<Qube[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [showExitDialog, setShowExitDialog] = useState(false);
  const [allowClose, setAllowClose] = useState(false);
  const [isFirstRun, setIsFirstRun] = useState<boolean | null>(null);
  const [checkingFirstRun, setCheckingFirstRun] = useState(true);
  const [showDebugPrompt, setShowDebugPrompt] = useState(false);
  const [appImageError, setAppImageError] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [legacyMigration, setLegacyMigration] = useState<{ dataDir: string; users: string[] } | null>(null);
  const [migrating, setMigrating] = useState(false);

  // Dev-only keyboard shortcut for debug prompt modal (Ctrl+Shift+D)
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (isDev && e.ctrlKey && e.shiftKey && e.key === 'D') {
      e.preventDefault();
      setShowDebugPrompt(prev => !prev);
    }
  }, []);

  useEffect(() => {
    if (isDev && isAuthenticated) {
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }
  }, [isDev, isAuthenticated, handleKeyDown]);

  // Check for first run on mount
  useEffect(() => {
    const checkFirstRun = async () => {
      try {
        const response = await invoke<FirstRunResponse>('check_first_run');
        // Check for legacy data that needs migration
        if (response.is_first_run && response.legacy_data_dir && response.legacy_users?.length) {
          setLegacyMigration({ dataDir: response.legacy_data_dir, users: response.legacy_users });
        }
        setIsFirstRun(response.is_first_run);
      } catch (err) {
        console.error('Failed to check first run:', err);
        const errStr = String(err);
        if (errStr.includes('APPIMAGE_NO_BACKEND')) {
          setAppImageError(true);
        } else {
          // If check fails, assume first run (show wizard) - better to let user
          // create an account than to show login for a nonexistent account
          setIsFirstRun(true);
        }
      } finally {
        setCheckingFirstRun(false);
      }
    };
    checkFirstRun();
  }, []);

  // Load qubes and start TTS server after authentication
  useEffect(() => {
    if (isAuthenticated && userId) {
      loadQubes();

      // Start WSL2 TTS server in background (Windows only - WSL2 doesn't exist on Linux/macOS)
      if (navigator.platform === 'Win32') {
        invoke('start_wsl2_tts_server', { userId }).catch(() => {});
      }
    }
  }, [isAuthenticated, userId]);

  // Listen for model changes (from revolver mode, switch_model tool, etc.)
  // Updates the qube's ai_model and ai_provider so roster and other components show current model
  useEffect(() => {
    const setupModelChangeListener = async () => {
      const unlisten = await listen<{ qubeId: string; newModel: string; newProvider?: string }>(
        'qube-model-changed',
        (event) => {
          const { qubeId, newModel, newProvider } = event.payload;
          setQubes((prevQubes) =>
            prevQubes.map((qube) =>
              qube.qube_id === qubeId
                ? { ...qube, ai_model: newModel, ...(newProvider && { ai_provider: newProvider }) }
                : qube
            )
          );
        }
      );
      return unlisten;
    };

    const cleanupPromise = setupModelChangeListener();
    return () => {
      cleanupPromise.then((cleanup) => cleanup());
    };
  }, []);

  // Set up window close listener
  useEffect(() => {
    if (!isAuthenticated) return;

    const setupCloseListener = async () => {
      const appWindow = getCurrentWindow();

      const unlisten = await appWindow.onCloseRequested(async (event) => {
        // If we've processed the session, allow close
        if (allowClose) {
          return; // Don't prevent, let it close
        }

        // If locked, just exit immediately (no dialog needed)
        if (isLocked) {
          try {
            await invoke('force_exit');
          } catch (err) {
            console.error('force_exit failed:', err);
          }
          return;
        }

        // Prevent the window from closing
        event.preventDefault();

        // Check if any qube has session blocks
        let hasAnySessions = false;
        for (const qube of qubes) {
          try {
            const result = await invoke<{ has_session: boolean }>('check_sessions', {
              userId,
              qubeId: qube.qube_id
            });
            if (result.has_session) {
              hasAnySessions = true;
              break;
            }
          } catch (err) {
            console.error('Failed to check sessions for qube:', qube.qube_id, err);
          }
        }

        if (hasAnySessions) {
          // Show exit confirmation dialog
          setShowExitDialog(true);
        } else {
          // No sessions, just exit immediately
          try {
            await invoke('force_exit');
          } catch (err) {
            console.error('force_exit failed:', err);
          }
        }
      });

      return unlisten;
    };

    const unlistenPromise = setupCloseListener();

    return () => {
      unlistenPromise.then(unlisten => unlisten());
    };
  }, [isAuthenticated, isLocked, allowClose, qubes, userId]);

  const handleLogin = async (username: string, password: string) => {
    try {
      setLoginError(null);
      const response = await invoke<AuthResponse>('authenticate', { username, password });

      if (response.success && response.user_id && response.data_dir) {
        login(response.user_id, response.data_dir, password);
      } else {
        setLoginError(response.error || 'Authentication failed. Please try again.');
      }
    } catch (err) {
      console.error('Login error:', err);
      // Clean up the error message for display
      const errorStr = String(err);
      if (errorStr.includes('Invalid password') || errorStr.includes('User not found')) {
        setLoginError('Invalid username or password. Please try again.');
      } else if (errorStr.includes('Python bridge')) {
        setLoginError('Backend connection error. Please ensure the Python backend is configured correctly.');
      } else {
        setLoginError('Login failed. Please try again.');
      }
    }
  };

  const loadQubes = async () => {
    try {
      setLoading(true);
      setError(null);
      const qubeList = await invoke<Qube[]>('list_qubes', { userId, password });
      setQubes(qubeList);
    } catch (err) {
      console.error('Failed to load qubes:', err);
      setError(err as string);
    } finally {
      setLoading(false);
    }
  };


  // Handle wizard completion
  const handleWizardComplete = async (data: {
    userId: string;
    password: string;
    dataDir: string;
    apiKeys?: {
      openai?: string;
      anthropic?: string;
      google?: string;
      deepseek?: string;
      perplexity?: string;
      venice?: string;
      nanogpt?: string;
      pinata?: string;
    };
  }) => {
    // Save API keys if provided
    if (data.apiKeys) {
      const keyMapping: Record<string, string> = {
        openai: 'openai',
        anthropic: 'anthropic',
        google: 'google',
        deepseek: 'deepseek',
        perplexity: 'perplexity',
        venice: 'venice',
        nanogpt: 'nanogpt',
        pinata: 'pinata_jwt',  // Backend field is pinata_jwt
      };

      for (const [key, provider] of Object.entries(keyMapping)) {
        const apiKey = data.apiKeys[key as keyof typeof data.apiKeys];
        if (apiKey && apiKey.trim()) {
          try {
            await invoke('save_api_key', {
              userId: data.userId,
              provider,
              apiKey: apiKey.trim(),
              password: data.password,
            });
          } catch (err) {
            console.error(`Failed to save ${provider} API key:`, err);
            // Continue with other keys even if one fails
          }
        }
      }
    }

    setIsFirstRun(false);
    setShowWizard(false);
    // Log in the user with their credentials from the wizard
    login(data.userId, data.dataDir, data.password);
  };

  // Handle account reset (for corrupted accounts)
  const handleResetAccount = async (username: string) => {
    try {
      await invoke('delete_user_account', { userId: username });
      setLoginError(null);
    } catch (err) {
      console.error('Failed to reset account:', err);
      setLoginError('Failed to reset account. Please try again.');
    }
  };

  // Handle migrating all legacy users
  const handleMigrateLegacy = async () => {
    if (!legacyMigration) return;
    setMigrating(true);
    try {
      for (const user of legacyMigration.users) {
        await invoke('migrate_user_data', {
          oldDataDir: legacyMigration.dataDir,
          userId: user,
        });
      }
      // Re-check first run — should now find the migrated users
      const response = await invoke<FirstRunResponse>('check_first_run');
      setIsFirstRun(response.is_first_run);
      setLegacyMigration(null);
    } catch (err) {
      console.error('Migration failed:', err);
      setLoginError('Data migration failed: ' + String(err));
      setLegacyMigration(null);
    } finally {
      setMigrating(false);
    }
  };

  // Show loading while checking first run
  if (checkingFirstRun) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-bg-primary">
        <div className="text-accent-primary text-xl font-display">
          Starting Qubes...
        </div>
      </div>
    );
  }

  // Show AppImage error if backend is missing
  if (appImageError) {
    return <AppImageErrorScreen />;
  }

  // Show migration prompt if legacy data found
  if (legacyMigration && isFirstRun) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-bg-primary relative overflow-hidden">
        <div className="absolute inset-0 opacity-30">
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent-primary/20 rounded-full blur-3xl animate-pulse"></div>
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-accent-secondary/20 rounded-full blur-3xl animate-pulse" style={{ animationDelay: '1s' }}></div>
        </div>
        <div className="w-full max-w-lg p-8 relative z-10 bg-glass-bg border border-glass-border rounded-xl backdrop-blur-lg">
          <div className="text-center mb-6">
            <h1 className="text-3xl font-display text-accent-primary mb-2">QUBES</h1>
            <h2 className="text-xl font-display text-text-primary">Data Migration Required</h2>
          </div>
          <div className="space-y-3 text-text-secondary text-sm mb-6">
            <p>
              We found existing account data from a previous installation that needs to be migrated
              to the new data directory.
            </p>
            <div className="p-3 bg-bg-secondary rounded-lg">
              <p className="text-text-tertiary text-xs mb-1">Accounts found:</p>
              {legacyMigration.users.map(u => (
                <p key={u} className="text-accent-primary font-medium">{u}</p>
              ))}
            </div>
            <p className="text-text-tertiary text-xs">
              From: {legacyMigration.dataDir}
            </p>
          </div>
          <div className="space-y-3">
            <button
              onClick={handleMigrateLegacy}
              disabled={migrating}
              className="w-full py-3 rounded-lg bg-accent-primary text-bg-primary font-medium hover:bg-accent-primary/80 transition-colors disabled:opacity-50"
            >
              {migrating ? 'Migrating...' : 'Migrate My Data'}
            </button>
            <button
              onClick={() => { setLegacyMigration(null); }}
              disabled={migrating}
              className="w-full py-2 rounded-lg text-text-tertiary hover:text-text-secondary text-sm transition-colors"
            >
              Skip (start fresh)
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Show setup wizard when user clicks "Create Account" from login screen
  if (showWizard) {
    return (
      <SetupWizard
        onComplete={handleWizardComplete}
        onBackToLogin={() => setShowWizard(false)}
      />
    );
  }

  // Always show login screen when not authenticated (even on first run)
  if (!isAuthenticated) {
    return (
      <LoginScreen
        onLogin={handleLogin}
        onCreateAccount={() => setShowWizard(true)}
        onResetAccount={handleResetAccount}
        error={loginError}
      />
    );
  }

  if (loading) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-bg-primary">
        <div className="text-accent-primary text-xl font-display">
          Loading Qubes...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-bg-primary">
        <div className="max-w-2xl p-8">
          <h1 className="text-accent-danger text-2xl font-display mb-4">
            Connection Error
          </h1>
          <p className="text-text-secondary mb-4">
            Failed to connect to Python backend:
          </p>
          <pre className="bg-bg-secondary p-4 rounded text-text-tertiary text-sm overflow-auto">
            {error}
          </pre>
          <button
            onClick={loadQubes}
            className="mt-4 px-6 py-2 bg-accent-primary text-bg-primary rounded font-medium hover:bg-accent-primary/80"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const handleExitComplete = async () => {
    setShowExitDialog(false);

    // Use our custom force_exit command to immediately terminate
    try {
      await invoke('force_exit');
    } catch (err) {
      console.error('force_exit failed:', err);
    }
  };

  const handleExitCancel = () => {
    setShowExitDialog(false);
  };

  return (
    <AudioProvider>
      <WalletProvider>
      <ChainStateProvider>
      <VoiceLibraryProvider>
      <CelebrationProvider>
      {/* Lock Screen Overlay */}
      <LockScreen />

      {/* Celebration UI (XP toasts, level-ups, etc) */}
      <CelebrationOverlay />

      <div className={`h-screen w-screen flex flex-col overflow-hidden bg-bg-primary ${isLocked ? 'invisible' : ''}`}>
        {/* Title Bar */}
        <div className="h-8 flex items-center justify-between px-4 bg-bg-quaternary border-b border-glass-border">
          <span className="text-sm font-display text-accent-primary">QUBES ({qubes.length} loaded)</span>
          <div className="flex items-center gap-3 text-xs">
            <span className="text-accent-primary flex items-center gap-1">
              <span>👤</span>
              <span>{userId}</span>
            </span>
            <button
              onClick={logout}
              className="px-2 py-0.5 rounded bg-accent-danger/10 text-accent-danger hover:bg-accent-danger/20 transition-colors text-xs font-medium"
              title="Logout"
            >
              Logout
            </button>
          </div>
        </div>

        {/* Tab Bar */}
        <TabBar />

        {/* Main Content Area */}
        <div className="flex-1 flex overflow-hidden">
          {/* Persistent Roster Sidebar */}
          <QubeRoster qubes={qubes} />

          {/* Tab Content */}
          <TabContent qubes={qubes} setQubes={setQubes} onQubesChange={loadQubes} />
        </div>

        {/* Exit Confirmation Dialog */}
        {showExitDialog && (
          <ExitConfirmDialog
            userId={userId || ''}
            password={password || ''}
            qubes={qubes}
            onComplete={handleExitComplete}
            onCancel={handleExitCancel}
          />
        )}

        {/* Dev-only: AI Prompt Debug Modal (Ctrl+Shift+D) */}
        {isDev && (
          <PromptDebugModal
            isOpen={showDebugPrompt}
            onClose={() => setShowDebugPrompt(false)}
          />
        )}
      </div>
      </CelebrationProvider>
      </VoiceLibraryProvider>
      </ChainStateProvider>
      </WalletProvider>
    </AudioProvider>
  );
}

export default App;
