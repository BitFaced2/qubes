"""
User Preferences Management

Manages non-sensitive user preferences like auto-anchor settings,
UI preferences, and other configuration options.

Unlike API keys, these preferences don't require encryption and are
stored in plain JSON for easy access and modification.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict, field


@dataclass
class BlockPreferences:
    """Preferences for blockchain anchoring behavior."""

    # Individual chat settings
    individual_auto_anchor: bool = True
    individual_anchor_threshold: int = 10  # Anchor every N blocks

    # Group chat settings
    group_auto_anchor: bool = True
    group_anchor_threshold: int = 20  # Anchor threshold for group chats

    # IPFS backup settings
    auto_sync_ipfs_on_anchor: bool = True   # Auto-sync to IPFS after auto-anchor
    auto_sync_ipfs_periodic: bool = True   # Periodic background sync to IPFS
    auto_sync_ipfs_interval: int = 15      # Interval in minutes (5, 15, 30, 60)


@dataclass
class AudioPreferences:
    """Preferences for audio/TTS configuration."""

    # Google Cloud TTS credentials path (optional)
    google_tts_credentials_path: Optional[str] = None


@dataclass
class VoiceLibraryEntry:
    """A saved voice in the user's library."""

    name: str                              # User-given name (e.g., "Wise Mentor")
    voice_type: str                        # "designed" or "cloned"
    created_at: str                        # ISO timestamp
    language: str = "en"                   # Language code (en, zh, ja, ko, de, fr, ru, pt, es, it)

    # For designed voices
    design_prompt: Optional[str] = None    # Natural language description

    # For cloned voices
    clone_audio_path: Optional[str] = None  # Path to audio file
    clone_audio_text: Optional[str] = None  # Transcript of audio

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VoiceLibraryEntry':
        """Create from dictionary."""
        return cls(**data)


@dataclass
class Qwen3Preferences:
    """Qwen3-TTS specific preferences."""

    model_variant: str = "1.7B"            # "1.7B" or "0.6B"
    use_flash_attention: bool = True       # Use FlashAttention2 for memory efficiency

    # Voice library: Dict[voice_id, VoiceLibraryEntry as dict]
    # voice_id is a slugified version of name (e.g., "wise_mentor")
    voice_library: Dict[str, Dict] = field(default_factory=dict)

    # Supported languages for reference
    SUPPORTED_LANGUAGES: List[str] = field(default_factory=lambda: [
        "en",  # English
        "zh",  # Chinese
        "ja",  # Japanese
        "ko",  # Korean
        "de",  # German
        "fr",  # French
        "ru",  # Russian
        "pt",  # Portuguese
        "es",  # Spanish
        "it",  # Italian
    ])


@dataclass
class RelayPreferences:
    """Preferences for the P2P relay node."""

    relay_enabled: bool = True
    relay_listen_port: int = 0                        # 0 = auto-assign
    relay_max_connections: int = 50
    relay_retention_days: int = 7
    relay_custom_peers: List[str] = field(default_factory=list)
    p2pd_binary_path: Optional[str] = None            # None = use bundled binary


@dataclass
class EndpointPreferences:
    """Preferences for network endpoint configuration."""

    fulcrum_nodes: List[str] = field(default_factory=lambda: [
        # Well-known public Fulcrum / Electrum BCH servers (same pre-load as Electron Cash)
        "wss://bch.imaginary.cash:50004",
        "wss://electroncash.de:50004",
        "wss://bch.loping.net:50004",
        "wss://blackie.c3-soft.com:50004",
        "wss://electroncash.dk:50002",
        "wss://electron.jochen-hoenicke.de:51002",
        "wss://bitcoincash.network:50004",
        "wss://bch.soul-dev.com:50002",
    ])
    nostr_relays: List[str] = field(default_factory=lambda: [
        # Well-known public Nostr relays (used as Phase 3 transport fallback)
        "wss://relay.damus.io",
        "wss://nos.lol",
        "wss://relay.nostr.band",
        "wss://relay.snort.social",
        "wss://nostr.wine",
        "wss://relay.current.fyi",
        "wss://relay.primal.net",
        "wss://nostr-pub.wellorder.net",
    ])
    cauldron_indexer: str = "https://indexer.riften.net"
    thorchain_midgard: str = "https://midgard.ninerealms.com/v2"
    meta_icon_service: str = "https://meta.riften.net"


