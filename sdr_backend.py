#!/usr/bin/env python3
import time

import numpy as np
from rtlsdr import RtlSdr

CENTER_FREQ_MHZ = 420.690
SAMPLE_RATE_HZ = 2_400_000
SDR_GAIN = 33.8
SDR_SAMPLE_SIZE = 1024 * 4
TUNE_OFFSET_HZ = 10_000

DEFAULT_MIN_DB = -11.0
DEFAULT_MAX_DB = 30.0

SAMPLE_RATE_OPTIONS = [1_024_000, 1_800_000, 2_000_000, 2_400_000, 3_000_000]

GAIN_OPTIONS = [
    "auto",
    0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6,
    19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2,
    38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6
]


def init_sdr():
    sdr = RtlSdr()
    sdr.center_freq = CENTER_FREQ_MHZ * 1_000_000
    sdr.sample_rate = SAMPLE_RATE_HZ
    sdr.gain = SDR_GAIN
    return sdr


def get_supported_gains(sdr):
    gains = ["auto"]
    raw_gains = []

    try:
        raw_gains = sdr.valid_gains_db
    except AttributeError:
        try:
            raw_gains = sdr.get_gains()
        except Exception:
            raw_gains = []

    cleaned = []
    for g in raw_gains:
        try:
            val = float(g)
            if val > 100:
                val = val / 10.0
            cleaned.append(val)
        except Exception:
            pass

    cleaned = sorted(set(cleaned))
    gains.extend(cleaned)
    return gains


def apply_sdr_settings(sdr, center_freq_hz, sample_rate_hz, gain_value):
    sdr.center_freq = center_freq_hz + TUNE_OFFSET_HZ
    sdr.sample_rate = sample_rate_hz
    sdr.gain = gain_value


def compute_spectrum(samples, width, min_db, max_db, sample_rate_hz):
    freqs = np.absolute(np.fft.fft(samples))
    freqs = np.fft.fftshift(freqs)

    bins_per_pixel = len(freqs) // width
    if bins_per_pixel > 1:
        freqs = freqs[:bins_per_pixel * width]
        freqs = freqs.reshape(width, bins_per_pixel).mean(axis=1)
    else:
        freqs = freqs[:width]

    freqs_db = 20.0 * np.log10(freqs + 1e-12)

    # Shift bins back so displayed center still matches requested center_freq_hz
    hz_per_bin = sample_rate_hz / max(1, width - 1)
    offset_bins = int(round(TUNE_OFFSET_HZ / hz_per_bin))
    if offset_bins != 0 and abs(offset_bins) < len(freqs_db):
        freqs_db = np.roll(freqs_db, -offset_bins)

    frame_min_db = float(np.min(freqs_db))
    frame_max_db = float(np.max(freqs_db))

    rng = max(1e-6, max_db - min_db)
    scaled = (freqs_db - min_db) / rng
    scaled = np.clip(scaled, 0.0, 1.0)
    return scaled.tolist(), frame_min_db, frame_max_db



def _median_filter_1d(arr, radius=1):
    if len(arr) < 3:
        return arr.copy()
    out = arr.copy()
    for i in range(len(arr)):
        left = max(0, i - radius)
        right = min(len(arr), i + radius + 1)
        out[i] = np.median(arr[left:right])
    return out


