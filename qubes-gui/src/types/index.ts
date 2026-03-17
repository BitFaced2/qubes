export interface Qube {
  qube_id: string;
  name: string;
  genesis_prompt: string;
  ai_provider: string;
  ai_model: string;
  evaluation_model?: string;  // Model used for self-evaluation (separate from main model)
  voice_model?: string;
  tts_enabled?: boolean;  // Whether TTS (voice responses) is enabled
  creator?: string;
  birth_timestamp?: number;
  home_blockchain?: string;
  favorite_color: string;
  nft_category_id?: string;
  mint_txid?: string;
  avatar_url?: string;  // IPFS URL only (https://ipfs.io/ipfs/...) - use avatar_local_path for local files
  avatar_local_path?: string;  // Local file path for use with convertFileSrc()
  created_at: string;
  trust_score?: number;  // Average trust score across all relationships (0-100)
  memory_blocks_count?: number;
  block_breakdown?: Record<string, number>;  // Block counts by type (GENESIS, MESSAGE, THOUGHT, etc.)
  friends_count?: number;  // Number of friends (not just total relationships)
  total_relationships?: number;  // Total number of relationships
  best_friend?: string;  // Entity ID of best friend
  close_friends?: number;  // Number of close friends
  acquaintances?: number;  // Number of acquaintances
  strangers?: number;  // Number of strangers
  highest_trust?: number;  // Highest trust score among relationships (0-100)
  lowest_trust?: number;  // Lowest trust score among relationships (0-100)
  total_messages_sent?: number;  // Total messages sent across all relationships
  total_messages_received?: number;  // Total messages received across all relationships
  total_collaborations?: number;  // Total collaborations across all relationships
  successful_joint_tasks?: number;  // Successful joint tasks
  failed_joint_tasks?: number;  // Failed joint tasks
  avg_reliability?: number;  // Average reliability score (0-100)
  avg_honesty?: number;  // Average honesty score (0-100)
  avg_responsiveness?: number;  // Average responsiveness score (0-100)
  avg_compatibility?: number;  // Average compatibility score (0-100)
  status: 'active' | 'inactive' | 'busy';
  // Additional blockchain metadata
  recipient_address?: string;  // BCH address that owns this NFT
  public_key?: string;  // Public key of the Qube
  genesis_block_hash?: string;  // Hash of the genesis block
  commitment?: string;  // NFT commitment hash
  bcmr_uri?: string;  // IPFS URI for BCMR metadata
  avatar_ipfs_cid?: string;  // IPFS CID for avatar
  network?: string;  // mainnet or testnet
  // Wallet fields (P2SH co-signing wallet)
  wallet_address?: string;  // P2SH address (bitcoincash:p...)
  wallet_owner_pubkey?: string;  // Owner's public key (hex)
  wallet_qube_pubkey?: string;  // Qube's public key for wallet (hex)
}

export type Tab = 'dashboard' | 'blocks' | 'qubes' | 'relationships' | 'skills' | 'economy' | 'settings' | 'connections' | 'games';

// Games Types
export interface GamePlayer {
  type: 'human' | 'qube';
  id?: string;       // Player ID (qube_id for Qubes, user_id for humans)
  qube_id?: string;  // Legacy field, prefer 'id'
  name: string;
}

export interface MoveRecord {
  move_number: number;
  player: 'white' | 'black';
  player_id: string;
  uci: string;
  san: string;
  fen_before: string;
  fen_after: string;
  timestamp: number;
}

export interface GameChatMessage {
  sender_id: string;
  sender_type: 'human' | 'qube';
  message: string;
  timestamp: number;
  trigger?: string;
  move_number?: number;
}

export interface GameState {
  game_id: string;
  game_type: string;
  fen: string;
  moves: MoveRecord[];
  white_player: GamePlayer;
  black_player: GamePlayer;
  status: 'active' | 'completed' | 'abandoned';
  current_turn: 'white' | 'black';
  total_moves: number;
  chat_messages: GameChatMessage[];
  start_time: string;
  your_color?: 'white' | 'black';
  pending_draw_offer?: {
    offered_by: 'white' | 'black';
    timestamp: number;
  } | null;
}

