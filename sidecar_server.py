#!/usr/bin/env python3
"""
Persistent sidecar server for Qubes.

Launched via: gui_bridge.py server
Reads JSONL requests from stdin, dispatches to GUIBridge methods, writes JSONL
responses to stdout. Maintains cached state (bridges, master keys, loaded qubes)
between requests for dramatically reduced latency.

Protocol:
  Request:  {"id": "req_1", "command": "send-message", "params": {...}, "secrets": {...}}
  Response: {"id": "req_1", "result": {...}}
  Error:    {"id": "req_1", "error": "message"}
  Stream:   {"id": "req_1", "stream": "tool_call", "data": {...}}
  Ready:    {"id": null, "ready": true}
"""
import json
import asyncio
import sys
import os
import contextvars
import inspect
import traceback
from pathlib import Path
from typing import Dict, Any, Optional, Union

import logging

# CRITICAL: Redirect ALL logging to stderr before any module imports.
# Stdout is reserved exclusively for JSONL protocol with the Rust sidecar manager.
# Without this, structlog/logging from imported modules (crypto, etc.) would
# pollute the JSONL channel, causing parse errors in the Rust reader thread.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
# Also redirect structlog if it's available
try:
    import structlog
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
except ImportError:
    pass

logger = logging.getLogger("sidecar_server")


# ============================================================================
# Context variables for concurrent request tracking
# ============================================================================
_current_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "sidecar_request_id", default=None
)
_current_server: contextvars.ContextVar[Optional["SidecarServer"]] = contextvars.ContextVar(
    "sidecar_server", default=None
)


def _sidecar_tool_event_callback(event_data: dict):
    """Callback installed into ai.tools.registry for sidecar-mode tool event streaming."""
    request_id = _current_request_id.get()
    server = _current_server.get()
    if request_id and server and server._loop and server._loop.is_running():
        asyncio.run_coroutine_threadsafe(
            server.send_stream(request_id, "tool_call", event_data),
            server._loop,
        )


# ============================================================================
# Command classification
# ============================================================================

# Pre-bridge: no GUIBridge needed, handled before bridge construction
PRE_BRIDGE_COMMANDS = {
    "check-first-run",
    "create-user-account",
    "delete-user-account",
    "migrate-user-data",
    "check-ollama-status",
    "get-available-models",
    "get-difficulty-presets",
    "install-wsl2",
    "get-wsl2-tts-managed-status",
    "warm-up",
}

# Commands that invalidate qube cache after execution
CACHE_INVALIDATING = {
    "delete-qube", "reset-qube", "anchor-session", "discard-session",
    "create-qube", "transfer-qube", "finalize-transfer-wc", "update-qube-config", "update-qube-avatar", "import-qube", "import-account-backup", "import-account-backup-ipfs",
    "delete-session-block", "discard-last-block", "finalize-qube-mint",
}

