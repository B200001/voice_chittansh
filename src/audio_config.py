"""
Audio processing configuration.
All audio filter parameters are defined here for easy tuning.
"""

# ============================================================
# RNNoise Configuration
# ============================================================

# Enable/disable RNNoise filter
RNNOISE_ENABLED = True

# ============================================================
# Audio Gate Configuration
# ============================================================

# Audio gate threshold in dB
# - Lower values (e.g., -30) are more permissive (allows quieter audio)
# - Higher values (e.g., -20) are stricter (rejects more quiet audio)
# - Default: -25.0 (balanced for telephony)
AUDIO_GATE_THRESHOLD_DB = -25.0

# VAD aggressiveness multiplier (0.0 - 1.0)
# - Lower values (e.g., 0.5) make gate more permissive
# - Higher values (e.g., 0.9) make gate stricter
# - Default: 0.7 (moderate)
# - This multiplies with AUDIO_GATE_THRESHOLD_DB for effective threshold
VAD_AGGRESSIVENESS = 0.7

# ============================================================
# EMA (Exponential Moving Average) Configuration
# ============================================================

# EMA smoothing factor for RMS level (0.0 - 1.0)
# - Lower values (e.g., 0.05) = more smoothing, slower response
# - Higher values (e.g., 0.3) = less smoothing, faster response
# - Default: 0.1 (balanced)
# - Prevents gate "fluttering" at threshold boundary
EMA_ALPHA = 0.1

# ============================================================
# Performance & Logging Configuration
# ============================================================

# Log audio filter metrics every N frames
# - Set to 100 for normal operation
# - Set to 10 for debugging
# - Set to 1000 for production (less verbose)
AUDIO_LOG_EVERY_N_FRAMES = 1000

# ============================================================
# Debug Configuration
# ============================================================

# Enable debug audio recording (saves audio at each processing stage)
AUDIO_DEBUG_SAVE_ENABLED = False

# Directory to save debug audio files
AUDIO_DEBUG_SAVE_DIR = "audio_debug"

# Maximum files per stage before rotation
AUDIO_DEBUG_MAX_FILES_PER_STAGE = 100

# Save audio at specific stages (only if AUDIO_DEBUG_SAVE_ENABLED = True)
AUDIO_DEBUG_SAVE_RAW = False
AUDIO_DEBUG_SAVE_AFTER_RNNOISE = False
AUDIO_DEBUG_SAVE_AFTER_GATE = False