@dataclass
class OnboardingPreferences:
    """Track tutorial completion per tab."""

    # Tutorial seen flags for each tab
    qubes_tutorial_seen: bool = False
    dashboard_tutorial_seen: bool = False
    blocks_tutorial_seen: bool = False
    relationships_tutorial_seen: bool = False
    skills_tutorial_seen: bool = False
    economy_tutorial_seen: bool = False
    settings_tutorial_seen: bool = False

    # Master toggle for showing tutorials
    show_tutorials: bool = True


@dataclass
class MemoryConfig:
    """User-configurable memory recall settings."""

    # Basic Settings
    recall_threshold: float = 15.0      # Minimum relevance score (0-100) to recall a memory
    max_recalls: int = 5                # Maximum memories to inject per query
    decay_rate: float = 0.1             # Temporal decay rate (0.1=slow, 1.0=fast)

    # Advanced Settings - Scoring Weights (must sum to ~1.0)
    semantic_weight: float = 0.4        # Weight for semantic/meaning similarity
    keyword_weight: float = 0.3         # Weight for exact keyword matches
    temporal_weight: float = 0.15       # Weight for recency
    relationship_weight: float = 0.1    # Weight for relationship strength with participants


@dataclass
class DecisionConfig:
    """User-configurable decision intelligence settings."""

    # Trust & Relationship Thresholds (0-100)
    trust_threshold: int = 70
    expertise_threshold: int = 60
    collaboration_threshold: int = 65

    # Self-Evaluation Thresholds (0-100)
    confidence_threshold: int = 60
    humility_threshold: int = 70

    # Influence Levels (0-100%)
    metric_influence: int = 70
    validation_strictness: int = 50  # 0=Soft, 50=Medium, 100=Hard

    # Negative Metric Tolerances (0-100)
    max_antagonism: int = 30
    max_distrust: int = 40
    max_betrayal: int = 10

    # Feature Toggles
    enable_auto_temperature: bool = True
    enable_validation_layer: bool = True
    enable_metric_tools: bool = True
    auto_thresholds: bool = False  # Auto-derive thresholds from self-evaluation


