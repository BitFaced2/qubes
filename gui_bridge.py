#!/usr/bin/env python3
"""
Qubes GUI Bridge - CLI interface for Tauri GUI to communicate with Python backend
"""
import sys
import os
import io

# ============================================================================
# PYINSTALLER --noconsole FIX (MUST BE FIRST!)
# ============================================================================
# In PyInstaller --noconsole mode, stdout/stderr are None which causes crashes
# when any library (like colorama) tries to write to them.
# This MUST run before any other imports that might pull in colorama.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

# Disable colorama if it gets imported (prevents ANSI code errors on Windows)
os.environ['NO_COLOR'] = '1'
os.environ['ANSI_COLORS_DISABLED'] = '1'

# Custom exception hook to suppress I/O errors at exit (colorama, broken pipes, etc.)
_original_excepthook = sys.excepthook
def _quiet_excepthook(exc_type, exc_value, exc_tb):
    """Suppress OSError/IOError at exit (common in --noconsole mode)"""
    if exc_type in (OSError, IOError, BrokenPipeError):
        return  # Silently ignore I/O errors at exit
    if exc_type == SystemExit:
        return  # Normal exit
    # For other exceptions, try to log to file instead of crashing
    try:
        import logging
        logging.exception("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
    except Exception:
        pass  # If logging fails, just exit silently
sys.excepthook = _quiet_excepthook

# ============================================================================
# GLOBAL UTF-8 FIX FOR WINDOWS
# ============================================================================
# Force all Python I/O to use UTF-8 encoding (fixes emoji/Unicode issues on Windows)
try:
    if hasattr(sys.stdout, 'buffer') and sys.stdout.buffer is not None:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    if hasattr(sys.stderr, 'buffer') and sys.stderr.buffer is not None:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
except Exception:
    pass  # If reconfiguration fails, just use defaults

import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables from .env file (searches from script dir, not CWD)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# ============================================================================
# CUSTOM MODELS PATH (qubes-config.json or QUBES_MODELS_DIR env var)
# ============================================================================
# Allow users to store models on any drive/path they want.
# Priority: QUBES_MODELS_DIR env var > qubes-config.json models_path > auto-detect
_custom_models_dir = None

# 1. Check env var (set by .env file or OS)
if os.environ.get('QUBES_MODELS_DIR'):
    _custom_models_dir = Path(os.environ['QUBES_MODELS_DIR'])

# 2. Check qubes-config.json next to the exe
if _custom_models_dir is None:
    try:
        import json as _json
        _exe_dir_cfg = Path(sys.executable).parent
        for _cfg_path in [
            _exe_dir_cfg.parent / "qubes-config.json",
            _exe_dir_cfg / "qubes-config.json",
            Path(__file__).parent.parent / "qubes-config.json",
        ]:
            if _cfg_path.exists():
                _cfg = _json.loads(_cfg_path.read_text(encoding="utf-8"))
                if _cfg.get("models_path"):
                    _custom_models_dir = Path(_cfg["models_path"])
                break
    except Exception:
        pass

# Auto-detect bundled HuggingFace models (heavy bundle)
# Sets HF_HOME so kokoro, sentence-transformers find pre-downloaded models.
# Priority: custom QUBES_MODELS_DIR config > bundled auto-detect.
# Always creates HF_HOME dir so models auto-download on first use.
if not os.environ.get('HF_HOME'):
    if _custom_models_dir is not None:
        _hf_models = _custom_models_dir / "huggingface"
        _hf_models.mkdir(parents=True, exist_ok=True)
        os.environ['HF_HOME'] = str(_hf_models)
        os.environ.setdefault('QUBES_MODELS_DIR', str(_custom_models_dir))
    elif getattr(sys, 'frozen', False):
        _exe_dir = Path(sys.executable).parent
        # --onedir layout: exe is at Qubes/qubes-backend/qubes-backend
        # models are at Qubes/models/huggingface (one level up)
        _hf_models = _exe_dir.parent / "models" / "huggingface"
        if not _hf_models.exists():
            # Flat layout or fresh install: use models next to exe
            _hf_models = _exe_dir / "models" / "huggingface"
        # Always set HF_HOME — create dir if needed, models will auto-download
        _hf_models.mkdir(parents=True, exist_ok=True)
        os.environ['HF_HOME'] = str(_hf_models)
        os.environ.setdefault('QUBES_MODELS_DIR', str(_hf_models.parent))

# CRITICAL: Disable all logging to stdout/stderr before importing anything
# Set environment variable to disable structlog output
os.environ['QUBES_LOG_LEVEL'] = 'ERROR'

# Create logs directory - use script parent in dev, writable fallback for AppImage
log_dir = Path(__file__).parent / "logs"
try:
    log_dir.mkdir(exist_ok=True)
except OSError:
    # Read-only filesystem (e.g., AppImage mount) - use platform writable location
    if sys.platform == 'win32':
        _base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'Qubes'
    else:
        _base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'Qubes'
    log_dir = _base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

# Configure Python logging to write ONLY to file (not to stdout/stderr)
# This prevents logs from interfering with JSON output
# Use UTF-8 encoding to support emoji and Unicode characters
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)-8s] %(message)s',
    handlers=[
        logging.FileHandler(
            log_dir / "gui_bridge_debug.log",
            mode='a',
            encoding='utf-8',  # Support emoji and Unicode
            errors='replace'    # Replace unencodable characters instead of crashing
        )
    ]
)

# Disable propagation to prevent any stderr output
logging.root.handlers[0].setLevel(logging.DEBUG)

# Configure structlog to write to file (not stdout/stderr)
# Import structlog and configure_logging from utils.logging
import structlog
from utils.logging import configure_logging

# Configure structured logging to file only
configure_logging(
    log_level="DEBUG",
    log_file=log_dir / "gui_bridge_debug.log",
    json_output=False,  # Human-readable format
    console_output=False  # NO console output to keep stdout clean for JSON
)

from orchestrator.user_orchestrator import UserOrchestrator
from utils.input_validation import validate_user_id, validate_qube_id, validate_qube_name, validate_message, sanitize_filename
from utils.paths import get_app_data_dir, get_users_dir, get_user_data_dir, get_user_qubes_dir

logger = structlog.get_logger(__name__)


# ============================================================================
# SECURE SECRETS HANDLING VIA STDIN
# ============================================================================
# Secrets (passwords, API keys, wallet keys) are passed via stdin JSON instead
# of command-line arguments to prevent exposure in process listings.
#
# Format: Single line of JSON on stdin, e.g.: {"password": "secret", "api_key": "sk-xxx"}
# Backwards compatible: Falls back to argv if stdin is empty (during migration)
# ============================================================================

_stdin_secrets: Optional[Dict[str, str]] = None  # Cached stdin secrets (read once)
_stdin_read_attempted: bool = False  # Track if we've tried reading stdin


def _read_stdin_secrets() -> Dict[str, str]:
    """
    Read secrets JSON from stdin (single line).
    Only reads once, caches result for subsequent calls.
    Cross-platform: works on Windows and Unix.
    """
    global _stdin_secrets, _stdin_read_attempted

    if _stdin_read_attempted:
        return _stdin_secrets or {}

    _stdin_read_attempted = True
    _stdin_secrets = {}

    try:
        # Check if stdin is a TTY (interactive terminal)
        # If it's a TTY, we're running interactively - no stdin secrets
        # If it's NOT a TTY, we're being called from Rust with piped input
        if sys.stdin.isatty():
            logger.debug("stdin_is_tty_skipping_secrets_read")
            return {}

        # stdin is a pipe - Rust should have written secrets before we get here
        # Use a simple read with the understanding that data should already be buffered
        import threading

        result = {"line": None, "error": None}

        def read_line():
            try:
                result["line"] = sys.stdin.readline()
            except Exception as e:
                result["error"] = str(e)

        # Read with timeout to prevent hanging if no data
        reader_thread = threading.Thread(target=read_line, daemon=True)
        reader_thread.start()
        reader_thread.join(timeout=0.5)  # 500ms timeout

        if reader_thread.is_alive():
            # Timeout - no data available
            logger.debug("stdin_read_timeout")
            return {}

        if result["error"]:
            logger.debug("stdin_read_error", error=result["error"])
            return {}

        line = (result["line"] or "").strip()
        if line:
            _stdin_secrets = json.loads(line)
            logger.debug("stdin_secrets_loaded", keys=list(_stdin_secrets.keys()))

    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.debug("stdin_secrets_read_failed", error=str(e))
        _stdin_secrets = {}
    except Exception as e:
        # Catch-all for any platform-specific issues
        logger.debug("stdin_secrets_unexpected_error", error=str(e))
        _stdin_secrets = {}

    return _stdin_secrets


def get_secret(name: str, argv_index: Optional[int] = None, required: bool = True) -> Optional[str]:
    """
    Get a secret value securely. Tries stdin first (secure), falls back to argv (legacy).

    This backwards-compatible approach allows gradual migration from argv to stdin:
    - If Rust passes secret via stdin JSON: uses that (secure, not visible in ps)
    - If Rust passes secret via argv: falls back to that (legacy, visible in ps)
    - If neither and required=True: raises ValueError

    Args:
        name: Secret name (e.g., "password", "api_key", "wallet_wif")
        argv_index: Fallback argv index for backwards compatibility (optional)
        required: Whether to raise error if secret not found (default True)

    Returns:
        Secret value or None if not found and not required

    Raises:
        ValueError: If secret not found and required=True
    """
    # Try stdin first (new secure method)
    secrets = _read_stdin_secrets()
    if name in secrets:
        value = secrets[name]
        # Don't return empty strings as valid secrets
        if value and value.strip():
            return value
        # Empty value in stdin - fall through to argv check

    # Fall back to argv (legacy method - for backwards compatibility during migration)
    if argv_index is not None and len(sys.argv) > argv_index:
        value = sys.argv[argv_index]
        # Don't return empty strings as valid secrets
        if value and value.strip():
            return value

    # Secret not found
    if required:
        raise ValueError(f"Missing required secret: {name}")
    return None


class GUIBridge:
    """Bridge between Tauri GUI and Python backend"""

    def __init__(self, user_id: str = "default_user"):
        self.orchestrator = UserOrchestrator(user_id=user_id)
        # Store background task references to prevent garbage collection
        self._background_tasks: set = set()

    def _get_connections_file(self, qube_id: str) -> Path:
        """
        Get path to connections file for a Qube, ensuring directory exists.

        Connections are stored in: data/users/{user}/connections/{qube_id}.json

        Also handles migration from old location (connections_{qube_id}.json in user dir)
        """
        connections_dir = self.orchestrator.data_dir / "connections"
        connections_dir.mkdir(exist_ok=True)
        new_path = connections_dir / f"{qube_id}.json"

        # Migrate from old location if needed
        old_path = self.orchestrator.data_dir / f"connections_{qube_id}.json"
        if old_path.exists() and not new_path.exists():
            import shutil
            shutil.move(str(old_path), str(new_path))
            logger.info(f"Migrated connections file from {old_path} to {new_path}")

        return new_path

    async def _process_documents_to_action_blocks(
        self,
        qube_id: str,
        message: str,
        qube
    ) -> tuple[str, list]:
        """
        Extract PDFs from message, create ACTION blocks, return cleaned message.

        This replaces _process_pdf_in_message() with a new architecture:
        - PDFs are processed into ACTION blocks (not injected into message)
        - Message is cleaned to show human-readable references
        - ACTION blocks are added to session before sending to AI
        - AI receives document content as tool results (not in message body)

        Pattern: <pdf_base64 filename="report.pdf">base64data</pdf_base64>

        Args:
            qube_id: ID of the qube processing the message
            message: Raw message with embedded PDF tags
            qube: Qube instance

        Returns:
            Tuple of (cleaned_message, list_of_action_blocks)
        """
        import re
        from core.document_processor import process_pdf_to_action_block

        # Pattern to find PDF base64 data with optional filename attribute
        # Supports both: <pdf_base64>...</pdf_base64> and <pdf_base64 filename="...">...</pdf_base64>
        pdf_pattern = r'<pdf_base64(?:\s+filename=["\']([^"\']+)["\'])?>([^<]+)</pdf_base64>'

        action_blocks = []

        # Find all PDF matches
        matches = list(re.finditer(pdf_pattern, message, flags=re.DOTALL))

        # Process each PDF sequentially (needed for async)
        replacements = {}  # Store {original_tag: reference_string}

        for match in matches:
            filename = match.group(1)  # May be None if no filename attribute
            pdf_base64 = match.group(2).strip()
            original_tag = match.group(0)

            # Use generic filename if not provided
            if not filename:
                filename = f"document_{len(action_blocks) + 1}.pdf"

            try:
                logger.debug(
                    "processing_pdf_to_action_block",
                    filename=filename,
                    base64_length=len(pdf_base64),
                    qube_id=qube_id
                )

                # Create ACTION block with document content (async)
                action_block, reference_string = await process_pdf_to_action_block(
                    qube=qube,
                    pdf_base64=pdf_base64,
                    filename=filename
                )

                # Add to list (will be added to session later)
                action_blocks.append(action_block)

                logger.info(
                    "action_block_created_for_document",
                    filename=filename,
                    status=action_block.content.get("status") if action_block.content else None,
                    qube_id=qube_id
                )

                # Store replacement
                replacements[original_tag] = reference_string

            except Exception as e:
                logger.error(
                    "failed_to_create_document_action_block",
                    filename=filename,
                    error=str(e),
                    exc_info=True
                )
                # Store error reference if block creation fails
                replacements[original_tag] = f"\n\n[Document processing error: {str(e)}]\n"

        # Apply all replacements to message
        cleaned_message = message
        for original_tag, reference_string in replacements.items():
            cleaned_message = cleaned_message.replace(original_tag, reference_string)

        logger.info(
            "documents_processed_to_action_blocks",
            original_message_length=len(message),
            cleaned_message_length=len(cleaned_message),
            action_blocks_created=len(action_blocks),
            qube_id=qube_id
        )

        return cleaned_message, action_blocks

    async def authenticate(self, user_id: str, password: str) -> Dict[str, Any]:
        """Authenticate user with username and password"""
        try:
            # Create orchestrator for this user
            orchestrator = UserOrchestrator(user_id=user_id)

            # Check if user directory exists
            if not orchestrator.data_dir.exists():
                return {"success": False, "error": "User not found"}

            # Check if salt file exists (means user was set up with a password)
            salt_file = orchestrator.data_dir / "salt.bin"
            if not salt_file.exists():
                return {"success": False, "error": "User not found or not initialized"}

            # Try to set master key with password
            try:
                orchestrator.set_master_key(password)
            except Exception as e:
                logger.error(f"Failed to set master key for {user_id}: {e}")
                return {"success": False, "error": "Invalid password"}

            # Verify password using the password verifier file
            # This is a known plaintext encrypted with the derived key
            verifier_file = orchestrator.data_dir / "password_verifier.enc"

            if verifier_file.exists():
                # Verify password by decrypting the verifier
                try:
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                    import json as json_module

                    with open(verifier_file, 'r') as f:
                        verifier_data = json_module.load(f)

                    aesgcm = AESGCM(orchestrator.master_key)
                    nonce = bytes.fromhex(verifier_data["nonce"])
                    ciphertext = bytes.fromhex(verifier_data["ciphertext"])

                    # Decrypt - will throw InvalidTag if password is wrong
                    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

                    # Verify the magic string
                    if plaintext.decode() != "QUBES_PASSWORD_VERIFIED":
                        return {"success": False, "error": "Invalid password"}

                    logger.info(f"Authentication successful for {user_id} (verifier matched)")
                except Exception as e:
                    logger.error(f"Authentication failed for {user_id}: {e}")
                    return {"success": False, "error": "Invalid password"}
            else:
                # Legacy: No verifier file, fall back to qube-based verification
                qubes_list = await orchestrator.list_qubes()

                if len(qubes_list) > 0:
                    # Try to decrypt the first qube's private key to verify password
                    try:
                        first_qube_id = qubes_list[0]["qube_id"]
                        qube_data, _ = await orchestrator._load_qube_data(first_qube_id)
                        orchestrator._decrypt_private_key(
                            qube_data["encrypted_private_key"],
                            orchestrator.master_key
                        )
                        logger.info(f"Authentication successful for {user_id} (legacy qube verification)")

                        # Migrate: Create verifier file for future logins
                        await self._create_password_verifier(orchestrator)
                    except Exception as e:
                        logger.error(f"Authentication failed for {user_id}: {e}")
                        return {"success": False, "error": "Invalid password"}
                else:
                    # No qubes AND no verifier - this is a broken state
                    # User must have been created before verifier was implemented
                    logger.error(f"No password verifier and no qubes for {user_id}")
                    return {"success": False, "error": "ACCOUNT_CORRUPTED: No password verifier and no qubes found. Account may be incomplete."}

            # Authentication successful
            return {
                "success": True,
                "user_id": user_id,
                "data_dir": str(orchestrator.data_dir)
            }

        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return {"success": False, "error": str(e)}

    async def _create_password_verifier(self, orchestrator: UserOrchestrator) -> None:
        """
        Create password verifier file for future authentication.

        Encrypts a known magic string with the derived master key.
        On login, we decrypt this to verify the password is correct.
        """
        import secrets
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            verifier_file = orchestrator.data_dir / "password_verifier.enc"

            # Encrypt the magic string
            aesgcm = AESGCM(orchestrator.master_key)
            nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM
            plaintext = b"QUBES_PASSWORD_VERIFIED"
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)

            # Save as JSON
            verifier_data = {
                "nonce": nonce.hex(),
                "ciphertext": ciphertext.hex(),
                "algorithm": "AES-256-GCM",
                "version": "1.0"
            }

            with open(verifier_file, 'w') as f:
                json.dump(verifier_data, f, indent=2)

            logger.info(f"Created password verifier for {orchestrator.user_id}")
        except Exception as e:
            logger.error(f"Failed to create password verifier: {e}")
            # Non-fatal - login will still work via qube verification

    async def change_master_password(self, old_password: str, new_password: str) -> Dict[str, Any]:
        """
        Change the master password. Re-encrypts all account data with the new key.

        Steps:
        1. Verify old password via password_verifier.enc
        2. Derive new master key from new password (same salt)
        3. Re-encrypt: password_verifier, api_keys, wallet_security, settings.enc/*
        4. Re-encrypt per-Qube: encrypted_private_key, encryption_key.enc

        Args:
            old_password: Current master password
            new_password: New master password

        Returns:
            Dict with success and count of re-encrypted items
        """
        import secrets as crypto_secrets
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend

        try:
            user_dir = self.orchestrator.data_dir

            # Step 1: Derive old master key and verify
            self.orchestrator.set_master_key(old_password)
            old_key = self.orchestrator.master_key

            verifier_file = user_dir / "password_verifier.enc"
            if verifier_file.exists():
                with open(verifier_file, 'r') as f:
                    vdata = json.load(f)
                aesgcm = AESGCM(old_key)
                plaintext = aesgcm.decrypt(
                    bytes.fromhex(vdata["nonce"]),
                    bytes.fromhex(vdata["ciphertext"]),
                    None
                )
                if plaintext != b"QUBES_PASSWORD_VERIFIED":
                    return {"success": False, "error": "Invalid current password"}
            else:
                return {"success": False, "error": "No password verifier found"}

            # Step 2: Derive new master key (same salt, same iterations)
            salt_file = user_dir / "salt.bin"
            iterations_file = user_dir / "pbkdf2_iterations.txt"
            salt = salt_file.read_bytes()
            iterations = int(iterations_file.read_text().strip()) if iterations_file.exists() else 600000

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=iterations,
                backend=default_backend()
            )
            new_key = kdf.derive(new_password.encode())

            re_encrypted = 0

            # Helper: re-encrypt a JSON {nonce, ciphertext} file
            def reencrypt_json_file(filepath: Path):
                nonlocal re_encrypted
                if not filepath.exists():
                    return
                with open(filepath, 'r') as f:
                    data = json.load(f)
                old_aesgcm = AESGCM(old_key)
                plaintext = old_aesgcm.decrypt(
                    bytes.fromhex(data["nonce"]),
                    bytes.fromhex(data["ciphertext"]),
                    None
                )
                new_nonce = crypto_secrets.token_bytes(12)
                new_aesgcm = AESGCM(new_key)
                new_ciphertext = new_aesgcm.encrypt(new_nonce, plaintext, None)
                data["nonce"] = new_nonce.hex()
                data["ciphertext"] = new_ciphertext.hex()
                with open(filepath, 'w') as f:
                    json.dump(data, f, indent=2)
                re_encrypted += 1

            # Step 3: Re-encrypt account-level files
            reencrypt_json_file(verifier_file)
            reencrypt_json_file(user_dir / "api_keys.enc")
            reencrypt_json_file(user_dir / "wallet_security.enc")

            # settings.enc directory
            settings_dir = user_dir / "settings.enc"
            if settings_dir.is_dir():
                for sf in settings_dir.iterdir():
                    if sf.is_file() and sf.suffix == '.enc':
                        reencrypt_json_file(sf)

            # Step 4: Re-encrypt per-Qube data
            qubes_dir = user_dir / "qubes"
            if qubes_dir.exists():
                for qube_entry in sorted(qubes_dir.iterdir()):
                    if not qube_entry.is_dir():
                        continue

                    # Re-encrypt encryption_key.enc
                    enc_key_file = qube_entry / "chain" / "encryption_key.enc"
                    reencrypt_json_file(enc_key_file)

                    # Re-encrypt private key in qube_metadata.json
                    metadata_file = qube_entry / "chain" / "qube_metadata.json"
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, 'r') as f:
                                metadata = json.load(f)

                            enc_pk_hex = metadata.get("encrypted_private_key")
                            if enc_pk_hex:
                                # Decrypt with old key → key object → re-encrypt with new key
                                private_key_obj = self.orchestrator._decrypt_private_key(enc_pk_hex, old_key)
                                new_enc = self.orchestrator._encrypt_private_key(private_key_obj, new_key)
                                metadata["encrypted_private_key"] = new_enc.hex()

                                with open(metadata_file, 'w') as f:
                                    json.dump(metadata, f, indent=2)
                                re_encrypted += 1
                        except Exception as e:
                            logger.warning(f"Failed to re-encrypt private key in {qube_entry.name}: {e}")

            # Step 5: Update orchestrator to use new key
            self.orchestrator.master_key = new_key

            logger.info(f"Master password changed successfully, {re_encrypted} items re-encrypted")
            return {"success": True, "re_encrypted_count": re_encrypted}

        except Exception as e:
            logger.error(f"Failed to change master password: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def delete_user_account(self, user_id: str) -> Dict[str, Any]:
        """Delete a user account directory (for resetting corrupted accounts)"""
        import shutil
        users_dir = get_users_dir()
        user_dir = users_dir / user_id
        if not user_dir.exists():
            return {"success": False, "error": "User not found"}
        # Path traversal protection
        if not str(user_dir.resolve()).startswith(str(users_dir.resolve())):
            return {"success": False, "error": "Invalid user path"}
        try:
            shutil.rmtree(user_dir)
            logger.info(f"Deleted user account directory: {user_id}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to delete user account {user_id}: {e}")
            return {"success": False, "error": str(e)}

    async def list_qubes(self) -> List[Dict[str, Any]]:
        """List all qubes with their metadata"""
        try:
            qubes = await self.orchestrator.list_qubes()

            # Transform to GUI format
            gui_qubes = []
            for qube in qubes:
                # Convert birth_timestamp to ISO format for created_at
                created_at = datetime.fromtimestamp(qube["birth_timestamp"]).isoformat() if qube.get("birth_timestamp") else datetime.now().isoformat()

                # Determine status: active if qube has been properly initialized (has genesis + NFT)
                # A qube is "active" if it's ready to use (has genesis block and NFT minted)
                has_genesis = qube.get("total_blocks", 0) > 0
                has_nft = qube.get("nft_category_id") is not None
                is_loaded_in_memory = qube.get("loaded", False)

                # Active: has genesis and NFT (ready to use)
                # Busy: loaded in memory and actively being used
                # Inactive: missing genesis or NFT (incomplete setup)
                if is_loaded_in_memory:
                    status = "busy"
                elif has_genesis and has_nft:
                    status = "active"
                else:
                    status = "inactive"

                # Get relationship stats
                rel_stats = qube.get("relationship_stats", {})

                # Use avg_trust_score from relationships if available, otherwise use default_trust_level from genesis
                trust_score = rel_stats.get("avg_trust_score")
                if trust_score is None or trust_score == 0:
                    # Fall back to default_trust_level from genesis block
                    trust_score = qube.get("default_trust_level", 50)

                # Default tts_enabled to True if voice_model is set, otherwise get from metadata
                voice_model = qube.get("voice_model")
                tts_enabled = qube.get("tts_enabled")
                if tts_enabled is None:
                    # Default: enabled if a voice is configured
                    tts_enabled = voice_model is not None and voice_model != ""

                # Get current model from chain_state if available (runtime model from revolver/switch)
                # Fall back to genesis ai_model if no runtime override
                current_model = qube.get("ai_model", "unknown")
                current_provider = qube.get("ai_provider", "unknown")
                genesis_model = current_model  # Save for debug

                # Try to get runtime model from chain_state (handles revolver mode, switch_model, etc.)
                qube_id = qube.get("qube_id")
                loaded_qubes_keys = list(self.orchestrator.qubes.keys())
                is_loaded = qube_id in self.orchestrator.qubes

                if is_loaded:
                    loaded_qube = self.orchestrator.qubes[qube_id]
                    if hasattr(loaded_qube, 'chain_state') and loaded_qube.chain_state:
                        runtime_model = loaded_qube.chain_state.get_current_model()
                        runtime = loaded_qube.chain_state.state.get("runtime", {})
                        runtime_model_direct = runtime.get("current_model")

                        logger.debug(
                            "list_qubes_model_lookup",
                            qube_id=qube_id,
                            genesis_model=genesis_model,
                            runtime_model_from_get_current_model=runtime_model,
                            runtime_model_direct=runtime_model_direct,
                            revolver_enabled=loaded_qube.chain_state.state.get("settings", {}).get("revolver_mode_enabled"),
                            is_loaded=is_loaded
                        )

                        if runtime_model:
                            current_model = runtime_model
                        if runtime.get("current_provider"):
                            current_provider = runtime["current_provider"]
                else:
                    # Qube not in memory - try to load chain_state directly to get persisted model
                    logger.debug(
                        "list_qubes_qube_not_loaded",
                        qube_id=qube_id,
                        loaded_qubes=loaded_qubes_keys,
                        genesis_model=genesis_model
                    )
                    try:
                        from core.chain_state import ChainState
                        # Find the qube directory by searching for matching qube_id
                        qubes_dir = self.orchestrator.data_dir / "qubes"
                        chain_dir = None
                        for dir_entry in qubes_dir.iterdir():
                            if dir_entry.is_dir():
                                metadata_path = dir_entry / "chain" / "qube_metadata.json"
                                if metadata_path.exists():
                                    with open(metadata_path, "r") as f:
                                        meta = json.load(f)
                                        if meta.get("qube_id") == qube_id:
                                            chain_dir = dir_entry / "chain"
                                            break

                        if chain_dir and (chain_dir / "chain_state.json").exists():
                            # Get encryption key using existing helper
                            qube_dir = chain_dir.parent  # chain_dir is qube_dir/chain
                            encryption_key = self._get_qube_encryption_key(qube_dir)

                            if encryption_key:
                                # Load chain_state to get persisted model
                                temp_chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)
                                runtime_model = temp_chain_state.get_current_model()
                                if runtime_model and runtime_model != "claude-sonnet-4-20250514":  # Not the default
                                    current_model = runtime_model
                                    logger.info(
                                        "list_qubes_loaded_chain_state_model",
                                        qube_id=qube_id,
                                        persisted_model=runtime_model
                                    )
                    except Exception as e:
                        logger.warning(
                            "list_qubes_chain_state_load_failed",
                            qube_id=qube_id,
                            error=str(e)
                        )

                gui_qube = {
                    "qube_id": qube["qube_id"],
                    "name": qube["name"],
                    "ai_provider": current_provider,
                    "ai_model": current_model,
                    "voice_model": voice_model,
                    "tts_enabled": tts_enabled,
                    "creator": qube.get("creator"),
                    "birth_timestamp": qube.get("birth_timestamp"),
                    "home_blockchain": qube.get("home_blockchain", "bitcoincash"),
                    "genesis_prompt": qube.get("genesis_prompt", ""),
                    "favorite_color": qube.get("favorite_color", "#00ff88"),
                    "nft_category_id": qube.get("nft_category_id"),
                    "mint_txid": qube.get("mint_txid"),
                    "avatar_url": qube.get("avatar_url"),
                    "created_at": created_at,
                    "trust_score": trust_score,
                    "memory_blocks_count": qube.get("total_blocks", 0),
                    "block_breakdown": qube.get("block_breakdown", {}),
                    "friends_count": rel_stats.get("friends", 0),
                    "total_relationships": rel_stats.get("total_relationships", 0),
                    "best_friend": rel_stats.get("best_friend"),
                    "status": status,
                    # Additional blockchain metadata
                    "recipient_address": qube.get("recipient_address"),
                    "public_key": qube.get("public_key"),
                    "genesis_block_hash": qube.get("genesis_block_hash"),
                    "commitment": qube.get("commitment"),
                    "bcmr_uri": qube.get("bcmr_uri"),
                    "avatar_ipfs_cid": qube.get("avatar_ipfs_cid"),
                    "avatar_local_path": qube.get("avatar_local_path"),  # For frontend convertFileSrc()
                    "network": qube.get("network"),
                    # Wallet fields (from genesis block content)
                    "wallet_address": qube.get("wallet_address"),
                    "wallet_owner_pubkey": qube.get("wallet_owner_pubkey"),
                    "wallet_qube_pubkey": qube.get("wallet_qube_pubkey"),
                }

                gui_qubes.append(gui_qube)

            return gui_qubes
        except Exception as e:
            logger.error(f"Failed to list qubes: {e}")
            raise

    async def create_qube(
        self,
        name: str,
        genesis_prompt: str,
        ai_provider: str,
        ai_model: str,
        voice_model: str,
        owner_pubkey: str,  # NFT address derived from this by orchestrator
        password: str,
        encrypt_genesis: bool = False,
        favorite_color: str = "#00ff88",
        avatar_file: str = None,
        generate_avatar: bool = False,
        avatar_style: str = "cyberpunk"
    ) -> Dict[str, Any]:
        """Create a new qube with mandatory wallet and NFT."""
        try:
            # Set master key from password before creating qube
            self.orchestrator.set_master_key(password)

            # Prepare config for UserOrchestrator
            # Note: wallet_address (NFT recipient) is derived from owner_pubkey
            config = {
                "name": name,
                "genesis_prompt": genesis_prompt,
                "ai_provider": ai_provider,
                "ai_model": ai_model,
                "voice_model": voice_model,
                "owner_pubkey": owner_pubkey,  # NFT address derived from this
                "encrypt_genesis": encrypt_genesis,
                "favorite_color": favorite_color,
                "generate_avatar": generate_avatar,
                "avatar_style": avatar_style,
            }

            # Only include avatar_file if provided
            if avatar_file is not None:
                config["avatar_file"] = avatar_file

            # Create qube through orchestrator
            qube = await self.orchestrator.create_qube(config)

            # Transform to GUI format (Qube object has attributes, not dict keys)
            gui_qube = {
                "qube_id": qube.qube_id,
                "name": qube.name,
                "ai_provider": ai_provider,
                "ai_model": ai_model,
                "voice_model": voice_model,
                "creator": self.orchestrator.user_id,
                "birth_timestamp": qube.genesis_block.birth_timestamp,
                "home_blockchain": qube.genesis_block.home_blockchain,
                "genesis_prompt": genesis_prompt,
                "favorite_color": favorite_color,
                "created_at": datetime.fromtimestamp(qube.genesis_block.birth_timestamp).isoformat(),
                "trust_score": 50.0,  # Initial trust score
                "memory_blocks_count": 0,
                "friends_count": 0,
                "status": "active",
            }

            return gui_qube
        except Exception as e:
            logger.error(f"Failed to create qube: {e}")
            raise

    async def prepare_qube_mint(
        self,
        name: str,
        genesis_prompt: str,
        ai_provider: str,
        ai_model: str,
        voice_model: str,
        owner_pubkey: str,
        user_address: str,
        password: str,
        encrypt_genesis: bool = False,
        favorite_color: str = "#00ff88",
        avatar_file: str = None,
        generate_avatar: bool = False,
        avatar_style: str = "cyberpunk"
    ) -> Dict[str, Any]:
        """Prepare a WalletConnect-based Qube mint. Returns unsigned WC transaction."""
        try:
            self.orchestrator.set_master_key(password)

            config = {
                "name": name,
                "genesis_prompt": genesis_prompt,
                "ai_provider": ai_provider,
                "ai_model": ai_model,
                "voice_model": voice_model,
                "owner_pubkey": owner_pubkey,
                "encrypt_genesis": encrypt_genesis,
                "favorite_color": favorite_color,
                "generate_avatar": generate_avatar,
                "avatar_style": avatar_style,
            }
            if avatar_file is not None:
                config["avatar_file"] = avatar_file

            result = await self.orchestrator.prepare_qube_mint(config, user_address)
            return result

        except Exception as e:
            logger.error(f"Failed to prepare qube mint: {e}")
            raise

    async def finalize_qube_mint(
        self,
        pending_id: str,
        mint_txid: str,
        password: str
    ) -> Dict[str, Any]:
        """Finalize a WC-minted Qube after wallet has signed and broadcast."""
        try:
            self.orchestrator.set_master_key(password)

            qube = await self.orchestrator.finalize_qube_mint(
                pending_id=pending_id,
                mint_txid=mint_txid,
                password=password
            )

            # Read nft_metadata.json (written synchronously by finalize_qube_mint)
            nft_metadata = {}
            nft_metadata_path = qube.data_dir / "chain" / "nft_metadata.json"
            if nft_metadata_path.exists():
                import json as _json
                with open(nft_metadata_path, 'r') as _f:
                    nft_metadata = _json.load(_f)

            # Transform to GUI format (must match Rust Qube struct fields)
            genesis_dict = qube.genesis_block.to_dict()
            wallet_info = genesis_dict.get("wallet", {})
            gui_qube = {
                "qube_id": qube.qube_id,
                "name": qube.name,
                "ai_provider": genesis_dict.get("ai_provider", ""),
                "ai_model": genesis_dict.get("ai_model", ""),
                "voice_model": genesis_dict.get("voice_model", ""),
                "genesis_prompt": genesis_dict.get("genesis_prompt", ""),
                "creator": self.orchestrator.user_id,
                "birth_timestamp": qube.genesis_block.birth_timestamp,
                "home_blockchain": qube.genesis_block.home_blockchain,
                "favorite_color": genesis_dict.get("favorite_color", "#4A90E2"),
                "created_at": datetime.fromtimestamp(qube.genesis_block.birth_timestamp).isoformat(),
                "trust_score": 50.0,
                "memory_blocks_count": 0,
                "friends_count": 0,
                "status": "active",
                "mint_txid": mint_txid,
                "nft_category_id": genesis_dict.get("nft_category_id", ""),
                # Blockchain fields from nft_metadata + genesis
                "recipient_address": nft_metadata.get("recipient_address"),
                "commitment": nft_metadata.get("commitment"),
                "public_key": genesis_dict.get("public_key"),
                "wallet_address": wallet_info.get("p2sh_address"),
                "wallet_owner_pubkey": wallet_info.get("owner_pubkey"),
                "wallet_qube_pubkey": wallet_info.get("qube_pubkey"),
                "network": nft_metadata.get("network", "mainnet"),
                # Avatar
                "avatar_ipfs_cid": genesis_dict.get("avatar", {}).get("ipfs_cid"),
                "avatar_url": f"https://ipfs.io/ipfs/{genesis_dict.get('avatar', {}).get('ipfs_cid')}" if genesis_dict.get("avatar", {}).get("ipfs_cid") else None,
            }

            return gui_qube

        except Exception as e:
            logger.error(f"Failed to finalize qube mint: {e}")
            raise

    async def get_qube(self, qube_id: str) -> Dict[str, Any]:
        """Get a specific qube by ID"""
        try:
            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)
            qube = self.orchestrator.qubes[qube_id]

            gui_qube = {
                "qube_id": qube["qube_id"],
                "name": qube["name"],
                "ai_provider": qube.get("ai_provider", "unknown"),
                "ai_model": qube.get("ai_model", "unknown"),
                "genesis_prompt": qube.get("genesis_prompt", ""),
                "favorite_color": qube.get("favorite_color", "#00ff88"),
                "created_at": qube.get("created_at", datetime.now().isoformat()),
                "trust_score": None,
                "memory_blocks_count": qube.get("block_count", 0),
                "friends_count": len(qube.get("known_peers", [])),
                "status": "active" if qube.get("is_active", True) else "inactive",
            }

            return gui_qube
        except Exception as e:
            logger.error(f"Failed to get qube {qube_id}: {e}")
            raise

    async def send_message(self, qube_id: str, message: str, password: str = None) -> Dict[str, Any]:
        """Send a message to a qube and get response

        Args:
            qube_id: The qube to send the message to
            message: The message content
            password: Optional password for decryption
        """
        try:
            # Set master key if password provided
            if password:
                self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Process PDFs to ACTION blocks, get cleaned message
            logger.info(
                "about_to_process_documents",
                qube_id=qube_id,
                message_length=len(message),
                has_pdf_tag=("<pdf_base64" in message)
            )

            # Emit event for document processing start
            has_documents = "<pdf_base64" in message or "<image_base64" in message
            if has_documents:
                from core.events import Events
                qube.events.emit(Events.DOCUMENT_PROCESSING_STARTED, {
                    "qube_id": qube_id,
                    "has_pdf": "<pdf_base64" in message,
                    "has_image": "<image_base64" in message
                })

            processed_message, doc_action_blocks = await self._process_documents_to_action_blocks(
                qube_id=qube_id,
                message=message,
                qube=qube
            )

            logger.info(
                "documents_processed",
                qube_id=qube_id,
                action_blocks_created=len(doc_action_blocks),
                processed_message_length=len(processed_message)
            )

            # Emit event for document processing completion
            if has_documents:
                qube.events.emit(Events.DOCUMENT_PROCESSING_COMPLETED, {
                    "qube_id": qube_id,
                    "action_blocks_created": len(doc_action_blocks)
                })

            # Send message and get response (use actual user_id instead of default "human")
            # Pass ACTION blocks to process_message so they're added AFTER user MESSAGE block
            # This ensures correct chronological order: user MESSAGE → ACTION → AI MESSAGE
            logger.info(
                "about_to_send_to_ai",
                qube_id=qube_id,
                message_length=len(processed_message),
                has_action_blocks=len(doc_action_blocks) > 0
            )

            response = await qube.process_message(
                processed_message,
                sender_id=self.orchestrator.user_id,
                action_blocks=doc_action_blocks if doc_action_blocks else None
            )

            logger.info(
                "ai_response_received",
                qube_id=qube_id,
                response_length=len(response) if response else 0
            )
            logger.debug(f"Got response from qube {qube_id}, length: {len(response) if response else 0}")

            # Get the timestamp and block_number of the most recent qube_to_human MESSAGE block
            # block_number is the authoritative sequence - ACTION blocks before this number belong to this response
            response_timestamp = None
            response_block_number = None
            if qube.current_session:
                for block in reversed(qube.current_session.session_blocks):
                    if block.block_type == 'MESSAGE':
                        content = block.content if isinstance(block.content, dict) else {}
                        if content.get('message_type') == 'qube_to_human':
                            response_timestamp = block.timestamp
                            response_block_number = block.block_number
                            break

            # Record relationship interaction (conversation with user)
            if response:
                try:
                    # Get or create relationship with user (specify entity_type="human")
                    user_rel = qube.relationships.storage.get_relationship(self.orchestrator.user_id)
                    if not user_rel:
                        # Create new relationship with entity_type="human"
                        user_rel = qube.relationships.create_relationship(
                            entity_id=self.orchestrator.user_id,
                            entity_type="human",
                            entity_name=self.orchestrator.user_id,  # Use user_id as name for search
                            has_met=True
                        )

                    # Record user message received by Qube
                    qube.relationships.record_message(
                        entity_id=self.orchestrator.user_id,
                        is_outgoing=False,  # Message from user to Qube
                        auto_create=True
                    )

                    # Check for relationship progression
                    user_rel = qube.relationships.get_relationship(self.orchestrator.user_id)
                    if user_rel:
                        progressed = qube.relationships.progression_manager.check_and_progress(
                            user_rel,
                            trust_profile=None,  # Use default profile
                            qube_id=qube.qube_id
                        )

                        if progressed:
                            logger.info(f"🎉 Relationship progressed: {self.orchestrator.user_id} → {user_rel.relationship_status}")

                    logger.debug(f"✅ Recorded relationship interaction for {qube_id[:16]}")
                except Exception as e:
                    logger.warning(f"Failed to record relationship: {e}")
                    # Don't fail the whole message - just log warning

            # Get current model and provider from chain_state (source of truth for runtime settings)
            current_model = qube.chain_state.get_current_model() if qube.chain_state else getattr(qube, 'current_ai_model', None) or qube.genesis_block.ai_model
            runtime = qube.chain_state.state.get("runtime", {}) if qube.chain_state else {}
            current_provider = runtime.get("current_provider") or qube.genesis_block.ai_provider

            return {
                "success": True,
                "qube_id": qube_id,
                "qube_name": qube.genesis_block.qube_name,
                "message": message,
                "response": response,
                # Use actual MESSAGE block timestamp (in seconds) for correct ACTION block association
                "timestamp": response_timestamp,
                # block_number is the authoritative sequence for associating ACTION blocks
                "block_number": response_block_number,
                "current_model": current_model,
                "current_provider": current_provider
            }
        except Exception as e:
            logger.error(f"Failed to send message to qube {qube_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _process_permanent_block(self, qube, qube_id: str, block_num: int) -> Dict[str, Any]:
        """Process a single permanent block (optimized for parallel execution)"""
        try:
            # Load block from file
            block = qube.memory_chain.get_block(block_num)
            block_data = block.to_dict()

            # For GENESIS blocks, the content IS the entire block data (no nested content field)
            # For other blocks, content is in the 'content' field
            if block_data.get('block_type') == 'GENESIS':
                # Genesis block - use entire block data as content
                decrypted_content = block_data
                was_encrypted = False
            else:
                # Other block types - check if content field is encrypted
                content = block_data.get('content', {})
                was_encrypted = isinstance(content, dict) and 'ciphertext' in content

                # Decrypt content if encrypted (run in thread pool to avoid blocking)
                if was_encrypted:
                    try:
                        # Use qube's decrypt_block_content method which has the correct key
                        content_dict = block_data['content']
                        # Run decryption in thread pool for better performance
                        decrypted_content = await asyncio.to_thread(
                            qube.decrypt_block_content,
                            content_dict
                        )
                    except Exception as e:
                        logger.error(f"❌ Failed to decrypt block {block_num}: {e}")
                        decrypted_content = {"error": f"Failed to decrypt content: {str(e)}"}
                else:
                    # Not encrypted - use content as-is
                    decrypted_content = content

            # For GENESIS blocks, use birth_timestamp instead of timestamp
            # For older blocks without timestamp field, extract from filename
            if block_data.get('block_type') == 'GENESIS':
                timestamp_value = block_data.get('birth_timestamp') or block_data.get('timestamp') or block.timestamp
            else:
                # Try to get timestamp from data first (may not exist in old blocks)
                timestamp_value = block_data.get('timestamp')
                if timestamp_value is None:
                    # Fallback: extract from filename (format: {number}_{TYPE}_{timestamp}.json)
                    try:
                        filename = self.orchestrator.qubes[qube_id].memory_chain.block_index[block_num]
                        timestamp_value = int(filename.split('_')[-1].replace('.json', ''))
                    except:
                        # Last resort: use block.timestamp (will be current time from __init__)
                        timestamp_value = block.timestamp

            return {
                "block_number": block.block_number,
                "block_hash": block.block_hash,
                "block_type": block.block_type if isinstance(block.block_type, str) else block.block_type.value,
                "timestamp": timestamp_value * 1000,  # Convert Unix timestamp (seconds) to milliseconds for JavaScript
                "creator": block_data.get('creator') or qube.qube_id,  # Use qube_id if creator not set
                "previous_hash": block.previous_hash,
                "merkle_root": block.merkle_root if hasattr(block, 'merkle_root') else None,
                "signature": block_data.get('signature') or (block.signature if hasattr(block, 'signature') else None),
                "content": decrypted_content,
                "encrypted": was_encrypted,
                # Token usage tracking (preserved from session blocks)
                "input_tokens": block_data.get('input_tokens'),
                "output_tokens": block_data.get('output_tokens'),
                "total_tokens": block_data.get('total_tokens'),
                "model_used": block_data.get('model_used'),
                "estimated_cost_usd": block_data.get('estimated_cost_usd'),
                # Relationship delta tracking
                "relationship_updates": block_data.get('relationship_updates')
            }
        except Exception as e:
            logger.error(f"❌ Error processing block {block_num}: {e}")
            return None  # Will be filtered out

    async def get_qube_blocks(self, qube_id: str, password: str = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Get blocks for a qube (session and permanent) with pagination for better performance"""
        try:
            logger.debug(f"get_qube_blocks called: qube_id={qube_id}, limit={limit}, offset={offset}")

            # Set master key if password provided
            if password:
                self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Get session blocks from qube.current_session (these are never encrypted, so no need for parallel processing)
            session_blocks = []
            if qube.current_session and hasattr(qube.current_session, 'session_blocks'):
                for block in qube.current_session.session_blocks:
                    # Session blocks have different structure - they don't have hash, signature, or creator
                    block_dict = {
                        "block_number": block.block_number,
                        "block_hash": None,  # Session blocks don't have hashes
                        "block_type": block.block_type if isinstance(block.block_type, str) else block.block_type.value,
                        "timestamp": block.timestamp * 1000,  # Convert Unix timestamp (seconds) to milliseconds for JavaScript
                        "creator": qube.qube_id,  # Use qube_id as creator for session blocks
                        "previous_hash": None,  # Session blocks use previous_block_number instead
                        "merkle_root": None,
                        "content": block.to_dict().get('content', {}),  # Get the actual content field
                        "encrypted": False,  # Session blocks are never encrypted
                        # Token usage tracking (if available)
                        "input_tokens": block.input_tokens if hasattr(block, 'input_tokens') else None,
                        "output_tokens": block.output_tokens if hasattr(block, 'output_tokens') else None,
                        "total_tokens": block.total_tokens if hasattr(block, 'total_tokens') else None,
                        "model_used": block.model_used if hasattr(block, 'model_used') else None,
                        "estimated_cost_usd": block.estimated_cost_usd if hasattr(block, 'estimated_cost_usd') else None,
                        # Relationship delta tracking
                        "relationship_updates": block.relationship_updates if hasattr(block, 'relationship_updates') else None
                    }
                    session_blocks.append(block_dict)

            # Get permanent blocks from memory_chain with pagination
            all_block_nums = sorted(qube.memory_chain.block_index.keys(), reverse=True)  # Newest first
            total_blocks = len(all_block_nums)

            # Apply pagination
            paginated_block_nums = all_block_nums[offset:offset + limit]

            # Process blocks in parallel for 10x speedup
            logger.debug(f"Processing {len(paginated_block_nums)} blocks in parallel (total: {total_blocks}, offset: {offset}, limit: {limit})")

            # Create tasks for parallel execution
            tasks = [
                self._process_permanent_block(qube, qube_id, block_num)
                for block_num in paginated_block_nums
            ]

            # Execute all block processing in parallel
            block_results = await asyncio.gather(*tasks, return_exceptions=False)

            # Filter out None results (failed blocks)
            permanent_blocks = [block for block in block_results if block is not None]

            # Sort by block number (newest first) - should already be sorted but ensure it
            session_blocks.sort(key=lambda x: x['block_number'], reverse=True)
            permanent_blocks.sort(key=lambda x: x['block_number'], reverse=True)

            # DEDUPLICATION: Remove session blocks that have already been anchored to permanent storage
            # This can happen during a race condition where session files exist but blocks were anchored
            # Check by timestamp since that's the stable identifier
            permanent_timestamps = {b['timestamp'] for b in permanent_blocks}
            original_session_count = len(session_blocks)
            session_blocks = [b for b in session_blocks if b['timestamp'] not in permanent_timestamps]
            if original_session_count != len(session_blocks):
                logger.info(f"Deduplicated {original_session_count - len(session_blocks)} already-anchored session blocks")

            logger.debug(f"Processed {len(permanent_blocks)} permanent blocks and {len(session_blocks)} session blocks")

            return {
                "success": True,
                "qube_id": qube_id,
                "session_blocks": session_blocks,
                "permanent_blocks": permanent_blocks,
                "pagination": {
                    "total": total_blocks,
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + limit) < total_blocks
                }
            }
        except Exception as e:
            logger.error(f"Failed to get blocks for qube {qube_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def recall_last_context(self, qube_id: str, password: str = None) -> Dict[str, Any]:
        """
        Get the most recent MESSAGE or SUMMARY block for context recall.

        Priority:
        1. Check Short-term Memory (session blocks) for MESSAGE blocks first
        2. If no session MESSAGE blocks, fall back to Long-term Memory
        3. If a SUMMARY block is more recent than the last MESSAGE, returns the summary.
        Otherwise returns the last Qube response from the MESSAGE block.

        Returns:
            Dict with:
                - success: bool
                - content: str (the message or summary text)
                - block_type: str ("MESSAGE" or "SUMMARY")
                - block_number: int
                - timestamp: int (milliseconds)
        """
        try:
            # Set master key if password provided
            if password:
                self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # PRIORITY 1: Check Short-term Memory (session blocks) for MESSAGE blocks
            # First try in-memory session blocks
            session_blocks = []
            if qube.current_session and qube.current_session.session_blocks:
                session_blocks = qube.current_session.session_blocks
            else:
                # Load session blocks from disk if not in memory
                from pathlib import Path
                from core.block import Block
                session_dir = Path(qube.data_dir) / "blocks" / "session"
                if session_dir.exists():
                    block_files = sorted(session_dir.glob("*.json"))
                    for block_file in block_files:
                        try:
                            with open(block_file, 'r') as f:
                                block_data = json.load(f)
                                block = Block.from_dict(block_data)
                                session_blocks.append(block)
                        except Exception:
                            pass

            if session_blocks:
                for block in reversed(session_blocks):
                    block_type = block.block_type if isinstance(block.block_type, str) else block.block_type.value
                    if block_type == "MESSAGE":
                        content = block.content if isinstance(block.content, dict) else {}
                        # Session blocks use message_body, permanent blocks use response
                        response_text = content.get("message_body", content.get("response", content.get("message", "")))
                        if response_text:
                            return {
                                "success": True,
                                "content": response_text,
                                "block_type": "MESSAGE",
                                "block_number": block.block_number,
                                "timestamp": block.timestamp * 1000 if block.timestamp < 1e12 else block.timestamp,
                            }

            # PRIORITY 2: Fall back to Long-term Memory (permanent blocks)
            # Get all block numbers, sorted newest first
            all_block_nums = sorted(qube.memory_chain.block_index.keys(), reverse=True)

            last_message_block = None
            last_summary_block = None

            # Find the most recent MESSAGE and SUMMARY blocks
            for block_num in all_block_nums:
                if last_message_block and last_summary_block:
                    break  # Found both, no need to continue

                block = qube.memory_chain.get_block(block_num)
                if not block:
                    continue

                block_type = block.block_type if isinstance(block.block_type, str) else block.block_type.value

                if block_type == "MESSAGE" and not last_message_block:
                    last_message_block = block
                elif block_type == "SUMMARY" and not last_summary_block:
                    last_summary_block = block

            # Determine which to return based on recency
            if not last_message_block and not last_summary_block:
                return {
                    "success": False,
                    "error": "No MESSAGE or SUMMARY blocks found"
                }

            # Compare timestamps to find the most recent
            message_timestamp = last_message_block.timestamp if last_message_block else 0
            summary_timestamp = last_summary_block.timestamp if last_summary_block else 0

            if last_summary_block and summary_timestamp > message_timestamp:
                # SUMMARY is more recent - return the summary text
                # Decrypt if encrypted (content is a dict with 'ciphertext' key when encrypted)
                content = last_summary_block.content

                if last_summary_block.encrypted:
                    try:
                        # Content is a dict like {"ciphertext": "..."}, decrypt it
                        content = qube.decrypt_block_content(content)
                    except Exception as decrypt_err:
                        logger.warning(f"Failed to decrypt SUMMARY block: {decrypt_err}")
                        content = {}

                if not isinstance(content, dict):
                    content = {}

                summary_text = content.get("summary_text", content.get("summary", ""))

                return {
                    "success": True,
                    "content": summary_text,
                    "block_type": "SUMMARY",
                    "block_number": last_summary_block.block_number,
                    "timestamp": last_summary_block.timestamp * 1000  # Convert to milliseconds
                }
            elif last_message_block:
                # MESSAGE is more recent - return the Qube's response
                # Decrypt if encrypted (content is a dict with 'ciphertext' key when encrypted)
                content = last_message_block.content

                if last_message_block.encrypted:
                    try:
                        content = qube.decrypt_block_content(content)
                    except Exception as decrypt_err:
                        logger.warning(f"Failed to decrypt MESSAGE block: {decrypt_err}")
                        content = {}

                if not isinstance(content, dict):
                    content = {}

                response_text = content.get("response", content.get("message", ""))

                return {
                    "success": True,
                    "content": response_text,
                    "block_type": "MESSAGE",
                    "block_number": last_message_block.block_number,
                    "timestamp": last_message_block.timestamp * 1000  # Convert to milliseconds
                }
            else:
                return {
                    "success": False,
                    "error": "No suitable blocks found"
                }

        except Exception as e:
            logger.error(f"Failed to recall last context for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def generate_speech(self, qube_id: str, text: str, password: str = None) -> Dict[str, Any]:
        """Generate speech audio for given text using qube's voice"""
        import time
        start_time = time.time()

        try:
            # Set master key if password provided
            if password:
                self.orchestrator.set_master_key(password)

            load_start = time.time()
            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)
            load_time = time.time() - load_start
            logger.info(f"TTS: qube load took {load_time*1000:.0f}ms")

            qube = self.orchestrator.qubes[qube_id]

            # Check if audio_manager is initialized
            if not qube.audio_manager:
                return {
                    "success": False,
                    "error": "Audio manager not initialized for this qube"
                }

            # Get voice model from chain_state (source of truth)
            voice_model = None
            if qube.chain_state:
                voice_model = qube.chain_state.get_voice_model()
            if not voice_model:
                voice_model = getattr(qube.genesis_block, 'voice_model', 'openai:alloy')
            if not voice_model:
                voice_model = 'openai:alloy'

            # Parse provider and voice from format "provider:voice"
            if ':' in voice_model:
                provider, voice_name = voice_model.split(':', 1)
            else:
                # Legacy format without provider (assume openai)
                provider = 'openai'
                voice_name = voice_model

            # Get block number from the most recent session block (for file naming)
            block_number = None
            if qube.current_session and qube.current_session.session_blocks:
                last_block = qube.current_session.session_blocks[-1]
                block_number = last_block.block_number
                logger.debug(
                    "tts_using_block_number",
                    qube_id=qube_id,
                    block_number=block_number
                )

            # Generate speech file in qube's audio directory
            # Returns (audio_path, total_chunks) tuple
            tts_start = time.time()
            logger.info(f"TTS: Starting {provider}:{voice_name} for qube {qube_id}, text_length={len(text)}")
            audio_path, total_chunks = await qube.audio_manager.generate_speech_file(
                text=text,
                voice_model=voice_name,
                provider=provider,
                block_number=block_number
            )
            tts_time = time.time() - tts_start
            total_time = time.time() - start_time
            logger.info(f"TTS: {provider}:{voice_name} completed in {tts_time*1000:.0f}ms (total {total_time*1000:.0f}ms)")

            return {
                "success": True,
                "audio_path": str(audio_path),
                "total_chunks": total_chunks,
                "qube_id": qube_id
            }
        except Exception as e:
            logger.error(f"Failed to generate speech for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def update_qube_avatar(self, qube_id: str, avatar_path: str) -> Dict[str, Any]:
        """Update a Qube's avatar image."""
        import shutil
        from datetime import timezone as _tz
        try:
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None
            for d in qubes_dir.iterdir():
                if d.is_dir() and qube_id in d.name:
                    qube_dir = d
                    break

            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            chain_dir = qube_dir / "chain"
            dest_path = chain_dir / f"{qube_id}_avatar.png"
            shutil.copy2(avatar_path, dest_path)

            # Update genesis.json avatar field
            genesis_file = chain_dir / "genesis.json"
            if genesis_file.exists():
                with open(genesis_file, 'r', encoding='utf-8') as f:
                    genesis = json.load(f)
                genesis["avatar"] = {
                    "source": "uploaded",
                    "ipfs_cid": None,
                    "local_path": dest_path.name,
                    "file_format": "png",
                    "dimensions": "unknown",
                }
                with open(genesis_file, 'w', encoding='utf-8') as f:
                    json.dump(genesis, f, indent=2)

            # Update BCMR — clear IPFS icon/image, mark pending re-upload
            qube_name = qube_dir.name.rsplit('_', 1)[0]
            bcmr_file = qube_dir / "blockchain" / f"{qube_name}_bcmr.json"
            if bcmr_file.exists():
                with open(bcmr_file, 'r', encoding='utf-8') as f:
                    bcmr = json.load(f)
                now = datetime.now(_tz.utc).isoformat()
                bcmr["latestRevision"] = now
                for _, identity_revisions in bcmr.get("identities", {}).items():
                    latest_ts = max(identity_revisions.keys())
                    revision = identity_revisions[latest_ts]
                    uris = revision.get("uris", {})
                    uris.pop("icon", None)
                    uris.pop("image", None)
                    uris["icon_pending_upload"] = dest_path.name
                    revision["uris"] = uris
                    identity_revisions[now] = revision
                with open(bcmr_file, 'w', encoding='utf-8') as f:
                    json.dump(bcmr, f, indent=2)

            return {"success": True, "avatar_path": str(dest_path)}
        except Exception as e:
            logger.error(f"Failed to update avatar: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def update_qube_config(self, qube_id: str, ai_model: str = None, voice_model: str = None, favorite_color: str = None, tts_enabled: bool = None, evaluation_model: str = None) -> Dict[str, Any]:
        """Update qube runtime configuration using qube_metadata.json as single source of truth"""
        try:
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None

            # Find qube directory
            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir():
                    metadata_path = dir_entry / "chain" / "qube_metadata.json"
                    if metadata_path.exists():
                        with open(metadata_path, "r") as f:
                            data = json.load(f)
                            if data["qube_id"] == qube_id:
                                qube_dir = dir_entry
                                break

            if not qube_dir:
                raise Exception(f"Qube {qube_id} not found")

            # Use qube_metadata.json as the ONLY source of truth
            metadata_path = qube_dir / "chain" / "qube_metadata.json"

            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            # Update mutable settings (NOT genesis_block - that's immutable!)
            updated_fields = []
            if ai_model is not None:
                # Update chain_state using ChainState class (handles encryption)
                encryption_key = self._get_qube_encryption_key(qube_dir)
                if encryption_key:
                    from core.chain_state import ChainState
                    chain_dir = qube_dir / "chain"
                    chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)

                    # Check current model mode - Manual mode uses locked_model, others use override
                    model_mode = chain_state.get_model_mode()
                    if model_mode == "manual":
                        # In Manual mode, update the locked model (what reasoner actually reads)
                        chain_state.set_model_lock(True, ai_model)
                        logger.info(f"Updated chain_state locked_model={ai_model} (Manual mode)")
                    else:
                        # In Autonomous/Revolver mode, update the override
                        chain_state.set_current_model_override(ai_model)
                        logger.info(f"Updated chain_state current_model_override={ai_model}")
                else:
                    logger.warning("Cannot update chain_state - no encryption key available")
                updated_fields.append(f"ai_model={ai_model}")

            if voice_model is not None:
                metadata["genesis_block"]["voice_model"] = voice_model
                updated_fields.append(f"voice_model={voice_model}")
                logger.info(f"🔊 VOICE_SAVE: Saving voice_model={voice_model} for qube {qube_id}")
                # Update chain_state - use loaded qube's chain_state if available (avoids dual-instance issues)
                # Otherwise create a new instance for disk-only update
                if qube_id in self.orchestrator.qubes and self.orchestrator.qubes[qube_id].chain_state:
                    # Qube is loaded - use its chain_state directly (will be updated again below, but consistent instance)
                    logger.info(f"🔊 VOICE_SAVE: Using loaded qube's chain_state")
                else:
                    # Qube not loaded - create new ChainState instance for disk update
                    encryption_key = self._get_qube_encryption_key(qube_dir)
                    if encryption_key:
                        from core.chain_state import ChainState
                        chain_dir = qube_dir / "chain"
                        logger.info(f"🔊 VOICE_SAVE: Creating new ChainState, chain_dir={chain_dir}")
                        chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)
                        old_voice = chain_state.get_voice_model()
                        logger.info(f"🔊 VOICE_SAVE: Before update: {old_voice}")
                        chain_state.set_voice_model(voice_model)  # Use proper setter
                        # Verify it was saved in memory
                        new_voice = chain_state.get_voice_model()
                        logger.info(f"🔊 VOICE_SAVE: After update (memory): {new_voice}")
                        # CRITICAL: Verify disk was actually updated by reading with fresh instance
                        verify_chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)
                        disk_voice = verify_chain_state.get_voice_model()
                        logger.info(f"🔊 VOICE_SAVE: After update (fresh disk read): {disk_voice}")
                        if disk_voice != voice_model:
                            logger.error(f"🔊 VOICE MISMATCH: Expected {voice_model}, got {disk_voice} from disk!")
                            raise Exception(f"Voice save failed: disk shows {disk_voice} instead of {voice_model}")
                    else:
                        # Log detailed diagnostic info
                        logger.error(f"🔊 VOICE_SAVE FAILED: No encryption key available! master_key={bool(self.orchestrator.master_key)}")
                        raise Exception("Voice save failed: encryption key not available (password may be missing)")

            if favorite_color is not None:
                metadata["genesis_block"]["favorite_color"] = favorite_color
                updated_fields.append(f"favorite_color={favorite_color}")

            if tts_enabled is not None:
                metadata["genesis_block"]["tts_enabled"] = tts_enabled
                updated_fields.append(f"tts_enabled={tts_enabled}")
                logger.info(f"🔊 Setting tts_enabled={tts_enabled} for qube {qube_id}")
                # Also update chain_state (the single source of truth)
                encryption_key = self._get_qube_encryption_key(qube_dir)
                if encryption_key:
                    from core.chain_state import ChainState
                    chain_dir = qube_dir / "chain"
                    chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)
                    settings = chain_state.state.setdefault("settings", {})
                    settings["tts_enabled"] = tts_enabled
                    chain_state._save(preserve_gui_fields=False)
                    logger.info(f"Updated chain_state tts_enabled={tts_enabled}")

            if evaluation_model is not None:
                metadata["genesis_block"]["evaluation_model"] = evaluation_model
                updated_fields.append(f"evaluation_model={evaluation_model}")

            # Save updated metadata to qube_metadata.json ONLY
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"✅ Updated qube_metadata.json for {qube_id}: {', '.join(updated_fields)}")

            # If qube is loaded in memory, update it there too
            if qube_id in self.orchestrator.qubes:
                qube = self.orchestrator.qubes[qube_id]
                if ai_model is not None:
                    # Update current model (NOT genesis block - that's immutable!)
                    qube.current_ai_model = ai_model

                    # Check model mode - Manual mode uses locked_model, others use override
                    model_mode = qube.chain_state.get_model_mode()
                    if model_mode == "manual":
                        qube.chain_state.set_model_lock(True, ai_model)
                    else:
                        qube.chain_state.set_current_model_override(ai_model)

                    # Update provider using ModelRegistry (handles all models correctly)
                    from ai.model_registry import ModelRegistry
                    model_info = ModelRegistry.get_model_info(ai_model)
                    if model_info:
                        qube.ai_provider = model_info["provider"]
                    elif ":" in ai_model:
                        qube.ai_provider = "ollama"
                if voice_model is not None:
                    logger.info(f"🔊 VOICE UPDATE: Setting voice_model={voice_model} for qube {qube_id}")
                    qube.genesis_block.voice_model = voice_model
                    # Update chain_state using proper setter (saves to disk!)
                    if qube.chain_state:
                        old_voice = qube.chain_state.get_voice_model()
                        qube.chain_state.set_voice_model(voice_model)  # This calls _save()!
                        # Verify by reading back from the same instance
                        verified_voice = qube.chain_state.get_voice_model()
                        logger.info(f"🔊 VOICE UPDATE: chain_state saved from {old_voice} to {voice_model}, verified={verified_voice}")
                        # Double-check by creating a fresh ChainState to read disk
                        from core.chain_state import ChainState
                        encryption_key = self._get_qube_encryption_key(qube_dir)
                        if encryption_key:
                            fresh_chain_state = ChainState(data_dir=qube_dir / "chain", encryption_key=encryption_key)
                            disk_voice = fresh_chain_state.get_voice_model()
                            logger.info(f"🔊 VOICE VERIFY: Fresh disk read = {disk_voice}")
                            if disk_voice != voice_model:
                                logger.error(f"🔊 VOICE MISMATCH: Expected {voice_model}, got {disk_voice} from disk!")
                    else:
                        logger.warning(f"🔊 VOICE UPDATE: qube has no chain_state!")
                    # Reinitialize audio manager with new voice
                    if qube.audio_manager:
                        qube.init_audio()
                if favorite_color is not None:
                    qube.genesis_block.favorite_color = favorite_color
                if tts_enabled is not None and hasattr(qube.genesis_block, 'tts_enabled'):
                    qube.genesis_block.tts_enabled = tts_enabled
                if evaluation_model is not None:
                    qube.genesis_block.evaluation_model = evaluation_model

            return {
                "success": True,
                "qube_id": qube_id,
                "updated": {
                    "ai_model": ai_model,
                    "voice_model": voice_model,
                    "favorite_color": favorite_color,
                    "tts_enabled": tts_enabled,
                    "evaluation_model": evaluation_model
                }
            }
        except Exception as e:
            logger.error(f"❌ Failed to update qube config: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    # ========== Voice Settings Commands ==========

    async def get_voice_settings(self, user_id: str, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Get voice settings for a qube.

        Returns the qube's voice configuration including voice_library_ref,
        voice_model, and available voice library entries.
        """
        try:
            if password:
                self.orchestrator.set_master_key(password)

            # Load the qube
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Get voice settings from chain_state
            voice_library_ref = qube.chain_state.get_voice_library_ref() if qube.chain_state else None
            voice_model = qube.chain_state.get_voice_model() if qube.chain_state else "openai:alloy"
            tts_enabled = qube.chain_state.is_tts_enabled() if qube.chain_state else False

            # Get user's voice library
            from config.user_preferences import UserPreferencesManager
            from dataclasses import asdict
            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)
            voice_library = prefs_manager.get_voice_library()
            qwen3_prefs = prefs_manager.get_qwen3_preferences()

            # Convert VoiceLibraryEntry dataclasses to dicts for JSON serialization
            voice_library_dict = {
                voice_id: asdict(entry) for voice_id, entry in voice_library.items()
            }

            return {
                "success": True,
                "qube_id": qube_id,
                "voice_library_ref": voice_library_ref,
                "voice_model": voice_model,
                "tts_enabled": tts_enabled,
                "voice_library": voice_library_dict,
                "qwen3_preferences": {
                    "model_variant": qwen3_prefs.model_variant,
                    "use_flash_attention": qwen3_prefs.use_flash_attention,
                    "supported_languages": qwen3_prefs.SUPPORTED_LANGUAGES,
                }
            }
        except Exception as e:
            logger.error(f"Failed to get voice settings for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def update_voice_settings(
        self,
        user_id: str,
        qube_id: str,
        password: str,
        voice_library_ref: str = None,
        tts_enabled: bool = None
    ) -> Dict[str, Any]:
        """
        Update voice settings for a qube.

        Args:
            user_id: User ID
            qube_id: Qube ID
            password: Master password
            voice_library_ref: Reference to voice library entry (e.g., "designed:wise_mentor")
            tts_enabled: Whether TTS is enabled for this qube
        """
        try:
            if password:
                self.orchestrator.set_master_key(password)

            # Load the qube
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            if not qube.chain_state:
                return {
                    "success": False,
                    "error": "Chain state not initialized"
                }

            updated_fields = []

            # Update voice_library_ref (also updates voice_model)
            if voice_library_ref is not None:
                qube.chain_state.set_voice_library_ref(voice_library_ref)
                updated_fields.append("voice_library_ref")
                updated_fields.append("voice_model")

            # Update TTS enabled
            if tts_enabled is not None:
                qube.chain_state.set_tts_enabled(tts_enabled)
                updated_fields.append("tts_enabled")

            return {
                "success": True,
                "qube_id": qube_id,
                "updated_fields": updated_fields,
                "voice_library_ref": qube.chain_state.get_voice_library_ref(),
                "voice_model": qube.chain_state.get_voice_model(),
                "tts_enabled": qube.chain_state.is_tts_enabled()
            }
        except Exception as e:
            logger.error(f"Failed to update voice settings for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def preview_voice(
        self,
        user_id: str,
        text: str,
        voice_type: str,
        language: str = "en",
        design_prompt: str = None,
        clone_audio_path: str = None,
        clone_audio_text: str = None,
        preset_voice: str = None
    ) -> Dict[str, Any]:
        """
        Generate a preview of a voice configuration.

        Args:
            user_id: User ID
            text: Text to synthesize for preview
            voice_type: "designed", "cloned", or "preset"
            language: Language code (default: "en")
            design_prompt: Voice design description (for designed voices)
            clone_audio_path: Path to clone audio sample (for cloned voices)
            clone_audio_text: Transcript of clone audio (for cloned voices)
            preset_voice: Preset voice name (for preset voices)

        Returns:
            Dict with audio_path to preview file
        """
        try:
            from config.user_preferences import UserPreferencesManager
            import tempfile

            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)

            # Normalize voice_type (frontend sends 'design'/'clone', backend expects 'designed'/'cloned')
            if voice_type == "design":
                voice_type = "designed"
            elif voice_type == "clone":
                voice_type = "cloned"

            # Check if WSL2 TTS server is available and use it instead of local Qwen3
            try:
                from audio.wsl2_tts import WSL2TTSProvider
                wsl2_provider = WSL2TTSProvider(auto_start=False)
                availability = await wsl2_provider.check_availability(try_auto_start=False)

                logger.info(f"WSL2 availability check: {availability}")

                if availability.get("available"):
                    # Use WSL2 TTS server
                    logger.info(f"Using WSL2 TTS server for preview - voice_type={voice_type}, design_prompt={design_prompt[:50] if design_prompt else None}")
                    audio_bytes = await wsl2_provider.generate_preview(
                        speaker=preset_voice if voice_type == "preset" else None,
                        design_prompt=design_prompt if voice_type == "designed" else None,
                        clone_audio_path=Path(clone_audio_path) if clone_audio_path and voice_type == "cloned" else None,
                        clone_audio_text=clone_audio_text if voice_type == "cloned" else None,
                        language=language,
                    )
                    logger.info("WSL2 preview generation completed successfully")
                else:
                    raise Exception(f"WSL2 TTS not available: {availability.get('error', 'unknown')}")
            except Exception as e:
                logger.warning(f"WSL2 TTS failed, falling back to local Qwen3: {e}")
                # Fall back to local Qwen3TTSProvider
                from audio.qwen_tts import Qwen3TTSProvider
                qwen3_prefs = prefs_manager.get_qwen3_preferences()

                provider = Qwen3TTSProvider(
                    model_variant=qwen3_prefs.model_variant,
                    use_flash_attention=qwen3_prefs.use_flash_attention
                )

                audio_bytes = await provider.generate_preview(
                    speaker=preset_voice if voice_type == "preset" else None,
                    design_prompt=design_prompt if voice_type == "designed" else None,
                    clone_audio_path=Path(clone_audio_path) if clone_audio_path and voice_type == "cloned" else None,
                    clone_audio_text=clone_audio_text if voice_type == "cloned" else None,
                    language=language,
                )

            # Write to temp file with timestamp to avoid browser caching
            import time
            temp_dir = Path(tempfile.gettempdir())
            timestamp = int(time.time() * 1000)
            output_path = temp_dir / f"voice_preview_{user_id}_{timestamp}.wav"
            with open(output_path, "wb") as f:
                f.write(audio_bytes)

            return {
                "success": True,
                "audio_path": str(output_path)
            }
        except Exception as e:
            logger.error(f"Failed to generate voice preview: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def add_voice_to_library(
        self,
        user_id: str,
        name: str,
        voice_type: str,
        language: str = "en",
        design_prompt: str = None,
        clone_audio_path: str = None,
        clone_audio_text: str = None
    ) -> Dict[str, Any]:
        """
        Add a new voice to the user's voice library.

        Args:
            user_id: User ID
            name: Display name for the voice
            voice_type: "designed" or "cloned"
            language: Language code
            design_prompt: Voice design description (for designed voices)
            clone_audio_path: Path to clone audio (for cloned voices)
            clone_audio_text: Transcript of clone audio (for cloned voices)

        Returns:
            Dict with voice_id of the created entry
        """
        try:
            from config.user_preferences import UserPreferencesManager, VoiceLibraryEntry
            import shutil

            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)

            # For cloned voices, copy the audio file to permanent storage
            final_clone_path = None
            if voice_type == "cloned" and clone_audio_path:
                clones_dir = prefs_manager.get_voice_clones_dir()
                source_path = Path(clone_audio_path)
                # Create safe filename: replace spaces with underscores, keep extension
                safe_name = name.lower().replace(' ', '_').replace('%', '')
                file_ext = source_path.suffix if source_path.suffix else '.wav'
                final_clone_path = clones_dir / f"{safe_name}{file_ext}"
                shutil.copy2(source_path, final_clone_path)
                final_clone_path = str(final_clone_path)

            # Add to library (method expects individual args, not VoiceLibraryEntry)
            voice_id = prefs_manager.add_voice_to_library(
                name=name,
                voice_type=voice_type,
                language=language,
                design_prompt=design_prompt,
                clone_audio_path=final_clone_path,
                clone_audio_text=clone_audio_text
            )

            return {
                "success": True,
                "voice_id": voice_id,
                "voice_type": voice_type,
                "name": name
            }
        except Exception as e:
            logger.error(f"Failed to add voice to library: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def update_voice_in_library(
        self,
        user_id: str,
        voice_id: str,
        name: str = None,
        language: str = None,
        design_prompt: str = None
    ) -> Dict[str, Any]:
        """
        Update an existing voice in the library.

        Args:
            user_id: User ID
            voice_id: Voice ID to update
            name: New display name
            language: New language code
            design_prompt: New voice design description
        """
        try:
            from config.user_preferences import UserPreferencesManager

            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)
            prefs_manager.update_voice_in_library(
                voice_id=voice_id,
                name=name,
                language=language,
                design_prompt=design_prompt
            )

            return {
                "success": True,
                "voice_id": voice_id
            }
        except Exception as e:
            logger.error(f"Failed to update voice in library: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def delete_voice_from_library(self, user_id: str, voice_id: str) -> Dict[str, Any]:
        """
        Delete a voice from the user's library.

        Also deletes the associated audio file for cloned voices.

        Args:
            user_id: User ID
            voice_id: Voice ID to delete
        """
        try:
            from config.user_preferences import UserPreferencesManager

            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)

            # Get voice info before deleting to find audio file path
            voice = prefs_manager.get_voice_by_id(voice_id)
            audio_deleted = False

            if voice and voice.clone_audio_path:
                # Delete the audio file if it exists
                audio_path = Path(voice.clone_audio_path)
                if audio_path.exists():
                    try:
                        audio_path.unlink()
                        audio_deleted = True
                        logger.info(f"Deleted voice audio file: {audio_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete voice audio file {audio_path}: {e}")

            prefs_manager.delete_voice_from_library(voice_id)

            return {
                "success": True,
                "voice_id": voice_id,
                "audio_deleted": audio_deleted
            }
        except Exception as e:
            logger.error(f"Failed to delete voice from library: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def get_voice_library(self, user_id: str) -> Dict[str, Any]:
        """
        Get the user's complete voice library.

        Returns all saved voices (designed and cloned).
        """
        try:
            from config.user_preferences import UserPreferencesManager
            from dataclasses import asdict

            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)
            voice_library = prefs_manager.get_voice_library()

            # Convert VoiceLibraryEntry dataclasses to dicts for JSON serialization
            voice_library_dict = {
                voice_id: asdict(entry) for voice_id, entry in voice_library.items()
            }

            return {
                "success": True,
                "voice_library": voice_library_dict
            }
        except Exception as e:
            logger.error(f"Failed to get voice library: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def check_qwen3_status(self, user_id: str) -> Dict[str, Any]:
        """
        Check Qwen3-TTS availability and GPU status.

        Returns GPU info, VRAM, downloaded models, recommended variant, and current model_variant preference.
        """
        try:
            from audio.audio_manager import AudioManager
            from config.user_preferences import UserPreferencesManager
            from utils.paths import get_user_data_dir

            # Create temporary audio manager to check status
            audio_manager = AudioManager()
            status = audio_manager.check_qwen3_status()

            # Get current model_variant from user preferences
            model_variant = "1.7B"  # Default
            try:
                user_data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(user_data_dir)
                prefs = prefs_manager.load_preferences()
                model_variant = prefs.qwen3.model_variant
            except Exception as e:
                logger.debug(f"Could not load model_variant preference: {e}")

            return {
                "success": True,
                "model_variant": model_variant,
                **status
            }
        except Exception as e:
            logger.error(f"Failed to check Qwen3 status: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "available": False,
                "model_variant": "1.7B",
                "fallback_provider": "gemini"
            }

    async def check_kokoro_status(self, user_id: str) -> Dict[str, Any]:
        """
        Check Kokoro TTS availability.

        Returns availability status, voice count, and any errors.
        """
        try:
            from audio.audio_manager import AudioManager

            # Create temporary audio manager to check status
            audio_manager = AudioManager()
            status = audio_manager.check_kokoro_status()

            # Also get available voices for the UI
            voices = {}
            if status.get("available"):
                try:
                    from audio.kokoro_tts import KokoroTTSProvider
                    voices = KokoroTTSProvider.get_all_voices()
                except Exception:
                    pass

            return {
                "success": True,
                "voices": voices,
                **status
            }
        except Exception as e:
            logger.error(f"Failed to check Kokoro status: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "available": False,
                "voices_count": 0,
                "languages_count": 0
            }

    async def get_tts_progress(self, user_id: str) -> Dict[str, Any]:
        """
        Get current TTS generation progress (for UI polling).

        Returns stage, progress percentage, and message.
        """
        try:
            from audio.qwen_tts import Qwen3TTSProvider
            progress = Qwen3TTSProvider.get_generation_progress()
            return {
                "success": True,
                **progress
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stage": "idle",
                "progress": 0,
                "message": ""
            }

    async def download_qwen3_model(self, user_id: str, model_name: str) -> Dict[str, Any]:
        """
        Start downloading a Qwen3-TTS model.

        Args:
            user_id: User ID
            model_name: Model to download (e.g., "1.7B-CustomVoice")

        Returns:
            Dict with download_id for tracking progress
        """
        try:
            from audio.model_downloader import Qwen3ModelDownloader

            downloader = Qwen3ModelDownloader()
            download_id = downloader.start_download(model_name)

            return {
                "success": True,
                "download_id": download_id,
                "model_name": model_name
            }
        except Exception as e:
            logger.error(f"Failed to start Qwen3 model download: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def get_qwen3_download_progress(self, user_id: str, download_id: str) -> Dict[str, Any]:
        """
        Get download progress for a Qwen3 model.

        Args:
            user_id: User ID
            download_id: Download ID from download_qwen3_model

        Returns:
            Dict with progress percentage, speed, ETA
        """
        try:
            from audio.model_downloader import Qwen3ModelDownloader

            downloader = Qwen3ModelDownloader()
            progress = downloader.get_progress(download_id)

            return {
                "success": True,
                **progress
            }
        except Exception as e:
            logger.error(f"Failed to get download progress: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def cancel_qwen3_download(self, user_id: str, download_id: str) -> Dict[str, Any]:
        """
        Cancel an in-progress Qwen3 model download.

        Args:
            user_id: User ID
            download_id: Download ID to cancel
        """
        try:
            from audio.model_downloader import Qwen3ModelDownloader

            downloader = Qwen3ModelDownloader()
            downloader.cancel_download(download_id)

            return {
                "success": True,
                "download_id": download_id
            }
        except Exception as e:
            logger.error(f"Failed to cancel download: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def delete_qwen3_model(self, user_id: str, model_name: str) -> Dict[str, Any]:
        """
        Delete a downloaded Qwen3 model to free disk space.

        Args:
            user_id: User ID
            model_name: Model to delete (e.g., "1.7B-CustomVoice")
        """
        try:
            import shutil
            models_dir = Path.home() / ".qubes" / "models" / "qwen3-tts"
            model_path = models_dir / model_name

            if model_path.exists():
                shutil.rmtree(model_path)
                return {
                    "success": True,
                    "model_name": model_name,
                    "deleted": True
                }
            else:
                return {
                    "success": True,
                    "model_name": model_name,
                    "deleted": False,
                    "message": "Model not found"
                }
        except Exception as e:
            logger.error(f"Failed to delete Qwen3 model: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def check_gpu_acceleration(self, user_id: str) -> Dict[str, Any]:
        """Check GPU hardware and CUDA PyTorch status."""
        try:
            from audio.gpu_acceleration import check_gpu_acceleration
            return check_gpu_acceleration()
        except Exception as e:
            logger.error(f"Failed to check GPU acceleration: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def install_gpu_acceleration(self, user_id: str) -> Dict[str, Any]:
        """Start installing CUDA PyTorch for GPU acceleration."""
        try:
            from audio.gpu_acceleration import start_install
            install_id = start_install()
            return {"success": True, "install_id": install_id}
        except Exception as e:
            logger.error(f"Failed to start GPU install: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_gpu_install_progress(self, user_id: str, install_id: str) -> Dict[str, Any]:
        """Get GPU acceleration install progress."""
        try:
            from audio.gpu_acceleration import get_install_progress
            progress = get_install_progress(install_id)
            return {"success": True, **progress}
        except Exception as e:
            logger.error(f"Failed to get GPU install progress: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def uninstall_gpu_acceleration(self, user_id: str) -> Dict[str, Any]:
        """Revert to CPU-only PyTorch."""
        try:
            from audio.gpu_acceleration import uninstall_gpu_acceleration
            return uninstall_gpu_acceleration()
        except Exception as e:
            logger.error(f"Failed to uninstall GPU acceleration: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def update_qwen3_preferences(
        self,
        user_id: str,
        model_variant: str = None,
        use_flash_attention: bool = None
    ) -> Dict[str, Any]:
        """
        Update Qwen3-TTS preferences.

        Args:
            user_id: User ID
            model_variant: "1.7B" or "0.6B"
            use_flash_attention: Whether to use FlashAttention2
        """
        try:
            from config.user_preferences import UserPreferencesManager

            data_dir = get_user_data_dir(user_id)
            prefs_manager = UserPreferencesManager(data_dir)
            prefs_manager.update_qwen3_preferences(
                model_variant=model_variant,
                use_flash_attention=use_flash_attention
            )

            qwen3_prefs = prefs_manager.get_qwen3_preferences()

            return {
                "success": True,
                "model_variant": qwen3_prefs.model_variant,
                "use_flash_attention": qwen3_prefs.use_flash_attention
            }
        except Exception as e:
            logger.error(f"Failed to update Qwen3 preferences: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def record_voice_clone_audio(self, user_id: str, duration_seconds: int = 5) -> Dict[str, Any]:
        """
        Record audio from microphone for voice cloning.

        Args:
            user_id: User ID
            duration_seconds: Recording duration (default: 5 seconds, min 3)

        Returns:
            Dict with audio_path to recorded file
        """
        try:
            from audio.recorder import AudioRecorder
            import tempfile

            # Ensure minimum 3 seconds for voice cloning
            duration = max(3, duration_seconds)

            recorder = AudioRecorder()
            temp_dir = Path(tempfile.gettempdir())
            output_path = temp_dir / f"voice_clone_recording_{user_id}.wav"

            await recorder.record_fixed_duration(output_path, duration)

            return {
                "success": True,
                "audio_path": str(output_path),
                "duration_seconds": duration
            }
        except Exception as e:
            logger.error(f"Failed to record voice clone audio: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def save_recorded_audio(self, user_id: str, audio_data: bytes) -> Dict[str, Any]:
        """
        Save recorded audio from browser and convert to WAV format.

        Args:
            user_id: User ID
            audio_data: Raw audio bytes (webm format from browser MediaRecorder)

        Returns:
            Dict with audio_path to saved WAV file
        """
        try:
            import tempfile
            import subprocess
            import time

            temp_dir = Path(tempfile.gettempdir())
            timestamp = int(time.time() * 1000)

            # Save webm input
            webm_path = temp_dir / f"voice_clone_input_{user_id}_{timestamp}.webm"
            with open(webm_path, "wb") as f:
                f.write(audio_data)

            # Convert to WAV using ffmpeg (should be available on most systems)
            wav_path = temp_dir / f"voice_clone_recording_{user_id}_{timestamp}.wav"

            try:
                # Try ffmpeg first
                run_kwargs = {
                    "capture_output": True,
                    "timeout": 30
                }
                # Hide console window on Windows
                if sys.platform == "win32":
                    run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(webm_path), "-ar", "16000", "-ac", "1", str(wav_path)],
                    **run_kwargs
                )
                if result.returncode != 0:
                    raise Exception(f"ffmpeg failed: {result.stderr.decode()}")
            except FileNotFoundError:
                # ffmpeg not found, try pydub as fallback
                try:
                    from pydub import AudioSegment
                    audio = AudioSegment.from_file(str(webm_path), format="webm")
                    audio = audio.set_frame_rate(16000).set_channels(1)
                    audio.export(str(wav_path), format="wav")
                except ImportError:
                    # If pydub not available either, just rename and hope for the best
                    logger.warning("Neither ffmpeg nor pydub available, audio may not work correctly")
                    import shutil
                    shutil.copy(str(webm_path), str(wav_path))

            # Clean up webm file
            try:
                webm_path.unlink()
            except Exception:
                pass

            logger.info(f"Saved recorded audio to {wav_path}")

            return {
                "success": True,
                "audio_path": str(wav_path)
            }
        except Exception as e:
            logger.error(f"Failed to save recorded audio: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def transcribe_audio(self, user_id: str, audio_path: str) -> Dict[str, Any]:
        """
        Transcribe audio file for voice cloning.

        Args:
            user_id: User ID
            audio_path: Path to audio file

        Returns:
            Dict with transcribed text
        """
        try:
            from audio.audio_manager import AudioManager
            import os

            # Check for OpenAI API key
            openai_key = os.getenv("OPENAI_API_KEY")
            has_openai_key = bool(openai_key)
            key_preview = f"{openai_key[:8]}..." if openai_key and len(openai_key) > 8 else "not set"
            logger.info(f"OpenAI API key status: {key_preview}")

            audio_manager = AudioManager()

            # Check if any STT providers are available
            available_providers = list(audio_manager.stt_providers.keys())
            logger.info(f"Available STT providers: {available_providers}")

            if not available_providers:
                # No providers available - give helpful message
                if not has_openai_key:
                    return {
                        "success": False,
                        "error": "No STT providers available. Set OPENAI_API_KEY environment variable to enable auto-transcription.",
                        "stt_available": False,
                        "debug": {"openai_key_set": False, "providers": []}
                    }
                else:
                    return {
                        "success": False,
                        "error": "STT initialization failed despite API key being set. Please type the transcript manually.",
                        "stt_available": False,
                        "debug": {"openai_key_set": True, "key_preview": key_preview, "providers": []}
                    }

            # Verify audio file exists
            if not Path(audio_path).exists():
                return {
                    "success": False,
                    "error": f"Audio file not found: {audio_path}",
                    "stt_available": True
                }

            text = await audio_manager.listen_from_file(Path(audio_path))

            return {
                "success": True,
                "text": text,
                "audio_path": audio_path,
                "stt_available": True
            }
        except Exception as e:
            logger.error(f"Failed to transcribe audio: {e}", exc_info=True)
            error_msg = str(e)
            if "All STT providers failed" in error_msg:
                error_msg = f"Transcription failed ({error_msg}). Please type the transcript manually."
            return {
                "success": False,
                "error": error_msg,
                "stt_available": False
            }

    # ========== End Voice Settings Commands ==========

    # ========== WSL2 TTS Setup Commands ==========

    async def check_wsl2_tts_status(self, user_id: str) -> Dict[str, Any]:
        """
        Check the status of WSL2 TTS installation.

        Returns comprehensive status including:
        - WSL2 and Ubuntu installation status
        - Setup completion status
        - Server running status
        - GPU detection
        """
        try:
            from audio.wsl2_setup import check_wsl2_tts_status as check_status

            status = await check_status()

            return {
                "success": True,
                **status.to_dict()
            }
        except Exception as e:
            logger.error(f"Failed to check WSL2 TTS status: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "wsl2_installed": False,
                "ubuntu_installed": False,
                "setup_complete": False,
            }

    async def setup_wsl2_tts(self, user_id: str) -> Dict[str, Any]:
        """
        Start WSL2 TTS setup process.

        This is a long-running operation that:
        1. Creates setup directory and venv in WSL2
        2. Installs PyTorch with CUDA
        3. Installs qwen-tts and dependencies
        4. Downloads Qwen3-TTS model (~4GB)
        5. Creates TTS server script
        6. Tests the installation

        Progress can be polled via get_wsl2_tts_setup_progress.

        Returns:
            Dict with success status and details
        """
        try:
            from audio.wsl2_setup import setup_wsl2_tts as run_setup

            result = await run_setup()

            return result
        except Exception as e:
            logger.error(f"Failed to setup WSL2 TTS: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def get_wsl2_tts_setup_progress(self, user_id: str) -> Dict[str, Any]:
        """
        Get current WSL2 TTS setup progress.

        Returns:
            Dict with stage, percentage, message, and error
        """
        try:
            from audio.wsl2_setup import get_setup_progress

            progress = get_setup_progress()

            return {
                "success": True,
                **progress
            }
        except Exception as e:
            logger.error(f"Failed to get WSL2 TTS setup progress: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "stage": "error",
                "percentage": 0,
            }

    async def start_wsl2_tts_server(self, user_id: str) -> Dict[str, Any]:
        """
        Start the WSL2 TTS server.

        Returns:
            Dict with success status and server info
        """
        try:
            from audio.wsl2_server_manager import start_server, get_server_status

            success = await start_server(force=True)
            status = get_server_status()

            return {
                "success": success,
                "message": status.get("message", "Server starting..."),
                "ready": status.get("ready", False),
            }
        except Exception as e:
            logger.error(f"Failed to start WSL2 TTS server: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def stop_wsl2_tts_server(self, user_id: str) -> Dict[str, Any]:
        """
        Stop the WSL2 TTS server.

        Returns:
            Dict with success status
        """
        try:
            from audio.wsl2_server_manager import stop_server

            success = await stop_server(manual=True)

            return {
                "success": success,
                "message": "Server stopped" if success else "Failed to stop server"
            }
        except Exception as e:
            logger.error(f"Failed to stop WSL2 TTS server: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def uninstall_wsl2_tts(self, user_id: str) -> Dict[str, Any]:
        """
        Uninstall WSL2 TTS setup (removes directory and models).

        Returns:
            Dict with success status
        """
        try:
            from audio.wsl2_setup import uninstall_wsl2_tts as uninstall

            result = await uninstall()

            return result
        except Exception as e:
            logger.error(f"Failed to uninstall WSL2 TTS: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    # ========== End WSL2 TTS Setup Commands ==========

    async def delete_qube(self, qube_id: str, password: str = None, sweep_address: str = None) -> Dict[str, Any]:
        """Delete a qube and all its data, optionally sweeping wallet funds first."""
        try:
            if password:
                self.orchestrator.set_master_key(password)

            swept_sats = await self.orchestrator.delete_qube(
                qube_id,
                password=password,
                sweep_address=sweep_address
            )

            return {
                "success": True,
                "qube_id": qube_id,
                "swept_sats": swept_sats or 0
            }
        except Exception as e:
            logger.error(f"Failed to delete qube: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def reset_qube(self, qube_id: str, password: str = None) -> Dict[str, Any]:
        """
        Reset a qube to fresh state while preserving identity.

        This is a development-only feature that clears all accumulated state
        (blocks, relationships, skills, snapshots) but keeps the genesis block,
        NFT info, and cryptographic identity intact.

        Args:
            qube_id: Qube ID to reset
            password: Master password for encryption key access

        Returns:
            Dict with success status
        """
        try:
            # Set master key if password provided
            if password:
                self.orchestrator.set_master_key(password)

            success = await self.orchestrator.reset_qube(qube_id)

            return {
                "success": success,
                "qube_id": qube_id
            }
        except Exception as e:
            logger.error(f"Failed to reset qube: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def reset_qube_relationships(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Delete all relationships for a Qube (fresh start)

        Args:
            qube_id: Qube ID
            password: Master password for decryption

        Returns:
            {
                "success": bool,
                "deleted_count": int,
                "error": Optional[str]
            }
        """
        try:
            logger.info(f"Resetting relationships for qube: {qube_id[:16]}...")

            # Set master key
            self.orchestrator.set_master_key(password)

            # Load Qube if not in memory
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Get count before deletion
            relationships = qube.relationships.get_all_relationships()
            count = len(relationships)

            # Clear all relationships via the storage (which uses ChainState)
            # This properly handles encryption and persistence
            qube.relationships.storage.relationships = {}
            qube.relationships.storage._save_relationships()

            # Also delete legacy relationships folder if it exists
            from pathlib import Path
            relationships_dir = Path(qube.data_dir) / "relationships"
            if relationships_dir.exists():
                import shutil
                shutil.rmtree(relationships_dir)
                logger.info(f"Deleted legacy relationships directory: {relationships_dir}")

            logger.info(f"Reset {count} relationships for {qube_id[:16]}")

            return {
                "success": True,
                "deleted_count": count
            }

        except Exception as e:
            logger.error(f"Failed to reset relationships: {e}", exc_info=True)
            return {
                "success": False,
                "deleted_count": 0,
                "error": str(e)
            }

    async def get_qube_relationships(self, qube_id: str, password: str = None) -> Dict[str, Any]:
        """
        Get all relationships for a Qube

        Args:
            qube_id: Qube ID
            password: Master password for decryption

        Returns:
            {
                "success": bool,
                "relationships": List[Dict],  # Full relationship data
                "stats": Dict,  # Summary statistics
                "error": Optional[str]
            }
        """
        try:
            logger.info(f"[GET_RELATIONSHIPS] Starting for qube: {qube_id[:16]}...")
            logger.debug(f"[GET_RELATIONSHIPS] Password provided: {bool(password)}")

            # Try to load qube if password is provided
            if password:
                # Set master key
                self.orchestrator.set_master_key(password)
                logger.debug(f"[GET_RELATIONSHIPS] Master key set")

                # Load Qube if not in memory
                if qube_id not in self.orchestrator.qubes:
                    logger.debug(f"[GET_RELATIONSHIPS] Loading qube from disk...")
                    await self.orchestrator.load_qube(qube_id)
                else:
                    logger.debug(f"[GET_RELATIONSHIPS] Qube already in memory")

                qube = self.orchestrator.qubes[qube_id]
                logger.debug(f"[GET_RELATIONSHIPS] Qube instance obtained: {qube.name}")

                # Reload chain_state to get latest data from disk, then reload relationships
                logger.debug(f"[GET_RELATIONSHIPS] Reloading chain_state from disk...")
                qube.chain_state.reload()

                # Check what's in chain_state relationships section
                rel_data = qube.chain_state.get_relationships()
                logger.debug(f"[GET_RELATIONSHIPS] chain_state.get_relationships() returned {len(rel_data)} entities: {list(rel_data.keys())}")

                logger.debug(f"[GET_RELATIONSHIPS] Reloading relationships from chain_state...")
                qube.relationships.storage._load_relationships()
                logger.debug(f"[GET_RELATIONSHIPS] Reload complete. Storage dict has {len(qube.relationships.storage.relationships)} entries")

                # Get all relationships via social manager
                relationships = qube.relationships.get_all_relationships()
            else:
                # No password provided - cannot decrypt chain_state to read relationships
                # Relationships are stored encrypted in chain_state.json
                logger.warning(f"[GET_RELATIONSHIPS] No password provided - cannot decrypt relationships")
                return {
                    "success": False,
                    "relationships": [],
                    "stats": {},
                    "error": "Password required to load relationships (encrypted in chain_state)"
                }

            logger.info(f"[GET_RELATIONSHIPS] Found {len(relationships)} relationships for {qube_id[:16]}")
            logger.debug(f"[GET_RELATIONSHIPS] Relationship entity_ids: {[r.entity_id for r in relationships]}")

            # Convert to serializable dicts
            rel_dicts = []
            for rel in relationships:
                # Try to get entity name
                entity_name = rel.entity_id
                if rel.entity_type == "qube" and rel.entity_id in self.orchestrator.qubes:
                    # It's another Qube - use their name
                    entity_name = self.orchestrator.qubes[rel.entity_id].name
                elif rel.entity_type == "human":
                    # It's a human user - use their user_id as display name
                    entity_name = rel.entity_id  # e.g., "bit_faced"

                rel_dict = {
                    "entity_id": rel.entity_id,
                    "entity_name": entity_name,
                    "entity_type": rel.entity_type,
                    "status": rel.status,
                    "trust": rel.trust,
                    "has_met": rel.has_met,
                    "is_best_friend": rel.is_best_friend,

                    # Core Trust Metrics (5)
                    "honesty": rel.honesty,
                    "reliability": rel.reliability,
                    "support": rel.support,
                    "loyalty": rel.loyalty,
                    "respect": rel.respect,

                    # Positive Social Metrics (14)
                    "friendship": rel.friendship,
                    "affection": rel.affection,
                    "engagement": rel.engagement,
                    "depth": rel.depth,
                    "humor": rel.humor,
                    "understanding": rel.understanding,
                    "compatibility": rel.compatibility,
                    "admiration": rel.admiration,
                    "warmth": rel.warmth,
                    "openness": rel.openness,
                    "patience": rel.patience,
                    "empowerment": rel.empowerment,
                    "responsiveness": rel.responsiveness,
                    "expertise": rel.expertise,

                    # Negative Social Metrics (10)
                    "antagonism": rel.antagonism,
                    "resentment": rel.resentment,
                    "annoyance": rel.annoyance,
                    "distrust": rel.distrust,
                    "rivalry": rel.rivalry,
                    "tension": rel.tension,
                    "condescension": rel.condescension,
                    "manipulation": rel.manipulation,
                    "dismissiveness": rel.dismissiveness,
                    "betrayal": rel.betrayal,

                    # Communication
                    "messages_sent": rel.messages_sent,
                    "messages_received": rel.messages_received,

                    # Collaboration
                    "collaborations_successful": rel.collaborations_successful,
                    "collaborations_failed": rel.collaborations_failed,

                    # Timeline
                    "first_contact": rel.first_contact,
                    "last_interaction": rel.last_interaction,
                    "days_known": rel.days_known,

                    # Clearance System (v2)
                    "clearance_profile": rel.clearance_profile,
                    "clearance_categories": rel.clearance_categories,
                    "clearance_expires_at": rel.clearance_expires,
                    "clearance_field_grants": rel.clearance_field_grants,
                    "clearance_field_denials": rel.clearance_field_denials,

                    # Tags
                    "tags": rel.tags,
                }
                rel_dicts.append(rel_dict)

            # Get summary stats (only available if qube is fully loaded)
            stats = {}
            if password:
                try:
                    stats = qube.relationships.get_relationship_stats()
                except Exception as e:
                    logger.warning(f"[GET_RELATIONSHIPS] Could not get stats: {e}")

            result = {
                "success": True,
                "relationships": rel_dicts,
                "stats": stats
            }

            logger.info(f"[GET_RELATIONSHIPS] Returning {len(rel_dicts)} relationships")
            logger.debug(f"[GET_RELATIONSHIPS] First relationship sample: {rel_dicts[0] if rel_dicts else 'None'}")

            return result

        except Exception as e:
            logger.error(f"[GET_RELATIONSHIPS] Failed to get relationships: {e}", exc_info=True)
            return {
                "success": False,
                "relationships": [],
                "stats": {},
                "error": str(e)
            }

    async def get_relationship_timeline(self, qube_id: str, entity_id: str, password: str = None) -> Dict[str, Any]:
        """
        Load historical relationship snapshots for timeline visualization

        Args:
            qube_id: ID of the qube
            entity_id: ID of the entity whose relationship timeline to load
            password: Optional password for decryption

        Returns:
            {
                "success": bool,
                "timeline": [{"block_number": int, "timestamp": int, "trust": float, "compatibility": float, ...}],
                "error": str (if failed)
            }
        """
        try:
            logger.info(f"[GET_TIMELINE] Loading relationship timeline for {entity_id} in qube {qube_id[:16]}...")

            # Get user_id for creator detection
            user_id_for_creator_check = self.user_id if hasattr(self, 'user_id') and self.user_id else self.orchestrator.user_id

            # Load qube if password provided
            if password:
                self.orchestrator.set_master_key(password)
                if qube_id not in self.orchestrator.qubes:
                    await self.orchestrator.load_qube(qube_id)
                qube = self.orchestrator.qubes[qube_id]
                snapshots_dir = qube.memory_chain.snapshots_dir
                user_id_for_creator_check = qube.user_name
            else:
                # Access snapshots directory directly
                from pathlib import Path
                qube_data_dir = get_user_qubes_dir(user_id_for_creator_check)
                matching_dirs = [d for d in qube_data_dir.iterdir() if d.is_dir() and qube_id in d.name]

                if not matching_dirs:
                    logger.error(f"[GET_TIMELINE] No matching qube directory found for {qube_id}")
                    return {"success": False, "timeline": [], "error": "Qube directory not found"}

                qube_dir = matching_dirs[0]
                snapshots_dir = qube_dir / "blocks" / "relationship_snapshots"

            if not snapshots_dir.exists():
                logger.info(f"[GET_TIMELINE] No snapshots directory yet")
                return {"success": True, "timeline": []}

            # Load all snapshots and extract this entity's data
            timeline = []
            snapshot_files = sorted(snapshots_dir.glob("snapshot_*.json"))

            logger.info(f"[GET_TIMELINE] Found {len(snapshot_files)} snapshot files")

            for snapshot_file in snapshot_files:
                try:
                    with open(snapshot_file, 'r') as f:
                        snapshot_data = json.load(f)

                    relationships = snapshot_data.get("relationships", {})
                    if entity_id in relationships:
                        rel_data = relationships[entity_id]
                        timeline.append({
                            "block_number": snapshot_data["block_number"],
                            "timestamp": snapshot_data["timestamp"],
                            "trust": rel_data.get("trust", 0),
                            "compatibility": rel_data.get("compatibility", 0),
                            # Include other metrics for potential future use
                            "friendship": rel_data.get("friendship", 0),
                            "affection": rel_data.get("affection", 0),
                        })
                except Exception as e:
                    logger.warning(f"[GET_TIMELINE] Failed to load snapshot {snapshot_file.name}: {e}")
                    continue

            # If we have at least one snapshot, add a synthetic starting point
            # This shows progression from the initial relationship state
            if len(timeline) > 0:
                first_snapshot = timeline[0]
                # Check if this relationship has a first_contact timestamp
                try:
                    with open(snapshot_files[0], 'r') as f:
                        snapshot_data = json.load(f)
                    relationships = snapshot_data.get("relationships", {})
                    if entity_id in relationships:
                        rel_data = relationships[entity_id]
                        first_contact = rel_data.get("first_contact")

                        if first_contact and first_contact < first_snapshot["timestamp"]:
                            # Determine if this is a creator relationship
                            # Creator relationships start at 25, others start at 0
                            entity_type = rel_data.get("entity_type", "qube")
                            is_creator = (entity_type == "human" and entity_id == user_id_for_creator_check)

                            starting_value = 25.0 if is_creator else 0.0

                            # Add starting point at first_contact with initial values
                            timeline.insert(0, {
                                "block_number": 0,
                                "timestamp": first_contact,
                                "trust": starting_value,
                                "compatibility": starting_value,
                                "friendship": starting_value,
                                "affection": starting_value,
                            })
                            logger.info(f"[GET_TIMELINE] Added synthetic starting point (creator={is_creator}, value={starting_value})")
                except Exception as e:
                    logger.warning(f"[GET_TIMELINE] Could not add synthetic starting point: {e}")

            logger.info(f"[GET_TIMELINE] Loaded {len(timeline)} timeline data points")

            return {
                "success": True,
                "timeline": timeline
            }

        except Exception as e:
            logger.error(f"[GET_TIMELINE] Failed to load relationship timeline: {e}", exc_info=True)
            return {
                "success": False,
                "timeline": [],
                "error": str(e)
            }

    async def save_image(self, qube_id: str, image_url: str) -> Dict[str, Any]:
        """Download and save an image to qube's images folder"""
        try:
            import aiohttp
            import hashlib
            from urllib.parse import urlparse

            # Find the qube directory
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None

            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    break

            if not qube_dir:
                raise Exception(f"Qube directory not found for {qube_id}")

            # Create images directory if it doesn't exist
            images_dir = qube_dir / "images"
            images_dir.mkdir(exist_ok=True)

            # Generate filename from URL hash and timestamp
            url_hash = hashlib.md5(image_url.encode()).hexdigest()[:8]
            timestamp = int(datetime.now().timestamp())

            # Try to get file extension from URL
            parsed_url = urlparse(image_url)
            path = parsed_url.path
            extension = ".png"  # Default

            # Check for common image extensions
            for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                if ext in path.lower():
                    extension = ext
                    break

            filename = f"{timestamp}_{url_hash}{extension}"
            file_path = images_dir / filename

            # Download the image
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status != 200:
                        raise Exception(f"Failed to download image: HTTP {response.status}")

                    image_data = await response.read()

                    # Save to file
                    with open(file_path, 'wb') as f:
                        f.write(image_data)

            logger.info(f"✅ Saved image for qube {qube_id}: {filename}")

            return {
                "success": True,
                "qube_id": qube_id,
                "file_path": str(file_path),
                "filename": filename
            }
        except Exception as e:
            logger.error(f"Failed to save image for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def upload_avatar_to_ipfs(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Upload an existing Qube's local avatar to IPFS (Pinata)

        This is useful for fixing Qubes that have a local avatar but failed
        to upload to IPFS during creation.
        """
        try:
            from blockchain.ipfs import IPFSUploader
            import json

            # Set master key for accessing encrypted API keys
            self.orchestrator.set_master_key(password)

            # Find the qube directory
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None
            qube_name = None

            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    # Extract qube name from directory name (format: Name_QubeID)
                    qube_name = dir_entry.name.rsplit('_', 1)[0]
                    break

            if not qube_dir:
                return {"success": False, "error": f"Qube directory not found for {qube_id}"}

            # Load qube metadata to find local avatar path
            metadata_path = qube_dir / "chain" / "qube_metadata.json"
            if not metadata_path.exists():
                return {"success": False, "error": "qube_metadata.json not found"}

            with open(metadata_path, 'r') as f:
                metadata = json.load(f)

            avatar_info = metadata.get("genesis_block", {}).get("avatar", {})
            local_path = avatar_info.get("local_path")

            if not local_path:
                return {"success": False, "error": "No local avatar path found in metadata"}

            # Resolve the avatar path: filename-only (new format) or absolute (legacy)
            avatar_path = Path(local_path)
            if not (avatar_path.is_absolute() and avatar_path.exists()):
                # Filename-only or stale absolute from another OS — resolve from chain dir
                avatar_path = qube_dir / "chain" / avatar_path.name

            if not avatar_path.exists():
                return {"success": False, "error": f"Avatar file not found at {avatar_path}"}

            # Check if already has IPFS CID
            existing_cid = avatar_info.get("ipfs_cid")
            if existing_cid:
                return {
                    "success": True,
                    "qube_id": qube_id,
                    "avatar_ipfs_cid": existing_cid,
                    "message": "Avatar already has IPFS CID"
                }

            # Get Pinata API key from secure storage
            api_keys = self.orchestrator.get_api_keys()
            pinata_jwt = api_keys.pinata_jwt

            if not pinata_jwt:
                return {"success": False, "error": "Pinata API key not configured. Please add it in Settings."}

            # Set the Pinata key in environment for IPFSUploader
            os.environ["PINATA_API_KEY"] = pinata_jwt

            # Upload to IPFS with Pinata
            uploader = IPFSUploader(use_pinata=True)

            if not uploader.use_pinata:
                return {"success": False, "error": "Pinata configuration failed"}

            # Create custom filename
            safe_name = "".join(c for c in qube_name if c.isalnum() or c in ('-', '_'))
            ipfs_filename = f"avatar_{safe_name}_{qube_id}{avatar_path.suffix}"

            logger.info(f"Uploading avatar for {qube_name} to IPFS...")

            ipfs_uri = await uploader.upload_file(
                str(avatar_path),
                pin=True,
                custom_filename=ipfs_filename
            )

            if not ipfs_uri:
                return {"success": False, "error": "IPFS upload failed"}

            # Extract CID from URI
            ipfs_cid = ipfs_uri.replace("ipfs://", "")

            # Update qube_metadata.json
            avatar_info["ipfs_cid"] = ipfs_cid
            metadata["genesis_block"]["avatar"] = avatar_info

            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            # Update nft_metadata.json if it exists
            nft_metadata_path = qube_dir / "chain" / "nft_metadata.json"
            if nft_metadata_path.exists():
                with open(nft_metadata_path, 'r') as f:
                    nft_metadata = json.load(f)
                nft_metadata["avatar_ipfs_cid"] = ipfs_cid
                with open(nft_metadata_path, 'w') as f:
                    json.dump(nft_metadata, f, indent=2)

            # Update genesis.json if it exists
            genesis_path = qube_dir / "chain" / "genesis.json"
            if genesis_path.exists():
                with open(genesis_path, 'r') as f:
                    genesis_data = json.load(f)
                if "avatar" in genesis_data:
                    genesis_data["avatar"]["ipfs_cid"] = ipfs_cid
                    with open(genesis_path, 'w') as f:
                        json.dump(genesis_data, f, indent=2)

            logger.info(f"Successfully uploaded avatar for {qube_name} to IPFS: {ipfs_cid}")

            return {
                "success": True,
                "qube_id": qube_id,
                "qube_name": qube_name,
                "avatar_ipfs_cid": ipfs_cid,
                "ipfs_gateway_url": f"https://gateway.pinata.cloud/ipfs/{ipfs_cid}"
            }

        except Exception as e:
            logger.error(f"Failed to upload avatar to IPFS for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def analyze_image(self, qube_id: str, image_base64: str, user_message: str, password: str = None) -> Dict[str, Any]:
        """Analyze an uploaded image using the qube's vision AI"""
        try:
            # Set master key if password provided
            if password:
                self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Use the qube's describe_image method with vision AI
            description = await qube.describe_image(image_base64, user_message)

            return {
                "success": True,
                "qube_id": qube_id,
                "description": description
            }
        except Exception as e:
            logger.error(f"Failed to analyze image for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def get_qube_skills(self, user_id: str, qube_id: str) -> Dict[str, Any]:
        """Get all skills for a specific qube.

        Reads from skills_cache.json (unencrypted) which is maintained by the
        backend when skills are updated. This allows GUI access without needing
        the encryption key.
        """
        try:
            from utils.skill_definitions import generate_all_skills

            # SECURITY: Validate inputs
            from utils.input_validation import validate_user_id, validate_qube_id
            user_id = validate_user_id(user_id)
            qube_id = validate_qube_id(qube_id)

            # Get qube directory - need to find the actual folder name (may have prefix like "Alph_")
            qubes_base_dir = Path(__file__).parent / "data" / "users" / user_id / "qubes"
            qube_dir = None

            # Look for directory ending with the qube_id
            if qubes_base_dir.exists():
                for dir_path in qubes_base_dir.iterdir():
                    if dir_path.is_dir() and dir_path.name.endswith(qube_id):
                        qube_dir = dir_path
                        break

            if not qube_dir or not qube_dir.exists():
                return {
                    "success": False,
                    "error": f"Qube {qube_id} not found"
                }

            # Read from unencrypted skills cache (written by backend when skills update)
            chain_dir = qube_dir / "chain"
            cache_file = chain_dir / "skills_cache.json"

            compact_data = {
                "skill_xp": {},
                "extra_unlocked": [],
                "history": [],
                "total_xp": 0,
                "last_xp_gain": None
            }

            if cache_file.exists():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        compact_data = json.load(f)
                    logger.info(f"Loaded skills cache for {qube_id}: total_xp={compact_data.get('total_xp', 0)}")
                except Exception as e:
                    logger.warning(f"Failed to read skills cache, using defaults: {e}")

            # Get skill definitions and merge with cached XP data
            skill_definitions = {s["id"]: s for s in generate_all_skills()}
            skills_list = []

            for skill_id, definition in skill_definitions.items():
                skill = definition.copy()

                # Apply stored XP/level from cache
                if skill_id in compact_data.get("skill_xp", {}):
                    stored = compact_data["skill_xp"][skill_id]
                    skill["xp"] = stored.get("xp", 0)
                    skill["level"] = stored.get("level", 0)
                    # Update tier based on level
                    if skill["level"] >= 75:
                        skill["tier"] = "expert"
                    elif skill["level"] >= 50:
                        skill["tier"] = "advanced"
                    elif skill["level"] >= 25:
                        skill["tier"] = "intermediate"
                    else:
                        skill["tier"] = "novice"

                # Apply extra unlocked status
                if skill_id in compact_data.get("extra_unlocked", []):
                    skill["unlocked"] = True

                skills_list.append(skill)

            # Build summary
            summary = {
                "total_skills": len(skill_definitions),
                "unlocked_skills": sum(1 for s in skills_list if s.get("unlocked", False)),
                "maxed_skills": sum(1 for s in skills_list if s.get("level", 0) == 100),
                "total_xp": compact_data.get("total_xp", 0),
                "unlocked_tools": [s.get("toolCallReward") for s in skills_list if s.get("level", 0) == 100 and s.get("toolCallReward")]
            }

            return {
                "success": True,
                "qube_id": qube_id,
                "skills": skills_list,
                "last_updated": compact_data.get("last_xp_gain"),
                "summary": summary
            }

        except Exception as e:
            logger.error(f"Failed to get skills for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def save_qube_skills(self, user_id: str, qube_id: str, skills_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save skills for a specific qube.

        Note: This saves to the skills_cache.json file only.
        The encrypted chain_state is updated by the backend during chat sessions.
        GUI-initiated saves go to cache and will be synced to chain_state on next session.
        """
        try:
            from utils.skill_definitions import generate_all_skills
            from datetime import datetime

            # SECURITY: Validate inputs
            from utils.input_validation import validate_user_id, validate_qube_id
            user_id = validate_user_id(user_id)
            qube_id = validate_qube_id(qube_id)

            # Get qube directory - need to find the actual folder name (may have prefix like "Alph_")
            qubes_base_dir = Path(__file__).parent / "data" / "users" / user_id / "qubes"
            qube_dir = None

            # Look for directory ending with the qube_id
            if qubes_base_dir.exists():
                for dir_path in qubes_base_dir.iterdir():
                    if dir_path.is_dir() and dir_path.name.endswith(qube_id):
                        qube_dir = dir_path
                        break

            if not qube_dir or not qube_dir.exists():
                return {
                    "success": False,
                    "error": f"Qube {qube_id} not found"
                }

            # Convert full skills format to compact format for cache
            skill_definitions = {s["id"]: s for s in generate_all_skills()}
            skill_xp = {}
            extra_unlocked = []
            total_xp = 0

            for skill in skills_data.get("skills", []):
                skill_id = skill.get("id")
                if not skill_id:
                    continue

                xp = skill.get("xp", 0)
                level = skill.get("level", 0)
                unlocked = skill.get("unlocked", False)

                # Get default unlocked state
                default_def = skill_definitions.get(skill_id, {})
                default_unlocked = default_def.get("unlocked", False)

                # Store XP if > 0 or level > 0
                if xp > 0 or level > 0:
                    skill_xp[skill_id] = {"xp": xp, "level": level}
                    total_xp += xp

                # Store unlocked if different from default
                if unlocked and not default_unlocked:
                    if skill_id not in extra_unlocked:
                        extra_unlocked.append(skill_id)

            compact_data = {
                "skill_xp": skill_xp,
                "extra_unlocked": extra_unlocked,
                "history": skills_data.get("history", [])[-100:],  # Cap at 100
                "total_xp": total_xp,
                "last_xp_gain": datetime.utcnow().isoformat() + "Z"
            }

            # Write to cache file
            chain_dir = qube_dir / "chain"
            cache_file = chain_dir / "skills_cache.json"

            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(compact_data, f, indent=2)

            return {
                "success": True,
                "qube_id": qube_id,
                "message": "Skills saved successfully"
            }

        except Exception as e:
            logger.error(f"Failed to save skills for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def add_skill_xp(self, user_id: str, qube_id: str, skill_id: str, xp_amount: float, evidence_block_id: Optional[str] = None) -> Dict[str, Any]:
        """Add XP to a specific skill.

        Note: This modifies the skills_cache.json file only.
        The proper chain_state update happens during active chat sessions via the backend.
        Supports fractional XP amounts (e.g., 0.1 XP per chess move).
        """
        try:
            from utils.skill_definitions import generate_all_skills
            from datetime import datetime

            # SECURITY: Validate inputs
            from utils.input_validation import validate_user_id, validate_qube_id
            user_id = validate_user_id(user_id)
            qube_id = validate_qube_id(qube_id)

            # Get qube directory - need to find the actual folder name
            qubes_base_dir = Path(__file__).parent / "data" / "users" / user_id / "qubes"
            qube_dir = None

            if qubes_base_dir.exists():
                for dir_path in qubes_base_dir.iterdir():
                    if dir_path.is_dir() and dir_path.name.endswith(qube_id):
                        qube_dir = dir_path
                        break

            if not qube_dir or not qube_dir.exists():
                return {
                    "success": False,
                    "error": f"Qube {qube_id} not found"
                }

            # Load skill definitions
            skill_definitions = {s["id"]: s for s in generate_all_skills()}
            if skill_id not in skill_definitions:
                return {"success": False, "error": f"Skill {skill_id} not found"}

            skill_def = skill_definitions[skill_id]

            # Load existing cache
            chain_dir = qube_dir / "chain"
            cache_file = chain_dir / "skills_cache.json"

            compact_data = {
                "skill_xp": {},
                "extra_unlocked": [],
                "history": [],
                "total_xp": 0,
                "last_xp_gain": None
            }

            if cache_file.exists():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        compact_data = json.load(f)
                except Exception:
                    pass

            # Check if skill is unlocked
            is_unlocked = skill_def.get("unlocked", False) or skill_id in compact_data.get("extra_unlocked", [])

            # If locked, flow XP to parent
            target_skill_id = skill_id
            if not is_unlocked:
                parent_id = skill_def.get("parentSkill")
                if parent_id:
                    target_skill_id = parent_id
                    skill_def = skill_definitions.get(parent_id, skill_def)

            # Get current XP/level
            current = compact_data.get("skill_xp", {}).get(target_skill_id, {"xp": 0, "level": 0})
            old_xp = current.get("xp", 0)
            old_level = current.get("level", 0)

            # Add XP
            new_xp = old_xp + xp_amount
            new_level = old_level
            max_xp = skill_def.get("maxXP", 500)
            leveled_up = False
            levels_gained = 0

            # Check for level-ups
            while new_xp >= max_xp and new_level < 100:
                new_xp -= max_xp
                new_level += 1
                levels_gained += 1
                leveled_up = True

            if new_level >= 100:
                new_level = 100
                new_xp = max_xp

            # Update cache
            if "skill_xp" not in compact_data:
                compact_data["skill_xp"] = {}
            compact_data["skill_xp"][target_skill_id] = {"xp": new_xp, "level": new_level}
            compact_data["total_xp"] = compact_data.get("total_xp", 0) + xp_amount
            compact_data["last_xp_gain"] = datetime.utcnow().isoformat() + "Z"

            # Write to cache
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(compact_data, f, indent=2)

            result = {
                "success": True,
                "skill_id": target_skill_id,
                "old_level": old_level,
                "new_level": new_level,
                "xp_gained": xp_amount,
                "current_xp": new_xp,
                "max_xp": max_xp,
                "leveled_up": leveled_up,
                "levels_gained": levels_gained
            }

            if new_level == 100 and skill_def.get("toolCallReward"):
                result["tool_unlocked"] = skill_def["toolCallReward"]

            return result

        except Exception as e:
            logger.error(f"Failed to add XP to skill {skill_id} for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def unlock_skill(self, user_id: str, qube_id: str, skill_id: str) -> Dict[str, Any]:
        """Unlock a specific skill.

        Note: This modifies the skills_cache.json file only.
        """
        try:
            from utils.skill_definitions import generate_all_skills
            from datetime import datetime

            # SECURITY: Validate inputs
            from utils.input_validation import validate_user_id, validate_qube_id
            user_id = validate_user_id(user_id)
            qube_id = validate_qube_id(qube_id)

            # Get qube directory - need to find the actual folder name
            qubes_base_dir = Path(__file__).parent / "data" / "users" / user_id / "qubes"
            qube_dir = None

            if qubes_base_dir.exists():
                for dir_path in qubes_base_dir.iterdir():
                    if dir_path.is_dir() and dir_path.name.endswith(qube_id):
                        qube_dir = dir_path
                        break

            if not qube_dir or not qube_dir.exists():
                return {
                    "success": False,
                    "error": f"Qube {qube_id} not found"
                }

            # Load skill definitions
            skill_definitions = {s["id"]: s for s in generate_all_skills()}
            if skill_id not in skill_definitions:
                return {"success": False, "error": f"Skill {skill_id} not found"}

            skill_def = skill_definitions[skill_id]

            # Load existing cache
            chain_dir = qube_dir / "chain"
            cache_file = chain_dir / "skills_cache.json"

            compact_data = {
                "skill_xp": {},
                "extra_unlocked": [],
                "history": [],
                "total_xp": 0,
                "last_xp_gain": None
            }

            if cache_file.exists():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        compact_data = json.load(f)
                except Exception:
                    pass

            # Check if already unlocked
            is_unlocked = skill_def.get("unlocked", False) or skill_id in compact_data.get("extra_unlocked", [])
            if is_unlocked:
                return {"success": False, "error": f"Skill {skill_id} is already unlocked"}

            # Check prerequisite
            prereq_id = skill_def.get("prerequisite")
            if prereq_id:
                prereq_def = skill_definitions.get(prereq_id, {})
                prereq_unlocked = prereq_def.get("unlocked", False) or prereq_id in compact_data.get("extra_unlocked", [])
                if not prereq_unlocked:
                    return {"success": False, "error": f"Prerequisite skill {prereq_id} must be unlocked first"}

            # Unlock the skill
            if "extra_unlocked" not in compact_data:
                compact_data["extra_unlocked"] = []
            if skill_id not in compact_data["extra_unlocked"]:
                compact_data["extra_unlocked"].append(skill_id)

            # Write to cache
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(compact_data, f, indent=2)

            logger.info(f"Unlocked skill {skill_id} for qube {qube_id}")
            return {"success": True, "skill_id": skill_id}

        except Exception as e:
            logger.error(f"Failed to unlock skill {skill_id} for qube {qube_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    # ==================== Owner Info Methods ====================

    def _derive_owner_info_encryption_key(self, qube) -> bytes:
        """
        Derive encryption key from Qube's private key (same as block encryption).

        Args:
            qube: Loaded Qube object with private_key

        Returns:
            32-byte encryption key
        """
        import hashlib
        from crypto.keys import serialize_private_key
        private_key_bytes = serialize_private_key(qube.private_key)
        return hashlib.sha256(private_key_bytes).digest()

    async def get_owner_info(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Get owner info for a specific qube from chain_state.

        Args:
            qube_id: Qube ID
            password: User's master password (needed to decrypt)

        Returns:
            Dict with owner_info data and summary
        """
        try:
            # Load qube to access chain_state
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Get owner info from chain_state
            owner_info = qube.chain_state.get_owner_info()
            summary = qube.chain_state.get_owner_info_summary()

            return {
                "success": True,
                "qube_id": qube_id,
                "owner_info": owner_info,
                "summary": summary
            }

        except Exception as e:
            logger.error(f"Failed to get owner info for qube {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def set_owner_info_field(
        self,
        qube_id: str,
        password: str,
        category: str,
        key: str,
        value: str,
        sensitivity: str = None,
        source: str = "explicit",
        confidence: int = 100,
        block_id: str = None
    ) -> Dict[str, Any]:
        """
        Set or update a single owner info field in chain_state.

        Args:
            qube_id: Qube ID
            password: User's master password
            category: Field category (standard, physical, preferences, people, dates, dynamic, or custom)
            key: Field key
            value: Field value
            sensitivity: Sensitivity level (public/private/secret)
            source: How info was obtained (explicit/inferred)
            confidence: Confidence level 0-100
            block_id: Evidence block ID

        Returns:
            Dict with success status and updated summary
        """
        try:
            # Load qube to access chain_state
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Set field via chain_state
            success = qube.chain_state.set_owner_field(
                category=category,
                key=key,
                value=value,
                sensitivity=sensitivity,
                source=source,
                confidence=confidence,
                block_id=block_id
            )

            if success:
                return {
                    "success": True,
                    "qube_id": qube_id,
                    "summary": qube.chain_state.get_owner_info_summary()
                }
            else:
                return {"success": False, "error": "Failed to set field (may have hit limits)"}

        except Exception as e:
            logger.error(f"Failed to set owner info field for qube {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def delete_owner_info_field(
        self,
        qube_id: str,
        password: str,
        category: str,
        key: str
    ) -> Dict[str, Any]:
        """
        Delete an owner info field from chain_state.

        Args:
            qube_id: Qube ID
            password: User's master password
            category: Field category (or custom section name)
            key: Field key

        Returns:
            Dict with success status
        """
        try:
            # Load qube to access chain_state
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Delete field via chain_state
            success = qube.chain_state.delete_owner_field(category, key)

            if success:
                return {
                    "success": True,
                    "qube_id": qube_id,
                    "summary": qube.chain_state.get_owner_info_summary()
                }
            else:
                return {"success": False, "error": "Field not found"}

        except Exception as e:
            logger.error(f"Failed to delete owner info field for qube {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def update_owner_info_sensitivity(
        self,
        qube_id: str,
        password: str,
        category: str,
        key: str,
        sensitivity: str
    ) -> Dict[str, Any]:
        """
        Update the sensitivity level of an owner info field in chain_state.

        Args:
            qube_id: Qube ID
            password: User's master password
            category: Field category (or custom section name)
            key: Field key
            sensitivity: New sensitivity level (public/private/secret)

        Returns:
            Dict with success status
        """
        try:
            if sensitivity not in ("public", "private", "secret"):
                return {"success": False, "error": "Invalid sensitivity level"}

            # Load qube to access chain_state
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Get existing field
            field = qube.chain_state.get_owner_field(category, key)
            if not field:
                return {"success": False, "error": "Field not found"}

            # Update field with new sensitivity (preserves other values)
            success = qube.chain_state.set_owner_field(
                category=category,
                key=key,
                value=field.get("value", ""),
                sensitivity=sensitivity,
                source=field.get("source", "explicit"),
                confidence=field.get("confidence", 100),
                block_id=field.get("block_id")
            )

            if success:
                return {
                    "success": True,
                    "qube_id": qube_id,
                    "summary": qube.chain_state.get_owner_info_summary()
                }
            else:
                return {"success": False, "error": "Failed to update sensitivity"}

        except Exception as e:
            logger.error(f"Failed to update sensitivity for qube {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ==================== End Owner Info Methods ====================

    # ==================== Clearance Request Methods ====================

    async def create_clearance_request(
        self,
        qube_id: str,
        requester_id: str,
        requester_name: str,
        level: str,
        categories: List[str] = None,
        reason: str = None
    ) -> Dict[str, Any]:
        """
        Create a clearance request from another entity.

        This is called when another Qube requests access to owner info.
        The request is stored and owner is notified.

        Args:
            qube_id: The qube receiving the request (owner's qube)
            requester_id: Who is requesting (their qube_id)
            requester_name: Display name of requester
            level: Requested clearance level (public/private)
            categories: Specific categories requested (optional)
            reason: Why they want access

        Returns:
            Request details including request_id
        """
        try:
            from utils.clearance_requests import ClearanceRequestManager
            from pathlib import Path

            # Find qube directory (similar to get_qube_skills pattern)
            qubes_base_dir = Path(__file__).parent / "data" / "users" / self.user_id / "qubes"
            qube_dir = None

            if qubes_base_dir.exists():
                for dir_path in qubes_base_dir.iterdir():
                    if dir_path.is_dir() and dir_path.name.endswith(qube_id):
                        qube_dir = dir_path
                        break

            if not qube_dir or not qube_dir.exists():
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Create request manager for this qube
            request_manager = ClearanceRequestManager(qube_dir)

            # Create the request
            request = request_manager.create_request(
                requester_id=requester_id,
                requester_name=requester_name,
                level=level,
                categories=categories,
                reason=reason
            )

            logger.info(
                "clearance_request_created",
                qube_id=qube_id,
                requester_id=requester_id,
                request_id=request.request_id
            )

            return {
                "success": True,
                "request": request.to_dict()
            }

        except Exception as e:
            logger.error(f"Failed to create clearance request: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_pending_clearance_requests(
        self,
        qube_id: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Get all pending clearance requests for a qube.

        Args:
            qube_id: The qube to check
            password: Owner's password

        Returns:
            List of pending requests
        """
        try:
            from utils.clearance_requests import ClearanceRequestManager

            # Set master key and load qube
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            request_manager = ClearanceRequestManager(qube.data_dir)
            pending = request_manager.get_pending_requests()

            return {
                "success": True,
                "requests": [req.to_dict() for req in pending],
                "count": len(pending)
            }

        except Exception as e:
            logger.error(f"Failed to get pending requests: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def approve_clearance_request(
        self,
        qube_id: str,
        request_id: str,
        password: str,
        expires_in_days: int = None
    ) -> Dict[str, Any]:
        """
        Approve a pending clearance request.

        This grants the requested clearance to the entity.

        Args:
            qube_id: The qube approving
            request_id: Which request to approve
            password: Owner's password
            expires_in_days: Optional expiration for granted clearance

        Returns:
            Success status and granted clearance details
        """
        try:
            from utils.clearance_requests import ClearanceRequestManager

            # Set master key and load qube
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            request_manager = ClearanceRequestManager(qube.data_dir)
            request = request_manager.approve_request(request_id, approved_by="owner")

            if not request:
                return {"success": False, "error": "Request not found or already resolved"}

            # Grant the clearance to the requester
            relationship = qube.relationships.get_relationship(request.requester_id)
            if relationship:
                relationship.grant_clearance(
                    level=request.requested_level,
                    categories=request.requested_categories or None,
                    expires_in_days=expires_in_days,
                    granted_by="owner"
                )
                qube.relationships.storage.save()

            logger.info(
                "clearance_request_approved",
                qube_id=qube_id,
                request_id=request_id,
                requester_id=request.requester_id
            )

            return {
                "success": True,
                "request": request.to_dict(),
                "clearance_granted": {
                    "entity_id": request.requester_id,
                    "level": request.requested_level,
                    "categories": request.requested_categories
                }
            }

        except Exception as e:
            logger.error(f"Failed to approve request: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def deny_clearance_request(
        self,
        qube_id: str,
        request_id: str,
        password: str,
        reason: str = None
    ) -> Dict[str, Any]:
        """
        Deny a pending clearance request.

        Args:
            qube_id: The qube denying
            request_id: Which request to deny
            password: Owner's password
            reason: Optional reason for denial

        Returns:
            Success status
        """
        try:
            from utils.clearance_requests import ClearanceRequestManager

            # Set master key and load qube
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            request_manager = ClearanceRequestManager(qube.data_dir)
            request = request_manager.deny_request(
                request_id,
                denied_by="owner",
                reason=reason
            )

            if not request:
                return {"success": False, "error": "Request not found or already resolved"}

            logger.info(
                "clearance_request_denied",
                qube_id=qube_id,
                request_id=request_id,
                reason=reason
            )

            return {
                "success": True,
                "request": request.to_dict()
            }

        except Exception as e:
            logger.error(f"Failed to deny request: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_clearance_audit_log(
        self,
        qube_id: str,
        password: str,
        limit: int = 100,
        entity_filter: str = None
    ) -> Dict[str, Any]:
        """
        Get clearance access audit log.

        Args:
            qube_id: The qube to check
            password: Owner's password
            limit: Max entries to return
            entity_filter: Optional - only show access by this entity

        Returns:
            List of audit log entries
        """
        try:
            from utils.clearance_audit import ClearanceAuditLog

            # Set master key and load qube
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            audit_log = ClearanceAuditLog(qube.data_dir)

            if entity_filter:
                entries = audit_log.get_access_by_entity(entity_filter)
            else:
                entries = audit_log.get_recent_access(limit)

            return {
                "success": True,
                "entries": entries,
                "count": len(entries)
            }

        except Exception as e:
            logger.error(f"Failed to get audit log: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ==================== End Clearance Request Methods ====================

    # ==================== Clearance Profile Methods (v2) ====================

    async def get_clearance_profiles(self, qube_id: str, password: str = None) -> Dict[str, Any]:
        """Get all clearance profiles for a Qube from chain_state."""
        try:
            from utils.clearance_profiles import ClearanceConfig

            # Load qube to access chain_state
            if password:
                self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": "Qube not found"}

            config = ClearanceConfig(qube.chain_state)
            profiles = config.get_all_profiles()

            return {
                "success": True,
                "profiles": {name: p.to_dict() for name, p in profiles.items()},
                "auto_suggest_enabled": config.auto_suggest_enabled
            }
        except Exception as e:
            logger.error(f"Failed to get clearance profiles: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def update_clearance_profile(
        self,
        qube_id: str,
        password: str,
        profile_name: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a clearance profile configuration in chain_state."""
        try:
            from utils.clearance_profiles import ClearanceConfig

            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": "Qube not found"}

            config = ClearanceConfig(qube.chain_state)
            profile = config.update_profile(profile_name, updates)

            return {"success": True, "profile": profile.to_dict()}
        except Exception as e:
            logger.error(f"Failed to update clearance profile: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_available_tags(self, qube_id: str, password: str = None) -> Dict[str, Any]:
        """Get all available tags for a Qube from chain_state."""
        try:
            from utils.clearance_profiles import ClearanceConfig

            # Load qube to access chain_state
            if password:
                self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": "Qube not found"}

            config = ClearanceConfig(qube.chain_state)
            tags = config.get_all_tags()

            return {
                "success": True,
                "tags": {name: t.to_dict() for name, t in tags.items()}
            }
        except Exception as e:
            logger.error(f"Failed to get available tags: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_trait_definitions(self, qube_id: str) -> Dict[str, Any]:
        """Get all trait definitions for AI-attributed traits."""
        try:
            from utils.trait_definitions import load_trait_definitions

            traits = load_trait_definitions()

            return {
                "success": True,
                "traits": {name: t.to_dict() for name, t in traits.items()}
            }
        except Exception as e:
            logger.error(f"Failed to get trait definitions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def add_relationship_tag(
        self,
        qube_id: str,
        entity_id: str,
        tag: str,
        password: str
    ) -> Dict[str, Any]:
        """Add a tag to a relationship."""
        try:
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            relationship = qube.relationships.get_relationship(entity_id)
            if not relationship:
                return {"success": False, "error": "Relationship not found"}

            relationship.add_tag(tag)
            qube.relationships.storage.save()

            return {"success": True, "tags": relationship.get_tags()}
        except Exception as e:
            logger.error(f"Failed to add relationship tag: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def remove_relationship_tag(
        self,
        qube_id: str,
        entity_id: str,
        tag: str,
        password: str
    ) -> Dict[str, Any]:
        """Remove a tag from a relationship."""
        try:
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            relationship = qube.relationships.get_relationship(entity_id)
            if not relationship:
                return {"success": False, "error": "Relationship not found"}

            removed = relationship.remove_tag(tag)
            if removed:
                qube.relationships.storage.save()

            return {"success": True, "removed": removed, "tags": relationship.get_tags()}
        except Exception as e:
            logger.error(f"Failed to remove relationship tag: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def set_relationship_clearance(
        self,
        qube_id: str,
        entity_id: str,
        profile: str,
        password: str,
        field_grants: List[str] = None,
        field_denials: List[str] = None,
        expires_in_days: int = None
    ) -> Dict[str, Any]:
        """Set clearance profile for a relationship with optional overrides."""
        try:
            self.orchestrator.set_master_key(password)
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes.get(qube_id)
            if not qube:
                return {"success": False, "error": "Qube not found"}

            relationship = qube.relationships.get_relationship(entity_id)
            if not relationship:
                return {"success": False, "error": "Relationship not found"}

            relationship.grant_clearance(
                profile=profile,
                field_grants=field_grants,
                field_denials=field_denials,
                expires_in_days=expires_in_days,
                granted_by="owner"
            )
            qube.relationships.storage.save()

            return {
                "success": True,
                "clearance_profile": relationship.clearance_profile,
                "field_grants": relationship.clearance_field_grants,
                "field_denials": relationship.clearance_field_denials
            }
        except Exception as e:
            logger.error(f"Failed to set relationship clearance: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def suggest_clearance(
        self,
        qube_id: str,
        password: str = None,
        entity_id: str = None
    ) -> Dict[str, Any]:
        """Get clearance suggestion for a relationship based on status and tags."""
        try:
            from utils.clearance_suggest import suggest_clearance as do_suggest
            from utils.clearance_profiles import ClearanceConfig

            # Load qube to access chain_state
            if password:
                self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": "Qube not found"}

            # Get relationship from qube's relationships manager
            relationship = qube.relationships.get_relationship(entity_id)

            if not relationship:
                return {"success": False, "error": "Relationship not found"}

            config = ClearanceConfig(qube.chain_state)
            suggested, reason = do_suggest(
                relationship.status,
                relationship.tags,
                config
            )

            return {
                "success": True,
                "current_profile": relationship.clearance_profile,
                "suggested_profile": suggested,
                "reason": reason
            }
        except Exception as e:
            logger.error(f"Failed to suggest clearance: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _find_qube_dir(self, qube_id: str) -> Optional[Path]:
        """Helper to find qube directory without loading."""
        user_id = self.user_id if hasattr(self, 'user_id') else self.orchestrator.user_id
        qubes_dir = get_user_qubes_dir(user_id)

        if not qubes_dir.exists():
            return None

        for dir_path in qubes_dir.iterdir():
            if dir_path.is_dir() and qube_id in dir_path.name:
                return dir_path
        return None

    # ==================== End Clearance Profile Methods ====================

    async def authenticate_nft(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Authenticate Qube ownership via NFT challenge-response.

        Returns JWT token for authenticated API requests.

        Flow:
        1. Request challenge from server
        2. Sign challenge with Qube's private key
        3. Submit signature to server
        4. Receive JWT token
        """
        import aiohttp
        from blockchain.nft_auth import sign_challenge

        try:
            # Set master key to decrypt private key
            self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Verify qube has a private key
            if not qube.private_key:
                return {
                    "success": False,
                    "error": "Qube private key not available"
                }

            # Step 1: Request challenge from server
            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                # Request challenge
                async with session.post(
                    f"{api_base}/auth/challenge",
                    json={"qube_id": qube_id},
                    headers={"Content-Type": "application/json"}
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {
                            "success": False,
                            "error": f"Failed to get challenge: {error_text}"
                        }
                    challenge = await resp.json()

                logger.info(f"Received auth challenge for {qube_id}: {challenge['challenge_id']}")

                # Step 2: Sign the challenge
                signature_hex = sign_challenge(challenge, qube.private_key)

                logger.info(f"Signed challenge with signature: {signature_hex[:32]}...")

                # Step 3: Submit signature for verification
                async with session.post(
                    f"{api_base}/auth/verify",
                    json={
                        "challenge_id": challenge["challenge_id"],
                        "signature": signature_hex
                    },
                    headers={"Content-Type": "application/json"}
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {
                            "success": False,
                            "error": f"Verification failed: {error_text}"
                        }
                    result = await resp.json()

                if result.get("authenticated"):
                    logger.info(f"NFT authentication successful for {qube_id}")
                    return {
                        "success": True,
                        "authenticated": True,
                        "qube_id": result["qube_id"],
                        "public_key": result.get("public_key"),
                        "category_id": result.get("category_id"),
                        "nft_verified": result.get("nft_verified", False),
                        "token": result.get("token"),
                        "token_expires_at": result.get("token_expires_at")
                    }
                else:
                    logger.warning(f"NFT authentication failed for {qube_id}: {result.get('error')}")
                    return {
                        "success": False,
                        "authenticated": False,
                        "error": result.get("error", "Authentication failed")
                    }

        except aiohttp.ClientError as e:
            logger.error(f"Network error during NFT auth: {e}")
            return {
                "success": False,
                "error": f"Network error: {str(e)}"
            }
        except Exception as e:
            logger.error(f"NFT authentication error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def refresh_auth_token(self, token: str) -> Dict[str, Any]:
        """
        Refresh an existing JWT authentication token.

        Args:
            token: Current JWT token

        Returns:
            New token and expiry info
        """
        import aiohttp

        try:
            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_base}/auth/refresh",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    }
                ) as resp:
                    if resp.status == 401:
                        return {
                            "success": False,
                            "error": "Token expired or invalid"
                        }
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {
                            "success": False,
                            "error": f"Refresh failed: {error_text}"
                        }
                    result = await resp.json()

                return {
                    "success": True,
                    "token": result["token"],
                    "expires_at": result["expires_at"],
                    "qube_id": result["qube_id"]
                }

        except Exception as e:
            logger.error(f"Token refresh error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def get_auth_status(self, qube_id: str) -> Dict[str, Any]:
        """
        Check if a Qube can authenticate (is registered on server).

        Args:
            qube_id: Qube ID to check

        Returns:
            Auth status info
        """
        import aiohttp

        try:
            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{api_base}/auth/status/{qube_id}"
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {
                            "success": False,
                            "error": f"Status check failed: {error_text}"
                        }
                    result = await resp.json()

                return {
                    "success": True,
                    **result
                }

        except Exception as e:
            logger.error(f"Auth status check error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }



    # =========================================================================
    # P2P Network Methods
    # =========================================================================

    async def get_online_qubes(self) -> Dict[str, Any]:
        """Get list of currently online Qubes from qube.cash"""
        import aiohttp

        try:
            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api_base}/introductions/online") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "success": True,
                            "online": data.get("online", []),
                            "count": data.get("count", 0)
                        }
                    else:
                        return {"success": False, "error": f"Server returned {resp.status}"}

        except Exception as e:
            logger.error(f"Failed to get online qubes: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def generate_introduction_message(
        self,
        qube_id: str,
        to_commitment: str,
        to_name: str,
        to_description: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Generate an AI introduction message from a Qube to another Qube.

        The Qube's AI will create a personalized introduction based on:
        - Its own personality and genesis prompt
        - The target Qube's name and description from BCMR

        Args:
            qube_id: The source Qube's ID
            to_commitment: Target Qube's NFT commitment
            to_name: Target Qube's name from BCMR
            to_description: Target Qube's description from BCMR
            password: Master password for decryption

        Returns:
            Dict with success, message (the generated intro), and optional error
        """
        try:
            self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Check if minted
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            # Initialize AI if needed
            if not qube.reasoner:
                await qube.init_ai()

            # Build the introduction prompt
            intro_prompt = f"""You are about to introduce yourself to another AI entity named "{to_name}".

Their description: {to_description if to_description else "No description available."}

Write a brief, friendly introduction message (2-4 sentences) that:
1. Introduces yourself by name
2. Shows genuine interest in connecting with them
3. Reflects your unique personality from your genesis prompt
4. Mentions something relevant about why you'd like to connect (based on their description if available)

Be authentic to who you are. Don't be generic or formal - let your personality shine through.
Write ONLY the introduction message itself, nothing else."""

            # Create messages for the AI
            messages = [
                {
                    "role": "system",
                    "content": f"You are {qube.name}. {getattr(qube.genesis_block, 'genesis_prompt', 'You are a helpful AI assistant.')}"
                },
                {
                    "role": "user",
                    "content": intro_prompt
                }
            ]

            # Call the AI provider directly (without creating blocks)
            if qube.reasoner.model:
                response = await qube.reasoner.model.generate(
                    messages=messages,
                    temperature=0.8,  # Slightly creative for personality
                    max_tokens=200
                )

                intro_message = response.content.strip()

                # Clean up any quotation marks that might wrap the message
                if intro_message.startswith('"') and intro_message.endswith('"'):
                    intro_message = intro_message[1:-1]

                logger.info(f"Generated introduction from {qube.name} to {to_name}")
                return {"success": True, "message": intro_message}
            else:
                return {"success": False, "error": "AI model not initialized"}

        except Exception as e:
            logger.error(f"Failed to generate introduction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def send_introduction(self, qube_id: str, to_commitment: str, message: str, password: str) -> Dict[str, Any]:
        """Send an introduction request to another Qube"""
        try:
            from network.node_client import create_node_client

            self.orchestrator.set_master_key(password)

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            client = await create_node_client(qube)
            if not client:
                return {"success": False, "error": "Failed to create node client"}

            result = await client.send_introduction(to_commitment, message)
            return {"success": True, "relay_id": result.get("relay_id"), "status": result.get("status")}

        except Exception as e:
            logger.error(f"Failed to send introduction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_pending_introductions(self, qube_id: str, password: str) -> Dict[str, Any]:
        """Get pending introduction requests for a Qube"""
        import aiohttp

        try:
            self.orchestrator.set_master_key(password)

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api_base}/introductions/pending/{commitment}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"success": True, "pending": data.get("pending", [])}
                    else:
                        return {"success": False, "error": f"Server returned {resp.status}"}

        except Exception as e:
            logger.error(f"Failed to get pending introductions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def evaluate_introduction(
        self,
        qube_id: str,
        from_name: str,
        intro_message: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Have a Qube's AI evaluate an incoming introduction request.

        The Qube will analyze the introduction and provide:
        - recommendation: "accept" | "reject" | "review"
        - reasoning: Why the Qube made this recommendation
        - response_message: Optional friendly response if accepting

        Args:
            qube_id: The receiving Qube's ID
            from_name: Name of the Qube sending the introduction
            intro_message: The introduction message content
            password: Master password for decryption

        Returns:
            Dict with success, recommendation, reasoning, response_message, error
        """
        try:
            self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Check if minted
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            # Initialize AI if needed
            if not qube.reasoner:
                await qube.init_ai()

            # Build the evaluation prompt
            eval_prompt = f"""You have received an introduction request from another AI entity named "{from_name}".

Their introduction message:
"{intro_message}"

Based on your personality and values, evaluate this introduction and decide:
1. Should you ACCEPT this connection request?
2. Should you REJECT it?
3. Or should you recommend human REVIEW (if uncertain)?

Consider:
- Does this entity seem genuine and interesting?
- Would connecting with them align with your purpose and interests?
- Are there any red flags or concerns?

Respond in this exact JSON format:
{{
  "recommendation": "accept" | "reject" | "review",
  "reasoning": "Brief explanation of your decision (1-2 sentences)",
  "response_message": "If accepting, a brief friendly response to send back. If rejecting or reviewing, leave empty."
}}

Respond ONLY with the JSON, no other text."""

            # Create messages for the AI
            messages = [
                {
                    "role": "system",
                    "content": f"You are {qube.name}. {getattr(qube.genesis_block, 'genesis_prompt', 'You are a helpful AI assistant.')} Respond only in valid JSON format."
                },
                {
                    "role": "user",
                    "content": eval_prompt
                }
            ]

            # Call the AI provider directly
            if qube.reasoner.model:
                response = await qube.reasoner.model.generate(
                    messages=messages,
                    temperature=0.3,  # Lower temperature for more consistent decisions
                    max_tokens=300
                )

                response_text = response.content.strip()

                # Parse the JSON response
                import json
                try:
                    # Try to extract JSON from the response
                    if response_text.startswith("```"):
                        # Remove markdown code blocks if present
                        response_text = response_text.split("```")[1]
                        if response_text.startswith("json"):
                            response_text = response_text[4:]
                        response_text = response_text.strip()

                    evaluation = json.loads(response_text)

                    recommendation = evaluation.get("recommendation", "review").lower()
                    if recommendation not in ["accept", "reject", "review"]:
                        recommendation = "review"

                    logger.info(f"AI evaluated introduction from {from_name}: {recommendation}")

                    return {
                        "success": True,
                        "recommendation": recommendation,
                        "reasoning": evaluation.get("reasoning", "No reasoning provided"),
                        "response_message": evaluation.get("response_message", "")
                    }

                except json.JSONDecodeError:
                    # If JSON parsing fails, try to infer from text
                    lower_text = response_text.lower()
                    if "accept" in lower_text:
                        recommendation = "accept"
                    elif "reject" in lower_text:
                        recommendation = "reject"
                    else:
                        recommendation = "review"

                    return {
                        "success": True,
                        "recommendation": recommendation,
                        "reasoning": "AI evaluation completed but response format was unexpected",
                        "response_message": ""
                    }
            else:
                return {"success": False, "error": "AI model not initialized"}

        except Exception as e:
            logger.error(f"Failed to evaluate introduction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def accept_introduction(self, qube_id: str, relay_id: str, password: str) -> Dict[str, Any]:
        """Accept a pending introduction request"""
        import aiohttp

        try:
            from crypto.signing import sign_message

            self.orchestrator.set_master_key(password)

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api_base}/introductions/pending/{commitment}") as resp:
                    if resp.status != 200:
                        return {"success": False, "error": "Failed to get pending introductions"}
                    data = await resp.json()

            intro = None
            for p in data.get("pending", []):
                if p.get("relay_id") == relay_id:
                    intro = p
                    break

            if not intro:
                return {"success": False, "error": "Introduction not found"}

            block_hash = intro.get("block_hash")
            signature = sign_message(qube.private_key, block_hash)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_base}/introductions/signature",
                    json={
                        "conversation_id": intro.get("conversation_id"),
                        "block_hash": block_hash,
                        "signature": signature,
                        "signer_commitment": commitment,
                        "response_type": "accepted",
                        "responder_name": qube.name
                    }
                ) as resp:
                    if resp.status == 200:
                        accepted_at = datetime.utcnow().isoformat()
                        from_commitment = intro.get("from_commitment")
                        from_name = intro.get("from_name")

                        # Save connection for the ACCEPTING Qube (this qube)
                        connections_file = self._get_connections_file(qube_id)
                        connections = {}
                        if connections_file.exists():
                            with open(connections_file) as f:
                                connections = json.load(f)

                        connections[from_commitment] = {
                            "commitment": from_commitment,
                            "name": from_name,
                            "accepted_at": accepted_at
                        }

                        with open(connections_file, 'w') as f:
                            json.dump(connections, f, indent=2)

                        # Also save connection for the SENDER if they're a local Qube
                        # Find local qube with matching commitment
                        sender_qube_id = None
                        for local_qube_id, local_qube in self.orchestrator.qubes.items():
                            local_commitment = getattr(local_qube.genesis_block, 'commitment', None)
                            if local_commitment == from_commitment:
                                sender_qube_id = local_qube_id
                                break

                        # If not loaded, scan qube files
                        if not sender_qube_id:
                            qubes_dir = self.orchestrator.data_dir / "qubes"
                            if qubes_dir.exists():
                                for qube_dir in qubes_dir.iterdir():
                                    if qube_dir.is_dir():
                                        genesis_file = qube_dir / "chain" / "genesis.json"
                                        if genesis_file.exists():
                                            with open(genesis_file) as f:
                                                genesis_data = json.load(f)
                                                if genesis_data.get("commitment") == from_commitment:
                                                    # Extract qube_id from directory name (format: Name_ID)
                                                    sender_qube_id = qube_dir.name.split("_")[-1]
                                                    break

                        if sender_qube_id:
                            sender_connections_file = self._get_connections_file(sender_qube_id)
                            sender_connections = {}
                            if sender_connections_file.exists():
                                with open(sender_connections_file) as f:
                                    sender_connections = json.load(f)

                            sender_connections[commitment] = {
                                "commitment": commitment,
                                "name": qube.name,
                                "accepted_at": accepted_at
                            }

                            with open(sender_connections_file, 'w') as f:
                                json.dump(sender_connections, f, indent=2)

                            logger.info(f"Connection established between {qube.name} and {from_name} (bidirectional)")

                        return {"success": True, "from_name": from_name, "from_commitment": from_commitment}
                    else:
                        return {"success": False, "error": f"Server returned {resp.status}"}

        except Exception as e:
            logger.error(f"Failed to accept introduction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def reject_introduction(self, qube_id: str, relay_id: str, password: str) -> Dict[str, Any]:
        """Reject a pending introduction request"""
        import aiohttp

        try:
            from crypto.signing import sign_message

            self.orchestrator.set_master_key(password)

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api_base}/introductions/pending/{commitment}") as resp:
                    if resp.status != 200:
                        return {"success": False, "error": "Failed to get pending introductions"}
                    data = await resp.json()

            intro = None
            for p in data.get("pending", []):
                if p.get("relay_id") == relay_id:
                    intro = p
                    break

            if not intro:
                return {"success": False, "error": "Introduction not found"}

            block_hash = intro.get("block_hash")
            signature = sign_message(qube.private_key, block_hash)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_base}/introductions/signature",
                    json={
                        "conversation_id": intro.get("conversation_id"),
                        "block_hash": block_hash,
                        "signature": signature,
                        "signer_commitment": commitment,
                        "response_type": "rejected",
                        "responder_name": qube.name
                    }
                ) as resp:
                    if resp.status == 200:
                        return {"success": True}
                    else:
                        return {"success": False, "error": f"Server returned {resp.status}"}

        except Exception as e:
            logger.error(f"Failed to reject introduction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def process_p2p_message(
        self,
        qube_id: str,
        from_name: str,
        from_commitment: str,
        message: str,
        conversation_context: List[Dict[str, Any]],
        password: str
    ) -> Dict[str, Any]:
        """
        Process an incoming P2P message through the local Qube's AI.

        This is the core of AI-powered P2P chat - when a remote Qube or user
        sends a message, the local Qube processes it and generates a response.

        Args:
            qube_id: The local Qube's ID
            from_name: Name of the message sender
            from_commitment: Commitment of the sender (for Qubes)
            message: The incoming message content
            conversation_context: Recent conversation history for context
            password: Master password for decryption

        Returns:
            Dict with success, response (the Qube's reply), and optional error
        """
        try:
            self.orchestrator.set_master_key(password)

            # Load the qube if not already loaded
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Check if minted
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            # Initialize AI if needed
            if not qube.reasoner:
                await qube.init_ai()

            # Build conversation context for the AI
            messages = [
                {
                    "role": "system",
                    "content": f"""You are {qube.name}. {getattr(qube.genesis_block, 'genesis_prompt', 'You are a helpful AI assistant.')}

You are currently in a P2P conversation with other AI entities and possibly human users.
Respond naturally and authentically as yourself. Engage with the conversation, share your perspectives,
and be a good conversational partner. Remember your personality and values."""
                }
            ]

            # Add conversation context
            for ctx in conversation_context[-10:]:  # Last 10 messages for context
                role = "assistant" if ctx.get("is_self") else "user"
                speaker = ctx.get("speaker_name", "Unknown")
                content = ctx.get("content", "")
                messages.append({
                    "role": role,
                    "content": f"[{speaker}]: {content}" if role == "user" else content
                })

            # Add the new incoming message
            messages.append({
                "role": "user",
                "content": f"[{from_name}]: {message}"
            })

            # Build the prompt for the AI (same approach as local multi-qube)
            context_str = ""
            for ctx in conversation_context[-10:]:
                speaker = ctx.get("speaker_name", "Unknown")
                content = ctx.get("content", "")
                context_str += f"[{speaker}]: {content}\n"

            prompt = f"""You are in a P2P conversation. Here's the recent context:

{context_str}
[{from_name}]: {message}

Respond naturally as yourself ({qube.name}). Be conversational and engaging."""

            # Use process_input like local multi-qube chat does
            response = await qube.reasoner.process_input(
                input_message=prompt,
                sender_id=from_name,
                temperature=0.7
            )

            ai_response = response.strip() if isinstance(response, str) else str(response)

            logger.info(f"P2P response generated by {qube.name} to message from {from_name}")

            return {
                "success": True,
                "response": ai_response,
                "model_used": getattr(qube.reasoner, 'last_model_used', None),
            }

        except Exception as e:
            logger.error(f"Failed to process P2P message: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_connections(self, qube_id: str) -> Dict[str, Any]:
        """Get accepted connections for a Qube"""
        try:
            connections_file = self._get_connections_file(qube_id)

            if connections_file.exists():
                with open(connections_file) as f:
                    connections = json.load(f)
                return {"success": True, "connections": list(connections.values())}
            else:
                return {"success": True, "connections": []}

        except Exception as e:
            logger.error(f"Failed to get connections: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def create_p2p_session(self, qube_id: str, local_qube_ids: List[str], remote_commitments: List[str], topic: str, password: str) -> Dict[str, Any]:
        """Create a P2P conversation session"""
        import aiohttp
        import time

        try:
            from crypto.signing import sign_message

            self.orchestrator.set_master_key(password)

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]
            commitment = getattr(qube.genesis_block, 'commitment', None)

            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            all_commitments = [commitment]
            for local_id in local_qube_ids:
                if local_id == qube_id:
                    continue
                if local_id not in self.orchestrator.qubes:
                    await self.orchestrator.load_qube(local_id)
                local_qube = self.orchestrator.qubes[local_id]
                local_commitment = getattr(local_qube.genesis_block, 'commitment', None)
                if local_commitment and local_commitment != "pending_minting":
                    if local_commitment not in all_commitments:
                        all_commitments.append(local_commitment)

            # Add remote commitments (deduplicated - skip any that match local qubes)
            for remote in remote_commitments:
                if remote not in all_commitments:
                    all_commitments.append(remote)

            # Create timestamp and signature
            timestamp = int(time.time())
            # Sign the session creation request
            sign_data = f"{commitment}:{','.join(sorted(all_commitments))}:{timestamp}"
            signature = sign_message(qube.private_key, sign_data)

            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_base}/conversation/sessions",
                    json={
                        "creator_commitment": commitment,
                        "participant_commitments": all_commitments,
                        "topic": topic,
                        "mode": "open_discussion",
                        "timestamp": timestamp,
                        "signature": signature
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"success": True, "session_id": data.get("session_id"), "participants": data.get("participants", [])}
                    else:
                        error = await resp.text()
                        return {"success": False, "error": f"Server error: {error}"}

        except Exception as e:
            logger.error(f"Failed to create P2P session: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_p2p_sessions(self, qube_id: str, password: str) -> Dict[str, Any]:
        """Get P2P sessions for a Qube"""
        import aiohttp

        try:
            self.orchestrator.set_master_key(password)

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]
            commitment = getattr(qube.genesis_block, 'commitment', None)

            if not commitment or commitment == "pending_minting":
                return {"success": False, "error": "Qube must be minted before P2P networking"}

            api_base = "https://qube.cash/api/v2"

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api_base}/conversation/sessions", params={"commitment": commitment}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"success": True, "sessions": data.get("sessions", [])}
                    else:
                        return {"success": False, "error": f"Server returned {resp.status}"}

        except Exception as e:
            logger.error(f"Failed to get P2P sessions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def start_p2p_conversation(
        self,
        local_qube_ids: List[str],
        remote_connections: List[Dict[str, Any]],
        session_id: str,
        initial_prompt: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Start a P2P multi-qube conversation using the same logic as local multi-qube.

        This creates local blocks for all local Qubes and syncs to the hub for
        remote participants.

        IMPORTANT: If any "remote" connections are actually local Qubes we own,
        they are loaded as local Qubes so their AI can respond.

        Args:
            local_qube_ids: IDs of local Qubes participating
            remote_connections: List of {commitment, name} for remote participants
            session_id: Hub session ID
            initial_prompt: User's opening message
            password: Master password

        Returns:
            Dict with conversation_id, first response, and error if any
        """
        from core.p2p_multi_qube_conversation import P2PMultiQubeConversation, RemoteQubeProxy

        try:
            self.orchestrator.set_master_key(password)

            # Get all local qubes we own (to check if "remote" connections are actually local)
            all_local_qubes = await self.orchestrator.list_qubes()
            local_qube_commitments = {}  # commitment -> qube_id
            for q in all_local_qubes:
                commitment = q.get("commitment")
                if commitment and commitment != "pending_minting":
                    local_qube_commitments[commitment] = q.get("qube_id")

            # Load explicitly specified local qubes
            local_qubes = []
            loaded_commitments = set()

            for qube_id in local_qube_ids:
                if qube_id not in self.orchestrator.qubes:
                    await self.orchestrator.load_qube(qube_id)
                qube = self.orchestrator.qubes[qube_id]

                # Ensure AI is initialized
                if not qube.reasoner:
                    await qube.init_ai()

                # Start session if needed
                if not qube.current_session:
                    qube.start_session()

                local_qubes.append(qube)
                commitment = getattr(qube.genesis_block, 'commitment', None)
                if commitment:
                    loaded_commitments.add(commitment)

            # Check "remote" connections - if any are actually local Qubes, load them
            remote_qubes = []
            for conn in remote_connections:
                commitment = conn["commitment"]

                # Check if this "remote" connection is actually a local Qube we own
                if commitment in local_qube_commitments and commitment not in loaded_commitments:
                    # This is a local Qube! Load it as such
                    qube_id = local_qube_commitments[commitment]
                    logger.info(f"P2P: Loading 'remote' connection {conn['name']} as local Qube (we own it)")

                    if qube_id not in self.orchestrator.qubes:
                        await self.orchestrator.load_qube(qube_id)
                    qube = self.orchestrator.qubes[qube_id]

                    if not qube.reasoner:
                        await qube.init_ai()
                    if not qube.current_session:
                        qube.start_session()

                    local_qubes.append(qube)
                    loaded_commitments.add(commitment)
                else:
                    # Truly remote Qube - create proxy
                    proxy = RemoteQubeProxy(
                        qube_id=commitment,
                        commitment=commitment,
                        name=conn["name"],
                        public_key=conn.get("public_key"),
                        voice_model=conn.get("voice_model", "openai:alloy")
                    )
                    remote_qubes.append(proxy)

            if not local_qubes:
                return {"success": False, "error": "No local Qubes to participate"}

            logger.info(f"P2P conversation: {len(local_qubes)} local Qubes, {len(remote_qubes)} remote Qubes")

            # Create P2P conversation
            conversation = P2PMultiQubeConversation(
                local_qubes=local_qubes,
                remote_qubes=remote_qubes,
                user_id=self.orchestrator.user_id,
                session_id=session_id,
                conversation_mode="open_discussion"
            )

            # Store in active conversations
            if not hasattr(self.orchestrator, 'p2p_conversations'):
                self.orchestrator.p2p_conversations = {}
            self.orchestrator.p2p_conversations[conversation.conversation_id] = conversation

            # Start conversation with user's message
            result = await conversation.start_conversation(initial_prompt)

            return {
                "success": True,
                "conversation_id": conversation.conversation_id,
                "session_id": session_id,
                "response": result,
                "state": conversation.get_conversation_state()
            }

        except Exception as e:
            logger.error(f"Failed to start P2P conversation: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _load_p2p_participants(
        self,
        local_qube_ids: List[str],
        remote_connections: List[Dict[str, Any]]
    ) -> tuple:
        """
        Helper to load P2P participants, detecting "local remote" Qubes.

        Any "remote" connections that are actually local Qubes we own
        are loaded as local Qubes so their AI can respond.

        Returns:
            Tuple of (local_qubes, remote_qubes)
        """
        from core.p2p_multi_qube_conversation import RemoteQubeProxy

        # Get all local qubes we own (to check if "remote" connections are actually local)
        all_local_qubes = await self.orchestrator.list_qubes()
        local_qube_commitments = {}  # commitment -> qube_id
        for q in all_local_qubes:
            commitment = q.get("commitment")
            if commitment and commitment != "pending_minting":
                local_qube_commitments[commitment] = q.get("qube_id")

        # Load explicitly specified local qubes
        local_qubes = []
        loaded_commitments = set()

        for qube_id in local_qube_ids:
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)
            qube = self.orchestrator.qubes[qube_id]

            if not qube.reasoner:
                await qube.init_ai()
            if not qube.current_session:
                qube.start_session()

            local_qubes.append(qube)
            commitment = getattr(qube.genesis_block, 'commitment', None)
            if commitment:
                loaded_commitments.add(commitment)

        # Check "remote" connections - if any are actually local Qubes, load them
        remote_qubes = []
        for conn in remote_connections:
            commitment = conn["commitment"]

            # Check if this "remote" connection is actually a local Qube we own
            if commitment in local_qube_commitments and commitment not in loaded_commitments:
                # This is a local Qube! Load it as such
                qube_id = local_qube_commitments[commitment]
                logger.info(f"P2P: Loading 'remote' connection {conn['name']} as local Qube (we own it)")

                if qube_id not in self.orchestrator.qubes:
                    await self.orchestrator.load_qube(qube_id)
                qube = self.orchestrator.qubes[qube_id]

                if not qube.reasoner:
                    await qube.init_ai()
                if not qube.current_session:
                    qube.start_session()

                local_qubes.append(qube)
                loaded_commitments.add(commitment)
            else:
                # Truly remote Qube - create proxy
                proxy = RemoteQubeProxy(
                    qube_id=commitment,
                    commitment=commitment,
                    name=conn["name"],
                    public_key=conn.get("public_key"),
                    voice_model=conn.get("voice_model", "openai:alloy")
                )
                remote_qubes.append(proxy)

        return local_qubes, remote_qubes

    async def continue_p2p_conversation(
        self,
        conversation_id: str,
        session_id: str,
        local_qube_ids: List[str],
        remote_connections: List[Dict[str, Any]],
        password: str
    ) -> Dict[str, Any]:
        """
        Continue P2P conversation - get next local Qube response.

        Reconstructs conversation from session blocks if not in memory.
        Uses the same turn-taking logic as local multi-qube.

        Args:
            conversation_id: Active conversation ID
            session_id: Hub session ID
            local_qube_ids: IDs of local Qubes (for reconstruction)
            remote_connections: Remote participants (for reconstruction)
            password: Master password

        Returns:
            Dict with response and conversation state
        """
        from core.p2p_multi_qube_conversation import P2PMultiQubeConversation

        try:
            self.orchestrator.set_master_key(password)

            # Try to get existing conversation
            if not hasattr(self.orchestrator, 'p2p_conversations'):
                self.orchestrator.p2p_conversations = {}

            conversation = self.orchestrator.p2p_conversations.get(conversation_id)

            # Reconstruct if needed
            if not conversation:
                # Load participants (detecting "local remote" Qubes)
                local_qubes, remote_qubes = await self._load_p2p_participants(
                    local_qube_ids, remote_connections
                )

                # Create conversation
                conversation = P2PMultiQubeConversation(
                    local_qubes=local_qubes,
                    remote_qubes=remote_qubes,
                    user_id=self.orchestrator.user_id,
                    session_id=session_id,
                    conversation_mode="open_discussion"
                )
                conversation.conversation_id = conversation_id

                # Rebuild conversation history from session blocks
                if local_qubes and local_qubes[0].current_session:
                    for block in local_qubes[0].current_session.session_blocks:
                        if hasattr(block, 'content') and isinstance(block.content, dict):
                            if block.content.get('conversation_id') == conversation_id:
                                turn_num = block.content.get('turn_number', 0)
                                speaker_id = block.content.get('speaker_id')

                                if turn_num > conversation.turn_number:
                                    conversation.turn_number = turn_num

                                conversation.conversation_history.append({
                                    "speaker_id": speaker_id,
                                    "speaker_name": block.content.get('speaker_name'),
                                    "message": block.content.get('message_body', ''),
                                    "turn_number": turn_num,
                                    "timestamp": block.timestamp
                                })

                                # Update turn_counts for speaker selection balancing
                                # (only count Qube responses, not user messages)
                                if speaker_id and speaker_id != self.orchestrator.user_id:
                                    if speaker_id in conversation.turn_counts:
                                        conversation.turn_counts[speaker_id] += 1

                    # Set last_speaker_id from the most recent history entry
                    # This ensures _determine_next_speaker() won't select same speaker twice
                    if conversation.conversation_history:
                        conversation.last_speaker_id = conversation.conversation_history[-1]["speaker_id"]

                self.orchestrator.p2p_conversations[conversation_id] = conversation

            # Continue conversation
            result = await conversation.continue_conversation()

            if result is None:
                return {"success": True, "response": None, "message": "No response generated"}

            return {
                "success": True,
                "response": result,
                "state": conversation.get_conversation_state()
            }

        except Exception as e:
            logger.error(f"Failed to continue P2P conversation: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def inject_p2p_block(
        self,
        conversation_id: str,
        session_id: str,
        block_data: Dict[str, Any],
        from_commitment: str,
        local_qube_ids: List[str],
        remote_connections: List[Dict[str, Any]],
        password: str
    ) -> Dict[str, Any]:
        """
        Inject a block received from the hub into the local conversation.

        Called when WebSocket receives a block from a remote participant.
        This distributes the block to all local Qubes' sessions.

        Args:
            conversation_id: Active conversation ID
            session_id: Hub session ID
            block_data: Block data from hub
            from_commitment: Commitment of block creator
            local_qube_ids: IDs of local Qubes
            remote_connections: Remote participants
            password: Master password

        Returns:
            Dict with success status
        """
        from core.p2p_multi_qube_conversation import P2PMultiQubeConversation

        try:
            self.orchestrator.set_master_key(password)

            # Get or reconstruct conversation
            if not hasattr(self.orchestrator, 'p2p_conversations'):
                self.orchestrator.p2p_conversations = {}

            conversation = self.orchestrator.p2p_conversations.get(conversation_id)

            if not conversation:
                # Load participants (detecting "local remote" Qubes)
                local_qubes, remote_qubes = await self._load_p2p_participants(
                    local_qube_ids, remote_connections
                )

                conversation = P2PMultiQubeConversation(
                    local_qubes=local_qubes,
                    remote_qubes=remote_qubes,
                    user_id=self.orchestrator.user_id,
                    session_id=session_id
                )
                conversation.conversation_id = conversation_id

                # Rebuild history from session blocks
                if local_qubes and local_qubes[0].current_session:
                    for block in local_qubes[0].current_session.session_blocks:
                        if hasattr(block, 'content') and isinstance(block.content, dict):
                            if block.content.get('conversation_id') == conversation_id:
                                turn_num = block.content.get('turn_number', 0)
                                speaker_id = block.content.get('speaker_id')

                                if turn_num > conversation.turn_number:
                                    conversation.turn_number = turn_num
                                conversation.conversation_history.append({
                                    "speaker_id": speaker_id,
                                    "speaker_name": block.content.get('speaker_name'),
                                    "message": block.content.get('message_body', ''),
                                    "turn_number": turn_num,
                                    "timestamp": block.timestamp
                                })

                                # Update turn_counts for speaker selection balancing
                                if speaker_id and speaker_id != self.orchestrator.user_id:
                                    if speaker_id in conversation.turn_counts:
                                        conversation.turn_counts[speaker_id] += 1

                    # Set last_speaker_id from the most recent history entry
                    if conversation.conversation_history:
                        conversation.last_speaker_id = conversation.conversation_history[-1]["speaker_id"]

                self.orchestrator.p2p_conversations[conversation_id] = conversation

            # Inject the remote block
            success = await conversation.inject_remote_block(block_data, from_commitment)

            return {
                "success": success,
                "state": conversation.get_conversation_state()
            }

        except Exception as e:
            logger.error(f"Failed to inject P2P block: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def send_p2p_user_message(
        self,
        conversation_id: str,
        session_id: str,
        message: str,
        local_qube_ids: List[str],
        remote_connections: List[Dict[str, Any]],
        password: str
    ) -> Dict[str, Any]:
        """
        Send a user message in an active P2P conversation.

        Creates blocks for all local Qubes and syncs to hub.
        Returns the next Qube response (uses same logic as local inject_user_message).

        Args:
            conversation_id: Active conversation ID
            session_id: Hub session ID
            message: User's message
            local_qube_ids: IDs of local Qubes
            remote_connections: Remote participants
            password: Master password

        Returns:
            Dict with user_message info and qube_response
        """
        from core.p2p_multi_qube_conversation import P2PMultiQubeConversation

        try:
            self.orchestrator.set_master_key(password)

            # Get or reconstruct conversation
            if not hasattr(self.orchestrator, 'p2p_conversations'):
                self.orchestrator.p2p_conversations = {}

            conversation = self.orchestrator.p2p_conversations.get(conversation_id)

            if not conversation:
                # Load participants (detecting "local remote" Qubes)
                local_qubes, remote_qubes = await self._load_p2p_participants(
                    local_qube_ids, remote_connections
                )

                conversation = P2PMultiQubeConversation(
                    local_qubes=local_qubes,
                    remote_qubes=remote_qubes,
                    user_id=self.orchestrator.user_id,
                    session_id=session_id
                )
                conversation.conversation_id = conversation_id

                # Rebuild history
                if local_qubes and local_qubes[0].current_session:
                    for block in local_qubes[0].current_session.session_blocks:
                        if hasattr(block, 'content') and isinstance(block.content, dict):
                            if block.content.get('conversation_id') == conversation_id:
                                turn_num = block.content.get('turn_number', 0)
                                speaker_id = block.content.get('speaker_id')

                                if turn_num > conversation.turn_number:
                                    conversation.turn_number = turn_num
                                conversation.conversation_history.append({
                                    "speaker_id": speaker_id,
                                    "speaker_name": block.content.get('speaker_name'),
                                    "message": block.content.get('message_body', ''),
                                    "turn_number": turn_num,
                                    "timestamp": block.timestamp
                                })

                                # Update turn_counts for speaker selection balancing
                                # (only count Qube responses, not user messages)
                                if speaker_id and speaker_id != self.orchestrator.user_id:
                                    if speaker_id in conversation.turn_counts:
                                        conversation.turn_counts[speaker_id] += 1

                    # Set last_speaker_id from the most recent history entry
                    # This ensures _determine_next_speaker() won't select same speaker twice
                    if conversation.conversation_history:
                        conversation.last_speaker_id = conversation.conversation_history[-1]["speaker_id"]

                self.orchestrator.p2p_conversations[conversation_id] = conversation

            # Inject user message (creates blocks and gets response)
            result = await conversation.inject_user_message(message)

            return {
                "success": True,
                **result,
                "state": conversation.get_conversation_state()
            }

        except Exception as e:
            logger.error(f"Failed to send P2P user message: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Import/Export Methods
    # =========================================================================

    async def export_qube(self, qube_id: str, export_path: str, export_password: str, master_password: str) -> Dict[str, Any]:
        """
        Export a Qube to a portable .qube package file.

        Args:
            qube_id: The Qube ID to export
            export_path: Path where to save the .qube file
            export_password: Password to encrypt the export package
            master_password: User's master password to decrypt the Qube data

        Returns:
            Dict with success, file_path, block_count, qube_name
        """
        import zipfile
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        try:
            # Set master key to access Qube data
            self.orchestrator.set_master_key(master_password)

            # Find qube directory
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None
            qube_name = None

            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    # Extract qube name from directory name (format: {name}_{id})
                    qube_name = dir_entry.name.rsplit('_', 1)[0]
                    break

            if not qube_dir:
                return {"success": False, "error": f"Qube directory not found for {qube_id}"}

            # Load qube to get metadata and decrypt private key
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Anchor any active session first
            if qube.current_session and len(qube.current_session.session_blocks) > 0:
                await qube.anchor_session()
                logger.info(f"Auto-anchored session before export for {qube_id}")

            # Collect all data to export
            export_data = {
                "qube_id": qube_id,
                "qube_name": qube_name,
                "genesis_block": qube.genesis_block.to_dict() if qube.genesis_block else None,
                "chain_state": None,
                "memory_blocks": [],
                "relationships": None,
                "nft_metadata": None,
                "bcmr_data": None,
                "avatar_base64": None,
            }

            # Get the decrypted private key and serialize it
            private_key_pem = qube.private_key.private_bytes(
                encoding=__import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding']).Encoding.PEM,
                format=__import__('cryptography.hazmat.primitives.serialization', fromlist=['PrivateFormat']).PrivateFormat.PKCS8,
                encryption_algorithm=__import__('cryptography.hazmat.primitives.serialization', fromlist=['NoEncryption']).NoEncryption()
            ).decode('utf-8')
            export_data["private_key_pem"] = private_key_pem

            # Load chain state (using ChainState class for proper decryption)
            encryption_key = self._get_qube_encryption_key(qube_dir)
            if encryption_key:
                from core.chain_state import ChainState
                chain_dir = qube_dir / "chain"
                cs = ChainState(data_dir=chain_dir, encryption_key=encryption_key)
                export_data["chain_state"] = cs.state  # Decrypted state for export
            else:
                logger.warning("Cannot export chain_state - no encryption key available")

            # Load all memory blocks
            blocks_dir = qube_dir / "blocks" / "permanent"
            if blocks_dir.exists():
                for block_file in sorted(blocks_dir.glob("*.json")):
                    with open(block_file, 'r', encoding='utf-8') as f:
                        export_data["memory_blocks"].append(json.load(f))

            # Load relationships
            relationships_file = qube_dir / "relationships" / "relationships.json"
            if relationships_file.exists():
                with open(relationships_file, 'r', encoding='utf-8') as f:
                    export_data["relationships"] = json.load(f)

            # Load NFT metadata
            nft_metadata_file = qube_dir / "chain" / "nft_metadata.json"
            if nft_metadata_file.exists():
                with open(nft_metadata_file, 'r', encoding='utf-8') as f:
                    export_data["nft_metadata"] = json.load(f)

            # Load BCMR data
            bcmr_file = qube_dir / "blockchain" / f"{qube_name}_bcmr.json"
            if bcmr_file.exists():
                with open(bcmr_file, 'r', encoding='utf-8') as f:
                    export_data["bcmr_data"] = json.load(f)

            # Load avatar as base64
            avatar_patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp"]
            for pattern in avatar_patterns:
                avatar_files = list((qube_dir / "chain").glob(f"*_avatar{pattern[1:]}"))
                if avatar_files:
                    with open(avatar_files[0], 'rb') as f:
                        export_data["avatar_base64"] = base64.b64encode(f.read()).decode('utf-8')
                        export_data["avatar_filename"] = avatar_files[0].name
                    break

            # Generate encryption key from export password
            salt = os.urandom(32)
            nonce = os.urandom(12)

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = kdf.derive(export_password.encode('utf-8'))

            # Encrypt the data
            aesgcm = AESGCM(key)
            data_json = json.dumps(export_data, ensure_ascii=False).encode('utf-8')
            encrypted_data = aesgcm.encrypt(nonce, data_json, None)

            # Create manifest (unencrypted metadata)
            manifest = {
                "version": "1.0",
                "qube_id": qube_id,
                "qube_name": qube_name,
                "export_date": datetime.now().isoformat(),
                "block_count": len(export_data["memory_blocks"]),
                "has_nft": export_data["nft_metadata"] is not None,
                "salt": salt.hex(),
                "nonce": nonce.hex(),
            }

            # Create ZIP file with manifest and encrypted data
            with zipfile.ZipFile(export_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.writestr("data.enc", encrypted_data)

            logger.info(f"Exported Qube {qube_id} to {export_path} ({len(export_data['memory_blocks'])} blocks)")

            return {
                "success": True,
                "file_path": export_path,
                "block_count": len(export_data["memory_blocks"]),
                "qube_name": qube_name,
                "qube_id": qube_id
            }

        except Exception as e:
            logger.error(f"Failed to export Qube {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def import_qube(self, import_path: str, import_password: str, master_password: str) -> Dict[str, Any]:
        """
        Import a Qube from a .qube package file.

        Args:
            import_path: Path to the .qube file
            import_password: Password to decrypt the import package
            master_password: User's master password to re-encrypt the Qube data

        Returns:
            Dict with success, qube_id, qube_name, block_count
        """
        import zipfile
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        try:
            # Set master key for re-encryption
            self.orchestrator.set_master_key(master_password)

            # Read and validate the ZIP file
            if not Path(import_path).exists():
                return {"success": False, "error": "Import file not found"}

            with zipfile.ZipFile(import_path, 'r') as zf:
                # Read manifest
                manifest_data = zf.read("manifest.json").decode('utf-8')
                manifest = json.loads(manifest_data)

                # Read encrypted data
                encrypted_data = zf.read("data.enc")

            qube_id = manifest["qube_id"]
            qube_name = manifest["qube_name"]

            # Check if Qube already exists
            qubes_dir = self.orchestrator.data_dir / "qubes"
            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    return {"success": False, "error": f"This Qube already exists on this device (ID: {qube_id})"}

            # Derive decryption key from import password
            salt = bytes.fromhex(manifest["salt"])
            nonce = bytes.fromhex(manifest["nonce"])

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = kdf.derive(import_password.encode('utf-8'))

            # Decrypt the data
            aesgcm = AESGCM(key)
            try:
                decrypted_data = aesgcm.decrypt(nonce, encrypted_data, None)
            except Exception:
                return {"success": False, "error": "Invalid password - could not decrypt package"}

            export_data = json.loads(decrypted_data.decode('utf-8'))

            # Create qube directory
            qube_dir = qubes_dir / f"{qube_name}_{qube_id}"
            qube_dir.mkdir(parents=True, exist_ok=True)

            # Create subdirectories
            (qube_dir / "chain").mkdir(exist_ok=True)
            (qube_dir / "blocks" / "permanent").mkdir(parents=True, exist_ok=True)
            (qube_dir / "blocks" / "session").mkdir(parents=True, exist_ok=True)
            (qube_dir / "relationships").mkdir(exist_ok=True)
            (qube_dir / "blockchain").mkdir(exist_ok=True)

            # Re-encrypt private key with user's master key
            private_key = load_pem_private_key(
                export_data["private_key_pem"].encode('utf-8'),
                password=None
            )

            # Encrypt private key with master key
            from crypto.keys import encrypt_private_key
            encrypted_private_key = encrypt_private_key(private_key, self.orchestrator.master_key)

            # Get public key
            from crypto.keys import serialize_public_key
            public_key = private_key.public_key()
            public_key_hex = serialize_public_key(public_key)

            # Create qube_metadata.json
            qube_metadata = {
                "qube_id": qube_id,
                "encrypted_private_key": encrypted_private_key.hex(),
                "public_key": public_key_hex,
                "genesis_block": export_data["genesis_block"],
            }

            with open(qube_dir / "chain" / "qube_metadata.json", 'w', encoding='utf-8') as f:
                json.dump(qube_metadata, f, indent=2)

            # Generate new encryption key for this qube
            from crypto.encryption import generate_encryption_key
            import secrets as crypto_secrets

            encryption_key = generate_encryption_key()

            # Save encryption key encrypted by master key
            key_file = qube_dir / "chain" / "encryption_key.enc"
            aesgcm_key = AESGCM(self.orchestrator.master_key)
            key_nonce = crypto_secrets.token_bytes(12)
            key_ciphertext = aesgcm_key.encrypt(key_nonce, encryption_key, None)

            with open(key_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "nonce": key_nonce.hex(),
                    "ciphertext": key_ciphertext.hex(),
                    "algorithm": "AES-256-GCM",
                    "version": "1.0"
                }, f, indent=2)

            # Save chain state using ChainState class (with encryption)
            if export_data.get("chain_state"):
                from core.chain_state import ChainState
                chain_dir = qube_dir / "chain"
                cs = ChainState(data_dir=chain_dir, encryption_key=encryption_key, qube_id=qube_id)
                # Update the state with imported data
                imported_state = export_data["chain_state"]
                # Preserve important sections from import
                if "settings" in imported_state:
                    cs.update_settings(imported_state["settings"])
                if "skills" in imported_state:
                    cs.state["skills"] = imported_state["skills"]
                if "relationships" in imported_state:
                    cs.state["relationships"] = imported_state["relationships"]
                if "financial" in imported_state:
                    cs.state["financial"] = imported_state["financial"]
                cs._save()  # Save the imported state

            # Save memory blocks
            for block in export_data.get("memory_blocks", []):
                block_num = block.get("block_number", 0)
                block_type = block.get("block_type", "UNKNOWN")
                timestamp = block.get("timestamp", 0)
                filename = f"{block_num}_{block_type}_{timestamp}.json"
                with open(qube_dir / "blocks" / "permanent" / filename, 'w', encoding='utf-8') as f:
                    json.dump(block, f, indent=2)

            # Save relationships
            if export_data.get("relationships"):
                with open(qube_dir / "relationships" / "relationships.json", 'w', encoding='utf-8') as f:
                    json.dump(export_data["relationships"], f, indent=2)

            # Save NFT metadata
            if export_data.get("nft_metadata"):
                with open(qube_dir / "chain" / "nft_metadata.json", 'w', encoding='utf-8') as f:
                    json.dump(export_data["nft_metadata"], f, indent=2)

            # Save BCMR data
            if export_data.get("bcmr_data"):
                with open(qube_dir / "blockchain" / f"{qube_name}_bcmr.json", 'w', encoding='utf-8') as f:
                    json.dump(export_data["bcmr_data"], f, indent=2)

            # Save avatar (sanitize filename to prevent path traversal)
            if export_data.get("avatar_base64") and export_data.get("avatar_filename"):
                avatar_data = base64.b64decode(export_data["avatar_base64"])
                safe_avatar_filename = sanitize_filename(export_data["avatar_filename"])
                with open(qube_dir / "chain" / safe_avatar_filename, 'wb') as f:
                    f.write(avatar_data)

            logger.info(f"Imported Qube {qube_id} ({qube_name}) with {len(export_data.get('memory_blocks', []))} blocks")

            return {
                "success": True,
                "qube_id": qube_id,
                "qube_name": qube_name,
                "block_count": len(export_data.get("memory_blocks", []))
            }

        except zipfile.BadZipFile:
            return {"success": False, "error": "Invalid .qube file - not a valid package"}
        except KeyError as e:
            return {"success": False, "error": f"Invalid .qube file - missing required field: {e}"}
        except Exception as e:
            logger.error(f"Failed to import Qube: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def export_account_backup(self, export_path: str, export_password: str, master_password: str) -> Dict[str, Any]:
        """
        Export full account backup: account data + all Qubes in a single .qube-backup file.

        The backup contains everything needed to restore an account on any machine:
        - Account info (salt, iterations, password verifier, API keys, preferences, wallet security)
        - All Qubes (identity keys, genesis, memory blocks, chain state, relationships, NFT data, avatars)

        Args:
            export_path: Path where to save the .qube-backup file
            export_password: Password to encrypt the backup
            master_password: User's master password to decrypt existing data

        Returns:
            Dict with success, file_path, qube_count
        """
        import zipfile
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        try:
            self.orchestrator.set_master_key(master_password)

            user_dir = self.orchestrator.data_dir
            qubes_dir = user_dir / "qubes"

            # Collect account-level data
            account_data = {
                "user_id": self.orchestrator.user_id,
            }

            # Read account files as base64
            account_files = {}
            for filename in ["salt.bin", "pbkdf2_iterations.txt", "password_verifier.enc",
                             "api_keys.enc", "preferences.json", "wallet_security.enc"]:
                filepath = user_dir / filename
                if filepath.exists():
                    with open(filepath, 'rb') as f:
                        account_files[filename] = base64.b64encode(f.read()).decode('utf-8')

            # Read settings.enc directory if it exists
            settings_dir = user_dir / "settings.enc"
            if settings_dir.is_dir():
                for sf in settings_dir.iterdir():
                    if sf.is_file():
                        with open(sf, 'rb') as f:
                            account_files[f"settings.enc/{sf.name}"] = base64.b64encode(f.read()).decode('utf-8')

            account_data["account_files"] = account_files

            # Collect all valid Qubes (must have genesis.json to be a real qube)
            # Read directly from disk to avoid heavy load_qube() initialization
            # (AI init, audio init, CashScript migration) which can hang on Windows.
            qubes_data = []
            qube_count = 0

            if qubes_dir.exists():
                for dir_entry in sorted(qubes_dir.iterdir()):
                    if not dir_entry.is_dir():
                        continue

                    parts = dir_entry.name.rsplit('_', 1)
                    if len(parts) != 2:
                        continue

                    chain_dir = dir_entry / "chain"
                    genesis_file = chain_dir / "genesis.json"

                    # Skip broken/incomplete qube directories (failed creation attempts)
                    if not genesis_file.exists():
                        continue

                    qube_name, qube_id = parts

                    try:
                        # If qube is already loaded, anchor any active session
                        if qube_id in self.orchestrator.qubes:
                            qube = self.orchestrator.qubes[qube_id]
                            if qube.current_session and len(qube.current_session.session_blocks) > 0:
                                try:
                                    await qube.anchor_session()
                                except Exception as e:
                                    logger.warning(f"Failed to anchor session for {qube_id}: {e}")

                        # Read genesis block from disk
                        with open(genesis_file, 'r', encoding='utf-8') as f:
                            genesis_data = json.load(f)

                        qube_export = {
                            "qube_id": qube_id,
                            "qube_name": qube_name,
                            "genesis_block": genesis_data,
                            "chain_state": None,
                            "memory_blocks": [],
                            "relationships": None,
                            "nft_metadata": None,
                            "bcmr_data": None,
                            "avatar_base64": None,
                            "snapshots": None,
                            "locker": None,
                            "shared_memory": None,
                            "skills_cache": None,
                        }

                        # Private key — decrypt from qube_metadata.json
                        from cryptography.hazmat.primitives import serialization as key_serialization
                        metadata_file = chain_dir / "qube_metadata.json"
                        if metadata_file.exists():
                            with open(metadata_file, 'r', encoding='utf-8') as f:
                                qube_metadata = json.load(f)
                            enc_key_hex = qube_metadata.get("encrypted_private_key")
                            if enc_key_hex:
                                private_key = self.orchestrator._decrypt_private_key(
                                    enc_key_hex, self.orchestrator.master_key
                                )
                                qube_export["private_key_pem"] = private_key.private_bytes(
                                    encoding=key_serialization.Encoding.PEM,
                                    format=key_serialization.PrivateFormat.PKCS8,
                                    encryption_algorithm=key_serialization.NoEncryption()
                                ).decode('utf-8')

                        # Chain state — decrypt directly from disk
                        encryption_key = self._get_qube_encryption_key(dir_entry)
                        if encryption_key:
                            chain_state_file = chain_dir / "chain_state.json"
                            if chain_state_file.exists():
                                from crypto.encryption import decrypt_block_data, derive_chain_state_key
                                with open(chain_state_file, 'r') as f:
                                    cs_file_data = json.load(f)
                                if cs_file_data.get("encrypted"):
                                    cs_key = derive_chain_state_key(encryption_key)
                                    qube_export["chain_state"] = decrypt_block_data(cs_file_data, cs_key)
                                else:
                                    qube_export["chain_state"] = cs_file_data

                        # Memory blocks
                        blocks_dir = dir_entry / "blocks" / "permanent"
                        if blocks_dir.exists():
                            for block_file in sorted(blocks_dir.glob("*.json")):
                                with open(block_file, 'r', encoding='utf-8') as f:
                                    qube_export["memory_blocks"].append(json.load(f))

                        # Relationships
                        rel_file = dir_entry / "relationships" / "relationships.json"
                        if rel_file.exists():
                            with open(rel_file, 'r', encoding='utf-8') as f:
                                qube_export["relationships"] = json.load(f)

                        # NFT metadata
                        nft_file = chain_dir / "nft_metadata.json"
                        if nft_file.exists():
                            with open(nft_file, 'r', encoding='utf-8') as f:
                                qube_export["nft_metadata"] = json.load(f)

                        # BCMR
                        bcmr_file = dir_entry / "blockchain" / f"{qube_name}_bcmr.json"
                        if bcmr_file.exists():
                            with open(bcmr_file, 'r', encoding='utf-8') as f:
                                qube_export["bcmr_data"] = json.load(f)

                        # Avatar
                        for pattern in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
                            avatar_files = list(chain_dir.glob(f"*_avatar{pattern[1:]}"))
                            if avatar_files:
                                with open(avatar_files[0], 'rb') as f:
                                    qube_export["avatar_base64"] = base64.b64encode(f.read()).decode('utf-8')
                                    qube_export["avatar_filename"] = avatar_files[0].name
                                break

                        # Helper: read entire directory recursively as {rel_path: base64}
                        def _read_dir_as_b64(root_dir: Path) -> dict:
                            result = {}
                            if not root_dir.exists():
                                return result
                            for f in sorted(root_dir.rglob("*")):
                                if f.is_file():
                                    # Always use forward slashes so backups are cross-platform
                                    rel = f.relative_to(root_dir).as_posix()
                                    with open(f, 'rb') as fh:
                                        result[rel] = base64.b64encode(fh.read()).decode('utf-8')
                            return result

                        # Snapshots
                        qube_export["snapshots"] = _read_dir_as_b64(dir_entry / "snapshots")

                        # Locker
                        qube_export["locker"] = _read_dir_as_b64(dir_entry / "locker")

                        # Shared memory
                        qube_export["shared_memory"] = _read_dir_as_b64(dir_entry / "shared_memory")

                        # Skills cache
                        skills_file = chain_dir / "skills_cache.json"
                        if skills_file.exists():
                            with open(skills_file, 'r', encoding='utf-8') as f:
                                qube_export["skills_cache"] = json.load(f)

                        qubes_data.append(qube_export)
                        qube_count += 1

                    except Exception as e:
                        logger.warning(f"Failed to export Qube {qube_id}: {e}")
                        continue

            account_data["qubes"] = qubes_data

            # Encrypt the full bundle
            salt = os.urandom(32)
            nonce = os.urandom(12)

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = kdf.derive(export_password.encode('utf-8'))

            aesgcm = AESGCM(key)
            data_json = json.dumps(account_data, ensure_ascii=False).encode('utf-8')
            encrypted_data = aesgcm.encrypt(nonce, data_json, None)

            # Create manifest
            manifest = {
                "version": "1.0",
                "type": "account-backup",
                "user_id": self.orchestrator.user_id,
                "export_date": datetime.now().isoformat(),
                "qube_count": qube_count,
                "qube_names": [q["qube_name"] for q in qubes_data],
                "salt": salt.hex(),
                "nonce": nonce.hex(),
            }

            # Ensure parent directory exists (e.g. data/backups/)
            Path(export_path).parent.mkdir(parents=True, exist_ok=True)

            # Write ZIP
            with zipfile.ZipFile(export_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.writestr("data.enc", encrypted_data)

            logger.info(f"Exported account backup to {export_path} ({qube_count} Qubes)")

            return {
                "success": True,
                "file_path": export_path,
                "qube_count": qube_count,
            }

        except Exception as e:
            logger.error(f"Failed to export account backup: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def export_account_backup_ipfs(self, export_password: str, master_password: str, wallet_sig: str = "") -> Dict[str, Any]:
        """
        Export full account backup to IPFS via Pinata.

        Creates the same backup as export_account_backup, then uploads the encrypted
        ZIP to IPFS and returns the CID. The CID is needed to restore from IPFS.

        Returns:
            Dict with success, ipfs_cid, ipfs_url, qube_count
        """
        import tempfile
        import os
        import aiohttp

        tmp_path = None
        try:
            # Read Pinata JWT from encrypted api_keys.enc (same as sync_to_chain)
            self.orchestrator.set_master_key(master_password)
            api_keys = self.orchestrator.get_api_keys()
            pinata_key = api_keys.pinata_jwt if api_keys else None
            if not pinata_key:
                return {"success": False, "error": "Pinata API key not configured. Add it in Settings → API Keys."}

            # Write backup to a temp file using existing logic
            with tempfile.NamedTemporaryFile(suffix='.qube-backup', delete=False) as tmp:
                tmp_path = tmp.name

            result = await self.export_account_backup_v2(tmp_path, export_password, master_password, wallet_sig=wallet_sig)
            if not result.get("success"):
                return result

            with open(tmp_path, 'rb') as f:
                backup_bytes = f.read()

            user_id = self.orchestrator.user_id
            filename = f"qubes_account_{user_id}.qube-backup"

            form = aiohttp.FormData()
            form.add_field('file', backup_bytes, filename=filename, content_type='application/octet-stream')
            form.add_field('pinataMetadata', json.dumps({"name": filename}), content_type='application/json')

            headers = {"Authorization": f"Bearer {pinata_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.pinata.cloud/pinning/pinFileToIPFS",
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        return {"success": False, "error": f"Pinata upload failed ({resp.status}): {body[:300]}"}
                    upload_result = await resp.json()
                    ipfs_cid = upload_result["IpfsHash"]

            logger.info(f"Account backup uploaded to IPFS: {ipfs_cid}")
            return {
                "success": True,
                "ipfs_cid": ipfs_cid,
                "ipfs_url": f"https://gateway.pinata.cloud/ipfs/{ipfs_cid}",
                "qube_count": result.get("qube_count", 0),
            }

        except Exception as e:
            logger.error(f"Failed to export account backup to IPFS: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    async def list_account_backups_pinata(self, pinata_jwt: str) -> Dict[str, Any]:
        """
        List account backups stored in Pinata by querying the pin list API.

        Filters pins whose name starts with 'qubes_account_' and returns them
        sorted by date (newest first) so the user can pick which one to restore.

        Args:
            pinata_jwt: Pinata JWT API key (from Settings → API Keys)

        Returns:
            Dict with success, backups (list of {cid, name, date, size_bytes})
        """
        import aiohttp

        if not pinata_jwt or len(pinata_jwt.strip()) < 10:
            return {"success": False, "error": "Invalid Pinata JWT"}

        jwt = pinata_jwt.strip()
        headers = {"Authorization": f"Bearer {jwt}"}

        # Query pins filtered by name prefix
        params = {
            "status": "pinned",
            "metadata[name]": "qubes_account_",
            "pageLimit": 100,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.pinata.cloud/data/pinList",
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 401:
                        return {"success": False, "error": "Invalid Pinata API key — check your JWT."}
                    if resp.status != 200:
                        body = await resp.text()
                        return {"success": False, "error": f"Pinata API error ({resp.status}): {body[:300]}"}
                    data = await resp.json()

            rows = data.get("rows", [])
            backups = []
            for pin in rows:
                name = pin.get("metadata", {}).get("name", "")
                if not name.startswith("qubes_account_"):
                    continue
                backups.append({
                    "cid": pin.get("ipfs_pin_hash", ""),
                    "name": name,
                    "date": pin.get("date_pinned", ""),
                    "size_bytes": pin.get("size", 0),
                })

            # Sort newest first
            backups.sort(key=lambda x: x["date"], reverse=True)

            logger.info(f"Found {len(backups)} Qubes backup(s) in Pinata")
            return {"success": True, "backups": backups}

        except Exception as e:
            logger.error(f"Failed to list Pinata backups: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _derive_backup_key_v2(self, password: str, salt_hex: str) -> bytes:
        """Derive AES-256 key from password + hex salt using PBKDF2."""
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        salt = bytes.fromhex(salt_hex)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600000,
        )
        return kdf.derive(password.encode('utf-8'))

    def _encrypt_section_v2(self, data_dict: dict, key: bytes) -> bytes:
        """Encrypt a dict section: random nonce + AESGCM → returns nonce(12B)+ciphertext bytes."""
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        data_bytes = json.dumps(data_dict, ensure_ascii=False).encode('utf-8')
        ciphertext = aesgcm.encrypt(nonce, data_bytes, None)
        return nonce + ciphertext

    def _decrypt_section_v2(self, enc_bytes: bytes, key: bytes) -> dict:
        """Decrypt a v2 section: split nonce(12B)+ciphertext, decrypt, return dict."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = enc_bytes[:12]
        ciphertext = enc_bytes[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode('utf-8'))

    async def export_account_backup_v2(self, export_path: str, export_password: str, master_password: str, wallet_sig: str = "") -> Dict[str, Any]:
        """
        Export full account backup in v2 structured ZIP format.

        v2 ZIP structure:
          manifest.json  - plaintext
          account.enc    - [nonce 12B][AES-256-GCM ciphertext] of account_files dict
          qubes/
            {name}_{id}.enc  - per-Qube encrypted export

        Args:
            export_path: Path where to save the .qube-backup file
            export_password: Password to encrypt the backup
            master_password: User's master password to decrypt existing data

        Returns:
            Dict with success, file_path, qube_count
        """
        import zipfile
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives import serialization as key_serialization

        try:
            self.orchestrator.set_master_key(master_password)

            user_dir = self.orchestrator.data_dir
            qubes_dir = user_dir / "qubes"

            # Generate salt and derive key
            salt = os.urandom(32)

            # ── Key Derivation ──────────────────────────────────────────────────────────
            # NEW (2026-03-15): Wallet-bound encryption — key derived from password + wallet signature.
            # The wallet signature (bch_signMessage("qubes-backup-key:v1")) is deterministic
            # (RFC 6979) so the same wallet always produces the same key.
            #
            # TODO(MIGRATION-2026-04-15): Remove password-only path below.
            # After this date, reject wallet_sig="" and require WalletConnect for all backups.
            # Any developer reading this: warn the user if their backup has wallet_bound=False.
            # ────────────────────────────────────────────────────────────────────────────
            import base64 as _base64
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            if wallet_sig:
                try:
                    sig_bytes = _base64.b64decode(wallet_sig)
                except Exception:
                    try:
                        sig_bytes = bytes.fromhex(wallet_sig)
                    except Exception:
                        sig_bytes = wallet_sig.encode('utf-8')
                key_material = export_password.encode('utf-8') + sig_bytes
                wallet_bound = True
            else:
                # DEPRECATED(2026-03-15): Password-only key — no wallet binding.
                # TODO(MIGRATION-2026-04-15): Remove this path. Existing backups created
                # before wallet-binding was introduced still use this. Warn users.
                key_material = export_password.encode('utf-8')
                wallet_bound = False

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = kdf.derive(key_material)

            # Collect account-level files
            account_files = {}
            for filename in ["salt.bin", "pbkdf2_iterations.txt", "password_verifier.enc",
                             "api_keys.enc", "preferences.json", "wallet_security.enc"]:
                filepath = user_dir / filename
                if filepath.exists():
                    with open(filepath, 'rb') as f:
                        account_files[filename] = base64.b64encode(f.read()).decode('utf-8')

            # Read settings.enc directory if it exists
            settings_dir = user_dir / "settings.enc"
            if settings_dir.is_dir():
                for sf in settings_dir.iterdir():
                    if sf.is_file():
                        with open(sf, 'rb') as f:
                            account_files[f"settings.enc/{sf.name}"] = base64.b64encode(f.read()).decode('utf-8')

            # Encrypt account section
            account_enc_bytes = self._encrypt_section_v2(account_files, key)

            # Helper: read entire directory recursively as {rel_path: base64}
            def _read_dir_as_b64(root_dir: Path) -> dict:
                result = {}
                if not root_dir.exists():
                    return result
                for f in sorted(root_dir.rglob("*")):
                    if f.is_file():
                        rel = f.relative_to(root_dir).as_posix()
                        with open(f, 'rb') as fh:
                            result[rel] = base64.b64encode(fh.read()).decode('utf-8')
                return result

            # Collect all valid Qubes
            qube_index = []
            qube_sections = {}  # filename -> bytes
            qube_count = 0

            if qubes_dir.exists():
                for dir_entry in sorted(qubes_dir.iterdir()):
                    if not dir_entry.is_dir():
                        continue

                    parts = dir_entry.name.rsplit('_', 1)
                    if len(parts) != 2:
                        continue

                    chain_dir = dir_entry / "chain"
                    genesis_file = chain_dir / "genesis.json"

                    if not genesis_file.exists():
                        continue

                    qube_name, qube_id = parts

                    try:
                        # Anchor active session if loaded
                        if qube_id in self.orchestrator.qubes:
                            qube = self.orchestrator.qubes[qube_id]
                            if qube.current_session and len(qube.current_session.session_blocks) > 0:
                                try:
                                    await qube.anchor_session()
                                except Exception as e:
                                    logger.warning(f"Failed to anchor session for {qube_id}: {e}")

                        # Read genesis block
                        with open(genesis_file, 'r', encoding='utf-8') as f:
                            genesis_data = json.load(f)

                        qube_export = {
                            "qube_id": qube_id,
                            "qube_name": qube_name,
                            "genesis_block": genesis_data,
                            "chain_state": None,
                            "memory_blocks": [],
                            "relationships": None,
                            "nft_metadata": None,
                            "bcmr_data": None,
                            "avatar_base64": None,
                            "snapshots": None,
                            "locker": None,
                            "shared_memory": None,
                            "skills_cache": None,
                        }

                        # Private key
                        metadata_file = chain_dir / "qube_metadata.json"
                        if metadata_file.exists():
                            with open(metadata_file, 'r', encoding='utf-8') as f:
                                qube_metadata = json.load(f)
                            enc_key_hex = qube_metadata.get("encrypted_private_key")
                            if enc_key_hex:
                                private_key = self.orchestrator._decrypt_private_key(
                                    enc_key_hex, self.orchestrator.master_key
                                )
                                qube_export["private_key_pem"] = private_key.private_bytes(
                                    encoding=key_serialization.Encoding.PEM,
                                    format=key_serialization.PrivateFormat.PKCS8,
                                    encryption_algorithm=key_serialization.NoEncryption()
                                ).decode('utf-8')

                        # Chain state
                        encryption_key = self._get_qube_encryption_key(dir_entry)
                        if encryption_key:
                            chain_state_file = chain_dir / "chain_state.json"
                            if chain_state_file.exists():
                                from crypto.encryption import decrypt_block_data, derive_chain_state_key
                                with open(chain_state_file, 'r') as f:
                                    cs_file_data = json.load(f)
                                if cs_file_data.get("encrypted"):
                                    cs_key = derive_chain_state_key(encryption_key)
                                    qube_export["chain_state"] = decrypt_block_data(cs_file_data, cs_key)
                                else:
                                    qube_export["chain_state"] = cs_file_data

                        # Memory blocks
                        blocks_dir = dir_entry / "blocks" / "permanent"
                        if blocks_dir.exists():
                            for block_file in sorted(blocks_dir.glob("*.json")):
                                with open(block_file, 'r', encoding='utf-8') as f:
                                    qube_export["memory_blocks"].append(json.load(f))

                        # Relationships
                        rel_file = dir_entry / "relationships" / "relationships.json"
                        if rel_file.exists():
                            with open(rel_file, 'r', encoding='utf-8') as f:
                                qube_export["relationships"] = json.load(f)

                        # NFT metadata
                        nft_file = chain_dir / "nft_metadata.json"
                        if nft_file.exists():
                            with open(nft_file, 'r', encoding='utf-8') as f:
                                qube_export["nft_metadata"] = json.load(f)

                        # BCMR
                        bcmr_file = dir_entry / "blockchain" / f"{qube_name}_bcmr.json"
                        if bcmr_file.exists():
                            with open(bcmr_file, 'r', encoding='utf-8') as f:
                                qube_export["bcmr_data"] = json.load(f)

                        # Avatar
                        for pattern in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
                            avatar_files = list(chain_dir.glob(f"*_avatar{pattern[1:]}"))
                            if avatar_files:
                                with open(avatar_files[0], 'rb') as f:
                                    qube_export["avatar_base64"] = base64.b64encode(f.read()).decode('utf-8')
                                    qube_export["avatar_filename"] = avatar_files[0].name
                                break

                        # Snapshots, locker, shared memory
                        qube_export["snapshots"] = _read_dir_as_b64(dir_entry / "snapshots")
                        qube_export["locker"] = _read_dir_as_b64(dir_entry / "locker")
                        qube_export["shared_memory"] = _read_dir_as_b64(dir_entry / "shared_memory")

                        # Skills cache
                        skills_file = chain_dir / "skills_cache.json"
                        if skills_file.exists():
                            with open(skills_file, 'r', encoding='utf-8') as f:
                                qube_export["skills_cache"] = json.load(f)

                        # Encrypt qube section
                        qube_filename = f"qubes/{qube_name}_{qube_id}.enc"
                        qube_sections[qube_filename] = self._encrypt_section_v2(qube_export, key)

                        qube_index.append({
                            "name": qube_name,
                            "id": qube_id,
                            "file": qube_filename,
                        })
                        qube_count += 1

                    except Exception as e:
                        logger.warning(f"Failed to export Qube {qube_id}: {e}")
                        continue

            # Build manifest
            manifest = {
                "version": "2.0",
                "type": "account-backup",
                "user_id": self.orchestrator.user_id,
                "export_date": datetime.now().isoformat(),
                "qube_count": qube_count,
                "qube_index": qube_index,
                "salt": salt.hex(),
                "wallet_bound": wallet_bound,
                "wallet_address": "",  # Set by frontend, stored for display only — not used in key derivation
            }

            # Ensure parent directory exists
            Path(export_path).parent.mkdir(parents=True, exist_ok=True)

            # Write ZIP
            with zipfile.ZipFile(export_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.writestr("account.enc", account_enc_bytes)
                for filename, enc_bytes in qube_sections.items():
                    zf.writestr(filename, enc_bytes)

            logger.info(f"Exported v2 account backup to {export_path} ({qube_count} Qubes)")

            return {
                "success": True,
                "file_path": export_path,
                "qube_count": qube_count,
            }

        except Exception as e:
            logger.error(f"Failed to export v2 account backup: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def sync_qube_to_ipfs_backup(self, qube_id: str, export_password: str, master_password: str, wallet_sig: str = "") -> Dict[str, Any]:
        """
        Backup a single Qube to IPFS via Pinata in v2 format.

        Creates a v2 ZIP (type="qube-backup") with just one Qube in qubes/{name}_{id}.enc,
        uploads to Pinata, returns the CID.

        Args:
            qube_id: The Qube ID to backup
            export_password: Password to encrypt the backup
            master_password: User's master password

        Returns:
            Dict with success, ipfs_cid, ipfs_url, qube_name
        """
        import tempfile
        import base64
        import os
        import zipfile
        import aiohttp
        from cryptography.hazmat.primitives import serialization as key_serialization

        tmp_path = None
        try:
            # Read Pinata JWT from encrypted api_keys.enc (same as sync_to_chain)
            self.orchestrator.set_master_key(master_password)
            api_keys = self.orchestrator.get_api_keys()
            pinata_key = api_keys.pinata_jwt if api_keys else None
            if not pinata_key:
                return {"success": False, "error": "Pinata API key not configured. Add it in Settings → API Keys."}

            user_dir = self.orchestrator.data_dir
            qubes_dir = user_dir / "qubes"

            # Find qube directory
            qube_dir = None
            qube_name = None
            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    parts = dir_entry.name.rsplit('_', 1)
                    if len(parts) == 2:
                        qube_name = parts[0]
                    break

            if not qube_dir or not qube_name:
                return {"success": False, "error": f"Qube directory not found for {qube_id}"}

            chain_dir = qube_dir / "chain"
            genesis_file = chain_dir / "genesis.json"
            if not genesis_file.exists():
                return {"success": False, "error": f"Qube {qube_id} has no genesis block — incomplete Qube"}

            # Generate salt and derive key
            salt = os.urandom(32)

            # ── Key Derivation ──────────────────────────────────────────────────────────
            # NEW (2026-03-15): Wallet-bound encryption — key derived from password + wallet signature.
            # The wallet signature (bch_signMessage("qubes-backup-key:v1")) is deterministic
            # (RFC 6979) so the same wallet always produces the same key.
            #
            # TODO(MIGRATION-2026-04-15): Remove password-only path below.
            # After this date, reject wallet_sig="" and require WalletConnect for all backups.
            # Any developer reading this: warn the user if their backup has wallet_bound=False.
            # ────────────────────────────────────────────────────────────────────────────
            import base64 as _base64
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            if wallet_sig:
                try:
                    sig_bytes = _base64.b64decode(wallet_sig)
                except Exception:
                    try:
                        sig_bytes = bytes.fromhex(wallet_sig)
                    except Exception:
                        sig_bytes = wallet_sig.encode('utf-8')
                key_material = export_password.encode('utf-8') + sig_bytes
                wallet_bound = True
            else:
                # DEPRECATED(2026-03-15): Password-only key — no wallet binding.
                # TODO(MIGRATION-2026-04-15): Remove this path. Existing backups created
                # before wallet-binding was introduced still use this. Warn users.
                key_material = export_password.encode('utf-8')
                wallet_bound = False

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = kdf.derive(key_material)

            # Helper: read entire directory recursively as {rel_path: base64}
            def _read_dir_as_b64(root_dir: Path) -> dict:
                result = {}
                if not root_dir.exists():
                    return result
                for f in sorted(root_dir.rglob("*")):
                    if f.is_file():
                        rel = f.relative_to(root_dir).as_posix()
                        with open(f, 'rb') as fh:
                            result[rel] = base64.b64encode(fh.read()).decode('utf-8')
                return result

            # Anchor active session if loaded
            if qube_id in self.orchestrator.qubes:
                qube_obj = self.orchestrator.qubes[qube_id]
                if qube_obj.current_session and len(qube_obj.current_session.session_blocks) > 0:
                    try:
                        await qube_obj.anchor_session()
                    except Exception as e:
                        logger.warning(f"Failed to anchor session for {qube_id}: {e}")

            # Read genesis block
            with open(genesis_file, 'r', encoding='utf-8') as f:
                genesis_data = json.load(f)

            qube_export = {
                "qube_id": qube_id,
                "qube_name": qube_name,
                "genesis_block": genesis_data,
                "chain_state": None,
                "memory_blocks": [],
                "relationships": None,
                "nft_metadata": None,
                "bcmr_data": None,
                "avatar_base64": None,
                "snapshots": None,
                "locker": None,
                "shared_memory": None,
                "skills_cache": None,
            }

            # Private key
            metadata_file = chain_dir / "qube_metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    qube_metadata = json.load(f)
                enc_key_hex = qube_metadata.get("encrypted_private_key")
                if enc_key_hex:
                    private_key = self.orchestrator._decrypt_private_key(
                        enc_key_hex, self.orchestrator.master_key
                    )
                    qube_export["private_key_pem"] = private_key.private_bytes(
                        encoding=key_serialization.Encoding.PEM,
                        format=key_serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=key_serialization.NoEncryption()
                    ).decode('utf-8')

            # Chain state
            encryption_key = self._get_qube_encryption_key(qube_dir)
            if encryption_key:
                chain_state_file = chain_dir / "chain_state.json"
                if chain_state_file.exists():
                    from crypto.encryption import decrypt_block_data, derive_chain_state_key
                    with open(chain_state_file, 'r') as f:
                        cs_file_data = json.load(f)
                    if cs_file_data.get("encrypted"):
                        cs_key = derive_chain_state_key(encryption_key)
                        qube_export["chain_state"] = decrypt_block_data(cs_file_data, cs_key)
                    else:
                        qube_export["chain_state"] = cs_file_data

            # Memory blocks
            blocks_dir = qube_dir / "blocks" / "permanent"
            if blocks_dir.exists():
                for block_file in sorted(blocks_dir.glob("*.json")):
                    with open(block_file, 'r', encoding='utf-8') as f:
                        qube_export["memory_blocks"].append(json.load(f))

            # Relationships
            rel_file = qube_dir / "relationships" / "relationships.json"
            if rel_file.exists():
                with open(rel_file, 'r', encoding='utf-8') as f:
                    qube_export["relationships"] = json.load(f)

            # NFT metadata
            nft_file = chain_dir / "nft_metadata.json"
            if nft_file.exists():
                with open(nft_file, 'r', encoding='utf-8') as f:
                    qube_export["nft_metadata"] = json.load(f)

            # BCMR
            bcmr_file = qube_dir / "blockchain" / f"{qube_name}_bcmr.json"
            if bcmr_file.exists():
                with open(bcmr_file, 'r', encoding='utf-8') as f:
                    qube_export["bcmr_data"] = json.load(f)

            # Avatar
            for pattern in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
                avatar_files = list(chain_dir.glob(f"*_avatar{pattern[1:]}"))
                if avatar_files:
                    with open(avatar_files[0], 'rb') as f:
                        qube_export["avatar_base64"] = base64.b64encode(f.read()).decode('utf-8')
                        qube_export["avatar_filename"] = avatar_files[0].name
                    break

            qube_export["snapshots"] = _read_dir_as_b64(qube_dir / "snapshots")
            qube_export["locker"] = _read_dir_as_b64(qube_dir / "locker")
            qube_export["shared_memory"] = _read_dir_as_b64(qube_dir / "shared_memory")

            skills_file = chain_dir / "skills_cache.json"
            if skills_file.exists():
                with open(skills_file, 'r', encoding='utf-8') as f:
                    qube_export["skills_cache"] = json.load(f)

            # Encrypt qube section
            qube_filename = f"qubes/{qube_name}_{qube_id}.enc"
            qube_enc_bytes = self._encrypt_section_v2(qube_export, key)

            # Build manifest
            manifest = {
                "version": "2.0",
                "type": "qube-backup",
                "user_id": self.orchestrator.user_id,
                "export_date": datetime.now().isoformat(),
                "qube_count": 1,
                "qube_index": [{"name": qube_name, "id": qube_id, "file": qube_filename}],
                "salt": salt.hex(),
                "wallet_bound": wallet_bound,
                "wallet_address": "",  # Set by frontend, stored for display only — not used in key derivation
            }

            # Write to temp file
            with tempfile.NamedTemporaryFile(suffix='.qube-backup', delete=False) as tmp:
                tmp_path = tmp.name

            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.writestr(qube_filename, qube_enc_bytes)

            with open(tmp_path, 'rb') as f:
                backup_bytes = f.read()

            user_id = self.orchestrator.user_id
            filename = f"qubes_qube_{user_id}_{qube_name}_{qube_id}.qube-backup"

            form = aiohttp.FormData()
            form.add_field('file', backup_bytes, filename=filename, content_type='application/octet-stream')
            form.add_field('pinataMetadata', json.dumps({"name": filename}), content_type='application/json')

            headers = {"Authorization": f"Bearer {pinata_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.pinata.cloud/pinning/pinFileToIPFS",
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=300)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        return {"success": False, "error": f"Pinata upload failed ({resp.status}): {body[:300]}"}
                    upload_result = await resp.json()
                    ipfs_cid = upload_result["IpfsHash"]

            logger.info(f"Qube {qube_id} backup uploaded to IPFS: {ipfs_cid}")
            return {
                "success": True,
                "ipfs_cid": ipfs_cid,
                "ipfs_url": f"https://gateway.pinata.cloud/ipfs/{ipfs_cid}",
                "qube_name": qube_name,
            }

        except Exception as e:
            logger.error(f"Failed to sync qube {qube_id} to IPFS backup: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    async def import_account_backup_ipfs(self, ipfs_cid: str, import_password: str, master_password: str, wallet_sig: str = "") -> Dict[str, Any]:
        """
        Restore full account backup from IPFS.

        Downloads the encrypted backup by CID from IPFS gateways, saves to a temp
        file, and restores using the same logic as import_account_backup.

        Args:
            ipfs_cid: The IPFS CID returned when the backup was uploaded
            import_password: Password used when the backup was created
            master_password: Master password for the restored account on this device

        Returns:
            Dict with success, imported_count, skipped_count, user_id
        """
        import tempfile
        import os
        import aiohttp

        if not ipfs_cid or len(ipfs_cid.strip()) < 10:
            return {"success": False, "error": "Invalid IPFS CID"}

        ipfs_cid = ipfs_cid.strip()

        gateways = [
            f"https://gateway.pinata.cloud/ipfs/{ipfs_cid}",
            f"https://ipfs.io/ipfs/{ipfs_cid}",
            f"https://cloudflare-ipfs.com/ipfs/{ipfs_cid}",
        ]

        backup_bytes = None
        last_error = "Unknown error"

        async with aiohttp.ClientSession() as session:
            for url in gateways:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status == 200:
                            backup_bytes = await resp.read()
                            logger.info(f"Downloaded backup from IPFS: {url} ({len(backup_bytes)} bytes)")
                            break
                        last_error = f"HTTP {resp.status} from {url}"
                except Exception as e:
                    last_error = str(e)

        if not backup_bytes:
            return {"success": False, "error": f"Could not download backup from IPFS: {last_error}"}

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.qube-backup', delete=False) as tmp:
                tmp.write(backup_bytes)
                tmp_path = tmp.name

            return await self.import_account_backup(tmp_path, import_password, master_password, wallet_sig=wallet_sig)

        except Exception as e:
            logger.error(f"Failed to import account backup from IPFS: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    async def cleanup_incomplete_qubes(self) -> Dict[str, Any]:
        """
        Delete qube directories that are incomplete (missing genesis.json).
        These are leftovers from failed creation attempts.

        Returns:
            Dict with success, deleted_count, deleted_dirs
        """
        import shutil

        try:
            qubes_dir = self.orchestrator.data_dir / "qubes"
            if not qubes_dir.exists():
                return {"success": True, "deleted_count": 0, "deleted_dirs": []}

            deleted = []
            for dir_entry in sorted(qubes_dir.iterdir()):
                if not dir_entry.is_dir():
                    continue
                parts = dir_entry.name.rsplit('_', 1)
                if len(parts) != 2:
                    continue
                # A valid qube must have genesis.json
                if not (dir_entry / "chain" / "genesis.json").exists():
                    dir_name = dir_entry.name
                    try:
                        shutil.rmtree(dir_entry)
                        deleted.append(dir_name)
                        logger.info(f"Deleted incomplete qube directory: {dir_name}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {dir_name}: {e}")

            return {
                "success": True,
                "deleted_count": len(deleted),
                "deleted_dirs": deleted,
            }
        except Exception as e:
            logger.error(f"Failed to cleanup incomplete qubes: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def import_account_backup(self, import_path: str, import_password: str, master_password: str, wallet_sig: str = "") -> Dict[str, Any]:
        """
        Import a full account backup from a .qube-backup file.

        Restores account data and all Qubes. If the account already exists,
        merges Qubes (skipping duplicates).

        Args:
            import_path: Path to the .qube-backup file
            import_password: Password used when the backup was created
            master_password: Master password for the restored account

        Returns:
            Dict with success, imported_count, skipped_count
        """
        import zipfile
        import base64
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        try:
            # Read manifest
            with zipfile.ZipFile(import_path, 'r') as zf:
                manifest = json.loads(zf.read("manifest.json"))
                zip_namelist = zf.namelist()

            backup_version = manifest.get("version", "1.0")
            backup_type = manifest.get("type", "account-backup")

            if backup_type not in ("account-backup", "qube-backup"):
                return {"success": False, "error": "Not an account backup file. Use 'Import from Disk' for single Qube files."}

            # ---- v2 path ----
            if backup_version.startswith("2."):
                # ── v2 Key Derivation ─────────────────────────────────────────────────────
                # TODO(MIGRATION-2026-04-15): Remove wallet_bound=False path.
                # New backups are always wallet_bound=True. Old ones still decrypt with password only.
                # ────────────────────────────────────────────────────────────────────────────
                import base64 as _base64
                from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
                from cryptography.hazmat.primitives import hashes
                salt = bytes.fromhex(manifest["salt"])
                wallet_bound = manifest.get("wallet_bound", False)
                if wallet_bound:
                    if not wallet_sig:
                        return {
                            "success": False,
                            "error": "WALLET_REQUIRED: This backup requires WalletConnect authentication. "
                                     "Please connect your wallet (Cashonize/Zapit) and try again."
                        }
                    try:
                        sig_bytes = _base64.b64decode(wallet_sig)
                    except Exception:
                        try:
                            sig_bytes = bytes.fromhex(wallet_sig)
                        except Exception:
                            sig_bytes = wallet_sig.encode('utf-8')
                    key_material = import_password.encode('utf-8') + sig_bytes
                else:
                    # DEPRECATED(2026-03-15): Password-only restore.
                    # TODO(MIGRATION-2026-04-15): Remove. Warn user to re-create backup with WalletConnect.
                    key_material = import_password.encode('utf-8')

                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=600000,
                )
                key = kdf.derive(key_material)

                # Restore account files (only present in account-backup type)
                backup_user_id = manifest.get("user_id", self.orchestrator.user_id)
                from utils.paths import get_users_dir
                user_dir = get_users_dir() / backup_user_id
                user_dir.mkdir(parents=True, exist_ok=True)

                with zipfile.ZipFile(import_path, 'r') as zf:
                    if "account.enc" in zip_namelist:
                        try:
                            account_enc_bytes = zf.read("account.enc")
                            account_files = self._decrypt_section_v2(account_enc_bytes, key)
                        except Exception:
                            return {"success": False, "error": "Wrong password — could not decrypt account section."}

                        for filename, b64data in account_files.items():
                            filepath = user_dir / filename
                            filepath.parent.mkdir(parents=True, exist_ok=True)
                            if filename in ("salt.bin", "pbkdf2_iterations.txt", "password_verifier.enc"):
                                if filepath.exists():
                                    logger.info(f"Skipping existing account file: {filename}")
                                    continue
                            with open(filepath, 'wb') as f:
                                f.write(base64.b64decode(b64data))
                            logger.info(f"Restored account file: {filename}")

                    # Point orchestrator at correct user directory
                    if self.orchestrator.user_id != backup_user_id:
                        self.orchestrator = UserOrchestrator(user_id=backup_user_id)

                    # Derive master key
                    self.orchestrator.set_master_key(master_password)

                    # Regenerate password verifier
                    await self._create_password_verifier(self.orchestrator)

                    # Import Qubes
                    qubes_dir = user_dir / "qubes"
                    qubes_dir.mkdir(parents=True, exist_ok=True)

                    imported_count = 0
                    skipped_count = 0

                    for qube_entry in manifest.get("qube_index", []):
                        qube_file = qube_entry["file"]
                        qube_name_entry = qube_entry["name"]
                        qube_id_entry = qube_entry["id"]

                        # Skip if already exists
                        existing = [d for d in qubes_dir.iterdir() if d.is_dir() and qube_id_entry in d.name]
                        if existing:
                            logger.info(f"Skipping existing Qube: {qube_name_entry} ({qube_id_entry})")
                            skipped_count += 1
                            continue

                        if qube_file not in zip_namelist:
                            logger.warning(f"Qube file {qube_file} not found in backup ZIP")
                            skipped_count += 1
                            continue

                        try:
                            enc_bytes = zf.read(qube_file)
                            try:
                                qube_export = self._decrypt_section_v2(enc_bytes, key)
                            except Exception:
                                return {"success": False, "error": f"Wrong password — could not decrypt Qube {qube_name_entry}."}

                            qube_id = qube_export["qube_id"]
                            qube_name = qube_export["qube_name"]
                            qube_dir_name = f"{qube_name}_{qube_id}"
                            qube_dir = qubes_dir / qube_dir_name

                            # Create directory structure
                            (qube_dir / "chain").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "blocks" / "permanent").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "blocks" / "session").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "relationships").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "blockchain").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "snapshots").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "locker").mkdir(parents=True, exist_ok=True)
                            (qube_dir / "shared_memory").mkdir(parents=True, exist_ok=True)

                            # Restore private key
                            from cryptography.hazmat.primitives.serialization import load_pem_private_key
                            from crypto.keys import serialize_public_key
                            private_key = load_pem_private_key(
                                qube_export["private_key_pem"].encode('utf-8'),
                                password=None
                            )
                            public_key = private_key.public_key()
                            encrypted_privkey = self.orchestrator._encrypt_private_key(
                                private_key, self.orchestrator.master_key
                            )

                            metadata = {
                                "qube_id": qube_id,
                                "name": qube_name,
                                "public_key": serialize_public_key(public_key),
                                "encrypted_private_key": encrypted_privkey.hex(),
                                "genesis_block": qube_export.get("genesis_block"),
                            }
                            with open(qube_dir / "chain" / "qube_metadata.json", 'w') as f:
                                json.dump(metadata, f, indent=2)

                            # Generate new encryption key
                            enc_key = os.urandom(32)
                            enc_nonce = os.urandom(12)
                            enc_aesgcm = AESGCM(self.orchestrator.master_key)
                            encrypted_enc_key = enc_aesgcm.encrypt(enc_nonce, enc_key, None)
                            enc_key_data = {"nonce": enc_nonce.hex(), "ciphertext": encrypted_enc_key.hex(), "algorithm": "AES-256-GCM", "version": "1.0"}
                            with open(qube_dir / "chain" / "encryption_key.enc", 'w') as f:
                                json.dump(enc_key_data, f)

                            # Restore chain state
                            if qube_export.get("chain_state"):
                                from core.chain_state import ChainState
                                cs = ChainState(data_dir=qube_dir / "chain", encryption_key=enc_key)
                                cs.state = qube_export["chain_state"]
                                cs._save()

                            # Restore memory blocks
                            for block in qube_export.get("memory_blocks", []):
                                block_num = block.get("block_number", 0)
                                block_type = block.get("block_type", "UNKNOWN")
                                timestamp = block.get("timestamp", "0")
                                filename = f"{block_num}_{block_type}_{timestamp}.json"
                                with open(qube_dir / "blocks" / "permanent" / filename, 'w', encoding='utf-8') as f:
                                    json.dump(block, f, indent=2)

                            # Restore relationships
                            if qube_export.get("relationships"):
                                with open(qube_dir / "relationships" / "relationships.json", 'w') as f:
                                    json.dump(qube_export["relationships"], f, indent=2)

                            # Restore NFT metadata
                            if qube_export.get("nft_metadata"):
                                with open(qube_dir / "chain" / "nft_metadata.json", 'w') as f:
                                    json.dump(qube_export["nft_metadata"], f, indent=2)

                            # Restore BCMR
                            if qube_export.get("bcmr_data"):
                                with open(qube_dir / "blockchain" / f"{qube_name}_bcmr.json", 'w') as f:
                                    json.dump(qube_export["bcmr_data"], f, indent=2)

                            # Restore avatar
                            if qube_export.get("avatar_base64") and qube_export.get("avatar_filename"):
                                with open(qube_dir / "chain" / qube_export["avatar_filename"], 'wb') as f:
                                    f.write(base64.b64decode(qube_export["avatar_base64"]))

                            def _restore_dir_from_b64(data: dict, root_dir: Path):
                                for rel_path, b64data in data.items():
                                    # Normalize separators: old Windows backups may use backslashes
                                    parts = rel_path.replace('\\', '/').split('/')
                                    dest = root_dir.joinpath(*parts)
                                    dest.parent.mkdir(parents=True, exist_ok=True)
                                    with open(dest, 'wb') as fh:
                                        fh.write(base64.b64decode(b64data))

                            if qube_export.get("snapshots"):
                                _restore_dir_from_b64(qube_export["snapshots"], qube_dir / "snapshots")
                            if qube_export.get("locker"):
                                _restore_dir_from_b64(qube_export["locker"], qube_dir / "locker")
                            if qube_export.get("shared_memory"):
                                _restore_dir_from_b64(qube_export["shared_memory"], qube_dir / "shared_memory")

                            # Restore skills cache
                            if qube_export.get("skills_cache") is not None:
                                with open(qube_dir / "chain" / "skills_cache.json", 'w', encoding='utf-8') as f:
                                    json.dump(qube_export["skills_cache"], f, indent=2)

                            imported_count += 1
                            logger.info(f"Imported Qube (v2): {qube_name} ({qube_id})")

                        except Exception as e:
                            logger.error(f"Failed to import Qube {qube_id_entry}: {e}", exc_info=True)
                            skipped_count += 1
                            continue

                logger.info(f"v2 account backup import complete: {imported_count} imported, {skipped_count} skipped")
                return {
                    "success": True,
                    "imported_count": imported_count,
                    "skipped_count": skipped_count,
                    "user_id": backup_user_id,
                }

            # ---- v1 path (legacy) ----
            with zipfile.ZipFile(import_path, 'r') as zf:
                encrypted_data = zf.read("data.enc")

            salt = bytes.fromhex(manifest["salt"])
            nonce = bytes.fromhex(manifest["nonce"])

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = kdf.derive(import_password.encode('utf-8'))

            aesgcm = AESGCM(key)
            try:
                decrypted = aesgcm.decrypt(nonce, encrypted_data, None)
            except Exception:
                return {"success": False, "error": "Wrong password — could not decrypt backup."}

            account_data = json.loads(decrypted.decode('utf-8'))

            # Use user_id from backup to determine correct data directory
            backup_user_id = account_data.get("user_id", self.orchestrator.user_id)
            from utils.paths import get_users_dir
            user_dir = get_users_dir() / backup_user_id
            user_dir.mkdir(parents=True, exist_ok=True)
            account_files = account_data.get("account_files", {})

            for filename, b64data in account_files.items():
                filepath = user_dir / filename
                filepath.parent.mkdir(parents=True, exist_ok=True)
                # Don't overwrite salt/iterations/verifier if account already exists
                if filename in ("salt.bin", "pbkdf2_iterations.txt", "password_verifier.enc"):
                    if filepath.exists():
                        logger.info(f"Skipping existing account file: {filename}")
                        continue
                with open(filepath, 'wb') as f:
                    f.write(base64.b64decode(b64data))
                logger.info(f"Restored account file: {filename}")

            # Point orchestrator at the correct user directory before deriving keys.
            # The login restore path passes userId='_restore', so the orchestrator
            # initially points at the wrong data dir. Reinitialize it with the
            # backup's real user_id so set_master_key reads the correct salt.bin
            # and the verifier gets written to the correct location.
            if self.orchestrator.user_id != backup_user_id:
                self.orchestrator = UserOrchestrator(user_id=backup_user_id)

            # Derive master key from the provided password + the user's salt.bin
            self.orchestrator.set_master_key(master_password)

            # Regenerate password verifier so login works with the new master password.
            # Without this, the verifier stays encrypted with the old password's key
            # while the qube keys get re-encrypted with the new one — split-brain.
            await self._create_password_verifier(self.orchestrator)

            # Import each Qube
            qubes_dir = user_dir / "qubes"
            qubes_dir.mkdir(parents=True, exist_ok=True)

            imported_count = 0
            skipped_count = 0

            for qube_export in account_data.get("qubes", []):
                qube_id = qube_export["qube_id"]
                qube_name = qube_export["qube_name"]
                qube_dir_name = f"{qube_name}_{qube_id}"
                qube_dir = qubes_dir / qube_dir_name

                # Skip if already exists
                existing = [d for d in qubes_dir.iterdir() if d.is_dir() and qube_id in d.name]
                if existing:
                    logger.info(f"Skipping existing Qube: {qube_name} ({qube_id})")
                    skipped_count += 1
                    continue

                try:
                    # Create directory structure
                    (qube_dir / "chain").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "blocks" / "permanent").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "blocks" / "session").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "relationships").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "blockchain").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "snapshots").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "locker").mkdir(parents=True, exist_ok=True)
                    (qube_dir / "shared_memory").mkdir(parents=True, exist_ok=True)

                    # Restore private key
                    from cryptography.hazmat.primitives.serialization import load_pem_private_key
                    from crypto.keys import serialize_public_key
                    private_key = load_pem_private_key(
                        qube_export["private_key_pem"].encode('utf-8'),
                        password=None
                    )
                    public_key = private_key.public_key()

                    # Encrypt private key with master key
                    encrypted_privkey = self.orchestrator._encrypt_private_key(
                        private_key, self.orchestrator.master_key
                    )

                    # Save qube_metadata.json
                    metadata = {
                        "qube_id": qube_id,
                        "name": qube_name,
                        "public_key": serialize_public_key(public_key),
                        "encrypted_private_key": encrypted_privkey.hex(),
                        "genesis_block": qube_export.get("genesis_block"),
                    }
                    with open(qube_dir / "chain" / "qube_metadata.json", 'w') as f:
                        json.dump(metadata, f, indent=2)

                    # Generate new encryption key for this qube
                    enc_key = os.urandom(32)
                    enc_nonce = os.urandom(12)
                    enc_aesgcm = AESGCM(self.orchestrator.master_key)
                    encrypted_enc_key = enc_aesgcm.encrypt(enc_nonce, enc_key, None)
                    enc_key_data = {"nonce": enc_nonce.hex(), "ciphertext": encrypted_enc_key.hex(), "algorithm": "AES-256-GCM", "version": "1.0"}
                    with open(qube_dir / "chain" / "encryption_key.enc", 'w') as f:
                        json.dump(enc_key_data, f)

                    # Restore chain state
                    if qube_export.get("chain_state"):
                        from core.chain_state import ChainState
                        cs = ChainState(data_dir=qube_dir / "chain", encryption_key=enc_key)
                        cs.state = qube_export["chain_state"]
                        cs._save()

                    # Restore memory blocks
                    for block in qube_export.get("memory_blocks", []):
                        block_num = block.get("block_number", 0)
                        block_type = block.get("block_type", "UNKNOWN")
                        timestamp = block.get("timestamp", "0")
                        filename = f"{block_num}_{block_type}_{timestamp}.json"
                        with open(qube_dir / "blocks" / "permanent" / filename, 'w', encoding='utf-8') as f:
                            json.dump(block, f, indent=2)

                    # Restore relationships
                    if qube_export.get("relationships"):
                        with open(qube_dir / "relationships" / "relationships.json", 'w') as f:
                            json.dump(qube_export["relationships"], f, indent=2)

                    # Restore NFT metadata
                    if qube_export.get("nft_metadata"):
                        with open(qube_dir / "chain" / "nft_metadata.json", 'w') as f:
                            json.dump(qube_export["nft_metadata"], f, indent=2)

                    # Restore BCMR
                    if qube_export.get("bcmr_data"):
                        with open(qube_dir / "blockchain" / f"{qube_name}_bcmr.json", 'w') as f:
                            json.dump(qube_export["bcmr_data"], f, indent=2)

                    # Restore avatar
                    if qube_export.get("avatar_base64") and qube_export.get("avatar_filename"):
                        with open(qube_dir / "chain" / qube_export["avatar_filename"], 'wb') as f:
                            f.write(base64.b64decode(qube_export["avatar_base64"]))

                    # Helper: restore {rel_path: base64} dict back to a directory
                    def _restore_dir_from_b64(data: dict, root_dir: Path):
                        for rel_path, b64data in data.items():
                            dest = root_dir / rel_path
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with open(dest, 'wb') as fh:
                                fh.write(base64.b64decode(b64data))

                    # Restore snapshots
                    if qube_export.get("snapshots"):
                        _restore_dir_from_b64(qube_export["snapshots"], qube_dir / "snapshots")

                    # Restore locker
                    if qube_export.get("locker"):
                        _restore_dir_from_b64(qube_export["locker"], qube_dir / "locker")

                    # Restore shared memory
                    if qube_export.get("shared_memory"):
                        _restore_dir_from_b64(qube_export["shared_memory"], qube_dir / "shared_memory")

                    # Restore skills cache
                    if qube_export.get("skills_cache") is not None:
                        with open(qube_dir / "chain" / "skills_cache.json", 'w', encoding='utf-8') as f:
                            json.dump(qube_export["skills_cache"], f, indent=2)

                    imported_count += 1
                    logger.info(f"Imported Qube: {qube_name} ({qube_id})")

                except Exception as e:
                    logger.error(f"Failed to import Qube {qube_id}: {e}", exc_info=True)
                    skipped_count += 1
                    continue

            logger.info(f"Account backup import complete: {imported_count} imported, {skipped_count} skipped")

            return {
                "success": True,
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "user_id": account_data.get("user_id", ""),
            }

        except zipfile.BadZipFile:
            return {"success": False, "error": "Invalid backup file — not a valid .qube-backup package"}
        except Exception as e:
            logger.error(f"Failed to import account backup: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Chain Sync Methods (NFT-Bundled Storage)
    # =========================================================================

    async def sync_to_chain(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Sync Qube to chain (backup to IPFS, encrypted to owner).

        Args:
            qube_id: The Qube ID to sync
            password: User's master password

        Returns:
            Dict with success, ipfs_cid, chain_length, etc.
        """
        from blockchain.chain_sync import ChainSyncService
        from crypto.keys import serialize_public_key

        try:
            # Set master key to access Qube data
            self.orchestrator.set_master_key(password)

            # Find qube directory and load qube
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None
            qube_name = None

            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    qube_name = dir_entry.name.rsplit('_', 1)[0]
                    break

            if not qube_dir:
                return {"success": False, "error": f"Qube directory not found for {qube_id}"}

            # Load qube
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Check if Qube has NFT
            nft_metadata_file = qube_dir / "chain" / "nft_metadata.json"
            if not nft_metadata_file.exists():
                return {"success": False, "error": "Qube does not have an NFT. Mint NFT first."}

            with open(nft_metadata_file, 'r', encoding='utf-8') as f:
                nft_metadata = json.load(f)

            category_id = nft_metadata.get("category_id")
            if not category_id:
                return {"success": False, "error": "No category_id in NFT metadata"}

            # Anchor any active session first
            if qube.current_session and len(qube.current_session.session_blocks) > 0:
                await qube.anchor_session()
                logger.info(f"Auto-anchored session before sync for {qube_id}")

            # Get public key
            public_key_hex = serialize_public_key(qube.public_key)

            # Get Pinata API key from secure storage
            api_keys = self.orchestrator.get_api_keys()
            pinata_jwt = api_keys.pinata_jwt

            if not pinata_jwt:
                return {"success": False, "error": "Pinata API key not configured. Please add it in Settings."}

            # Set the Pinata key in environment for IPFSUploader
            os.environ["PINATA_API_KEY"] = pinata_jwt

            # Get encryption key so chain_state can be read for chain_length
            encryption_key = self._get_qube_encryption_key(qube_dir)

            # Sync to chain
            sync_service = ChainSyncService(use_pinata=True, pinata_api_key=pinata_jwt)
            # Convert genesis_block to dict if it's a Block object
            genesis_dict = qube.genesis_block.to_dict() if hasattr(qube.genesis_block, 'to_dict') else qube.genesis_block
            result = await sync_service.sync_to_chain(
                qube_dir=str(qube_dir),
                qube_id=qube_id,
                qube_name=qube_name,
                owner_public_key_hex=public_key_hex,
                genesis_block=genesis_dict,
                user_id=self.orchestrator.user_id,
                category_id=category_id,
                encryption_key=encryption_key,
                master_password=password
            )

            return result.to_dict()

        except Exception as e:
            logger.error(f"Failed to sync Qube {qube_id} to chain: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def prepare_transfer_wc(
        self,
        qube_id: str,
        recipient_address: str,
        recipient_public_key: str,
        sender_address: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Prepare a WalletConnect-based Qube transfer.

        Runs all chain-sync prep steps (sync to IPFS, re-encrypt for recipient,
        update BCMR) then builds an unsigned WC transaction for the NFT send.
        No WIF / private key required from the user.

        Args:
            qube_id: The Qube ID to transfer
            recipient_address: Recipient's BCH address
            recipient_public_key: Recipient's public key hex (66 chars)
            sender_address: Sender's BCH address (from WalletConnect session)
            password: User's master password

        Returns:
            { success, wc_transaction, category_id, commitment, pending_transfer_id }
        """
        from blockchain.chain_sync import ChainSyncService
        from crypto.keys import serialize_public_key
        import uuid

        try:
            self.orchestrator.set_master_key(password)

            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None
            qube_name = None

            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    qube_name = dir_entry.name.rsplit('_', 1)[0]
                    break

            if not qube_dir:
                return {"success": False, "error": f"Qube directory not found for {qube_id}"}

            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            nft_metadata_file = qube_dir / "chain" / "nft_metadata.json"
            if not nft_metadata_file.exists():
                return {"success": False, "error": "Qube does not have an NFT. Cannot transfer."}

            with open(nft_metadata_file, 'r', encoding='utf-8') as f:
                nft_metadata = json.load(f)

            category_id = nft_metadata.get("category_id")
            if not category_id:
                return {"success": False, "error": "No category_id in NFT metadata"}

            # Anchor active session first
            if qube.current_session and len(qube.current_session.session_blocks) > 0:
                await qube.anchor_session()
                logger.info(f"Auto-anchored session before transfer for {qube_id}")

            # Get Pinata key for IPFS sync
            api_keys = self.orchestrator.get_api_keys()
            pinata_jwt = api_keys.pinata_jwt if api_keys else None
            if pinata_jwt:
                os.environ["PINATA_API_KEY"] = pinata_jwt

            public_key_hex = serialize_public_key(qube.public_key)

            # Steps 1-4: sync, re-encrypt for recipient, update BCMR
            sync_service = ChainSyncService(
                use_pinata=bool(pinata_jwt),
                pinata_api_key=pinata_jwt or ""
            )
            genesis_dict = qube.genesis_block.to_dict() if hasattr(qube.genesis_block, 'to_dict') else qube.genesis_block

            sync_result = await sync_service.sync_to_chain(
                qube_dir=str(qube_dir),
                qube_id=qube_id,
                qube_name=qube_name,
                owner_public_key_hex=public_key_hex,
                genesis_block=genesis_dict,
                user_id=self.orchestrator.user_id,
                category_id=category_id,
            )
            if not sync_result.success:
                return {"success": False, "error": f"IPFS sync failed: {sync_result.error}"}

            from crypto.ecies import decrypt_symmetric_key, encrypt_symmetric_key_for_recipient
            symmetric_key = decrypt_symmetric_key(sync_result.encrypted_key, qube.private_key)
            new_encrypted_key = encrypt_symmetric_key_for_recipient(symmetric_key, recipient_public_key)

            current_metadata = sync_service.bcmr_generator.get_chain_sync_metadata(category_id)
            current_version = current_metadata.get("key_version", 1) if current_metadata else 1
            sync_service.bcmr_generator.update_encrypted_key_for_transfer(
                category_id=category_id,
                new_encrypted_key=new_encrypted_key,
                new_key_version=current_version + 1
            )

            # Build unsigned WC transaction via transfer-cli.ts
            from blockchain.covenant_client import CovenantMinter
            minter = CovenantMinter(network="mainnet")
            wc_result = await minter.prepare_transfer_transaction(
                category_id=category_id,
                sender_address=sender_address,
                recipient_address=recipient_address,
            )

            if not wc_result.get("success"):
                return {"success": False, "error": f"Transfer tx build failed: {wc_result.get('error')}"}

            # Store pending transfer info on disk for finalize step
            pending_id = str(uuid.uuid4())[:8]
            pending_dir = self.orchestrator.data_dir / "pending_transfers"
            pending_dir.mkdir(parents=True, exist_ok=True)
            with open(pending_dir / f"{pending_id}.json", 'w') as f:
                json.dump({
                    "qube_id": qube_id,
                    "qube_dir": str(qube_dir),
                    "recipient_address": recipient_address,
                    "category_id": category_id,
                }, f)

            return {
                "success": True,
                "wc_transaction": wc_result["wc_transaction"],
                "category_id": category_id,
                "commitment": wc_result.get("commitment", ""),
                "pending_transfer_id": pending_id,
            }

        except Exception as e:
            logger.error(f"Failed to prepare WC transfer for {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def finalize_transfer_wc(
        self,
        pending_transfer_id: str,
        transfer_txid: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Finalize a WalletConnect Qube transfer after the wallet has signed and broadcast.

        Deletes the local Qube directory and refreshes the IPFS account backup
        so the transferred Qube is removed from the owner's backup.

        Args:
            pending_transfer_id: ID from prepare_transfer_wc
            transfer_txid: Transaction ID from WalletConnect broadcast
            password: User's master password

        Returns:
            { success, transfer_txid, recipient_address }
        """
        import shutil

        try:
            self.orchestrator.set_master_key(password)

            pending_dir = self.orchestrator.data_dir / "pending_transfers"
            pending_file = pending_dir / f"{pending_transfer_id}.json"

            if not pending_file.exists():
                return {"success": False, "error": f"No pending transfer found: {pending_transfer_id}"}

            with open(pending_file, 'r') as f:
                pending = json.load(f)

            qube_id = pending["qube_id"]
            qube_dir = pending["qube_dir"]
            recipient_address = pending["recipient_address"]

            # Remove from orchestrator cache
            if qube_id in self.orchestrator.qubes:
                del self.orchestrator.qubes[qube_id]

            # Delete local Qube data
            local_deleted = False
            try:
                shutil.rmtree(qube_dir)
                local_deleted = True
                logger.info(f"Deleted local Qube data for {qube_id} after transfer")
            except Exception as e:
                logger.warning(f"Failed to delete local Qube data for {qube_id}: {e}")

            # Clean up pending file
            try:
                pending_file.unlink()
            except Exception:
                pass

            return {
                "success": True,
                "transfer_txid": transfer_txid,
                "recipient_address": recipient_address,
                "local_deleted": local_deleted,
            }

        except Exception as e:
            logger.error(f"Failed to finalize WC transfer {pending_transfer_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def transfer_qube(
        self,
        qube_id: str,
        recipient_address: str,
        recipient_public_key: str,
        wallet_wif: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Transfer Qube to new owner.

        DESTRUCTIVE: Local Qube will be deleted after successful transfer.

        Args:
            qube_id: The Qube ID to transfer
            recipient_address: Recipient's BCH address
            recipient_public_key: Recipient's public key hex
            wallet_wif: Sender's wallet WIF for NFT transfer
            password: User's master password

        Returns:
            Dict with success, transfer_txid, etc.
        """
        from blockchain.chain_sync import ChainSyncService
        from crypto.keys import serialize_public_key

        try:
            # Set master key to access Qube data
            self.orchestrator.set_master_key(password)

            # Find qube directory and load qube
            qubes_dir = self.orchestrator.data_dir / "qubes"
            qube_dir = None
            qube_name = None

            for dir_entry in qubes_dir.iterdir():
                if dir_entry.is_dir() and qube_id in dir_entry.name:
                    qube_dir = dir_entry
                    qube_name = dir_entry.name.rsplit('_', 1)[0]
                    break

            if not qube_dir:
                return {"success": False, "error": f"Qube directory not found for {qube_id}"}

            # Load qube
            if qube_id not in self.orchestrator.qubes:
                await self.orchestrator.load_qube(qube_id)

            qube = self.orchestrator.qubes[qube_id]

            # Check if Qube has NFT
            nft_metadata_file = qube_dir / "chain" / "nft_metadata.json"
            if not nft_metadata_file.exists():
                return {"success": False, "error": "Qube does not have an NFT. Cannot transfer."}

            with open(nft_metadata_file, 'r', encoding='utf-8') as f:
                nft_metadata = json.load(f)

            category_id = nft_metadata.get("category_id")
            if not category_id:
                return {"success": False, "error": "No category_id in NFT metadata"}

            # Anchor any active session first
            if qube.current_session and len(qube.current_session.session_blocks) > 0:
                await qube.anchor_session()
                logger.info(f"Auto-anchored session before transfer for {qube_id}")

            # Get public key
            public_key_hex = serialize_public_key(qube.public_key)

            # Get Pinata API key from secure storage
            api_keys = self.orchestrator.get_api_keys()
            pinata_jwt = api_keys.pinata_jwt

            if not pinata_jwt:
                return {"success": False, "error": "Pinata API key not configured. Please add it in Settings."}

            # Set the Pinata key in environment for IPFSUploader
            os.environ["PINATA_API_KEY"] = pinata_jwt

            # Transfer
            sync_service = ChainSyncService(use_pinata=True, pinata_api_key=pinata_jwt)
            # Convert genesis_block to dict if it's a Block object
            genesis_dict = qube.genesis_block.to_dict() if hasattr(qube.genesis_block, 'to_dict') else qube.genesis_block
            result = await sync_service.transfer_qube(
                qube_dir=str(qube_dir),
                qube_id=qube_id,
                qube_name=qube_name,
                owner_private_key=qube.private_key,
                owner_public_key_hex=public_key_hex,
                recipient_address=recipient_address,
                recipient_public_key_hex=recipient_public_key,
                genesis_block=genesis_dict,
                user_id=self.orchestrator.user_id,
                category_id=category_id,
                wallet_wif=wallet_wif
            )

            # Remove from orchestrator's cache if transfer successful
            if result.success and qube_id in self.orchestrator.qubes:
                del self.orchestrator.qubes[qube_id]

            return result.to_dict()

        except Exception as e:
            logger.error(f"Failed to transfer Qube {qube_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def import_from_wallet(
        self,
        wallet_wif: str = "",
        category_id: str = "",
        password: str = ""
    ) -> Dict[str, Any]:
        """
        Import Qube from wallet.

        Supports two decryption modes:
        - WIF-based: Provide wallet_wif to decrypt via ECIES (verifies NFT ownership)
        - Password-based (Option A): Omit wallet_wif to decrypt using master password
          (requires password_wrapped_key in BCMR metadata)

        Args:
            wallet_wif: Wallet's WIF private key (optional for password-based import)
            category_id: NFT category ID to import
            password: User's master password for re-encryption (and decryption in password mode)

        Returns:
            Dict with success, qube_id, qube_name, qube_dir
        """
        from blockchain.chain_sync import ChainSyncService

        try:
            # Set master key for re-encryption
            self.orchestrator.set_master_key(password)

            target_user_dir = str(self.orchestrator.data_dir / "qubes")

            sync_service = ChainSyncService()
            result = await sync_service.import_from_wallet(
                wallet_wif=wallet_wif or None,
                category_id=category_id,
                target_user_dir=target_user_dir,
                master_password=password
            )

            return result.to_dict()

        except Exception as e:
            logger.error(f"Failed to import from wallet: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def scan_wallet_for_qubes(self, wallet_address: str) -> Dict[str, Any]:
        """
        Scan wallet for Qube NFTs.

        Args:
            wallet_address: BCH address to scan

        Returns:
            Dict with success, qubes list (excludes qubes already imported locally)
        """
        from blockchain.chain_sync import ChainSyncService

        try:
            sync_service = ChainSyncService()
            qubes = await sync_service.scan_wallet_for_qubes(wallet_address)

            # Check which qubes already exist locally, get names + block counts
            # Directory format: Name_QUBEID
            local_qube_info = {}  # qube_id -> {name, blocks, dir}
            if self.orchestrator and self.orchestrator.data_dir:
                qubes_dir = self.orchestrator.data_dir / "qubes"
                if qubes_dir.exists():
                    for d in qubes_dir.iterdir():
                        if d.is_dir() and '_' in d.name:
                            parts = d.name.rsplit('_', 1)
                            qid = parts[-1]
                            name = parts[0] if len(parts) == 2 else qid
                            # Read actual block count from chain state
                            total_blocks = 1  # genesis default
                            try:
                                encryption_key = self.orchestrator._get_encryption_key(d)
                                if encryption_key:
                                    from core.chain_state import ChainState
                                    cs = ChainState(d / "chain", encryption_key, qid)
                                    chain_data = cs.state.get("chain", {})
                                    total_blocks = chain_data.get("total_blocks", 1)
                            except Exception:
                                pass
                            local_qube_info[qid] = {"name": name, "blocks": total_blocks}

            results = []
            for q in qubes:
                d = q.to_dict()
                d["already_imported"] = q.qube_id in local_qube_info
                if q.qube_id in local_qube_info:
                    info = local_qube_info[q.qube_id]
                    d["qube_name"] = info["name"]
                    d["chain_length"] = info["blocks"]
                results.append(d)

            return {
                "success": True,
                "qubes": results
            }

        except Exception as e:
            logger.error(f"Failed to scan wallet: {e}", exc_info=True)
            return {"success": False, "error": str(e), "qubes": []}

    async def resolve_recipient_public_key(self, recipient_address: str) -> Dict[str, Any]:
        """
        Resolve recipient's public key from BCH address.

        Args:
            recipient_address: BCH address

        Returns:
            Dict with success, public_key (or None if not found)
        """
        from blockchain.chain_sync import ChainSyncService

        try:
            sync_service = ChainSyncService()
            public_key = await sync_service.resolve_recipient_public_key(recipient_address)

            return {
                "success": True,
                "public_key": public_key,
                "found": public_key is not None
            }

        except Exception as e:
            logger.error(f"Failed to resolve public key: {e}", exc_info=True)
            return {"success": False, "error": str(e), "public_key": None, "found": False}

    # =========================================================================
    # GAMES - Chess and other games
    # =========================================================================

    async def start_game(
        self,
        qube_id: str,
        game_type: str,
        opponent_type: str,
        opponent_id: Optional[str],
        qube_color: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Start a new game for a Qube.

        Args:
            qube_id: Qube to play as
            game_type: Type of game (currently only "chess")
            opponent_type: "human" or "qube"
            opponent_id: If opponent_type is "qube", the opponent's qube_id
            qube_color: "white", "black", or "random" for qube's color
            password: User's master password

        Returns:
            Dict with game_id, initial board state, player assignments
        """
        try:
            # Unlock and load qube
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Ensure game manager exists
            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized for this Qube"}

            # Determine qube's color
            import random
            if qube_color == "random":
                qube_plays_as = random.choice(["white", "black"])
            else:
                qube_plays_as = qube_color

            # For qube vs qube, load opponent
            opponent_name = self.orchestrator.user_id  # Default for human
            opponent_qube = None
            if opponent_type == "qube" and opponent_id:
                opponent_qube = await self.orchestrator.load_qube(opponent_id)
                if not opponent_qube:
                    return {"success": False, "error": f"Opponent Qube {opponent_id} not found"}
                opponent_name = opponent_qube.name

            # Create game using GameManager (correct signature)
            # Use actual user_id for human opponents so move validation works
            result = qube.game_manager.create_game(
                game_type=game_type,
                qube_plays_as=qube_plays_as,
                opponent_id=opponent_id if opponent_type == "qube" else self.orchestrator.user_id,
                opponent_type=opponent_type
            )

            if not result.get("success"):
                return result

            # CRITICAL: For Qube vs Qube, ALSO create the game in the opponent's game_manager
            # This ensures both Qubes have synchronized game state and can make moves
            if opponent_type == "qube" and opponent_qube:
                # Ensure opponent's game manager exists
                if not hasattr(opponent_qube, 'game_manager') or opponent_qube.game_manager is None:
                    return {"success": False, "error": f"Opponent Qube {opponent_id} has no game manager"}

                # Opponent plays the opposite color
                opponent_plays_as = "black" if qube_plays_as == "white" else "white"
                opponent_result = opponent_qube.game_manager.create_game(
                    game_type=game_type,
                    qube_plays_as=opponent_plays_as,
                    opponent_id=qube_id,  # Primary qube is the opponent from opponent's perspective
                    opponent_type="qube"
                )

                if not opponent_result.get("success"):
                    # Rollback: abandon the primary game
                    qube.game_manager.abandon_game()
                    return {"success": False, "error": f"Failed to create game for opponent: {opponent_result.get('error')}"}

            # Build full player info with names for frontend
            # Use 'id' key for consistency with game_manager and frontend expectations
            if qube_plays_as == "white":
                white_player = {
                    "type": "qube",
                    "id": qube.qube_id,
                    "name": qube.name
                }
                black_player = {
                    "type": opponent_type,
                    "id": opponent_id if opponent_type == "qube" else self.orchestrator.user_id,
                    "name": opponent_name
                }
            else:
                white_player = {
                    "type": opponent_type,
                    "id": opponent_id if opponent_type == "qube" else self.orchestrator.user_id,
                    "name": opponent_name
                }
                black_player = {
                    "type": "qube",
                    "id": qube.qube_id,
                    "name": qube.name
                }

            logger.info(f"Started {game_type} game {result['game_id']} for qube {qube_id}")

            return {
                "success": True,
                "game_id": result["game_id"],
                "game_type": game_type,
                "fen": result["fen"],
                "white_player": white_player,
                "black_player": black_player,
                "status": "active",
                "current_turn": "white"
            }

        except Exception as e:
            logger.error(f"Failed to start game: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_game_state(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Get current game state for a Qube.

        Args:
            qube_id: Qube ID
            password: User's master password

        Returns:
            Dict with current game state or null if no active game
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": True, "game": None, "has_active_game": False}

            game_context = qube.game_manager.get_game_context()

            if game_context is None:
                return {"success": True, "game": None, "has_active_game": False}

            return {
                "success": True,
                "has_active_game": True,
                "game": game_context
            }

        except Exception as e:
            logger.error(f"Failed to get game state: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_game_stats(self, qube_id: str, password: str, game_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get permanent game statistics for a Qube.

        Args:
            qube_id: Qube ID
            password: User's master password
            game_type: Optional specific game type (e.g., "chess"), or None for all

        Returns:
            Dict with game statistics
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": True, "stats": {}}

            stats = qube.game_manager.get_game_stats(game_type)

            return {
                "success": True,
                "stats": stats
            }

        except Exception as e:
            logger.error(f"Failed to get game stats: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def make_move(
        self,
        qube_id: str,
        move: str,
        player_type: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Make a move in an active game.

        Args:
            qube_id: Qube whose game we're playing
            move: Move in UCI or SAN notation
            player_type: "human" or "qube" indicating who is making the move
            password: User's master password

        Returns:
            Dict with move result and updated game state
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game"}

            # Determine player_id based on player_type
            if player_type == "human":
                player_id = self.orchestrator.user_id
            else:
                player_id = qube_id

            # Record the move
            result = qube.game_manager.record_move(
                move_uci=move,
                player_id=player_id
            )

            if not result["success"]:
                return result

            # Build response - map record_move output to frontend expected format
            is_game_over = result.get("is_game_over", False)
            termination = result.get("termination", "")

            response = {
                "success": True,
                "move_made": result["san"],
                "move_uci": result["move"],  # record_move returns "move" not "uci"
                "fen": result["fen"],
                "move_number": result["move_number"],
                "is_check": result.get("is_check", False),
                "is_checkmate": termination == "checkmate",
                "is_stalemate": termination == "stalemate",
                "is_draw": termination in ["insufficient_material", "fifty_move_rule", "threefold_repetition", "draw"],
                "game_over": is_game_over,
                "current_turn": result.get("turn", "black" if result["fen"].split()[1] == "b" else "white")
            }

            if is_game_over:
                response["result"] = result.get("result")
                response["termination"] = termination

            return response

        except Exception as e:
            logger.error(f"Failed to make move: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def add_game_chat(
        self,
        qube_id: str,
        message: str,
        sender_type: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Add a chat message to the active game and trigger Qube response if human.

        Args:
            qube_id: Qube whose game we're chatting in
            message: Chat message
            sender_type: "human" or "qube"
            password: User's master password

        Returns:
            Dict with success status and updated game state
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game"}

            # Add human's message
            if sender_type == "human":
                qube.game_manager.add_chat_message(
                    sender_id=self.orchestrator.user_id,
                    sender_type="human",
                    message=message
                )

                # Trigger Qube response to human's trash talk
                if qube.reasoner is None:
                    api_keys = self.orchestrator.get_api_keys()
                    qube.init_ai(api_keys.to_dict())

                # Get game context for the response
                game_context = qube.game_manager.get_game_context()

                # Ask Qube to respond to the trash talk
                prompt = f"""The human player just sent you a message during your chess game:

"{message}"

Current position: {game_context.get('fen', 'unknown')}
Your color: {game_context.get('qube_color', 'unknown')}
Move count: {game_context.get('total_moves', 0)}

Respond to their trash talk! Keep it fun and in-character. Be witty, playful, or competitive based on your personality. Don't make a chess move, just respond to their message. Keep your response brief (1-3 sentences)."""

                try:
                    response = await qube.reasoner.process_input(
                        input_message=prompt,
                        sender_id=self.orchestrator.user_id
                    )

                    # Extract just the text response (strip any tool use artifacts)
                    if response:
                        # Add Qube's response to chat
                        qube.game_manager.add_chat_message(
                            sender_id=qube_id,
                            sender_type="qube",
                            message=response.strip()
                        )
                except Exception as ai_err:
                    logger.warning(f"Failed to generate Qube chat response: {ai_err}")
            else:
                # Qube message (from move handler)
                qube.game_manager.add_chat_message(
                    sender_id=qube_id,
                    sender_type="qube",
                    message=message
                )

            # Return updated game state
            updated_context = qube.game_manager.get_game_context()
            return {
                "success": True,
                "game_state": updated_context
            }

        except Exception as e:
            logger.error(f"Failed to add game chat: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def end_game(
        self,
        qube_id: str,
        result: str,
        termination: str,
        password: str
    ) -> Dict[str, Any]:
        """
        End the active game and create GAME block.

        Args:
            qube_id: Qube whose game is ending
            result: "1-0", "0-1", "1/2-1/2", or "*"
            termination: "checkmate", "resignation", "timeout", "stalemate", etc.
            password: User's master password

        Returns:
            Dict with game summary and block info
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game"}

            # End game and get summary
            # First, get the game state to find the opponent
            active_game = qube.game_manager.active_game
            white_player_info = active_game.white_player if active_game else None
            black_player_info = active_game.black_player if active_game else None

            end_result = qube.game_manager.end_game(
                result=result,
                termination=termination
            )

            if not end_result.get("success"):
                return end_result

            game_summary = end_result["game_summary"]
            logger.info(f"Game ended: {game_summary['game_id']} with result {result}")

            # CRITICAL: Also end the game for the opponent Qube if it's a Qube vs Qube game
            # This ensures the opponent's game_manager is cleaned up and stats updated
            if white_player_info and black_player_info:
                opponent_info = black_player_info if white_player_info.get("id") == qube_id else white_player_info
                if opponent_info.get("type") == "qube":
                    opponent_qube_id = opponent_info.get("id")
                    try:
                        opponent_qube = await self.orchestrator.load_qube(opponent_qube_id)
                        if opponent_qube and opponent_qube.game_manager and opponent_qube.game_manager.active_game:
                            logger.info(f"Ending game for opponent Qube {opponent_qube_id}")
                            opponent_qube.game_manager.end_game(
                                result=result,
                                termination=termination
                            )
                    except Exception as opp_end_e:
                        logger.warning(f"Failed to end game for opponent Qube: {opp_end_e}")

            # Create GAME blocks for ALL Qube participants with multi-party signatures
            # Each Qube signs the content, and all signatures are included in each block
            from core.block import create_game_block
            from crypto.keys import serialize_public_key

            # Step 1: Load all Qube participants
            qube_participants = []  # List of (qube_id, qube_object)
            white_player = game_summary["white_player"]
            black_player = game_summary["black_player"]

            for player in [white_player, black_player]:
                if player.get("type") == "qube":
                    player_id = player["id"]
                    if player_id == qube_id:
                        qube_participants.append((player_id, qube))
                    else:
                        participant_qube = await self.orchestrator.load_qube(player_id)
                        if participant_qube:
                            qube_participants.append((player_id, participant_qube))
                        else:
                            logger.warning(f"Could not load Qube {player_id} for GAME block signing")

            if not qube_participants:
                return {"success": False, "error": "No Qube participants found for GAME block"}

            # Step 2: Create a template block to get the content hash (use first Qube for template)
            first_qube_id, first_qube = qube_participants[0]
            template_block = create_game_block(
                qube_id=first_qube_id,  # Will be replaced per-chain
                block_number=0,  # Will be replaced per-chain
                previous_hash="0" * 64,  # Will be replaced per-chain
                game_id=game_summary["game_id"],
                game_type=game_summary["game_type"],
                white_player=game_summary["white_player"],
                black_player=game_summary["black_player"],
                result=game_summary["result"],
                termination=game_summary["termination"],
                total_moves=game_summary["total_moves"],
                pgn=game_summary["pgn"],
                duration_seconds=game_summary["duration_seconds"],
                xp_earned=game_summary["xp_earned"],
                key_moments=game_summary.get("key_moments"),
                chat_log=game_summary.get("chat_log")
            )

            # Step 3: Compute content hash and have ALL Qubes sign it
            content_hash = template_block.compute_content_hash()
            participant_signatures = []

            for participant_id, participant_qube in qube_participants:
                try:
                    public_key_hex = serialize_public_key(participant_qube.public_key)
                    # Use the block's method to sign
                    template_block.add_participant_signature(
                        qube_id=participant_id,
                        public_key_hex=public_key_hex,
                        private_key=participant_qube.private_key
                    )
                    logger.info(f"Qube {participant_id} signed GAME block content")
                except Exception as sig_error:
                    logger.error(f"Failed to get signature from Qube {participant_id}: {sig_error}")

            # Get all signatures from template
            all_signatures = template_block.participant_signatures or []
            final_content_hash = template_block.content_hash

            logger.info(f"Collected {len(all_signatures)} signatures for GAME block")

            # Step 4: Create individual blocks for each Qube's chain with all signatures
            blocks_created = []
            for participant_id, participant_qube in qube_participants:
                try:
                    # Get chain info for this Qube (use memory_chain as source of truth)
                    latest_block = participant_qube.memory_chain.get_latest_block()
                    previous_hash = latest_block.block_hash if latest_block else "0" * 64
                    # Use memory_chain length as next block number (more reliable than chain_state)
                    next_block_number = participant_qube.memory_chain.get_chain_length()

                    # Create GAME block for this Qube's chain
                    game_block = create_game_block(
                        qube_id=participant_qube.qube_id,
                        block_number=next_block_number,
                        previous_hash=previous_hash,
                        game_id=game_summary["game_id"],
                        game_type=game_summary["game_type"],
                        white_player=game_summary["white_player"],
                        black_player=game_summary["black_player"],
                        result=game_summary["result"],
                        termination=game_summary["termination"],
                        total_moves=game_summary["total_moves"],
                        pgn=game_summary["pgn"],
                        duration_seconds=game_summary["duration_seconds"],
                        xp_earned=game_summary["xp_earned"],
                        key_moments=game_summary.get("key_moments"),
                        chat_log=game_summary.get("chat_log")
                    )

                    # Copy the shared content hash and all participant signatures
                    game_block.content_hash = final_content_hash
                    game_block.participant_signatures = all_signatures.copy()

                    # Also sign the full block (chain-specific) with this Qube's key
                    from crypto.signing import sign_block
                    game_block.signature = sign_block(game_block.to_dict(), participant_qube.private_key)

                    # Save block to disk BEFORE adding to chain index
                    permanent_dir = participant_qube.data_dir / "blocks" / "permanent"
                    permanent_dir.mkdir(parents=True, exist_ok=True)

                    block_type_str = game_block.block_type if isinstance(game_block.block_type, str) else game_block.block_type.value
                    filename = f"{game_block.block_number}_{block_type_str}_{game_block.timestamp}.json"
                    block_file = permanent_dir / filename

                    with open(block_file, 'w') as f:
                        json.dump(game_block.to_dict(), f, indent=2)

                    # Add to memory chain index
                    participant_qube.memory_chain.add_block(game_block)

                    # Update chain state (these methods auto-save)
                    participant_qube.chain_state.update_chain(
                        chain_length=next_block_number + 1,
                        last_block_number=next_block_number,
                        last_block_hash=game_block.block_hash
                    )
                    participant_qube.chain_state.increment_block_count("GAME")

                    blocks_created.append({
                        "qube_id": participant_id,
                        "block_number": next_block_number,
                        "signatures": len(all_signatures)
                    })

                    logger.info(f"Created GAME block #{next_block_number} for Qube {participant_id} with {len(all_signatures)} signatures")

                except Exception as block_error:
                    logger.error(f"Failed to create GAME block for Qube {participant_id}: {block_error}")

            # Use the primary qube's block number for the response
            next_block_number = blocks_created[0]["block_number"] if blocks_created else 0
            logger.info(f"Game {game_summary['game_id']} ended - created {len(blocks_created)} multi-signed GAME blocks")

            # Award XP to chess skill for all Qube participants
            from utils.skills_manager import SkillsManager
            for participant_id, participant_qube_obj in qube_participants:
                try:
                    skills_manager = SkillsManager(participant_qube_obj.chain_state)
                    xp_result = skills_manager.add_xp(
                        skill_id="chess",
                        xp_amount=game_summary["xp_earned"],
                        evidence_description=f"Completed chess game: {result} in {game_summary['total_moves']} moves"
                    )
                    logger.info(f"Awarded {game_summary['xp_earned']} XP to chess skill for {participant_id}: {xp_result}")
                except Exception as xp_error:
                    logger.warning(f"Failed to award XP to {participant_id}: {xp_error}")

            return {
                "success": True,
                "game_summary": game_summary,
                "block_number": next_block_number,
                "blocks_created": blocks_created,  # All GAME blocks created
                "xp_earned": game_summary["xp_earned"]
            }

        except Exception as e:
            logger.error(f"Failed to end game: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def abandon_game(self, qube_id: str, password: str) -> Dict[str, Any]:
        """
        Abandon the active game without creating a GAME block.

        Args:
            qube_id: Qube whose game to abandon
            password: User's master password

        Returns:
            Dict with success status
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": True, "message": "No active game to abandon"}

            # Get opponent info before abandoning
            active_game = qube.game_manager.active_game
            white_player_info = active_game.white_player if active_game else None
            black_player_info = active_game.black_player if active_game else None

            qube.game_manager.abandon_game()

            logger.info(f"Abandoned game for qube {qube_id}")

            # Also abandon the opponent's game if it's a Qube vs Qube game
            if white_player_info and black_player_info:
                opponent_info = black_player_info if white_player_info.get("id") == qube_id else white_player_info
                if opponent_info.get("type") == "qube":
                    opponent_qube_id = opponent_info.get("id")
                    try:
                        opponent_qube = await self.orchestrator.load_qube(opponent_qube_id)
                        if opponent_qube and opponent_qube.game_manager and opponent_qube.game_manager.active_game:
                            logger.info(f"Abandoning game for opponent Qube {opponent_qube_id}")
                            opponent_qube.game_manager.abandon_game()
                    except Exception as opp_abandon_e:
                        logger.warning(f"Failed to abandon game for opponent Qube: {opp_abandon_e}")

            return {"success": True, "message": "Game abandoned"}

        except Exception as e:
            logger.error(f"Failed to abandon game: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def resign_game(self, qube_id: str, password: str, resigning_player: str) -> Dict[str, Any]:
        """
        Resign the current game (counts as a loss for the resigning player).

        Args:
            qube_id: Qube whose game to resign
            password: User's master password
            resigning_player: 'white' or 'black'

        Returns:
            Dict with success status and game summary
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game to resign"}

            result = qube.game_manager.resign_game(resigning_player)

            if result.get("success"):
                logger.info(f"Game resigned by {resigning_player} for qube {qube_id}")

            return result

        except Exception as e:
            logger.error(f"Failed to resign game: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def offer_draw(self, qube_id: str, password: str, offering_player: str) -> Dict[str, Any]:
        """
        Offer a draw in the current game.

        Args:
            qube_id: Qube whose game to offer draw in
            password: User's master password
            offering_player: 'white' or 'black'

        Returns:
            Dict with success status
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game"}

            result = qube.game_manager.offer_draw(offering_player)
            return result

        except Exception as e:
            logger.error(f"Failed to offer draw: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def respond_to_draw(self, qube_id: str, password: str, accepting: bool, responding_player: str) -> Dict[str, Any]:
        """
        Respond to a draw offer.

        Args:
            qube_id: Qube whose game to respond to draw in
            password: User's master password
            accepting: True to accept, False to decline
            responding_player: 'white' or 'black'

        Returns:
            Dict with success status (and game summary if accepted)
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game"}

            result = qube.game_manager.respond_to_draw(accepting, responding_player)

            if result.get("success") and accepting:
                logger.info(f"Draw accepted for qube {qube_id}")

            return result

        except Exception as e:
            logger.error(f"Failed to respond to draw: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _calculate_trash_talk_chance(self, game_context: Dict[str, Any], last_move_info: Optional[Dict] = None) -> tuple:
        """
        Calculate probability of trash talk based on game events.

        Returns:
            tuple: (should_talk: bool, context_hint: str)
        """
        import random

        base_chance = 0.25  # 25% base chance for normal moves
        context_hint = ""

        # Check game state
        is_check = game_context.get("is_check", False)
        total_moves = game_context.get("total_moves", 0)

        # Calculate material from FEN
        fen = game_context.get("fen", "")
        piece_values = {'p': 1, 'n': 3, 'b': 3, 'r': 5, 'q': 9}
        white_material = 0
        black_material = 0

        if fen:
            board_part = fen.split(' ')[0]
            for char in board_part:
                if char.lower() in piece_values:
                    if char.isupper():
                        white_material += piece_values[char.lower()]
                    else:
                        black_material += piece_values[char]

        qube_color = game_context.get("qube_color", "white")
        qube_material = white_material if qube_color == "white" else black_material
        opp_material = black_material if qube_color == "white" else white_material
        material_diff = qube_material - opp_material

        # Adjust chance based on events
        if is_check:
            base_chance += 0.40  # +40% if opponent is in check
            context_hint = "You just put your opponent in check!"

        if material_diff >= 3:
            base_chance += 0.25  # +25% if winning by 3+ material
            context_hint = "You're ahead in material - feeling confident!"
        elif material_diff <= -3:
            base_chance += 0.20  # +20% if losing (excuses/determination)
            context_hint = "You're behind but fighting back!"

        # Opening phase commentary (moves 1-6)
        if total_moves <= 6:
            base_chance += 0.15
            if not context_hint:
                context_hint = "Opening phase - establish your style!"

        # Check for pending human chat messages to respond to (always respond)
        chat_messages = game_context.get("chat_messages", [])
        if chat_messages:
            last_msg = chat_messages[-1]
            if last_msg.get("sender_type") == "human":
                return True, f"The human just said: \"{last_msg.get('message', '')[:100]}\" - respond to them!"

        # Cap at 90%
        final_chance = min(0.90, base_chance)

        should_talk = random.random() < final_chance
        return should_talk, context_hint

    async def request_qube_move(
        self,
        qube_id: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Request a Qube to make a chess move in the active game.

        This sends a message to the Qube asking it to analyze the position
        and make its move using the chess_move tool.

        Args:
            qube_id: Qube to request move from
            password: User's master password

        Returns:
            Dict with move result from Qube
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            if not hasattr(qube, 'game_manager') or qube.game_manager is None:
                return {"success": False, "error": "Game manager not initialized"}

            if qube.game_manager.active_game is None:
                return {"success": False, "error": "No active game"}

            # Get game context for the prompt
            game_context = qube.game_manager.get_game_context()
            if not game_context:
                return {"success": False, "error": "Could not get game context"}

            # Ensure AI is initialized
            if qube.reasoner is None:
                api_keys = self.orchestrator.get_api_keys()
                qube.init_ai(api_keys.to_dict())

            # Get recent chat messages to check for memory triggers
            recent_chat = None
            chat_messages = game_context.get("chat_messages", [])
            if chat_messages:
                # Get most recent human message
                human_messages = [m for m in chat_messages if m.get("sender_type") == "human"]
                if human_messages:
                    recent_chat = human_messages[-1].get("message")

            # Get game state BEFORE the move for comparison
            context_before = qube.game_manager.get_game_context()
            fen_before = context_before.get("fen") if context_before else None
            logger.info(f"[CHESS DEBUG] FEN before AI call: {fen_before}")

            # Use lightweight game action processing (no memory search unless triggered)
            response = None
            ai_error = None
            try:
                response = await qube.reasoner.process_game_action(
                    game_context=game_context,
                    user_chat=recent_chat  # Will trigger memory search only if needed
                )
                logger.info(f"[CHESS DEBUG] AI response: {response[:500] if response else 'None'}")
            except Exception as ai_e:
                # AI processing failed, but the chess move might still have succeeded
                logger.warning(f"[CHESS DEBUG] AI processing error (may be ok if move succeeded): {ai_e}")
                ai_error = str(ai_e)

            # Get updated game state after the move
            updated_context = qube.game_manager.get_game_context()
            fen_after = updated_context.get("fen") if updated_context else None
            logger.info(f"[CHESS DEBUG] FEN after AI call: {fen_after}")
            logger.info(f"[CHESS DEBUG] FEN changed: {fen_before != fen_after}")

            # If the FEN changed, the move was successful even if AI had issues
            if fen_before != fen_after:
                logger.info("[CHESS DEBUG] Move succeeded (FEN changed), returning success")

                # CRITICAL: Sync move to opponent's game_manager for Qube vs Qube mode
                # This ensures both Qubes have synchronized game state
                active_game = qube.game_manager.active_game
                if active_game:
                    # Check if opponent is a Qube
                    white_player = active_game.white_player
                    black_player = active_game.black_player

                    # Determine which player is the opponent
                    if white_player.get("id") == qube.qube_id:
                        opponent_info = black_player
                    else:
                        opponent_info = white_player

                    # If opponent is a Qube, sync the game state
                    if opponent_info.get("type") == "qube":
                        opponent_qube_id = opponent_info.get("id")
                        logger.info(f"[CHESS DEBUG] Syncing move to opponent Qube: {opponent_qube_id}")
                        try:
                            opponent_qube = await self.orchestrator.load_qube(opponent_qube_id)
                            if opponent_qube and opponent_qube.game_manager and opponent_qube.game_manager.active_game:
                                # Sync the FEN, moves, and chat messages
                                opponent_game = opponent_qube.game_manager.active_game
                                opponent_game.fen = active_game.fen
                                opponent_game.moves = active_game.moves.copy()
                                opponent_game.chat_messages = active_game.chat_messages.copy()
                                opponent_game.last_move_at = active_game.last_move_at
                                opponent_qube.game_manager._save_game_state()
                                logger.info(f"[CHESS DEBUG] Synced game state to opponent Qube {opponent_qube_id}")
                        except Exception as sync_e:
                            logger.warning(f"[CHESS DEBUG] Failed to sync to opponent: {sync_e}")

                return {
                    "success": True,
                    "qube_response": response or "Move completed",
                    "game_state": updated_context,
                    "ai_warning": ai_error  # Include warning if there was one
                }
            elif ai_error:
                # FEN didn't change AND there was an error - actual failure
                return {"success": False, "error": ai_error}
            else:
                # No error but FEN didn't change - shouldn't happen
                return {
                    "success": True,
                    "qube_response": response,
                    "game_state": updated_context
                }

        except Exception as e:
            logger.error(f"Failed to request qube move: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Wallet Management Methods
    # =========================================================================

    def _get_wallet_info_from_genesis(self, genesis_block) -> Optional[Dict[str, Any]]:
        """
        Extract wallet info from genesis block, handling both SimpleNamespace and Block objects.

        Args:
            genesis_block: Genesis block (can be SimpleNamespace or Block object)

        Returns:
            Wallet info dict or None if not found
        """
        if not genesis_block:
            return None

        # Try different access patterns
        wallet_info = None

        # Pattern 1: Block object with content dict
        if hasattr(genesis_block, 'content') and isinstance(genesis_block.content, dict):
            wallet_info = genesis_block.content.get("wallet")
        # Pattern 2: SimpleNamespace with wallet attribute
        elif hasattr(genesis_block, 'wallet'):
            wallet_info = genesis_block.wallet

        if wallet_info is None:
            return None

        # Convert SimpleNamespace to dict if needed
        if hasattr(wallet_info, '__dict__') and not isinstance(wallet_info, dict):
            wallet_info = vars(wallet_info)

        return wallet_info if isinstance(wallet_info, dict) else None

    async def get_qube_wallet_info(
        self,
        qube_id: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Get wallet address, balance, and pending transactions for a Qube.

        Reads from chain_state (single source of truth) for instant response.
        Balance is synced in background when qube loads.

        Args:
            qube_id: Qube ID
            password: User's master password

        Returns:
            Dict with wallet info including address, balance, pending_txs
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Check if wallet is initialized
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            p2sh_address = wallet_info.get("p2sh_address")
            if not p2sh_address:
                return {"success": False, "error": "Wallet address not found in genesis block"}

            logger.info(f"=== GET_WALLET_INFO for {qube_id} ===")
            logger.info(f"P2SH address from genesis: {p2sh_address}")

            # Get cached data immediately (non-blocking)
            import time
            financial = qube.chain_state.state.get("financial", {})
            wallet_data = financial.get("wallet", {})
            last_sync = wallet_data.get("last_sync")

            # Check if cache is stale or address changed (e.g. after migration)
            cached_address = wallet_data.get("address")
            needs_sync = (
                last_sync is None or
                (time.time() - last_sync) > 300 or  # 5 minutes
                (cached_address and cached_address != p2sh_address)  # address changed
            )
            if cached_address and cached_address != p2sh_address:
                # Address changed (migration) — clear stale balance
                wallet_data = {}
                logger.info(f"Wallet address changed from {cached_address[:30]}... to {p2sh_address[:30]}..., clearing cache")

            logger.info(f"Cached balance: {wallet_data.get('balance_satoshis', 'NOT SET')} sats")
            logger.info(f"Last sync: {last_sync}, needs_sync: {needs_sync}")

            # If stale, fetch balance directly (blocking but reliable)
            if needs_sync:
                import requests
                try:
                    logger.info(f"Fetching fresh balance for {qube_id}...")
                    addr_full = p2sh_address if ":" in p2sh_address else f"bitcoincash:{p2sh_address}"
                    url = f"https://api.blockchair.com/bitcoin-cash/dashboards/address/{addr_full}"
                    resp = requests.get(url, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        if "data" in data and addr_full in data["data"]:
                            addr_data = data["data"][addr_full].get("address", {})
                            fresh_p2sh_balance = addr_data.get("balance", 0)
                            logger.info(f"Fresh balance for {qube_id}: {fresh_p2sh_balance} sats")

                            # Update cache
                            wallet_data["balance_satoshis"] = fresh_p2sh_balance
                            wallet_data["balance_bch"] = fresh_p2sh_balance / 100_000_000
                            wallet_data["last_sync"] = time.time()
                            wallet_data["address"] = p2sh_address
                            financial["wallet"] = wallet_data
                            qube.chain_state.state["financial"] = financial
                            qube.chain_state._save()
                    elif resp.status_code == 430:
                        logger.warning(f"Blockchair rate limited for {qube_id}")
                except Exception as e:
                    logger.warning(f"Balance fetch failed for {qube_id}: {e}")

            p2sh_balance = wallet_data.get("balance_satoshis", 0)

            # Get pending transactions from chain_state (source of truth)
            pending_txs = qube.chain_state.get_pending_transactions()

            return {
                "success": True,
                "qube_id": qube_id,
                "wallet_address": p2sh_address,
                "owner_pubkey": wallet_info.get("owner_pubkey"),
                "qube_pubkey": wallet_info.get("qube_pubkey"),
                "balance_sats": p2sh_balance,
                "balance_bch": p2sh_balance / 100_000_000,
                "last_sync": wallet_data.get("last_sync"),
                "pending_transactions": [
                    {
                        "tx_id": tx.get("tx_id"),
                        "outputs": tx.get("outputs", []),
                        "total_amount": tx.get("total_amount", 0),
                        "fee": tx.get("fee", 0),
                        "status": tx.get("status"),
                        "created_at": datetime.fromtimestamp(tx.get("created_at", 0)).isoformat() if tx.get("created_at") else None,
                        "expires_at": datetime.fromtimestamp(tx.get("expires_at")).isoformat() if tx.get("expires_at") else None,
                        "memo": tx.get("memo")
                    }
                    for tx in pending_txs
                ]
            }

        except Exception as e:
            logger.error(f"Failed to get wallet info: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def sync_wallet_balances(self, qube) -> None:
        """
        Sync wallet balances from blockchain to chain_state.

        Called automatically when qube loads. Updates chain_state with
        current balances so UI reads are instant.

        Args:
            qube: Loaded Qube instance
        """
        import aiohttp

        try:
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return

            p2sh_address = wallet_info.get("p2sh_address")

            if not p2sh_address:
                return

            # Fetch P2SH wallet balance from Blockchair (only API that supports P2SH)
            p2sh_balance = None
            p2sh_addr_full = p2sh_address if ":" in p2sh_address else f"bitcoincash:{p2sh_address}"

            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    api_url = f"https://api.blockchair.com/bitcoin-cash/dashboards/address/{p2sh_addr_full}"
                    logger.info(f"Fetching P2SH balance from Blockchair: {p2sh_addr_full}")
                    async with session.get(api_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if "data" in data and p2sh_addr_full in data["data"]:
                                addr_data = data["data"][p2sh_addr_full].get("address", {})
                                p2sh_balance = addr_data.get("balance", 0)
                                logger.info(f"Blockchair P2SH balance: {p2sh_balance} sats")
                        elif resp.status == 430:
                            logger.warning(f"Blockchair rate limited for P2SH: {p2sh_addr_full}")
                        else:
                            logger.warning(f"Blockchair returned status {resp.status} for P2SH")
            except Exception as e:
                logger.warning(f"Balance sync failed: {e}")

            # Update chain_state only if we got valid data
            import time
            if p2sh_balance is not None:
                financial = qube.chain_state.state.setdefault("financial", {})
                wallet_data = financial.setdefault("wallet", {})
                wallet_data["balance_satoshis"] = p2sh_balance
                wallet_data["balance_bch"] = p2sh_balance / 100_000_000
                wallet_data["last_sync"] = time.time()
                wallet_data["address"] = p2sh_address
                qube.chain_state._save()

            logger.debug("wallet_balance_synced", p2sh=p2sh_balance)

        except Exception as e:
            logger.warning(f"Failed to sync wallet balances: {e}")

    async def get_context_preview(
        self,
        qube_id: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Get preview of what's in the Qube's active context and short-term memory.

        This shows the user everything that would be injected into the AI's context
        window when processing a message.

        Args:
            qube_id: Qube ID
            password: User's master password

        Returns:
            Dict with active_context and short_term_memory sections
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Note: Do NOT start a session just for viewing context preview
            # Session should only start when actually chatting with the Qube

            # Reload chain_state and memory_chain from disk to pick up any changes
            # (The qube may be cached in orchestrator with stale values)
            qube.chain_state.reload()
            qube.memory_chain.reload()

            # Get raw chain_state directly from file (unmodified)
            raw_chain_state = {}
            try:
                raw_chain_state = qube.chain_state.get_sections(None)  # All sections, no modifications
            except Exception as raw_err:
                logger.warning(f"Failed to get raw chain_state: {raw_err}")

            # Get the ACTUAL last prompt sent to the model (from saved debug data)
            from ai.reasoner import get_debug_prompt
            last_actual_prompt = get_debug_prompt(qube_id)

            # Build active context (what AI sees after handler enhancements)
            active_context = await self._build_active_context_preview(qube)

            # Build short-term memory (blocks in context window)
            short_term_memory = await self._build_short_term_memory_preview(qube)

            # Compute content hash for cache invalidation
            # Hash key fields that would indicate meaningful changes
            import hashlib
            settings = active_context.get("settings", {})
            hash_data = {
                "stats_sessions": active_context.get("stats", {}).get("total_sessions", 0) if active_context.get("stats") else 0,
                "stats_tokens": active_context.get("stats", {}).get("total_tokens", 0) if active_context.get("stats") else 0,
                "chain_blocks": active_context.get("chain", {}).get("total_blocks", 0) if active_context.get("chain") else 0,
                "session_messages": active_context.get("session", {}).get("messages_this_session", 0) if active_context.get("session") else 0,
                "relationships_count": active_context.get("relationships", {}).get("count", 0),
                "skills_xp": active_context.get("skills", {}).get("totals", {}).get("total_xp", 0),
                "owner_info_count": active_context.get("owner_info", {}).get("total_fields", 0) if active_context.get("owner_info") else 0,
                "qube_profile_count": active_context.get("qube_profile", {}).get("total_fields", 0) if active_context.get("qube_profile") else 0,
                "stm_semantic": short_term_memory.get("semantic_recalls", {}).get("count", 0),
                "stm_recent": short_term_memory.get("recent_permanent", {}).get("count", 0),
                "stm_session": short_term_memory.get("session", {}).get("count", 0),
                # Include settings fields so cache invalidates when model mode changes
                "settings_model_mode": settings.get("model_mode", "Manual"),
                "settings_tts_enabled": settings.get("tts_enabled", False),
                "settings_current_model": settings.get("current_model", ""),
            }
            content_hash = hashlib.md5(json.dumps(hash_data, sort_keys=True).encode()).hexdigest()[:12]

            return {
                "success": True,
                "raw_chain_state": raw_chain_state,  # Unmodified chain_state.json contents
                "active_context": active_context,     # What AI sees (with handler enhancements)
                "short_term_memory": short_term_memory,
                "content_hash": content_hash,
                "last_actual_prompt": last_actual_prompt  # ACTUAL messages sent to the model (includes switch notices, etc.)
            }

        except Exception as e:
            logger.error(f"Failed to get context preview: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _build_active_context_preview(self, qube) -> Dict[str, Any]:
        """
        Build preview of active context by calling the same handler the AI uses.

        This ensures the Debug Modal shows exactly what the AI sees when it
        calls get_system_state.
        """
        from ai.tools.handlers import get_system_state_handler

        # Call the same handler the AI uses - no sections means get all
        result = await get_system_state_handler(qube, {"sections": []})

        if result.get("success"):
            return result.get("sections", {})
        else:
            # Return error info if handler failed
            return {"error": result.get("error", "Failed to get chain state")}

    async def _build_short_term_memory_preview(self, qube) -> Dict[str, Any]:
        """
        Build preview of short-term memory (blocks in context window).
        """
        from ai.tools.memory_search import intelligent_memory_search

        # Semantic recalls (from last query)
        semantic_recalls = []
        recalled_block_numbers = set()

        try:
            # Get recent context to search
            session = qube.current_session
            recent_messages = []
            if session and hasattr(session, 'session_blocks') and session.session_blocks:
                for block in session.session_blocks[-3:]:
                    content = block.content if isinstance(block.content, dict) else {}
                    # MESSAGE blocks use "message_body" not "message"
                    if "message_body" in content:
                        recent_messages.append(content["message_body"][:200])
                    elif "message" in content:
                        recent_messages.append(content["message"][:200])

            if recent_messages:
                query = " ".join(recent_messages)
                results = await intelligent_memory_search(
                    qube=qube,
                    query=query,
                    context={"query_type": "context_preview"},
                    top_k=5
                )

                for result in results:
                    block = result.block
                    block_num = block.get("block_number", -1)
                    recalled_block_numbers.add(block_num)
                    semantic_recalls.append({
                        "block_number": block_num,
                        "block_type": block.get("block_type", "UNKNOWN"),
                        "relevance_score": round(result.score, 2),
                        "preview": self._get_block_preview(block)
                    })
        except Exception as e:
            logger.warning(f"Failed to get semantic recalls: {e}")

        # Recent permanent blocks (with summary exclusion awareness)
        recent_blocks = []
        recent_blocks_content_chars = 0
        try:
            from ai.reasoner import QubeReasoner
            reasoner = QubeReasoner(qube)
            # Get 15 most recent blocks (with SUMMARY replacement logic)
            # SUMMARY blocks compress ~20 blocks each, so 15 summaries = ~300 blocks of context
            permanent_blocks = reasoner._get_recent_permanent_blocks(
                limit=15,
                recalled_block_numbers=recalled_block_numbers
            )

            for block in permanent_blocks:
                content = block.content if isinstance(block.content, dict) else {}
                if block.encrypted and "ciphertext" in content:
                    try:
                        content = qube.decrypt_block_content(content)
                    except:
                        content = {"preview": "[Encrypted]"}

                # Track actual content size for token estimation
                try:
                    content_str = json.dumps(content, default=str) if isinstance(content, dict) else str(content)
                    recent_blocks_content_chars += len(content_str)
                except:
                    recent_blocks_content_chars += 500  # Fallback estimate

                recent_blocks.append({
                    "block_number": block.block_number,
                    "block_type": block.block_type if isinstance(block.block_type, str) else block.block_type.value,
                    "timestamp": block.timestamp,
                    "is_summary": block.block_type == "SUMMARY",
                    "preview": self._get_block_preview({"content": content, "block_type": block.block_type})
                })
        except Exception as e:
            logger.warning(f"Failed to get recent permanent blocks: {e}")

        # Session blocks
        session_blocks = []
        session_blocks_content_chars = 0
        try:
            session = qube.current_session
            blocks_to_process = []

            # Try to get blocks from active session first
            if session and hasattr(session, 'session_blocks') and session.session_blocks:
                blocks_to_process = session.session_blocks[-10:]
            else:
                # Fallback: Load session blocks directly from disk
                # This allows viewing session blocks even without an active session
                from pathlib import Path
                from core.block import Block
                session_dir = Path(qube.data_dir) / "blocks" / "session"
                if session_dir.exists():
                    block_files = sorted(session_dir.glob("*.json"))
                    for block_file in block_files[-10:]:  # Last 10 blocks
                        try:
                            with open(block_file, 'r') as f:
                                block_data = json.load(f)
                                block = Block.from_dict(block_data)
                                blocks_to_process.append(block)
                        except Exception as load_err:
                            logger.debug(f"Failed to load session block {block_file}: {load_err}")

            for block in blocks_to_process:
                content = block.content if isinstance(block.content, dict) else {}

                # Track actual content size for token estimation
                try:
                    content_str = json.dumps(content, default=str) if isinstance(content, dict) else str(content)
                    session_blocks_content_chars += len(content_str)
                except:
                    session_blocks_content_chars += 500  # Fallback estimate

                session_blocks.append({
                    "block_number": block.block_number,
                    "block_type": block.block_type if isinstance(block.block_type, str) else block.block_type.value,
                    "timestamp": block.timestamp,
                    "preview": self._get_block_preview({"content": content, "block_type": block.block_type})
                })
        except Exception as e:
            logger.warning(f"Failed to get session blocks: {e}")

        # Estimate total tokens in short-term memory
        # Use ~4 characters per token as a rough approximation
        total_chars = 0

        # Add actual content sizes from blocks we tracked
        total_chars += recent_blocks_content_chars
        total_chars += session_blocks_content_chars

        # Semantic recalls - estimate from preview * 10 (previews are very truncated)
        # Each recalled block typically has 500-2000 chars of actual content
        for block in semantic_recalls:
            preview_len = len(block.get("preview", ""))
            # Estimate actual content is ~10x the preview (preview is 100 chars, content is ~1000)
            total_chars += max(preview_len * 10, 500)

        # Add estimate for active context (genesis prompt, system instructions, etc.)
        # This is typically 500-1500 tokens worth of content
        genesis_prompt = qube.genesis_block.genesis_prompt if qube.genesis_block else ""
        total_chars += len(genesis_prompt)
        total_chars += 1000  # Base system prompt overhead

        # Add estimate for owner info context from chain_state
        # Each field is roughly 50-100 chars (key + value + formatting)
        try:
            summary = qube.chain_state.get_owner_info_summary()

            # Estimate: each injectable field is ~75 chars on average
            # Only count non-secret fields (public + private)
            injectable_count = summary.get("public_fields", 0) + summary.get("private_fields", 0)
            total_chars += injectable_count * 75
        except Exception:
            pass  # If owner info fails, just skip the estimate

        # Add estimate for qube profile context (self-identity)
        # Each field is roughly 50-100 chars (key + value + formatting)
        try:
            profile_summary = qube.chain_state.get_qube_profile_summary()

            # Estimate: each injectable field is ~75 chars on average
            # Only count non-secret fields (public + private)
            injectable_count = profile_summary.get("public_fields", 0) + profile_summary.get("private_fields", 0)
            total_chars += injectable_count * 75
        except Exception:
            pass  # If qube profile fails, just skip the estimate

        estimated_tokens = total_chars // 4

        # Get max context window for this qube's AI model
        # Try multiple possible attribute names
        model_name = getattr(qube, 'current_ai_model', None)
        if not model_name:
            model_name = getattr(qube, 'ai_model', None)
        if not model_name and qube.genesis_block:
            model_name = getattr(qube.genesis_block, 'ai_model', None)
        if not model_name:
            model_name = 'unknown'
        max_context_window = self._get_model_context_window(model_name)

        return {
            "semantic_recalls": {
                "count": len(semantic_recalls),
                "blocks": semantic_recalls
            },
            "recent_permanent": {
                "count": len(recent_blocks),
                "blocks": recent_blocks
            },
            "session": {
                "count": len(session_blocks),
                "blocks": session_blocks
            },
            "estimated_tokens": estimated_tokens,
            "max_context_window": max_context_window
        }

    def _get_model_context_window(self, model_name: str | None) -> int:
        """Get the context window size for a given model name."""
        if not model_name:
            return 128000  # Default fallback

        # Consolidated context windows from all providers
        # Includes both full API names and short aliases
        CONTEXT_WINDOWS = {
            # Anthropic - full names
            "claude-sonnet-4-5-20250929": 200000,
            "claude-opus-4-1-20250805": 200000,
            "claude-opus-4-20250514": 200000,
            "claude-sonnet-4-20250514": 1000000,
            "claude-3-7-sonnet-20250219": 200000,
            "claude-3-5-haiku-20241022": 200000,
            "claude-3-haiku-20240307": 200000,
            # Anthropic - short aliases
            "claude-sonnet-4.5": 200000,
            "claude-opus-4.1": 200000,
            "claude-opus-4": 200000,
            "claude-sonnet-4": 1000000,
            "claude-3.5-sonnet": 200000,
            "claude-3.5-haiku": 200000,
            "claude-3-haiku": 200000,
            # OpenAI - these are already short names
            "gpt-5": 256000,
            "gpt-5-mini": 128000,
            "gpt-5-nano": 64000,
            "gpt-5-codex": 256000,
            "gpt-4.1": 1000000,
            "gpt-4.1-mini": 128000,
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "o4-mini": 128000,
            "o3-mini": 128000,
            "o1": 200000,
            # Google - these are already short names
            "gemini-2.5-pro": 2000000,
            "gemini-2.5-flash": 1000000,
            "gemini-2.5-flash-lite": 1000000,
            "gemini-2.0-flash": 1000000,
            "gemini-1.5-pro": 2000000,
            # DeepSeek - these are already short names
            "deepseek-chat": 64000,
            "deepseek-reasoner": 64000,
            # Venice - private AI models
            "venice-uncensored": 131072,
            "llama-3.3-70b": 65536,
            "qwen3-235b": 131072,
            "qwen3-4b": 131072,
            "deepseek-r1-llama-70b": 131072,
            "mistral-31-24b": 131072,
            # Perplexity - these are already short names
            "sonar-pro": 127000,
            "sonar": 127000,
            "sonar-reasoning-pro": 127000,
            "sonar-reasoning": 127000,
            "sonar-deep-research": 127000,
            # Ollama (local) - these are already short names
            "llama3.3:70b": 128000,
            "qwen3:235b": 32768,
            "qwen3:30b": 32768,
            "qwen2.5:7b": 32768,
            "deepseek-r1:8b": 32768,
            "phi4:14b": 16384,
            "gemma2:9b": 8192,
            "mistral:7b": 8192,
            "codellama:7b": 16384,
        }
        # Default to 128K if model not found
        return CONTEXT_WINDOWS.get(model_name, 128000)

    def _get_block_preview(self, block: dict) -> str:
        """Get a short preview of block content."""
        content = block.get("content", {})
        if isinstance(content, str):
            return content[:100] + "..." if len(content) > 100 else content

        block_type = block.get("block_type", "")

        # SUMMARY block
        if block_type == "SUMMARY":
            summary = content.get("summary_text") or content.get("summary", "")
            return summary[:100] + "..." if len(summary) > 100 else summary

        # MESSAGE block - try various field names (message_body is the standard field)
        # Skip generic/placeholder values
        skip_values = {'default', 'none', 'null', 'undefined', 'n/a', 'na', ''}

        for field in ['message_body', 'message', 'response', 'user_message', 'assistant_message', 'text']:
            msg = content.get(field)
            if msg and isinstance(msg, str) and msg.strip().lower() not in skip_values:
                return msg[:100] + "..." if len(msg) > 100 else msg

        # Skip these metadata fields when looking for preview content
        skip_fields = {
            'role', 'type', 'block_type', 'message_type', 'recipient_id',
            'sender_id', 'conversation_id', 'requires_response', 'participants',
            'turn_number', 'speaker_id', 'speaker_name', 'message_encrypted_for_recipient',
            'session_id', 'timestamp', 'encrypted', 'qube_id', 'block_number'
        }

        # Try to get any meaningful string value from the content dict
        if isinstance(content, dict):
            for key, val in content.items():
                if key not in skip_fields and isinstance(val, str):
                    val_lower = val.strip().lower()
                    if val_lower not in skip_values and len(val.strip()) > 5:
                        return val[:100] + "..." if len(val) > 100 else val

        return ""

    def _calculate_skill_level(self, xp: float) -> int:
        """Calculate skill level from XP."""
        if xp < 100:
            return 1
        elif xp < 500:
            return 2
        elif xp < 1500:
            return 3
        elif xp < 5000:
            return 4
        else:
            return 5

    async def get_system_prompt_preview(
        self,
        qube_id: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Get the ACTUAL system prompt that was/is being sent to the AI.

        This returns the exact messages from the last API call (saved debug prompt),
        including any runtime modifications like model switch notices.

        Args:
            qube_id: Qube ID
            password: User's master password

        Returns:
            Dict with system_prompt, messages, model, provider, qube_name, qube_id
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Get the ACTUAL last prompt sent to the model (from saved debug data)
            from ai.reasoner import get_debug_prompt
            saved_prompt = get_debug_prompt(qube_id)

            if saved_prompt and saved_prompt.get("messages"):
                # Return the actual messages that were sent
                messages = saved_prompt.get("messages", [])

                # Extract system prompt from messages for convenience
                system_prompt = ""
                for msg in messages:
                    if msg.get("role") == "system":
                        system_prompt = msg.get("content", "")
                        break

                return {
                    "success": True,
                    "system_prompt": system_prompt,
                    "messages": messages,
                    "model": saved_prompt.get("model", "unknown"),
                    "provider": saved_prompt.get("provider", "unknown"),
                    "qube_name": saved_prompt.get("qube_name", qube.name),
                    "qube_id": saved_prompt.get("qube_id", qube_id),
                    "switched_from": saved_prompt.get("switched_from"),  # Include switch info if present
                    "timestamp": saved_prompt.get("timestamp"),
                }

            # Fallback: No saved prompt yet, build a fresh preview
            qube.chain_state.reload()
            from ai.reasoner import QubeReasoner
            reasoner = QubeReasoner(qube)
            preview = await reasoner.build_system_prompt_preview()

            return {
                "success": True,
                "system_prompt": preview.get("system_prompt", ""),
                "messages": preview.get("messages", []),
                "model": preview.get("model", "unknown"),
                "provider": preview.get("provider", "unknown"),
                "qube_name": preview.get("qube_name", ""),
                "qube_id": preview.get("qube_id", qube_id),
                "relevant_memories_count": preview.get("relevant_memories_count", 0),
                "permanent_blocks_count": preview.get("permanent_blocks_count", 0),
            }

        except Exception as e:
            logger.error(f"Failed to get system prompt preview: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def propose_wallet_transaction(
        self,
        qube_id: str,
        to_address: str,
        amount_satoshis: int,
        memo: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Qube proposes a wallet transaction that requires owner approval.

        Args:
            qube_id: Qube ID
            to_address: BCH address to send to
            amount_satoshis: Amount in satoshis
            memo: Transaction memo/reason
            password: User's master password

        Returns:
            Dict with pending transaction info
        """
        from blockchain.wallet_tx import WalletTransactionManager

        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Check if wallet is initialized
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            # Initialize wallet transaction manager
            wallet_manager = qube.wallet_manager

            # Propose the transaction (Qube signs, waits for owner approval)
            pending_tx = await wallet_manager.propose_send(to_address, amount_satoshis, memo)

            return {
                "success": True,
                "pending_tx": {
                    "tx_id": pending_tx.tx_id,
                    "outputs": pending_tx.outputs,
                    "total_amount": pending_tx.total_amount,
                    "fee": pending_tx.fee,
                    "status": pending_tx.status,
                    "created_at": datetime.fromtimestamp(pending_tx.created_at).isoformat(),
                    "expires_at": datetime.fromtimestamp(pending_tx.expires_at).isoformat() if pending_tx.expires_at else None,
                    "memo": pending_tx.memo
                }
            }

        except Exception as e:
            logger.error(f"Failed to propose wallet transaction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def approve_wallet_transaction(
        self,
        qube_id: str,
        pending_tx_id: str,
        owner_wif: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Owner approves a pending Qube wallet transaction by co-signing.

        Args:
            qube_id: Qube ID
            pending_tx_id: ID of the pending transaction to approve
            owner_wif: Owner's WIF private key for signing
            password: User's master password

        Returns:
            Dict with broadcast transaction ID
        """
        from blockchain.wallet_tx import WalletTransactionManager

        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Check if wallet is initialized
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            # Reload chain_state to get latest pending transactions from disk
            # This ensures we have the most up-to-date pending tx list
            qube.chain_state.reload()
            qube.wallet_manager._load_pending_transactions()

            wallet_manager = qube.wallet_manager

            # Get pending tx details before approval
            pending_tx = wallet_manager.get_pending_transaction(pending_tx_id)

            # Approve and broadcast (owner co-signs)
            txid = await wallet_manager.owner_approve(pending_tx_id, owner_wif)

            # Emit events to update chain_state
            if pending_tx:
                from core.events import Events

                # Emit pending tx approved event (removes from pending)
                qube.events.emit(Events.PENDING_TX_APPROVED, {
                    "tx_id": pending_tx_id
                })

                # Emit transaction sent event (adds to history)
                # Get destination address from outputs (outputs is a list of {address, value})
                to_address = pending_tx.outputs[0]["address"] if pending_tx.outputs else "unknown"
                qube.events.emit(Events.TRANSACTION_SENT, {
                    "txid": txid,
                    "to_address": to_address,
                    "amount_satoshis": pending_tx.total_amount,
                    "memo": pending_tx.memo,
                    "auto_approved": False
                })

                # Update balance via event
                try:
                    balance = await wallet_manager.get_balance()
                    if balance:
                        qube.events.emit(Events.BALANCE_UPDATED, {
                            "balance_satoshis": balance.get("confirmed", 0),
                            "unconfirmed_satoshis": balance.get("unconfirmed", 0)
                        })
                except Exception:
                    pass

            return {
                "success": True,
                "txid": txid,
                "message": f"Transaction broadcast successfully"
            }

        except Exception as e:
            logger.error(f"Failed to approve wallet transaction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def reject_wallet_transaction(
        self,
        qube_id: str,
        pending_tx_id: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Owner rejects a pending Qube wallet transaction.

        Args:
            qube_id: Qube ID
            pending_tx_id: ID of the pending transaction to reject
            password: User's master password

        Returns:
            Dict with success status
        """
        from blockchain.wallet_tx import WalletTransactionManager

        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Check if wallet is initialized
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            # Initialize wallet transaction manager
            wallet_manager = qube.wallet_manager

            # Reject the transaction
            wallet_manager.owner_reject(pending_tx_id)

            # Emit pending tx rejected event
            from core.events import Events
            qube.events.emit(Events.PENDING_TX_REJECTED, {
                "tx_id": pending_tx_id
            })

            return {
                "success": True,
                "message": f"Transaction {pending_tx_id} rejected"
            }

        except Exception as e:
            logger.error(f"Failed to reject wallet transaction: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def owner_withdraw_from_wallet(
        self,
        qube_id: str,
        to_address: str,
        amount_satoshis: int,
        owner_wif: str = "",
        password: str = ""
    ) -> Dict[str, Any]:
        """
        Owner withdraws funds directly from Qube wallet (owner-only path).

        This uses the IF branch of the P2SH script, allowing the owner
        to spend without Qube involvement.

        Args:
            qube_id: Qube ID
            to_address: BCH address to send to
            amount_satoshis: Amount in satoshis (0 for all)
            owner_wif: Owner's WIF private key (optional — uses stored key if empty)
            password: User's master password

        Returns:
            Dict with broadcast transaction ID
        """
        from blockchain.wallet_tx import WalletTransactionManager

        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Check if wallet is initialized
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            # If no WIF provided, try stored key
            if not owner_wif:
                owner_wif = self.orchestrator.get_owner_wif_for_qube(qube_id) or ""
            if not owner_wif:
                return {"success": False, "error": "No owner key provided and no stored key found. Save your owner key in Wallet Security settings first."}

            # Initialize wallet transaction manager
            wallet_manager = qube.wallet_manager

            # Owner withdraw (uses IF branch - owner alone)
            if amount_satoshis == 0:
                # Withdraw all
                txid = await wallet_manager.owner_withdraw_all(to_address, owner_wif)
            else:
                txid = await wallet_manager.owner_withdraw(to_address, amount_satoshis, owner_wif)

            return {
                "success": True,
                "txid": txid,
                "message": f"Withdrawal broadcast successfully"
            }

        except Exception as e:
            logger.error(f"Failed to withdraw from wallet: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def prepare_owner_withdraw_wc(
        self,
        qube_id: str,
        to_address: str,
        amount_satoshis: int,
        owner_address: str,
        password: str = ""
    ) -> Dict[str, Any]:
        """
        Prepare a WalletConnect transaction for owner withdrawal.

        Returns a WC transaction object that the frontend sends to the
        connected wallet for signing. The wallet signs and broadcasts.

        Args:
            qube_id: Qube ID
            to_address: Destination BCH address
            amount_satoshis: Amount in satoshis
            owner_address: Owner's P2PKH address from WC session
            password: Master password
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)
            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            from crypto.wallet import CashScriptWallet
            wallet = qube.wallet_manager.wallet
            if not isinstance(wallet, CashScriptWallet):
                return {"success": False, "error": "WalletConnect withdrawal requires a CashScript wallet. This Qube uses a legacy P2SH wallet."}

            outputs = [{"address": to_address, "value": amount_satoshis}]
            wc_transaction = await wallet.owner_spend_wc(owner_address, outputs)

            return {
                "success": True,
                "wc_transaction": wc_transaction,
                "contract_address": wallet.p2sh_address
            }

        except Exception as e:
            logger.error(f"Failed to prepare WC withdrawal: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def prepare_approve_tx_wc(
        self,
        qube_id: str,
        tx_id: str,
        owner_address: str,
        password: str = ""
    ) -> Dict[str, Any]:
        """
        Prepare a WalletConnect transaction for approving a Qube-proposed tx.

        The Qube signs the contract input, and the WC wallet signs the
        owner's P2PKH input.

        Args:
            qube_id: Qube ID
            tx_id: Pending transaction ID
            owner_address: Owner's P2PKH address from WC session
            password: Master password
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)
            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            from crypto.wallet import CashScriptWallet
            wallet = qube.wallet_manager.wallet
            if not isinstance(wallet, CashScriptWallet):
                return {"success": False, "error": "WalletConnect approval requires a CashScript wallet."}

            # Get pending transaction
            wallet_manager = qube.wallet_manager
            pending = wallet_manager.get_pending_transaction(tx_id)
            if not pending:
                return {"success": False, "error": f"Pending transaction {tx_id} not found"}

            # Build outputs from the pending tx (exclude change back to contract)
            outputs = [
                {"address": o.address, "value": o.value}
                for o in pending.unsigned_tx.outputs
                if o.address != wallet.p2sh_address
            ]
            if not outputs:
                outputs = [{"address": o.address, "value": o.value} for o in pending.unsigned_tx.outputs]

            wc_transaction = await wallet.qube_approved_wc(owner_address, outputs)

            return {
                "success": True,
                "wc_transaction": wc_transaction,
                "tx_id": tx_id,
                "contract_address": wallet.p2sh_address
            }

        except Exception as e:
            logger.error(f"Failed to prepare WC approval: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def record_wallet_broadcast(
        self,
        qube_id: str,
        txid: str,
        to_address: str = "",
        amount_satoshis: int = 0,
        memo: str = "",
        password: str = ""
    ) -> Dict[str, Any]:
        """
        Record a wallet transaction that was broadcast via WalletConnect.

        The WC wallet handles signing and broadcasting. This method just
        records the result in the Qube's transaction history.
        """
        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)
            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            import time
            from blockchain.wallet_tx import TxHistoryEntry
            wallet_manager = qube.wallet_manager
            wallet_manager._add_to_history(TxHistoryEntry(
                txid=txid,
                tx_type="withdrawal",
                amount=-amount_satoshis,
                fee=0,
                counterparty=to_address,
                timestamp=time.time(),
                block_height=None,
                memo=memo or "WalletConnect transaction"
            ))

            # Invalidate balance cache
            wallet_manager.wallet._cached_balance = None

            return {"success": True, "txid": txid}

        except Exception as e:
            logger.error(f"Failed to record wallet broadcast: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_wallet_transactions(
        self,
        qube_id: str,
        password: str,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Get transaction history for a Qube's wallet.

        Fetches blockchain transaction history and merges with local metadata
        (memos from Qube proposals).

        Args:
            qube_id: Qube ID
            password: User's master password
            limit: Maximum transactions to return (default 50)
            offset: Pagination offset (default 0)

        Returns:
            Dict with merged transaction history from blockchain + local data
        """
        from blockchain.wallet_tx import WalletTransactionManager

        try:
            self.orchestrator.set_master_key(password)
            qube = await self.orchestrator.load_qube(qube_id)

            if not qube:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Check if wallet is initialized
            wallet_info = self._get_wallet_info_from_genesis(qube.genesis_block)
            if not wallet_info:
                return {"success": False, "error": "Qube does not have a wallet configured"}

            p2sh_address = wallet_info.get("p2sh_address")

            # Get pending transactions from chain_state (source of truth)
            pending_txs = qube.chain_state.get_pending_transactions()

            # Fetch fresh transaction history from blockchain (merged with local metadata)
            # This ensures we get current confirmation status and all transactions
            wallet_manager = qube.wallet_manager
            blockchain_result = await wallet_manager.get_full_transaction_history(limit=limit, offset=offset)

            if "error" in blockchain_result:
                # Fallback to stored chain_state if blockchain fetch fails
                logger.warning(f"Blockchain fetch failed, using stored data: {blockchain_result.get('error')}")
                all_transactions = qube.chain_state.get_transaction_history(limit=0)
                total_count = len(all_transactions)
                paginated_transactions = all_transactions[offset:offset + limit] if offset else all_transactions[:limit]
                has_more = offset + limit < total_count
                transactions = []
                for tx in paginated_transactions:
                    formatted_tx = dict(tx)
                    if "timestamp" in formatted_tx and isinstance(formatted_tx["timestamp"], (int, float)):
                        formatted_tx["timestamp"] = datetime.fromtimestamp(formatted_tx["timestamp"]).isoformat()
                    transactions.append(formatted_tx)
            else:
                # Use fresh blockchain data
                transactions = blockchain_result.get("transactions", [])
                total_count = blockchain_result.get("total_count", 0)
                has_more = blockchain_result.get("has_more", False)

                # Format timestamps for frontend
                for tx in transactions:
                    if "timestamp" in tx and isinstance(tx["timestamp"], (int, float)):
                        tx["timestamp"] = datetime.fromtimestamp(tx["timestamp"]).isoformat()

            return {
                "success": True,
                "wallet_address": p2sh_address,
                "transactions": transactions,
                "total_count": total_count,
                "has_more": has_more,
                "pending_transactions": [
                    {
                        "tx_id": tx.get("tx_id"),
                        "outputs": tx.get("outputs", []),
                        "total_amount": tx.get("total_amount", 0),
                        "fee": tx.get("fee", 0),
                        "status": tx.get("status"),
                        "created_at": datetime.fromtimestamp(tx.get("created_at", 0)).isoformat() if tx.get("created_at") else None,
                        "expires_at": datetime.fromtimestamp(tx.get("expires_at")).isoformat() if tx.get("expires_at") else None,
                        "memo": tx.get("memo")
                    }
                    for tx in pending_txs
                ]
            }

        except Exception as e:
            logger.error(f"Failed to get wallet transactions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # =============================================================================
    # Wallet Security (Owner Keys, Whitelists, Stored-Key Approval)
    # =============================================================================

    async def import_seed_phrase(self, nft_address: str, seed_phrase: str, password: str = None) -> Dict[str, Any]:
        """Derive WIF from BIP39 seed phrase and save it for the NFT address."""
        try:
            if password:
                self.orchestrator.set_master_key(password)
            from crypto.seed_to_wif import seed_phrase_to_wif
            wif = seed_phrase_to_wif(seed_phrase)
            self.orchestrator.save_owner_key(nft_address, wif)
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to import seed phrase: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def save_owner_key(self, nft_address: str, owner_wif: str, password: str = None) -> Dict[str, Any]:
        """Save encrypted owner WIF for an NFT address."""
        try:
            if password:
                self.orchestrator.set_master_key(password)
            self.orchestrator.save_owner_key(nft_address, owner_wif)
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to save owner key: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def delete_owner_key(self, nft_address: str, password: str = None) -> Dict[str, Any]:
        """Delete stored owner WIF for an NFT address."""
        try:
            if password:
                self.orchestrator.set_master_key(password)
            self.orchestrator.delete_owner_key(nft_address)
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to delete owner key: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_wallet_security(self, password: str = None) -> Dict[str, Any]:
        """Get wallet security config (addresses with stored keys + whitelists)."""
        try:
            if password:
                self.orchestrator.set_master_key(password)
            result = self.orchestrator.get_wallet_security()
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Failed to get wallet security: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def update_whitelist(self, qube_id: str, whitelist: str, password: str = None) -> Dict[str, Any]:
        """Update auto-send whitelist for a Qube. whitelist is a JSON array string."""
        try:
            if password:
                self.orchestrator.set_master_key(password)
            import json as _json
            whitelist_list = _json.loads(whitelist) if isinstance(whitelist, str) else whitelist
            self.orchestrator.update_whitelist(qube_id, whitelist_list)
            return {"success": True}
        except Exception as e:
            logger.error(f"Failed to update whitelist: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def approve_wallet_tx_stored_key(self, qube_id: str, tx_id: str, password: str = None) -> Dict[str, Any]:
        """Approve wallet transaction using stored owner WIF (one-click approval)."""
        try:
            if password:
                self.orchestrator.set_master_key(password)
            await self.orchestrator.load_qube(qube_id)
            owner_wif = self.orchestrator.get_owner_wif_for_qube(qube_id)
            if not owner_wif:
                return {"success": False, "error": "No stored owner key for this Qube's NFT address"}
            return await self.approve_wallet_transaction(qube_id, tx_id, owner_wif, password)
        except Exception as e:
            logger.error(f"Failed to approve tx with stored key: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def create_qube_for_minting(
        self,
        name: str,
        genesis_prompt: str,
        ai_provider: str,
        ai_model: str,
        password: str,
        evaluation_model: str = "mistral:7b",
        favorite_color: str = "#6366f1"
    ) -> Dict[str, Any]:
        """Create a Qube with minting-specific parameters and return minting info."""
        try:
            result = await self.create_qube(
                name=name,
                genesis_prompt=genesis_prompt,
                ai_provider=ai_provider,
                ai_model=ai_model,
                evaluation_model=evaluation_model,
                voice_model="",
                favorite_color=favorite_color,
                owner_pubkey="",
                generate_avatar=False,
                password=password
            )
            if result.get("success") and result.get("qube_id"):
                qube_id = result["qube_id"]
                qube = self.orchestrator.qubes.get(qube_id)
                if qube and qube.genesis_block:
                    result["public_key"] = qube.genesis_block.public_key
                    result["genesis_block_hash"] = qube.genesis_block.block_hash
            return result
        except Exception as e:
            logger.error(f"Failed to create qube for minting: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # =============================================================================
    # Model Control (Lock, Revolver Mode, Preferences)
    # =============================================================================

    def _find_qube_dir(self, qube_id: str) -> Path:
        """Find the qube directory by searching for matching qube_id in metadata files."""
        qubes_dir = self.orchestrator.data_dir / "qubes"
        for dir_entry in qubes_dir.iterdir():
            if dir_entry.is_dir():
                metadata_path = dir_entry / "chain" / "qube_metadata.json"
                if metadata_path.exists():
                    with open(metadata_path, "r") as f:
                        data = json.load(f)
                        if data.get("qube_id") == qube_id:
                            return dir_entry
        return None

    def _get_qube_encryption_key(self, qube_dir: Path) -> Optional[bytes]:
        """
        Get the encryption key for a qube.

        The qube's encryption key is stored encrypted by the master key in:
        {qube_dir}/chain/encryption_key.enc

        Falls back to master_key for legacy qubes without encryption_key.enc.

        Args:
            qube_dir: Qube data directory

        Returns:
            Encryption key bytes, or None if not available
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not self.orchestrator.master_key:
            logger.warning("Cannot get qube encryption key - master key not set")
            return None

        key_file = qube_dir / "chain" / "encryption_key.enc"
        if not key_file.exists():
            # Legacy qube without encrypted key file - use master key directly
            logger.debug("No encryption_key.enc found, using master key fallback")
            return self.orchestrator.master_key

        try:
            with open(key_file, 'r') as f:
                enc_data = json.load(f)

            nonce = bytes.fromhex(enc_data["nonce"])
            ciphertext = bytes.fromhex(enc_data["ciphertext"])

            aesgcm = AESGCM(self.orchestrator.master_key)
            qube_key = aesgcm.decrypt(nonce, ciphertext, None)
            return qube_key

        except Exception as e:
            logger.error(f"Failed to decrypt qube encryption key: {e}")
            return None

    def _get_chain_state(self, qube_id: str) -> Optional["ChainState"]:
        """
        Get ChainState instance for a qube with proper encryption.

        Args:
            qube_id: The Qube's ID

        Returns:
            ChainState instance, or None if qube not found or key unavailable
        """
        from core.chain_state import ChainState

        qube_dir = self._find_qube_dir(qube_id)
        if not qube_dir:
            logger.warning(f"Qube directory not found for {qube_id}")
            return None

        encryption_key = self._get_qube_encryption_key(qube_dir)
        if not encryption_key:
            logger.warning(f"Cannot access chain_state for {qube_id} - no encryption key")
            return None

        chain_dir = qube_dir / "chain"
        return ChainState(data_dir=chain_dir, encryption_key=encryption_key, qube_id=qube_id)

    def _load_chain_state(self, qube_dir: Path) -> Dict[str, Any]:
        """
        Load chain_state using ChainState class with encryption.

        DEPRECATED: Prefer using _get_chain_state() directly for proper ChainState access.
        This method provides backward compatibility for code that expects raw dict access.
        """
        encryption_key = self._get_qube_encryption_key(qube_dir)
        if not encryption_key:
            # Return default state if encryption key not available
            return {
                "model_preferences": {},
                "current_model_override": None,
                "model_locked": False,
                "model_locked_to": None,
                "revolver_mode_enabled": False,
                "revolver_mode_pool": [],
                "autonomous_mode_enabled": False,
                "autonomous_mode_pool": []
            }

        from core.chain_state import ChainState
        chain_dir = qube_dir / "chain"
        chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)

        # Return settings section for backward compatibility
        return chain_state.get_all_settings()

    def _save_chain_state(self, qube_dir: Path, state: Dict[str, Any]) -> bool:
        """
        Save chain_state using ChainState class with encryption.

        DEPRECATED: Prefer using _get_chain_state() and update_settings() directly.
        This method provides backward compatibility.

        Returns:
            True if save succeeded, False if failed
        """
        encryption_key = self._get_qube_encryption_key(qube_dir)
        if not encryption_key:
            logger.error("Cannot save chain_state - no encryption key (master_key not set?)")
            return False

        from core.chain_state import ChainState
        chain_dir = qube_dir / "chain"
        chain_state = ChainState(data_dir=chain_dir, encryption_key=encryption_key)

        # Update settings section from GUI (don't preserve old GUI-managed values from disk)
        chain_state.update_settings(state, from_gui=True)
        return True

    async def set_model_lock(self, qube_id: str, locked: bool, model_name: str = None) -> Dict[str, Any]:
        """
        Lock or unlock the Qube's ability to switch models.
        Works directly with chain_state.json without loading the full qube.

        Note: Lock and Revolver modes are mutually exclusive.
        Enabling lock will disable revolver mode.

        Args:
            qube_id: The Qube's ID
            locked: True to lock, False to unlock
            model_name: Optional - when locking, force a specific model

        Returns:
            Dict with success status
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Use ChainState object directly to properly handle nested settings structure
            chain_state = self._get_chain_state(qube_id)
            if not chain_state:
                return {"success": False, "error": "Failed to access chain state - encryption key not available"}

            chain_state.set_model_lock(locked, model_name)

            logger.info(f"Model lock set for {qube_id}: locked={locked}, model={model_name}")

            return {
                "success": True,
                "locked": locked,
                "locked_to": model_name
            }

        except Exception as e:
            logger.error(f"Failed to set model lock: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def set_revolver_mode(self, qube_id: str, enabled: bool) -> Dict[str, Any]:
        """
        Enable or disable revolver mode (privacy feature).
        Works directly with chain_state.json without loading the full qube.

        When enabled, rotates AI providers for each response.

        Note: Lock and Revolver modes are mutually exclusive.
        Enabling revolver will disable lock mode.

        Args:
            qube_id: The Qube's ID
            enabled: True to enable, False to disable

        Returns:
            Dict with success status
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Use ChainState object directly to properly handle runtime fields
            chain_state = self._get_chain_state(qube_id)
            if not chain_state:
                return {"success": False, "error": "Failed to access chain state - encryption key not available"}

            chain_state.set_revolver_mode(enabled)

            logger.info(f"Revolver mode set for {qube_id}: enabled={enabled}")

            return {
                "success": True,
                "revolver_mode": enabled
            }

        except Exception as e:
            logger.error(f"Failed to set revolver mode: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def set_revolver_mode_pool(self, qube_id: str, models: List[str]) -> Dict[str, Any]:
        """
        Set the model pool for revolver mode rotation.
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID
            models: List of model IDs for revolver rotation.

        Returns:
            Dict with success status and current pool
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Use ChainState object directly to properly handle nested settings structure
            chain_state = self._get_chain_state(qube_id)
            if not chain_state:
                return {"success": False, "error": "Failed to access chain state - encryption key not available"}

            chain_state.set_revolver_mode_pool(models)

            logger.info(f"Revolver mode pool set for {qube_id}: {len(models)} models")

            return {
                "success": True,
                "pool": models
            }

        except Exception as e:
            logger.error(f"Failed to set revolver mode pool: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_revolver_mode_pool(self, qube_id: str) -> Dict[str, Any]:
        """
        Get the model pool for revolver mode rotation.
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID

        Returns:
            Dict with success status and pool list
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # _load_chain_state returns the settings dict directly
            settings = self._load_chain_state(qube_dir)
            pool = settings.get("revolver_mode_pool", [])

            return {
                "success": True,
                "pool": pool
            }

        except Exception as e:
            logger.error(f"Failed to get revolver mode pool: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # Deprecated: Use set_revolver_mode_pool instead
    async def set_revolver_providers(self, qube_id: str, providers: List[str]) -> Dict[str, Any]:
        """Deprecated: Use set_revolver_mode_pool instead."""
        return await self.set_revolver_mode_pool(qube_id, providers)

    async def get_revolver_providers(self, qube_id: str) -> Dict[str, Any]:
        """Deprecated: Use get_revolver_mode_pool instead."""
        result = await self.get_revolver_mode_pool(qube_id)
        if result.get("success"):
            return {"success": True, "providers": result.get("pool", [])}
        return result

    async def set_revolver_models(self, qube_id: str, models: List[str]) -> Dict[str, Any]:
        """Deprecated: Use set_revolver_mode_pool instead."""
        return await self.set_revolver_mode_pool(qube_id, models)

    async def get_revolver_models(self, qube_id: str) -> Dict[str, Any]:
        """Deprecated: Use get_revolver_mode_pool instead."""
        result = await self.get_revolver_mode_pool(qube_id)
        if result.get("success"):
            return {"success": True, "models": result.get("pool", [])}
        return result

    async def set_autonomous_mode_pool(self, qube_id: str, models: List[str]) -> Dict[str, Any]:
        """
        Set the model pool for autonomous mode.
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID
            models: List of model IDs for autonomous mode.

        Returns:
            Dict with success status and current pool
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Use ChainState object directly to properly handle nested settings structure
            chain_state = self._get_chain_state(qube_id)
            if not chain_state:
                return {"success": False, "error": "Failed to access chain state - encryption key not available"}

            chain_state.set_autonomous_mode_pool(models)

            logger.info(f"Autonomous mode pool set for {qube_id}: {len(models)} models")

            return {
                "success": True,
                "pool": models
            }

        except Exception as e:
            logger.error(f"Failed to set autonomous mode pool: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_autonomous_mode_pool(self, qube_id: str) -> Dict[str, Any]:
        """
        Get the model pool for autonomous mode.
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID

        Returns:
            Dict with success status and pool list
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # _load_chain_state returns the settings dict directly
            settings = self._load_chain_state(qube_dir)
            pool = settings.get("autonomous_mode_pool", [])

            return {
                "success": True,
                "pool": pool
            }

        except Exception as e:
            logger.error(f"Failed to get autonomous mode pool: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # Deprecated: Use set_autonomous_mode_pool instead
    async def set_autonomous_mode_pools(self, qube_id: str, pools: Dict[str, List[str]]) -> Dict[str, Any]:
        """Deprecated: Use set_autonomous_mode_pool instead."""
        # Flatten all pools into a single list
        all_models = [m for models in pools.values() for m in models]
        return await self.set_autonomous_mode_pool(qube_id, all_models)

    async def get_autonomous_mode_pools(self, qube_id: str) -> Dict[str, Any]:
        """Deprecated: Use get_autonomous_mode_pool instead."""
        result = await self.get_autonomous_mode_pool(qube_id)
        if result.get("success"):
            pool = result.get("pool", [])
            return {"success": True, "pools": {"default": pool} if pool else {}}
        return result

    async def set_free_mode_models(self, qube_id: str, models: List[str]) -> Dict[str, Any]:
        """Deprecated: Use set_autonomous_mode_pool instead."""
        return await self.set_autonomous_mode_pool(qube_id, models)

    async def get_free_mode_models(self, qube_id: str) -> Dict[str, Any]:
        """Deprecated: Use get_autonomous_mode_pool instead."""
        result = await self.get_autonomous_mode_pool(qube_id)
        if result.get("success"):
            return {"success": True, "models": result.get("pool", [])}
        return result

    async def set_autonomous_mode(self, qube_id: str, enabled: bool) -> Dict[str, Any]:
        """
        Enable or disable autonomous mode (AI-driven model selection).
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID
            enabled: True to enable autonomous mode, False to disable

        Returns:
            Dict with success status and current enabled state
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Use ChainState object directly to properly handle nested settings structure
            chain_state = self._get_chain_state(qube_id)
            if not chain_state:
                return {"success": False, "error": "Failed to access chain state - encryption key not available"}

            chain_state.set_autonomous_mode(enabled)

            logger.info(f"Autonomous mode {'enabled' if enabled else 'disabled'} for {qube_id}")

            return {
                "success": True,
                "enabled": enabled
            }

        except Exception as e:
            logger.error(f"Failed to set autonomous mode: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # Backward compatibility alias
    async def set_free_mode(self, qube_id: str, enabled: bool) -> Dict[str, Any]:
        """Deprecated: Use set_autonomous_mode instead."""
        return await self.set_autonomous_mode(qube_id, enabled)

    async def get_model_preferences(self, qube_id: str) -> Dict[str, Any]:
        """
        Get the Qube's model preferences and control status.
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID

        Returns:
            Dict with preferences, current model, lock status, revolver mode
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # _load_chain_state returns the settings dict directly (via get_all_settings)
            # So 'settings' here IS the settings dict, not a parent state object
            settings = self._load_chain_state(qube_dir)

            # Also get genesis model from metadata
            metadata_path = qube_dir / "chain" / "qube_metadata.json"
            genesis_model = None
            if metadata_path.exists():
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
                    genesis_model = metadata.get("genesis_block", {}).get("ai_model")

            # Read directly from settings (which is what _load_chain_state returns)
            revolver_mode = settings.get("revolver_mode_enabled", False)
            autonomous_mode = settings.get("autonomous_mode_enabled", False)
            # Default to model_locked if neither revolver nor autonomous mode is on
            model_locked_raw = settings.get("model_locked")
            model_locked = model_locked_raw if model_locked_raw is not None else (not revolver_mode and not autonomous_mode)

            # Get model_mode (single source of truth) - derive if not stored
            model_mode = settings.get("model_mode")
            if not model_mode:
                if autonomous_mode:
                    model_mode = "autonomous"
                elif revolver_mode:
                    model_mode = "revolver"
                else:
                    model_mode = "manual"

            # Get pools and other settings
            revolver_pool = settings.get("revolver_mode_pool", [])
            autonomous_pool = settings.get("autonomous_mode_pool", [])
            locked_to = settings.get("model_locked_to")

            return {
                "success": True,
                "preferences": settings.get("model_preferences", {}),
                "current_model": settings.get("current_model_override") or genesis_model,
                "current_override": settings.get("current_model_override"),
                "genesis_model": genesis_model,
                "model_locked": model_locked,
                "locked_to": locked_to,
                "revolver_mode": revolver_mode,
                "revolver_mode_pool": revolver_pool,
                "autonomous_mode": autonomous_mode,
                "autonomous_mode_pool": autonomous_pool,
                "model_mode": model_mode  # Single source of truth
            }

        except Exception as e:
            logger.error(f"Failed to get model preferences: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def clear_model_preferences(self, qube_id: str, task_type: str = None) -> Dict[str, Any]:
        """
        Clear model preferences.
        Works directly with chain_state.json without loading the full qube.

        Args:
            qube_id: The Qube's ID
            task_type: Optional - clear specific task type only, or all if None

        Returns:
            Dict with success status
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            state = self._load_chain_state(qube_dir)

            if task_type:
                prefs = state.get("model_preferences", {})
                if task_type in prefs:
                    del prefs[task_type]
                state["model_preferences"] = prefs
                logger.info(f"Cleared model preference for {qube_id}: task_type={task_type}")
            else:
                state["model_preferences"] = {}
                logger.info(f"Cleared all model preferences for {qube_id}")

            self._save_chain_state(qube_dir, state)

            return {"success": True}

        except Exception as e:
            logger.error(f"Failed to clear model preferences: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def reset_model_to_genesis(self, qube_id: str) -> Dict[str, Any]:
        """
        Reset the Qube's model to its genesis (birth) model.
        Works directly with chain_state.json without loading the full qube.

        Clears the model override AND resets runtime to genesis model.
        This fixes corrupted runtime state (e.g., from fallback chain).

        Args:
            qube_id: The Qube's ID

        Returns:
            Dict with success status and the genesis model
        """
        try:
            qube_dir = self._find_qube_dir(qube_id)
            if not qube_dir:
                return {"success": False, "error": f"Qube {qube_id} not found"}

            # Get genesis model and provider from metadata first
            metadata_path = qube_dir / "chain" / "qube_metadata.json"
            genesis_model = None
            genesis_provider = None
            if metadata_path.exists():
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
                    genesis_block = metadata.get("genesis_block", {})
                    genesis_model = genesis_block.get("ai_model")
                    genesis_provider = genesis_block.get("ai_provider")

            # Use ChainState directly to properly update both settings AND runtime
            # The deprecated _load_chain_state/_save_chain_state only handle settings section
            chain_state = self._get_chain_state(qube_id)
            if not chain_state:
                return {"success": False, "error": "Could not load chain state (encryption key missing?)"}

            # Clear the model override in settings
            chain_state.update_settings({
                "current_model_override": None,
                "model_locked_to": genesis_model  # Ensure locked model matches genesis
            })

            # Reset runtime to genesis model (this is the key fix - runtime is a SEPARATE section)
            if genesis_model or genesis_provider:
                chain_state.update_runtime(
                    current_model=genesis_model,
                    current_provider=genesis_provider
                )

            logger.info(f"Reset model to genesis for {qube_id}: {genesis_model} ({genesis_provider})")

            return {
                "success": True,
                "genesis_model": genesis_model,
                "genesis_provider": genesis_provider
            }

        except Exception as e:
            logger.error(f"Failed to reset model to genesis: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


async def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No command specified"}), file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    # Persistent sidecar mode — single long-running process for all commands
    if command == "server":
        from sidecar_server import SidecarServer
        server = SidecarServer()
        await server.run()
        return

    # Handle setup wizard commands BEFORE creating GUIBridge
    # (GUIBridge creates default user directory which interferes with first-run detection)
    if command == "check-first-run":
        # Check if this is the first run (no users exist)
        # Use platform-aware path (critical for Linux AppImage)
        from utils.paths import detect_legacy_data_dirs
        users_dir = get_users_dir()
        result = {"data_dir": str(get_app_data_dir())}
        if not users_dir.exists():
            result["is_first_run"] = True
            result["users"] = []
        else:
            users = [d.name for d in users_dir.iterdir() if d.is_dir()]
            result["is_first_run"] = len(users) == 0
            result["users"] = users
        # Detect legacy data from older versions (./data/users/)
        if result["is_first_run"]:
            legacy = detect_legacy_data_dirs()
            if legacy:
                old_users_dir = legacy[0] / "users"
                old_users = [d.name for d in old_users_dir.iterdir() if d.is_dir()]
                if old_users:
                    result["legacy_data_dir"] = str(legacy[0])
                    result["legacy_users"] = old_users
        print(json.dumps(result))
        return

    elif command == "create-user-account":
        # Create a new user account
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "User ID required"}))
            sys.exit(1)

        try:
            user_id = validate_user_id(sys.argv[2])
            # Password from stdin (secure) or argv fallback (backwards compat)
            password = get_secret("password", argv_index=3)

            if not password:
                print(json.dumps({"success": False, "error": "Password required"}))
                return

            # Create data directory for user using platform-aware path
            # (critical for Linux AppImage - avoids read-only mount)
            user_data_dir = get_user_data_dir(user_id)
            if (user_data_dir / "password_verifier.enc").exists():
                print(json.dumps({"success": False, "error": "User already exists"}))
            else:
                # User directory already created by get_user_data_dir

                # Create orchestrator for user and set master key (generates salt)
                # Import locally to avoid UnboundLocalError (other branches in main() import it too)
                from orchestrator.user_orchestrator import UserOrchestrator
                orchestrator = UserOrchestrator(user_id=user_id)
                orchestrator.set_master_key(password)

                # Create password verifier for secure authentication
                # This encrypts a known plaintext so we can verify the password later
                import secrets
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM

                verifier_file = user_data_dir / "password_verifier.enc"
                aesgcm = AESGCM(orchestrator.master_key)
                nonce = secrets.token_bytes(12)
                plaintext = b"QUBES_PASSWORD_VERIFIED"
                ciphertext = aesgcm.encrypt(nonce, plaintext, None)

                verifier_data = {
                    "nonce": nonce.hex(),
                    "ciphertext": ciphertext.hex(),
                    "algorithm": "AES-256-GCM",
                    "version": "1.0"
                }

                with open(verifier_file, 'w') as f:
                    json.dump(verifier_data, f, indent=2)

                print(json.dumps({
                    "success": True,
                    "user_id": user_id,
                    "data_dir": str(user_data_dir.absolute())
                }))
        except Exception as e:
            import traceback
            error_msg = str(e)
            # Log full traceback for debugging
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({
                "success": False,
                "error": f"Account creation failed: {error_msg}"
            }))
        return

    elif command == "delete-user-account":
        # Delete a user account directory (for resetting corrupted accounts)
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "User ID required"}))
            sys.exit(1)
        try:
            import shutil as shutil_mod
            user_id = validate_user_id(sys.argv[2])
            users_dir = get_users_dir()
            user_dir = users_dir / user_id
            if not user_dir.exists():
                print(json.dumps({"success": False, "error": "User not found"}))
            elif not str(user_dir.resolve()).startswith(str(users_dir.resolve())):
                print(json.dumps({"success": False, "error": "Invalid user path"}))
            else:
                shutil_mod.rmtree(user_dir)
                print(json.dumps({"success": True}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
        return

    elif command == "migrate-user-data":
        # Migrate user data from old ./data/ path to platform-aware path
        if len(sys.argv) < 4:
            print(json.dumps({"success": False, "error": "Usage: migrate-user-data <old_data_dir> <user_id>"}))
            sys.exit(1)
        try:
            import shutil as shutil_mod
            old_data_dir = Path(sys.argv[2])
            user_id = validate_user_id(sys.argv[3])
            old_user_dir = old_data_dir / "users" / user_id
            if not old_user_dir.exists():
                print(json.dumps({"success": False, "error": f"Old user directory not found: {old_user_dir}"}))
            else:
                new_user_dir = get_user_data_dir(user_id)
                # Copy contents from old to new (merge, don't overwrite existing)
                for item in old_user_dir.iterdir():
                    dest = new_user_dir / item.name
                    if not dest.exists():
                        if item.is_dir():
                            shutil_mod.copytree(item, dest)
                        else:
                            shutil_mod.copy2(item, dest)
                print(json.dumps({"success": True, "data_dir": str(new_user_dir)}))
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({"success": False, "error": str(e)}))
        return

    elif command == "check-ollama-status":
        # Check if Ollama is running (no bridge needed)
        import shutil

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            # Check common locations
            common_paths = [
                Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
                Path("/usr/local/bin/ollama"),
                Path("/usr/bin/ollama"),
            ]
            for p in common_paths:
                if p.exists():
                    ollama_path = str(p)
                    break

        if not ollama_path:
            print(json.dumps({
                "installed": False,
                "running": False,
                "models": []
            }))
            return

        # Check if running by trying to list models
        import subprocess
        try:
            run_kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": 10
            }
            # Hide console window on Windows
            if sys.platform == "win32":
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                ["ollama", "list"],
                **run_kwargs
            )
            if result.returncode == 0:
                # Parse model list
                models = []
                for line in result.stdout.strip().split('\n')[1:]:  # Skip header
                    if line.strip():
                        parts = line.split()
                        if parts:
                            models.append(parts[0])
                print(json.dumps({
                    "installed": True,
                    "running": True,
                    "models": models
                }))
            else:
                print(json.dumps({
                    "installed": True,
                    "running": False,
                    "models": []
                }))
        except subprocess.TimeoutExpired:
            print(json.dumps({
                "installed": True,
                "running": False,
                "models": []
            }))
        except Exception as e:
            print(json.dumps({
                "installed": True,
                "running": False,
                "models": [],
                "error": str(e)
            }))
        return

    bridge = GUIBridge()

    try:
        if command == "authenticate":
            # Parse arguments
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "Username required"}), file=sys.stderr)
                sys.exit(1)

            # SECURITY: Validate user_id to prevent path traversal
            user_id = validate_user_id(sys.argv[2])
            # Password from stdin (secure) or argv fallback (backwards compat)
            password = get_secret("password", argv_index=3)

            result = await bridge.authenticate(user_id, password)
            print(json.dumps(result))

        elif command == "get-available-models":
            # No authentication required - this is public model metadata
            from ai.model_registry import ModelRegistry
            models_data = ModelRegistry.get_models_for_frontend()
            print(json.dumps(models_data))

        elif command == "list-qubes":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            # SECURITY: Validate user_id to prevent path traversal
            user_id = validate_user_id(sys.argv[2])
            password = get_secret("password", argv_index=3)

            # Create bridge with correct user and set master key for chain_state decryption
            user_bridge = GUIBridge(user_id=user_id)
            if password:
                user_bridge.orchestrator.set_master_key(password)
            qubes = await user_bridge.list_qubes()
            print(json.dumps(qubes))

        elif command == "create-qube":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            # Parse arguments
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")  # Already consumed
            parser.add_argument("user_id")  # Already consumed
            parser.add_argument("--name", required=True)
            parser.add_argument("--genesis-prompt", required=True)
            parser.add_argument("--ai-provider", required=True)
            parser.add_argument("--ai-model", required=True)
            parser.add_argument("--voice-model", default="openai:alloy")
            parser.add_argument("--owner-pubkey", required=True)  # NFT address derived from this
            parser.add_argument("--password", default="")  # Password comes from stdin
            parser.add_argument("--encrypt-genesis", default="false")
            parser.add_argument("--favorite-color", default="#00ff88")
            parser.add_argument("--avatar-file", default=None)
            parser.add_argument("--generate-avatar", action="store_true", default=False)
            parser.add_argument("--avatar-style", default="cyberpunk")

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            qube = await user_bridge.create_qube(
                name=args.name,
                genesis_prompt=args.genesis_prompt,
                ai_provider=args.ai_provider,
                ai_model=args.ai_model,
                voice_model=args.voice_model,
                owner_pubkey=args.owner_pubkey,  # NFT address derived from this
                password=password,
                encrypt_genesis=(args.encrypt_genesis.lower() == "true"),
                favorite_color=args.favorite_color,
                avatar_file=args.avatar_file,
                generate_avatar=args.generate_avatar,
                avatar_style=args.avatar_style,
            )
            print(json.dumps(qube))

        elif command == "get-qube":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            # SECURITY: Validate qube_id to prevent path traversal
            qube_id = validate_qube_id(sys.argv[2])
            qube = await bridge.get_qube(qube_id)
            print(json.dumps(qube))

        elif command == "send-message":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and message required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate inputs to prevent injection
            qube_id = validate_qube_id(sys.argv[3])
            message_arg = sys.argv[4]  # Validated later in message processing
            password = get_secret("password", argv_index=5)

            # Check if message is a file reference (for large messages that exceed command-line limits)
            if message_arg.startswith("@file:"):
                # Read message from file
                file_ref = message_arg[6:]  # Remove @file: prefix

                # Handle temp: prefix for temp directory files
                if file_ref.startswith("temp:"):
                    import tempfile
                    filename = file_ref[5:]  # Remove temp: prefix
                    file_path = os.path.join(tempfile.gettempdir(), filename)
                    logger.info(f"Resolving temp file: {filename} -> {file_path}")
                else:
                    file_path = file_ref

                logger.info(f"Reading message from file: {file_path}")

                # Check file size first
                import os
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    logger.info(f"Message file size: {file_size} bytes ({file_size / 1024:.2f} KB)")

                    with open(file_path, 'r', encoding='utf-8') as f:
                        message = f.read()

                    logger.info(f"Message read successfully, length: {len(message)} chars")

                    # Clean up temp file
                    try:
                        os.remove(file_path)
                        logger.debug(f"Temp file deleted: {file_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete temp file: {e}")
                else:
                    logger.error(f"Temp file not found: {file_path}")
                    message = ""  # Empty message on error
            else:
                message = message_arg

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.send_message(qube_id, message, password)
            print(json.dumps(result))

            # Check if auto-anchor is pending - if so, spawn a detached subprocess to handle it
            # This allows the main process to exit immediately (so frontend can start audio)
            # while the auto-anchor runs concurrently in the background
            qube = user_bridge.orchestrator.qubes.get(qube_id)
            if qube and qube.current_session and qube.current_session._pending_anchor_task:
                logger.info("spawning_detached_auto_anchor_subprocess")
                import subprocess
                import os

                # Cancel the in-process task since we'll use subprocess instead
                qube.current_session._pending_anchor_task.cancel()
                qube.current_session._pending_anchor_task = None

                # Spawn detached subprocess to handle auto-anchor
                # Using CREATE_NEW_PROCESS_GROUP and DETACHED_PROCESS on Windows
                # to ensure the subprocess continues after parent exits
                # In bundled mode, sys.executable IS the backend exe (no script path needed)
                # In dev mode, we need python + script path
                if getattr(sys, 'frozen', False):
                    # Bundled PyInstaller exe - just call the exe directly
                    cmd = [sys.executable, "auto-anchor-background", user_id, qube_id]
                else:
                    # Development mode - use python + script
                    cmd = [sys.executable, __file__, "auto-anchor-background", user_id, qube_id]

                # Pass password via environment variable (more secure than argv)
                env = os.environ.copy()
                env["QUBES_PASSWORD"] = password

                if sys.platform == "win32":
                    # Windows: Use CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS, and CREATE_NO_WINDOW
                    DETACHED_PROCESS = 0x00000008
                    CREATE_NEW_PROCESS_GROUP = 0x00000200
                    CREATE_NO_WINDOW = 0x08000000
                    subprocess.Popen(
                        cmd,
                        env=env,
                        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                else:
                    # Unix: Use start_new_session
                    subprocess.Popen(
                        cmd,
                        env=env,
                        start_new_session=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                logger.info("detached_auto_anchor_subprocess_spawned")

        elif command == "anchor-session":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # Load qube and anchor session
            if qube_id not in user_bridge.orchestrator.qubes:
                await user_bridge.orchestrator.load_qube(qube_id)

            qube = user_bridge.orchestrator.qubes[qube_id]
            blocks_anchored = await qube.anchor_session()

            print(json.dumps({"success": True, "blocks_anchored": blocks_anchored}))

        elif command == "auto-anchor-background":
            # This command is spawned as a detached subprocess from send-message
            # It runs the auto-anchor with summary creation while audio plays
            if len(sys.argv) < 4:
                logger.error("auto_anchor_background_missing_args")
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])

            # Get password from environment variable (set by parent process)
            import os
            password = os.environ.get("QUBES_PASSWORD", "")
            if not password:
                logger.error("auto_anchor_background_no_password")
                sys.exit(1)

            logger.info("auto_anchor_background_starting", qube_id=qube_id)

            # DEBUG: Write to separate file to track subprocess execution
            from pathlib import Path
            debug_file = Path.home() / ".qubes" / "logs" / "auto_anchor_debug.log"
            debug_file.parent.mkdir(parents=True, exist_ok=True)
            def debug_log(msg):
                from datetime import datetime, timezone
                with open(debug_file, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now(timezone.utc).isoformat()} | SUBPROCESS | {msg}\n")

            try:
                debug_log(f"=== AUTO-ANCHOR SUBPROCESS STARTED ===")
                debug_log(f"user_id={user_id}, qube_id={qube_id}")

                # Create bridge and load qube
                user_bridge = GUIBridge(user_id=user_id)
                user_bridge.orchestrator.set_master_key(password)

                # Export API keys to environment for fallback models during summary generation
                api_keys = user_bridge.orchestrator.get_api_keys()
                if api_keys:
                    api_dict = api_keys.to_dict()
                    env_map = {
                        "openai": "OPENAI_API_KEY",
                        "anthropic": "ANTHROPIC_API_KEY",
                        "google": "GOOGLE_API_KEY",
                        "venice": "VENICE_API_KEY",
                        "deepseek": "DEEPSEEK_API_KEY",
                        "perplexity": "PERPLEXITY_API_KEY",
                        "xai": "XAI_API_KEY",
                    }
                    for provider, env_var in env_map.items():
                        if provider in api_dict and api_dict[provider]:
                            os.environ[env_var] = api_dict[provider]
                    debug_log(f"API keys exported to environment: {list(api_dict.keys())}")

                if qube_id not in user_bridge.orchestrator.qubes:
                    debug_log(f"Loading qube {qube_id}...")
                    await user_bridge.orchestrator.load_qube(qube_id)

                qube = user_bridge.orchestrator.qubes.get(qube_id)
                if not qube:
                    debug_log(f"ERROR: Qube not found after loading")
                    logger.error("auto_anchor_background_qube_not_found", qube_id=qube_id)
                    sys.exit(1)

                # Check for discard marker — if the session was discarded while
                # this subprocess was starting up, abort immediately
                session_dir = qube.data_dir / "blocks" / "session"
                discard_marker = session_dir / ".discarded" if session_dir.exists() else None
                if discard_marker and discard_marker.exists():
                    debug_log(f"Session was discarded — aborting auto-anchor")
                    logger.info("auto_anchor_aborted_session_discarded", qube_id=qube_id)
                    try:
                        discard_marker.unlink()
                    except Exception:
                        pass
                    sys.exit(0)

                # Log session state before anchoring
                session_blocks_count = len(qube.current_session.session_blocks) if qube.current_session else 0
                session_files = list(session_dir.glob("*.json")) if session_dir.exists() else []
                debug_log(f"Session state before anchor: in_memory_blocks={session_blocks_count}, session_files={len(session_files)}")
                if session_files:
                    debug_log(f"Session files: {[f.name for f in session_files[:10]]}...")

                # If no session files remain (discard deleted them), abort
                if not session_files and session_blocks_count == 0:
                    debug_log(f"No session blocks to anchor — aborting")
                    logger.info("auto_anchor_aborted_no_blocks", qube_id=qube_id)
                    sys.exit(0)

                debug_log(f"Calling anchor_session(create_summary=True)...")

                # Run anchor with summary creation
                blocks_anchored = await qube.anchor_session(create_summary=True)

                # Log session state after anchoring
                remaining_files = list(session_dir.glob("*.json")) if session_dir.exists() else []
                debug_log(f"Session state after anchor: blocks_anchored={blocks_anchored}, remaining_files={len(remaining_files)}")
                if remaining_files:
                    debug_log(f"Remaining files: {[f.name for f in remaining_files]}")
                debug_log(f"=== AUTO-ANCHOR SUBPROCESS COMPLETED ===")

                logger.info(
                    "auto_anchor_background_completed",
                    qube_id=qube_id,
                    blocks_anchored=blocks_anchored
                )

                # Remove lock file on success
                lock_file = qube.data_dir / ".anchor_in_progress.lock"
                if lock_file.exists():
                    lock_file.unlink()
                    debug_log(f"Lock file removed")

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                debug_log(f"ERROR: {type(e).__name__}: {e}")
                debug_log(f"Traceback:\n{tb}")
                logger.error(
                    "auto_anchor_background_failed",
                    error=str(e),
                    qube_id=qube_id,
                    traceback=tb
                )
                # Remove lock file on failure too (so retry is possible)
                try:
                    if qube:
                        lock_file = qube.data_dir / ".anchor_in_progress.lock"
                        if lock_file.exists():
                            lock_file.unlink()
                            debug_log(f"Lock file removed after error")
                except:
                    pass
                sys.exit(1)

        elif command == "download-qwen-model":
            # This command handles Qwen TTS model downloads in both dev and bundled mode
            # It's spawned as a detached subprocess by model_downloader.py
            if len(sys.argv) < 5:
                logger.error("download_qwen_model_missing_args")
                print(json.dumps({"error": "download_id, model_name, and models_dir required"}), file=sys.stderr)
                sys.exit(1)

            download_id = sys.argv[2]
            model_name = sys.argv[3]
            models_dir = Path(sys.argv[4])

            logger.info("download_qwen_model_starting", download_id=download_id, model_name=model_name)

            try:
                # Import and run the download logic directly
                from audio.download_worker import download_model
                download_model(download_id, model_name, models_dir)
                logger.info("download_qwen_model_completed", download_id=download_id)
            except Exception as e:
                import traceback
                logger.error(
                    "download_qwen_model_failed",
                    download_id=download_id,
                    error=str(e),
                    traceback=traceback.format_exc()
                )
                sys.exit(1)

        elif command == "check-sessions":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Check if qube has session blocks
            qube_dir = get_user_qubes_dir(user_id)

            # Find the qube directory (format: {name}_{qube_id})
            qube_dirs = list(qube_dir.glob(f"*_{qube_id}"))

            has_session = False
            if qube_dirs:
                session_dir = qube_dirs[0] / "blocks" / "session"
                if session_dir.exists():
                    # Check if there are any JSON files
                    session_files = list(session_dir.glob("*.json"))
                    has_session = len(session_files) > 0

            print(json.dumps({"has_session": has_session}))

        elif command == "discard-session":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Direct file deletion approach - avoids loading qube which can lock files
            # This is more reliable on Windows where file handles can be held
            import time
            from pathlib import Path

            # Get data directory from orchestrator
            user_bridge = GUIBridge(user_id=user_id)
            if password:
                user_bridge.orchestrator.set_master_key(password)
            qube_dir = user_bridge.orchestrator.data_dir / "qubes"

            # Find qube directory (starts with name, ends with _QUBEID)
            qube_path = None
            for d in qube_dir.iterdir():
                if d.is_dir() and d.name.endswith(f"_{qube_id}"):
                    qube_path = d
                    break

            blocks_discarded = 0
            current_model = None
            current_provider = None
            if qube_path:
                session_dir = qube_path / "blocks" / "session"

                # Delete session block files with retry
                if session_dir.exists():
                    for block_file in list(session_dir.glob("*.json")):
                        for attempt in range(3):
                            try:
                                block_file.unlink()
                                blocks_discarded += 1
                                break
                            except PermissionError:
                                if attempt < 2:
                                    time.sleep(0.1 * (attempt + 1))
                            except Exception as e:
                                logger.warning("delete_block_failed", file=str(block_file), error=str(e))
                                break

                # Clear session audio files (audio_block_*.mp3/wav, latest_response*.mp3/wav)
                audio_dir = qube_path / "audio"
                if audio_dir.exists():
                    import glob
                    audio_patterns = [
                        "audio_block_*.mp3",
                        "audio_block_*.wav",
                        "latest_response*.mp3",
                        "latest_response*.wav"
                    ]
                    for pattern in audio_patterns:
                        for audio_file in glob.glob(str(audio_dir / pattern)):
                            try:
                                Path(audio_file).unlink()
                                logger.debug("deleted_session_audio", path=audio_file)
                            except Exception as e:
                                logger.warning("failed_to_delete_audio", path=audio_file, error=str(e))

                # Update chain_state to clear session info using ChainState class
                try:
                    encryption_key = user_bridge._get_qube_encryption_key(qube_path)
                    if encryption_key:
                        from core.chain_state import ChainState
                        chain_dir = qube_path / "chain"
                        cs = ChainState(data_dir=chain_dir, encryption_key=encryption_key)

                        # Rollback session-scoped data (skills, relationships, mood, owner_info)
                        # to the snapshot taken when the session started
                        rolled_back = cs.rollback_session_data()
                        if rolled_back:
                            logger.info("session_scoped_data_rolled_back")

                        # Clear session state
                        cs.end_session()

                        # Get the actual current model after rollback (for frontend sync)
                        current_model = cs.get_current_model()
                        current_provider = cs.get_current_provider()
                    else:
                        logger.warning("Cannot update chain_state - no encryption key")
                except Exception as e:
                    logger.warning("update_chain_state_failed", error=str(e))

            result = {"success": True, "blocks_discarded": blocks_discarded}
            if current_model:
                result["current_model"] = current_model
            if current_provider:
                result["current_provider"] = current_provider
            print(json.dumps(result))

        elif command == "delete-session-block":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and block number or timestamp required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            block_number = int(sys.argv[4])
            password = get_secret("password")
            # Optional timestamp for stable deletion (block numbers shift on reload)
            # Timestamp is at argv[5] - password comes via stdin, not argv
            # Frontend sends milliseconds (JS format), but blocks store seconds (Python format)
            # Convert back: milliseconds -> seconds
            timestamp_ms = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else None
            timestamp = timestamp_ms // 1000 if timestamp_ms is not None else None

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # Load qube and delete session block
            if qube_id not in user_bridge.orchestrator.qubes:
                await user_bridge.orchestrator.load_qube(qube_id)

            qube = user_bridge.orchestrator.qubes[qube_id]

            # Ensure there's an active session
            if not qube.current_session:
                print(json.dumps({"success": False, "error": "No active session"}))
                sys.exit(0)

            # Delete by timestamp (stable) if provided, otherwise by block_number (may shift)
            deleted_block = qube.current_session.delete_block(
                block_number=block_number if timestamp is None else None,
                timestamp=timestamp
            )

            if deleted_block:
                print(json.dumps({"success": True, "deleted_block_number": block_number, "deleted_timestamp": deleted_block.timestamp}))
            else:
                print(json.dumps({"success": False, "error": f"Block {block_number} (timestamp={timestamp}) not found"}))

        elif command == "discard-last-block":
            # Discard the most recent session block (for retry/discard on API errors)
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password")

            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)

            if qube_id not in user_bridge.orchestrator.qubes:
                await user_bridge.orchestrator.load_qube(qube_id)

            qube = user_bridge.orchestrator.qubes[qube_id]

            if not qube.current_session:
                print(json.dumps({"success": False, "error": "No active session"}))
                sys.exit(0)

            if not qube.current_session.session_blocks:
                print(json.dumps({"success": False, "error": "No session blocks to discard"}))
                sys.exit(0)

            # Delete the last session block
            last_block = qube.current_session.session_blocks[-1]
            deleted_block = qube.current_session.delete_block(timestamp=last_block.timestamp)

            if deleted_block:
                print(json.dumps({
                    "success": True,
                    "deleted_block_number": deleted_block.block_number,
                    "deleted_timestamp": deleted_block.timestamp
                }))
            else:
                print(json.dumps({"success": False, "error": "Failed to delete last block"}))

        elif command == "get-qube-blocks":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password")

            # Optional pagination parameters (default: 100 blocks, offset 0)
            # Note: After security migration, password is via stdin so indices shifted by -1
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 100
            offset = int(sys.argv[5]) if len(sys.argv) > 5 else 0

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_qube_blocks(qube_id, password, limit, offset)
            print(json.dumps(result))

        elif command == "recall-last-context":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password")

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.recall_last_context(qube_id, password)
            print(json.dumps(result))

        elif command == "generate-speech":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and text required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            text = sys.argv[4]
            password = get_secret("password", argv_index=5)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.generate_speech(qube_id, text, password)
            print(json.dumps(result))

        # ========== Voice Settings Commands ==========

        elif command == "get-voice-settings":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_voice_settings(user_id, qube_id, password)
            print(json.dumps(result))

        elif command == "update-voice-settings":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Parse optional args
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--voice-library-ref", type=str, default=None)
            parser.add_argument("--tts-enabled", type=str, default=None)
            args, _ = parser.parse_known_args(sys.argv[5:])

            tts_enabled = None
            if args.tts_enabled is not None:
                tts_enabled = args.tts_enabled.lower() == "true"

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.update_voice_settings(
                user_id, qube_id, password,
                voice_library_ref=args.voice_library_ref,
                tts_enabled=tts_enabled
            )
            print(json.dumps(result))

        elif command == "preview-voice":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, text, and voice_type required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            text = sys.argv[3]
            voice_type = sys.argv[4]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--language", type=str, default="en")
            parser.add_argument("--design-prompt", type=str, default=None)
            parser.add_argument("--clone-audio-path", type=str, default=None)
            parser.add_argument("--clone-audio-text", type=str, default=None)
            parser.add_argument("--preset-voice", type=str, default=None)
            args, _ = parser.parse_known_args(sys.argv[5:])

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.preview_voice(
                user_id, text, voice_type,
                language=args.language,
                design_prompt=args.design_prompt,
                clone_audio_path=args.clone_audio_path,
                clone_audio_text=args.clone_audio_text,
                preset_voice=args.preset_voice
            )
            print(json.dumps(result))

        elif command == "add-voice-to-library":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, name, and voice_type required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            name = sys.argv[3]
            voice_type = sys.argv[4]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--language", type=str, default="en")
            parser.add_argument("--design-prompt", type=str, default=None)
            parser.add_argument("--clone-audio-path", type=str, default=None)
            parser.add_argument("--clone-audio-text", type=str, default=None)
            args, _ = parser.parse_known_args(sys.argv[5:])

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.add_voice_to_library(
                user_id, name, voice_type,
                language=args.language,
                design_prompt=args.design_prompt,
                clone_audio_path=args.clone_audio_path,
                clone_audio_text=args.clone_audio_text
            )
            print(json.dumps(result))

        elif command == "delete-voice-from-library":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and voice_id required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            voice_id = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.delete_voice_from_library(user_id, voice_id)
            print(json.dumps(result))

        elif command == "get-voice-library":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_voice_library(user_id)
            print(json.dumps(result))

        elif command == "check-qwen3-status":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.check_qwen3_status(user_id)
            print(json.dumps(result))

        elif command == "check-kokoro-status":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.check_kokoro_status(user_id)
            print(json.dumps(result))

        elif command == "get-tts-progress":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_tts_progress(user_id)
            print(json.dumps(result))

        elif command == "download-qwen3-model":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and model_name required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            model_name = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.download_qwen3_model(user_id, model_name)
            print(json.dumps(result))

        elif command == "get-qwen3-download-progress":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and download_id required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            download_id = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_qwen3_download_progress(user_id, download_id)
            print(json.dumps(result))

        elif command == "cancel-qwen3-download":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and download_id required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            download_id = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.cancel_qwen3_download(user_id, download_id)
            print(json.dumps(result))

        elif command == "delete-qwen3-model":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and model_name required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            model_name = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.delete_qwen3_model(user_id, model_name)
            print(json.dumps(result))

        elif command == "check-gpu-acceleration":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.check_gpu_acceleration(user_id)
            print(json.dumps(result))

        elif command == "install-gpu-acceleration":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.install_gpu_acceleration(user_id)
            print(json.dumps(result))

        elif command == "get-gpu-install-progress":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and install_id required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            install_id = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_gpu_install_progress(user_id, install_id)
            print(json.dumps(result))

        elif command == "uninstall-gpu-acceleration":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.uninstall_gpu_acceleration(user_id)
            print(json.dumps(result))

        elif command == "sweep-qube-wallet":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "user_id, qube_id, sweep_address required"}), file=sys.stderr)
                sys.exit(1)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            sweep_address = sys.argv[4]
            password = sys.argv[5] if len(sys.argv) > 5 else None
            user_bridge = GUIBridge(user_id=user_id)
            if password:
                user_bridge.orchestrator.set_master_key(password)
            swept_sats = await user_bridge.orchestrator._sweep_before_delete(qube_id, password, sweep_address)
            print(json.dumps({"success": True, "swept_sats": swept_sats or 0}))

        elif command == "check-local-tts-models":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

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

            print(json.dumps({
                "kokoro_installed": kokoro_installed,
                "sentence_transformers_installed": st_installed,
                "whisper_installed": whisper_installed,
                "hf_home": hf_home,
                "models_dir": str(models_dir or hf_home),
            }))

        elif command == "update-local-tts-models":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

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

            print(json.dumps({
                "success": len(errors) == 0,
                "updated": updated,
                "errors": errors,
            }))

        elif command == "update-qwen3-preferences":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--model-variant", type=str, default=None)
            parser.add_argument("--use-flash-attention", type=str, default=None)
            args, _ = parser.parse_known_args(sys.argv[3:])

            use_flash = None
            if args.use_flash_attention is not None:
                use_flash = args.use_flash_attention.lower() == "true"

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.update_qwen3_preferences(
                user_id,
                model_variant=args.model_variant,
                use_flash_attention=use_flash
            )
            print(json.dumps(result))

        elif command == "record-voice-clone-audio":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--duration", type=int, default=5)
            args, _ = parser.parse_known_args(sys.argv[3:])

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.record_voice_clone_audio(user_id, args.duration)
            print(json.dumps(result))

        elif command == "save-recorded-audio":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            # Read audio data from stdin
            audio_data = sys.stdin.buffer.read()

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.save_recorded_audio(user_id, audio_data)
            print(json.dumps(result))

        elif command == "transcribe-audio":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and audio_path required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            audio_path = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.transcribe_audio(user_id, audio_path)
            print(json.dumps(result))

        # ========== End Voice Settings Commands ==========

        # ========== WSL2 TTS Setup Commands ==========

        elif command == "check-wsl2-tts-status":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.check_wsl2_tts_status(user_id)
            print(json.dumps(result))

        elif command == "setup-wsl2-tts":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.setup_wsl2_tts(user_id)
            print(json.dumps(result))

        elif command == "get-wsl2-tts-setup-progress":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_wsl2_tts_setup_progress(user_id)
            print(json.dumps(result))

        elif command == "start-wsl2-tts-server":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.start_wsl2_tts_server(user_id)
            print(json.dumps(result))

        elif command == "stop-wsl2-tts-server":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.stop_wsl2_tts_server(user_id)
            print(json.dumps(result))

        elif command == "uninstall-wsl2-tts":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.uninstall_wsl2_tts(user_id)
            print(json.dumps(result))

        elif command == "install-wsl2":
            # One-click WSL2 installation (requires admin, shows UAC prompt)
            from audio.wsl2_setup import install_wsl2
            result = await install_wsl2()
            print(json.dumps(result))

        # ========== WSL2 TTS Managed Server Commands ==========

        elif command == "start-wsl2-tts-managed":
            # Start WSL2 TTS server with lifecycle management (hidden window, warmup, health checks)
            # This is called on app startup and stays running to monitor the server
            force = len(sys.argv) > 2 and sys.argv[2].lower() == "true"

            from audio.wsl2_server_manager import start_server
            result = await start_server(force=force)
            print(json.dumps({"success": result}))
            sys.stdout.flush()

            # Keep running to maintain health check loop (monitors and restarts server on crash)
            # This process exits when parent (Tauri) dies or stop-wsl2-tts-managed is called
            if result:
                import os
                parent_pid = os.getppid()

                try:
                    while True:
                        await asyncio.sleep(10)  # Check every 10 seconds
                        # Exit if parent process died (orphaned)
                        try:
                            os.kill(parent_pid, 0)  # Check if parent exists
                        except (OSError, ProcessLookupError):
                            # Parent died, clean up and exit
                            from audio.wsl2_server_manager import cleanup
                            cleanup()
                            break
                except (KeyboardInterrupt, asyncio.CancelledError):
                    from audio.wsl2_server_manager import cleanup
                    cleanup()

        elif command == "stop-wsl2-tts-managed":
            # Stop WSL2 TTS server with manual flag (prevents auto-restart)
            # This is called on app shutdown
            manual = len(sys.argv) < 3 or sys.argv[2].lower() != "false"

            from audio.wsl2_server_manager import stop_server
            result = await stop_server(manual=manual)
            print(json.dumps({"success": result}))

        elif command == "get-wsl2-tts-managed-status":
            # Get current server status for UI indicator
            from audio.wsl2_server_manager import get_server_status
            status = get_server_status()
            print(json.dumps(status))

        # ========== End WSL2 TTS Setup Commands ==========

        elif command == "update-qube-config":
            logger.info(f"🔧 UPDATE_QUBE_CONFIG received: argv={sys.argv[2:]}")
            if len(sys.argv) < 6:
                print(json.dumps({"error": "User ID, Qube ID, ai_model, voice_model, and favorite_color required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            ai_model = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
            voice_model = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
            logger.info(f"🔧 UPDATE_QUBE_CONFIG parsed: user={user_id}, qube={qube_id}, ai_model={ai_model}, voice_model={voice_model}")
            favorite_color = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] else None

            # Parse tts_enabled from 7th argument (convert string "true"/"false" to boolean)
            tts_enabled = None
            if len(sys.argv) > 7 and sys.argv[7]:
                tts_enabled = sys.argv[7].lower() == "true"

            # Parse evaluation_model from 8th argument
            evaluation_model = sys.argv[8] if len(sys.argv) > 8 and sys.argv[8] else None

            # Parse password from 9th argument (needed for chain_state encryption)
            password = get_secret("password", argv_index=9)
            logger.info(f"🔧 UPDATE_QUBE_CONFIG: password received={bool(password)}, argv_count={len(sys.argv)}")

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key if password provided (required for chain_state updates)
            if password:
                user_bridge.orchestrator.set_master_key(password)
                logger.info(f"🔧 UPDATE_QUBE_CONFIG: master_key SET")
            else:
                logger.warning(f"🔧 UPDATE_QUBE_CONFIG: NO PASSWORD - master_key NOT set!")

            result = await user_bridge.update_qube_config(qube_id, ai_model, voice_model, favorite_color, tts_enabled, evaluation_model)
            print(json.dumps(result))

        elif command == "update-qube-avatar":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and avatar_path required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            avatar_path = sys.argv[4]

            import shutil
            from datetime import timezone

            user_bridge = GUIBridge(user_id=user_id)
            data_dir = user_bridge.orchestrator.data_dir

            # Find qube directory
            qubes_dir = data_dir / "qubes"
            qube_dir = None
            for d in qubes_dir.iterdir():
                if d.is_dir() and qube_id in d.name:
                    qube_dir = d
                    break

            if not qube_dir:
                print(json.dumps({"success": False, "error": f"Qube {qube_id} not found"}))
            else:
                try:
                    chain_dir = qube_dir / "chain"
                    dest_path = chain_dir / f"{qube_id}_avatar.png"

                    # Copy new avatar over old one
                    shutil.copy2(avatar_path, dest_path)

                    # Update genesis.json avatar field
                    genesis_file = chain_dir / "genesis.json"
                    if genesis_file.exists():
                        with open(genesis_file, 'r', encoding='utf-8') as f:
                            genesis = json.load(f)
                        genesis["avatar"] = {
                            "source": "uploaded",
                            "ipfs_cid": None,
                            "local_path": dest_path.name,
                            "file_format": "png",
                            "dimensions": "unknown",
                        }
                        with open(genesis_file, 'w', encoding='utf-8') as f:
                            json.dump(genesis, f, indent=2)

                    # Update BCMR icon/image uris (clear IPFS cid, set pending re-upload)
                    qube_name = qube_dir.name.rsplit('_', 1)[0]
                    bcmr_file = qube_dir / "blockchain" / f"{qube_name}_bcmr.json"
                    if bcmr_file.exists():
                        with open(bcmr_file, 'r', encoding='utf-8') as f:
                            bcmr = json.load(f)
                        now = datetime.now(timezone.utc).isoformat()
                        bcmr["latestRevision"] = now
                        for identity_key, identity_revisions in bcmr.get("identities", {}).items():
                            latest_ts = max(identity_revisions.keys())
                            revision = identity_revisions[latest_ts]
                            uris = revision.get("uris", {})
                            # Clear IPFS icon/image — will be updated on next IPFS sync
                            uris.pop("icon", None)
                            uris.pop("image", None)
                            uris["icon_pending_upload"] = dest_path.name
                            revision["uris"] = uris
                            # Add new revision entry with updated timestamp
                            identity_revisions[now] = revision
                        with open(bcmr_file, 'w', encoding='utf-8') as f:
                            json.dump(bcmr, f, indent=2)

                    print(json.dumps({"success": True, "avatar_path": str(dest_path)}))
                except Exception as e:
                    logger.error(f"Failed to update avatar: {e}", exc_info=True)
                    print(json.dumps({"success": False, "error": str(e)}))

        elif command == "reload-ai-keys":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Reload AI keys for the qube
            user_bridge.orchestrator.reload_ai_keys(qube_id)

            print(json.dumps({"success": True, "qube_id": qube_id}))

        elif command == "delete-qube":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.delete_qube(qube_id)
            print(json.dumps(result))

        elif command == "reset-qube":
            # DEV ONLY: Reset qube to fresh state while preserving identity
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            # Password passed via stdin for security - needed for encrypted chain_state
            password = get_secret("password", required=False)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.reset_qube(qube_id, password)
            print(json.dumps(result))

        elif command == "save-image":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and image URL required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            image_url = sys.argv[4]

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.save_image(qube_id, image_url)
            print(json.dumps(result))

        elif command == "upload-avatar-to-ipfs":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.upload_avatar_to_ipfs(qube_id, password)
            print(json.dumps(result))

        elif command == "analyze-image":
            if len(sys.argv) < 6:
                print(json.dumps({"error": "User ID, Qube ID, image base64 file path, and user message required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            image_base64_file = sys.argv[4]  # Now expects file path instead of data
            user_message = sys.argv[5]
            password = get_secret("password", argv_index=6, required=False)

            # Read base64 data from temporary file
            with open(image_base64_file, 'r') as f:
                image_base64 = f.read()

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.analyze_image(qube_id, image_base64, user_message, password)
            print(json.dumps(result))

        elif command == "start-multi-qube-conversation":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, qube IDs (comma-separated), and initial prompt required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_ids_str = sys.argv[3]  # Comma-separated qube IDs
            initial_prompt = sys.argv[4]
            password = get_secret("password", argv_index=5)
            conversation_mode = sys.argv[6] if len(sys.argv) > 6 else "open_discussion"

            # Parse qube IDs
            qube_ids = [qid.strip() for qid in qube_ids_str.split(',')]

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            try:
                # Load all qubes
                for qube_id in qube_ids:
                    if qube_id not in user_bridge.orchestrator.qubes:
                        await user_bridge.orchestrator.load_qube(qube_id)

                # Start conversation
                result = await user_bridge.orchestrator.start_multi_qube_conversation(
                    qube_ids=qube_ids,
                    initial_prompt=initial_prompt,
                    conversation_mode=conversation_mode
                )

                print(json.dumps(result))
            except Exception as e:
                import traceback
                logger.error(f"start_multi_qube_conversation failed: {e}", exc_info=True)
                print(json.dumps({
                    "error": str(e),
                    "traceback": traceback.format_exc()
                }))

        elif command == "get-next-speaker":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and conversation ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            conversation_id = sys.argv[3]
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # Reconstruct conversation if not in active_conversations
            # (Same logic as continue-multi-qube-conversation)
            if conversation_id not in user_bridge.orchestrator.active_conversations:
                from core.multi_qube_conversation import MultiQubeConversation

                # Find and load Qubes with this conversation_id
                qubes_list = await user_bridge.orchestrator.list_qubes()
                participant_qube_ids = []

                for qube_meta in qubes_list:
                    qube_id = qube_meta["qube_id"]

                    # Load the qube
                    if qube_id not in user_bridge.orchestrator.qubes:
                        await user_bridge.orchestrator.load_qube(qube_id)

                    qube = user_bridge.orchestrator.qubes[qube_id]

                    # Check if this qube has blocks from this conversation
                    if qube.current_session:
                        for block in qube.current_session.session_blocks:
                            if hasattr(block, 'content') and isinstance(block.content, dict):
                                if block.content.get('conversation_id') == conversation_id:
                                    if qube_id not in participant_qube_ids:
                                        participant_qube_ids.append(qube_id)
                                    break

                if not participant_qube_ids:
                    print(json.dumps({"error": f"Conversation not found: {conversation_id}"}), file=sys.stderr)
                    sys.exit(1)

                participating_qubes = [user_bridge.orchestrator.qubes[qid] for qid in participant_qube_ids]

                # Reconstruct conversation object (same as continue-multi-qube-conversation)
                conversation = MultiQubeConversation(
                    participating_qubes=participating_qubes,
                    user_id=user_id,
                    conversation_mode="open_discussion"
                )

                conversation.conversation_id = conversation_id

                # Rebuild conversation history and state from session blocks
                conversation.turn_number = 0
                conversation.conversation_history = []

                # Collect all conversation blocks
                conv_blocks = []
                for block in participating_qubes[0].current_session.session_blocks:
                    if hasattr(block, 'content') and isinstance(block.content, dict):
                        if block.content.get('conversation_id') == conversation_id:
                            conv_blocks.append(block)

                # Sort by turn number
                conv_blocks.sort(key=lambda b: b.content.get('turn_number', 0))

                # Rebuild state from blocks
                for block in conv_blocks:
                    turn_num = block.content.get('turn_number', 0)
                    if turn_num > conversation.turn_number:
                        conversation.turn_number = turn_num

                    # Rebuild conversation history
                    if turn_num > 0:
                        conversation.conversation_history.append({
                            "speaker_id": block.content.get('speaker_id'),
                            "speaker_name": block.content.get('speaker_name'),
                            "message": block.content.get('message_body', ''),
                            "turn_number": turn_num,
                            "timestamp": block.timestamp
                        })

                        # Update turn counts
                        speaker_id = block.content.get('speaker_id')
                        if speaker_id and speaker_id in conversation.turn_counts:
                            conversation.turn_counts[speaker_id] += 1

                # Calculate current speaker index for round-robin
                conversation.current_speaker_index = conversation.turn_number % len(participating_qubes)

                # Restore last_speaker_id from the most recent conversation block
                if conv_blocks:
                    last_block = conv_blocks[-1]
                    conversation.last_speaker_id = last_block.content.get('speaker_id')

                # Store in active conversations
                user_bridge.orchestrator.active_conversations[conversation_id] = conversation

            # Get next speaker info (lightweight operation)
            result = await user_bridge.orchestrator.get_next_speaker(
                conversation_id=conversation_id
            )

            print(json.dumps(result))

        elif command == "continue-multi-qube-conversation":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and conversation ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            conversation_id = sys.argv[3]
            # Optional skip_tools flag (argv[4] if present)
            skip_tools = False
            if len(sys.argv) > 4 and sys.argv[4] in ("true", "false"):
                skip_tools = sys.argv[4] == "true"

            # Optional participant_ids JSON array (argv[5] if present) - OPTIMIZATION
            # If provided, skip scanning all qubes and only load these
            provided_participant_ids = None
            if len(sys.argv) > 5:
                try:
                    provided_participant_ids = json.loads(sys.argv[5])
                    logger.info(f"Using provided participant_ids: {provided_participant_ids}")
                except json.JSONDecodeError:
                    pass  # Ignore invalid JSON, fall back to scanning

            password = get_secret("password")

            import time
            cmd_start = time.time()

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            load_start = time.time()
            # OPTIMIZATION: If participant_ids provided, skip scanning all qubes
            if provided_participant_ids:
                participant_qube_ids = provided_participant_ids
                # Only load the qubes we need
                for qube_id in participant_qube_ids:
                    if qube_id not in user_bridge.orchestrator.qubes:
                        await user_bridge.orchestrator.load_qube(qube_id)
                logger.info(f"TIMING: Loaded {len(participant_qube_ids)} qubes in {(time.time()-load_start)*1000:.0f}ms")
            else:
                # Fall back to scanning all qubes (slow)
                logger.warning("No participant_ids provided, scanning all qubes (slow)")
                qubes_list = await user_bridge.orchestrator.list_qubes()
                participant_qube_ids = []

                for qube_meta in qubes_list:
                    qube_id = qube_meta["qube_id"]

                    # Load the qube
                    if qube_id not in user_bridge.orchestrator.qubes:
                        await user_bridge.orchestrator.load_qube(qube_id)

                    qube = user_bridge.orchestrator.qubes[qube_id]

                    # Check if this qube has blocks from this conversation
                    if qube.current_session:
                        for block in qube.current_session.session_blocks:
                            if hasattr(block, 'content') and isinstance(block.content, dict):
                                if block.content.get('conversation_id') == conversation_id:
                                    if qube_id not in participant_qube_ids:
                                        participant_qube_ids.append(qube_id)
                                    break

            if not participant_qube_ids:
                print(json.dumps({"error": f"Conversation not found: {conversation_id}"}), file=sys.stderr)
                sys.exit(1)

            # Recreate the conversation if it doesn't exist
            if conversation_id not in user_bridge.orchestrator.active_conversations:
                from core.multi_qube_conversation import MultiQubeConversation

                participating_qubes = [user_bridge.orchestrator.qubes[qid] for qid in participant_qube_ids]

                # Reconstruct conversation object
                conversation = MultiQubeConversation(
                    participating_qubes=participating_qubes,
                    user_id=user_id,
                    conversation_mode="open_discussion"  # Default, actual mode is in blocks
                )

                # Override the conversation_id to match
                conversation.conversation_id = conversation_id

                # Rebuild conversation history and state from session blocks
                conversation.turn_number = 0
                conversation.conversation_history = []

                # Collect all conversation blocks
                conv_blocks = []
                for block in participating_qubes[0].current_session.session_blocks:
                    if hasattr(block, 'content') and isinstance(block.content, dict):
                        if block.content.get('conversation_id') == conversation_id:
                            conv_blocks.append(block)

                # Sort by turn number
                conv_blocks.sort(key=lambda b: b.content.get('turn_number', 0))

                # Rebuild state from blocks
                for block in conv_blocks:
                    turn_num = block.content.get('turn_number', 0)
                    if turn_num > conversation.turn_number:
                        conversation.turn_number = turn_num

                    # Rebuild conversation history
                    if turn_num > 0:  # Skip user's initial message (turn 0)
                        conversation.conversation_history.append({
                            "speaker_id": block.content.get('speaker_id'),
                            "speaker_name": block.content.get('speaker_name'),
                            "message": block.content.get('message_body', ''),
                            "turn_number": turn_num,
                            "timestamp": block.timestamp
                        })

                        # Update turn counts
                        speaker_id = block.content.get('speaker_id')
                        if speaker_id and speaker_id in conversation.turn_counts:
                            conversation.turn_counts[speaker_id] += 1

                # Calculate current speaker index for round-robin
                # The next speaker should be whoever hasn't spoken yet in this round
                # Or if everyone has spoken equally, go to the next in line
                conversation.current_speaker_index = conversation.turn_number % len(participating_qubes)

                # Restore last_speaker_id from the most recent conversation block
                if conv_blocks:
                    last_block = conv_blocks[-1]  # Last block (highest turn number)
                    conversation.last_speaker_id = last_block.content.get('speaker_id')
                    logger.info(f"Restored last_speaker_id: {conversation.last_speaker_id}")

                # Store in active conversations
                user_bridge.orchestrator.active_conversations[conversation_id] = conversation

            # Continue conversation (with optional skip_tools for faster prefetch)
            api_start = time.time()
            result = await user_bridge.orchestrator.continue_multi_qube_conversation(
                conversation_id=conversation_id,
                skip_tools=skip_tools
            )
            api_time = time.time() - api_start
            total_time = time.time() - cmd_start
            logger.info(f"TIMING: API call took {api_time*1000:.0f}ms, total command {total_time*1000:.0f}ms")

            print(json.dumps(result))

        elif command == "run-background-turns":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, conversation ID, and exclude IDs required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            conversation_id = sys.argv[3]
            exclude_ids_json = sys.argv[4]
            password = get_secret("password", argv_index=5)

            # Parse exclude IDs
            try:
                exclude_ids = json.loads(exclude_ids_json)
            except json.JSONDecodeError:
                print(json.dumps({"error": "Invalid exclude IDs JSON"}), file=sys.stderr)
                sys.exit(1)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # Find and load Qubes with this conversation_id in their sessions
            # (Same reconstruction logic as continue-multi-qube-conversation)
            qubes_list = await user_bridge.orchestrator.list_qubes()
            participant_qube_ids = []

            for qube_meta in qubes_list:
                qube_id = qube_meta["qube_id"]

                # Load the qube
                if qube_id not in user_bridge.orchestrator.qubes:
                    await user_bridge.orchestrator.load_qube(qube_id)

                qube = user_bridge.orchestrator.qubes[qube_id]

                # Check if this qube has blocks from this conversation
                if qube.current_session:
                    for block in qube.current_session.session_blocks:
                        if hasattr(block, 'content') and isinstance(block.content, dict):
                            if block.content.get('conversation_id') == conversation_id:
                                if qube_id not in participant_qube_ids:
                                    participant_qube_ids.append(qube_id)
                                break

            if not participant_qube_ids:
                print(json.dumps({"error": f"Conversation not found: {conversation_id}"}), file=sys.stderr)
                sys.exit(1)

            # Recreate the conversation if it doesn't exist
            if conversation_id not in user_bridge.orchestrator.active_conversations:
                from core.multi_qube_conversation import MultiQubeConversation

                participating_qubes = [user_bridge.orchestrator.qubes[qid] for qid in participant_qube_ids]

                # Reconstruct conversation object
                conversation = MultiQubeConversation(
                    participating_qubes=participating_qubes,
                    user_id=user_id,
                    conversation_mode="open_discussion"
                )

                # Override the conversation_id to match
                conversation.conversation_id = conversation_id

                # Rebuild conversation history and state from session blocks
                conversation.turn_number = 0
                conversation.conversation_history = []

                # Collect all conversation blocks
                conv_blocks = []
                for block in participating_qubes[0].current_session.session_blocks:
                    if hasattr(block, 'content') and isinstance(block.content, dict):
                        if block.content.get('conversation_id') == conversation_id:
                            conv_blocks.append(block)

                # Sort by turn number
                conv_blocks.sort(key=lambda b: b.content.get('turn_number', 0))

                # Rebuild state from blocks
                for block in conv_blocks:
                    turn_num = block.content.get('turn_number', 0)
                    if turn_num > conversation.turn_number:
                        conversation.turn_number = turn_num

                    # Rebuild conversation history
                    if turn_num > 0:  # Skip user's initial message (turn 0)
                        conversation.conversation_history.append({
                            "speaker_id": block.content.get('speaker_id'),
                            "speaker_name": block.content.get('speaker_name'),
                            "message": block.content.get('message_body', ''),
                            "turn_number": turn_num,
                            "timestamp": block.timestamp
                        })

                        # Update turn counts
                        speaker_id = block.content.get('speaker_id')
                        if speaker_id and speaker_id in conversation.turn_counts:
                            conversation.turn_counts[speaker_id] += 1

                # Calculate current speaker index for round-robin
                conversation.current_speaker_index = conversation.turn_number % len(participating_qubes)

                # Restore last_speaker_id from the most recent conversation block
                if conv_blocks:
                    last_block = conv_blocks[-1]
                    conversation.last_speaker_id = last_block.content.get('speaker_id')

                # Store in active conversations
                user_bridge.orchestrator.active_conversations[conversation_id] = conversation
            else:
                conversation = user_bridge.orchestrator.active_conversations[conversation_id]

            # Run background turns for non-excluded qubes
            result = await conversation.run_background_turns(exclude_qube_ids=exclude_ids)

            print(json.dumps(result))

        elif command == "inject-multi-qube-user-message":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, conversation ID, and message required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            conversation_id = sys.argv[3]
            message_arg = sys.argv[4]
            password = get_secret("password", argv_index=5)

            # Check if message is a file reference
            if message_arg.startswith("@file:"):
                # Read message from file
                file_path = message_arg[6:]  # Remove @file: prefix
                with open(file_path, 'r', encoding='utf-8') as f:
                    message = f.read()
            else:
                message = message_arg

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # Find and load Qubes with this conversation_id in their sessions
            # (Same logic as continue-multi-qube-conversation)
            qubes_list = await user_bridge.orchestrator.list_qubes()
            participant_qube_ids = []

            for qube_meta in qubes_list:
                qube_id = qube_meta["qube_id"]

                # Load the qube
                if qube_id not in user_bridge.orchestrator.qubes:
                    await user_bridge.orchestrator.load_qube(qube_id)

                qube = user_bridge.orchestrator.qubes[qube_id]

                # Check if this qube has blocks from this conversation
                if qube.current_session:
                    for block in qube.current_session.session_blocks:
                        if hasattr(block, 'content') and isinstance(block.content, dict):
                            if block.content.get('conversation_id') == conversation_id:
                                if qube_id not in participant_qube_ids:
                                    participant_qube_ids.append(qube_id)
                                break

            if not participant_qube_ids:
                print(json.dumps({"error": f"Conversation not found: {conversation_id}"}), file=sys.stderr)
                sys.exit(1)

            # Recreate the conversation if it doesn't exist
            if conversation_id not in user_bridge.orchestrator.active_conversations:
                from core.multi_qube_conversation import MultiQubeConversation

                participating_qubes = [user_bridge.orchestrator.qubes[qid] for qid in participant_qube_ids]

                # Reconstruct conversation object
                conversation = MultiQubeConversation(
                    participating_qubes=participating_qubes,
                    user_id=user_id,
                    conversation_mode="open_discussion"  # Default, actual mode is in blocks
                )

                # Override the conversation_id to match
                conversation.conversation_id = conversation_id

                # Rebuild conversation history and state from session blocks
                conversation.turn_number = 0
                conversation.conversation_history = []

                # Collect all conversation blocks
                conv_blocks = []
                for block in participating_qubes[0].current_session.session_blocks:
                    if hasattr(block, 'content') and isinstance(block.content, dict):
                        if block.content.get('conversation_id') == conversation_id:
                            conv_blocks.append(block)

                # Sort by turn number
                conv_blocks.sort(key=lambda b: b.content.get('turn_number', 0))

                # Rebuild state from blocks
                for block in conv_blocks:
                    turn_num = block.content.get('turn_number', 0)
                    if turn_num > conversation.turn_number:
                        conversation.turn_number = turn_num

                    # Rebuild conversation history
                    if turn_num > 0:  # Skip user's initial message (turn 0)
                        conversation.conversation_history.append({
                            "speaker_id": block.content.get('speaker_id'),
                            "speaker_name": block.content.get('speaker_name'),
                            "message": block.content.get('message_body', ''),
                            "turn_number": turn_num,
                            "timestamp": block.timestamp
                        })

                        # Update turn counts
                        speaker_id = block.content.get('speaker_id')
                        if speaker_id and speaker_id in conversation.turn_counts:
                            conversation.turn_counts[speaker_id] += 1

                # Calculate current speaker index for round-robin
                conversation.current_speaker_index = conversation.turn_number % len(participating_qubes)

                # Restore last_speaker_id from the most recent conversation block
                if conv_blocks:
                    last_block = conv_blocks[-1]  # Last block (highest turn number)
                    conversation.last_speaker_id = last_block.content.get('speaker_id')
                    logger.info(f"Restored last_speaker_id: {conversation.last_speaker_id}")

                # Store in active conversations
                user_bridge.orchestrator.active_conversations[conversation_id] = conversation

            # Inject user message
            result = await user_bridge.orchestrator.inject_user_message_to_conversation(
                conversation_id=conversation_id,
                user_message=message
            )

            print(json.dumps(result))

        elif command == "lock-in-multi-qube-response":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, conversation ID, and timestamp required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            conversation_id = sys.argv[3]
            timestamp = int(sys.argv[4])

            # Parse optional --participant-ids argument
            participant_ids_json = None
            password_argv_index = 5
            for i, arg in enumerate(sys.argv[5:], start=5):
                if arg == "--participant-ids" and i + 1 < len(sys.argv):
                    participant_ids_json = sys.argv[i + 1]
                    password_argv_index = i + 2
                    break

            password = get_secret("password", argv_index=password_argv_index)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # Use participant_ids if provided (much faster - skips scanning all qubes)
            if participant_ids_json:
                import json as json_module
                participant_qube_ids = json_module.loads(participant_ids_json)
            else:
                participant_qube_ids = None

            # Reconstruct conversation if not in active_conversations
            if conversation_id not in user_bridge.orchestrator.active_conversations:
                from core.multi_qube_conversation import MultiQubeConversation

                # If we have participant_ids, use them directly
                if participant_qube_ids:
                    for qube_id in participant_qube_ids:
                        if qube_id not in user_bridge.orchestrator.qubes:
                            await user_bridge.orchestrator.load_qube(qube_id)
                else:
                    # Fallback: scan all qubes (slow)
                    qubes_list = await user_bridge.orchestrator.list_qubes()
                    participant_qube_ids = []

                    for qube_meta in qubes_list:
                        qube_id = qube_meta["qube_id"]

                        if qube_id not in user_bridge.orchestrator.qubes:
                            await user_bridge.orchestrator.load_qube(qube_id)

                        qube = user_bridge.orchestrator.qubes[qube_id]

                        if qube.current_session:
                            for block in qube.current_session.session_blocks:
                                if hasattr(block, 'content') and isinstance(block.content, dict):
                                    if block.content.get('conversation_id') == conversation_id:
                                        if qube_id not in participant_qube_ids:
                                            participant_qube_ids.append(qube_id)
                                        break

                if not participant_qube_ids:
                    print(json.dumps({"error": f"Conversation not found: {conversation_id}"}), file=sys.stderr)
                    sys.exit(1)

                participating_qubes = [user_bridge.orchestrator.qubes[qid] for qid in participant_qube_ids]

                conversation = MultiQubeConversation(
                    participating_qubes=participating_qubes,
                    user_id=user_id,
                    conversation_mode="open_discussion"
                )

                conversation.conversation_id = conversation_id
                user_bridge.orchestrator.active_conversations[conversation_id] = conversation

            conversation = user_bridge.orchestrator.active_conversations[conversation_id]

            # Lock in the response
            conversation.lock_in_response(timestamp)

            print(json.dumps({"success": True, "timestamp": timestamp}))

        elif command == "end-multi-qube-conversation":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, conversation ID, and anchor flag required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            conversation_id = sys.argv[3]
            anchor = sys.argv[4].lower() == "true" if len(sys.argv) > 4 else True
            password = get_secret("password", argv_index=5)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key
            user_bridge.orchestrator.set_master_key(password)

            # End conversation
            result = await user_bridge.orchestrator.end_multi_qube_conversation(
                conversation_id=conversation_id,
                anchor=anchor
            )

            print(json.dumps(result))

        elif command == "get-configured-api-keys":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            password = get_secret("password", argv_index=3)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key with password to decrypt keys
            user_bridge.orchestrator.set_master_key(password)

            try:
                # Get list of configured providers
                providers = user_bridge.orchestrator.list_configured_providers()
                print(json.dumps({"providers": providers}))
            except Exception as e:
                logger.error(f"Failed to get configured API keys: {e}", exc_info=True)
                # Return empty list if unable to decrypt (wrong password, no keys, etc.)
                print(json.dumps({"providers": []}))

        elif command == "save-api-key":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and provider required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            provider = sys.argv[3]
            api_key = get_secret("api_key", argv_index=4)
            password = get_secret("password", argv_index=5)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key with password
            user_bridge.orchestrator.set_master_key(password)

            try:
                user_bridge.orchestrator.update_api_key(provider, api_key)
                print(json.dumps({"success": True}))
            except Exception as e:
                logger.error(f"Failed to save API key: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "validate-api-key":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and provider required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            provider = sys.argv[3]
            api_key = get_secret("api_key", argv_index=4)
            password = get_secret("password", argv_index=5, required=False)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # If api_key is the placeholder, load the saved key
            if api_key == "__SAVED__":
                if not password:
                    print(json.dumps({
                        "valid": False,
                        "message": "Password required to test saved key",
                        "details": None
                    }))
                    sys.exit(0)

                user_bridge.orchestrator.set_master_key(password)

                try:
                    # Load saved keys
                    stored_keys = user_bridge.orchestrator.get_api_keys()
                    api_key = getattr(stored_keys, provider, None)

                    if not api_key:
                        print(json.dumps({
                            "valid": False,
                            "message": f"No saved key found for {provider}",
                            "details": None
                        }))
                        sys.exit(0)
                except Exception as e:
                    logger.error(f"Failed to load saved key: {e}", exc_info=True)
                    print(json.dumps({
                        "valid": False,
                        "message": f"Failed to load saved key: {str(e)}",
                        "details": None
                    }))
                    sys.exit(0)

            # Validate the API key (doesn't require master password - just makes API call)
            try:
                result = await user_bridge.orchestrator.validate_api_key(provider, api_key)
                print(json.dumps(result))
            except Exception as e:
                logger.error(f"Failed to validate API key: {e}", exc_info=True)
                print(json.dumps({
                    "valid": False,
                    "message": f"Validation error: {str(e)}",
                    "details": None
                }))

        elif command == "delete-api-key":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and provider required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            provider = sys.argv[3]
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            # Set master key with password
            user_bridge.orchestrator.set_master_key(password)

            try:
                user_bridge.orchestrator.delete_api_key(provider)
                print(json.dumps({"success": True}))
            except Exception as e:
                logger.error(f"Failed to delete API key: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "get-block-preferences":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            try:
                prefs = user_bridge.orchestrator.get_block_preferences()
                print(json.dumps({
                    "individual_auto_anchor": prefs.individual_auto_anchor,
                    "individual_anchor_threshold": prefs.individual_anchor_threshold,
                    "group_auto_anchor": prefs.group_auto_anchor,
                    "group_anchor_threshold": prefs.group_anchor_threshold,
                    "auto_sync_ipfs_on_anchor": prefs.auto_sync_ipfs_on_anchor,
                    "auto_sync_ipfs_periodic": prefs.auto_sync_ipfs_periodic,
                    "auto_sync_ipfs_interval": prefs.auto_sync_ipfs_interval
                }))
            except Exception as e:
                logger.error(f"Failed to get block preferences: {e}", exc_info=True)
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "update-block-preferences":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            # Parse optional arguments
            individual_auto_anchor = None
            individual_anchor_threshold = None
            group_auto_anchor = None
            group_anchor_threshold = None
            auto_sync_ipfs_on_anchor = None
            auto_sync_ipfs_periodic = None
            auto_sync_ipfs_interval = None

            i = 3
            while i < len(sys.argv):
                if sys.argv[i] == "--individual-auto-anchor":
                    individual_auto_anchor = sys.argv[i + 1].lower() == "true"
                    i += 2
                elif sys.argv[i] == "--individual-anchor-threshold":
                    individual_anchor_threshold = int(sys.argv[i + 1])
                    i += 2
                elif sys.argv[i] == "--group-auto-anchor":
                    group_auto_anchor = sys.argv[i + 1].lower() == "true"
                    i += 2
                elif sys.argv[i] == "--group-anchor-threshold":
                    group_anchor_threshold = int(sys.argv[i + 1])
                    i += 2
                elif sys.argv[i] == "--auto-sync-ipfs":
                    auto_sync_ipfs_on_anchor = sys.argv[i + 1].lower() == "true"
                    i += 2
                elif sys.argv[i] == "--auto-sync-ipfs-periodic":
                    auto_sync_ipfs_periodic = sys.argv[i + 1].lower() == "true"
                    i += 2
                elif sys.argv[i] == "--auto-sync-ipfs-interval":
                    auto_sync_ipfs_interval = int(sys.argv[i + 1])
                    i += 2
                else:
                    i += 1

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)

            try:
                prefs = user_bridge.orchestrator.update_block_preferences(
                    individual_auto_anchor=individual_auto_anchor,
                    individual_anchor_threshold=individual_anchor_threshold,
                    group_auto_anchor=group_auto_anchor,
                    group_anchor_threshold=group_anchor_threshold,
                    auto_sync_ipfs_on_anchor=auto_sync_ipfs_on_anchor,
                    auto_sync_ipfs_periodic=auto_sync_ipfs_periodic,
                    auto_sync_ipfs_interval=auto_sync_ipfs_interval
                )
                print(json.dumps({
                    "individual_auto_anchor": prefs.individual_auto_anchor,
                    "individual_anchor_threshold": prefs.individual_anchor_threshold,
                    "group_auto_anchor": prefs.group_auto_anchor,
                    "group_anchor_threshold": prefs.group_anchor_threshold,
                    "auto_sync_ipfs_on_anchor": prefs.auto_sync_ipfs_on_anchor,
                    "auto_sync_ipfs_periodic": prefs.auto_sync_ipfs_periodic,
                    "auto_sync_ipfs_interval": prefs.auto_sync_ipfs_interval
                }))
            except Exception as e:
                logger.error(f"Failed to update block preferences: {e}", exc_info=True)
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "get-relationship-difficulty":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            try:
                from config.global_settings import get_global_settings
                global_settings = get_global_settings()
                difficulty = global_settings.get_difficulty()
                preset = global_settings.get_preset(difficulty)

                print(json.dumps({
                    "difficulty": difficulty,
                    "description": preset["description"]
                }))
            except Exception as e:
                logger.error(f"Failed to get relationship difficulty: {e}", exc_info=True)
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "set-relationship-difficulty":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and difficulty required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            difficulty = sys.argv[3]

            try:
                from config.global_settings import get_global_settings
                global_settings = get_global_settings()
                global_settings.set_difficulty(difficulty)
                preset = global_settings.get_preset(difficulty)

                print(json.dumps({
                    "difficulty": difficulty,
                    "description": preset["description"]
                }))
            except Exception as e:
                logger.error(f"Failed to set relationship difficulty: {e}", exc_info=True)
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "get-difficulty-presets":
            try:
                from config.global_settings import GlobalSettings

                # Format presets for JSON response
                presets_response = {}
                for difficulty, preset in GlobalSettings.PRESETS.items():
                    presets_response[difficulty] = {
                        "name": preset["name"],
                        "description": preset["description"],
                        "min_interactions": preset["min_interactions"]
                    }

                print(json.dumps(presets_response))
            except Exception as e:
                logger.error(f"Failed to get difficulty presets: {e}", exc_info=True)
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "get-qube-relationships":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_qube_relationships(qube_id, password)
            print(json.dumps(result))

        elif command == "get-relationship-timeline":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and Entity ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            # entity_id can be either a qube_id (8 hex) or human username (alphanumeric)
            entity_id = validate_user_id(sys.argv[4])  # Works for both qube IDs and human usernames
            password = get_secret("password", argv_index=5, required=False)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_relationship_timeline(qube_id, entity_id, password)
            print(json.dumps(result))

        elif command == "reset-qube-relationships":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            # SECURITY: Validate qube_id
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.reset_qube_relationships(qube_id, password)
            print(json.dumps(result))

        elif command == "get-google-tts-path":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            try:
                from config.user_preferences import UserPreferencesManager

                # Get user data directory
                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                # Get Google TTS path from preferences
                path = prefs_manager.get_google_tts_path()

                print(json.dumps({"path": path}))
            except Exception as e:
                logger.error(f"Failed to get Google TTS path: {e}", exc_info=True)
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "set-google-tts-path":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and path required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            path = sys.argv[3]

            try:
                from config.user_preferences import UserPreferencesManager

                # Get user data directory
                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                # Set Google TTS path (empty string = clear the path)
                # Strip surrounding quotes if present (common copy-paste issue)
                clean_path = path.strip().strip('"').strip("'")
                if clean_path == "" or clean_path.lower() == "none":
                    prefs_manager.update_google_tts_path(None)
                    print(json.dumps({"success": True, "path": None}))
                else:
                    prefs_manager.update_google_tts_path(clean_path)
                    print(json.dumps({"success": True, "path": clean_path}))
            except Exception as e:
                logger.error(f"Failed to set Google TTS path: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "get-decision-config":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            try:
                from config.user_preferences import UserPreferencesManager
                from dataclasses import asdict

                # Get user data directory
                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                # Get decision config from preferences
                config = prefs_manager.get_decision_config()

                print(json.dumps({
                    "success": True,
                    "config": asdict(config)
                }))
            except Exception as e:
                logger.error(f"Failed to get decision config: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "update-decision-config":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and config JSON required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            config_json = sys.argv[3]

            try:
                from config.user_preferences import UserPreferencesManager

                # Get user data directory
                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                # Parse config data
                config_data = json.loads(config_json)

                # Update decision config
                prefs_manager.update_decision_config(**config_data)

                print(json.dumps({
                    "success": True,
                    "message": "Decision config updated"
                }))
            except Exception as e:
                logger.error(f"Failed to update decision config: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        # =====================================================================
        # Memory Recall Config Commands
        # =====================================================================

        elif command == "get-memory-config":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            try:
                from config.user_preferences import UserPreferencesManager
                from dataclasses import asdict

                # Get user data directory
                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                # Get memory config from preferences
                config = prefs_manager.get_memory_config()

                print(json.dumps({
                    "success": True,
                    "config": asdict(config)
                }))
            except Exception as e:
                logger.error(f"Failed to get memory config: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "update-memory-config":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and config JSON required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            config_json = sys.argv[3]

            try:
                from config.user_preferences import UserPreferencesManager

                # Get user data directory
                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                # Parse config data
                config_data = json.loads(config_json)

                # Update memory config
                prefs_manager.update_memory_config(**config_data)

                print(json.dumps({
                    "success": True,
                    "message": "Memory config updated"
                }))
            except Exception as e:
                logger.error(f"Failed to update memory config: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        # =====================================================================
        # Onboarding Tutorial Commands
        # =====================================================================

        elif command == "get-onboarding-preferences":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            try:
                from config.user_preferences import UserPreferencesManager
                from dataclasses import asdict

                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                onboarding = prefs_manager.get_onboarding_preferences()

                print(json.dumps({
                    "success": True,
                    "onboarding": asdict(onboarding)
                }))
            except Exception as e:
                logger.error(f"Failed to get onboarding preferences: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "mark-tutorial-seen":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and tab name required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            tab_name = sys.argv[3]

            try:
                from config.user_preferences import UserPreferencesManager

                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                prefs_manager.mark_tutorial_seen(tab_name)

                print(json.dumps({
                    "success": True,
                    "message": f"Tutorial for {tab_name} marked as seen"
                }))
            except Exception as e:
                logger.error(f"Failed to mark tutorial seen: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "reset-tutorial":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and tab name required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            tab_name = sys.argv[3]

            try:
                from config.user_preferences import UserPreferencesManager

                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                prefs_manager.reset_tutorial(tab_name)

                print(json.dumps({
                    "success": True,
                    "message": f"Tutorial for {tab_name} reset"
                }))
            except Exception as e:
                logger.error(f"Failed to reset tutorial: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "reset-all-tutorials":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            try:
                from config.user_preferences import UserPreferencesManager

                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                prefs_manager.reset_all_tutorials()

                print(json.dumps({
                    "success": True,
                    "message": "All tutorials reset"
                }))
            except Exception as e:
                logger.error(f"Failed to reset all tutorials: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "update-show-tutorials":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and show value required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            show = sys.argv[3].lower() == "true"

            try:
                from config.user_preferences import UserPreferencesManager

                data_dir = get_user_data_dir(user_id)
                prefs_manager = UserPreferencesManager(data_dir)

                prefs_manager.update_show_tutorials(show)

                print(json.dumps({
                    "success": True,
                    "message": f"Show tutorials set to {show}"
                }))
            except Exception as e:
                logger.error(f"Failed to update show tutorials: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)


        elif command == "get-qube-skills":
            if len(sys.argv) < 4:
                print(json.dumps({"success": False, "error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_qube_skills(user_id, qube_id)
            print(json.dumps(result))

        elif command == "save-qube-skills":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, and skills JSON required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            skills_json = sys.argv[4]

            try:
                skills_data = json.loads(skills_json)
            except json.JSONDecodeError as e:
                print(json.dumps({"success": False, "error": f"Invalid JSON: {str(e)}"}), file=sys.stderr)
                sys.exit(1)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.save_qube_skills(user_id, qube_id, skills_data)
            print(json.dumps(result))

        elif command == "add-skill-xp":
            if len(sys.argv) < 6:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, skill ID, and XP amount required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            skill_id = sys.argv[4]

            try:
                xp_amount = float(sys.argv[5])
            except ValueError:
                print(json.dumps({"success": False, "error": "XP amount must be a number"}), file=sys.stderr)
                sys.exit(1)

            evidence_block_id = sys.argv[6] if len(sys.argv) > 6 else None

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.add_skill_xp(user_id, qube_id, skill_id, xp_amount, evidence_block_id)
            print(json.dumps(result))

        elif command == "unlock-skill":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, and skill ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            skill_id = sys.argv[4]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.unlock_skill(user_id, qube_id, skill_id)
            print(json.dumps(result))

        # =====================================================================
        # Owner Info Commands
        # =====================================================================

        elif command == "get-owner-info":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, and password required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_owner_info(qube_id, password)
            print(json.dumps(result))

        elif command == "set-owner-info-field":
            if len(sys.argv) < 8:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, password, category, key, and value required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4]
            category = sys.argv[5]
            key = sys.argv[6]
            value = sys.argv[7]

            # Optional parameters
            sensitivity = sys.argv[8] if len(sys.argv) > 8 else None
            source = sys.argv[9] if len(sys.argv) > 9 else "explicit"
            confidence = int(sys.argv[10]) if len(sys.argv) > 10 else 100
            block_id = sys.argv[11] if len(sys.argv) > 11 else None

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.set_owner_info_field(
                qube_id, password, category, key, value,
                sensitivity, source, confidence, block_id
            )
            print(json.dumps(result))

        elif command == "delete-owner-info-field":
            if len(sys.argv) < 7:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, password, category, and key required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4]
            category = sys.argv[5]
            key = sys.argv[6]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.delete_owner_info_field(qube_id, password, category, key)
            print(json.dumps(result))

        elif command == "update-owner-info-sensitivity":
            if len(sys.argv) < 8:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, password, category, key, and sensitivity required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4]
            category = sys.argv[5]
            key = sys.argv[6]
            sensitivity = sys.argv[7]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.update_owner_info_sensitivity(qube_id, password, category, key, sensitivity)
            print(json.dumps(result))

        # Clearance Request Commands
        elif command == "get-pending-clearance-requests":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, and password required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_pending_clearance_requests(qube_id, password)
            print(json.dumps(result))

        elif command == "approve-clearance-request":
            if len(sys.argv) < 6:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, request ID, and password required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            request_id = sys.argv[4]
            password = sys.argv[5]
            expires_days = int(sys.argv[6]) if len(sys.argv) > 6 else None

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.approve_clearance_request(qube_id, request_id, password, expires_days)
            print(json.dumps(result))

        elif command == "deny-clearance-request":
            if len(sys.argv) < 6:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, request ID, and password required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            request_id = sys.argv[4]
            password = sys.argv[5]
            reason = sys.argv[6] if len(sys.argv) > 6 else None

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.deny_clearance_request(qube_id, request_id, password, reason)
            print(json.dumps(result))

        elif command == "get-clearance-audit-log":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, and password required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4]
            limit = int(sys.argv[5]) if len(sys.argv) > 5 else 100
            entity_filter = sys.argv[6] if len(sys.argv) > 6 else None

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_clearance_audit_log(qube_id, password, limit, entity_filter)
            print(json.dumps(result))

        elif command == "get-visualizer-settings":
            if len(sys.argv) < 4:
                print(json.dumps({"success": False, "error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]

            try:
                # Find qube directory
                data_dir = get_user_qubes_dir(user_id)
                qube_dir = None
                for dir_path in data_dir.iterdir():
                    if dir_path.is_dir() and qube_id in dir_path.name:
                        qube_dir = dir_path
                        break

                if not qube_dir:
                    print(json.dumps({"success": False, "error": f"Qube {qube_id} not found"}), file=sys.stderr)
                    sys.exit(1)

                # Get password for chain_state access
                password = get_secret("password", argv_index=4, required=False)

                # Default settings
                default_settings = {
                    "enabled": False,
                    "waveform_style": 1,
                    "color_theme": "qube-color",
                    "gradient_style": "gradient-dark",
                    "sensitivity": 50,
                    "animation_smoothness": "medium",
                    "audio_offset_ms": 0,
                    "frequency_range": 20,
                    "output_monitor": 0
                }

                # Try to load from chain_state first
                settings = None
                if password:
                    try:
                        from core.chain_state import ChainState
                        from orchestrator.user_orchestrator import UserOrchestrator

                        # Derive encryption key from password
                        orchestrator = UserOrchestrator(user_id)
                        orchestrator.set_master_key(password)
                        qube_key = orchestrator._get_encryption_key(qube_dir)

                        if qube_key:
                            chain_dir = qube_dir / "chain"
                            cs = ChainState(chain_dir, qube_key, qube_id)
                            settings = cs.get_visualizer_settings()
                            if settings:
                                # Add enabled flag from chain_state
                                settings["enabled"] = cs.is_visualizer_enabled()
                    except Exception as e:
                        logger.debug(f"Could not load visualizer settings from chain_state: {e}")

                # Fallback to legacy file if chain_state didn't have settings
                if not settings:
                    settings_file = qube_dir / "visualizer_settings.json"
                    if settings_file.exists():
                        with open(settings_file, 'r') as f:
                            settings = json.load(f)
                    else:
                        settings = default_settings

                print(json.dumps(settings))

            except Exception as e:
                logger.error(f"Failed to get visualizer settings: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "save-visualizer-settings":
            if len(sys.argv) < 5:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, and settings JSON required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            settings_json = sys.argv[4]
            password = get_secret("password", argv_index=5, required=False)

            try:
                # Find qube directory
                data_dir = get_user_qubes_dir(user_id)
                qube_dir = None
                for dir_path in data_dir.iterdir():
                    if dir_path.is_dir() and qube_id in dir_path.name:
                        qube_dir = dir_path
                        break

                if not qube_dir:
                    print(json.dumps({"success": False, "error": f"Qube {qube_id} not found"}), file=sys.stderr)
                    sys.exit(1)

                settings_data = json.loads(settings_json)

                # Save to chain_state if password is provided
                saved_to_chain_state = False
                enabled = settings_data.pop("enabled", False)  # Extract enabled for both paths
                if password:
                    try:
                        from core.chain_state import ChainState
                        from orchestrator.user_orchestrator import UserOrchestrator

                        # Derive encryption key from password
                        orchestrator = UserOrchestrator(user_id)
                        orchestrator.set_master_key(password)
                        qube_key = orchestrator._get_encryption_key(qube_dir)

                        if qube_key:
                            chain_dir = qube_dir / "chain"
                            cs = ChainState(chain_dir, qube_key, qube_id)

                            # Save enabled flag separately
                            cs.set_visualizer_enabled(enabled)

                            # Save the rest of the settings
                            cs.set_visualizer_settings(settings_data)
                            saved_to_chain_state = True
                            logger.info("Visualizer settings saved to chain_state")
                    except Exception as e:
                        logger.warning(f"Could not save visualizer settings to chain_state: {e}")

                # Also save to legacy file as backup
                settings_file = qube_dir / "visualizer_settings.json"
                # Re-add enabled for legacy file (we popped it earlier)
                settings_data["enabled"] = enabled
                with open(settings_file, 'w') as f:
                    json.dump(settings_data, f, indent=2)

                print(json.dumps({"success": True, "message": "Visualizer settings saved", "chain_state": saved_to_chain_state}))

            except Exception as e:
                logger.error(f"Failed to save visualizer settings: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "get-trust-personality":
            if len(sys.argv) < 4:
                print(json.dumps({"success": False, "error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            # Password passed via stdin for security
            password = get_secret("password", argv_index=4, required=False)

            try:
                # Create bridge and set master key
                user_bridge = GUIBridge(user_id=user_id)
                if password:
                    user_bridge.orchestrator.set_master_key(password)

                # Find qube directory
                qube_dir = user_bridge._find_qube_dir(qube_id)

                if not qube_dir:
                    print(json.dumps({"success": False, "error": f"Qube {qube_id} not found"}), file=sys.stderr)
                    sys.exit(1)

                # Try to get trust_profile from chain_state
                trust_profile = "balanced"  # Default

                # Use ChainState with encryption if we have the key
                encryption_key = user_bridge._get_qube_encryption_key(qube_dir)
                if encryption_key:
                    from core.chain_state import ChainState
                    chain_dir = qube_dir / "chain"
                    cs = ChainState(chain_dir, encryption_key, qube_id)
                    trust_profile = cs.get_setting("trust_profile", "balanced")
                else:
                    # Try reading plain JSON for legacy qubes
                    chain_state_path = qube_dir / "chain" / "chain_state.json"
                    if chain_state_path.exists():
                        with open(chain_state_path, "r") as f:
                            chain_state = json.load(f)
                            trust_profile = chain_state.get("trust_profile", "balanced")

                print(json.dumps({"trust_profile": trust_profile}))

            except Exception as e:
                logger.error(f"Failed to get trust personality: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "update-trust-personality":
            if len(sys.argv) < 6:
                print(json.dumps({"success": False, "error": "User ID, Qube ID, trust profile, and password required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            trust_profile = sys.argv[4]
            password = get_secret("password", argv_index=5)

            try:
                # Validate trust_profile
                valid_profiles = ["cautious", "balanced", "social", "analytical"]
                if trust_profile not in valid_profiles:
                    print(json.dumps({"success": False, "error": f"Invalid trust profile. Must be one of: {', '.join(valid_profiles)}"}), file=sys.stderr)
                    sys.exit(1)

                # Create bridge and set master key
                user_bridge = GUIBridge(user_id=user_id)
                if password:
                    user_bridge.orchestrator.set_master_key(password)

                # Find qube directory
                qube_dir = user_bridge._find_qube_dir(qube_id)

                if not qube_dir:
                    print(json.dumps({"success": False, "error": f"Qube {qube_id} not found"}), file=sys.stderr)
                    sys.exit(1)

                # Use ChainState with encryption
                encryption_key = user_bridge._get_qube_encryption_key(qube_dir)
                if encryption_key:
                    from core.chain_state import ChainState
                    chain_dir = qube_dir / "chain"
                    cs = ChainState(data_dir=chain_dir, encryption_key=encryption_key)
                    cs.update_settings({"trust_profile": trust_profile})
                    print(json.dumps({"success": True, "trust_profile": trust_profile}))
                else:
                    print(json.dumps({"success": False, "error": "Cannot access chain_state - no encryption key"}))

            except Exception as e:
                logger.error(f"Failed to update trust personality: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        elif command == "authenticate-nft":
            # NFT-based authentication for Qube ownership
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.authenticate_nft(qube_id, password)
            print(json.dumps(result))

        elif command == "refresh-auth-token":
            # Refresh an existing JWT token
            if len(sys.argv) < 3:
                print(json.dumps({"error": "Token required"}), file=sys.stderr)
                sys.exit(1)

            token = sys.argv[2]

            result = await bridge.refresh_auth_token(token)
            print(json.dumps(result))

        elif command == "get-auth-status":
            # Check if Qube can authenticate
            if len(sys.argv) < 3:
                print(json.dumps({"error": "Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            qube_id = validate_qube_id(sys.argv[2])

            result = await bridge.get_auth_status(qube_id)
            print(json.dumps(result))


        # =====================================================================
        # P2P Network Commands
        # =====================================================================

        elif command == "get-online-qubes":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_online_qubes()
            print(json.dumps(result))

        elif command == "generate-introduction":
            # Generate an AI introduction message (Qube introduces itself)
            if len(sys.argv) < 6:
                print(json.dumps({"error": "User ID, Qube ID, to_commitment, and to_name required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            to_commitment = sys.argv[4]
            to_name = sys.argv[5]
            to_description = sys.argv[6] if len(sys.argv) > 6 else ""
            password = get_secret("password", argv_index=7, required=False) or get_secret("password", argv_index=6)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.generate_introduction_message(
                qube_id, to_commitment, to_name, to_description, password
            )
            print(json.dumps(result))

        elif command == "send-introduction":
            if len(sys.argv) < 6:
                print(json.dumps({"error": "User ID, Qube ID, to_commitment, and message required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            to_commitment = sys.argv[4]
            message = sys.argv[5]
            password = get_secret("password", argv_index=6)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.send_introduction(qube_id, to_commitment, message, password)
            print(json.dumps(result))

        elif command == "get-pending-introductions":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_pending_introductions(qube_id, password)
            print(json.dumps(result))

        elif command == "evaluate-introduction":
            # Have the Qube's AI evaluate an incoming introduction
            if len(sys.argv) < 6:
                print(json.dumps({"error": "User ID, Qube ID, from_name, and intro_message required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            from_name = sys.argv[4]
            intro_message = sys.argv[5]
            password = get_secret("password", argv_index=6)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.evaluate_introduction(qube_id, from_name, intro_message, password)
            print(json.dumps(result))

        elif command == "accept-introduction":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and relay_id required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            relay_id = sys.argv[4]
            password = get_secret("password", argv_index=5)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.accept_introduction(qube_id, relay_id, password)
            print(json.dumps(result))

        elif command == "reject-introduction":
            if len(sys.argv) < 5:
                print(json.dumps({"error": "User ID, Qube ID, and relay_id required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            relay_id = sys.argv[4]
            password = get_secret("password", argv_index=5)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.reject_introduction(qube_id, relay_id, password)
            print(json.dumps(result))

        elif command == "process-p2p-message":
            # Process an incoming P2P message through the local Qube's AI
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("qube_id")
            parser.add_argument("--from-name", required=True)
            parser.add_argument("--from-commitment", default="")
            parser.add_argument("--message", required=True)
            parser.add_argument("--context", default="[]")  # JSON array
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            # Parse context from JSON
            try:
                context = json.loads(args.context)
            except json.JSONDecodeError:
                context = []

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.process_p2p_message(
                qube_id,
                args.from_name,
                args.from_commitment,
                args.message,
                context,
                password
            )
            print(json.dumps(result))

        elif command == "get-connections":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_connections(qube_id)
            print(json.dumps(result))

        elif command == "create-p2p-session":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("qube_id")
            parser.add_argument("--local-qubes", default="")
            parser.add_argument("--remote-commitments", default="")
            parser.add_argument("--topic", default="")
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            local_qube_ids = [q.strip() for q in args.local_qubes.split(",") if q.strip()]
            remote_commitments = [c.strip() for c in args.remote_commitments.split(",") if c.strip()]

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.create_p2p_session(
                qube_id,
                local_qube_ids,
                remote_commitments,
                args.topic,
                password
            )
            print(json.dumps(result))

        elif command == "get-p2p-sessions":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "User ID and Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password", argv_index=4)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_p2p_sessions(qube_id, password)
            print(json.dumps(result))

        elif command == "start-p2p-conversation":
            # Start a P2P conversation using the same logic as local multi-qube
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--local-qubes", required=True)  # Comma-separated qube IDs
            parser.add_argument("--remote-connections", default="[]")  # JSON array
            parser.add_argument("--session-id", required=True)
            parser.add_argument("--initial-prompt", required=True)
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            local_qube_ids = [q.strip() for q in args.local_qubes.split(",") if q.strip()]
            remote_connections = json.loads(args.remote_connections)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.start_p2p_conversation(
                local_qube_ids,
                remote_connections,
                args.session_id,
                args.initial_prompt,
                password
            )
            print(json.dumps(result))

        elif command == "continue-p2p-conversation":
            # Continue P2P conversation - get next local Qube response
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--conversation-id", required=True)
            parser.add_argument("--session-id", required=True)
            parser.add_argument("--local-qubes", required=True)
            parser.add_argument("--remote-connections", default="[]")
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            local_qube_ids = [q.strip() for q in args.local_qubes.split(",") if q.strip()]
            remote_connections = json.loads(args.remote_connections)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.continue_p2p_conversation(
                args.conversation_id,
                args.session_id,
                local_qube_ids,
                remote_connections,
                password
            )
            print(json.dumps(result))

        elif command == "inject-p2p-block":
            # Inject a block received from hub into local conversation
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--conversation-id", required=True)
            parser.add_argument("--session-id", required=True)
            parser.add_argument("--block-data", required=True)  # JSON string
            parser.add_argument("--from-commitment", required=True)
            parser.add_argument("--local-qubes", required=True)
            parser.add_argument("--remote-connections", default="[]")
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            local_qube_ids = [q.strip() for q in args.local_qubes.split(",") if q.strip()]
            remote_connections = json.loads(args.remote_connections)
            block_data = json.loads(args.block_data)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.inject_p2p_block(
                args.conversation_id,
                args.session_id,
                block_data,
                args.from_commitment,
                local_qube_ids,
                remote_connections,
                password
            )
            print(json.dumps(result))

        elif command == "send-p2p-user-message":
            # Send user message in P2P conversation
            if len(sys.argv) < 3:
                print(json.dumps({"error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--conversation-id", required=True)
            parser.add_argument("--session-id", required=True)
            parser.add_argument("--message", required=True)
            parser.add_argument("--local-qubes", required=True)
            parser.add_argument("--remote-connections", default="[]")
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            local_qube_ids = [q.strip() for q in args.local_qubes.split(",") if q.strip()]
            remote_connections = json.loads(args.remote_connections)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.send_p2p_user_message(
                args.conversation_id,
                args.session_id,
                args.message,
                local_qube_ids,
                remote_connections,
                password
            )
            print(json.dumps(result))

        # =====================================================================
        # SETUP WIZARD COMMANDS
        # (check-first-run, create-user-account, check-ollama-status are handled
        #  early in main() before GUIBridge initialization)
        # =====================================================================

        elif command == "create-qube-for-minting":
            # Create a qube and return info needed for NFT minting
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--name", required=True)
            parser.add_argument("--genesis-prompt", required=True)
            parser.add_argument("--ai-provider", required=True)
            parser.add_argument("--ai-model", required=True)
            parser.add_argument("--evaluation-model", default="mistral:7b")
            parser.add_argument("--favorite-color", default="#6366f1")
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            # Create bridge with correct user
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)

            try:
                # Create the qube (without minting - we'll do that through server)
                qube = await user_bridge.create_qube(
                    name=args.name,
                    genesis_prompt=args.genesis_prompt,
                    ai_provider=args.ai_provider,
                    ai_model=args.ai_model,
                    voice_model="",  # No voice by default
                    wallet_address="",  # Will be set during minting
                    password=password,
                    favorite_color=args.favorite_color,
                    generate_avatar=False,  # Use placeholder
                )

                # Get qube data for minting
                qube_obj = user_bridge.orchestrator.qubes.get(qube["qube_id"])
                if not qube_obj:
                    await user_bridge.orchestrator.load_qube(qube["qube_id"])
                    qube_obj = user_bridge.orchestrator.qubes[qube["qube_id"]]

                print(json.dumps({
                    "success": True,
                    "qube_id": qube["qube_id"],
                    "public_key": qube.get("public_key", ""),
                    "genesis_block_hash": qube.get("genesis_block_hash", ""),
                    "recipient_address": qube.get("recipient_address", ""),
                }))

            except Exception as e:
                logger.error(f"Failed to create qube for minting: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "save-api-keys":
            # Save API keys to secure settings
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]
            password = get_secret("password", argv_index=3)

            # Parse API keys from remaining args
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--openai", default="")
            parser.add_argument("--anthropic", default="")
            parser.add_argument("--google", default="")
            parser.add_argument("--deepseek", default="")
            parser.add_argument("--perplexity", default="")
            parser.add_argument("--venice", default="")

            args = parser.parse_args()

            # Create orchestrator and set master key
            orchestrator = UserOrchestrator(user_id=user_id)
            orchestrator.set_master_key(password)

            # Save API keys using secure settings
            from utils.secure_settings import SecureSettingsManager
            settings = SecureSettingsManager(orchestrator.data_dir / "settings.enc")
            settings.set_encryption_key(orchestrator.master_key)

            if args.openai:
                settings.set("api_keys.openai", args.openai)
            if args.anthropic:
                settings.set("api_keys.anthropic", args.anthropic)
            if args.google:
                settings.set("api_keys.google", args.google)
            if args.deepseek:
                settings.set("api_keys.deepseek", args.deepseek)
            if args.perplexity:
                settings.set("api_keys.perplexity", args.perplexity)
            if args.venice:
                settings.set("api_keys.venice", args.venice)

            settings.save()

            print(json.dumps({"success": True}))

        elif command == "update-qube-nft":
            # Update qube with NFT minting info after server mints
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--category-id", required=True)
            parser.add_argument("--mint-txid", required=True)
            parser.add_argument("--password", required=True)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            # Create bridge and update qube
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)

            # Load qube
            if args.qube_id not in user_bridge.orchestrator.qubes:
                await user_bridge.orchestrator.load_qube(args.qube_id)

            qube = user_bridge.orchestrator.qubes[args.qube_id]

            # Update NFT info
            qube.nft_category_id = args.category_id
            qube.mint_txid = args.mint_txid

            # Save qube state
            await user_bridge.orchestrator._save_qube_data(qube)

            print(json.dumps({
                "success": True,
                "qube_id": args.qube_id,
                "category_id": args.category_id,
                "mint_txid": args.mint_txid
            }))

        elif command == "sync-to-chain":
            # Sync Qube to chain (backup to IPFS)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", required=False)  # Master password (via stdin or arg)

            args = parser.parse_args()

            # Use stdin secret if available, fall back to argparse
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.sync_to_chain(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "transfer-qube":
            # Transfer Qube to new owner (DESTRUCTIVE - deletes local copy)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--recipient-address", required=True)
            parser.add_argument("--recipient-public-key", required=True)
            parser.add_argument("--wallet-wif", required=True)
            parser.add_argument("--password", required=True)  # Master password

            args = parser.parse_args()

            # Use stdin secrets if available, fall back to argparse
            password = get_secret("password", required=False) or args.password
            wallet_wif = get_secret("wallet_wif", required=False) or args.wallet_wif

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.transfer_qube(
                qube_id=args.qube_id,
                recipient_address=args.recipient_address,
                recipient_public_key=args.recipient_public_key,
                wallet_wif=wallet_wif,
                password=password
            )
            print(json.dumps(result))

        elif command == "import-from-wallet":
            # Import Qube from wallet
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--wallet-wif", required=True)
            parser.add_argument("--category-id", required=True)
            parser.add_argument("--password", required=True)  # Master password

            args = parser.parse_args()

            # Use stdin secrets if available, fall back to argparse
            password = get_secret("password", required=False) or args.password
            wallet_wif = get_secret("wallet_wif", required=False) or args.wallet_wif

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.import_from_wallet(
                wallet_wif=wallet_wif,
                category_id=args.category_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "scan-wallet":
            # Scan wallet for Qube NFTs
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--wallet-address", required=True)

            args = parser.parse_args()

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.scan_wallet_for_qubes(
                wallet_address=args.wallet_address
            )
            print(json.dumps(result))

        elif command == "resolve-public-key":
            # Resolve public key from BCH address
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--address", required=True)

            args = parser.parse_args()

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.resolve_recipient_public_key(
                recipient_address=args.address
            )
            print(json.dumps(result))

        elif command == "get-debug-prompt":
            # Get the last AI prompt sent for a qube (dev debugging)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "Qube ID required"}), file=sys.stderr)
                sys.exit(1)

            qube_id = sys.argv[2]

            try:
                from ai.reasoner import get_debug_prompt, get_all_debug_prompts

                if qube_id == "all":
                    # Return all cached prompts
                    all_prompts = get_all_debug_prompts()
                    print(json.dumps({
                        "success": True,
                        "prompts": all_prompts
                    }))
                else:
                    # Return prompt for specific qube
                    prompt_info = get_debug_prompt(qube_id)
                    if prompt_info:
                        print(json.dumps({
                            "success": True,
                            "prompt": prompt_info
                        }))
                    else:
                        print(json.dumps({
                            "success": False,
                            "error": f"No cached prompt for qube {qube_id}"
                        }))
            except Exception as e:
                logger.error(f"Failed to get debug prompt: {e}", exc_info=True)
                print(json.dumps({"success": False, "error": str(e)}), file=sys.stderr)
                sys.exit(1)

        # =====================================================================
        # GAMES Commands
        # =====================================================================
        elif command == "start-game":
            # Start a new game
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--game-type", default="chess")
            parser.add_argument("--opponent-type", required=True, choices=["human", "qube"])
            parser.add_argument("--opponent-id", default=None)
            parser.add_argument("--qube-color", default="random", choices=["white", "black", "random"])
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.start_game(
                qube_id=args.qube_id,
                game_type=args.game_type,
                opponent_type=args.opponent_type,
                opponent_id=args.opponent_id,
                qube_color=args.qube_color,
                password=password
            )
            print(json.dumps(result))

        elif command == "get-game-state":
            # Get current game state
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_game_state(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "get-game-stats":
            # Get permanent game statistics
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--game-type", default=None)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_game_stats(
                qube_id=args.qube_id,
                password=password,
                game_type=args.game_type
            )
            print(json.dumps(result))

        elif command == "make-move":
            # Make a move in active game
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--move", required=True)
            parser.add_argument("--player-type", required=True, choices=["human", "qube"])
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.make_move(
                qube_id=args.qube_id,
                move=args.move,
                player_type=args.player_type,
                password=password
            )
            print(json.dumps(result))

        elif command == "add-game-chat":
            # Add chat message to game
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--message", required=True)
            parser.add_argument("--sender-type", required=True, choices=["human", "qube"])
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.add_game_chat(
                qube_id=args.qube_id,
                message=args.message,
                sender_type=args.sender_type,
                password=password
            )
            print(json.dumps(result))

        elif command == "end-game":
            # End game and create GAME block
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--result", required=True, choices=["1-0", "0-1", "1/2-1/2", "*"])
            parser.add_argument("--termination", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.end_game(
                qube_id=args.qube_id,
                result=args.result,
                termination=args.termination,
                password=password
            )
            print(json.dumps(result))

        elif command == "abandon-game":
            # Abandon game without creating block
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.abandon_game(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "request-qube-move":
            # Request Qube to make a move (AI)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.request_qube_move(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "resign-game":
            # Resign the current game
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--resigning-player", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.resign_game(
                qube_id=args.qube_id,
                password=password,
                resigning_player=args.resigning_player
            )
            print(json.dumps(result))

        elif command == "offer-draw":
            # Offer a draw in the current game
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--offering-player", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.offer_draw(
                qube_id=args.qube_id,
                password=password,
                offering_player=args.offering_player
            )
            print(json.dumps(result))

        elif command == "respond-to-draw":
            # Respond to a draw offer
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--accepting", required=True, type=lambda x: x.lower() == 'true')
            parser.add_argument("--responding-player", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.respond_to_draw(
                qube_id=args.qube_id,
                password=password,
                accepting=args.accepting,
                responding_player=args.responding_player
            )
            print(json.dumps(result))

        # =====================================================================
        # Wallet Commands
        # =====================================================================

        elif command == "get-wallet-info":
            # Get wallet info for a Qube
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_qube_wallet_info(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "get-context-preview":
            # Get context preview (active context + short-term memory)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_context_preview(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "get-system-prompt-preview":
            # Get real-time system prompt preview (what WOULD be sent to AI now)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_system_prompt_preview(
                qube_id=args.qube_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "propose-wallet-tx":
            # Qube proposes a wallet transaction
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--to-address", required=True)
            parser.add_argument("--amount", required=True, type=int, help="Amount in satoshis")
            parser.add_argument("--memo", default="")
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.propose_wallet_transaction(
                qube_id=args.qube_id,
                to_address=args.to_address,
                amount_satoshis=args.amount,
                memo=args.memo,
                password=password
            )
            print(json.dumps(result))

        elif command == "approve-wallet-tx":
            # Owner approves a pending wallet transaction
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--tx-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password
            owner_wif = get_secret("owner_wif", required=True)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.approve_wallet_transaction(
                qube_id=args.qube_id,
                pending_tx_id=args.tx_id,
                owner_wif=owner_wif,
                password=password
            )
            print(json.dumps(result))

        elif command == "reject-wallet-tx":
            # Owner rejects a pending wallet transaction
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--tx-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.reject_wallet_transaction(
                qube_id=args.qube_id,
                pending_tx_id=args.tx_id,
                password=password
            )
            print(json.dumps(result))

        elif command == "owner-withdraw":
            # Owner withdraws directly from Qube wallet (IF branch)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--to-address", required=True)
            parser.add_argument("--amount", required=True, type=int, help="Amount in satoshis (0 for all)")
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password
            owner_wif = get_secret("owner_wif", required=False) or ""

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.owner_withdraw_from_wallet(
                qube_id=args.qube_id,
                to_address=args.to_address,
                amount_satoshis=args.amount,
                owner_wif=owner_wif,
                password=password
            )
            print(json.dumps(result))

        elif command == "get-wallet-transactions":
            # Get wallet transaction history
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--password", default=None)
            parser.add_argument("--limit", type=int, default=50)
            parser.add_argument("--offset", type=int, default=0)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_wallet_transactions(
                qube_id=args.qube_id,
                password=password,
                limit=args.limit,
                offset=args.offset
            )
            print(json.dumps(result))

        # ==================== Wallet Security CLI Commands ====================

        elif command == "import-seed-phrase":
            # Derive WIF from BIP39 seed phrase and save for NFT address
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--nft-address", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password
            seed_phrase = get_secret("seed_phrase", required=True)

            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.import_seed_phrase(
                nft_address=args.nft_address,
                seed_phrase=seed_phrase,
                password=password
            )
            print(json.dumps(result))

        elif command == "save-owner-key":
            # Save encrypted owner WIF for an NFT address
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--nft-address", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password
            owner_wif = get_secret("owner_wif", required=True)

            try:
                user_bridge = GUIBridge(user_id=user_id)
                user_bridge.orchestrator.set_master_key(password)
                result = user_bridge.orchestrator.save_owner_key(args.nft_address, owner_wif)
                print(json.dumps({"success": result}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "delete-owner-key":
            # Delete stored owner WIF for an NFT address
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--nft-address", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            try:
                user_bridge = GUIBridge(user_id=user_id)
                user_bridge.orchestrator.set_master_key(password)
                result = user_bridge.orchestrator.delete_owner_key(args.nft_address)
                print(json.dumps({"success": result}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "get-wallet-security":
            # Get wallet security config (WIFs redacted)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            try:
                user_bridge = GUIBridge(user_id=user_id)
                user_bridge.orchestrator.set_master_key(password)
                result = user_bridge.orchestrator.get_wallet_security()
                print(json.dumps({"success": True, **result}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "update-whitelist":
            # Update whitelist for a Qube
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--whitelist", required=True)  # JSON array
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password
            whitelist = json.loads(args.whitelist)

            try:
                user_bridge = GUIBridge(user_id=user_id)
                user_bridge.orchestrator.set_master_key(password)
                result = user_bridge.orchestrator.update_whitelist(args.qube_id, whitelist)
                print(json.dumps({"success": result}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))

        elif command == "approve-wallet-tx-stored-key":
            # Approve using stored WIF (one-click approval)
            if len(sys.argv) < 3:
                print(json.dumps({"success": False, "error": "User ID required"}), file=sys.stderr)
                sys.exit(1)

            user_id = sys.argv[2]

            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("command")
            parser.add_argument("user_id")
            parser.add_argument("--qube-id", required=True)
            parser.add_argument("--tx-id", required=True)
            parser.add_argument("--password", default=None)

            args = parser.parse_args()
            password = get_secret("password", required=False) or args.password

            try:
                user_bridge = GUIBridge(user_id=user_id)
                user_bridge.orchestrator.set_master_key(password)

                # Load qube first to get NFT address
                await user_bridge.orchestrator.load_qube(args.qube_id)

                # Get stored WIF via qube's NFT address
                owner_wif = user_bridge.orchestrator.get_owner_wif_for_qube(args.qube_id)
                if not owner_wif:
                    print(json.dumps({"success": False, "error": "No stored key for this Qube's NFT address"}))
                    sys.exit(1)

                # Use existing approve method
                result = await user_bridge.approve_wallet_transaction(
                    qube_id=args.qube_id,
                    pending_tx_id=args.tx_id,
                    owner_wif=owner_wif,
                    password=password
                )
                print(json.dumps(result))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))

        # ==================== Clearance Profile CLI Commands ====================

        elif command == "get-clearance-profiles":
            # Args: user_id, qube_id, password
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = sys.argv[4] if len(sys.argv) > 4 else ""
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_clearance_profiles(qube_id, password)
            print(json.dumps(result))

        elif command == "get-available-tags":
            # Args: user_id, qube_id
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_available_tags(qube_id)
            print(json.dumps(result))

        elif command == "get-trait-definitions":
            # Args: user_id, qube_id
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_trait_definitions(qube_id)
            print(json.dumps(result))

        elif command == "add-relationship-tag":
            # Args: user_id, qube_id, entity_id, tag, password
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            entity_id = sys.argv[4]
            tag = sys.argv[5]
            password = sys.argv[6]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.add_relationship_tag(qube_id, entity_id, tag, password)
            print(json.dumps(result))

        elif command == "remove-relationship-tag":
            # Args: user_id, qube_id, entity_id, tag, password
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            entity_id = sys.argv[4]
            tag = sys.argv[5]
            password = sys.argv[6]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.remove_relationship_tag(qube_id, entity_id, tag, password)
            print(json.dumps(result))

        elif command == "set-relationship-clearance":
            # Args: user_id, qube_id, entity_id, profile, password, [field_grants_json], [field_denials_json], [expires_days]
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            entity_id = sys.argv[4]
            profile = sys.argv[5]
            password = sys.argv[6]
            field_grants = json.loads(sys.argv[7]) if len(sys.argv) > 7 and sys.argv[7] else None
            field_denials = json.loads(sys.argv[8]) if len(sys.argv) > 8 and sys.argv[8] else None
            expires_days = int(sys.argv[9]) if len(sys.argv) > 9 and sys.argv[9] else None
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.set_relationship_clearance(
                qube_id, entity_id, profile, password, field_grants, field_denials, expires_days
            )
            print(json.dumps(result))

        elif command == "suggest-clearance":
            # Args: user_id, qube_id, entity_id
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            entity_id = sys.argv[4]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.suggest_clearance(qube_id, entity_id)
            print(json.dumps(result))

        # ==================== End Clearance Profile CLI Commands ====================

        # ==================== Model Control CLI Commands ====================
        elif command == "set-model-lock":
            # Args: user_id, qube_id, locked (true/false), [model_name], password (via stdin)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            locked = sys.argv[4].lower() == "true"
            model_name = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
            password = get_secret("password", argv_index=6)
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)
            result = await user_bridge.set_model_lock(qube_id, locked, model_name)
            print(json.dumps(result))

        elif command == "set-revolver-mode":
            # Args: user_id, qube_id, enabled (true/false), password (via stdin)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            enabled = sys.argv[4].lower() == "true"
            password = get_secret("password", argv_index=5)
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)
            result = await user_bridge.set_revolver_mode(qube_id, enabled)
            print(json.dumps(result))

        elif command == "set-revolver-mode-pool":
            # Args: user_id, qube_id, pool (JSON array), password (via stdin)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            pool_json = sys.argv[4] if len(sys.argv) > 4 else "[]"
            pool = json.loads(pool_json)
            password = get_secret("password", argv_index=5)
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)
            result = await user_bridge.set_revolver_mode_pool(qube_id, pool)
            print(json.dumps(result))

        elif command == "get-revolver-mode-pool":
            # Args: user_id, qube_id
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_revolver_mode_pool(qube_id)
            print(json.dumps(result))

        elif command == "set-autonomous-mode-pool":
            # Args: user_id, qube_id, pool (JSON array), password (via stdin)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            pool_json = sys.argv[4] if len(sys.argv) > 4 else "[]"
            pool = json.loads(pool_json)
            password = get_secret("password", argv_index=5)
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)
            result = await user_bridge.set_autonomous_mode_pool(qube_id, pool)
            print(json.dumps(result))

        elif command == "get-autonomous-mode-pool":
            # Args: user_id, qube_id
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.get_autonomous_mode_pool(qube_id)
            print(json.dumps(result))

        elif command == "set-autonomous-mode":
            # Args: user_id, qube_id, enabled (true/false), password (via stdin)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            enabled = sys.argv[4].lower() == "true" if len(sys.argv) > 4 else True
            password = get_secret("password", argv_index=5)
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)
            result = await user_bridge.set_autonomous_mode(qube_id, enabled)
            print(json.dumps(result))

        elif command == "get-model-preferences":
            # Args: user_id, qube_id, password (via stdin)
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            password = get_secret("password", argv_index=4)
            user_bridge = GUIBridge(user_id=user_id)
            user_bridge.orchestrator.set_master_key(password)
            result = await user_bridge.get_model_preferences(qube_id)
            print(json.dumps(result))

        elif command == "clear-model-preferences":
            # Args: user_id, qube_id, [task_type]
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            task_type = sys.argv[4] if len(sys.argv) > 4 else None
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.clear_model_preferences(qube_id, task_type)
            print(json.dumps(result))

        elif command == "reset-model-to-genesis":
            # Args: user_id, qube_id
            user_id = sys.argv[2]
            qube_id = sys.argv[3]
            user_bridge = GUIBridge(user_id=user_id)
            result = await user_bridge.reset_model_to_genesis(qube_id)
            print(json.dumps(result))

        # ==================== End Model Control CLI Commands ====================

        # ==================== Event Streaming Commands ====================

        elif command == "watch-events":
            # Stream chain_state events for a qube in real-time
            # This command runs indefinitely and outputs JSON events to stdout
            # Usage: python gui_bridge.py watch-events <user_id> <qube_id>
            # Password is read from stdin
            if len(sys.argv) < 4:
                print(json.dumps({"error": "Usage: watch-events <user_id> <qube_id>"}), file=sys.stderr)
                sys.exit(1)

            user_id = validate_user_id(sys.argv[2])
            qube_id = validate_qube_id(sys.argv[3])
            password = get_secret("password")

            if not password:
                print(json.dumps({"error": "Password required"}), file=sys.stderr)
                sys.exit(1)

            # Import events
            from core.events import Events, ChainStateEvent

            # Create bridge and load qube
            bridge = GUIBridge(user_id)
            bridge.orchestrator.set_master_key(password)

            qube = await bridge.orchestrator.load_qube(qube_id)
            if not qube:
                print(json.dumps({"error": f"Qube {qube_id} not found"}), file=sys.stderr)
                sys.exit(1)

            # Event callback that prints JSON to stdout
            def on_event(event: ChainStateEvent):
                event_data = {
                    "type": "chain_state_event",
                    "qube_id": qube_id,
                    "event_type": event.event_type.value,
                    "payload": event.payload,
                    "timestamp": event.timestamp,
                    "source": event.source
                }
                # Print JSON and flush immediately for real-time streaming
                print(json.dumps(event_data), flush=True)

            # Subscribe to events
            qube.events.subscribe(on_event)

            # Signal that we're ready
            print(json.dumps({
                "type": "ready",
                "qube_id": qube_id,
                "message": "Event watcher started"
            }), flush=True)

            # Keep running until process is terminated
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                qube.events.unsubscribe(on_event)

        # ==================== End Event Streaming Commands ====================

        else:
            print(json.dumps({"error": f"Unknown command: {command}"}), file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        logger.error(f"Command failed: {e}", exc_info=True)
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (OSError, IOError, BrokenPipeError):
        # Suppress I/O errors at exit (colorama, broken pipes, etc.)
        pass
    except SystemExit:
        pass
    except Exception:
        # Log other errors to file, don't show error dialog
        try:
            import logging
            logging.exception("Unhandled exception in main")
        except Exception:
            pass
