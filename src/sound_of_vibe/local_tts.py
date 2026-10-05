"""Offline bilingual VITS/Piper synthesis; networking is confined to installation."""

import asyncio
import hashlib
from io import BytesIO
import os
import re
from dataclasses import replace
from pathlib import Path
import tarfile
import tempfile
from threading import Event, Lock
from urllib.request import urlopen

from .speech import EdgeSpeech
from .voice_assignment import voice_profiles

MODELS = {
    "zh": {"name": "vits-icefall-zh-aishell3", "file": "model.onnx", "speakers": 174,
           "sha256": "ab468db3a3308cdd861495e0db2f25d79418a0c00639f74944c7cdf5dd8c6ec1"},
    "en": {"name": "vits-piper-en_GB-vctk-medium", "file": "en_GB-vctk-medium.onnx", "speakers": 109,
           "sha256": "abafd35bdab0a72a3c6b947228ae3cccdf3624db83313c77d1abb5cbc75e1f64"},
}
LOCAL_VOICES = {"zh": "local:zh:066", "en": "local:en:000"}


def model_directory():
    if os.environ.get("SOUND_OF_VIBE_MODEL_DIR"):
        return Path(os.environ["SOUND_OF_VIBE_MODEL_DIR"])
    from .kimi_hooks import state_directory
    return state_directory() / "models"


def local_catalog():
    entries = []
    for language, model in MODELS.items():
        for sid in range(model["speakers"]):
            base = f"local:{language}:{sid:03d}"
            variants = [("", "标准 / Standard", 1.0)]
            if language == "zh":
                variants += [("::low", "低音 / Low", .9), ("::high", "高音 / High", 1.1)]
            for suffix, label, scale in variants:
                entries.append({"ShortName": base + suffix, "BaseVoice": base,
                            "Locale": "zh-CN" if language == "zh" else "en-GB",
                            "Gender": "Unknown", "SpeakerId": sid,
                            "NativeLanguage": language, "PitchHz": None, "PitchScale": scale,
                            "DisplayName": f"{base} · {label} ({scale:.0%} 原音高 / native pitch)"})
    return entries


def local_voices(selected):
    names = {entry["ShortName"] for entry in local_catalog()}
    return {language: selected.get(language) if selected.get(language) in names
            and selected[language].startswith(f"local:{language}:") else fallback
            for language, fallback in LOCAL_VOICES.items()}


def model_ready(directory=None, language=None):
    directory = directory or model_directory()
    for lang in ([language] if language else MODELS):
        model = MODELS[lang]
        files = [model["file"], "tokens.txt"] + (["lexicon.txt", "phone.fst", "date.fst", "number.fst"] if lang == "zh" else ["espeak-ng-data"])
        if not all((directory / model["name"] / name).exists() for name in files):
            return False
    return True


def install_model(progress=print):
    directory = model_directory().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for language, model in MODELS.items():
        if model_ready(directory, language):
            progress(f"Local {language} model ready: {directory / model['name']}")
            continue
        with tempfile.TemporaryDirectory(prefix="tts-install-", dir=directory) as temporary:
            staging = Path(temporary)
            archive = staging / "model.tar.bz2"
            digest = hashlib.sha256()
            url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{model['name']}.tar.bz2"
            progress(f"Downloading local {language} model; subsequent synthesis is offline.")
            with urlopen(url, timeout=30) as source, archive.open("wb") as target:
                total, reported = 0, 0
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    total += len(chunk)
                    if total - reported >= 16 * 1024 * 1024:
                        progress(f"Downloaded {total // (1024 * 1024)} MiB")
                        reported = total
            if digest.hexdigest() != model["sha256"]:
                raise ValueError("Local model checksum mismatch")
            with tarfile.open(archive) as package:
                for member in package.getmembers():
                    destination = (staging / member.name).resolve()
                    if not destination.is_relative_to(staging.resolve()) or not (member.isfile() or member.isdir()):
                        raise ValueError("Unsafe model archive entry")
                options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                package.extractall(staging, **options)
            if not model_ready(staging, language):
                raise ValueError("Incomplete local model archive")
            destination = directory / model["name"]
            if destination.exists():
                raise ValueError(f"Incomplete existing model directory: {destination}; choose another model directory")
            (staging / model["name"]).rename(destination)
    progress(f"Local models installed: {directory}")
    return directory


_ENGINES = {}
_ENGINE_LOCK = Lock()