@dataclass
class UserPreferences:
    """Complete user preferences."""

    blocks: BlockPreferences
    audio: AudioPreferences
    memory: MemoryConfig
    decision: DecisionConfig
    onboarding: OnboardingPreferences
    qwen3: Qwen3Preferences
    relay: RelayPreferences
    endpoints: EndpointPreferences

    def __init__(self):
        self.blocks = BlockPreferences()
        self.audio = AudioPreferences()
        self.memory = MemoryConfig()
        self.decision = DecisionConfig()
        self.onboarding = OnboardingPreferences()
        self.qwen3 = Qwen3Preferences()
        self.relay = RelayPreferences()
        self.endpoints = EndpointPreferences()

    def to_dict(self) -> Dict[str, Any]:
        """Convert preferences to dictionary."""
        # Handle Qwen3Preferences specially to exclude SUPPORTED_LANGUAGES (it's a constant)
        qwen3_dict = {
            'model_variant': self.qwen3.model_variant,
            'use_flash_attention': self.qwen3.use_flash_attention,
            'voice_library': self.qwen3.voice_library,
        }
        return {
            'blocks': asdict(self.blocks),
            'audio': asdict(self.audio),
            'memory': asdict(self.memory),
            'decision': asdict(self.decision),
            'onboarding': asdict(self.onboarding),
            'qwen3': qwen3_dict,
            'relay': asdict(self.relay),
            'endpoints': asdict(self.endpoints),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserPreferences':
        """Create preferences from dictionary."""
        prefs = cls()

        if 'blocks' in data:
            prefs.blocks = BlockPreferences(**data['blocks'])

        if 'audio' in data:
            prefs.audio = AudioPreferences(**data['audio'])

        if 'memory' in data:
            prefs.memory = MemoryConfig(**data['memory'])

        if 'decision' in data:
            prefs.decision = DecisionConfig(**data['decision'])

        if 'onboarding' in data:
            prefs.onboarding = OnboardingPreferences(**data['onboarding'])

        if 'qwen3' in data:
            qwen3_data = data['qwen3']
            prefs.qwen3 = Qwen3Preferences(
                model_variant=qwen3_data.get('model_variant', '1.7B'),
                use_flash_attention=qwen3_data.get('use_flash_attention', True),
                voice_library=qwen3_data.get('voice_library', {}),
            )

        if 'relay' in data:
            relay_data = data['relay']
            prefs.relay = RelayPreferences(
                relay_enabled=relay_data.get('relay_enabled', True),
                relay_listen_port=relay_data.get('relay_listen_port', 0),
                relay_max_connections=relay_data.get('relay_max_connections', 50),
                relay_retention_days=relay_data.get('relay_retention_days', 7),
                relay_custom_peers=relay_data.get('relay_custom_peers', []),
                p2pd_binary_path=relay_data.get('p2pd_binary_path', None),
            )

        if 'endpoints' in data:
            ep = data['endpoints']
            prefs.endpoints = EndpointPreferences(
                fulcrum_nodes=ep.get('fulcrum_nodes', EndpointPreferences().fulcrum_nodes),
                nostr_relays=ep.get('nostr_relays', EndpointPreferences().nostr_relays),
                cauldron_indexer=ep.get('cauldron_indexer', "https://indexer.riften.net"),
                thorchain_midgard=ep.get('thorchain_midgard', "https://midgard.ninerealms.com/v2"),
                meta_icon_service=ep.get('meta_icon_service', "https://meta.riften.net"),
            )

        return prefs


class UserPreferencesManager:
    """Manages user preferences storage and retrieval."""

    def __init__(self, user_data_dir: Path):
        """
        Initialize preferences manager.

        Args:
            user_data_dir: Directory for user data (e.g., data/users/{user_id}/)
        """
        self.user_data_dir = Path(user_data_dir)
        self.prefs_file = self.user_data_dir / "preferences.json"

        # Ensure directory exists
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    def load_preferences(self) -> UserPreferences:
        """
        Load user preferences from file.

        Returns:
            UserPreferences object (default if file doesn't exist)
        """
        if not self.prefs_file.exists():
            return UserPreferences()

        try:
            with open(self.prefs_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return UserPreferences.from_dict(data)
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error loading preferences: {e}")
            # Return defaults on error
            return UserPreferences()

    def save_preferences(self, preferences: UserPreferences) -> None:
        """
        Save user preferences to file.

        Args:
            preferences: UserPreferences object to save
        """
        with open(self.prefs_file, 'w', encoding='utf-8') as f:
            json.dump(preferences.to_dict(), f, indent=2)

    def update_block_preferences(
        self,
        individual_auto_anchor: Optional[bool] = None,
        individual_anchor_threshold: Optional[int] = None,
        group_auto_anchor: Optional[bool] = None,
        group_anchor_threshold: Optional[int] = None,
        auto_sync_ipfs_on_anchor: Optional[bool] = None,
        auto_sync_ipfs_periodic: Optional[bool] = None,
        auto_sync_ipfs_interval: Optional[int] = None
    ) -> UserPreferences:
        """
        Update block-related preferences.

        Args:
            individual_auto_anchor: Enable/disable auto-anchor for individual chats
            individual_anchor_threshold: Blocks between anchors for individual chats
            group_auto_anchor: Enable/disable auto-anchor for group chats
            group_anchor_threshold: Blocks between anchors for group chats
            auto_sync_ipfs_on_anchor: Enable/disable auto-sync to IPFS after auto-anchor

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()

        if individual_auto_anchor is not None:
            prefs.blocks.individual_auto_anchor = individual_auto_anchor

        if individual_anchor_threshold is not None:
            if individual_anchor_threshold < 1:
                raise ValueError("Anchor threshold must be at least 1")
            prefs.blocks.individual_anchor_threshold = individual_anchor_threshold

        if group_auto_anchor is not None:
            prefs.blocks.group_auto_anchor = group_auto_anchor

        if group_anchor_threshold is not None:
            if group_anchor_threshold < 1:
                raise ValueError("Anchor threshold must be at least 1")
            prefs.blocks.group_anchor_threshold = group_anchor_threshold

        if auto_sync_ipfs_on_anchor is not None:
            prefs.blocks.auto_sync_ipfs_on_anchor = auto_sync_ipfs_on_anchor

        if auto_sync_ipfs_periodic is not None:
            prefs.blocks.auto_sync_ipfs_periodic = auto_sync_ipfs_periodic

        if auto_sync_ipfs_interval is not None:
            if auto_sync_ipfs_interval not in (15, 30, 45, 60):
                raise ValueError("Sync interval must be 15, 30, 45, or 60 minutes")
            prefs.blocks.auto_sync_ipfs_interval = auto_sync_ipfs_interval

        self.save_preferences(prefs)
        return prefs

    def get_block_preferences(self) -> BlockPreferences:
        """Get block-related preferences."""
        return self.load_preferences().blocks

    def update_google_tts_path(self, path: Optional[str]) -> UserPreferences:
        """
        Update Google Cloud TTS credentials path.

        Args:
            path: Path to Google Cloud service account JSON file (or None to clear)

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()
        prefs.audio.google_tts_credentials_path = path
        self.save_preferences(prefs)
        return prefs

    def get_google_tts_path(self) -> Optional[str]:
        """Get Google Cloud TTS credentials path."""
        return self.load_preferences().audio.google_tts_credentials_path

    def get_audio_preferences(self) -> AudioPreferences:
        """Get audio-related preferences."""
        return self.load_preferences().audio

    # =========================================================================
    # MEMORY RECALL PREFERENCES
    # =========================================================================

    def update_memory_config(self, **kwargs) -> UserPreferences:
        """
        Update memory recall configuration.

        Args:
            **kwargs: Key-value pairs of MemoryConfig fields to update
                - recall_threshold: Minimum relevance score (0-100)
                - max_recalls: Maximum memories to inject per query
                - decay_rate: Temporal decay rate (0.1=slow, 1.0=fast)
                - semantic_weight: Weight for semantic similarity
                - keyword_weight: Weight for keyword matches
                - temporal_weight: Weight for recency
                - relationship_weight: Weight for relationship strength

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()

        for key, value in kwargs.items():
            if hasattr(prefs.memory, key):
                # Validate specific fields
                if key == 'recall_threshold' and not (0 <= value <= 100):
                    raise ValueError("recall_threshold must be between 0 and 100")
                if key == 'max_recalls' and value < 1:
                    raise ValueError("max_recalls must be at least 1")
                if key == 'decay_rate' and not (0 < value <= 1.0):
                    raise ValueError("decay_rate must be between 0 and 1.0")
                if key in ('semantic_weight', 'keyword_weight', 'temporal_weight', 'relationship_weight'):
                    if not (0 <= value <= 1.0):
                        raise ValueError(f"{key} must be between 0 and 1.0")

                setattr(prefs.memory, key, value)

        self.save_preferences(prefs)
        return prefs

    def get_memory_config(self) -> MemoryConfig:
        """Get memory recall configuration."""
        return self.load_preferences().memory

    # =========================================================================
    # DECISION INTELLIGENCE PREFERENCES
    # =========================================================================

    def update_decision_config(self, **kwargs) -> UserPreferences:
        """
        Update decision intelligence configuration.

        Args:
            **kwargs: Key-value pairs of DecisionConfig fields to update

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()

        for key, value in kwargs.items():
            if hasattr(prefs.decision, key):
                setattr(prefs.decision, key, value)

        self.save_preferences(prefs)
        return prefs

    def get_decision_config(self) -> DecisionConfig:
        """Get decision intelligence configuration."""
        return self.load_preferences().decision

    def reset_to_defaults(self) -> UserPreferences:
        """Reset all preferences to defaults."""
        prefs = UserPreferences()
        self.save_preferences(prefs)
        return prefs

    # =========================================================================
    # ONBOARDING PREFERENCES
    # =========================================================================

    def get_onboarding_preferences(self) -> OnboardingPreferences:
        """Get onboarding tutorial preferences."""
        return self.load_preferences().onboarding

    def mark_tutorial_seen(self, tab_name: str) -> UserPreferences:
        """
        Mark a specific tab's tutorial as seen.

        Args:
            tab_name: Tab identifier (qubes, dashboard, blocks, etc.)

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()
        field_name = f"{tab_name}_tutorial_seen"

        if hasattr(prefs.onboarding, field_name):
            setattr(prefs.onboarding, field_name, True)
            self.save_preferences(prefs)

        return prefs

    def reset_tutorial(self, tab_name: str) -> UserPreferences:
        """
        Reset a specific tab's tutorial to unseen.

        Args:
            tab_name: Tab identifier (qubes, dashboard, blocks, etc.)

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()
        field_name = f"{tab_name}_tutorial_seen"

        if hasattr(prefs.onboarding, field_name):
            setattr(prefs.onboarding, field_name, False)
            self.save_preferences(prefs)

        return prefs

    def reset_all_tutorials(self) -> UserPreferences:
        """Reset all tutorials to unseen."""
        prefs = self.load_preferences()
        prefs.onboarding = OnboardingPreferences()  # Reset to defaults (all False)
        self.save_preferences(prefs)
        return prefs

    def update_show_tutorials(self, show: bool) -> UserPreferences:
        """
        Update the master toggle for showing tutorials.

        Args:
            show: Whether to show tutorial prompts

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()
        prefs.onboarding.show_tutorials = show
        self.save_preferences(prefs)
        return prefs

    # =========================================================================
    # QWEN3-TTS VOICE LIBRARY MANAGEMENT
    # =========================================================================

    def _slugify(self, name: str) -> str:
        """Convert a voice name to a URL-safe slug (voice_id)."""
        # Lowercase, replace spaces with underscores, remove non-alphanumeric
        slug = name.lower().strip()
        slug = re.sub(r'\s+', '_', slug)
        slug = re.sub(r'[^a-z0-9_]', '', slug)
        return slug or 'unnamed'

    def get_voice_library(self) -> Dict[str, VoiceLibraryEntry]:
        """
        Get all voices in the user's library.

        Returns:
            Dict mapping voice_id to VoiceLibraryEntry
        """
        prefs = self.load_preferences()
        library = {}
        for voice_id, voice_data in prefs.qwen3.voice_library.items():
            try:
                library[voice_id] = VoiceLibraryEntry.from_dict(voice_data)
            except (TypeError, KeyError):
                # Skip malformed entries
                pass
        return library

    def get_voice_by_id(self, voice_id: str) -> Optional[VoiceLibraryEntry]:
        """
        Get a specific voice by its ID.

        Args:
            voice_id: The voice identifier (slug)

        Returns:
            VoiceLibraryEntry or None if not found
        """
        prefs = self.load_preferences()
        voice_data = prefs.qwen3.voice_library.get(voice_id)
        if voice_data:
            try:
                return VoiceLibraryEntry.from_dict(voice_data)
            except (TypeError, KeyError):
                pass
        return None

    def add_voice_to_library(
        self,
        name: str,
        voice_type: str,
        language: str = "en",
        design_prompt: Optional[str] = None,
        clone_audio_path: Optional[str] = None,
        clone_audio_text: Optional[str] = None,
    ) -> str:
        """
        Add a new voice to the library.

        Args:
            name: User-given name for the voice
            voice_type: "designed" or "cloned"
            language: Language code (default: "en")
            design_prompt: For designed voices, the natural language description
            clone_audio_path: For cloned voices, path to the audio file
            clone_audio_text: For cloned voices, transcript of the audio

        Returns:
            voice_id: The generated voice identifier

        Raises:
            ValueError: If voice_type is invalid or required fields are missing
        """
        if voice_type not in ("designed", "cloned"):
            raise ValueError(f"Invalid voice_type: {voice_type}. Must be 'designed' or 'cloned'")

        if voice_type == "designed" and not design_prompt:
            raise ValueError("design_prompt is required for designed voices")

        if voice_type == "cloned" and (not clone_audio_path or not clone_audio_text):
            raise ValueError("clone_audio_path and clone_audio_text are required for cloned voices")

        # Generate voice_id from name
        voice_id = self._slugify(name)

        # Ensure uniqueness by appending number if needed
        prefs = self.load_preferences()
        base_id = voice_id
        counter = 1
        while voice_id in prefs.qwen3.voice_library:
            voice_id = f"{base_id}_{counter}"
            counter += 1

        # Create the entry
        entry = VoiceLibraryEntry(
            name=name,
            voice_type=voice_type,
            created_at=datetime.utcnow().isoformat(),
            language=language,
            design_prompt=design_prompt,
            clone_audio_path=clone_audio_path,
            clone_audio_text=clone_audio_text,
        )

        # Save to preferences
        prefs.qwen3.voice_library[voice_id] = entry.to_dict()
        self.save_preferences(prefs)

        return voice_id

    def update_voice_in_library(self, voice_id: str, **updates) -> bool:
        """
        Update an existing voice in the library.

        Args:
            voice_id: The voice identifier
            **updates: Fields to update (name, language, design_prompt, clone_audio_text)

        Returns:
            True if updated, False if voice not found
        """
        prefs = self.load_preferences()

        if voice_id not in prefs.qwen3.voice_library:
            return False

        voice_data = prefs.qwen3.voice_library[voice_id]

        # Update allowed fields
        allowed_fields = {'name', 'language', 'design_prompt', 'clone_audio_text', 'clone_audio_path'}
        for key, value in updates.items():
            if key in allowed_fields:
                voice_data[key] = value

        prefs.qwen3.voice_library[voice_id] = voice_data
        self.save_preferences(prefs)
        return True

    def delete_voice_from_library(self, voice_id: str) -> bool:
        """
        Delete a voice from the library.

        Args:
            voice_id: The voice identifier

        Returns:
            True if deleted, False if voice not found
        """
        prefs = self.load_preferences()

        if voice_id not in prefs.qwen3.voice_library:
            return False

        del prefs.qwen3.voice_library[voice_id]
        self.save_preferences(prefs)
        return True

    def get_qwen3_preferences(self) -> Qwen3Preferences:
        """Get Qwen3-TTS preferences."""
        return self.load_preferences().qwen3

    def update_qwen3_preferences(
        self,
        model_variant: Optional[str] = None,
        use_flash_attention: Optional[bool] = None,
    ) -> UserPreferences:
        """
        Update Qwen3-TTS preferences.

        Args:
            model_variant: "1.7B" or "0.6B"
            use_flash_attention: Whether to use FlashAttention2

        Returns:
            Updated UserPreferences object
        """
        prefs = self.load_preferences()

        if model_variant is not None:
            if model_variant not in ("1.7B", "0.6B"):
                raise ValueError(f"Invalid model_variant: {model_variant}. Must be '1.7B' or '0.6B'")
            prefs.qwen3.model_variant = model_variant

        if use_flash_attention is not None:
            prefs.qwen3.use_flash_attention = use_flash_attention

        self.save_preferences(prefs)
        return prefs

    # =========================================================================
    # RELAY PREFERENCES
    # =========================================================================

    def get_relay_preferences(self) -> RelayPreferences:
        """Get relay node preferences."""
        return self.load_preferences().relay

    def update_relay_preferences(self, **kwargs) -> UserPreferences:
        """Update relay node preferences."""
        prefs = self.load_preferences()
        for key, value in kwargs.items():
            if hasattr(prefs.relay, key):
                setattr(prefs.relay, key, value)
        self.save_preferences(prefs)
        return prefs

    # =========================================================================
    # ENDPOINT PREFERENCES
    # =========================================================================

    def get_endpoint_preferences(self) -> EndpointPreferences:
        """Get network endpoint preferences."""
        return self.load_preferences().endpoints

    def update_endpoint_preferences(self, **kwargs) -> UserPreferences:
        """Update network endpoint preferences."""
        prefs = self.load_preferences()
        for key, value in kwargs.items():
            if hasattr(prefs.endpoints, key):
                setattr(prefs.endpoints, key, value)
        self.save_preferences(prefs)
        return prefs

    def reset_endpoint_preferences(self) -> UserPreferences:
        """Reset endpoint preferences to defaults."""
        prefs = self.load_preferences()
        prefs.endpoints = EndpointPreferences()
        self.save_preferences(prefs)
        return prefs

    def get_voice_clones_dir(self) -> Path:
        """
        Get the directory for storing voice clone audio files.

        Returns:
            Path to ~/.qubes/voice_clones/{user_id}/
        """
        # user_data_dir is typically data/users/{user_id}/
        # We want ~/.qubes/voice_clones/{user_id}/
        user_id = self.user_data_dir.name
        clones_dir = Path.home() / ".qubes" / "voice_clones" / user_id
        clones_dir.mkdir(parents=True, exist_ok=True)
        return clones_dir