# Map commands to their positional arg names (from Rust args vec).
# For auto-dispatched commands, positional resolution uses GUIBridge method
# introspection. This map is needed for commands with explicit _handle_* methods.
POSITIONAL_ARG_NAMES = {
    # Sessions
    "anchor-session": ["user_id", "qube_id"],
    "check-sessions": ["user_id", "qube_id"],
    "discard-session": ["user_id", "qube_id"],
    "delete-session-block": ["user_id", "qube_id", "block_number", "timestamp"],
    "discard-last-block": ["user_id", "qube_id"],
    # Chat
    "send-message": ["user_id", "qube_id", "message"],
    # Voice / TTS
    "generate-speech": ["user_id", "qube_id", "text"],
    "get-voice-settings": ["user_id", "qube_id"],
    "update-voice-settings": ["user_id", "qube_id"],
    "preview-voice": ["user_id", "text", "voice_type"],
    "add-voice-to-library": ["user_id", "name", "voice_type"],
    "delete-voice-from-library": ["user_id", "voice_id"],
    "transcribe-audio": ["user_id", "audio_path"],
    # Qwen3 / GPU
    "download-qwen3-model": ["user_id", "model_name"],
    "get-qwen3-download-progress": ["user_id", "download_id"],
    "cancel-qwen3-download": ["user_id", "download_id"],
    "delete-qwen3-model": ["user_id", "model_name"],
    "get-gpu-install-progress": ["user_id", "install_id"],
    # Qube management
    "get-qube-blocks": ["user_id", "qube_id", "limit", "offset"],
    "recall-last-context": ["user_id", "qube_id"],
    "delete-qube": ["user_id", "qube_id", "--sweep-address"],
    "reset-qube": ["user_id", "qube_id"],
    "update-qube-avatar": ["user_id", "qube_id", "avatar_path"],
    "save-image": ["user_id", "qube_id", "image_url"],
    "upload-avatar-to-ipfs": ["user_id", "qube_id"],
    "analyze-image": ["user_id", "qube_id", "image_base64", "user_message"],
    # API keys
    "get-configured-api-keys": ["user_id"],
    "save-api-key": ["user_id", "provider"],
    "validate-api-key": ["user_id", "provider"],
    "delete-api-key": ["user_id", "provider"],
    "reload-ai-keys": ["user_id", "qube_id"],
    # Preferences
    "get-block-preferences": ["user_id"],
    "update-block-preferences": ["user_id"],
    "get-relationship-difficulty": ["user_id"],
    "set-relationship-difficulty": ["user_id", "difficulty"],
    "get-google-tts-path": ["user_id"],
    "set-google-tts-path": ["user_id", "path"],
    "get-decision-config": ["user_id"],
    "update-decision-config": ["user_id", "config_json"],
    "get-memory-config": ["user_id"],
    "update-memory-config": ["user_id", "config_json"],
    "get-onboarding-preferences": ["user_id"],
    "mark-tutorial-seen": ["user_id", "tab_name"],
    "reset-tutorial": ["user_id", "tab_name"],
    "reset-all-tutorials": ["user_id"],
    "update-show-tutorials": ["user_id", "show"],
    # Relationships (password now in secrets, not positional)
    "get-qube-relationships": ["user_id", "qube_id"],
    "get-relationship-timeline": ["user_id", "qube_id", "entity_id"],
    "get-clearance-profiles": ["user_id", "qube_id"],
    "get-available-tags": ["user_id", "qube_id"],
    "get-trait-definitions": ["user_id", "qube_id"],
    "suggest-clearance": ["user_id", "qube_id", "entity_id"],
    "add-relationship-tag": ["user_id", "qube_id", "entity_id", "tag"],
    "remove-relationship-tag": ["user_id", "qube_id", "entity_id", "tag"],
    # Clearance (password now in secrets)
    "get-pending-clearance-requests": ["user_id", "qube_id"],
    "approve-clearance-request": ["user_id", "qube_id", "request_id", "expires_in_days"],
    "deny-clearance-request": ["user_id", "qube_id", "request_id", "reason"],
    "get-clearance-audit-log": ["user_id", "qube_id"],
    "set-relationship-clearance": ["user_id", "qube_id", "entity_id", "profile", "field_grants", "field_denials", "expires_in_days"],
    # Owner info (password now in secrets)
    "get-owner-info": ["user_id", "qube_id"],
    "set-owner-info-field": ["user_id", "qube_id", "category", "key", "value"],
    "delete-owner-info-field": ["user_id", "qube_id", "category", "key"],
    "update-owner-info-sensitivity": ["user_id", "qube_id", "category", "key", "sensitivity"],
    # Skills
    "get-qube-skills": ["user_id", "qube_id"],
    "save-qube-skills": ["user_id", "qube_id", "skills_data"],
    "add-skill-xp": ["user_id", "qube_id", "skill_id", "xp_amount", "evidence_block_id"],
    "unlock-skill": ["user_id", "qube_id", "skill_id"],
    # Model preferences
    "get-model-preferences": ["user_id", "qube_id"],
    "set-model-lock": ["user_id", "qube_id", "locked", "model_name"],
    "set-revolver-mode": ["user_id", "qube_id", "enabled"],
    "set-revolver-mode-pool": ["user_id", "qube_id", "models"],
    "get-revolver-mode-pool": ["user_id", "qube_id"],
    "set-autonomous-mode-pool": ["user_id", "qube_id", "models"],
    "get-autonomous-mode-pool": ["user_id", "qube_id"],
    "set-autonomous-mode": ["user_id", "qube_id", "enabled"],
    "clear-model-preferences": ["user_id", "qube_id"],
    "reset-model-to-genesis": ["user_id", "qube_id"],
    # Multi-qube conversations
    "start-multi-qube-conversation": ["user_id", "qube_ids", "initial_prompt", "conversation_mode"],
    "get-next-speaker": ["user_id", "conversation_id"],
    "continue-multi-qube-conversation": ["user_id", "conversation_id", "skip_tools", "participant_ids"],
    "run-background-turns": ["user_id", "conversation_id", "exclude_ids"],
    "inject-multi-qube-user-message": ["user_id", "conversation_id", "message"],
    "lock-in-multi-qube-response": ["user_id", "conversation_id", "timestamp"],
    "end-multi-qube-conversation": ["user_id", "conversation_id", "anchor"],
    # Event watchers
    "watch-events": ["user_id", "qube_id"],
    "stop-watch-events": ["user_id", "qube_id"],
    # Auth / NFT
    "refresh-auth-token": [],
    "get-auth-status": ["qube_id"],
    "authenticate-nft": ["user_id", "qube_id"],
    "get-debug-prompt": ["qube_id"],
    # Export / Import
    "export-qube": ["qube_id", "export_path"],
    "import-qube": ["import_path"],
    "export-account-backup": ["user_id", "export_path"],
    "import-account-backup": ["user_id", "import_path"],
    "export-account-backup-ipfs": ["user_id"],
    "list-account-backups-pinata": ["user_id"],
    "import-account-backup-ipfs": ["user_id", "ipfs_cid"],
    "sync-qube-to-ipfs-backup": ["user_id", "qube_id"],
    "cleanup-incomplete-qubes": ["user_id"],
    "prepare-transfer-wc": ["user_id", "qube_id", "recipient_address", "recipient_public_key", "sender_address"],
    "finalize-transfer-wc": ["user_id", "pending_transfer_id", "transfer_txid"],
    # API key batch
    "save-api-keys": ["user_id"],
    "update-qube-nft": ["user_id", "qube_id"],
    # Visualizer / trust
    "get-visualizer-settings": ["user_id", "qube_id", "password"],
    "save-visualizer-settings": ["user_id", "qube_id", "settings", "password"],
    "get-trust-personality": ["user_id", "qube_id"],
    "update-trust-personality": ["user_id", "qube_id", "trust_profile"],
    # P2P / introductions
    "generate-introduction": ["user_id", "qube_id", "to_commitment", "to_name", "to_description"],
    "evaluate-introduction": ["user_id", "qube_id", "from_name", "intro_message"],
    "process-p2p-message": ["user_id", "qube_id"],
    "send-introduction": ["user_id", "qube_id", "to_commitment", "message"],
    "get-pending-introductions": ["user_id", "qube_id"],
    "accept-introduction": ["user_id", "qube_id", "relay_id"],
    "reject-introduction": ["user_id", "qube_id", "relay_id"],
    "get-connections": ["user_id", "qube_id"],
    "create-p2p-session": ["user_id", "qube_id"],
    "create-supervised-session": ["user_id", "qube_id", "owner_commitments", "local_qubes", "remote_commitments", "topic", "password"],
    "get-p2p-sessions": ["user_id", "qube_id"],
    # Pre-bridge
    "create-user-account": ["user_id"],
    "delete-user-account": ["user_id"],
    "migrate-user-data": ["old_data_dir", "user_id"],
    "check-first-run": [],
    # Local TTS model management
    "check-local-tts-models": ["user_id"],
    "update-local-tts-models": ["user_id"],
}


# ============================================================================
# State management
# ============================================================================

class SidecarState:
    """Holds cached state across commands."""

    MAX_CACHED_QUBES = 20

    def __init__(self):
        self.bridges: Dict[str, Any] = {}       # user_id -> GUIBridge
        self.master_keys: Dict[str, bytes] = {}  # user_id -> derived PBKDF2 key
        self.event_subs: Dict[str, Any] = {}     # "user:qube" -> unsubscribe callable
        self._lock = asyncio.Lock()

    async def get_bridge(self, user_id: str, password: str = None):
        """Get or create a GUIBridge, with master key caching."""
        from gui_bridge import GUIBridge

        async with self._lock:
            if user_id not in self.bridges:
                self.bridges[user_id] = GUIBridge(user_id=user_id)

            bridge = self.bridges[user_id]

            if password:
                if user_id not in self.master_keys:
                    # First time: expensive PBKDF2 derivation (~300ms)
                    bridge.orchestrator.set_master_key(password)
                    self.master_keys[user_id] = bridge.orchestrator.master_key
                    logger.info(f"master_key_derived_and_cached user_id={user_id}")
                elif not bridge.orchestrator.master_key:
                    # Restore cached key (0ms)
                    bridge.orchestrator.master_key = self.master_keys[user_id]

            # LRU eviction if too many qubes loaded
            if len(bridge.orchestrator.qubes) > self.MAX_CACHED_QUBES:
                qube_ids = list(bridge.orchestrator.qubes.keys())
                for qid in qube_ids[: len(qube_ids) - self.MAX_CACHED_QUBES]:
                    del bridge.orchestrator.qubes[qid]

            return bridge

    def invalidate_qube(self, user_id: str, qube_id: str):
        """Remove a qube from cache (after mutation)."""
        if user_id in self.bridges:
            self.bridges[user_id].orchestrator.qubes.pop(qube_id, None)

    def invalidate_user(self, user_id: str):
        """Remove all state for a user (on logout)."""
        self.bridges.pop(user_id, None)
        self.master_keys.pop(user_id, None)
        # Unsubscribe all event watchers for this user
        to_remove = [k for k in self.event_subs if k.startswith(f"{user_id}:")]
        for k in to_remove:
            unsub = self.event_subs.pop(k, None)
            if callable(unsub):
                unsub()


# ============================================================================
# Sidecar server
# ============================================================================