def language_segments(text, language):
    # Chinese VITS has no English lexicon. Route embedded identifiers/English
    # prose to the English model rather than silently losing those words.
    pattern = (r"([A-Za-z][A-Za-z0-9_./:\\-]*(?:[ \t]+[A-Za-z][A-Za-z0-9_./:\\-]*)*)"
               if language == "zh" else r"([\u3400-\u9fff][\u3400-\u9fff0-9，。！？：；、]*)")
    alternate = "en" if language == "zh" else "zh"
    return [(alternate if index % 2 else language, part)
            for index, part in enumerate(re.split(pattern, text))
            if re.search(r"[A-Za-z0-9\u3400-\u9fff]", part)]


class LocalEngine:
    def __init__(self, directory, threads=2):
        import sherpa_onnx
        if not model_ready(directory):
            raise ValueError("Local TTS model missing; run sound-of-vibe tts install")
        self.tts = {}
        for language, model in MODELS.items():
            folder = directory / model["name"]
            config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(folder / model["file"]), tokens=str(folder / "tokens.txt"),
                    lexicon=str(folder / "lexicon.txt") if language == "zh" else "",
                    data_dir=str(folder / "espeak-ng-data") if language == "en" else ""),
                num_threads=threads, provider="cpu"), silence_scale=.01,
                rule_fsts=",".join(str(folder / name) for name in ("phone.fst", "date.fst", "number.fst")) if language == "zh" else "")
            if not config.validate():
                raise ValueError("Invalid local TTS model configuration")
            self.tts[language] = sherpa_onnx.OfflineTts(config)
        self.lock = Lock()

    def synthesize(self, text, sid, speed, gender, pitch, robotic=True, cancelled=lambda: False,
                   language="zh", secondary_sid=None, pitch_scale=1.0):
        import numpy as np
        import soundfile as sf
        chunks, sample_rate = [], None
        with self.lock:
            for lang, part in language_segments(text, language):
                if cancelled():
                    return b""
                speaker = sid if lang == language else (secondary_sid if secondary_sid is not None else sid % MODELS[lang]["speakers"])
                audio = self.tts[lang].generate(text=part, sid=speaker, speed=speed,
                                              callback=lambda samples, progress: 0 if cancelled() else 1)
                if not len(audio.samples):
                    raise ValueError("Local TTS produced no audio")
                samples = np.asarray(audio.samples)
                sample_rate = sample_rate or audio.sample_rate
                if audio.sample_rate != sample_rate:
                    count = round(len(samples) * sample_rate / audio.sample_rate)
                    samples = np.interp(np.arange(count) * audio.sample_rate / sample_rate,
                                        np.arange(len(samples)), samples)
                chunks.append(samples)
        if cancelled():
            return b""
        if not chunks:
            raise ValueError("Local TTS produced no audio")
        buffer = BytesIO()
        sf.write(buffer, np.concatenate(chunks), sample_rate, format="WAV", subtype="PCM_16")
        data = buffer.getvalue()
        if robotic:
            from .robotic import mechanical_audio
            data = mechanical_audio(data, gender, pitch, pitch_scale)
        return data


def local_engine():
    directory = model_directory().resolve()
    with _ENGINE_LOCK:
        if directory not in _ENGINES:
            _ENGINES[directory] = LocalEngine(directory)
        return _ENGINES[directory]


class LocalSpeech(EdgeSpeech):
    """Reuse ordered playback, chimes and prefetch without online TTS calls."""

    audio_format = "wav"

    def __init__(self, voices=None, rate="+0%", **kwargs):
        super().__init__(local_voices(voices or LOCAL_VOICES), rate, **kwargs)

    async def initialize(self):
        self.initialize_audio()
        self.catalog = local_catalog()
        self.engine = await asyncio.to_thread(local_engine)
        self.edge = self.engine  # Parent playback only requires a ready backend.

    def synthesis_settings(self, narration):
        settings = super().synthesis_settings(narration)
        primary = next(v for v in self.catalog if v["ShortName"] == self.voice_for(narration))
        alternate = "en" if narration.language == "zh" else "zh"
        secondary = self.voice_for(replace(narration, language=alternate))
        entry = next(v for v in self.catalog if v["ShortName"] == secondary)
        return (*settings, entry["SpeakerId"], primary.get("PitchScale", 1))

    async def synthesize(self, narration, settings, stale):
        voice, rate, gender, pitch, robotic, secondary_sid, pitch_scale = settings
        entry = next(v for v in self.catalog if v["BaseVoice"] == voice)
        cancelled = Event()
        try:
            return await asyncio.to_thread(self.engine.synthesize, narration.text,
                                           entry["SpeakerId"], max(.5, 1 + int(rate[:-1]) / 100),
                                           gender, pitch, robotic, lambda: cancelled.is_set() or stale(), narration.language, secondary_sid, pitch_scale)
        except asyncio.CancelledError:
            cancelled.set()
            raise