def _smooth_filter_1d(arr):
    if len(arr) < 3:
        return arr.copy()
    kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    padded = np.pad(arr, (1, 1), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _center_notch(arr, width=3):
    out = arr.copy()
    c = len(out) // 2
    for i in range(max(0, c - width), min(len(out), c + width + 1)):
        out[i] = np.median(out)
    return out


def _noise_floor_subtract(arr, radius=8, strength=0.6):
    out = arr.copy()
    for i in range(len(arr)):
        left = max(0, i - radius)
        right = min(len(arr), i + radius + 1)
        floor = np.median(arr[left:right])
        out[i] = max(0.0, arr[i] - (floor * strength))
    return out


def _adaptive_threshold(arr, radius=8, factor=1.15):
    out = arr.copy()
    for i in range(len(arr)):
        left = max(0, i - radius)
        right = min(len(arr), i + radius + 1)
        local = np.median(arr[left:right]) * factor
        if out[i] < local:
            out[i] = 0.0
    return out


def _impulse_blank(arr, factor=4.0):
    out = arr.copy()
    med = np.median(arr)
    if med <= 0:
        return out
    limit = med * factor
    for i in range(len(out)):
        if out[i] > limit:
            out[i] = limit
    return out


def apply_display_filters(
    scaled,
    filter_median=False,
    filter_temporal_avg=False,
    filter_noise_floor=False,
    filter_peak_hold=False,
    filter_center_notch=False,
    filter_adaptive_threshold=False,
    filter_freq_smoothing=False,
    filter_impulse_blanking=False,
    filter_display_clamp=False,
    temporal_state=None,
    peak_hold_state=None,
):
    arr = np.array(scaled, dtype=np.float32)

    if filter_impulse_blanking:
        arr = _impulse_blank(arr)

    if filter_center_notch:
        arr = _center_notch(arr, width=2)

    if filter_median:
        arr = _median_filter_1d(arr, radius=1)

    if filter_freq_smoothing:
        arr = _smooth_filter_1d(arr)

    if filter_noise_floor:
        arr = _noise_floor_subtract(arr, radius=8, strength=0.6)

    if filter_adaptive_threshold:
        arr = _adaptive_threshold(arr, radius=8, factor=1.15)

    if filter_temporal_avg and temporal_state is not None:
        if temporal_state.get("prev") is None:
            temporal_state["prev"] = arr.copy()
        arr = (temporal_state["prev"] * 0.65) + (arr * 0.35)
        temporal_state["prev"] = arr.copy()

    if filter_peak_hold and peak_hold_state is not None:
        if peak_hold_state.get("hold") is None:
            peak_hold_state["hold"] = arr.copy()
        hold = peak_hold_state["hold"]
        hold = np.maximum(arr, hold * 0.985)
        peak_hold_state["hold"] = hold
        arr = hold

    if filter_display_clamp:
        arr = np.clip(arr, 0.0, 0.92)

    arr = np.clip(arr, 0.0, 1.0)
    return arr.tolist()


def sdr_worker(shared, stop_event, width):
    sdr = None
    try:
        sdr = init_sdr()
        last_applied = None
        temporal_state = {"prev": None}
        peak_hold_state = {"hold": None}

        while not stop_event.is_set():
            with shared["lock"]:
                desired = (
                    shared["center_freq_hz"],
                    shared["sample_rate_hz"],
                    shared["gain_value"],
                    shared["sample_size"],
                    shared["display_min_db"],
                    shared["display_max_db"],
                    shared.get("filter_median", False),
                    shared.get("filter_temporal_avg", False),
                    shared.get("filter_noise_floor", False),
                    shared.get("filter_peak_hold", False),
                    shared.get("filter_center_notch", False),
                    shared.get("filter_adaptive_threshold", False),
                    shared.get("filter_freq_smoothing", False),
                    shared.get("filter_impulse_blanking", False),
                    shared.get("filter_display_clamp", False),
                )

            (
                center_freq_hz,
                sample_rate_hz,
                gain_value,
                sample_size,
                display_min_db,
                display_max_db,
                filter_median,
                filter_temporal_avg,
                filter_noise_floor,
                filter_peak_hold,
                filter_center_notch,
                filter_adaptive_threshold,
                filter_freq_smoothing,
                filter_impulse_blanking,
                filter_display_clamp,
            ) = desired

            if desired[:3] != last_applied:
                try:
                    apply_sdr_settings(sdr, center_freq_hz, sample_rate_hz, gain_value)
                    last_applied = desired[:3]
                except Exception:
                    time.sleep(0.05)
                    continue

            try:
                samples = sdr.read_samples(sample_size)
                bins, frame_min_db, frame_max_db = compute_spectrum(
                    samples,
                    width,
                    display_min_db,
                    display_max_db,
                    sample_rate_hz,
                )
                bins = apply_display_filters(
                    bins,
                    filter_median=filter_median,
                    filter_temporal_avg=filter_temporal_avg,
                    filter_noise_floor=filter_noise_floor,
                    filter_peak_hold=filter_peak_hold,
                    filter_center_notch=filter_center_notch,
                    filter_adaptive_threshold=filter_adaptive_threshold,
                    filter_freq_smoothing=filter_freq_smoothing,
                    filter_impulse_blanking=filter_impulse_blanking,
                    filter_display_clamp=filter_display_clamp,
                    temporal_state=temporal_state,
                    peak_hold_state=peak_hold_state,
                )

                with shared["lock"]:
                    shared["bins"] = bins
                    shared["frame_min_db"] = frame_min_db
                    shared["frame_max_db"] = frame_max_db
                    shared["fresh"] = True
            except Exception:
                time.sleep(0.05)
    finally:
        if sdr is not None:
            try:
                sdr.close()
            except Exception:
                pass
