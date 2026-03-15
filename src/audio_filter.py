"""
Audio preprocessing module for noise reduction using RNNoise with audio gating.
Lightweight filter optimized for real-time voice communication.
"""

import numpy as np
import logging
import time
from typing import Optional
from livekit import rtc
from livekit.agents.voice import io

try:
    from . import audio_config as config
except ImportError:
    import audio_config as config

logger = logging.getLogger(__name__)


class RNNoiseFilter:
    """
    RNNoise deep learning-based noise suppression filter.
    Lightweight and optimized for real-time voice communication.
    """
    
    def __init__(self, sample_rate: int = 24000):
        """
        Initialize RNNoise filter.
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 24000)
        """
        self.sample_rate = sample_rate
        self.rnnoise_processor = None
        self.initialized = False
        
        # RNNoise requires 48kHz, so we'll need to resample
        self.rnnoise_sample_rate = 48000
        self.needs_resampling = (sample_rate != self.rnnoise_sample_rate)
        
        try:
            from pyrnnoise import RNNoise
            self.rnnoise_processor = RNNoise(sample_rate=self.rnnoise_sample_rate)
            self.initialized = True
            logger.info(
                f"RNNoise initialized: sample_rate={sample_rate}Hz, "
                f"rnnoise_internal={self.rnnoise_sample_rate}Hz"
            )
        except ImportError:
            logger.warning(
                "pyrnnoise not available. Install with: pip install pyrnnoise"
            )
            self.initialized = False
        except Exception as e:
            logger.error(f"Failed to initialize RNNoise: {e}", exc_info=True)
            self.initialized = False
    
    def process(self, audio_data: np.ndarray) -> np.ndarray:
        """
        Process audio data through RNNoise.
        
        Args:
            audio_data: Input audio as int16 numpy array
            
        Returns:
            Processed audio as int16 numpy array
        """
        if not self.initialized:
            return audio_data
        
        try:
            # Resample to 48kHz if needed (RNNoise works at 48kHz)
            if self.needs_resampling:
                from scipy import signal
                audio_float = audio_data.astype(np.float32) / 32768.0
                num_samples = int(len(audio_float) * self.rnnoise_sample_rate / self.sample_rate)
                audio_resampled_float = signal.resample(audio_float, num_samples)
                audio_resampled_int16 = (audio_resampled_float * 32768.0).astype(np.int16)
            else:
                audio_resampled_int16 = audio_data
            
            # Process through RNNoise
            denoised_chunks = []
            for vad_prob, denoised_chunk in self.rnnoise_processor.denoise_chunk(audio_resampled_int16):
                denoised_chunks.append(denoised_chunk.squeeze())
            
            if denoised_chunks:
                processed = np.concatenate(denoised_chunks)
            else:
                return audio_data
            
            # Resample back to original sample rate if needed
            if self.needs_resampling:
                from scipy import signal
                processed_float = processed.astype(np.float32) / 32768.0
                num_samples = len(audio_data)
                processed_float = signal.resample(processed_float, num_samples)
                processed = (processed_float * 32768.0).astype(np.int16)
            
            # Ensure we have the same length as input
            if len(processed) > len(audio_data):
                processed = processed[:len(audio_data)]
            elif len(processed) < len(audio_data):
                processed = np.pad(processed, (0, len(audio_data) - len(processed)))
            
            return processed
            
        except Exception as e:
            logger.error(f"RNNoise processing error: {e}", exc_info=True)
            return audio_data