export interface MoveResult {
  success: boolean;
  move_made?: string;
  move_uci?: string;
  fen?: string;
  move_number?: number;
  is_check?: boolean;
  is_checkmate?: boolean;
  is_stalemate?: boolean;
  is_draw?: boolean;
  game_over?: boolean;
  result?: string;
  termination?: string;
  error?: string;
}

export interface QubeSelectionState {
  activeQubeId: string | null;
  selectedQubeIds: string[];
  currentTab: Tab;
}

// Skill System Types
export type SkillTier = 'novice' | 'intermediate' | 'advanced' | 'expert';
export type SkillNodeType = 'avatar' | 'sun' | 'planet' | 'moon';

export interface SkillCategory {
  id: string;
  name: string;
  color: string;
  icon: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  category: string;  // Category ID
  nodeType: SkillNodeType;
  tier: SkillTier;
  level: number;  // 1-100
  xp: number;
  maxXP: number;  // 1000 for sun, 500 for planets, 250 for moons
  unlocked: boolean;
  prerequisite?: string;  // Skill ID of required skill
  toolCallReward?: string;  // Tool call unlocked when skill is maxed
  evidence: (string | { block_id: string; description?: string; xp_gained?: number })[];  // Block IDs or detailed evidence objects
  parentSkill?: string;  // For planets/moons, the parent skill ID
  icon?: string;  // Emoji/icon for planets and moons
  achievement?: boolean;  // For achievement-based skills (Board Games)
  reward?: string;  // Cosmetic reward for achievement skills
}

export interface SkillTreeNode {
  id: string;
  type: SkillNodeType;
  data: Skill | QubeAvatarData;
  position: { x: number; y: number };
  orbitRadius?: number;
  orbitSpeed?: number;
  orbitAngle?: number;
}

export interface QubeAvatarData {
  qubeId: string;
  name: string;
  avatarUrl?: string;
  favoriteColor: string;
}

export interface SkillData {
  skills: Skill[];
  categories: SkillCategory[];
  lastUpdated: string;
}

// Audio Visualizer Types
export type WaveformStyle = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12;
export type ColorTheme = 'qube-color' | 'rainbow' | 'neon-cyan' | 'electric-purple' | 'matrix-green' | 'fire' | 'ice';
export type GradientStyle = 'solid' | 'gradient-dark' | 'gradient-complementary' | 'gradient-analogous';
export type AnimationSmoothness = 'low' | 'medium' | 'high' | 'ultra';

export interface VisualizerSettings {
  enabled: boolean;               // Can be toggled with V key
  waveform_style: WaveformStyle;  // 1-12 (keys 1-9, 0, -, =)
  color_theme: ColorTheme;        // Color scheme for visualization
  gradient_style: GradientStyle;  // Gradient variant when using qube-color
  sensitivity: number;            // 0-100 (default: 50)
  animation_smoothness: AnimationSmoothness;  // Performance/quality trade-off
  audio_offset_ms: number;        // -500 to +500 (default: 0)
  frequency_range: number;        // 1-100 (default: 20) - percentage of frequency spectrum to use
  output_monitor: number;         // 0 = main window overlay, 1+ = external monitor index
  last_updated?: string;          // ISO timestamp
}

export interface WaveformStyleInfo {
  value: WaveformStyle;
  label: string;
  icon: string;
  description: string;
}

export interface ColorThemeInfo {
  value: ColorTheme;
  label: string;
  icon?: string;
  color?: string;
  hasGradientOptions: boolean;
}

// Minting Payment Types
export interface MintingPaymentInfo {
  address: string;
  amount_bch: number;
  amount_satoshis: number;
  payment_uri: string;
  qr_data: string;
  op_return_data?: string;
  op_return_hex?: string;
}

export interface PendingMintingResult {
  success: boolean;
  error?: string;
  qube_id?: string;
  registration_id?: string;
  payment?: MintingPaymentInfo;
  websocket_url?: string;
  expires_at?: string;
  expires_in_seconds?: number;
  qube_name?: string;
}

export type MintingStatus = 'pending' | 'paid' | 'minting' | 'complete' | 'failed' | 'expired';

export interface MintingStatusResult {
  success: boolean;
  error?: string;
  status?: MintingStatus;
  registration_id?: string;
  category_id?: string;
  mint_txid?: string;
  bcmr_ipfs_cid?: string;
  error_message?: string;
  qube?: {
    qube_id: string;
    name: string;
    nft_category_id?: string;
    mint_txid?: string;
  };
}