class SidecarServer:
    """JSONL server: reads requests from stdin, writes responses to stdout."""

    AUTO_SYNC_CHECK_INTERVAL = 60  # Check preferences every 60 seconds

    def __init__(self):
        self.state = SidecarState()
        self._output_lock = asyncio.Lock()
        self._running = True
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_tasks: set = set()  # Track in-flight request tasks
        self._auto_sync_task: Optional[asyncio.Task] = None
        self._last_sync_times: Dict[str, float] = {}  # qube_id -> last sync timestamp

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self):
        """Main JSONL loop reading from stdin."""
        self._loop = asyncio.get_event_loop()

        # Install tool event callback for sidecar mode
        try:
            from ai.tools.registry import set_tool_event_callback
            set_tool_event_callback(_sidecar_tool_event_callback)
        except (ImportError, AttributeError):
            logger.warning("tool_event_callback_not_installed")

        # Signal ready to Rust
        await self.send_response({"id": None, "ready": True})
        logger.info("sidecar_server_ready")

        # Launch auto-sync background task
        self._auto_sync_task = asyncio.create_task(self._auto_sync_loop())

        # Read stdin lines in executor (non-blocking)
        while self._running:
            try:
                line = await self._loop.run_in_executor(None, sys.stdin.readline)
            except (EOFError, OSError):
                break

            if not line:
                break  # stdin closed = Rust killed the pipe

            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error(f"invalid_jsonl_request error={e} line={line[:200]}")
                continue

            if request.get("command") == "shutdown":
                logger.info("shutdown_requested")
                break

            # Dispatch as concurrent task (error-isolated)
            task = asyncio.create_task(self._safe_handle(request))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

        # Cancel auto-sync
        if self._auto_sync_task:
            self._auto_sync_task.cancel()
            try:
                await self._auto_sync_task
            except asyncio.CancelledError:
                pass

        # Drain in-flight tasks before exiting (with timeout to avoid hanging)
        if self._pending_tasks:
            logger.info(f"draining_pending_tasks count={len(self._pending_tasks)}")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._pending_tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"drain_timeout remaining={len(self._pending_tasks)}")

        self._running = False
        # Cleanup event subscriptions
        for key, unsub in list(self.state.event_subs.items()):
            if callable(unsub):
                unsub()
        self.state.event_subs.clear()
        logger.info("sidecar_server_stopped")

    async def _safe_handle(self, request: dict):
        """Handle a request with full error isolation."""
        request_id = request.get("id", "unknown")
        try:
            await self.handle_request(request)
        except Exception as e:
            logger.error(f"unhandled_error request_id={request_id} error={e}", exc_info=True)
            await self.send_response({"id": request_id, "error": str(e)})

    # ------------------------------------------------------------------
    # Auto-sync background task (every 15 minutes)
    # ------------------------------------------------------------------

    def _get_sync_preferences(self):
        """Read periodic sync preferences from user settings."""
        # Use the first available bridge to read preferences
        bridge = next(iter(self.state.bridges.values()), None)
        if not bridge or not bridge.orchestrator:
            return False, 900  # disabled, 15 min default
        try:
            prefs = bridge.orchestrator.get_block_preferences()
            enabled = getattr(prefs, 'auto_sync_ipfs_periodic', False)
            interval_min = getattr(prefs, 'auto_sync_ipfs_interval', 15)
            return enabled, interval_min * 60  # convert to seconds
        except Exception:
            return False, 900

    async def _auto_sync_loop(self):
        """Background task: sync loaded qubes with NFTs to IPFS on user-configured interval."""
        import time

        # Wait 60 seconds before first check (let app stabilize)
        await asyncio.sleep(60)

        while self._running:
            try:
                enabled, interval_secs = self._get_sync_preferences()
                if enabled:
                    await self._run_auto_sync(interval_secs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"auto_sync_error: {e}")

            try:
                await asyncio.sleep(self.AUTO_SYNC_CHECK_INTERVAL)
            except asyncio.CancelledError:
                raise

    async def _run_auto_sync(self, interval_secs: int = 900):
        """Sync all eligible qubes to IPFS."""
        import time

        # Check if master key is available (user must have authenticated)
        password = os.environ.get("QUBES_PASSWORD")
        if not password:
            return

        now = time.time()
        synced_count = 0

        # Iterate all loaded user bridges
        for user_id, bridge in list(self.state.bridges.items()):
            if not bridge or not bridge.orchestrator:
                continue

            orchestrator = bridge.orchestrator
            if not orchestrator.qubes:
                continue

            for qube_id, qube in list(orchestrator.qubes.items()):
                # Skip if synced recently
                last_sync = self._last_sync_times.get(qube_id, 0)
                if now - last_sync < interval_secs:
                    continue

                # Skip qubes without NFT
                qube_dir = self._find_qube_dir(orchestrator, qube_id)
                if not qube_dir:
                    continue

                nft_file = qube_dir / "chain" / "nft_metadata.json"
                if not nft_file.exists():
                    continue

                try:
                    # Reuse the bridge's sync method (handles anchoring, IPFS upload, etc.)
                    result = await bridge.sync_to_chain(
                        qube_id=qube_id,
                        password=password
                    )
                    if result.get("success"):
                        self._last_sync_times[qube_id] = now
                        synced_count += 1
                        logger.info(f"auto_sync_success qube={qube_id} cid={result.get('ipfs_cid', '?')[:16]}...")
                    else:
                        logger.warning(f"auto_sync_failed qube={qube_id} error={result.get('error', '?')}")
                except Exception as e:
                    logger.error(f"auto_sync_exception qube={qube_id} error={e}")

        if synced_count > 0:
            logger.info(f"auto_sync_cycle_complete synced={synced_count}")

    def _find_qube_dir(self, orchestrator, qube_id: str) -> Optional[Path]:
        """Find a qube's data directory."""
        qubes_dir = orchestrator.data_dir / "qubes"
        if not qubes_dir.exists():
            return None
        for entry in qubes_dir.iterdir():
            if entry.is_dir() and qube_id in entry.name:
                return entry
        return None

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    async def send_response(self, response: dict):
        """Thread-safe JSONL write to stdout (one line, flushed)."""
        async with self._output_lock:
            try:
                sys.stdout.write(json.dumps(response, default=str) + "\n")
                sys.stdout.flush()
            except (OSError, IOError) as e:
                logger.error(f"stdout_write_error error={e}")

    async def send_stream(self, request_id: str, stream_type: str, data: dict):
        """Send a streaming event associated with an in-flight request."""
        await self.send_response({"id": request_id, "stream": stream_type, "data": data})

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_args(args: list) -> tuple:
        """Parse a CLI-style args array into (positional, flags) tuple.

        Handles: ["user_id", "--qube-id", "ABCD", "--name", "test", "extra"]
        Returns: (["user_id", "extra"], {"qube_id": "ABCD", "name": "test"})
        """
        positional = []
        flags = {}
        i = 0
        while i < len(args):
            a = str(args[i])
            if a.startswith("--"):
                flag = a[2:].replace("-", "_")
                if i + 1 < len(args) and not str(args[i + 1]).startswith("--"):
                    flags[flag] = args[i + 1]
                    i += 2
                else:
                    flags[flag] = True
                    i += 1
            else:
                positional.append(args[i])
                i += 1
        return positional, flags

    async def handle_request(self, request: dict):
        """Dispatch a single request and send the response."""
        request_id = request.get("id", "unknown")
        command = request.get("command", "")
        params = request.get("params", {})
        secrets = request.get("secrets", {})

        # Convert Rust-style "args" array to named "params" dict
        raw_args = request.get("args", [])
        if raw_args and not params:
            positional, flags = self._parse_args(raw_args)
            params = dict(flags)  # --flag value → params["flag"] = value

            # Map positional args to named params using POSITIONAL_ARG_NAMES
            arg_names = POSITIONAL_ARG_NAMES.get(command)
            if arg_names is not None:
                for i, name in enumerate(arg_names):
                    if i < len(positional):
                        params[name] = positional[i]
            else:
                # Unknown command — set user_id as first positional
                if positional:
                    params["user_id"] = positional[0]

            # Store full positional list for auto-dispatch introspection
            params["_positional"] = positional

        # Set context vars for tool event routing in concurrent tasks
        _current_request_id.set(request_id)
        _current_server.set(self)

        try:
            result = await self.dispatch(command, params, secrets, request_id)
            await self.send_response({"id": request_id, "result": result})
        except Exception as e:
            logger.error(f"command_failed command={command} error={e}", exc_info=True)
            await self.send_response({"id": request_id, "error": str(e)})

        # Post-command cache invalidation
        if command in CACHE_INVALIDATING:
            user_id = params.get("user_id", "")
            qube_id = params.get("qube_id", "")
            if user_id and qube_id:
                self.state.invalidate_qube(user_id, qube_id)

    async def dispatch(self, command: str, params: dict, secrets: dict, request_id: str) -> Any:
        """Route a command to its handler."""

        # 1. Pre-bridge commands (no GUIBridge needed)
        if command in PRE_BRIDGE_COMMANDS:
            handler = getattr(self, f"_pre_{command.replace('-', '_')}", None)
            if handler:
                return await handler(params, secrets)
            raise ValueError(f"Unimplemented pre-bridge command: {command}")

        # 2. Check for explicit handler (special commands)
        handler = getattr(self, f"_handle_{command.replace('-', '_')}", None)
        if handler:
            user_id = params.get("user_id", "default_user")
            password = secrets.get("password")
            bridge = await self.state.get_bridge(user_id, password)
            return await handler(bridge, params, secrets, request_id)

        # 3. Auto-dispatch to GUIBridge method via introspection
        user_id = params.get("user_id", "default_user")
        password = secrets.get("password")
        bridge = await self.state.get_bridge(user_id, password)
        return await self._dispatch_bridge_method(bridge, command, params, secrets)

    # Command-to-method aliases for cases where the lib.rs command name
    # doesn't match the GUIBridge method name (e.g., abbreviated command
    # names vs full method names). This avoids renaming lib.rs commands
    # or duplicating methods.
    COMMAND_ALIASES = {
        "get_wallet_info": "get_qube_wallet_info",
        "propose_wallet_tx": "propose_wallet_transaction",
        "approve_wallet_tx": "approve_wallet_transaction",
        "reject_wallet_tx": "reject_wallet_transaction",
        "owner_withdraw": "owner_withdraw_from_wallet",
    }

    async def _dispatch_bridge_method(self, bridge, command: str, params: dict, secrets: dict) -> Any:
        """Call a GUIBridge method by name, using introspection for parameter mapping."""
        method_name = command.replace("-", "_")
        method_name = self.COMMAND_ALIASES.get(method_name, method_name)
        method = getattr(bridge, method_name, None)
        if method is None:
            raise ValueError(f"Unknown command: {command} (no method '{method_name}' on GUIBridge)")

        # Build kwargs by matching method signature to params + secrets
        sig = inspect.signature(method)
        kwargs = {}
        positional = params.pop("_positional", [])
        pos_idx = 0  # Track which positional arg we're consuming

        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            # 1. Secrets take priority (password, api_key, wallet_wif, etc.)
            if pname in secrets and secrets[pname] is not None:
                kwargs[pname] = secrets[pname]
            # 2. Named params from --flag args
            elif pname in params:
                kwargs[pname] = params[pname]
            # 3. Next positional arg
            elif pos_idx < len(positional):
                kwargs[pname] = positional[pos_idx]
                pos_idx += 1
            elif param.default is not inspect.Parameter.empty:
                pass  # Let the method use its default
            # else: omit — method will raise if truly required

        # Coerce string args to match type annotations (JSON sends everything as strings)
        for pname, param in sig.parameters.items():
            if pname not in kwargs or not isinstance(kwargs[pname], str):
                continue
            ann = param.annotation
            if ann is inspect.Parameter.empty:
                continue
            # Unwrap Optional[X] → X
            origin = getattr(ann, "__origin__", None)
            if origin is Union:
                type_args = [a for a in ann.__args__ if a is not type(None)]
                ann = type_args[0] if len(type_args) == 1 else ann
            try:
                if ann is int:
                    kwargs[pname] = int(kwargs[pname])
                elif ann is float:
                    kwargs[pname] = float(kwargs[pname])
                elif ann is bool:
                    kwargs[pname] = kwargs[pname].lower() in ("true", "1", "yes")
            except (ValueError, TypeError):
                pass  # Let the method handle the bad value

        if asyncio.iscoroutinefunction(method):
            return await method(**kwargs)
        return method(**kwargs)

    # ====================================================================
    # Pre-bridge command handlers (no GUIBridge needed)
    # ====================================================================

    async def _pre_check_first_run(self, params, secrets):
        from utils.paths import get_users_dir, get_app_data_dir, detect_legacy_data_dirs
        users_dir = get_users_dir()
        result = {"data_dir": str(get_app_data_dir())}
        if not users_dir.exists():
            result["is_first_run"] = True
            result["users"] = []
        else:
            users = [d.name for d in users_dir.iterdir() if d.is_dir()]
            result["is_first_run"] = len(users) == 0
            result["users"] = users
        # Detect legacy data from older versions
        if result["is_first_run"]:
            legacy = detect_legacy_data_dirs()
            if legacy:
                old_users_dir = legacy[0] / "users"
                old_users = [d.name for d in old_users_dir.iterdir() if d.is_dir()]
                if old_users:
                    result["legacy_data_dir"] = str(legacy[0])
                    result["legacy_users"] = old_users
        return result

    async def _pre_create_user_account(self, params, secrets):
        from utils.input_validation import validate_user_id
        from utils.paths import get_user_data_dir
        from orchestrator.user_orchestrator import UserOrchestrator

        user_id = validate_user_id(params["user_id"])
        password = secrets.get("password")
        if not password:
            return {"success": False, "error": "Password required"}

        user_data_dir = get_user_data_dir(user_id)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        if (user_data_dir / "password_verifier.enc").exists():
            return {"success": False, "error": "User already exists"}

        orchestrator = UserOrchestrator(user_id=user_id)
        orchestrator.set_master_key(password)

        import secrets as secrets_mod
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(orchestrator.master_key)
        nonce = secrets_mod.token_bytes(12)
        ciphertext = aesgcm.encrypt(nonce, b"QUBES_PASSWORD_VERIFIED", None)
        with open(user_data_dir / "password_verifier.enc", "w") as f:
            json.dump({
                "nonce": nonce.hex(), "ciphertext": ciphertext.hex(),
                "algorithm": "AES-256-GCM", "version": "1.0",
            }, f, indent=2)

        # Cache master key for subsequent requests
        self.state.master_keys[user_id] = orchestrator.master_key
        return {"success": True, "user_id": user_id, "data_dir": str(user_data_dir.absolute())}

    async def _pre_delete_user_account(self, params, secrets):
        import shutil
        from utils.input_validation import validate_user_id
        from utils.paths import get_users_dir

        user_id = validate_user_id(params["user_id"])
        users_dir = get_users_dir()
        user_dir = users_dir / user_id
        if not user_dir.exists():
            return {"success": False, "error": "User not found"}
        if not str(user_dir.resolve()).startswith(str(users_dir.resolve())):
            return {"success": False, "error": "Invalid user path"}
        shutil.rmtree(user_dir)
        # Clear any cached state for this user
        self.state.master_keys.pop(user_id, None)
        self.state.bridges.pop(user_id, None)
        return {"success": True}

    async def _pre_migrate_user_data(self, params, secrets):
        import shutil
        from pathlib import Path
        from utils.input_validation import validate_user_id
        from utils.paths import get_user_data_dir

        old_data_dir = Path(params["old_data_dir"])
        user_id = validate_user_id(params["user_id"])
        old_user_dir = old_data_dir / "users" / user_id
        if not old_user_dir.exists():
            return {"success": False, "error": f"Old user directory not found: {old_user_dir}"}
        new_user_dir = get_user_data_dir(user_id)
        for item in old_user_dir.iterdir():
            dest = new_user_dir / item.name
            if not dest.exists():
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        return {"success": True, "data_dir": str(new_user_dir)}

    async def _pre_check_ollama_status(self, params, secrets):
        import shutil
        import subprocess
        ollama_path = shutil.which("ollama")
        if not ollama_path:
            for p in [Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
                      Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]:
                if p.exists():
                    ollama_path = str(p)
                    break
        if not ollama_path:
            return {"installed": False, "running": False, "models": []}
        try:
            kw = {"capture_output": True, "text": True, "timeout": 10}
            if sys.platform == "win32":
                kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(["ollama", "list"], **kw)
            if result.returncode == 0:
                models = [line.split()[0] for line in result.stdout.strip().split("\n")[1:] if line.strip()]
                return {"installed": True, "running": True, "models": models}
        except Exception:
            pass
        return {"installed": True, "running": False, "models": []}

    async def _pre_get_available_models(self, params, secrets):
        from ai.model_registry import ModelRegistry
        return ModelRegistry.get_models_for_frontend()

    async def _pre_get_difficulty_presets(self, params, secrets):
        try:
            from config.global_settings import GlobalSettings
            presets_response = {}
            for difficulty, preset in GlobalSettings.PRESETS.items():
                presets_response[difficulty] = {
                    "name": preset["name"],
                    "description": preset["description"],
                    "min_interactions": preset["min_interactions"]
                }
            return presets_response
        except ImportError:
            return {}

    async def _pre_install_wsl2(self, params, secrets):
        return {"error": "install-wsl2 requires interactive terminal"}

    async def _pre_get_wsl2_tts_managed_status(self, params, secrets):
        try:
            from audio.wsl2_server_manager import get_server_status
            return get_server_status()
        except ImportError:
            return {"status": "unavailable"}

    async def _pre_warm_up(self, params, secrets):
        """Pre-import heavy modules to reduce first-command latency."""
        imported = []
        try:
            import core.qube  # noqa: F401
            imported.append("core.qube")
        except Exception:
            pass
        try:
            import ai.tools.registry  # noqa: F401
            imported.append("ai.tools.registry")
        except Exception:
            pass
        try:
            import orchestrator.user_orchestrator  # noqa: F401
            imported.append("orchestrator.user_orchestrator")
        except Exception:
            pass
        logger.info(f"warm_up_complete modules={imported}")
        return {"success": True, "modules": imported}

    # ====================================================================
    # Special command handlers (need bridge but non-standard dispatch)
    # Signature: async def _handle_<command_underscored>(self, bridge, params, secrets, request_id)
    # ====================================================================

    # --- Sessions ---

    async def _handle_anchor_session(self, bridge, params, secrets, request_id):
        qube_id = params["qube_id"]
        qube = await bridge.orchestrator.load_qube(qube_id)
        if not qube:
            return {"success": False, "error": f"Qube {qube_id} not found"}
        result = await qube.anchor_session(create_summary=True)
        blocks_anchored = result if isinstance(result, int) else 0
        return {"success": True, "blocks_anchored": blocks_anchored}

    async def _handle_check_sessions(self, bridge, params, secrets, request_id):
        from utils.paths import get_user_qubes_dir
        user_id = params["user_id"]
        qube_id = params["qube_id"]
        qubes_dir = get_user_qubes_dir(user_id)
        # Find qube directory
        qube_dir = None
        if qubes_dir.exists():
            for d in qubes_dir.iterdir():
                if d.is_dir() and d.name.endswith(f"_{qube_id}"):
                    qube_dir = d
                    break
        if not qube_dir:
            return {"has_session": False, "block_count": 0}
        session_dir = qube_dir / "blocks" / "session"
        if not session_dir.exists():
            return {"has_session": False, "block_count": 0}
        blocks = [f for f in session_dir.iterdir() if f.suffix == ".json"]
        return {"has_session": len(blocks) > 0, "block_count": len(blocks)}

    async def _handle_discard_session(self, bridge, params, secrets, request_id):
        qube_id = params["qube_id"]
        password = secrets.get("password")
        qube = await bridge.orchestrator.load_qube(qube_id)
        if not qube:
            return {"success": False, "error": f"Qube {qube_id} not found"}
        # Delete session block files
        session_dir = qube.data_dir / "blocks" / "session"
        deleted = 0
        if session_dir.exists():
            for f in session_dir.iterdir():
                if f.suffix == ".json":
                    f.unlink()
                    deleted += 1
        # Only touch chain state if there were actual session blocks or an active session
        # end_session() → _save() is expensive (lock + decrypt + encrypt + write)
        # Run in executor so it doesn't block the asyncio event loop
        has_session = deleted > 0 or (qube.current_session is not None)
        if has_session:
            try:
                if hasattr(qube, "chain_state") and qube.chain_state:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, qube.chain_state.end_session)
            except Exception as e:
                logger.warning(f"discard_end_session_failed qube_id={qube_id} error={e}")
        qube.current_session = None
        return {"success": True, "blocks_deleted": deleted}

    async def _handle_delete_session_block(self, bridge, params, secrets, request_id):
        qube_id = params["qube_id"]
        block_number = params.get("block_number")
        timestamp = params.get("timestamp")
        qube = await bridge.orchestrator.load_qube(qube_id)
        if not qube or not qube.current_session:
            return {"success": False, "error": "No active session"}
        if timestamp:
            result = qube.current_session.delete_block(timestamp=float(timestamp))
        else:
            result = qube.current_session.delete_block(int(block_number))
        return {"success": bool(result)}

    async def _handle_discard_last_block(self, bridge, params, secrets, request_id):
        qube_id = params["qube_id"]
        qube = await bridge.orchestrator.load_qube(qube_id)
        if not qube or not qube.current_session:
            return {"success": False, "error": "No active session"}
        blocks = qube.current_session.blocks
        if not blocks:
            return {"success": False, "error": "No blocks to discard"}
        last = blocks[-1]
        result = qube.current_session.delete_block(timestamp=last.timestamp)
        return {"success": bool(result)}

    # --- API Keys ---

    async def _handle_get_configured_api_keys(self, bridge, params, secrets, request_id):
        providers = bridge.orchestrator.list_configured_providers()
        return {"success": True, "providers": providers}

    async def _handle_save_api_key(self, bridge, params, secrets, request_id):
        provider = params["provider"]
        api_key = secrets.get("api_key", "")
        bridge.orchestrator.update_api_key(provider, api_key)
        return {"success": True, "provider": provider}

    async def _handle_validate_api_key(self, bridge, params, secrets, request_id):
        provider = params["provider"]
        api_key = secrets.get("api_key", "")
        if api_key == "__SAVED__":
            api_key = bridge.orchestrator.get_api_key(provider)
        result = await bridge.orchestrator.validate_api_key(provider, api_key)
        return result if isinstance(result, dict) else {"valid": bool(result)}

    async def _handle_delete_api_key(self, bridge, params, secrets, request_id):
        provider = params["provider"]
        bridge.orchestrator.delete_api_key(provider)
        return {"success": True, "provider": provider}

    async def _handle_update_qube_config(self, bridge, params, secrets, request_id):
        positional = params.get("_positional", [])
        # Positional: user_id, qube_id, ai_model, voice_model, favorite_color, tts_enabled, evaluation_model
        qube_id = params.get("qube_id", positional[1] if len(positional) > 1 else "")
        ai_model = params.get("ai_model", positional[2] if len(positional) > 2 else "") or None
        voice_model = params.get("voice_model", positional[3] if len(positional) > 3 else "") or None
        favorite_color = params.get("favorite_color", positional[4] if len(positional) > 4 else "") or None
        tts_str = params.get("tts_enabled", positional[5] if len(positional) > 5 else "")
        tts_enabled = None
        if tts_str and str(tts_str).strip():
            tts_enabled = str(tts_str).lower() == "true"
        evaluation_model = params.get("evaluation_model", positional[6] if len(positional) > 6 else "") or None
        return await bridge.update_qube_config(qube_id, ai_model, voice_model, favorite_color, tts_enabled, evaluation_model)

    async def _handle_reload_ai_keys(self, bridge, params, secrets, request_id):
        qube_id = params["qube_id"]
        bridge.orchestrator.reload_ai_keys(qube_id)
        return {"success": True}

    # --- Preferences (orchestrator/UserPreferencesManager) ---

    async def _handle_get_block_preferences(self, bridge, params, secrets, request_id):
        from dataclasses import asdict
        return asdict(bridge.orchestrator.get_block_preferences())

    async def _handle_update_block_preferences(self, bridge, params, secrets, request_id):
        from dataclasses import asdict
        # params contains the preference fields directly (as strings from --flag parsing)
        prefs = {}
        bool_keys = {"individual_auto_anchor", "group_auto_anchor", "auto_sync_ipfs_on_anchor", "auto_sync_ipfs_periodic"}
        int_keys = {"individual_anchor_threshold", "group_anchor_threshold", "auto_sync_ipfs_interval"}
        for k, v in params.items():
            if k in ("user_id", "_positional"):
                continue
            if k in bool_keys and isinstance(v, str):
                v = v.lower() in ("true", "1", "yes")
            elif k in int_keys and isinstance(v, str):
                v = int(v)
            prefs[k] = v
        result = bridge.orchestrator.update_block_preferences(**prefs)
        return asdict(result)

    async def _handle_get_relationship_difficulty(self, bridge, params, secrets, request_id):
        from config.global_settings import get_global_settings
        gs = get_global_settings()
        difficulty = gs.get_difficulty()
        preset = gs.get_preset(difficulty)
        return {"difficulty": difficulty, "description": preset["description"]}

    async def _handle_set_relationship_difficulty(self, bridge, params, secrets, request_id):
        from config.global_settings import get_global_settings
        gs = get_global_settings()
        difficulty = params["difficulty"]
        gs.set_difficulty(difficulty)
        preset = gs.get_preset(difficulty)
        return {"difficulty": difficulty, "description": preset["description"]}

    async def _handle_get_google_tts_path(self, bridge, params, secrets, request_id):
        try:
            mgr = bridge.orchestrator.preferences_manager
            return {"path": mgr.get_google_tts_path() or ""}
        except Exception as e:
            return {"path": "", "error": str(e)}

    async def _handle_set_google_tts_path(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        path = params.get("path", "")
        mgr.update_google_tts_path(path if path not in ("", "none") else None)
        return {"success": True}

    async def _handle_get_decision_config(self, bridge, params, secrets, request_id):
        from dataclasses import asdict
        mgr = bridge.orchestrator.preferences_manager
        return asdict(mgr.get_decision_config())

    async def _handle_update_decision_config(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        config_json = params.get("config_json", "{}")
        if isinstance(config_json, str):
            config = json.loads(config_json)
        else:
            config = {k: v for k, v in params.items() if k not in ("user_id", "_positional", "config_json")}
        mgr.update_decision_config(**config)
        return {"success": True}

    async def _handle_get_memory_config(self, bridge, params, secrets, request_id):
        from dataclasses import asdict
        mgr = bridge.orchestrator.preferences_manager
        return asdict(mgr.get_memory_config())

    async def _handle_update_memory_config(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        config_json = params.get("config_json", "{}")
        if isinstance(config_json, str):
            config = json.loads(config_json)
        else:
            config = {k: v for k, v in params.items() if k not in ("user_id", "_positional", "config_json")}
        mgr.update_memory_config(**config)
        return {"success": True}

    async def _handle_get_onboarding_preferences(self, bridge, params, secrets, request_id):
        from dataclasses import asdict
        mgr = bridge.orchestrator.preferences_manager
        return asdict(mgr.get_onboarding_preferences())

    async def _handle_mark_tutorial_seen(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        mgr.mark_tutorial_seen(params["tab_name"])
        return {"success": True}

    async def _handle_reset_tutorial(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        mgr.reset_tutorial(params["tab_name"])
        return {"success": True}

    async def _handle_reset_all_tutorials(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        mgr.reset_all_tutorials()
        return {"success": True}

    async def _handle_update_show_tutorials(self, bridge, params, secrets, request_id):
        mgr = bridge.orchestrator.preferences_manager
        show = params.get("show", True)
        if isinstance(show, str):
            show = show.lower() == "true"
        mgr.update_show_tutorials(show)
        return {"success": True}

    # --- Multi-qube conversations ---

    async def _handle_start_multi_qube_conversation(self, bridge, params, secrets, request_id):
        qube_ids = params.get("qube_ids", "")
        if isinstance(qube_ids, str):
            qube_ids = [q.strip() for q in qube_ids.split(",") if q.strip()]
        initial_prompt = params.get("initial_prompt", "")
        mode = params.get("conversation_mode", "open_discussion")
        result = await bridge.orchestrator.start_multi_qube_conversation(qube_ids, initial_prompt, mode)
        return result

    async def _handle_get_next_speaker(self, bridge, params, secrets, request_id):
        conversation_id = params["conversation_id"]
        result = await bridge.orchestrator.get_next_speaker(conversation_id)
        return result

    async def _handle_continue_multi_qube_conversation(self, bridge, params, secrets, request_id):
        conversation_id = params["conversation_id"]
        skip_tools = params.get("skip_tools", False)
        if isinstance(skip_tools, str):
            skip_tools = skip_tools.lower() == "true"
        result = await bridge.orchestrator.continue_multi_qube_conversation(
            conversation_id, skip_tools=skip_tools
        )
        return result

    async def _handle_run_background_turns(self, bridge, params, secrets, request_id):
        conversation_id = params["conversation_id"]
        exclude_ids = params.get("exclude_ids", [])
        if isinstance(exclude_ids, str):
            exclude_ids = json.loads(exclude_ids) if exclude_ids else []
        conversation = bridge.orchestrator.conversations.get(conversation_id)
        if not conversation:
            return {"success": False, "error": "Conversation not found"}
        result = await conversation.run_background_turns(exclude_qube_ids=exclude_ids)
        return result

    async def _handle_inject_multi_qube_user_message(self, bridge, params, secrets, request_id):
        conversation_id = params["conversation_id"]
        message = params.get("message", "")
        result = await bridge.orchestrator.inject_user_message_to_conversation(conversation_id, message)
        return result

    async def _handle_lock_in_multi_qube_response(self, bridge, params, secrets, request_id):
        conversation_id = params["conversation_id"]
        timestamp = params.get("timestamp")
        conversation = bridge.orchestrator.conversations.get(conversation_id)
        if not conversation:
            return {"success": False, "error": "Conversation not found"}
        result = await conversation.lock_in_response(float(timestamp) if timestamp else None)
        return result

    async def _handle_end_multi_qube_conversation(self, bridge, params, secrets, request_id):
        conversation_id = params["conversation_id"]
        anchor = params.get("anchor", False)
        if isinstance(anchor, str):
            anchor = anchor.lower() == "true"
        result = await bridge.orchestrator.end_multi_qube_conversation(conversation_id, anchor=anchor)
        return result

    # --- Event watchers ---

    async def _handle_watch_events(self, bridge, params, secrets, request_id):
        """Subscribe to chain state events for a qube, stream them as JSONL."""
        from core.events import ChainStateEvent
        qube_id = params["qube_id"]
        user_id = params.get("user_id", "default_user")
        key = f"{user_id}:{qube_id}"

        # Already watching?
        if key in self.state.event_subs:
            return {"success": True, "message": "Already watching"}

        qube = await bridge.orchestrator.load_qube(qube_id)
        if not qube:
            return {"success": False, "error": f"Qube {qube_id} not found"}

        server = self  # capture for closure

        def on_event(event: ChainStateEvent):
            event_data = {
                "type": "chain_state_event",
                "qube_id": qube_id,
                "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
                "payload": event.payload,
                "timestamp": event.timestamp,
                "source": event.source,
            }
            if server._loop and server._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    server.send_stream(request_id, "chain_state_event", event_data),
                    server._loop,
                )

        qube.events.subscribe(on_event)

        def unsubscribe():
            qube.events.unsubscribe(on_event)

        self.state.event_subs[key] = unsubscribe
        logger.info(f"event_watcher_started qube_id={qube_id}")
        return {"success": True, "message": f"Watching events for {qube_id}"}

    async def _handle_stop_watch_events(self, bridge, params, secrets, request_id):
        """Unsubscribe from chain state events for a qube."""
        qube_id = params["qube_id"]
        user_id = params.get("user_id", "default_user")
        key = f"{user_id}:{qube_id}"
        unsub = self.state.event_subs.pop(key, None)
        if callable(unsub):
            unsub()
            logger.info(f"event_watcher_stopped qube_id={qube_id}")
            return {"success": True}
        return {"success": False, "error": "Not watching this qube"}

    # --- NFT / Minting (save-api-keys uses SecureSettingsManager) ---

    async def _handle_save_api_keys(self, bridge, params, secrets, request_id):
        """Batch save multiple API keys (from setup wizard)."""
        password = secrets.get("password")
        if password and params["user_id"] not in self.state.master_keys:
            bridge.orchestrator.set_master_key(password)
            self.state.master_keys[params["user_id"]] = bridge.orchestrator.master_key
        providers = {k: v for k, v in params.items() if k not in ("user_id", "_positional") and v}
        for provider, key in providers.items():
            bridge.orchestrator.update_api_key(provider, key)
        return {"success": True, "saved": list(providers.keys())}

    async def _handle_update_qube_nft(self, bridge, params, secrets, request_id):
        qube_id = params["qube_id"]
        qube = await bridge.orchestrator.load_qube(qube_id)
        if not qube:
            return {"success": False, "error": "Qube not found"}
        if "category_id" in params:
            qube.genesis_block.nft_category_id = params["category_id"]
        if "mint_txid" in params:
            qube.genesis_block.mint_txid = params["mint_txid"]
        bridge.orchestrator._save_qube_data(qube)
        return {"success": True}

    # --- Misc special handlers ---

    async def _handle_refresh_auth_token(self, bridge, params, secrets, request_id):
        token = secrets.get("token", "") or params.get("token", "")
        return await bridge.refresh_auth_token(token)

    async def _handle_get_auth_status(self, bridge, params, secrets, request_id):
        qube_id = params.get("qube_id", "")
        return await bridge.get_auth_status(qube_id)

    async def _handle_get_debug_prompt(self, bridge, params, secrets, request_id):
        qube_id = params.get("qube_id", "all")
        try:
            from ai.reasoner import get_debug_prompt
            return get_debug_prompt(qube_id)
        except ImportError:
            return {"error": "Debug prompt not available"}

    # --- Background download/install (long-running, but we return immediately) ---

    async def _handle_download_qwen_model(self, bridge, params, secrets, request_id):
        # This was a background subprocess. In sidecar mode, we can start it as a task.
        return {"error": "download-qwen-model should be handled via bridge method download_qwen3_model"}

    # --- Commands with method name mismatch or no GUIBridge method ---

    async def _handle_generate_introduction(self, bridge, params, secrets, request_id):
        """generate-introduction → generate_introduction_message (name mismatch)."""
        qube_id = params["qube_id"]
        to_commitment = params.get("to_commitment", "")
        to_name = params.get("to_name", "")
        to_description = params.get("to_description", "")
        password = secrets.get("password")
        return await bridge.generate_introduction_message(
            qube_id, to_commitment, to_name, to_description, password
        )

    async def _handle_get_trust_personality(self, bridge, params, secrets, request_id):
        """No GUIBridge method — reads chain_state directly."""
        qube_id = params["qube_id"]
        qube_dir = bridge._find_qube_dir(qube_id)
        if not qube_dir:
            return {"success": False, "error": f"Qube {qube_id} not found"}
        trust_profile = "balanced"
        encryption_key = bridge._get_qube_encryption_key(qube_dir)
        if encryption_key:
            from core.chain_state import ChainState
            cs = ChainState(qube_dir / "chain", encryption_key, qube_id)
            trust_profile = cs.get_setting("trust_profile", "balanced")
        else:
            chain_state_path = qube_dir / "chain" / "chain_state.json"
            if chain_state_path.exists():
                with open(chain_state_path, "r") as f:
                    chain_state = json.load(f)
                    trust_profile = chain_state.get("trust_profile", "balanced")
        return {"trust_profile": trust_profile}

    async def _handle_update_trust_personality(self, bridge, params, secrets, request_id):
        """No GUIBridge method — writes chain_state directly."""
        qube_id = params["qube_id"]
        trust_profile = params.get("trust_profile", "balanced")
        valid_profiles = ["cautious", "balanced", "social", "analytical"]
        if trust_profile not in valid_profiles:
            return {"success": False, "error": f"Invalid trust profile. Must be one of: {', '.join(valid_profiles)}"}
        qube_dir = bridge._find_qube_dir(qube_id)
        if not qube_dir:
            return {"success": False, "error": f"Qube {qube_id} not found"}
        encryption_key = bridge._get_qube_encryption_key(qube_dir)
        if not encryption_key:
            return {"success": False, "error": "Cannot access chain_state - no encryption key"}
        from core.chain_state import ChainState
        cs = ChainState(data_dir=qube_dir / "chain", encryption_key=encryption_key)
        cs.update_settings({"trust_profile": trust_profile})
        return {"success": True, "trust_profile": trust_profile}

    async def _handle_get_visualizer_settings(self, bridge, params, secrets, request_id):
        """No GUIBridge method — reads chain_state/file directly."""
        from utils.paths import get_user_qubes_dir
        qube_id = params["qube_id"]
        user_id = params.get("user_id", "default_user")
        data_dir = get_user_qubes_dir(user_id)
        qube_dir = None
        for dir_path in data_dir.iterdir():
            if dir_path.is_dir() and qube_id in dir_path.name:
                qube_dir = dir_path
                break
        if not qube_dir:
            return {"success": False, "error": f"Qube {qube_id} not found"}
        default_settings = {
            "enabled": False, "waveform_style": 1, "color_theme": "qube-color",
            "gradient_style": "gradient-dark", "sensitivity": 50,
            "animation_smoothness": "medium", "audio_offset_ms": 0,
            "frequency_range": 20, "output_monitor": 0,
        }
        password = secrets.get("password")
        settings = None
        if password or bridge.orchestrator.master_key:
            try:
                encryption_key = bridge._get_qube_encryption_key(qube_dir)
                if encryption_key:
                    from core.chain_state import ChainState
                    cs = ChainState(qube_dir / "chain", encryption_key, qube_id)
                    settings = cs.get_visualizer_settings()
                    if settings:
                        settings["enabled"] = cs.is_visualizer_enabled()
            except Exception:
                pass
        if not settings:
            settings_file = qube_dir / "visualizer_settings.json"
            if settings_file.exists():
                with open(settings_file, "r") as f:
                    settings = json.load(f)
            else:
                settings = default_settings
        return settings

    async def _handle_save_visualizer_settings(self, bridge, params, secrets, request_id):
        """No GUIBridge method — writes chain_state/file directly."""
        from utils.paths import get_user_qubes_dir
        qube_id = params["qube_id"]
        user_id = params.get("user_id", "default_user")
        settings_json = params.get("settings", "{}")
        if isinstance(settings_json, str):
            settings_data = json.loads(settings_json)
        else:
            settings_data = settings_json
        data_dir = get_user_qubes_dir(user_id)
        qube_dir = None
        for dir_path in data_dir.iterdir():
            if dir_path.is_dir() and qube_id in dir_path.name:
                qube_dir = dir_path
                break
        if not qube_dir:
            return {"success": False, "error": f"Qube {qube_id} not found"}
        saved_to_chain_state = False
        enabled = settings_data.pop("enabled", False)
        password = secrets.get("password")
        if password or bridge.orchestrator.master_key:
            try:
                encryption_key = bridge._get_qube_encryption_key(qube_dir)
                if encryption_key:
                    from core.chain_state import ChainState
                    cs = ChainState(qube_dir / "chain", encryption_key, qube_id)
                    cs.set_visualizer_enabled(enabled)
                    cs.set_visualizer_settings(settings_data)
                    saved_to_chain_state = True
            except Exception:
                pass
        settings_file = qube_dir / "visualizer_settings.json"
        settings_data["enabled"] = enabled
        with open(settings_file, "w") as f:
            json.dump(settings_data, f, indent=2)
        return {"success": True, "message": "Visualizer settings saved", "chain_state": saved_to_chain_state}

    async def _handle_start_wsl2_tts_managed(self, bridge, params, secrets, request_id):
        try:
            from audio.wsl2_server_manager import start_server
            force = params.get("force", "false")
            return start_server(force=(force == "true" if isinstance(force, str) else bool(force)))
        except ImportError:
            return {"error": "WSL2 TTS not available"}

    async def _handle_stop_wsl2_tts_managed(self, bridge, params, secrets, request_id):
        try:
            from audio.wsl2_server_manager import stop_server
            return stop_server()
        except ImportError:
            return {"error": "WSL2 TTS not available"}

    async def _handle_check_local_tts_models(self, bridge, params, secrets, request_id):
        """Check which local TTS/embedding models are installed."""
        import os
        from pathlib import Path
        hf_home = os.environ.get("HF_HOME", "")
        models_dir = os.environ.get("QUBES_MODELS_DIR", "")
        kokoro_installed = False
        st_installed = False
        whisper_installed = False
        if hf_home:
            hf_path = Path(hf_home)
            kokoro_path = hf_path / "hub" / "models--hexgrad--Kokoro-82M"
            st_path = hf_path / "hub" / "models--sentence-transformers--all-MiniLM-L6-v2"
            kokoro_installed = kokoro_path.exists()
            st_installed = st_path.exists()
        if models_dir:
            whisper_path = Path(models_dir) / "whisper"
            whisper_installed = any(whisper_path.glob("*.bin")) if whisper_path.exists() else False
        return {
            "kokoro_installed": kokoro_installed,
            "sentence_transformers_installed": st_installed,
            "whisper_installed": whisper_installed,
            "hf_home": hf_home,
            "models_dir": str(models_dir or hf_home),
        }

    async def _handle_update_local_tts_models(self, bridge, params, secrets, request_id):
        """Re-download/update local TTS and embedding models from HuggingFace."""
        import os
        from pathlib import Path
        updated = []
        errors = []
        try:
            from huggingface_hub import snapshot_download
            hf_home = os.environ.get("HF_HOME", None)
            dl_kwargs = {}
            if hf_home:
                dl_kwargs["cache_dir"] = str(Path(hf_home) / "hub")
            try:
                snapshot_download("hexgrad/Kokoro-82M", local_files_only=False, **dl_kwargs)
                updated.append("kokoro-82m")
            except Exception as e:
                errors.append(f"Kokoro: {str(e)}")
            try:
                snapshot_download("sentence-transformers/all-MiniLM-L6-v2", local_files_only=False, **dl_kwargs)
                updated.append("sentence-transformers")
            except Exception as e:
                errors.append(f"sentence-transformers: {str(e)}")
        except ImportError as e:
            errors.append(f"huggingface_hub not available: {str(e)}")
        return {
            "success": len(errors) == 0,
            "updated": updated,
            "errors": errors,
        }