class RNNoiseWithGateFilter:
    """
    Combined RNNoise + Audio Gate filtering pipeline.
    Applies noise reduction followed by audio gating with EMA smoothing.
    All parameters are loaded from audio_config.
    """
    
    def __init__(self, sample_rate: int = 24000):
        """
        Initialize RNNoise + Gate filter using config parameters.
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 24000)
        """
        self.sample_rate = sample_rate
        
        # Load configuration
        self.gate_threshold_db = config.AUDIO_GATE_THRESHOLD_DB
        self.vad_aggressiveness = config.VAD_AGGRESSIVENESS
        self.ema_alpha = config.EMA_ALPHA
        
        # Convert dB threshold to linear amplitude (for int16 audio)
        # Reference: 0 dBFS = 32768 (max int16)
        self.gate_threshold_linear = 32768 * (10 ** (self.gate_threshold_db / 20.0))
        
        # Initialize RNNoise if enabled
        self.rnnoise_filter = None
        if config.RNNOISE_ENABLED:
            self.rnnoise_filter = RNNoiseFilter(sample_rate=sample_rate)
            self.initialized = self.rnnoise_filter.initialized
        else:
            self.initialized = True
            logger.info("RNNoise disabled in config, using gate-only mode")
        
        # Exponential moving average for RMS level smoothing
        self.rms_ema = 0.0
        
        # Statistics
        self.gate_rejections = 0
        self.total_frames = 0
        
        logger.info(
            f"RNNoiseWithGateFilter initialized: "
            f"RNNoise={'enabled' if config.RNNOISE_ENABLED and self.rnnoise_filter and self.rnnoise_filter.initialized else 'disabled'}, "
            f"Gate_threshold={self.gate_threshold_db:.1f}dB, "
            f"VAD_aggressiveness={self.vad_aggressiveness:.2f}, "
            f"EMA_alpha={self.ema_alpha:.2f}"
        )
    
    def process(self, audio_data: np.ndarray) -> tuple[np.ndarray, dict]:
        """
        Process audio through RNNoise and gating with EMA smoothing.
        
        Args:
            audio_data: Input audio as int16 numpy array
            
        Returns:
            Tuple of (processed_audio, metrics_dict)
        """
        metrics = {
            "rnnoise_ms": 0.0,
            "gate_ms": 0.0,
            "total_ms": 0.0,
            "rms_db": 0.0,
            "rms_ema_db": 0.0,
            "passed_gate": True,
        }
        
        start_total = time.perf_counter()
        
        # Stage 1: RNNoise (if enabled)
        processed = audio_data
        if config.RNNOISE_ENABLED and self.rnnoise_filter and self.rnnoise_filter.initialized:
            start_rnnoise = time.perf_counter()
            processed = self.rnnoise_filter.process(audio_data)
            metrics["rnnoise_ms"] = (time.perf_counter() - start_rnnoise) * 1000
        
        # Stage 2: Audio Gating with EMA
        start_gate = time.perf_counter()
        
        # Calculate RMS level
        rms = np.sqrt(np.mean(processed.astype(np.float32) ** 2))
        
        # Update exponential moving average for smoothing
        self.rms_ema = self.ema_alpha * rms + (1 - self.ema_alpha) * self.rms_ema
        
        # Convert to dB (avoid log(0))
        rms_db = 20 * np.log10(max(rms, 1e-10) / 32768.0)
        rms_ema_db = 20 * np.log10(max(self.rms_ema, 1e-10) / 32768.0)
        metrics["rms_db"] = round(rms_db, 1)
        metrics["rms_ema_db"] = round(rms_ema_db, 1)
        
        # Apply audio gate with VAD aggressiveness multiplier
        effective_threshold = self.gate_threshold_linear * self.vad_aggressiveness
        
        if rms < effective_threshold:
            # Audio is too quiet (likely distant voice or background noise)
            # Attenuate by 90% instead of complete silence to maintain flow
            processed = (processed * 0.1).astype(np.int16)
            metrics["passed_gate"] = False
            self.gate_rejections += 1
        else:
            metrics["passed_gate"] = True
        
        metrics["gate_ms"] = (time.perf_counter() - start_gate) * 1000
        metrics["total_ms"] = (time.perf_counter() - start_total) * 1000
        
        self.total_frames += 1
        
        return processed, metrics


class FilteredAudioInput(io.AudioInput):
    """
    Custom AudioInput that applies RNNoise + audio gating to incoming audio frames.
    All parameters are loaded from audio_config.
    """
    
    def __init__(self, source: io.AudioInput):
        """
        Initialize filtered audio input using config parameters.
        
        Args:
            source: Original audio input from RoomIO
        """
        super().__init__(label="RNNoiseFiltered", source=source)
        self.filter: Optional[RNNoiseWithGateFilter] = None
        self.frame_count = 0
        
        # Load config
        self.log_every_n_frames = config.AUDIO_LOG_EVERY_N_FRAMES
        
        logger.info(
            f"FilteredAudioInput created: "
            f"gate_threshold={config.AUDIO_GATE_THRESHOLD_DB}dB, "
            f"vad_aggressiveness={config.VAD_AGGRESSIVENESS:.2f}, "
            f"ema_alpha={config.EMA_ALPHA:.2f}, "
            f"log_every_n={self.log_every_n_frames}"
        )
    
    async def __anext__(self) -> rtc.AudioFrame:
        """
        Get next audio frame and apply filtering.
        """
        # Get frame from source
        frame = await self.source.__anext__()
        
        # Initialize filter on first frame
        if self.filter is None:
            self.filter = RNNoiseWithGateFilter(sample_rate=frame.sample_rate)
            
            if not self.filter.initialized:
                logger.warning(
                    "Audio filter failed to initialize, passing through unfiltered audio"
                )
                return frame
        
        # Skip filtering if not initialized
        if not self.filter.initialized:
            return frame
        
        try:
            # Convert frame data to numpy array
            audio_data = np.frombuffer(frame.data, dtype=np.int16)
            
            # Apply filtering
            filtered_data, metrics = self.filter.process(audio_data)
            
            # Update frame count
            self.frame_count += 1
            
            # Log performance metrics periodically
            if self.frame_count % self.log_every_n_frames == 0:
                gate_rejection_rate = (self.filter.gate_rejections / self.filter.total_frames) * 100
                logger.debug(
                    f"Audio filter stats [frame {self.frame_count}]: "
                    f"rnnoise={metrics['rnnoise_ms']:.2f}ms, "
                    f"gate={metrics['gate_ms']:.2f}ms, "
                    f"total={metrics['total_ms']:.2f}ms, "
                    f"rms={metrics['rms_db']:.1f}dB, "
                    f"rms_ema={metrics['rms_ema_db']:.1f}dB, "
                    f"passed_gate={metrics['passed_gate']}, "
                    f"rejection_rate={gate_rejection_rate:.1f}%"
                )
            
            # Create filtered frame
            filtered_frame = rtc.AudioFrame(
                data=filtered_data.tobytes(),
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
                samples_per_channel=len(filtered_data) // frame.num_channels,
            )
            
            return filtered_frame
            
        except Exception as e:
            logger.error(f"Error in audio filtering, returning original frame: {e}", exc_info=True)
            return frame