export interface PendingRegistration {
  qube_id: string;
  registration_id: string;
  payment_address: string;
  payment_amount_bch: number;
  payment_amount_satoshis: number;
  payment_uri: string;
  qr_data: string;
  websocket_url: string;
  expires_at: string;
  expires_in_seconds: number;
  op_return_data?: string;
  op_return_hex?: string;
  created_at: string;
}

// NFT Authentication Types
export interface NftAuthResult {
  success: boolean;
  authenticated?: boolean;
  qube_id?: string;
  public_key?: string;
  category_id?: string;
  nft_verified?: boolean;
  token?: string;
  token_expires_at?: number;
  error?: string;
}

export interface NftAuthToken {
  qube_id: string;
  token: string;
  expires_at: number;
}

export interface NftAuthStatusResult {
  success: boolean;
  qube_id?: string;
  registered?: boolean;
  can_authenticate?: boolean;
  has_nft?: boolean;
  category_id?: string;
  error?: string;
}

// Transaction History Types
export interface TransactionHistoryEntry {
  txid: string;
  tx_type: 'deposit' | 'withdrawal' | 'qube_spend';
  amount: number;  // satoshis
  fee: number;
  counterparty: string | null;
  counterparty_qube_name: string | null;  // Name of Qube for Q2Q transactions
  timestamp: string;  // ISO format
  block_height: number | null;
  confirmations: number;
  memo: string | null;
  is_confirmed: boolean;
  explorer_url: string;
}

export interface TransactionHistoryResponse {
  success: boolean;
  wallet_address?: string;
  transactions?: TransactionHistoryEntry[];
  total_count?: number;
  has_more?: boolean;
  error?: string;
}

// =============================================================================
// CHAIN STATE V2.0 - Consolidated encrypted state
// =============================================================================

export interface ChainStateV2 {
  version: "2.0";
  qube_id: string;
  last_updated: number;

  chain: ChainSection;
  session: SessionSection;
  settings: SettingsSection;
  runtime: RuntimeSection;
  stats: StatsSection;
  skills: SkillsSection;
  relationships: RelationshipsSection;
  financial: FinancialSection;
  mood: MoodSection;
  health: HealthSection;
  attestation: AttestationSection;
}

// Chain Section - Blockchain tracking
export interface ChainSection {
  block_height: number;
  latest_block_hash: string | null;
  genesis_hash: string;
  genesis_timestamp: number;
  total_blocks: number;
  permanent_blocks: number;
  session_blocks: number;
}

// Session Section - Current conversation (ephemeral)
export interface SessionSection {
  session_id: string | null;
  started_at: number | null;
  messages_this_session: number;
  context_window_used: number;
  last_message_at: number | null;
  short_term_memory: unknown[];
}

// Settings Section - GUI-managed settings
export interface SettingsSection {
  // Model Mode (mutually exclusive)
  model_locked: boolean;
  model_locked_to: string | null;
  revolver_mode_enabled: boolean;
  revolver_mode_pool: string[];
  autonomous_mode_enabled: boolean;
  autonomous_mode_pool: string[];

  // Auto-anchor settings
  auto_anchor_enabled: boolean;
  auto_anchor_threshold: number;

  // TTS settings
  tts_enabled: boolean;
  voice_model: string | null;

  // Visualizer settings
  visualizer_enabled: boolean;
  visualizer_settings: VisualizerSettings | null;

  // Trust profile
  trust_profile?: string;
}

// Runtime Section - Active state (ephemeral)
export interface RuntimeSection {
  is_online: boolean;
  current_model: string | null;
  current_provider: string | null;
  last_api_call: number | null;
  pending_tool_calls: string[];
  active_conversation_id: string | null;
}

// Stats Section - Usage metrics
export interface StatsSection {
  total_messages_sent: number;
  total_messages_received: number;
  total_tokens_used: number;
  total_tool_calls: number;
  total_sessions: number;
  total_anchors: number;
  created_at: number;
  first_interaction: number | null;
  last_interaction: number | null;
}

// Skills Section - Unlocked skills only
export interface SkillsSection {
  unlocked: UnlockedSkill[];
  total_xp: number;
  last_xp_gain: number | null;
  history: SkillHistoryEntry[];
}

