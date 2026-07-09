import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { convertFileSrc } from '@tauri-apps/api/core';
import { emit, emitTo, listen } from '@tauri-apps/api/event';
import { open } from '@tauri-apps/plugin-dialog';
import { readFile, writeTextFile, BaseDirectory } from '@tauri-apps/plugin-fs';
import { GlassCard } from '../glass/GlassCard';
import { GlassButton } from '../glass/GlassButton';
import { Qube } from '../../types';
import { useAuth } from '../../hooks/useAuth';
import { useAudio } from '../../contexts/AudioContext';
import { useChainState } from '../../contexts/ChainStateContext';
import { useChatMessages, Message } from '../../hooks/useChatMessages';
import { useQubeSelection } from '../../hooks/useQubeSelection';
import { TypewriterText } from './TypewriterText';
import { ChatHeader } from './ChatHeader';
import { ToolCallBubble } from './ToolCallBubble';
import EmojiPicker, { EmojiClickData, Theme } from 'emoji-picker-react';
import { WaveformOverlay } from '../visualizer/WaveformOverlay';
// import { WSL2TTSStatusIndicator } from '../settings/WSL2TTSStatusIndicator';
import type { WaveformStyle, ColorTheme, GradientStyle, AnimationSmoothness } from '../../types';

interface ChatInterfaceProps {
  selectedQubes: Qube[];
  allQubes?: Qube[];
  onQubeModelChange?: (qubeId: string, newModel: string) => void;
}

interface ChatResponse {
  success: boolean;
  qube_id?: string;
  qube_name?: string;
  message?: string;
  response?: string;
  timestamp?: number;  // Unix timestamp in seconds from backend MESSAGE block
  block_number?: number;  // Session block number for ACTION block association
  current_model?: string;
  current_provider?: string;
  error?: string;
  streaming?: boolean;
  total_chunks?: number;
}

