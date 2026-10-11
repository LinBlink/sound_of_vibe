"""WORLD constant-pitch resynthesis for deliberately mechanical voice delivery."""

from io import BytesIO
from threading import Lock

_VOICE_LOCK = Lock()


def mechanical_audio(audio: bytes, gender: str = "Female", pitch_hz: float | None = None,
                     pitch_scale: float = 1.0) -> bytes:
    # Bound concurrent preview processing and audio memory use.
    with _VOICE_LOCK:
        return _render(audio, gender, pitch_hz, pitch_scale)


def _render(audio: bytes, gender: str, pitch_hz: float | None, pitch_scale: float) -> bytes:
    import numpy as np
    import pyworld as world
    import soundfile as sf

    samples, rate = sf.read(BytesIO(audio), always_2d=True)
    if len(samples) / rate > 60:
        raise ValueError("Speech segment exceeds 60 seconds")
    if not len(samples):
        raise ValueError("Mechanical speech requires nonempty audio")
    signal = np.ascontiguousarray(samples.mean(axis=1), dtype=np.float64)
    if rate < 16000:
        # WORLD requires >=16 kHz. This only enables processing of legacy 8 kHz
        # models; interpolation cannot restore their missing high frequencies.
        signal = np.interp(np.arange(round(len(signal) * 16000 / rate)) * rate / 16000,
                           np.arange(len(signal)), signal)
        rate = 16000
    # Model speech has a clean signal. DIO preserves voiced/unvoiced boundaries;
    # WORLD separates the spectral envelope from excitation instead of splicing
    # waveform cycles whose alignment can introduce roughness in PSOLA.
    f0, times = world.dio(signal, rate, f0_floor=60, f0_ceil=500, frame_period=5)
    f0 = world.stonemask(signal, f0, times, rate)
    voiced = f0 > 0
    if pitch_hz is None and gender == "Unknown":
        pitch = float(np.median(f0[voiced])) if voiced.any() else 190
    else:
        pitch = pitch_hz if pitch_hz is not None else (120 if gender == "Male" else 190)
    pitch *= pitch_scale
    if not 60 <= pitch <= 500:
        raise ValueError("Mechanical pitch must be between 60 and 500 Hz")
    if voiced.any():
        envelope = world.cheaptrick(signal, f0, times, rate)
        # The default D4C gate can classify clean high-pitched Chinese VITS
        # frames as entirely noise. DIO already supplies voicing decisions;
        # disabling the extra gate retains their harmonic excitation.
        aperiodicity = world.d4c(signal, f0, times, rate, threshold=0)
        aperiodicity[voiced] *= .5
        flat = f0.copy()
        flat[voiced] = pitch
        rendered = world.synthesize(flat, envelope, aperiodicity, rate, frame_period=5)
        # WORLD rounds to a complete frame; keep the exact source duration.
        output = np.pad(rendered, (0, max(0, len(signal) - len(rendered))))[:len(signal), None]
    else:
        output = signal[:, None]
    # Normalize utterance volume without clipping or amplifying silence.
    peak = float(np.max(np.abs(output)))
    if peak > 0:
        output *= min(2.0, 0.85 / peak)
    # Remove provider padding at sentence boundaries, retaining 10 ms to avoid
    # clipping consonant onsets and releases. Internal pauses remain intact.
    active = np.flatnonzero(np.max(np.abs(output), axis=1) > 0.003)
    if len(active):
        padding = round(rate * 0.01)
        output = output[max(0, active[0] - padding):min(len(output), active[-1] + padding + 1)]
    buffer = BytesIO()
    sf.write(buffer, output, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