export interface UnlockedSkill {
  id: string;
  xp: number;
  level: number;
  unlocked_at: number;
  last_updated: number;
}

export interface SkillHistoryEntry {
  timestamp: number;
  skill_id: string;
  xp_gained: number;
  reason: string;
  block_id?: string;
}

// Relationships Section
export interface RelationshipsSection {
  entities: Record<string, RelationshipEntity>;
  total_entities_known: number;
  best_friend: string | null;
  owner: string;
}

export interface RelationshipEntity {
  entity_id: string;
  entity_type: 'human' | 'qube' | 'system';
  relationship_id: string;
  public_key: string | null;

  // Positive metrics (0-100)
  reliability: number;
  honesty: number;
  responsiveness: number;
  expertise: number;
  trust: number;
  friendship: number;
  affection: number;
  respect: number;
  loyalty: number;
  support: number;
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

  // Negative metrics (0-100)
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

  // Interaction stats
  messages_sent: number;
  messages_received: number;
  response_time_avg: number;
  last_interaction: number;
  collaborations: number;
  collaborations_successful: number;
  collaborations_failed: number;

  // Status
  first_contact: number;
  days_known: number;
  has_met: boolean;
  status: 'stranger' | 'acquaintance' | 'friend' | 'close_friend' | 'best_friend';
  is_best_friend: boolean;

  // Clearance
  clearance_profile: string;
  clearance_categories: string[];
  clearance_fields: string[];
}

// Financial Section
export interface FinancialSection {
  wallet: WalletInfo;
  transactions: TransactionsInfo;
  pending: PendingTransactionEntry[];
}

export interface WalletInfo {
  address: string | null;
  balance_satoshis: number;
  balance_bch: number;
  last_sync: number | null;
  utxo_count: number;
}

export interface TransactionsInfo {
  history: TransactionEntry[];
  total_count: number;
  archived_count: number;
}

export interface TransactionEntry {
  txid: string;
  tx_type: 'deposit' | 'withdrawal' | 'qube_spend';
  amount: number;
  timestamp: number;
  block_height: number | null;
  confirmations: number;
  memo: string | null;
}

export interface PendingTransactionEntry {
  txid: string;
  created_at: number;
  amount: number;
  destination: string;
  status: 'pending' | 'broadcast' | 'confirmed' | 'failed';
}

// Mood Section
export interface MoodSection {
  current_mood: string;
  energy_level: number;
  stress_level: number;
  last_mood_update: number | null;
  mood_history: MoodHistoryEntry[];
}

export interface MoodHistoryEntry {
  timestamp: number;
  mood: string;
  energy: number;
  trigger: string | null;
}

// Health Section
export interface HealthSection {
  overall_status: 'healthy' | 'degraded' | 'critical';
  last_health_check: number | null;
  issues: string[];
  integrity_verified: boolean;
  last_integrity_check: number | null;
}

// Attestation Section (ephemeral)
export interface AttestationSection {
  last_attestation: number | null;
  attestation_hash: string | null;
  signed_by: string | null;
  verified: boolean;
}

// =============================================================================
// MODEL PREFERENCES RESPONSE (from Tauri command)
// =============================================================================

export interface ModelPreferencesResponse {
  success: boolean;
  model_locked?: boolean;
  model_locked_to?: string | null;
  revolver_mode?: boolean;
  revolver_mode_pool?: string[];
  autonomous_mode?: boolean;
  autonomous_mode_pool?: string[];
  error?: string;
}

// =============================================================================
// SUPERVISED OWNER GROUP CHAT TYPES
// =============================================================================

export interface OwnerParticipant {
  primary_commitment: string;  // the Qube commitment they used to join
  display_name: string;
  is_local: boolean;
  is_in_session: boolean;      // true = entered willingly; false = left/disconnected
  qubes: SupervisedQube[];
}

export interface SupervisedQube {
  commitment: string;
  qube_id: string | null;      // null for remote qubes
  name: string;
  is_local: boolean;
  is_active: boolean;          // false when owner not in session
  favorite_color?: string;
}

export interface SupervisedMessage {
  id: string;
  sender_type: 'owner' | 'qube';
  sender_commitment: string;
  sender_name: string;
  content: string;
  timestamp: number;
  block_number?: number;       // qube messages only (on-chain blocks)
}