export const ChatInterface: React.FC<ChatInterfaceProps> = ({ selectedQubes, allQubes = [], onQubeModelChange }) => {
  const { userId, password } = useAuth();
  const { playTTS, audioElement, audioDataUrl, stopAudio, startStreamingPlayback, resetStreamingState, isPlaying, currentSentence } = useAudio();
  const { invalidateCache, loadChainState, startWatching, stopWatching } = useChainState();
  const { getMessages, addMessage, clearMessages, getUploadedFiles, addUploadedFile, removeUploadedFile, clearUploadedFiles } = useChatMessages();
  const currentTab = useQubeSelection(state => state.currentTab);
  const switchTabAndSelect = useQubeSelection(state => state.switchTabAndSelect);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const isSendingRef = useRef(false); // Ref guard for double-send (state closures are stale)
  const [processingStage, setProcessingStage] = useState<'document' | 'response' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failedMessage, setFailedMessage] = useState<string | null>(null); // Store message for retry
  const [lastResponseText, setLastResponseText] = useState<string>('');
  const [pendingResponse, setPendingResponse] = useState<{ qubeName: string; content: string; timestamp?: number; blockNumber?: number; streaming?: boolean } | null>(null);
  const [activeTypewriterMessageId, setActiveTypewriterMessageId] = useState<string | null>(null);
  const [currentModel, setCurrentModel] = useState<string | null>(null); // Local model state for header updates
  const [isRecording, setIsRecording] = useState(false);
  const [isGeneratingTTS, setIsGeneratingTTS] = useState(false);
  const [ttsProgress, setTtsProgress] = useState<{ stage: string; progress: number; message: string }>({
    stage: 'idle', progress: 0, message: ''
  });
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [activeToolCalls, setActiveToolCalls] = useState<Array<{
    action_type: string;
    timestamp: number;
    target_model?: string;  // For revolver_switch actions
  }>>([]);
  // Store completed action blocks for display with messages
  const [completedActionBlocks, setCompletedActionBlocks] = useState<Array<{
    action_type: string;
    timestamp: number;
    blockNumber: number;  // Sequence number for reliable association
    parameters: any;
    result: any;
    status: string;
    fromSession: boolean;  // true = current session, false = anchored/permanent
  }>>([]);
  const chatClearedAtRef = useRef<number>(0);  // Timestamp when chat was last cleared (Escape)
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);  // Scroll container for smart scroll
  const isUserAtBottomRef = useRef<boolean>(true);  // Track if user is at bottom (use ref to avoid re-renders)
  const recognitionRef = useRef<any>(null);
  const pendingTypewriterRef = useRef<string | null>(null); // Track message waiting for typewriter activation
  const processingResponseRef = useRef<string | null>(null); // Track which response is being processed (guards against double execution)
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const emojiPickerRef = useRef<HTMLDivElement>(null);
  const processedModelSwitches = useRef<Set<number>>(new Set()); // Track which switch_model actions we've already processed

  // Streaming TTS state
  const [streamingText, setStreamingText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const streamingMessageIdRef = useRef<string | null>(null);
  const streamingTextRef = useRef('');  // Accumulator (no re-render per token)
  const flushIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const currentSentenceRef = useRef<string | null>(null);  // For interval to read (avoids stale closure)
  const streamingBlockNumberRef = useRef<number | undefined>(undefined);  // Block number from invoke response

  // Stream Mode (continuous voice conversation)
  const [isStreamMode, setIsStreamMode] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [interimTranscript, setInterimTranscript] = useState('');
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const originalHandlersRef = useRef<{ onresult: any; onend: any; onerror: any } | null>(null);
  const isStreamModeRef = useRef(false);
  const isPlayingRef = useRef(false);
  const isLoadingRef = useRef(false);
  const [silenceTimeoutMs, setSilenceTimeoutMs] = useState(() =>
    parseInt(localStorage.getItem('stream_silence_timeout_ms') || '1500')
  );
  // Track when we first saw each tool call (by timestamp) for minimum display time
  const toolCallFirstSeen = useRef<Map<number, number>>(new Map()); // tool timestamp -> Date.now() when first seen
  const MIN_TOOL_DISPLAY_MS = 1000; // Show tool indicator for at least 1 second

  // Visualizer state
  const [visualizerSettings, setVisualizerSettings] = useState({
    enabled: false,
    waveform_style: 1 as WaveformStyle,
    color_theme: 'qube-color' as ColorTheme,
    gradient_style: 'gradient-dark' as GradientStyle,
    sensitivity: 50,
    animation_smoothness: 'medium' as AnimationSmoothness,
    audio_offset_ms: 0,
    frequency_range: 20,
    output_monitor: 0
  });
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);

  // Refs for latest values (to avoid stale closures)
  const visualizerSettingsRef = useRef(visualizerSettings);
  const selectedQubesRef = useRef(selectedQubes);
  const visualizerWindowOpenRef = useRef(false);

  // Keep refs updated
  useEffect(() => {
    visualizerSettingsRef.current = visualizerSettings;
  }, [visualizerSettings]);

  useEffect(() => {
    selectedQubesRef.current = selectedQubes;
  }, [selectedQubes]);

  // Listen for visualizer toggle requests from external window
  useEffect(() => {
    const setupListener = async () => {
      const unlisten = await listen('visualizer-toggle-request', () => {
        setVisualizerSettings(prev => {
          const newSettings = { ...prev, enabled: !prev.enabled };

          // Save to backend if we have a user and qube selected
          if (userId && selectedQubes.length > 0) {
            const qubeId = selectedQubes[0].qube_id;
            invoke('save_visualizer_settings', {
              userId,
              qubeId,
              settings: JSON.stringify(newSettings),
              password
            }).then(() => {
              // Invalidate and refresh chain state cache
              invalidateCache(qubeId);
              loadChainState(qubeId, true);
            }).catch(err => console.error('Failed to save visualizer settings:', err));
          }

          return newSettings;
        });
      });
      return unlisten;
    };

    const cleanupPromise = setupListener();
    return () => {
      cleanupPromise.then(cleanup => cleanup());
    };
  }, [userId, selectedQubes]);

  // Get messages and uploaded files for the selected qube
  const messages = selectedQubes.length > 0 ? getMessages(selectedQubes[0].qube_id) : [];
  const uploadedFiles = selectedQubes.length > 0 ? getUploadedFiles(selectedQubes[0].qube_id) : [];

  // Check if user is near the bottom of the scroll container (within 100px)
  const checkIfAtBottom = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return true;
    const threshold = 100; // pixels from bottom to consider "at bottom"
    const isAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
    return isAtBottom;
  }, []);

  // Handle scroll events to track user position
  const handleScroll = useCallback(() => {
    isUserAtBottomRef.current = checkIfAtBottom();
  }, [checkIfAtBottom]);

  // Scroll to bottom helper function - only scrolls if user is at bottom (smart scroll)
  const scrollToBottom = useCallback((force = false) => {
    if (force || isUserAtBottomRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, []);

  // Force scroll to bottom (e.g., when user sends a new message)
  const forceScrollToBottom = useCallback(() => {
    isUserAtBottomRef.current = true;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  // Helper function to save an image to disk
  const saveImageToDisk = async (imageUrl: string) => {
    if (!userId || selectedQubes.length === 0) return;

    try {
      await invoke('save_image', {
        userId: userId,
        qubeId: selectedQubes[0].qube_id,
        imageUrl: imageUrl
      });
    } catch (err) {
      console.error('Failed to save image:', err);
      // Don't show error to user - saving is optional background task
    }
  };

  // Handle file upload
  const handleFileUpload = async () => {
    if (selectedQubes.length === 0) return;

    try {
      const selected = await open({
        multiple: true, // Allow multiple file selection
        filters: [{
          name: 'Images and Documents',
          extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'txt', 'md', 'json', 'pdf']
        }]
      });

      if (!selected) return;

      // Handle both single file (string) and multiple files (array)
      const filePaths = Array.isArray(selected) ? selected : [selected];

      for (const filePath of filePaths) {
        // Read file as bytes
        const fileBytes = await readFile(filePath);

        // Get file extension to determine type
        const fileName = filePath.split(/[\\/]/).pop() || 'file';
        const extension = fileName.split('.').pop()?.toLowerCase() || '';

        // Determine file type
        const imageExtensions = ['png', 'jpg', 'jpeg', 'gif', 'webp'];
        const textExtensions = ['txt', 'md', 'json'];
        const pdfExtensions = ['pdf'];
        const isImage = imageExtensions.includes(extension);
        const isTextFile = textExtensions.includes(extension);
        const isPDF = pdfExtensions.includes(extension);

        let fileData;
        if (isImage) {
          // Use chunked conversion for better performance
          const uint8Array = new Uint8Array(fileBytes);
          const chunkSize = 8192; // Process 8KB at a time
          let binaryString = '';

          for (let i = 0; i < uint8Array.length; i += chunkSize) {
            const chunk = uint8Array.slice(i, i + chunkSize);
            binaryString += String.fromCharCode(...chunk);
          }

          fileData = {
            name: fileName,
            path: filePath,
            type: 'image' as const,
            data: `data:image/${extension};base64,${btoa(binaryString)}`
          };
        } else if (isTextFile) {
          // Text files can be decoded as UTF-8
          fileData = {
            name: fileName,
            path: filePath,
            type: 'text' as const,
            data: new TextDecoder().decode(fileBytes)
          };
        } else if (isPDF) {
          // PDF files - send as base64 for backend processing
          // Use chunked conversion for better performance with large files
          const uint8Array = new Uint8Array(fileBytes);
          const chunkSize = 8192; // Process 8KB at a time
          let binaryString = '';

          for (let i = 0; i < uint8Array.length; i += chunkSize) {
            const chunk = uint8Array.slice(i, i + chunkSize);
            binaryString += String.fromCharCode(...chunk);
          }

          fileData = {
            name: fileName,
            path: filePath,
            type: 'pdf' as const,
            data: btoa(binaryString)
          };
        } else {
          // Other binary files (not supported)
          // Use chunked conversion for better performance
          const uint8Array = new Uint8Array(fileBytes);
          const chunkSize = 8192; // Process 8KB at a time
          let binaryString = '';

          for (let i = 0; i < uint8Array.length; i += chunkSize) {
            const chunk = uint8Array.slice(i, i + chunkSize);
            binaryString += String.fromCharCode(...chunk);
          }

          fileData = {
            name: fileName,
            path: filePath,
            type: 'binary' as const,
            data: btoa(binaryString)
          };
        }

        addUploadedFile(selectedQubes[0].qube_id, fileData);
      }
    } catch (err) {
      console.error('Failed to upload file:', err);
      setError(`Failed to upload file: ${String(err)}`);
    }
  };

  // Helper function to clean content by removing image URLs, paths, and thinking blocks
  const cleanContentForDisplay = (content: string): string => {
    // Remove [Thinking: ...] blocks from models like Kimi K2
    // These can span multiple lines, so use [\s\S] to match any character including newlines
    let cleaned = content.replace(/\[Thinking:[\s\S]*?\]/gi, '');

    // Remove Gemini 3's internal thinking/planning blocks
    // Look for end-of-thinking markers and take content after them
    const endMarkers = [
      "*Let's do this.*",
      "*Let's do this*",
      "*Let's roll.*",
      "*Let's roll*",
      "*Here's my response:*",
      "*Here's my response*",
      "*Response:*",
      "*Responding now:*",
      "*Final response:*",
      "my response:",
    ];

    for (const marker of endMarkers) {
      const markerLower = marker.toLowerCase();
      const cleanedLower = cleaned.toLowerCase();
      if (cleanedLower.includes(markerLower)) {
        const idx = cleanedLower.indexOf(markerLower);
        cleaned = cleaned.substring(idx + marker.length).trim();
        break;
      }
    }

    // If content starts with planning patterns, try to find the actual response
    const trimmedLower = cleaned.trim().toLowerCase();
    if (trimmedLower.startsWith('my plan:') ||
        trimmedLower.startsWith('my thought process') ||
        trimmedLower.startsWith('plan:')) {
      // Look for common response starters after planning
      const lines = cleaned.split('\n');
      let responseStart = 0;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim().toLowerCase();
        // Skip planning-related lines
        if (line.startsWith('my plan:') ||
            line.startsWith('my thought') ||
            line.startsWith('plan:') ||
            line.startsWith('*self-correction') ||
            line.startsWith('*refining') ||
            line.startsWith('*let') ||
            line.startsWith('**key response') ||
            line.match(/^\d+\.\s+\*\*/) ||  // Numbered bold items
            line.startsWith('- ') ||
            (line.startsWith('*') && line.endsWith('*'))) {
          continue;
        }
        // Found a line that looks like actual response
        if (line.length > 20 && !line.includes('should') && !line.includes('will ') && !line.includes('need to')) {
          responseStart = i;
          break;
        }
        // Common response starters
        if (line.startsWith('whoa') || line.startsWith('okay') || line.startsWith('hey') ||
            line.startsWith('so,') || line.startsWith('oh') || line.startsWith('hmm') ||
            line.startsWith('well') || line.startsWith('alright')) {
          responseStart = i;
          break;
        }
      }

      if (responseStart > 0) {
        cleaned = lines.slice(responseStart).join('\n').trim();
      }
    }

    // Regular expression to detect image URLs (including DALL-E Azure Blob Storage URLs)
    const imageUrlRegex = /(https?:\/\/[^\s\)]+?(?:\.(?:png|jpg|jpeg|gif|webp)|blob\.core\.windows\.net\/[^\s\)]+))/gi;
    // Regular expression to detect local file paths (Windows and Unix)
    const localPathRegex = /([A-Za-z]:[\\\/][^\s\)]+\.(?:png|jpg|jpeg|gif|webp)|\/[^\s\)]+\.(?:png|jpg|jpeg|gif|webp))/gi;

    // Remove complete markdown image syntax ![...](url or path)
    cleaned = cleaned.replace(/!\[([^\]]*)\]\([^\)]+\)/gi, '');

    // Remove any remaining image URLs (standalone)
    cleaned = cleaned.replace(imageUrlRegex, '');

    // Remove any remaining local file paths (standalone)
    cleaned = cleaned.replace(localPathRegex, '');

    // Remove any standalone markdown image syntax ![...]
    cleaned = cleaned.replace(/!\[([^\]]*)\]/g, '');

    // Remove empty parentheses that might be left over
    cleaned = cleaned.replace(/\(\s*\)/g, '');

    // Clean up extra whitespace and newlines
    cleaned = cleaned.replace(/\n\n+/g, '\n\n').trim();

    return cleaned;
  };

  // --- Streaming TTS helpers ---

  // Track how many characters of the raw token stream have been "spoken"
  const charsSpokenRef = useRef(0);
  // Previous sentence (to detect transitions)
  const prevSentenceRef = useRef<string | null>(null);
  // Full text saved from tts-stream-end (for finalization after all audio plays)
  const streamEndFullTextRef = useRef<string | null>(null);

  const startStreamingText = useCallback(() => {
    streamingTextRef.current = '';
    charsSpokenRef.current = 0;
    prevSentenceRef.current = null;
    streamEndFullTextRef.current = null;
    cumulativeAudioTimeRef.current = 0;
    lastAudioTimeRef.current = 0;
    setStreamingText('');
    isStreamingRef.current = true;
    setIsStreaming(true);
  }, []);

  const stopStreamingText = useCallback(() => {
    if (flushIntervalRef.current) {
      clearInterval(flushIntervalRef.current);
      flushIntervalRef.current = null;
    }
  }, []);

  // Audio-synced text reveal: uses cumulative audio time to estimate
  // how much text has been spoken, then shows that portion of the raw token stream.
  // Simple and reliable — no sentence-to-position mapping needed.
  const cumulativeAudioTimeRef = useRef(0);
  const lastAudioTimeRef = useRef(0);

  useEffect(() => {
    if (!isStreaming) return;

    if (flushIntervalRef.current) {
      clearInterval(flushIntervalRef.current);
    }

    flushIntervalRef.current = setInterval(() => {
      const sentence = currentSentenceRef.current;
      const audio = audioElement;

      // When sentence changes, previous one is fully spoken
      if (sentence !== prevSentenceRef.current) {
        if (prevSentenceRef.current) {
          charsSpokenRef.current += prevSentenceRef.current.length + 1; // +1 for space
        }
        prevSentenceRef.current = sentence;
      }

      // Use streamEndFullText when available (correct text from backend)
      const fullText = streamEndFullTextRef.current;
      if (!fullText && !sentence) return;

      if (fullText) {
        // Full text available — reveal based on sentences spoken + audio progress
        if (!sentence || !audio) {
          // No audio playing — show up to what's been spoken
          if (charsSpokenRef.current > 0) {
            setStreamingText(cleanContentForDisplay(fullText.substring(0, charsSpokenRef.current)));
          }
        } else {
          // Audio playing — reveal with progress through current sentence
          const duration = audio.duration || 5;
          const currentTime = audio.currentTime || 0;
          const progress = Math.min(currentTime / duration, 1);
          const partialChars = Math.ceil(sentence.length * progress);
          const totalChars = charsSpokenRef.current + partialChars;
          setStreamingText(cleanContentForDisplay(fullText.substring(0, Math.min(totalChars, fullText.length))));
        }
        scrollToBottom();
      } else if (sentence && audio) {
        // Before stream-end: show sentence-by-sentence (original approach that worked)
        const duration = audio.duration || 5;
        const currentTime = audio.currentTime || 0;
        const progress = Math.min(currentTime / duration, 1);
        const charsToShow = Math.ceil(sentence.length * progress);
        // Build from completed + partial current
        const completedText = streamingTextRef.current.substring(0, charsSpokenRef.current);
        const partialSentence = sentence.substring(0, charsToShow);
        setStreamingText(completedText + partialSentence);
        scrollToBottom();
      }
    }, 50);

    return () => {
      if (flushIntervalRef.current) {
        clearInterval(flushIntervalRef.current);
        flushIntervalRef.current = null;
      }
    };
  }, [isStreaming, audioElement, scrollToBottom]);

  // Finalize message when ALL audio has finished playing (not when stream-end fires)
  // Uses a 1s delay to avoid premature finalization if audio is briefly interrupted
  // (e.g., screenshot tools, window focus changes, brief gap between chunks)
  const finalizationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isStreaming) {
      if (finalizationTimerRef.current) {
        clearTimeout(finalizationTimerRef.current);
        finalizationTimerRef.current = null;
      }
      return;
    }

    if (!isPlaying && streamEndFullTextRef.current) {
      // Audio stopped and we have full text — wait 1s before finalizing
      // (in case next chunk is about to start or audio was briefly interrupted)
      if (!finalizationTimerRef.current) {
        finalizationTimerRef.current = setTimeout(() => {
          finalizationTimerRef.current = null;
          // Re-check: if audio resumed during the delay, don't finalize
          if (isPlayingRef.current) return;
          if (!streamEndFullTextRef.current) return;

          const fullText = streamEndFullTextRef.current;
          const messageId = streamingMessageIdRef.current;

          // Add finalized message FIRST, then hide streaming bubble in same batch.
          if (messageId && selectedQubes.length > 0) {
            const currentQube = selectedQubes[0];
            addMessage(currentQube.qube_id, {
              id: messageId,
              sender: 'qube' as const,
              qubeName: currentQube.name,
              content: cleanContentForDisplay(fullText),
              timestamp: new Date(),
              blockNumber: streamingBlockNumberRef.current,
            });
            streamingBlockNumberRef.current = undefined;
          }

          // Clean up streaming state
          stopStreamingText();
          setStreamingText('');
          isStreamingRef.current = false;
        setIsStreaming(false);
          streamingMessageIdRef.current = null;
          streamEndFullTextRef.current = null;
          charsSpokenRef.current = 0;
          prevSentenceRef.current = null;

          setTimeout(() => scrollToBottom(), 50);
        }, 1000);
      }
    } else {
      // Audio is playing or no full text yet — cancel any pending finalization
      if (finalizationTimerRef.current) {
        clearTimeout(finalizationTimerRef.current);
        finalizationTimerRef.current = null;
      }
    }
  }, [isStreaming, isPlaying, selectedQubes, addMessage, stopStreamingText, scrollToBottom]);


  // Keep refs in sync with state (for stable closures in intervals/handlers)
  useEffect(() => { isStreamModeRef.current = isStreamMode; }, [isStreamMode]);
  useEffect(() => { isPlayingRef.current = isPlaying; }, [isPlaying]);
  useEffect(() => { isLoadingRef.current = isLoading; }, [isLoading]);
  useEffect(() => { currentSentenceRef.current = currentSentence; }, [currentSentence]);
  const silenceTimeoutRef = useRef(silenceTimeoutMs);
  useEffect(() => { silenceTimeoutRef.current = silenceTimeoutMs; }, [silenceTimeoutMs]);
  const toggleStreamModeRef = useRef<(() => void) | null>(null);
  const handleStreamSendRef = useRef<((text: string) => Promise<void>) | null>(null);

  // Auto-scroll when tool calls or indicators change during loading/streaming
  useEffect(() => {
    if (isLoading || isStreaming) {
      scrollToBottom();
    }
  }, [activeToolCalls, completedActionBlocks, isLoading, isStreaming, scrollToBottom]);

  // --- Stream Mode (continuous voice conversation) ---

  const handleStreamSend = useCallback(async (text: string) => {
    if (!text.trim() || !userId || !password || selectedQubes.length === 0) return;
    // Guard against double calls (recognition fires multiple onresult events,
    // each restarting the silence timer — without this, second call clears streamingTextRef)
    if (isSendingRef.current) return;
    isSendingRef.current = true;
    const currentQube = selectedQubes[0];

    // Capitalize first letter of transcript
    const capitalizedText = text.charAt(0).toUpperCase() + text.slice(1);

    // Add user message to chat
    addMessage(currentQube.qube_id, {
      id: Date.now().toString(),
      sender: 'user' as const,
      content: capitalizedText,
      timestamp: new Date(),
    });

    // Clear silence timer (prevent double-send)
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    // Send via streaming command
    setIsLoading(true);
    startStreamingText();
    streamingMessageIdRef.current = (Date.now() + 1).toString();
    startStreamingPlayback();

    try {
      const response = await invoke<ChatResponse>('send_message_streaming', {
        userId, qubeId: currentQube.qube_id,
        message: text, password,
      });

      // Set pending response for model change detection etc.
      if (response.success && response.response) {
        // Save block number for message finalization
        const blockNum = response.block_number !== undefined && response.block_number !== null
          ? Number(response.block_number) : undefined;
        streamingBlockNumberRef.current = blockNum;

        setPendingResponse({
          qubeName: response.qube_name || currentQube.name,
          content: response.response,
          timestamp: response.timestamp ? Number(response.timestamp) : undefined,
          blockNumber: blockNum,
          streaming: true,
        });
        setLastResponseText(cleanContentForDisplay(response.response));

        if (response.current_model) {
          setCurrentModel(response.current_model);
          emit('qube-model-changed', {
            qubeId: currentQube.qube_id,
            newModel: response.current_model,
            newProvider: response.current_provider,
          });
        }
      }
    } catch (err) {
      console.error('[StreamMode] Send failed:', err);
      setError('Stream Mode send failed. Please try again.');
    } finally {
      setIsLoading(false);
      isSendingRef.current = false;
    }
  }, [userId, password, selectedQubes, addMessage, startStreamingText, startStreamingPlayback]);

  const startListening = useCallback(() => {
    const recognition = recognitionRef.current;
    if (!recognition) return;

    // Save original handlers before overriding
    if (!originalHandlersRef.current) {
      originalHandlersRef.current = {
        onresult: recognition.onresult,
        onend: recognition.onend,
        onerror: recognition.onerror,
      };
    }

    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      // Accumulate ALL results (finalized + interim) to preserve text across pauses
      let finalTranscript = '';
      let interimText = '';
      for (let i = 0; i < event.results.length; i++) {
        const text = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += (finalTranscript && !finalTranscript.endsWith(' ') ? ' ' : '') + text;
        } else {
          interimText += text;
        }
      }
      // Ensure space between final and interim segments
      const combined = finalTranscript + (finalTranscript && interimText && !finalTranscript.endsWith(' ') ? ' ' : '') + interimText;
      const transcript = applySttAliases(combined.trim());

      // Mic is muted during playback — discard any recognition results
      // (use spacebar to interrupt instead)
      if (isPlayingRef.current || isLoadingRef.current) {
        return;
      }

      setInterimTranscript(transcript);
      scrollToBottom();

      // Reset silence timer
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = setTimeout(() => {
        if (transcript.trim()) {
          handleStreamSend(transcript.trim());
          setInterimTranscript('');
        }
      }, silenceTimeoutRef.current);
    };

    // Auto-restart: Web Speech API can silently stop
    // BUT: don't restart if qube is responding (mic is intentionally muted)
    recognition.onend = () => {
      setIsListening(false);
      if (isStreamModeRef.current) {
        setTimeout(() => {
          // Only restart if still in stream mode AND qube is NOT responding
          if (isStreamModeRef.current && !isPlayingRef.current && !isLoadingRef.current) {
            try {
              recognition.start();
              setIsListening(true);
            } catch (_) {
              // Already started or other error
            }
          }
        }, 300);
      }
    };

    recognition.onerror = (event: any) => {
      console.error('[StreamMode] Recognition error:', event.error);
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        setIsListening(false);
      }
    };

    try {
      recognition.start();
      setIsListening(true);
    } catch (err) {
      console.error('[StreamMode] Failed to start listening:', err);
    }
  }, [handleStreamSend, stopAudio, resetStreamingState, stopStreamingText, userId, password, selectedQubes]);

  const toggleStreamMode = useCallback(() => {
    if (isStreamMode) {
      // EXIT Stream Mode
      const recognition = recognitionRef.current;
      if (recognition) {
        recognition.stop();
        // Restore original handlers
        if (originalHandlersRef.current) {
          recognition.onresult = originalHandlersRef.current.onresult;
          recognition.onend = originalHandlersRef.current.onend;
          recognition.onerror = originalHandlersRef.current.onerror;
          originalHandlersRef.current = null;
        }
      }
      setIsStreamMode(false);
      setIsListening(false);
      setInterimTranscript('');
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
    } else {
      // ENTER Stream Mode
      if (!recognitionRef.current) {
        setError('Speech recognition not supported in this browser');
        return;
      }
      // Stop any existing recording first
      if (isRecording) {
        recognitionRef.current.stop();
        setIsRecording(false);
      }
      setIsStreamMode(true);
      startListening();
    }
  }, [isStreamMode, isRecording, startListening]);
  useEffect(() => { toggleStreamModeRef.current = toggleStreamMode; }, [toggleStreamMode]);
  useEffect(() => { handleStreamSendRef.current = handleStreamSend; }, [handleStreamSend]);

  // Keyboard shortcuts for Stream Mode
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+M: toggle Stream Mode
      if (e.ctrlKey && e.key === 'm') {
        e.preventDefault();
        toggleStreamMode();
        return;
      }

      // Spacebar: interrupt qube while it's responding in Stream Mode
      // Only when not focused on an input/textarea
      if (e.key === ' ' && isStreamModeRef.current &&
          (isPlayingRef.current || isLoadingRef.current) &&
          !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();

        // Clear refs FIRST — prevents finalization useEffect from firing
        // during Zustand's synchronous re-render from addMessage below
        // Calculate how much of the current sentence was actually heard (not full length)
        const partialProgress = audioElement && audioElement.duration > 0
          ? Math.min(audioElement.currentTime / audioElement.duration, 1) : 0;
        const partialChars = Math.ceil((currentSentenceRef.current?.length || 0) * partialProgress);
        const totalSpoken = charsSpokenRef.current + partialChars;
        const spokenText = streamingTextRef.current.substring(0, totalSpoken);
        const messageId = streamingMessageIdRef.current;
        streamEndFullTextRef.current = null;
        streamingMessageIdRef.current = null;
        charsSpokenRef.current = 0;
        prevSentenceRef.current = null;
        currentSentenceRef.current = null;

        // Stop everything — pass spokenChars so backend truncates the block correctly
        stopAudio();
        invoke('cancel_stream', { userId, qubeId: selectedQubes[0]?.qube_id, password, spokenChars: totalSpoken }).catch(() => {});
        resetStreamingState();
        stopStreamingText();
        setStreamingText('');
        isStreamingRef.current = false;
        setIsStreaming(false);
        setIsLoading(false);
        isSendingRef.current = false;

        // NOW add the partial message (after all refs are cleared)
        // Include a blockNumber so tool calls from this turn associate correctly
        // Use the lowest (most negative) session block number as reference
        if (spokenText.trim() && selectedQubes[0]) {
          const sessionBlocks = completedActionBlocks
            .filter(b => b.fromSession)
            .map(b => b.blockNumber);
          const refBlockNumber = sessionBlocks.length > 0
            ? Math.min(...sessionBlocks) - 1  // One below the lowest action block
            : undefined;

          addMessage(selectedQubes[0].qube_id, {
            id: messageId || Date.now().toString(),
            sender: 'qube' as const,
            qubeName: selectedQubes[0].name,
            content: cleanContentForDisplay(spokenText) + '\n\n*[interrupted]*',
            timestamp: new Date(),
            blockNumber: refBlockNumber,
          });
        }

        // Resume listening immediately
        setTimeout(() => {
          if (isStreamModeRef.current) {
            startListening();
          }
        }, 300);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [toggleStreamMode, stopAudio, resetStreamingState, stopStreamingText,
      addMessage, startListening, userId, password, selectedQubes]);

  // Mute mic during playback, resume when qube finishes
  useEffect(() => {
    if (!isStreamMode) return;

    if (isPlaying || isLoading || isStreaming) {
      // Qube is responding — mute mic (use spacebar to interrupt)
      if (isListening && recognitionRef.current) {
        try { recognitionRef.current.stop(); } catch (_) {}
        setIsListening(false);
      }
    } else {
      // Qube finished — resume listening
      if (!isListening) {
        const timer = setTimeout(() => {
          if (isStreamModeRef.current && !isPlayingRef.current && !isLoadingRef.current) {
            startListening();
          }
        }, 200);
        return () => clearTimeout(timer);
      }
    }
  }, [isStreamMode, isLoading, isPlaying, isListening, isStreaming, startListening]);

  // Helper function to clean and truncate text for TTS
  // Removes non-speakable elements and respects OpenAI's 4096 char limit
  const truncateForTTS = (text: string, maxLength: number = 4000): string => {
    let processedText = text;

    // Remove code blocks first (```...```)
    processedText = processedText.replace(/```[\s\S]*?```/g, '');

    // Remove inline code (`code`)
    processedText = processedText.replace(/`[^`]+`/g, '');

    // IMPORTANT: Handle markdown bold/italic BEFORE action asterisks
    // Otherwise *[^*]+* matches inside **bold** and breaks things
    processedText = processedText.replace(/\*\*\*([^*]+)\*\*\*/g, '$1');  // ***bold italic*** -> text
    processedText = processedText.replace(/\*\*([^*]+)\*\*/g, '$1');      // **bold** -> bold
    processedText = processedText.replace(/__([^_]+)__/g, '$1');          // __bold__ -> bold

    // Remove asterisk CHARACTERS but keep the content inside, add comma for natural pause
    // *waves hello* becomes "waves hello," (speaks action with brief pause after)
    // Must come AFTER bold removal to avoid matching inside **bold**
    processedText = processedText.replace(/\*([^*]+)\*/g, '$1,');

    // Shorten long hexadecimal strings (BCH addresses, transaction IDs, etc.)
    // Pattern: Any hex string longer than 20 characters
    processedText = processedText.replace(/\b([a-fA-F0-9]{20,})\b/g, (match) => {
      // Keep first 8 and last 8 characters
      return `${match.substring(0, 8)}...${match.substring(match.length - 8)}`;
    });

    // Also handle BCH addresses that start with specific prefixes
    processedText = processedText.replace(/\b(bitcoincash:[a-z0-9]{20,})\b/gi, (match) => {
      const parts = match.split(':');
      if (parts.length === 2 && parts[1].length > 20) {
        return `${parts[0]}:${parts[1].substring(0, 8)}...${parts[1].substring(parts[1].length - 8)}`;
      }
      return match;
    });

    // Normalize whitespace (multiple spaces/newlines -> single space)
    processedText = processedText.replace(/\s+/g, ' ').trim();

    // Now check overall length and truncate if needed
    if (processedText.length <= maxLength) {
      return processedText;
    }

    // Truncate at word boundary (silent truncation - no suffix)
    const truncated = processedText.substring(0, maxLength);
    const lastSpace = truncated.lastIndexOf(' ');
    return truncated.substring(0, lastSpace);
  };

  // Helper function to detect and render images in message content
  const renderMessageContent = (content: string) => {
    // Regular expression to detect image URLs (including DALL-E Azure Blob Storage URLs)
    const imageUrlRegex = /(https?:\/\/[^\s\)]+?(?:\.(?:png|jpg|jpeg|gif|webp)|blob\.core\.windows\.net\/[^\s\)]+))/gi;
    // Regular expression to detect local file paths (Windows and Unix)
    const localPathRegex = /([A-Za-z]:[\\\/][^\s\)]+\.(?:png|jpg|jpeg|gif|webp)|\/[^\s\)]+\.(?:png|jpg|jpeg|gif|webp))/gi;
    // Regular expression to detect any URLs
    const anyUrlRegex = /(https?:\/\/[^\s\)]+)/gi;

    // Extract all image URLs first (both web URLs and local paths)
    const webImageUrls = content.match(imageUrlRegex) || [];
    const localImagePaths = content.match(localPathRegex) || [];

    // Convert local paths to asset:// URLs using Tauri's convertFileSrc
    const convertedLocalUrls = localImagePaths.map(path => convertFileSrc(path));

    // Combine all image sources
    const imageUrls = [...webImageUrls, ...convertedLocalUrls];

    // Get cleaned text content (without image URLs)
    let textContent = cleanContentForDisplay(content);

    // Replace remaining URLs with truncated clickable links
    const textParts = textContent.split(anyUrlRegex);
    const renderedText = textParts.map((part, index) => {
      if (part.match(anyUrlRegex)) {
        // Truncate URL for display (show first 40 chars + ...)
        const displayUrl = part.length > 40 ? part.substring(0, 40) + '...' : part;
        return (
          <a
            key={`link-${index}`}
            href={part}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent-primary underline hover:text-accent-primary/80"
          >
            {displayUrl}
          </a>
        );
      }
      return <span key={`text-${index}`}>{part}</span>;
    });

    // Return images FIRST, then text content
    return (
      <>
        {imageUrls.map((url, index) => (
          <img
            key={`img-${index}`}
            src={url}
            alt="Generated image"
            className="max-w-full rounded-lg mb-3 block"
            style={{ maxHeight: '400px', objectFit: 'contain' }}
            onLoad={() => {
              // Save image to disk
              saveImageToDisk(url);
              // Scroll when image finishes loading
              scrollToBottom();
            }}
            onError={(e) => {
              // Fallback if image fails to load - show truncated URL instead
              const target = e.target as HTMLImageElement;
              target.style.display = 'none';
              const fallback = document.createElement('a');
              fallback.href = url;
              fallback.target = '_blank';
              fallback.className = 'text-accent-primary underline';
              const displayUrl = url.length > 60 ? url.substring(0, 60) + '...' : url;
              fallback.textContent = `[Image failed to load: ${displayUrl}]`;
              target.parentNode?.insertBefore(fallback, target);
            }}
          />
        ))}
        {renderedText}
      </>
    );
  };

  // Auto-scroll to bottom when new messages arrive or loading state changes
  useEffect(() => {
    scrollToBottom();
  }, [messages.length, isLoading]);

  // Also scroll when typewriter completes
  useEffect(() => {
    if (!activeTypewriterMessageId) {
      // Small delay to allow final layout adjustment
      setTimeout(scrollToBottom, 100);
    }
  }, [activeTypewriterMessageId]);

  // Load visualizer settings when qube changes
  useEffect(() => {
    const loadVisualizerSettings = async () => {
      if (!userId || selectedQubes.length === 0) return;

      try {
        const settings = await invoke('get_visualizer_settings', {
          userId,
          qubeId: selectedQubes[0].qube_id,
          password
        });
        // Merge with defaults to handle missing fields (like output_monitor)
        const loadedSettings = {
          enabled: (settings as any).enabled ?? false,
          waveform_style: (settings as any).waveform_style ?? 1,
          color_theme: (settings as any).color_theme ?? 'qube-color',
          gradient_style: (settings as any).gradient_style ?? 'gradient-dark',
          sensitivity: (settings as any).sensitivity ?? 50,
          animation_smoothness: (settings as any).animation_smoothness ?? 'medium',
          audio_offset_ms: (settings as any).audio_offset_ms ?? 0,
          frequency_range: (settings as any).frequency_range ?? 20,
          output_monitor: (settings as any).output_monitor ?? 0
        };
        setVisualizerSettings(loadedSettings);
      } catch (error) {
        console.error('Failed to load visualizer settings:', error);
      }
    };

    loadVisualizerSettings();
  }, [userId, selectedQubes.length > 0 ? selectedQubes[0].qube_id : null]);

  // Listen for settings changes from QubeManagerTab
  useEffect(() => {
    const setupListener = async () => {
      const unlisten = await listen('visualizer-settings-changed', async (event: any) => {
        const { qubeId } = event.payload;

        // Only reload if it's for the currently selected qube
        if (userId && selectedQubes.length > 0 && selectedQubes[0].qube_id === qubeId) {
          try {
            const settings = await invoke('get_visualizer_settings', {
              userId,
              qubeId,
              password
            });

            const loadedSettings = {
              enabled: (settings as any).enabled ?? false,
              waveform_style: (settings as any).waveform_style ?? 1,
              color_theme: (settings as any).color_theme ?? 'qube-color',
              gradient_style: (settings as any).gradient_style ?? 'gradient-dark',
              sensitivity: (settings as any).sensitivity ?? 50,
              animation_smoothness: (settings as any).animation_smoothness ?? 'medium',
              audio_offset_ms: (settings as any).audio_offset_ms ?? 0,
              frequency_range: (settings as any).frequency_range ?? 20,
              output_monitor: (settings as any).output_monitor ?? 0
            };
            setVisualizerSettings(loadedSettings);
          } catch (error) {
            console.error('Failed to reload visualizer settings:', error);
          }
        }
      });
      return unlisten;
    };

    const cleanupPromise = setupListener();
    return () => {
      cleanupPromise.then(cleanup => cleanup());
    };
  }, [userId, selectedQubes]);

  // Track audio playback state
  useEffect(() => {
    if (!audioElement) return;

    const handlePlay = () => setIsPlayingAudio(true);
    const handlePause = () => setIsPlayingAudio(false);
    const handleEnded = () => setIsPlayingAudio(false);

    audioElement.addEventListener('play', handlePlay);
    audioElement.addEventListener('pause', handlePause);
    audioElement.addEventListener('ended', handleEnded);

    return () => {
      audioElement.removeEventListener('play', handlePlay);
      audioElement.removeEventListener('pause', handlePause);
      audioElement.removeEventListener('ended', handleEnded);
    };
  }, [audioElement]);

  // Broadcast playback state to visualizer window
  useEffect(() => {
    // Use emitTo to target the specific visualizer window
    emitTo('visualizer', 'visualizer-playback-update', {
      isPlaying: isPlayingAudio
    }).catch((err) => {
      // Silently fail if visualizer window doesn't exist
    });
  }, [isPlayingAudio]);

  // Broadcast visualizer settings to visualizer window
  // Only broadcast when external monitor is selected (to avoid unnecessary events)
  useEffect(() => {
    if (selectedQubes.length > 0 && visualizerSettings.output_monitor > 0) {
      emitTo('visualizer', 'visualizer-settings-update', {
        ...visualizerSettings,
        qube_favorite_color: selectedQubes[0].favorite_color
      }).catch(() => {
        // Silently fail if visualizer window doesn't exist
      });
    }
  }, [visualizerSettings.waveform_style, visualizerSettings.color_theme, visualizerSettings.gradient_style, visualizerSettings.sensitivity, visualizerSettings.animation_smoothness, visualizerSettings.audio_offset_ms, visualizerSettings.frequency_range, selectedQubes, visualizerSettings.output_monitor]);

  // Handle visualizer window lifecycle (create/close based on playback state)
  useEffect(() => {
    const manageVisualizerWindow = async () => {
      // Only manage window if external monitor is selected
      if (visualizerSettings.output_monitor === 0) {
        // Make sure window is closed if switching back to overlay
        if (visualizerWindowOpenRef.current) {
          try {
            await invoke('close_visualizer_window');
            visualizerWindowOpenRef.current = false;
          } catch (error) {
            // Silently fail
          }
        }
        return;
      }

      const shouldBeOpen = isPlayingAudio && visualizerSettings.enabled;

      if (shouldBeOpen && !visualizerWindowOpenRef.current) {
        // Create visualizer window on external monitor when audio starts
        try {
          // Use ref to get latest value and avoid stale closures
          const monitorIndex = visualizerSettingsRef.current.output_monitor;

          await invoke('create_visualizer_window', {
            monitorIndex
          });
          visualizerWindowOpenRef.current = true;

          // Small delay to ensure window is ready to receive events
          await new Promise(resolve => setTimeout(resolve, 300));

          // Broadcast current state to the new window immediately (use refs for latest values)
          emitTo('visualizer', 'visualizer-playback-update', {
            isPlaying: true
          }).catch((err) => {
            console.error('Failed to send playback state:', err);
          });

          const currentQubes = selectedQubesRef.current;
          const currentSettings = visualizerSettingsRef.current;
          if (currentQubes.length > 0) {
            emitTo('visualizer', 'visualizer-settings-update', {
              ...currentSettings,
              qube_favorite_color: currentQubes[0].favorite_color
            }).catch((err) => {
              console.error('Failed to send settings:', err);
            });
          }
        } catch (error) {
          console.error('Failed to create visualizer window:', error);
          visualizerWindowOpenRef.current = false;
        }
      } else if (!shouldBeOpen && visualizerWindowOpenRef.current) {
        // Close visualizer window when audio stops OR when disabled
        try {
          await invoke('close_visualizer_window');
          visualizerWindowOpenRef.current = false;
        } catch (error) {
          // Silently fail - window might not exist
          visualizerWindowOpenRef.current = false;
        }
      }
    };

    manageVisualizerWindow();
  }, [isPlayingAudio, visualizerSettings.output_monitor, visualizerSettings.enabled]);

  // STT name aliases for common misrecognitions
  // Keys are regex patterns (case-insensitive), values are replacements
  const applySttAliases = (text: string): string => {
    // Order matters: multi-word patterns first, then single-word replacements
    const aliases: [string, string][] = [
      // Websites (must run BEFORE cube→qube to catch "cube dot cash")
      ['\\bcube\\.cash\\b', 'qube.cash'],
      ['\\bqube\\.cash\\b', 'qube.cash'],
      ['\\bcube dot cash\\b', 'qube.cash'],
      ['\\bqube dot cash\\b', 'qube.cash'],
      ['\\bQ dot cash\\b', 'qube.cash'],
      // Multi-word terms
      ['\\bQ universe\\b', 'Qniverse'],
      ['\\bCuniverse\\b', 'Qniverse'],
      ['\\bkuniverse\\b', 'Qniverse'],
      // Single-word replacements (after multi-word)
      ['\\bAlf\\b', 'Alph'],
      ['\\bcubes\\b', 'qubes'],
      ['\\bCubes\\b', 'Qubes'],
      ['\\bcube\\b', 'qube'],
      ['\\bCube\\b', 'Qube'],
    ];
    let result = text;
    for (const [pattern, replacement] of aliases) {
      result = result.replace(new RegExp(pattern, 'gi'), replacement);
    }
    return result;
  };

  // --- Wake Word Detection ---
  // Listens for "Hey, [qube name]" when not in Stream Mode.
  // On detection: switches to Chat tab, selects the qube, enters Stream Mode.
  const wakeWordRecognitionRef = useRef<any>(null);
  const isWakeWordActiveRef = useRef(false);
  const pendingWakePhraseRef = useRef<{ qubeId: string; phrase: string } | null>(null);
  const wakeDebounceRef = useRef<{ qubeId: string; qubeName: string; timer: ReturnType<typeof setTimeout> } | null>(null);
  const wakeToStreamTransitionRef = useRef(false);  // Prevents onend restart during wake→stream transition
  const wakePhraseAccumulatorRef = useRef<string>('');  // Accumulates full phrase after wake word detection
  const [isWakeWordPending, setIsWakeWordPending] = useState(false);
  const [wakeWordRestartKey, setWakeWordRestartKey] = useState(0);  // Increment to force wake word effect re-run

  useEffect(() => {
    wakeToStreamTransitionRef.current = false;

    if (allQubes.length === 0 || isStreamMode || isRecording) {
      if (wakeWordRecognitionRef.current) {
        try { wakeWordRecognitionRef.current.stop(); } catch (_) {}
        wakeWordRecognitionRef.current = null;
        isWakeWordActiveRef.current = false;
      }
      return;
    }

    if (typeof window === 'undefined') return;
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    let stopped = false;

    // Single recognition instance — restarted, never recreated
    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    wakeWordRecognitionRef.current = recognition;

    const tryStart = () => {
      if (stopped || isStreamModeRef.current || isWakeWordActiveRef.current) return;
      try {
        recognition.start();
        isWakeWordActiveRef.current = true;
        console.log('[WakeWord] Listening for wake phrases...');
      } catch (e: any) {
        if (e?.name === 'InvalidStateError') {
          // Already running — just mark active
          isWakeWordActiveRef.current = true;
        }
      }
    };

    recognition.onresult = (event: any) => {
      // Build full transcript from ALL results (needed to capture complete phrase after wake word)
      let fullTranscript = '';
      for (let i = 0; i < event.results.length; i++) {
        fullTranscript += event.results[i][0].transcript;
      }
      fullTranscript = applySttAliases(fullTranscript.trim());
      const fullLower = fullTranscript.toLowerCase();

      // If we already have an active wake word debounce, update the accumulated phrase
      if (wakeDebounceRef.current) {
        const qube = allQubes.find(q => q.qube_id === wakeDebounceRef.current!.qubeId);
        if (qube) {
          const name = qube.name.toLowerCase();
          const wakePattern = new RegExp(`hey[,\\s]+${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
          const match = fullLower.match(wakePattern);
          if (match) {
            // Update accumulated phrase with everything after the wake word
            wakePhraseAccumulatorRef.current = fullTranscript.slice(match.index! + match[0].length).replace(/^[,.\s]+/, '').trim();
          }
        }
        // Reset the debounce timer (user is still speaking)
        clearTimeout(wakeDebounceRef.current.timer);
        const WAKE_PHRASE_DELAY = silenceTimeoutRef.current;
        const qubeId = wakeDebounceRef.current.qubeId;
        const qubeName = wakeDebounceRef.current.qubeName;
        wakeDebounceRef.current = {
          qubeId,
          qubeName,
          timer: setTimeout(() => {
            const phrase = wakePhraseAccumulatorRef.current;
            console.log('[WakeWord] Activating:', qubeName, phrase ? `with: "${phrase}"` : '(no phrase)');
            wakeDebounceRef.current = null;
            wakePhraseAccumulatorRef.current = '';
            setIsWakeWordPending(false);

            pendingWakePhraseRef.current = { qubeId, phrase };

            wakeToStreamTransitionRef.current = true;
            isWakeWordActiveRef.current = false;
            try { recognition.stop(); } catch (_) {}

            setTimeout(() => {
              const pending = pendingWakePhraseRef.current;
              if (!pending) return;
              pendingWakePhraseRef.current = null;

              if (!isStreamModeRef.current && toggleStreamModeRef.current) {
                toggleStreamModeRef.current();
              }
              if (pending.phrase && handleStreamSendRef.current) {
                setTimeout(() => handleStreamSendRef.current?.(pending.phrase), 500);
              }
            }, 800);
          }, WAKE_PHRASE_DELAY),
        };
        return;
      }

      // No active debounce — check latest result for wake word (first detection)
      const latestResult = event.results[event.results.length - 1];
      if (!latestResult) return;
      const latestTranscript = applySttAliases(latestResult[0].transcript.trim());
      const latestLower = latestTranscript.toLowerCase();

      for (const qube of allQubes) {
        const name = qube.name.toLowerCase();
        const wakePattern = new RegExp(`^hey[,\\s]+${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
        const match = latestLower.match(wakePattern);

        if (match) {
          console.log('[WakeWord] Detected:', qube.name);
          switchTabAndSelect('dashboard', qube.qube_id);
          setIsWakeWordPending(true);

          // Extract initial phrase from full transcript
          const fullMatch = fullLower.match(new RegExp(`hey[,\\s]+${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i'));
          wakePhraseAccumulatorRef.current = fullMatch
            ? fullTranscript.slice(fullMatch.index! + fullMatch[0].length).replace(/^[,.\s]+/, '').trim()
            : '';

          const WAKE_PHRASE_DELAY = silenceTimeoutRef.current;
          wakeDebounceRef.current = {
            qubeId: qube.qube_id,
            qubeName: qube.name,
            timer: setTimeout(() => {
              const phrase = wakePhraseAccumulatorRef.current;
              console.log('[WakeWord] Activating:', qube.name, phrase ? `with: "${phrase}"` : '(no phrase)');
              wakeDebounceRef.current = null;
              wakePhraseAccumulatorRef.current = '';
              setIsWakeWordPending(false);

              pendingWakePhraseRef.current = { qubeId: qube.qube_id, phrase };

              wakeToStreamTransitionRef.current = true;
              isWakeWordActiveRef.current = false;
              try { recognition.stop(); } catch (_) {}

              setTimeout(() => {
                const pending = pendingWakePhraseRef.current;
                if (!pending) return;
                pendingWakePhraseRef.current = null;

                if (!isStreamModeRef.current && toggleStreamModeRef.current) {
                  toggleStreamModeRef.current();
                }
                if (pending.phrase && handleStreamSendRef.current) {
                  setTimeout(() => handleStreamSendRef.current?.(pending.phrase), 500);
                }
              }, 800);
            }, WAKE_PHRASE_DELAY),
          };
          return;
        }
      }
    };

    recognition.onend = () => {
      console.log('[WakeWord] onend fired, stopped:', stopped, 'transition:', wakeToStreamTransitionRef.current);
      isWakeWordActiveRef.current = false;
      if (stopped || wakeToStreamTransitionRef.current) {
        wakeToStreamTransitionRef.current = false;
        return;
      }
      // Restart the same instance after a delay
      setTimeout(() => {
        if (!stopped && !isStreamModeRef.current) {
          console.log('[WakeWord] Restarting after onend...');
          tryStart();
        }
      }, 500);
    };

    recognition.onerror = (event: any) => {
      console.log('[WakeWord] onerror:', event.error);
      // onend fires after onerror and handles restart
    };

    tryStart();

    // Fallback: start on first user gesture if mic permission wasn't granted yet
    const handleUserGesture = () => {
      if (!isWakeWordActiveRef.current && !stopped && !isStreamModeRef.current) tryStart();
    };
    window.addEventListener('click', handleUserGesture);
    window.addEventListener('keydown', handleUserGesture);

    return () => {
      stopped = true;
      window.removeEventListener('click', handleUserGesture);
      window.removeEventListener('keydown', handleUserGesture);
      if (wakeDebounceRef.current) {
        clearTimeout(wakeDebounceRef.current.timer);
        wakeDebounceRef.current = null;
      }
      isWakeWordActiveRef.current = false;
      try { recognition.stop(); } catch (_) {}
      if (wakeWordRecognitionRef.current === recognition) {
        wakeWordRecognitionRef.current = null;
      }
    };
  }, [allQubes, isStreamMode, isRecording, switchTabAndSelect, wakeWordRestartKey]);

  // Handle pending wake word action — fires when selectedQubes updates after wake word selection
  useEffect(() => {
    const pending = pendingWakePhraseRef.current;
    if (!pending) return;
    if (selectedQubes.length === 0 || selectedQubes[0].qube_id !== pending.qubeId) return;

    const phrase = pending.phrase;
    pendingWakePhraseRef.current = null;
    console.log('[WakeWord] Selection matched, entering Stream Mode');

    // Wait for wake word mic to fully release before starting Stream Mode
    setTimeout(() => {
      if (!isStreamModeRef.current) {
        console.log('[WakeWord] Effect activation — entering Stream Mode');
        toggleStreamMode();
      }
      if (phrase) {
        setTimeout(() => {
          console.log('[WakeWord] Effect activation — sending phrase:', phrase);
          handleStreamSend(phrase);
        }, 500);
      }
    }, 800);
  }, [selectedQubes, toggleStreamMode, handleStreamSend]);

  // Listen for stream settings changes from Settings tab
  useEffect(() => {
    const handleSettingsChanged = () => {
      setSilenceTimeoutMs(parseInt(localStorage.getItem('stream_silence_timeout_ms') || '1500'));
    };
    window.addEventListener('stream-settings-changed', handleSettingsChanged);
    return () => window.removeEventListener('stream-settings-changed', handleSettingsChanged);
  }, []);

  // Initialize speech recognition
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

      if (SpeechRecognition) {
        const recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = 'en-US';

        recognition.onresult = (event: any) => {
          let transcript = '';
          for (let i = event.resultIndex; i < event.results.length; i++) {
            transcript += event.results[i][0].transcript;
          }
          // Apply STT aliases to fix common misrecognitions
          setInputValue(applySttAliases(transcript));
        };

        recognition.onerror = (event: any) => {
          console.error('Speech recognition error:', event.error);
          setIsRecording(false);
        };

        recognition.onend = () => {
          setIsRecording(false);
        };

        recognitionRef.current = recognition;
      }
    }

    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
    };
  }, []);

  // --- Streaming TTS event listeners (permanent — never torn down) ---
  // Uses refs to avoid stale closures and token loss during useEffect re-creation

  const isStreamingRef = useRef(false);
  useEffect(() => { isStreamingRef.current = isStreaming; }, [isStreaming]);

  useEffect(() => {
    let mounted = true;

    const unlistenToken = listen<{ qube_id: string; token: string }>('tts-text-token', (event) => {
      if (!mounted) return;
      // Always accumulate — no gating. streamingTextRef is cleared by
      // startStreamingText() before each new message.
      streamingTextRef.current += event.payload.token;

      // Clear loading indicator on first token
      if (isLoadingRef.current) {
        setIsLoading(false);
        setProcessingStage(null);
      }
    });

    const unlistenEnd = listen<{ qube_id: string; full_text: string; total_chunks: number; error?: string }>('tts-stream-end', (event) => {
      if (!mounted || !isStreamingRef.current) return;

      // DON'T finalize here — audio may still be playing.
      // Just save the full text. The finalization useEffect will
      // add the message when isPlaying becomes false.
      streamEndFullTextRef.current = event.payload.full_text;
    });

    return () => {
      mounted = false;
      unlistenToken.then(u => u());
      unlistenEnd.then(u => u());
    };
  }, []);  // Empty deps — set up once, never torn down

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

  // Add Escape key listener to clear chat
  useEffect(() => {
    const handleEscapeKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedQubes.length > 0) {
        clearMessages(selectedQubes[0].qube_id);
        setCompletedActionBlocks([]);
        setActiveToolCalls([]);
        chatClearedAtRef.current = Date.now();
        setError(null);
        setLastResponseText('');
        // Streaming cleanup
        stopAudio();
        resetStreamingState();
        stopStreamingText();
        setStreamingText('');
        isStreamingRef.current = false;
        setIsStreaming(false);
        streamingMessageIdRef.current = null;
        setActiveTypewriterMessageId(null);
        pendingTypewriterRef.current = null;
        setIsLoading(false);
        isSendingRef.current = false;
        setProcessingStage(null);
        setIsGeneratingTTS(false);
        // Exit Stream Mode if active
        if (isStreamMode) {
          recognitionRef.current?.stop();
          if (originalHandlersRef.current) {
            const recognition = recognitionRef.current;
            if (recognition) {
              recognition.onresult = originalHandlersRef.current.onresult;
              recognition.onend = originalHandlersRef.current.onend;
              recognition.onerror = originalHandlersRef.current.onerror;
            }
            originalHandlersRef.current = null;
          }
          setIsStreamMode(false);
          setIsListening(false);
          setInterimTranscript('');
          if (silenceTimerRef.current) {
            clearTimeout(silenceTimerRef.current);
            silenceTimerRef.current = null;
          }
        }
        if (isStreaming) {
          const partialProgress = audioElement && audioElement.duration > 0
            ? Math.min(audioElement.currentTime / audioElement.duration, 1) : 0;
          const spokenChars = charsSpokenRef.current + Math.ceil((currentSentenceRef.current?.length || 0) * partialProgress);
          invoke('cancel_stream', { userId, qubeId: selectedQubes[0].qube_id, password, spokenChars }).catch(() => {});
        }
      }
    };

    window.addEventListener('keydown', handleEscapeKey);
    return () => window.removeEventListener('keydown', handleEscapeKey);
  }, [selectedQubes, clearMessages]);

  // Track the current qube ID to detect actual qube changes (not just array reference changes)
  const currentQubeId = selectedQubes.length > 0 ? selectedQubes[0].qube_id : null;
  const prevQubeIdRef = useRef<string | null>(currentQubeId);

  // Clear pending states when switching Qubes (only when qube ID actually changes)
  useEffect(() => {
    // Skip if qube ID hasn't actually changed
    if (prevQubeIdRef.current === currentQubeId) {
      return;
    }
    prevQubeIdRef.current = currentQubeId;

    // Clear any pending responses from previous Qube
    setPendingResponse(null);
    setLastResponseText('');
    setActiveTypewriterMessageId(null);
    setCurrentModel(null); // Reset so header uses new qube's ai_model
    setIsGeneratingTTS(false);
    setError(null);
    processedModelSwitches.current.clear(); // Reset processed model switches for new qube
    toolCallFirstSeen.current.clear(); // Reset tool call display tracking for new qube
  }, [currentQubeId]);

  // Listen for external model change events (e.g., from BlocksTab after session discard)
  // This ensures ChatInterface's local model state stays in sync with backend
  useEffect(() => {
    const setupModelChangeListener = async () => {
      const unlisten = await listen<{ qubeId: string; newModel: string }>(
        'qube-model-changed',
        (event) => {
          const { qubeId, newModel } = event.payload;
          // Only update if this event is for the currently selected qube
          if (currentQubeId && qubeId === currentQubeId) {
            setCurrentModel(newModel);
          }
        }
      );
      return unlisten;
    };

    const cleanupPromise = setupModelChangeListener();
    return () => {
      cleanupPromise.then((cleanup) => cleanup());
    };
  }, [currentQubeId]);

  // Start/stop event watching when qube changes
  useEffect(() => {
    if (!currentQubeId) return;

    // Start watching events for this qube
    startWatching(currentQubeId);

    // Cleanup: stop watching when qube changes or component unmounts
    return () => {
      stopWatching(currentQubeId);
    };
  }, [currentQubeId, startWatching, stopWatching]);

  // Close emoji picker when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (emojiPickerRef.current && !emojiPickerRef.current.contains(event.target as Node)) {
        setShowEmojiPicker(false);
      }
    };

    if (showEmojiPicker) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => {
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [showEmojiPicker]);

  // Auto-focus textarea when switching to Chat tab with a qube selected
  useEffect(() => {
    if (currentTab === 'dashboard' && selectedQubes.length > 0 && textareaRef.current) {
      // Small delay to ensure the tab transition is complete
      setTimeout(() => {
        textareaRef.current?.focus();
      }, 100);
    }
  }, [currentTab, selectedQubes.length]);

  /**
   * Helper function to prepare messages for IPC.
   * NOTE: Tauri permissions for temp file writing aren't working properly.
   * For now, send directly and let Windows truncate. This will cause issues with PDFs.
   * TODO: Fix temp file permissions or implement alternative solution.
   */
  const prepareMessageForIPC = async (message: string): Promise<string> => {
    return message;
  };

  // Send message via streaming or non-streaming path
  const sendToBackend = async (preparedMessage: string): Promise<ChatResponse> => {
    const currentQube = selectedQubes[0];
    // Use streaming when TTS is enabled (streaming_tts_enabled defaults to true)
    if (currentQube.tts_enabled && (currentQube as any).streaming_tts_enabled !== false) {
      // Streaming path
      startStreamingText();
      streamingMessageIdRef.current = (Date.now() + 1).toString();
      startStreamingPlayback();

      const response = await invoke<ChatResponse>('send_message_streaming', {
        userId, qubeId: currentQube.qube_id,
        message: preparedMessage, password,
      });
      return response;
    } else {
      // Non-streaming path: existing behavior unchanged
      return await invoke<ChatResponse>('send_message', {
        userId: userId,
        qubeId: currentQube.qube_id,
        message: preparedMessage,
        password: password
      });
    }
  };

  const handleSend = async () => {
    // Guard against double-sending (e.g., double-click, Enter key repeat)
    // useRef is checked FIRST because React state closures can be stale
    // when multiple keypress events fire before a re-render
    if (isSendingRef.current || isLoading) return;

    // If streaming is active, interrupt and save partial response first
    if (isStreaming) {
      const totalSpoken = charsSpokenRef.current + (currentSentenceRef.current?.length || 0);
      const spokenText = streamingTextRef.current.substring(0, totalSpoken);
      const messageId = streamingMessageIdRef.current;
      streamEndFullTextRef.current = null;
      streamingMessageIdRef.current = null;
      charsSpokenRef.current = 0;
      prevSentenceRef.current = null;
      currentSentenceRef.current = null;

      stopAudio();
      invoke('cancel_stream', { userId, qubeId: selectedQubes[0]?.qube_id, password, spokenChars: totalSpoken }).catch(() => {});
      resetStreamingState();
      stopStreamingText();
      setStreamingText('');
      setIsStreaming(false);
      streamingBlockNumberRef.current = undefined;

      if (spokenText.trim() && selectedQubes[0]) {
        const sessionBlocks = completedActionBlocks
          .filter(b => b.fromSession)
          .map(b => b.blockNumber);
        const refBlockNumber = sessionBlocks.length > 0
          ? Math.min(...sessionBlocks) - 1
          : undefined;

        addMessage(selectedQubes[0].qube_id, {
          id: messageId || Date.now().toString(),
          sender: 'qube' as const,
          qubeName: selectedQubes[0].name,
          content: cleanContentForDisplay(spokenText) + '\n\n*[interrupted]*',
          timestamp: new Date(),
          blockNumber: refBlockNumber,
        });
      }
    }

    isSendingRef.current = true;

    if ((!inputValue.trim() && uploadedFiles.length === 0) || selectedQubes.length === 0 || !userId) {
      isSendingRef.current = false;
      return;
    }

    if (!password) {
      setError('Session expired. Please log out and log back in.');
      isSendingRef.current = false;
      return;
    }

    // Store files and input for async processing
    const filesToProcess = [...uploadedFiles];
    const messageToSend = inputValue;

    // Build file indicator for user message
    let fileIndicator = '';
    if (filesToProcess.length > 0) {
      const imageCount = filesToProcess.filter(f => f.type === 'image').length;
      const pdfCount = filesToProcess.filter(f => f.type === 'pdf').length;
      const textCount = filesToProcess.filter(f => f.type === 'text').length;
      const totalDocCount = pdfCount + textCount;

      if (imageCount > 0 && totalDocCount > 0) {
        fileIndicator = `\n[${imageCount} image(s) and ${totalDocCount} document(s) attached]`;
      } else if (imageCount > 0) {
        fileIndicator = `\n[${imageCount} image(s) attached]`;
      } else if (totalDocCount > 0) {
        fileIndicator = `\n[${totalDocCount} document(s) attached]`;
      }
    }

    // Create user message - SHOW IMMEDIATELY
    const userMessage: Message = {
      id: Date.now().toString(),
      sender: 'user',
      content: `${inputValue}${fileIndicator}`,
      timestamp: new Date(),
    };

    // Add message and clear input IMMEDIATELY
    addMessage(selectedQubes[0].qube_id, userMessage);
    setInputValue('');
    clearUploadedFiles(selectedQubes[0].qube_id);
    forceScrollToBottom();  // Always scroll when user sends a message
    setIsLoading(true);
    setProcessingStage('response'); // Default to response processing
    setError(null);

    try {
      // Process files
      if (filesToProcess.length > 0) {
        // Separate images, text files, PDFs, and other binaries
        const images = filesToProcess.filter(f => f.type === 'image');
        const textFiles = filesToProcess.filter(f => f.type === 'text');
        const pdfFiles = filesToProcess.filter(f => f.type === 'pdf');
        const binaryFiles = filesToProcess.filter(f => f.type === 'binary');

        // Check for unsupported binary files (PDFs are now supported)
        if (binaryFiles.length > 0) {
          setError(`Sorry, I cannot read binary files. Please upload images (.png, .jpg), documents (.pdf, .txt, .md, .json) instead.`);
          setIsLoading(false);
          isSendingRef.current = false;
      setProcessingStage(null);
          return;
        }

        // Build message with text files
        let fullMessage = messageToSend;
        for (const file of textFiles) {
          fullMessage += `\n\n[Attached file: ${file.name}]\n${file.data}`;
        }

        // Add PDF files to message (backend will handle extraction)
        for (const file of pdfFiles) {
          fullMessage += `\n\n[Attached PDF: ${file.name}]\n<pdf_base64 filename="${file.name}">${file.data}</pdf_base64>`;
        }

        // Set processing stage based on whether documents need processing
        if (pdfFiles.length > 0 || images.length > 0) {
          setProcessingStage('document');
        } else {
          setProcessingStage('response');
        }

        // Process images (analyze each one separately for now)
        let allResponses = '';
        for (let i = 0; i < images.length; i++) {
          const image = images[i];
          const base64Data = image.data.split(',')[1];
          const imagePrompt = i === 0 && images.length === 1
            ? (messageToSend || "Please describe this image.")
            : (messageToSend || `Please describe image ${i + 1} of ${images.length}.`);

          const analyzeResponse = await invoke<{success: boolean; description?: string; error?: string}>('analyze_image', {
            userId: userId,
            qubeId: selectedQubes[0].qube_id,
            imageBase64: base64Data,
            userMessage: imagePrompt,
            password: password
          });

          if (analyzeResponse.success && analyzeResponse.description) {
            if (images.length > 1) {
              allResponses += `\n\n**Image ${i + 1}:**\n${analyzeResponse.description}`;
            } else {
              allResponses += analyzeResponse.description;
            }
          } else {
            throw new Error(analyzeResponse.error || `Failed to analyze image ${i + 1}`);
          }
        }

        // If we have text files or PDFs after images, send them as a follow-up
        if (textFiles.length > 0 || pdfFiles.length > 0) {
          // Prepare message for IPC (writes to temp file if >100KB)
          const preparedMessage = await prepareMessageForIPC(fullMessage);

          const textResponse = await sendToBackend(preparedMessage);


          if (textResponse.success && textResponse.response) {
            // Combine image responses with text response
            const combinedResponse = allResponses
              ? `${allResponses}\n\n${textResponse.response}`
              : textResponse.response;

            setPendingResponse({
              qubeName: selectedQubes[0].name,
              content: combinedResponse,
              timestamp: textResponse.timestamp ? Number(textResponse.timestamp) : undefined,
              blockNumber: textResponse.block_number ? Number(textResponse.block_number) : undefined,
              streaming: textResponse.streaming,
            });
            setLastResponseText(cleanContentForDisplay(combinedResponse));

            // Check if model changed (e.g., via switch_model tool or revolver mode)
            // Use local state to update header without re-rendering entire chat (preserves typewriter)
            if (textResponse.current_model) {
              setCurrentModel(textResponse.current_model);
              // Emit event so other components (roster, etc.) can update
              emit('qube-model-changed', {
                qubeId: selectedQubes[0].qube_id,
                newModel: textResponse.current_model,
                newProvider: textResponse.current_provider,
              });
            }
          } else {
            throw new Error(textResponse.error || 'Failed to get response from qube');
          }
        } else if (allResponses) {
          // Only images, no text files or PDFs
          setPendingResponse({
            qubeName: selectedQubes[0].name,
            content: allResponses,
          });
          setLastResponseText(cleanContentForDisplay(allResponses));
        }
      } else {
        // No files - send regular message
        // Prepare message for IPC (writes to temp file if >100KB)
        const preparedMessage = await prepareMessageForIPC(messageToSend);

        const response = await sendToBackend(preparedMessage);

        if (response.success && response.response) {
          setPendingResponse({
            qubeName: response.qube_name || selectedQubes[0].name,
            content: response.response,
            timestamp: response.timestamp ? Number(response.timestamp) : undefined,
            blockNumber: response.block_number !== undefined && response.block_number !== null
              ? Number(response.block_number)
              : undefined,
            streaming: response.streaming,
          });
          setLastResponseText(cleanContentForDisplay(response.response));

          // Check if model changed (e.g., via switch_model tool or revolver mode)
          // Use local state to update header without re-rendering entire chat (preserves typewriter)
          if (response.current_model) {
            setCurrentModel(response.current_model);
            // Emit event so other components (roster, etc.) can update
            emit('qube-model-changed', {
              qubeId: selectedQubes[0].qube_id,
              newModel: response.current_model,
              newProvider: response.current_provider,
            });
          }
        } else {
          setError(response.error || 'Failed to get response from qube');
          setFailedMessage(messageToSend); // Store for retry
          setIsLoading(false);
          isSendingRef.current = false;
      setProcessingStage(null);
        }
      }
    } catch (err) {
      console.error('Failed to process message:', err);
      setError(`Backend failed. Please try again or check the logs for details.`);
      setFailedMessage(messageToSend); // Store for retry
      setIsLoading(false);
      isSendingRef.current = false;
      setProcessingStage(null);
    }
  };

  // Retry the failed message
  const handleRetry = async () => {
    if (!failedMessage || isLoading || !userId) return;

    // First, discard the failed block from the backend to avoid duplicate detection
    try {
      await invoke<{ success: boolean; error?: string }>('discard_last_block', {
        userId,
        qubeId: selectedQubes[0].qube_id,
        password,
      });
    } catch (err) {
      console.error('Failed to discard block before retry:', err);
      // Continue anyway - the retry might still work
    }

    setError(null);
    const messageToRetry = failedMessage;
    setFailedMessage(null);

    // Remove the failed user message from the chat (it will be re-added by handleSend)
    const messages = getMessages(selectedQubes[0].qube_id);
    if (messages.length > 0) {
      // Find and remove the last user message
      const lastUserMsgIndex = messages.findLastIndex((m: Message) => m.sender === 'user');
      if (lastUserMsgIndex >= 0) {
        clearMessages(selectedQubes[0].qube_id);
        // Re-add all messages except the last user message
        messages.slice(0, lastUserMsgIndex).forEach((m: Message) => addMessage(selectedQubes[0].qube_id, m));
      }
    }

    // Set input and trigger send after state update
    setInputValue(messageToRetry);
    setTimeout(() => {
      const sendButton = document.querySelector('[data-send-button]') as HTMLButtonElement;
      if (sendButton) sendButton.click();
    }, 100);
  };

  // Discard the failed message and its block
  const handleDiscard = async () => {
    if (!selectedQubes.length || !userId) return;

    try {
      // Call backend to discard the last session block
      const response = await invoke<{ success: boolean; error?: string }>('discard_last_block', {
        userId,
        qubeId: selectedQubes[0].qube_id,
        password,
      });

      if (response.success) {
        // Remove the failed user message from chat
        const messages = getMessages(selectedQubes[0].qube_id);
        if (messages.length > 0) {
          const lastUserMsgIndex = messages.findLastIndex((m: Message) => m.sender === 'user');
          if (lastUserMsgIndex >= 0) {
            clearMessages(selectedQubes[0].qube_id);
            messages.slice(0, lastUserMsgIndex).forEach((m: Message) => addMessage(selectedQubes[0].qube_id, m));
          }
        }
      } else {
        console.error('Failed to discard block:', response.error);
      }
    } catch (err) {
      console.error('Failed to discard block:', err);
    }

    setError(null);
    setFailedMessage(null);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !isLoading) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleEmojiClick = (emojiData: EmojiClickData) => {
    const emoji = emojiData.emoji;
    const textarea = textareaRef.current;

    if (textarea) {
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const text = inputValue;
      const before = text.substring(0, start);
      const after = text.substring(end);

      setInputValue(before + emoji + after);

      // Set cursor position after emoji
      setTimeout(() => {
        textarea.selectionStart = textarea.selectionEnd = start + emoji.length;
        textarea.focus();
      }, 0);
    } else {
      // Fallback if ref not available
      setInputValue(inputValue + emoji);
    }

    // Optionally close picker after selection (or keep it open)
    // setShowEmojiPicker(false);
  };

  const toggleRecording = () => {
    if (!recognitionRef.current) {
      setError('Speech recognition not supported in this browser');
      return;
    }

    if (isRecording) {
      recognitionRef.current.stop();
      setIsRecording(false);
    } else {
      try {
        recognitionRef.current.start();
        setIsRecording(true);
      } catch (err) {
        console.error('Failed to start recording:', err);
        setError('Failed to start voice recording');
      }
    }
  };

  // Simple TTS progress - just show "Generating audio..." while generating
  // No polling needed - the indeterminate animation handles the UX
  useEffect(() => {
    if (isGeneratingTTS) {
      setTtsProgress({ stage: 'generating', progress: 0, message: 'Generating audio...' });
    } else {
      setTtsProgress({ stage: 'idle', progress: 0, message: '' });
    }
  }, [isGeneratingTTS]);

  // Auto-generate and play TTS when qube responds, then show the message
  useEffect(() => {
    const generateAndPlayTTS = async () => {
      // Skip if no response text or pending response
      if (!lastResponseText || !pendingResponse || !userId || !password || selectedQubes.length === 0) {
        return;
      }

      // Streaming path: TTS already handled by backend pipeline
      if (pendingResponse?.streaming) {
        setPendingResponse(null);
        setIsLoading(false);
        isSendingRef.current = false;
        setProcessingStage(null);
        processingResponseRef.current = null;
        return;
      }

      // Guard against double execution (can happen during TTS rate limiting delays)
      // Use the response content as the key - if it's the same response, skip
      const responseKey = pendingResponse.content;
      if (processingResponseRef.current === responseKey) {
        return;
      }
      processingResponseRef.current = responseKey;

      const currentQube = selectedQubes[0];
      const messageId = (Date.now() + 1).toString();
      // Use backend timestamp (in seconds) converted to Date, or fall back to current time
      const messageTimestamp = pendingResponse.timestamp
        ? new Date(pendingResponse.timestamp * 1000)  // Convert seconds to ms
        : new Date();
      const qubeResponse: Message = {
        id: messageId,
        sender: 'qube',
        qubeName: pendingResponse.qubeName,
        content: pendingResponse.content,
        timestamp: messageTimestamp,
        // block_number is the authoritative sequence for ACTION block association
        blockNumber: pendingResponse.blockNumber,
      };

      // Check if TTS is enabled for this qube
      if (currentQube.tts_enabled === true) {
        try {
          // Show TTS generation indicator and reset progress
          setIsGeneratingTTS(true);
          setTtsProgress({ stage: 'idle', progress: 0, message: 'Starting...' });

          // Truncate text for TTS if needed (OpenAI limit: 4096 chars)
          const ttsText = truncateForTTS(lastResponseText);
          // Generate TTS and wait for it to be ready and start playing
          await playTTS(userId, currentQube.qube_id, ttsText, password);

          // Hide TTS generation indicator - audio is now playing
          setIsGeneratingTTS(false);
          setTtsProgress({ stage: 'idle', progress: 0, message: '' });

          // Mark this message as pending typewriter activation
          pendingTypewriterRef.current = messageId;

          // Add message to history FIRST (so DOM element exists)
          addMessage(currentQube.qube_id, qubeResponse);

          // Small delay to let React render the message bubble
          await new Promise(resolve => setTimeout(resolve, 50));

          // NOW activate typewriter - audio is already playing
          pendingTypewriterRef.current = null;
          setActiveTypewriterMessageId(messageId);

          // Clear pending response and stop loading
          setPendingResponse(null);
          setIsLoading(false);
          isSendingRef.current = false;
          setProcessingStage(null);
          processingResponseRef.current = null;
        } catch (err) {
          console.error('[ChatInterface] TTS error:', err);
          setIsGeneratingTTS(false);
          setTtsProgress({ stage: 'idle', progress: 0, message: '' });

          // Don't show error banner for expected skip cases (duplicate text)
          const errMsg = String(err);
          if (!errMsg.includes('TTS skipped')) {
            setError(`TTS error: ${errMsg}`);
          }

          // Even if TTS fails, show the message immediately (no typewriter)
          addMessage(currentQube.qube_id, qubeResponse);
          setPendingResponse(null);
          setIsLoading(false);
          isSendingRef.current = false;
          setProcessingStage(null);
          processingResponseRef.current = null;
        }
      } else {
        // TTS disabled - show message immediately without typewriter effect
        addMessage(currentQube.qube_id, qubeResponse);
        setPendingResponse(null);
        setIsLoading(false);
        isSendingRef.current = false;
      setProcessingStage(null);
        processingResponseRef.current = null;
      }
    };

    generateAndPlayTTS();
  }, [lastResponseText, pendingResponse, userId, password, selectedQubes, playTTS, addMessage]);

  // Poll for active tool calls during message processing
  useEffect(() => {
    if (!isLoading || !userId || !password || selectedQubes.length === 0) {
      setActiveToolCalls([]);
      return;
    }

    const pollInterval = setInterval(async () => {
      const recentActions: Array<{ action_type: string; timestamp: number; target_model?: string }> = [];

      try {
        const result = await invoke<any>('get_qube_blocks', {
          userId,
          qubeId: selectedQubes[0].qube_id,
          password,
          limit: 50
        });

        if (result?.session_blocks && Array.isArray(result.session_blocks)) {
          // Sort blocks by timestamp (most recent first)
          const sortedBlocks = [...result.session_blocks].sort(
            (a: any, b: any) => b.timestamp - a.timestamp
          );

          // Find the timestamp of the most recent MESSAGE block (qube response)
          const lastMessageBlock = sortedBlocks.find(
            (b: any) => b.block_type === 'MESSAGE' && b.content?.message_type === 'qube_to_human'
          );
          const lastMessageTimestamp = lastMessageBlock?.timestamp || 0;

          const now = Date.now();

          // Check for process_document ACTION blocks to switch from 'document' to 'response' stage
          const hasProcessDocumentAction = result.session_blocks.some((b: any) =>
            b.block_type === 'ACTION' &&
            b.content?.action_type === 'process_document' &&
            b.timestamp > lastMessageTimestamp
          );

          if (hasProcessDocumentAction && processingStage === 'document') {
            setProcessingStage('response');
          }

          // Get all ACTION blocks from current turn (after last message)
          // Exclude "process_document" - it's an internal action, not a tool call
          const currentTurnActions = result.session_blocks.filter((b: any) =>
            b.block_type === 'ACTION' &&
            b.content?.action_type &&
            b.content?.action_type !== 'process_document' &&
            b.timestamp > lastMessageTimestamp
          );

          // Track first-seen time for new actions
          currentTurnActions.forEach((b: any) => {
            if (!toolCallFirstSeen.current.has(b.timestamp)) {
              toolCallFirstSeen.current.set(b.timestamp, now);
            }
          });

          // Show actions that are either:
          // 1. Currently in_progress, OR
          // 2. Recently completed but haven't been shown for MIN_TOOL_DISPLAY_MS yet
          currentTurnActions.forEach((b: any) => {
            const firstSeen = toolCallFirstSeen.current.get(b.timestamp) || now;
            const displayedFor = now - firstSeen;

            if (b.content?.status === 'in_progress' || displayedFor < MIN_TOOL_DISPLAY_MS) {
              recentActions.push({
                action_type: b.content.action_type,
                timestamp: b.timestamp,
                // Extract target model for revolver_switch actions
                target_model: b.content?.parameters?.target_model || b.content?.result?.new_model
              });
            }
          });

          // Check for completed switch_model or revolver_switch actions to update model display immediately
          const completedSwitchModels = currentTurnActions.filter((b: any) =>
            (b.content?.action_type === 'switch_model' || b.content?.action_type === 'revolver_switch') &&
            b.content?.status === 'completed' &&
            b.content?.result?.success === true &&
            b.content?.result?.new_model &&
            !processedModelSwitches.current.has(b.timestamp)
          );

          completedSwitchModels.forEach((b: any) => {
            processedModelSwitches.current.add(b.timestamp);
            const newModel = b.content.result.new_model;
            const newProvider = b.content.result.provider;
            setCurrentModel(newModel);
            // Emit event so other components (roster, etc.) can update
            emit('qube-model-changed', {
              qubeId: selectedQubes[0].qube_id,
              newModel: newModel,
              newProvider: newProvider,
            });
          });

          // Store all ACTION blocks (completed or not) for display with messages
          // Include both session and permanent blocks (permanent has post-anchor ACTION blocks)
          const sessionBlocksAll = (result.session_blocks || []).map((b: any) => ({ ...b, _fromSession: true }));
          const permanentBlocksAll = (result.permanent_blocks || []).map((b: any) => ({ ...b, _fromSession: false }));
          const combinedBlocks = [...sessionBlocksAll, ...permanentBlocksAll];

          const pollClearedAt = chatClearedAtRef.current;
          const allActionBlocks = combinedBlocks
            .filter((b: any) =>
              b.block_type === 'ACTION' &&
              b.content?.action_type &&
              b.content?.action_type !== 'process_document' &&
              // Skip blocks from before chat was cleared
              (pollClearedAt === 0 || (b.timestamp * 1000) > pollClearedAt)
            )
            .map((b: any) => ({
              action_type: b.content.action_type,
              timestamp: b.timestamp * 1000,
              blockNumber: b.block_number,  // Sequence number for reliable association
              parameters: b.content.parameters || {},
              result: b.content.result || null,
              status: b.content.status || 'completed',
              fromSession: b._fromSession,
            }))
            .sort((a, b) => a.blockNumber - b.blockNumber);  // Sort by sequence

          setCompletedActionBlocks(allActionBlocks);

          // Clean up old entries from toolCallFirstSeen (actions from previous turns)
          for (const [ts] of toolCallFirstSeen.current) {
            if (ts <= lastMessageTimestamp) {
              toolCallFirstSeen.current.delete(ts);
            }
          }
        }

        if (recentActions.length > 0) {
          const uniqueActions = Array.from(
            new Map(recentActions.map(a => [a.action_type, a])).values()
          );
          setActiveToolCalls(uniqueActions);
        } else {
          setActiveToolCalls([]);
        }
      } catch (err) {
        console.error('Failed to poll for tool calls:', err);
      }
    }, 500);

    return () => clearInterval(pollInterval);
  }, [isLoading, userId, password, selectedQubes]);

  // Load action blocks on mount, when qube changes, and after loading completes
  // Includes both session blocks (pre-anchor) and permanent blocks (post-anchor)
  // Re-runs when isLoading becomes false to ensure action blocks are correctly associated
  // with the newly added message
  useEffect(() => {
    const loadActionBlocks = async () => {
      if (!userId || !password || selectedQubes.length === 0) {
        setCompletedActionBlocks([]);
        return;
      }

      try {
        const result = await invoke<any>('get_qube_blocks', {
          userId,
          qubeId: selectedQubes[0].qube_id,
          password,
          limit: 50
        });

        // Combine session blocks and permanent blocks to get all ACTION blocks
        // After auto-anchor, ACTION blocks move from session to permanent
        const sessionBlocks = (result?.session_blocks || []).map((b: any) => ({ ...b, _fromSession: true }));
        const permanentBlocks = (result?.permanent_blocks || []).map((b: any) => ({ ...b, _fromSession: false }));
        const allBlocks = [...sessionBlocks, ...permanentBlocks];

        const clearedAt = chatClearedAtRef.current;
        const allActionBlocks = allBlocks
          .filter((b: any) =>
            b.block_type === 'ACTION' &&
            b.content?.action_type &&
            b.content?.action_type !== 'process_document' &&
            // Skip blocks from before chat was cleared
            (clearedAt === 0 || (b.timestamp * 1000) > clearedAt)
          )
          .map((b: any) => ({
            action_type: b.content.action_type,
            timestamp: b.timestamp * 1000,
            blockNumber: b.block_number,  // Sequence number for reliable association
            parameters: b.content.parameters || {},
            result: b.content.result || null,
            status: b.content.status || 'completed',
            fromSession: b._fromSession,  // Track origin for current-turn filtering
          }))
          .sort((a, b) => a.blockNumber - b.blockNumber);  // Sort by sequence

        setCompletedActionBlocks(allActionBlocks);
      } catch (err) {
        console.error('Failed to load action blocks:', err);
      }
    };

    // Only reload when NOT loading (either on mount or when loading finishes)
    // This ensures action blocks are reloaded after a response completes
    if (!isLoading) {
      loadActionBlocks();
    }
  }, [userId, password, selectedQubes, isLoading]);

  // Memoized mapping of message index to tool calls
  // Uses block_number (sequence) for reliable association - matches backend order exactly
  // Falls back to timestamp-based association when blockNumber is not available
  const toolCallsByMessageIndex = useMemo(() => {
    const mapping: Map<number, typeof completedActionBlocks> = new Map();

    if (messages.length === 0 || completedActionBlocks.length === 0) {
      return mapping;
    }

    for (let msgIndex = 0; msgIndex < messages.length; msgIndex++) {
      const currentMsg = messages[msgIndex];
      if (currentMsg.sender !== 'qube') continue;

      const currentBlockNumber = currentMsg.blockNumber;
      const currentTimestamp = currentMsg.timestamp.getTime(); // milliseconds

      // Strategy 1: Use blockNumber if available (most reliable)
      if (currentBlockNumber !== undefined && currentBlockNumber !== null) {
        // Find the previous qube message's block number for inter-qube bounding
        let prevQubeBlockNumber: number | null = null;
        for (let i = msgIndex - 1; i >= 0; i--) {
          if (messages[i].sender === 'qube' && messages[i].blockNumber !== undefined) {
            prevQubeBlockNumber = messages[i].blockNumber!;
            break;
          }
        }

        // Also find preceding message timestamp (any sender) for timestamp-based bounding
        // User messages don't have blockNumbers, so we use their timestamp instead
        let prevMessageTimestamp = 0;
        for (let i = msgIndex - 1; i >= 0; i--) {
          prevMessageTimestamp = messages[i].timestamp.getTime();
          break;
        }

        // Find ACTION blocks between previous context and current qube message
        const toolCalls = completedActionBlocks.filter(block => {
          if (prevQubeBlockNumber !== null) {
            // Between two qube messages: use block number range
            return (block.blockNumber < prevQubeBlockNumber && block.blockNumber > currentBlockNumber) ||
                   (block.blockNumber > prevQubeBlockNumber && block.blockNumber < currentBlockNumber);
          }
          if (prevMessageTimestamp > 0) {
            // First qube response but has a preceding user message:
            // use timestamp to only include tool calls from THIS interaction
            return block.timestamp > prevMessageTimestamp && block.timestamp <= currentTimestamp;
          }
          // No previous message at all - only include current-session blocks
          return block.fromSession && block.blockNumber > currentBlockNumber;
        }).sort((a, b) => b.blockNumber - a.blockNumber); // Sort by sequence (descending for negative)

        if (toolCalls.length > 0) {
          mapping.set(msgIndex, toolCalls);
        }
      } else {
        // Strategy 2: Fallback to timestamp-based association
        // Find previous message timestamp (any sender)
        let prevTimestamp = 0;
        for (let i = msgIndex - 1; i >= 0; i--) {
          prevTimestamp = messages[i].timestamp.getTime();
          break;
        }

        // Find ACTION blocks between previous message and current message timestamps
        const toolCalls = completedActionBlocks.filter(block => {
          // ACTION blocks happen during processing, before the message is created
          if (prevTimestamp > 0) {
            return block.timestamp > prevTimestamp && block.timestamp <= currentTimestamp;
          } else {
            // First message - only include current-session blocks before it
            return block.fromSession && block.timestamp <= currentTimestamp;
          }
        }).sort((a, b) => a.timestamp - b.timestamp); // Sort chronologically

        if (toolCalls.length > 0) {
          mapping.set(msgIndex, toolCalls);
        }
      }
    }

    return mapping;
  }, [messages, completedActionBlocks]);

  // Helper to get tool calls for a specific message (uses memoized mapping)
  const getToolCallsForMessage = useCallback((msgIndex: number): typeof completedActionBlocks => {
    return toolCallsByMessageIndex.get(msgIndex) || [];
  }, [toolCallsByMessageIndex]);

  if (selectedQubes.length === 0) {
    return (
      <GlassCard className="flex-1 p-6 flex items-center justify-center">
        <div className="text-center">
          <p className="text-text-secondary mb-2">No qube selected</p>
          <p className="text-text-tertiary text-sm">
            Select a qube from the roster to start chatting
          </p>
        </div>
      </GlassCard>
    );
  }

  return (
    <div className="flex-1 flex flex-col gap-4 overflow-hidden">
      {/* Chat Header - Isolated component to prevent re-renders from breaking typewriter */}
      <ChatHeader qube={selectedQubes[0]} userId={userId || ''} currentModel={currentModel} />

      {/* Messages Area */}
      <GlassCard className="flex-1 p-4 pb-6 overflow-hidden flex flex-col">
        <div
          ref={messagesContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto"
        >
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-text-tertiary text-center">
              Start a conversation with {selectedQubes[0].name}
            </p>
          </div>
        ) : (
          <div className="space-y-4 pb-4">
            {messages.map((msg, msgIndex) => (
              <React.Fragment key={msg.id}>
                {/* Tool call bubbles before qube messages */}
                {msg.sender === 'qube' && getToolCallsForMessage(msgIndex).length > 0 && (
                  <div className="flex justify-start">
                    <div className="max-w-[70%]">
                      {getToolCallsForMessage(msgIndex).map((block, blockIdx) => (
                        <ToolCallBubble
                          key={`${block.timestamp}-${blockIdx}`}
                          toolName={block.action_type}
                          input={block.parameters}
                          result={block.result}
                          status={block.status as 'in_progress' | 'completed' | 'failed'}
                          accentColor={selectedQubes[0].favorite_color}
                          timestamp={block.timestamp}
                        />
                      ))}
                    </div>
                  </div>
                )}
                {/* Message bubble */}
                <div
                  className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[70%] rounded-lg p-3 border-2 ${
                      msg.sender === 'user'
                        ? 'bg-accent-primary/20 text-text-primary border-accent-primary'
                        : 'bg-bg-tertiary text-text-primary'
                    }`}
                    style={
                      msg.sender === 'qube'
                        ? { borderColor: selectedQubes[0].favorite_color }
                        : undefined
                    }
                  >
                  {/* Speaker Name */}
                  <div className="flex items-center gap-2 mb-2">
                    {msg.sender === 'qube' && (
                      <img
                        src={getAvatarPath(selectedQubes[0])}
                        alt={msg.qubeName || selectedQubes[0].name}
                        className="w-8 h-8 rounded-full object-cover border-2"
                        style={{
                          borderColor: selectedQubes[0].favorite_color,
                          boxShadow: `0 0 8px ${selectedQubes[0].favorite_color}60`
                        }}
                        onError={(e) => {
                          const target = e.target as HTMLImageElement;
                          target.style.display = 'none';
                        }}
                      />
                    )}
                    {msg.sender === 'user' ? (
                      <p className="text-sm font-medium" style={{ color: 'var(--accent-primary)' }}>
                        {userId}
                      </p>
                    ) : msg.sender === 'qube' && msg.qubeName ? (
                      <p className="text-sm font-medium" style={{ color: selectedQubes[0].favorite_color }}>
                        {msg.qubeName}
                      </p>
                    ) : null}
                  </div>
                  <div className="whitespace-pre-wrap break-words">
                    {msg.sender === 'qube' && msg.id === pendingTypewriterRef.current ? (
                      // Message waiting for typewriter activation - show loading dots
                      <div className="h-4 flex items-center">
                        <div className="flex gap-1">
                          <div className="w-1.5 h-1.5 bg-accent-primary rounded-full animate-bounce"></div>
                          <div className="w-1.5 h-1.5 bg-accent-primary rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
                          <div className="w-1.5 h-1.5 bg-accent-primary rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                        </div>
                      </div>
                    ) : msg.sender === 'qube' && msg.id === activeTypewriterMessageId ? (
                      <>
                        {/* Render images first (non-typewriter) */}
                        {(() => {
                          // Match both web URLs and local file paths
                          const imageUrlRegex = /(https?:\/\/[^\s\)]+?(?:\.(?:png|jpg|jpeg|gif|webp)|blob\.core\.windows\.net\/[^\s\)]+))/gi;
                          const localPathRegex = /([A-Za-z]:[\\\/][^\s\)]+\.(?:png|jpg|jpeg|gif|webp)|\/[^\s\)]+\.(?:png|jpg|jpeg|gif|webp))/gi;

                          const webImageUrls = msg.content.match(imageUrlRegex) || [];
                          const localImagePaths = msg.content.match(localPathRegex) || [];

                          // Convert local paths to asset:// URLs
                          const convertedLocalUrls = localImagePaths.map(path => convertFileSrc(path));
                          const allImageUrls = [...webImageUrls, ...convertedLocalUrls];

                          return allImageUrls.map((url, index) => (
                            <img
                              key={`img-${index}`}
                              src={url}
                              alt="Generated image"
                              className="max-w-full rounded-lg mb-3 block"
                              style={{ maxHeight: '400px', objectFit: 'contain' }}
                              onLoad={() => {
                                // Save image to disk (only for web URLs, local paths are already saved)
                                if (url.startsWith('http')) {
                                  saveImageToDisk(url);
                                }
                                // Scroll when image finishes loading
                                scrollToBottom();
                              }}
                            />
                          ));
                        })()}
                        {/* Then typewriter effect for cleaned text (without URLs) */}
                        <TypewriterText
                          text={cleanContentForDisplay(msg.content)}
                          audioElement={audioElement}
                          onComplete={() => {
                            setActiveTypewriterMessageId(null);
                            // Scroll after typewriter completes
                            setTimeout(scrollToBottom, 200);
                          }}
                          onTextUpdate={scrollToBottom}
                        />
                      </>
                    ) : (
                      // After typewriter completes or TTS disabled, render with inline images
                      renderMessageContent(msg.content)
                    )}
                  </div>
                  <p className="text-text-tertiary text-xs mt-1">
                    {msg.timestamp.toLocaleTimeString()}
                  </p>
                </div>
              </div>
              </React.Fragment>
            ))}

            {/* Current turn tool calls (shown during loading, TTS generation, AND streaming) */}
            {(isLoading || isGeneratingTTS || isStreaming) && selectedQubes.length > 0 && (() => {
              // Get the block number of the last qube message
              let lastQubeBlockNumber: number | null = null;
              for (let i = messages.length - 1; i >= 0; i--) {
                if (messages[i].sender === 'qube' && messages[i].blockNumber !== undefined) {
                  lastQubeBlockNumber = messages[i].blockNumber!;
                  break;
                }
              }

              // Find tool calls from the current turn (after last qube message)
              // Only show session blocks (not permanent/historical) to avoid showing old session tool calls
              // Session blocks use negative numbers: -1, -2, -3 (more negative = later)
              // So "after" the last qube means: blockNumber < lastQubeBlockNumber
              const currentTurnTools = completedActionBlocks.filter(block => {
                if (!block.fromSession) return false;  // Skip permanent/historical blocks
                if (lastQubeBlockNumber === null) return true;
                return block.blockNumber < lastQubeBlockNumber;
              }).sort((a, b) => b.blockNumber - a.blockNumber); // Sort newest first

              if (currentTurnTools.length === 0) return null;

              return (
                <div className="flex justify-start">
                  <div className="max-w-[70%]">
                    {currentTurnTools.map((block, idx) => (
                      <ToolCallBubble
                        key={`current-${block.blockNumber}-${idx}`}
                        toolName={block.action_type}
                        input={block.parameters}
                        result={block.result}
                        status={block.status as 'in_progress' | 'completed' | 'failed'}
                        accentColor={selectedQubes[0].favorite_color}
                        timestamp={block.timestamp}
                      />
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* TTS Generation Indicator */}
            {isGeneratingTTS && selectedQubes.length > 0 && (
              <div className="flex justify-start">
                <div className="rounded-lg px-4 py-2 border-2" style={{
                  backgroundColor: 'var(--bg-tertiary)',
                  borderColor: selectedQubes[0].favorite_color,
                }}>
                  <div className="flex items-center gap-3">
                    {/* Avatar */}
                    <img
                      src={getAvatarPath(selectedQubes[0])}
                      alt={selectedQubes[0].name}
                      className="w-8 h-8 rounded-full object-cover border-2"
                      style={{
                        borderColor: selectedQubes[0].favorite_color,
                        opacity: 0.6
                      }}
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                      }}
                    />

                    {/* Status text */}
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium" style={{
                          color: selectedQubes[0].favorite_color
                        }}>
                          {selectedQubes[0].name}
                        </span>
                        <span className="text-xs text-text-secondary">
                          Generating audio...
                        </span>
                        <div className="flex gap-1">
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s'
                          }}></div>
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s',
                            animationDelay: '0.2s'
                          }}></div>
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s',
                            animationDelay: '0.4s'
                          }}></div>
                        </div>
                      </div>

                      {/* Progress bar for TTS - always indeterminate animation */}
                      <div className="w-48 h-1.5 rounded-full overflow-hidden" style={{
                        backgroundColor: 'var(--bg-secondary)'
                      }}>
                        <div
                          className="h-full w-1/3"
                          style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animation: 'tts-progress 1.5s ease-in-out infinite'
                          }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Tool Call Indicator */}
            {activeToolCalls.length > 0 && isLoading && !isGeneratingTTS && selectedQubes.length > 0 && (() => {
              const tool = activeToolCalls[activeToolCalls.length - 1];

              const toolDisplay: Record<string, string> = {
                // Regular tools
                'web_search': 'searching the web',
                'generate_image': 'generating image',
                'browse_url': 'browsing URL',
                'memory_search': 'searching memory',
                'list_files': 'listing files',
                'read_file': 'reading file',
                'write_file': 'writing file',
                'run_command': 'running command',
                'calculate': 'calculating',
                // System tools
                'get_system_state': 'checking system state',
                'update_system_state': 'updating system state',
                'switch_model': 'switching model',
                'describe_my_avatar': 'looking in the mirror',
                // AI Reasoning Tools
                'think_step_by_step': 'thinking step by step',
                'self_critique': 'self-critiquing',
                'explore_alternatives': 'exploring alternatives',
                // Social Intelligence Tools (Social & Emotional Learning)
                'get_relationship_context': 'getting relationship context',
                'recall_relationship_history': 'recalling relationship history',
                'analyze_interaction_patterns': 'analyzing patterns',
                'get_relationship_timeline': 'getting timeline',
                'read_emotional_state': 'reading emotional state',
                'track_emotional_patterns': 'tracking emotions',
                'detect_mood_shift': 'detecting mood shift',
                'adapt_communication_style': 'adapting style',
                'match_communication_style': 'matching style',
                'calibrate_tone': 'calibrating tone',
                'steelman': 'steelmanning argument',
                'devils_advocate': 'playing devil\'s advocate',
                'spot_fallacy': 'spotting fallacies',
                'assess_trust_level': 'assessing trust',
                'detect_social_manipulation': 'detecting manipulation',
                'evaluate_request': 'evaluating request',
                // Technical Expertise Tools
                'debug_systematically': 'debugging',
                'research_with_synthesis': 'researching deeply',
                'validate_solution': 'validating solution',
                // Creative Expression Tools
                'brainstorm_variants': 'brainstorming ideas',
                'iterate_design': 'iterating on design',
                'cross_pollinate_ideas': 'cross-pollinating ideas',
                // Knowledge Domains Tools
                'deep_research': 'researching deeply',
                'synthesize_knowledge': 'synthesizing knowledge',
                'explain_like_im_five': 'simplifying explanation',
                // Security & Privacy Tools
                'assess_security_risks': 'assessing security',
                'privacy_impact_analysis': 'analyzing privacy',
                'verify_authenticity': 'verifying authenticity',
                // Games Tools
                'analyze_game_state': 'analyzing game',
                'plan_strategy': 'planning strategy',
                'learn_from_game': 'learning from game',
              };

              // Special handling for revolver_switch - show the model name
              let displayText: string;
              if (tool.action_type === 'revolver_switch' && tool.target_model) {
                displayText = `🎰 switching to ${tool.target_model}`;
              } else {
                displayText = toolDisplay[tool.action_type] || tool.action_type;
              }

              return (
                <div className="flex justify-start">
                  <div className="rounded-lg px-4 py-2 border-2" style={{
                    backgroundColor: 'var(--bg-tertiary)',
                    borderColor: selectedQubes[0].favorite_color,
                  }}>
                    <div className="flex items-center gap-3">
                      <img
                        src={getAvatarPath(selectedQubes[0])}
                        alt={selectedQubes[0].name}
                        className="w-8 h-8 rounded-full object-cover border-2"
                        style={{
                          borderColor: selectedQubes[0].favorite_color,
                          opacity: 0.6
                        }}
                        onError={(e) => {
                          const target = e.target as HTMLImageElement;
                          target.style.display = 'none';
                        }}
                      />
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium" style={{
                          color: selectedQubes[0].favorite_color
                        }}>
                          {selectedQubes[0].name}
                        </span>
                        <span className="text-xs text-text-secondary">
                          {displayText}...
                        </span>
                        <div className="flex gap-1">
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s'
                          }}></div>
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s',
                            animationDelay: '0.2s'
                          }}></div>
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s',
                            animationDelay: '0.4s'
                          }}></div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Streaming message — text grows as tokens arrive */}
            {isStreaming && streamingText && selectedQubes.length > 0 && (
              <div className="flex justify-start">
                <div className="rounded-lg px-4 py-3 max-w-[85%] border-2" style={{
                  backgroundColor: 'var(--bg-tertiary)',
                  borderColor: selectedQubes[0].favorite_color,
                }}>
                  <div className="flex items-start gap-3">
                    <img
                      src={getAvatarPath(selectedQubes[0])}
                      alt={selectedQubes[0].name}
                      className="w-8 h-8 rounded-full object-cover border-2 flex-shrink-0 mt-0.5"
                      style={{ borderColor: selectedQubes[0].favorite_color }}
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                    <div className="text-sm text-text-primary whitespace-pre-wrap break-words">
                      {renderMessageContent(streamingText)}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {isLoading && !isGeneratingTTS && activeToolCalls.length === 0 && selectedQubes.length > 0 && (
              <div className="flex justify-start">
                <div className="rounded-lg px-4 py-2 border-2" style={{
                  backgroundColor: 'var(--bg-tertiary)',
                  borderColor: selectedQubes[0].favorite_color,
                }}>
                  <div className="flex items-center gap-3">
                    {/* Avatar */}
                    <img
                      src={getAvatarPath(selectedQubes[0])}
                      alt={selectedQubes[0].name}
                      className="w-8 h-8 rounded-full object-cover border-2"
                      style={{
                        borderColor: selectedQubes[0].favorite_color,
                        opacity: 0.6
                      }}
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                      }}
                    />

                    {/* Status text */}
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium" style={{
                          color: selectedQubes[0].favorite_color
                        }}>
                          {selectedQubes[0].name}
                        </span>
                        <span className="text-xs text-text-secondary">
                          {processingStage === 'document' ? 'processing document...' : 'processing response...'}
                        </span>
                        <div className="flex gap-1">
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s'
                          }}></div>
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s',
                            animationDelay: '0.2s'
                          }}></div>
                          <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                            backgroundColor: selectedQubes[0].favorite_color,
                            animationDuration: '1s',
                            animationDelay: '0.4s'
                          }}></div>
                        </div>
                      </div>

                      {/* Indeterminate progress bar for document processing */}
                      {processingStage === 'document' && (
                        <div className="w-48 h-1.5 rounded-full overflow-hidden" style={{
                          backgroundColor: 'var(--bg-secondary)'
                        }}>
                          <div
                            className="h-full w-1/3"
                            style={{
                              backgroundColor: selectedQubes[0].favorite_color,
                              animation: 'tts-progress 1.5s ease-in-out infinite'
                            }}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
            {error && (
              <div className="flex justify-center">
                <div className="bg-accent-danger/10 text-accent-danger rounded-lg p-3 text-sm">
                  <div className="mb-2">Error: {error}</div>
                  {failedMessage && (
                    <div className="flex gap-2 justify-center mt-2">
                      <button
                        onClick={handleRetry}
                        disabled={isLoading}
                        className="px-3 py-1 bg-accent-primary hover:bg-accent-primary/80 text-white rounded text-xs font-medium transition-colors disabled:opacity-50"
                      >
                        Retry
                      </button>
                      <button
                        onClick={handleDiscard}
                        disabled={isLoading}
                        className="px-3 py-1 bg-bg-tertiary hover:bg-bg-secondary text-text-secondary rounded text-xs font-medium transition-colors disabled:opacity-50"
                      >
                        Discard
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
            {/* Stream Mode: show user's interim transcript */}
            {isStreamMode && interimTranscript && (
              <div className="flex justify-end">
                <div className="rounded-lg px-4 py-2 max-w-[85%] opacity-70 italic" style={{
                  backgroundColor: 'var(--accent-primary)',
                  color: 'white',
                }}>
                  {interimTranscript}...
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        )}
        </div>
      </GlassCard>

      {/* Wake word pending indicator */}
      {isWakeWordPending && !isStreamMode && selectedQubes.length > 0 && (
        <div className="flex justify-center -mt-2 mb-1">
          <div className="bg-accent-primary/10 border border-accent-primary/30 rounded-full px-4 py-1 text-xs text-accent-primary flex items-center gap-2 animate-pulse">
            <span className="w-2 h-2 rounded-full bg-accent-primary animate-pulse" />
            Wake word detected — listening... ({selectedQubes[0].name})
          </div>
        </div>
      )}

      {/* Stream Mode indicator */}
      {isStreamMode && selectedQubes.length > 0 && (
        <div className="flex justify-center -mt-2 mb-1">
          <div className="bg-accent-primary/10 border border-accent-primary/30 rounded-full px-4 py-1 text-xs text-accent-primary flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${
              isListening ? 'bg-accent-danger animate-pulse'
              : (isPlaying || isLoading || isStreaming) ? 'bg-yellow-500'
              : 'bg-text-secondary'
            }`} />
            Stream Mode {
              isListening ? '— listening'
              : (isPlaying || isLoading || isStreaming) ? '— mic paused'
              : '— waiting'
            } ({selectedQubes[0].name})
            <span className="text-text-secondary ml-1">{(isPlaying || isStreaming) ? 'Space to interrupt' : 'Ctrl+M to stop'}</span>
          </div>
        </div>
      )}

      {/* Input Area */}
      <GlassCard className="p-4">
        {/* WSL2 TTS Warmup Indicator - disabled for now */}

        {/* File Preview Grid */}
        {uploadedFiles.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {uploadedFiles.map((file, index) => (
              <div
                key={`${file.name}-${index}`}
                className="relative p-2 bg-bg-tertiary rounded-lg border-2 border-accent-primary/50 flex flex-col items-center"
                style={{ width: '120px' }}
              >
                {/* File preview */}
                {file.type === 'image' ? (
                  <img
                    src={file.data}
                    alt={file.name}
                    className="w-20 h-20 rounded object-cover mb-1"
                  />
                ) : (
                  <div className="w-20 h-20 rounded bg-accent-primary/20 flex items-center justify-center text-3xl mb-1">
                    📄
                  </div>
                )}

                {/* File name (truncated) */}
                <p className="text-text-primary text-xs font-medium truncate w-full text-center" title={file.name}>
                  {file.name.length > 12 ? file.name.substring(0, 12) + '...' : file.name}
                </p>

                {/* Remove button */}
                <button
                  onClick={() => selectedQubes.length > 0 && removeUploadedFile(selectedQubes[0].qube_id, file.name)}
                  className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-accent-danger text-white hover:bg-accent-danger/80 transition-all flex items-center justify-center text-xs"
                  title="Remove file"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-1.5">
          {/* Voice input button */}
          {recognitionRef.current && !isStreamMode && (
            <button
              onClick={toggleRecording}
              className={`w-10 h-10 flex-shrink-0 flex items-center justify-center rounded-lg border transition-all ${
                isRecording
                  ? 'bg-accent-danger/20 border-accent-danger/50 text-accent-danger animate-pulse'
                  : 'bg-bg-secondary border-accent-primary/30 text-text-secondary hover:text-accent-primary hover:border-accent-primary/50'
              }`}
              title={isRecording ? 'Stop recording' : 'Start voice input'}
              disabled={isLoading}
            >
              <span className="text-xl leading-none">{isRecording ? '🔴' : '🎤'}</span>
            </button>
          )}
          {/* Stream Mode button (Ctrl+M) */}
          {recognitionRef.current && selectedQubes[0]?.tts_enabled && (
            <button
              onClick={toggleStreamMode}
              className={`w-10 h-10 flex-shrink-0 flex items-center justify-center rounded-lg border transition-all ${
                isStreamMode
                  ? 'bg-accent-danger/20 border-accent-danger/50 text-accent-danger animate-pulse'
                  : isWakeWordPending
                    ? 'bg-accent-primary/20 border-accent-primary/50 text-accent-primary animate-pulse'
                    : 'bg-bg-secondary border-accent-primary/30 text-text-secondary hover:text-accent-primary hover:border-accent-primary/50'
              }`}
              title={isStreamMode ? 'Stop Stream Mode (Ctrl+M)' : isWakeWordPending ? 'Wake word detected — listening...' : 'Stream Mode — voice conversation (Ctrl+M)'}
              disabled={isLoading && !isStreamMode}
            >
              <span className="text-xl leading-none">{isStreamMode ? '⏹' : '🎙'}</span>
            </button>
          )}
          <button
            onClick={handleFileUpload}
            className="w-10 h-10 flex-shrink-0 flex items-center justify-center rounded-lg border border-accent-primary/30 transition-all bg-bg-secondary text-text-secondary hover:text-accent-primary hover:border-accent-primary/50"
            title="Upload file or image"
            disabled={isLoading}
          >
            <span className="text-xl leading-none">📎</span>
          </button>
          <button
            onClick={() => setShowEmojiPicker(!showEmojiPicker)}
            className={`w-10 h-10 flex-shrink-0 flex items-center justify-center rounded-lg border transition-all ${
              showEmojiPicker
                ? 'bg-accent-primary/20 border-accent-primary/50 text-accent-primary'
                : 'bg-bg-secondary border-accent-primary/30 text-text-secondary hover:text-accent-primary hover:border-accent-primary/50'
            }`}
            title="Insert emoji"
            disabled={isLoading}
          >
            <span className="text-xl leading-none">😊</span>
          </button>
          {showEmojiPicker && (
            <div className="absolute bottom-20 left-24 z-50" ref={emojiPickerRef}>
              <EmojiPicker
                onEmojiClick={handleEmojiClick}
                theme={Theme.DARK}
                width={350}
                height={450}
                searchPlaceHolder="Search emoji..."
                previewConfig={{ showPreview: false }}
              />
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder={`Message ${selectedQubes[0].name}...`}
            className="flex-1 bg-bg-secondary text-text-primary placeholder-text-tertiary rounded-lg border border-accent-primary/30 px-4 py-2 resize-none focus:outline-none focus:border-accent-primary/50 focus:shadow-glow-primary transition-all"
            rows={1}
            disabled={isLoading}
          />
          <GlassButton
            variant="primary"
            onClick={handleSend}
            disabled={(!inputValue.trim() && uploadedFiles.length === 0) || isLoading}
          >
            Send
          </GlassButton>
        </div>
      </GlassCard>

      {/* Waveform Visualizer Overlay */}
      {selectedQubes.length > 0 && (
        <WaveformOverlay
          audioElement={audioElement}
          audioDataUrl={audioDataUrl}
          isPlaying={isPlayingAudio}
          qubeFavoriteColor={selectedQubes[0].favorite_color}
          avatarUrl={getAvatarPath(selectedQubes[0])}
          waveformStyle={visualizerSettings.waveform_style}
          colorTheme={visualizerSettings.color_theme}
          gradientStyle={visualizerSettings.gradient_style}
          sensitivity={visualizerSettings.sensitivity}
          animationSmoothness={visualizerSettings.animation_smoothness}
          audioOffsetMs={visualizerSettings.audio_offset_ms}
          frequencyRange={visualizerSettings.frequency_range}
          enabled={visualizerSettings.enabled}
          outputMonitor={visualizerSettings.output_monitor}
          onWaveformChange={async (style) => {
            const newSettings = { ...visualizerSettings, waveform_style: style };
            setVisualizerSettings(newSettings);

            // Save to backend
            if (userId && selectedQubes.length > 0) {
              const qubeId = selectedQubes[0].qube_id;
              try {
                await invoke('save_visualizer_settings', {
                  userId,
                  qubeId,
                  settings: JSON.stringify(newSettings),
                  password
                });
                // Invalidate and refresh chain state cache
                invalidateCache(qubeId);
                await loadChainState(qubeId, true);
              } catch (error) {
                console.error('Failed to save waveform style:', error);
              }
            }
          }}
          onToggle={async (enabled) => {
            const newSettings = { ...visualizerSettings, enabled };
            setVisualizerSettings(newSettings);

            // Save to backend
            if (userId && selectedQubes.length > 0) {
              const qubeId = selectedQubes[0].qube_id;
              try {
                await invoke('save_visualizer_settings', {
                  userId,
                  qubeId,
                  settings: JSON.stringify(newSettings),
                  password
                });
                // Invalidate and refresh chain state cache
                invalidateCache(qubeId);
                await loadChainState(qubeId, true);
              } catch (error) {
                console.error('Failed to save visualizer toggle:', error);
              }
            }
          }}
        />
      )}
    </div>
  );
};
