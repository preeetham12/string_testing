"""
Part 2 - Noise cleaning (runs only on recordings that passed the quality gate).

  1. Mono + resample to 22,050 Hz
  2. High-pass filter just below the instrument's lowest note
     -> removes rumble, and the 50 Hz electrical hum common in India
        (plus its 100 / 150 Hz echoes for violin)
  3. Spectral gating: learn what the background noise looks like in the quiet
     moments, then turn that noise down everywhere (by 20 dB, not to zero,
     which avoids watery 'musical noise' artefacts)
  4. Trim silence at the start and end ONLY - pauses inside the piece are kept,
     because they are part of the music and the alignment step handles them
  5. Normalise the volume

Production can swap step 3 for DeepFilterNet (a neural denoiser); this simple
version needs no GPU and is easy to understand and tune.
"""
import numpy as np
import librosa
from scipy.ndimage import uniform_filter
from scipy.signal import butter, sosfiltfilt, iirnotch, filtfilt

from engine.pitch import SR
from engine.quality import LOWEST_HZ, load_raw

N_FFT, HOP_STFT = 2048, 512


def high_pass(y, cutoff_hz, sr=SR):
    # 8th order = steep: cuts hum just below the lowest note, keeps the note
    sos = butter(8, cutoff_hz, btype="highpass", fs=sr, output="sos")
    return sosfiltfilt(sos, y)


def detect_hum(y, sr=SR):
    """Is there mains hum, and is it 50 Hz (India, Europe) or 60 Hz (US)?
    Compares the energy exactly at 50/60 Hz with the energy just around it."""
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), 1 / sr)
    best, best_ratio = None, 0.0
    for base in (50.0, 60.0):
        ratios = []
        for k in (1, 2, 3):
            at = (np.abs(freqs - base * k) < 1.0)
            near = (np.abs(freqs - base * k) > 3.0) & (np.abs(freqs - base * k) < 10.0)
            ratios.append(spec[at].max() / (np.median(spec[near]) + 1e-12))
        r = float(np.median(ratios))
        if r > best_ratio:
            best, best_ratio = base, r
    return best if best_ratio > 20 else None  # clear peaks at the hum harmonics


def remove_hum(y, base, sr=SR, up_to_hz=1000.0):
    """Narrow notch at every harmonic of the hum (each only ~2 Hz wide, so a
    note sitting nearby is barely touched)."""
    k = 1
    while base * k < up_to_hz:
        b, a = iirnotch(base * k, Q=base * k / 2.0, fs=sr)
        y = filtfilt(b, a, y)
        k += 1
    return y


def spectral_gate(y, noise_sample=None, strength_db=20.0, sensitivity=1.5):
    S = librosa.stft(y, n_fft=N_FFT, hop_length=HOP_STFT)
    mag_db = librosa.amplitude_to_db(np.abs(S) + 1e-10)

    # Noise profile: from a mic-check sample if we have one, otherwise from the
    # quietest 10% of moments in the recording itself
    if noise_sample is not None:
        noise_db = librosa.amplitude_to_db(
            np.abs(librosa.stft(noise_sample, n_fft=N_FFT, hop_length=HOP_STFT)) + 1e-10)
    else:
        # Frames within 6 dB of the quietest moment = background only. (A fixed
        # "quietest 10%" fails on long takes with little silence: it would
        # include quiet music and then turn real notes down.)
        # Loudness of each moment = total sound POWER. (Averaging decibels over
        # all frequencies doesn't work: a note fills only a few frequencies, so
        # music and silence would look almost the same.)
        energy = 10 * np.log10(np.mean(np.abs(S) ** 2, axis=0) + 1e-20)
        quiet = energy <= energy.min() + 6.0
        if quiet.sum() < 5:
            quiet = energy <= np.sort(energy)[4]
        noise_db = mag_db[:, quiet]
    threshold = noise_db.mean(axis=1) + sensitivity * noise_db.std(axis=1)

    # Soft mask: 1 where clearly music, 0 where clearly noise, smooth in between
    mask = 1 / (1 + np.exp(-(mag_db - threshold[:, None]) / 2.0))
    mask = uniform_filter(mask, size=(3, 5))  # smooth to avoid flicker
    floor = 10 ** (-strength_db / 20)
    gain = floor + (1 - floor) * mask
    return librosa.istft(S * gain, hop_length=HOP_STFT, length=len(y))


def clean_recording(path, instrument="violin", noise_sample=None):
    y, sr = load_raw(path)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    hum = detect_hum(y)
    if hum:
        # only below 2x the lowest note: strong hum lives there, and filters
        # near the instrument's own notes would leave faint ringing tails
        y = remove_hum(y, hum, up_to_hz=LOWEST_HZ[instrument] * 2)
    y = high_pass(y, cutoff_hz=LOWEST_HZ[instrument] * 0.85)
    y = spectral_gate(y, noise_sample=noise_sample)
    y, (start, end) = librosa.effects.trim(y, top_db=35)
    y = y / (np.max(np.abs(y)) + 1e-10) * 0.9
    return y, {"hum_removed_hz": hum, "trimmed_start_s": round(start / SR, 2),
               "trimmed_end_s": round((end) / SR, 2)}
