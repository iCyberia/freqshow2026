#!/usr/bin/env python3
import json
import os
import sys
import select
import subprocess
import threading
import time
import socket
from datetime import datetime
from collections import deque

import numpy as np
import pygame
import st7796_lcd as st7796
from evdev import InputDevice, ecodes, list_devices
from gpiozero import Button, RotaryEncoder

import display
import sdr_backend
from sound_control import SoundControl

# Encoder 1: tuning / settings navigation
ENC1_A = 5
ENC1_B = 6
ENC1_SW = 26

# Encoder 2: gain / settings adjustment
ENC2_A = 16
ENC2_B = 20
ENC2_SW = 21

TUNE_STEPS_HZ = [100_000_000, 10_000_000, 1_000_000, 100_000, 10_000, 1_000]
BRIGHTNESS_OPTIONS = [2, 5, 10, 15, 20, 30, 40, 60, 80, 100]
WF_SPEED_OPTIONS = ["Normal", "Fast", "Max"]
SPECTRUM_SPEED_OPTIONS = ["Slow", "Normal", "Fast", "Max"]
WF_AVG_OPTIONS = [0.0, 0.20, 0.35, 0.50, 0.65, 0.80]

BAND_PRESETS = [
    ("All", None, None),
    ("AM BCB", 530_000, 1_710_000),
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_500, 5_406_500),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("CB", 26_965_000, 27_405_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("FM", 88_000_000, 108_000_000),
    ("Air Nav", 108_000_000, 117_950_000),
    ("Air Voice", 118_000_000, 136_975_000),
    ("2m", 144_000_000, 148_000_000),
    ("Marine", 156_000_000, 162_025_000),
    ("NOAA WX", 162_400_000, 162_550_000),
    ("1.25m", 222_000_000, 225_000_000),
    ("70cm", 420_000_000, 450_000_000),
    ("33cm", 902_000_000, 928_000_000),
    ("23cm", 1_240_000_000, 1_300_000_000),
]

TOUCH_SWAP_XY = True
TOUCH_FLIP_X = True
TOUCH_FLIP_Y = False
TOUCH_DEBOUNCE_MS = 250

SETTINGS_FILE = "freqshow_v3_settings.json"
SETTINGS_IDLE_SAVE_SECONDS = 5.0
FAVORITES_FILE = "freqshow_favorites.json"
REPEATERS_FILE = "freqshow_repeaters.json"
ENC1_LONG_PRESS_SECONDS = 3.0
SCAN_LONG_PRESS_SECONDS = 2.0
SCAN_HANG_SECONDS = 1.5
SCAN_STEP_INTERVAL = 0.12
SCAN_MIN_HZ = 100_000
SCAN_MAX_HZ = 1_750_000_000

# Touch tune settings
TOUCH_TUNE_SEARCH_RADIUS = 80
TOUCH_TUNE_HISTORY_SECONDS = 0.75
TOUCH_TUNE_PEAK_THRESHOLD = 0.20
TOUCH_TUNE_REFINE_RADIUS = 12
SIG_DB_SMOOTH_ALPHA = 0.02
SPECTRUM_SMOOTH_ALPHA = 0.22
PEAK_LABEL_SMOOTH_ALPHA = 0.06
PEAK_MARKER_COUNT = 3
VIEW_MODE_GAIN = 0
VIEW_MODE_ZOOM = 1
VIEW_MODE_LABELS = ["Gain", "Zoom"]
ZOOM_LEVELS = [1, 2, 4, 8, 16, 32]

GEAR_HIT_RECT = getattr(display, 'GEAR_HIT_RECT', display.GEAR_RECT.inflate(60, 60))


def load_settings():
    defaults = {
        "center_freq_hz": int(sdr_backend.CENTER_FREQ_MHZ * 1_000_000),
        "sample_rate_index": sdr_backend.SAMPLE_RATE_OPTIONS.index(sdr_backend.SAMPLE_RATE_HZ),
        "gain_index": sdr_backend.GAIN_OPTIONS.index(sdr_backend.SDR_GAIN),
        "display_min_db": sdr_backend.DEFAULT_MIN_DB,
        "display_max_db": sdr_backend.DEFAULT_MAX_DB,
        "wf_speed_index": 0,
        "spectrum_speed_index": 2,
        "wf_avg_index": 2,
        "brightness_index": len(BRIGHTNESS_OPTIONS) - 1,
        "tune_step_index": 1,
        "squelch_level": 3,
        "last_settings_selected": 0,
        "filter_median": False,
        "filter_temporal_avg": False,
        "filter_noise_floor": False,
        "filter_peak_hold": False,
        "filter_center_notch": False,
        "filter_adaptive_threshold": False,
        "filter_freq_smoothing": False,
        "filter_impulse_blanking": False,
        "filter_display_clamp": False,
        "sound_volume": 50,
        "sound_muted": False,
    }

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        defaults.update(loaded)
    except Exception:
        pass

    return defaults


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def find_touch_device():
    try:
        return InputDevice("/dev/input/event0")
    except Exception:
        pass

    for path in list_devices():
        try:
            dev = InputDevice(path)
            name = (dev.name or "").lower()
            if "touch" in name or "goodix" in name or "xpt2046" in name or "ads7846" in name:
                return dev
        except Exception:
            pass
    return None


def get_touch_axes(dev):
    caps = dev.capabilities().get(ecodes.EV_ABS, [])
    abs_codes = {item[0] for item in caps}

    if ecodes.ABS_X in abs_codes and ecodes.ABS_Y in abs_codes:
        x_code = ecodes.ABS_X
        y_code = ecodes.ABS_Y
    elif ecodes.ABS_MT_POSITION_X in abs_codes and ecodes.ABS_MT_POSITION_Y in abs_codes:
        x_code = ecodes.ABS_MT_POSITION_X
        y_code = ecodes.ABS_MT_POSITION_Y
    else:
        x_code = ecodes.ABS_X
        y_code = ecodes.ABS_Y

    try:
        x_info = dev.absinfo(x_code)
        y_info = dev.absinfo(y_code)
        x_min, x_max = x_info.min, x_info.max
        y_min, y_max = y_info.min, y_info.max
    except Exception:
        x_min, x_max = 0, 4095
        y_min, y_max = 0, 4095

    return x_code, y_code, x_min, x_max, y_min, y_max


def normalize_touch(raw_x, raw_y, width, height, axes):
    _x_code, _y_code, x_min, x_max, y_min, y_max = axes

    x = raw_x
    y = raw_y

    if TOUCH_SWAP_XY:
        x, y = y, x
        x_min, x_max, y_min, y_max = y_min, y_max, x_min, x_max

    if x_max <= x_min or y_max <= y_min:
        return None

    sx = int((x - x_min) * (width - 1) / max(1, x_max - x_min))
    sy = int((y - y_min) * (height - 1) / max(1, y_max - y_min))

    sx = max(0, min(width - 1, sx))
    sy = max(0, min(height - 1, sy))

    if TOUCH_FLIP_X:
        sx = width - 1 - sx
    if TOUCH_FLIP_Y:
        sy = height - 1 - sy

    return sx, sy


def poll_touch_click(dev, width, height, state, timeout=0.0):
    if dev is None:
        return None

    try:
        axes = get_touch_axes(dev)
    except Exception:
        return None

    result = None
    try:
        r, _, _ = select.select([dev.fd], [], [], timeout)
        if not r:
            return None

        for event in dev.read():
            if event.type == ecodes.EV_ABS:
                if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                    state["raw_x"] = event.value
                elif event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                    state["raw_y"] = event.value
            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                if event.value == 1:
                    state["down"] = True
                elif event.value == 0:
                    state["down"] = False
                    if state["raw_x"] is not None and state["raw_y"] is not None:
                        result = normalize_touch(state["raw_x"], state["raw_y"], width, height, axes)
    except Exception:
        return None

    return result


def is_touch_tune_area(point):
    if display.GEAR_HIT_RECT.collidepoint(point):
        return False
    _x, y = point
    if y < display.TOP_H:
        return True
    if y >= (display.TOP_H + display.MID_H):
        return True
    return False


def x_to_frequency(x, center_freq_hz, sample_rate_hz):
    span_hz = float(sample_rate_hz)
    left_freq_hz = center_freq_hz - (span_hz / 2.0)
    hz_per_pixel = span_hz / max(1, display.WIDTH - 1)
    return int(left_freq_hz + (x * hz_per_pixel))


def bin_to_frequency(bin_index, center_freq_hz, sample_rate_hz):
    return x_to_frequency(bin_index, center_freq_hz, sample_rate_hz)


def find_valid_peaks(bins, touched_x, radius, threshold):
    if len(bins) < 3:
        return []

    left = max(1, touched_x - radius)
    right = min(len(bins) - 2, touched_x + radius)

    if right < left:
        return []

    window = bins[left:right + 1]
    local_median = float(np.median(window))

    peaks = []
    for i in range(left, right + 1):
        if bins[i] > bins[i - 1] and bins[i] >= bins[i + 1]:
            prominence = bins[i] - local_median
            if prominence >= threshold:
                peaks.append({
                    "bin": i,
                    "distance": abs(i - touched_x),
                    "value": float(bins[i]),
                    "prominence": float(prominence),
                })

    return peaks


def choose_touch_tune_bin(recent_bins, touched_x):
    if not recent_bins:
        return touched_x

    aggregate = {}

    for _ts, frame_bins in recent_bins:
        peaks = find_valid_peaks(
            frame_bins,
            touched_x,
            TOUCH_TUNE_SEARCH_RADIUS,
            TOUCH_TUNE_PEAK_THRESHOLD,
        )

        for peak in peaks:
            bin_index = peak["bin"]
            entry = aggregate.setdefault(
                bin_index,
                {
                    "hits": 0,
                    "distance_sum": 0.0,
                    "prominence_sum": 0.0,
                    "value_max": 0.0,
                },
            )
            entry["hits"] += 1
            entry["distance_sum"] += peak["distance"]
            entry["prominence_sum"] += peak["prominence"]
            entry["value_max"] = max(entry["value_max"], peak["value"])

    if not aggregate:
        return touched_x

    best_bin = None
    best_score = None

    for bin_index, data in aggregate.items():
        avg_distance = data["distance_sum"] / max(1, data["hits"])
        avg_prominence = data["prominence_sum"] / max(1, data["hits"])

        score = (
            (data["hits"] * 3.0) +
            (avg_prominence * 28.0) +
            (data["value_max"] * 8.0) -
            (avg_distance * 0.45)
        )

        if best_score is None or score > best_score:
            best_score = score
            best_bin = bin_index

    if best_bin is None:
        return touched_x

    latest_bins = recent_bins[-1][1]
    refine_left = max(1, best_bin - TOUCH_TUNE_REFINE_RADIUS)
    refine_right = min(len(latest_bins) - 2, best_bin + TOUCH_TUNE_REFINE_RADIUS)

    refined_bin = best_bin
    refined_value = latest_bins[best_bin]

    for i in range(refine_left, refine_right + 1):
        if latest_bins[i] > latest_bins[i - 1] and latest_bins[i] >= latest_bins[i + 1]:
            if latest_bins[i] > refined_value:
                refined_value = latest_bins[i]
                refined_bin = i

    return refined_bin


def open_center_freq_keypad(current_hz):
    return True, f"{current_hz / 1_000_000:.3f}".rstrip("0").rstrip(".")


def round_frequency_hz(freq_hz, step_hz):
    return int(round(freq_hz / step_hz) * step_hz)


def apply_keypad_freq(value_text, current_hz):
    if not value_text or value_text == ".":
        return False, current_hz

    try:
        mhz = float(value_text)
    except ValueError:
        return False, current_hz

    hz = int(mhz * 1_000_000)
    hz = max(100_000, min(1_750_000_000, hz))
    return True, hz


def handle_keypad_press(label, value_text):
    if label in "0123456789":
        return value_text + label, False

    if label == ".":
        if "." not in value_text:
            if value_text == "":
                return "0.", False
            return value_text + ".", False
        return value_text, False

    if label == "OK":
        return value_text, True

    return value_text, False


def keypad_hit_test(point, buttons, clear_rect, cancel_rect, pad=20):
    if point is None:
        return (None, None)

    if clear_rect.inflate(pad * 2, pad * 2).collidepoint(point):
        return ("CLEAR", clear_rect)

    if cancel_rect.inflate(pad * 2, pad * 2).collidepoint(point):
        return ("CANCEL", cancel_rect)

    for label, rect in buttons.items():
        if rect.inflate(pad * 2, pad * 2).collidepoint(point):
            return (label, rect)

    return (None, None)



def get_band_limits(band_index):
    band_index = max(0, min(len(BAND_PRESETS) - 1, int(band_index)))
    _label, low_hz, high_hz = BAND_PRESETS[band_index]
    if low_hz is None or high_hz is None:
        return SCAN_MIN_HZ, SCAN_MAX_HZ
    return int(low_hz), int(high_hz)


def clamp_freq_to_band(freq_hz, band_index):
    low_hz, high_hz = get_band_limits(band_index)
    return max(low_hz, min(high_hz, int(freq_hz)))


def wrap_freq_in_band(freq_hz, band_index):
    low_hz, high_hz = get_band_limits(band_index)
    if freq_hz > high_hz:
        return low_hz
    if freq_hz < low_hz:
        return high_hz
    return int(freq_hz)



def restart_app(current_settings_snapshot):
    try:
        save_settings(current_settings_snapshot())
    except Exception as e:
        print(f"Restart save failed: {e}", flush=True)

    try:
        pygame.quit()
    except Exception:
        pass

    time.sleep(0.2)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def get_band_label(center_freq_hz):
    mhz = center_freq_hz / 1_000_000.0

    if 1.8 <= mhz <= 2.0:
        return "160m HF"
    if 3.5 <= mhz <= 4.0:
        return "80m HF"
    if 5.3305 <= mhz <= 5.4065:
        return "60m HF"
    if 7.0 <= mhz <= 7.3:
        return "40m HF"
    if 10.1 <= mhz <= 10.15:
        return "30m HF"
    if 14.0 <= mhz <= 14.35:
        return "20m HF"
    if 18.068 <= mhz <= 18.168:
        return "17m HF"
    if 21.0 <= mhz <= 21.45:
        return "15m HF"
    if 24.89 <= mhz <= 24.99:
        return "12m HF"
    if 28.0 <= mhz <= 29.7:
        return "10m HF"
    if 50.0 <= mhz <= 54.0:
        return "6m VHF"
    if 144.0 <= mhz <= 148.0:
        return "2m VHF"
    if 222.0 <= mhz <= 225.0:
        return "1.25m VHF"
    if 420.0 <= mhz <= 450.0:
        return "70cm UHF"
    if 902.0 <= mhz <= 928.0:
        return "33cm UHF"
    if 1240.0 <= mhz <= 1300.0:
        return "23cm UHF"
    if 2300.0 <= mhz <= 2310.0 or 2390.0 <= mhz <= 2450.0:
        return "13cm SHF"
    if 3300.0 <= mhz <= 3500.0:
        return "9cm SHF"
    if 5650.0 <= mhz <= 5925.0:
        return "5cm SHF"

    return ""


def get_center_signal_percent(bins, radius=6):
    if not bins:
        return 0

    center = len(bins) // 2
    left = max(0, center - radius)
    right = min(len(bins) - 1, center + radius)

    window = bins[left:right + 1]
    if not window:
        return 0

    value = max(window)
    return max(0, min(100, int(round(value * 50))))


def get_center_signal_db(bins, min_db, max_db, radius=6):
    if not bins:
        return min_db

    center = len(bins) // 2
    left = max(0, center - radius)
    right = min(len(bins) - 1, center + radius)

    window = bins[left:right + 1]
    if not window:
        return min_db

    value = max(window)
    return min_db + (value * (max_db - min_db))


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


def is_wifi_connected():
    candidates = ["/sys/class/net/wlan0/operstate", "/sys/class/net/wlp1s0/operstate"]

    for operstate_path in candidates:
        try:
            with open(operstate_path, "r", encoding="utf-8") as f:
                state = f.read().strip().lower()
            if state == "up":
                return True
        except Exception:
            pass

    return False


def set_wifi_enabled(enabled):
    cmd = ["sudo", "rfkill", "unblock" if enabled else "block", "wifi"]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.25)
        return True, f"Wi-Fi {'enabled' if enabled else 'disabled'}"
    except Exception:
        return False, "Wi-Fi change failed"


def load_repeaters_db():
    try:
        with open(REPEATERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        bands = data.get("bands", [])
        if isinstance(bands, list):
            return bands
    except Exception:
        pass
    return []


def load_favorites():
    try:
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        favorites = []
        for item in data:
            if isinstance(item, dict) and "freq_hz" in item:
                freq_hz = int(item["freq_hz"])
                name = str(item.get("name", f"{freq_hz / 1_000_000:.3f} MHz"))
                favorites.append({"name": name, "freq_hz": freq_hz})
        favorites.sort(key=lambda x: x["freq_hz"])
        return favorites
    except Exception:
        return []


def save_favorites(favorites):
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(favorites, f, indent=2)


def add_favorite(favorites, freq_hz):
    freq_hz = int(freq_hz)
    for item in favorites:
        if int(item["freq_hz"]) == freq_hz:
            return False, "Favorite exists"

    entry = {
        "name": f"{freq_hz / 1_000_000:.3f} MHz",
        "freq_hz": freq_hz,
    }
    favorites.append(entry)
    favorites.sort(key=lambda x: x["freq_hz"])
    save_favorites(favorites)
    return True, "Favorite added"


def draw_toast(screen, font, text):
    if not text:
        return

    img = font.render(text, True, display.WHITE)
    pad_x = 10
    pad_y = 6
    rect = pygame.Rect(
        (display.WIDTH - img.get_width()) // 2 - pad_x,
        display.HEIGHT - 56,
        img.get_width() + pad_x * 2,
        img.get_height() + pad_y * 2,
    )
    pygame.draw.rect(screen, (20, 20, 20), rect, border_radius=8)
    pygame.draw.rect(screen, (120, 120, 120), rect, 2, border_radius=8)
    screen.blit(img, (rect.x + pad_x, rect.y + pad_y))


def squelch_level_to_threshold(level):
    level = max(1, min(12, int(level)))
    return 0.04 + ((level - 1) * 0.03)


def is_squelch_open(bins, level, radius=6, extra_threshold=0.0):
    if not bins:
        return False

    center = len(bins) // 2
    left = max(0, center - radius)
    right = min(len(bins) - 1, center + radius)
    window = bins[left:right + 1]
    if not window:
        return False

    value = max(window)
    return value >= (squelch_level_to_threshold(level) + extra_threshold)


def apply_visual_squelch(bins, level):
    if not bins:
        return bins

    threshold = squelch_level_to_threshold(level)
    return [0.0 if v < threshold else v for v in bins]



def transform_bins_for_view(bins, zoom_level):
    if not bins:
        return bins

    zoom_level = max(1, int(zoom_level))
    if zoom_level == 1:
        return list(bins)

    width = len(bins)
    center = (width - 1) / 2.0
    out = []

    for x in range(width):
        src_x = center + ((x - center) / zoom_level)
        src_x = max(0.0, min(width - 1.0, src_x))

        left = int(src_x)
        right = min(width - 1, left + 1)
        frac = src_x - left

        value = bins[left] * (1.0 - frac) + bins[right] * frac
        out.append(value)

    return out


def get_left_mid_text(enc2_mode, gain_index, zoom_index):
    if enc2_mode == VIEW_MODE_ZOOM:
        return f"Zoom {ZOOM_LEVELS[zoom_index]}x"
    return f"Gain {sdr_backend.GAIN_OPTIONS[gain_index]}"
    

def is_scan_stop_signal(bins, level, radius=4):
    if not bins:
        return False

    center = len(bins) // 2
    left = max(0, center - radius)
    right = min(len(bins) - 1, center + radius)
    window = bins[left:right + 1]
    if not window:
        return False

    peak = max(window)

    # Make scan stop logic much stricter than normal squelch
    threshold = max(0.72, squelch_level_to_threshold(level) + 0.28)
    return peak >= threshold



def adjust_setting(
    item,
    delta,
    settings_center_freq_hz,
    sample_rate_index,
    gain_index,
    settings_min_db,
    settings_max_db,
    wf_speed_index,
    spectrum_speed_index,
    peak_marker_count,
    wf_avg_index,
    brightness_index,
    squelch_level,
    filter_median,
    filter_temporal_avg,
    filter_noise_floor,
    filter_peak_hold,
    filter_center_notch,
    filter_adaptive_threshold,
    filter_freq_smoothing,
    filter_impulse_blanking,
    filter_display_clamp,
    tune_step_hz,
):
    if delta == 0:
        return (
            settings_center_freq_hz,
            sample_rate_index,
            gain_index,
            settings_min_db,
            settings_max_db,
            wf_speed_index,
            spectrum_speed_index,
            peak_marker_count,
            wf_avg_index,
            brightness_index,
            squelch_level,
            filter_median,
            filter_temporal_avg,
            filter_noise_floor,
            filter_peak_hold,
            filter_center_notch,
            filter_adaptive_threshold,
            filter_freq_smoothing,
            filter_impulse_blanking,
            filter_display_clamp,
        )

    if item == "center_freq":
        settings_center_freq_hz += delta * tune_step_hz
        settings_center_freq_hz = max(100_000, min(1_750_000_000, settings_center_freq_hz))
    elif item == "sample_rate":
        sample_rate_index = max(0, min(len(sdr_backend.SAMPLE_RATE_OPTIONS) - 1, sample_rate_index + delta))
    elif item == "gain":
        gain_index = max(0, min(len(sdr_backend.GAIN_OPTIONS) - 1, gain_index + delta))
    elif item == "min_db":
        settings_min_db += delta * 1.0
        if settings_min_db >= settings_max_db - 1.0:
            settings_min_db = settings_max_db - 1.0
    elif item == "max_db":
        settings_max_db += delta * 1.0
        if settings_max_db <= settings_min_db + 1.0:
            settings_max_db = settings_min_db + 1.0
    elif item == "wf_speed":
        wf_speed_index = max(0, min(len(WF_SPEED_OPTIONS) - 1, wf_speed_index + delta))
    elif item == "spectrum_speed":
        spectrum_speed_index = max(0, min(len(SPECTRUM_SPEED_OPTIONS) - 1, spectrum_speed_index + delta))
    elif item == "peak_markers":
        peak_marker_count = max(0, min(4, peak_marker_count + delta))
    elif item == "wf_average":
        wf_avg_index = max(0, min(len(WF_AVG_OPTIONS) - 1, wf_avg_index + delta))
    elif item == "brightness":
        brightness_index = max(0, min(len(BRIGHTNESS_OPTIONS) - 1, brightness_index + delta))
    elif item == "squelch":
        squelch_level = max(1, min(12, squelch_level + delta))
    elif item == "filter_median":
        filter_median = not filter_median
    elif item == "filter_temporal_avg":
        filter_temporal_avg = not filter_temporal_avg
    elif item == "filter_noise_floor":
        filter_noise_floor = not filter_noise_floor
    elif item == "filter_peak_hold":
        filter_peak_hold = not filter_peak_hold
    elif item == "filter_center_notch":
        filter_center_notch = not filter_center_notch
    elif item == "filter_adaptive_threshold":
        filter_adaptive_threshold = not filter_adaptive_threshold
    elif item == "filter_freq_smoothing":
        filter_freq_smoothing = not filter_freq_smoothing
    elif item == "filter_impulse_blanking":
        filter_impulse_blanking = not filter_impulse_blanking
    elif item == "filter_display_clamp":
        filter_display_clamp = not filter_display_clamp

    return (
        settings_center_freq_hz,
        sample_rate_index,
        gain_index,
        settings_min_db,
        settings_max_db,
        wf_speed_index,
        spectrum_speed_index,
        peak_marker_count,
        wf_avg_index,
        brightness_index,
        squelch_level,
        filter_median,
        filter_temporal_avg,
        filter_noise_floor,
        filter_peak_hold,
        filter_center_notch,
        filter_adaptive_threshold,
        filter_freq_smoothing,
        filter_impulse_blanking,
        filter_display_clamp,
    )


def main():
    pygame.init()
    pygame.font.init()

    lcd = None  # DSI/KMS display mode


    touch_dev = find_touch_device()
    touch_state = {"raw_x": None, "raw_y": None, "down": False}
    last_touch_ms = 0

    temp_sdr = None
    shared = None
    worker = None
    stop_event = threading.Event()

    try:
        sound = SoundControl()

        temp_sdr = sdr_backend.init_sdr()
        sdr_backend.GAIN_OPTIONS = sdr_backend.get_supported_gains(temp_sdr)
        print("Supported gains:", sdr_backend.GAIN_OPTIONS, flush=True)
        temp_sdr.close()
        temp_sdr = None

        enc1 = RotaryEncoder(a=ENC1_A, b=ENC1_B, max_steps=0, wrap=False)
        btn1 = Button(ENC1_SW, pull_up=True, bounce_time=0.05)

        enc2 = RotaryEncoder(a=ENC2_A, b=ENC2_B, max_steps=0, wrap=False)
        btn2 = Button(ENC2_SW, pull_up=True, bounce_time=0.05)
        _ = btn2

        settings_data = load_settings()
        band_index = max(0, min(settings_data.get("band_index", 0), len(BAND_PRESETS) - 1))
        peak_marker_count = max(0, min(settings_data.get("peak_marker_count", 3), 4))
        zoom_index = max(0, min(settings_data.get("zoom_index", 0), len(ZOOM_LEVELS) - 1))
        enc2_mode = max(0, min(settings_data.get("enc2_mode", VIEW_MODE_GAIN), len(VIEW_MODE_LABELS) - 1))

        tune_step_index = settings_data["tune_step_index"]
        center_freq_hz = settings_data["center_freq_hz"]

        sample_rate_index = settings_data["sample_rate_index"]
        gain_index = settings_data["gain_index"]
        brightness_index = settings_data["brightness_index"]
        squelch_level = settings_data.get("squelch_level", 3)
        sound_volume = max(0, min(99, int(settings_data.get("sound_volume", 50))))
        sound_muted = bool(settings_data.get("sound_muted", False))
        last_settings_selected = settings_data.get("last_settings_selected", 0)

        filter_median = settings_data.get("filter_median", False)
        filter_temporal_avg = settings_data.get("filter_temporal_avg", False)
        filter_noise_floor = settings_data.get("filter_noise_floor", False)
        filter_peak_hold = settings_data.get("filter_peak_hold", False)
        filter_center_notch = settings_data.get("filter_center_notch", False)
        filter_adaptive_threshold = settings_data.get("filter_adaptive_threshold", False)
        filter_freq_smoothing = settings_data.get("filter_freq_smoothing", False)
        filter_impulse_blanking = settings_data.get("filter_impulse_blanking", False)
        filter_display_clamp = settings_data.get("filter_display_clamp", False)

        display_min_db = settings_data["display_min_db"]
        display_max_db = settings_data["display_max_db"]

        wf_speed_index = max(0, min(settings_data["wf_speed_index"], len(WF_SPEED_OPTIONS) - 1))
        wf_avg_index = settings_data["wf_avg_index"]

        settings_open = False
        settings_dirty = False
        last_user_input_time = time.time()
        settings_selected = max(0, min(last_settings_selected, len(display.settings_items()) - 1))

        keypad_open = False
        keypad_value = ""
        favorites_open = False
        favorites = load_favorites()
        favorites_selected = 0
        favorite_toast_text = ""
        favorite_toast_until = 0.0
        filters_open = False
        filters_selected = 0
        wifi_menu_open = False
        wifi_menu_selected = 0
        sound_menu_open = False
        sound_menu_selected = 0

        repeater_bands = load_repeaters_db()
        repeaters_bands_open = False
        repeaters_list_open = False
        repeaters_bands_selected = 0
        repeaters_list_selected = 0
        current_repeater_band = None
        band_menu_open = False
        active_repeater_location = ""
        active_repeater_freq_hz = None

        repeater_bands = load_repeaters_db()
        repeaters_bands_open = False
        repeaters_list_open = False
        repeaters_bands_selected = 0
        repeaters_list_selected = 0
        current_repeater_band = None

        settings_center_freq_hz = center_freq_hz
        settings_min_db = display_min_db
        settings_max_db = display_max_db
        settings_sample_rate_index = sample_rate_index
        settings_gain_index = gain_index
        settings_wf_speed_index = wf_speed_index
        settings_wf_avg_index = wf_avg_index
        settings_brightness_index = brightness_index
        settings_peak_marker_count = peak_marker_count
        settings_squelch_level = squelch_level
        band_index = 0
        settings_band_index = band_index
        settings_filter_median = filter_median
        settings_filter_temporal_avg = filter_temporal_avg
        settings_filter_noise_floor = filter_noise_floor
        settings_filter_peak_hold = filter_peak_hold
        settings_filter_center_notch = filter_center_notch
        settings_filter_adaptive_threshold = filter_adaptive_threshold
        settings_filter_freq_smoothing = filter_freq_smoothing
        settings_filter_impulse_blanking = filter_impulse_blanking
        settings_filter_display_clamp = filter_display_clamp
        band_index = 0
        settings_band_index = band_index
        band_menu_selected = band_index
        band_index = 0

        enc1_last_steps = 0
        enc2_last_steps = 0
        enc1_button_latch = False
        enc2_button_latch = False
        enc1_press_start = None
        enc1_longpress_done = False
        btn2_press_start = None
        btn2_longpress_done = False
        scan_active = False
        scan_pause_until = 0.0
        last_scan_step_time = 0.0
        scan_hit_count = 0

        shared = {
            "lock": threading.Lock(),
            "center_freq_hz": center_freq_hz,
            "sample_rate_hz": sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index],
            "gain_value": sdr_backend.GAIN_OPTIONS[gain_index],
            "sample_size": sdr_backend.SDR_SAMPLE_SIZE,
            "display_min_db": display_min_db,
            "display_max_db": display_max_db,
            "bins": [0.0] * display.WIDTH,
            "frame_min_db": display_min_db,
            "frame_max_db": display_max_db,
            "fresh": False,
            "filter_median": filter_median,
            "filter_temporal_avg": filter_temporal_avg,
            "filter_noise_floor": filter_noise_floor,
            "filter_peak_hold": filter_peak_hold,
            "filter_center_notch": filter_center_notch,
            "filter_adaptive_threshold": filter_adaptive_threshold,
            "filter_freq_smoothing": filter_freq_smoothing,
            "filter_impulse_blanking": filter_impulse_blanking,
            "filter_display_clamp": filter_display_clamp,
        }

        worker = threading.Thread(
            target=sdr_backend.sdr_worker,
            args=(shared, stop_event, display.WIDTH),
            daemon=True,
        )
        worker.start()

        display.apply_brightness(lcd, BRIGHTNESS_OPTIONS[brightness_index])

        if sound.available():
            try:
                sound.set_volume(sound_volume)
                sound.set_mute(sound_muted)
            except Exception as e:
                print(f"Sound init failed: {e}", flush=True)

        screen = pygame.display.set_mode((display.WIDTH, display.HEIGHT), pygame.FULLSCREEN)
        pygame.display.set_caption('FreqShow')
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("FreqShow v3")

        font_big = pygame.font.SysFont("DejaVu Sans", 32, bold=True)
        font_mid = pygame.font.SysFont("DejaVu Sans", 20, bold=True)
        font_small = pygame.font.SysFont("DejaVu Sans", 20)
        font_band = pygame.font.SysFont("DejaVu Sans", 12)
        font_tiny = pygame.font.SysFont("DejaVu Sans", 10)
        font_title = pygame.font.SysFont("DejaVu Sans", 20, bold=True)
        font_keypad = pygame.font.SysFont("DejaVu Sans", 24, bold=True)

        waterfall = pygame.Surface((display.WIDTH, display.BOT_H))
        waterfall.fill(display.BG_BOT)

        top_static = display.build_top_static_surface()
        peak_label_x_smooth = None
        top_surface = display.render_top_surface(top_static, font_big, font_band, [0.0] * display.WIDTH, center_freq_hz, sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index], peak_label_x_smooth, peak_marker_count)

        mid_static = display.build_mid_static_surface()
        gain_text = get_left_mid_text(enc2_mode, gain_index, zoom_index)
        step_text = display.format_step_mhz(TUNE_STEPS_HZ[tune_step_index])
        mid_surface = display.render_mid_surface(
            mid_static,
            font_mid,
            gain_text,
            step_text,
            sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index],
        )

        def current_settings_snapshot():
            return {
                "center_freq_hz": center_freq_hz,
                "sample_rate_index": sample_rate_index,
                "gain_index": gain_index,
                "display_min_db": display_min_db,
                "display_max_db": display_max_db,
                "wf_speed_index": wf_speed_index,
                "wf_avg_index": wf_avg_index,
                "brightness_index": brightness_index,
                "peak_marker_count": peak_marker_count,
                "zoom_index": zoom_index,
                "enc2_mode": enc2_mode,
                "band_index": band_index,
                "tune_step_index": tune_step_index,
                "squelch_level": squelch_level,
                "last_settings_selected": last_settings_selected,
                "filter_median": filter_median,
                "filter_temporal_avg": filter_temporal_avg,
                "filter_noise_floor": filter_noise_floor,
                "filter_peak_hold": filter_peak_hold,
                "filter_center_notch": filter_center_notch,
                "filter_adaptive_threshold": filter_adaptive_threshold,
                "filter_freq_smoothing": filter_freq_smoothing,
                "filter_impulse_blanking": filter_impulse_blanking,
                "filter_display_clamp": filter_display_clamp,
                "last_settings_selected": last_settings_selected,
                "filter_median": filter_median,
                "filter_temporal_avg": filter_temporal_avg,
                "filter_noise_floor": filter_noise_floor,
                "filter_peak_hold": filter_peak_hold,
                "filter_center_notch": filter_center_notch,
                "filter_adaptive_threshold": filter_adaptive_threshold,
                "filter_freq_smoothing": filter_freq_smoothing,
                "filter_impulse_blanking": filter_impulse_blanking,
                "filter_display_clamp": filter_display_clamp,
                "squelch_level": squelch_level,
                "sound_volume": sound_volume,
                "sound_muted": sound_muted,
            }

        last_saved_snapshot = current_settings_snapshot()

        recent_bins = deque()

        screen.fill(display.BLACK)
        screen.blit(top_surface, (0, 0))
        screen.blit(mid_surface, (0, display.TOP_H))
        screen.blit(waterfall, (0, display.TOP_H + display.MID_H))

        test_label = font_big.render("V3 START", True, display.WHITE)
        screen.blit(test_label, (20, 20))
        pygame.display.flip()
        display.present_to_lcd(lcd, screen)
        time.sleep(1.0)

        running = True
        clock = pygame.time.Clock()
        sig_text = "SIG --.- dB"
        sig_db_smoothed = None
        spectrum_frame_counter = 0
        spectrum_speed_index = 2
        settings_spectrum_speed_index = spectrum_speed_index
        last_bins = [0.0] * display.WIDTH
        display_bins = [0.0] * display.WIDTH
        have_fresh_bins = False
        mid_dirty = True
        top_dirty = True
        bot_full_dirty = False

        while running:
            was_settings_open = settings_open
            pending_click = None

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if getattr(event, "button", 1) == 1:
                        pending_click = event.pos

                    if (not settings_open) and display.GEAR_HIT_RECT.collidepoint(event.pos):
                        scan_active = False
                        settings_open = True
                        settings_selected = max(0, min(last_settings_selected, len(display.settings_items()) - 1))
                        settings_center_freq_hz = center_freq_hz
                        settings_min_db = display_min_db
                        settings_max_db = display_max_db
                        settings_sample_rate_index = sample_rate_index
                        settings_gain_index = gain_index
                        settings_wf_speed_index = wf_speed_index
                        settings_wf_avg_index = wf_avg_index
                        settings_brightness_index = brightness_index
                        settings_squelch_level = squelch_level
                        settings_filter_median = filter_median
                        settings_filter_temporal_avg = filter_temporal_avg
                        settings_filter_noise_floor = filter_noise_floor
                        settings_filter_peak_hold = filter_peak_hold
                        settings_filter_center_notch = filter_center_notch
                        settings_filter_adaptive_threshold = filter_adaptive_threshold
                        settings_filter_freq_smoothing = filter_freq_smoothing
                        settings_filter_impulse_blanking = filter_impulse_blanking
                        settings_filter_display_clamp = filter_display_clamp
                        enc1_last_steps = enc1.steps
                        enc2_last_steps = enc2.steps
                        last_user_input_time = time.time()

            point = poll_touch_click(touch_dev, display.WIDTH, display.HEIGHT, touch_state, timeout=0.0)
            if point is None and pending_click is not None:
                point = pending_click

            if point is not None and not settings_open:
                now_ms = pygame.time.get_ticks()
                if now_ms - last_touch_ms >= TOUCH_DEBOUNCE_MS:
                    last_touch_ms = now_ms

                    if display.GEAR_HIT_RECT.collidepoint(point):
                        scan_active = False
                        settings_open = True
                        settings_selected = max(0, min(last_settings_selected, len(display.settings_items()) - 1))
                        settings_center_freq_hz = center_freq_hz
                        settings_min_db = display_min_db
                        settings_max_db = display_max_db
                        settings_sample_rate_index = sample_rate_index
                        settings_gain_index = gain_index
                        settings_wf_speed_index = wf_speed_index
                        settings_wf_avg_index = wf_avg_index
                        settings_brightness_index = brightness_index
                        settings_squelch_level = squelch_level
                        settings_filter_median = filter_median
                        settings_filter_temporal_avg = filter_temporal_avg
                        settings_filter_noise_floor = filter_noise_floor
                        settings_filter_peak_hold = filter_peak_hold
                        settings_filter_center_notch = filter_center_notch
                        settings_filter_adaptive_threshold = filter_adaptive_threshold
                        settings_filter_freq_smoothing = filter_freq_smoothing
                        settings_filter_impulse_blanking = filter_impulse_blanking
                        settings_filter_display_clamp = filter_display_clamp
                        enc1_last_steps = enc1.steps
                        enc2_last_steps = enc2.steps
                        last_user_input_time = time.time()

                    elif is_touch_tune_area(point):
                        touched_x = max(0, min(display.WIDTH - 1, point[0]))
                        sample_rate_hz = sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index]

                        winning_bin = choose_touch_tune_bin(recent_bins, touched_x)

                        if recent_bins:
                            new_center_freq_hz = bin_to_frequency(
                                winning_bin,
                                center_freq_hz,
                                sample_rate_hz,
                            )
                        else:
                            new_center_freq_hz = x_to_frequency(
                                touched_x,
                                center_freq_hz,
                                sample_rate_hz,
                            )

                        new_center_freq_hz = round_frequency_hz(new_center_freq_hz, 5_000)
                        new_center_freq_hz = max(100_000, min(1_750_000_000, new_center_freq_hz))

                        center_freq_hz = new_center_freq_hz
                        settings_dirty = True
                        last_user_input_time = time.time()
                        top_dirty = True
                        mid_dirty = True
                        bot_full_dirty = True

                        with shared["lock"]:
                            shared["center_freq_hz"] = center_freq_hz

            if settings_open:
                if repeaters_list_open:
                    items = current_repeater_band.get("repeaters", []) if current_repeater_band else []
                    view_items = items if items else [{"freq_mhz": None, "offset": "", "access": "", "location": "(No repeaters)", "status": "Unknown"}]

                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        repeaters_list_selected = max(0, min(len(view_items) - 1, repeaters_list_selected + enc1_delta))
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    if btn1.is_pressed and not enc1_button_latch:
                        selected_rep = view_items[repeaters_list_selected]
                        if selected_rep.get("freq_mhz") is not None:
                            center_freq_hz = int(round(float(selected_rep["freq_mhz"]) * 1_000_000))
                            active_repeater_freq_hz = center_freq_hz
                            active_repeater_location = str(selected_rep.get("location", ""))
                            with shared["lock"]:
                                shared["center_freq_hz"] = center_freq_hz
                            settings_dirty = True
                            last_user_input_time = time.time()
                            top_dirty = True
                            mid_dirty = True
                            bot_full_dirty = True
                            repeaters_list_open = False
                            repeaters_bands_open = False
                            settings_open = False
                        enc1_button_latch = True
                    elif not btn1.is_pressed:
                        enc1_button_latch = False

                    if btn2.is_pressed and not enc2_button_latch:
                        repeaters_list_open = False
                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False

                elif repeaters_bands_open:
                    items = repeater_bands if repeater_bands else [{"label": "(No bands)", "repeaters": []}]

                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        repeaters_bands_selected = max(0, min(len(items) - 1, repeaters_bands_selected + enc1_delta))
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    if btn1.is_pressed and not enc1_button_latch:
                        current_repeater_band = items[repeaters_bands_selected]
                        repeaters_list_selected = 0
                        repeaters_list_open = True
                        enc1_button_latch = True
                    elif not btn1.is_pressed:
                        enc1_button_latch = False

                    if btn2.is_pressed and not enc2_button_latch:
                        repeaters_bands_open = False
                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False

                elif filters_open:
                    items = display.filter_settings_items()

                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        filters_selected = max(0, min(len(items) - 1, filters_selected + enc1_delta))
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    enc2_steps = enc2.steps
                    enc2_delta = enc2_steps - enc2_last_steps
                    if enc2_delta != 0:
                        selected_filter = items[filters_selected]
                        if selected_filter == "filter_median":
                            settings_filter_median = not settings_filter_median
                        elif selected_filter == "filter_temporal_avg":
                            settings_filter_temporal_avg = not settings_filter_temporal_avg
                        elif selected_filter == "filter_noise_floor":
                            settings_filter_noise_floor = not settings_filter_noise_floor
                        elif selected_filter == "filter_peak_hold":
                            settings_filter_peak_hold = not settings_filter_peak_hold
                        elif selected_filter == "filter_center_notch":
                            settings_filter_center_notch = not settings_filter_center_notch
                        elif selected_filter == "filter_adaptive_threshold":
                            settings_filter_adaptive_threshold = not settings_filter_adaptive_threshold
                        elif selected_filter == "filter_freq_smoothing":
                            settings_filter_freq_smoothing = not settings_filter_freq_smoothing
                        elif selected_filter == "filter_impulse_blanking":
                            settings_filter_impulse_blanking = not settings_filter_impulse_blanking
                        elif selected_filter == "filter_display_clamp":
                            settings_filter_display_clamp = not settings_filter_display_clamp

                        enc2_last_steps = enc2_steps
                        settings_dirty = True
                        last_user_input_time = time.time()

                    if btn2.is_pressed and not enc2_button_latch:
                        filters_open = False
                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False

                elif favorites_open:
                    items = favorites if favorites else [{"name": "(No favorites)", "freq_hz": None}]

                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        favorites_selected = max(0, min(len(items) - 1, favorites_selected + enc1_delta))
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    if btn1.is_pressed and not enc1_button_latch:
                        selected_fav = items[favorites_selected]
                        if selected_fav.get("freq_hz") is not None:
                            center_freq_hz = int(selected_fav["freq_hz"])
                            with shared["lock"]:
                                shared["center_freq_hz"] = center_freq_hz
                            settings_dirty = True
                            last_user_input_time = time.time()
                            top_dirty = True
                            mid_dirty = True
                            bot_full_dirty = True
                            favorites_open = False
                            settings_open = False
                        enc1_button_latch = True
                    elif not btn1.is_pressed:
                        enc1_button_latch = False

                    if btn2.is_pressed and not enc2_button_latch:
                        favorites_open = False
                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False



                elif sound_menu_open:
                    items = [
                        f"Mute: {'ON' if sound_muted else 'OFF'}",
                        f"Volume: {sound_volume}",
                        "Back",
                    ]

                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        sound_menu_selected = max(0, min(len(items) - 1, sound_menu_selected + enc1_delta))
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    enc2_steps = enc2.steps
                    enc2_delta = enc2_steps - enc2_last_steps
                    if enc2_delta != 0:
                        if sound_menu_selected == 0:
                            sound_muted = not sound_muted
                            if sound.available():
                                try:
                                    sound.set_mute(sound_muted)
                                except Exception as e:
                                    print(f"Sound mute failed: {e}", flush=True)
                            settings_dirty = True
                        elif sound_menu_selected == 1:
                            sound_volume = max(0, min(99, sound_volume + enc2_delta))
                            if sound.available():
                                try:
                                    sound.set_volume(sound_volume)
                                except Exception as e:
                                    print(f"Sound volume failed: {e}", flush=True)
                            settings_dirty = True

                        enc2_last_steps = enc2_steps
                        last_user_input_time = time.time()

                    if btn1.is_pressed and not enc1_button_latch:
                        if sound_menu_selected == 0:
                            sound_muted = not sound_muted
                            if sound.available():
                                try:
                                    sound.set_mute(sound_muted)
                                except Exception as e:
                                    print(f"Sound mute failed: {e}", flush=True)
                            settings_dirty = True
                            last_user_input_time = time.time()
                        elif sound_menu_selected == 2:
                            sound_menu_open = False
                        enc1_button_latch = True
                    elif not btn1.is_pressed:
                        enc1_button_latch = False

                    if btn2.is_pressed and not enc2_button_latch:
                        sound_menu_open = False
                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False
                elif wifi_menu_open:
                    items = ["Wi-Fi: ON", "Wi-Fi: OFF", "Cancel"]

                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        wifi_menu_selected = max(0, min(len(items) - 1, wifi_menu_selected + enc1_delta))
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    if btn1.is_pressed and not enc1_button_latch:
                        choice = items[wifi_menu_selected]
                        if choice == "Wi-Fi: ON":
                            _ok, msg = set_wifi_enabled(True)
                            favorite_toast_text = msg
                            favorite_toast_until = time.time() + 2.0
                        elif choice == "Wi-Fi: OFF":
                            _ok, msg = set_wifi_enabled(False)
                            favorite_toast_text = msg
                            favorite_toast_until = time.time() + 2.0

                        wifi_menu_open = False
                        enc1_button_latch = True
                    elif not btn1.is_pressed:
                        enc1_button_latch = False

                    if btn2.is_pressed and not enc2_button_latch:
                        wifi_menu_open = False
                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False

                elif band_menu_open:
                    if point is not None:
                        now_ms = pygame.time.get_ticks()
                        if now_ms - last_touch_ms >= TOUCH_DEBOUNCE_MS:
                            last_touch_ms = now_ms
                            layout = display.build_band_menu_layout(band_menu_selected, BAND_PRESETS)
                            for row in layout["rows"]:
                                if row["rect"].collidepoint(point):
                                    band_menu_selected = row["index"]
                                    settings_band_index = band_menu_selected
                                    settings_center_freq_hz = clamp_freq_to_band(settings_center_freq_hz, settings_band_index)
                                    settings_dirty = True
                                    band_menu_open = False
                                    last_user_input_time = time.time()
                                    break
                    else:
                        enc1_steps = enc1.steps
                        enc1_delta = enc1_steps - enc1_last_steps
                        if enc1_delta != 0:
                            band_menu_selected = max(0, min(len(BAND_PRESETS) - 1, band_menu_selected + enc1_delta))
                            enc1_last_steps = enc1_steps
                            last_user_input_time = time.time()

                        if btn1.is_pressed and not enc1_button_latch:
                            settings_band_index = band_menu_selected
                            settings_center_freq_hz = clamp_freq_to_band(settings_center_freq_hz, settings_band_index)
                            settings_dirty = True
                            band_menu_open = False
                            last_user_input_time = time.time()
                            enc1_button_latch = True
                        elif not btn1.is_pressed:
                            enc1_button_latch = False

                        if btn2.is_pressed and not enc2_button_latch:
                            band_menu_open = False
                            enc2_button_latch = True
                        elif not btn2.is_pressed:
                            enc2_button_latch = False

                elif keypad_open:
                    if point is not None:
                        now_ms = pygame.time.get_ticks()
                        if now_ms - last_touch_ms >= TOUCH_DEBOUNCE_MS:
                            last_touch_ms = now_ms
                            layout = display.build_keypad_layout()
                            clear_rect = layout["clear_rect"]
                            cancel_rect = layout["cancel_rect"]
                            buttons = layout["buttons"]

                            hit_label, _hit_rect = keypad_hit_test(point, buttons, clear_rect, cancel_rect, pad=20)

                            if hit_label == "CLEAR":
                                keypad_value = ""
                                point = None
                            elif hit_label == "CANCEL":
                                keypad_open = False
                                point = None
                            elif hit_label is not None:
                                result = handle_keypad_press(hit_label, keypad_value)
                                if not isinstance(result, tuple) or len(result) != 2:
                                    result = (keypad_value, False)
                                keypad_value, pressed_ok = result
                                point = None
                                if pressed_ok:
                                    ok, new_hz = apply_keypad_freq(keypad_value, settings_center_freq_hz)
                                    if ok:
                                        settings_center_freq_hz = new_hz
                                        settings_dirty = True
                                        last_user_input_time = time.time()
                                    keypad_open = False
                else:
                    enc1_steps = enc1.steps
                    enc1_delta = enc1_steps - enc1_last_steps
                    if enc1_delta != 0:
                        settings_selected = max(0, min(len(display.settings_items()) - 1, settings_selected + enc1_delta))
                        last_settings_selected = settings_selected
                        enc1_last_steps = enc1_steps
                        last_user_input_time = time.time()

                    enc2_steps = enc2.steps
                    enc2_delta = enc2_steps - enc2_last_steps
                    if enc2_delta != 0:
                        (
                            settings_center_freq_hz,
                            settings_sample_rate_index,
                            settings_gain_index,
                            settings_min_db,
                            settings_max_db,
                            settings_wf_speed_index,
                            settings_spectrum_speed_index,
                            settings_peak_marker_count,
                            settings_wf_avg_index,
                            settings_brightness_index,
                            settings_squelch_level,
                            settings_filter_median,
                            settings_filter_temporal_avg,
                            settings_filter_noise_floor,
                            settings_filter_peak_hold,
                            settings_filter_center_notch,
                            settings_filter_adaptive_threshold,
                            settings_filter_freq_smoothing,
                            settings_filter_impulse_blanking,
                            settings_filter_display_clamp,
                        ) = adjust_setting(
                            display.settings_items()[settings_selected],
                            enc2_delta,
                            settings_center_freq_hz,
                            settings_sample_rate_index,
                            settings_gain_index,
                            settings_min_db,
                            settings_max_db,
                            settings_wf_speed_index,
                            settings_spectrum_speed_index,
                            settings_peak_marker_count,
                            settings_wf_avg_index,
                            settings_brightness_index,
                            settings_squelch_level,
                            settings_filter_median,
                            settings_filter_temporal_avg,
                            settings_filter_noise_floor,
                            settings_filter_peak_hold,
                            settings_filter_center_notch,
                            settings_filter_adaptive_threshold,
                            settings_filter_freq_smoothing,
                            settings_filter_impulse_blanking,
                            settings_filter_display_clamp,
                            TUNE_STEPS_HZ[tune_step_index],
                        )
                        enc2_last_steps = enc2_steps
                        settings_dirty = True
                        last_user_input_time = time.time()

                        if display.settings_items()[settings_selected] == "brightness":
                            display.apply_brightness(lcd, BRIGHTNESS_OPTIONS[settings_brightness_index])

                    if btn1.is_pressed and not enc1_button_latch:
                        selected_item = display.settings_items()[settings_selected]

                        if selected_item == "band_limit":
                            band_menu_open = True
                            band_menu_selected = settings_band_index
                            print(f"BAND MENU OPEN requested: selected={band_menu_selected}", flush=True)
                        elif selected_item == "center_freq":
                            keypad_open, keypad_value = open_center_freq_keypad(settings_center_freq_hz)
                        elif selected_item == "favorites":
                            favorites = load_favorites()
                            favorites_open = True
                            favorites_selected = 0
                        elif selected_item == "repeaters":
                            repeater_bands = load_repeaters_db()
                            repeaters_bands_open = True
                            repeaters_bands_selected = 0
                        elif selected_item == "filters":
                            filters_open = True
                            filters_selected = 0
                        elif selected_item == "wifi":
                            wifi_menu_open = True
                            wifi_menu_selected = 0
                        elif selected_item == "sound":
                            sound_menu_open = True
                            sound_menu_selected = 0
                        elif selected_item == "restart":
                            save_settings(current_settings_snapshot())
                            pygame.quit()
                            os.execv(sys.executable, [sys.executable] + sys.argv)
                        elif selected_item == "quit":
                            running = False

                        enc1_button_latch = True
                    elif not btn1.is_pressed:
                        enc1_button_latch = False

                    if btn2.is_pressed and not enc2_button_latch:
                        selected_item = display.settings_items()[settings_selected]

                        if selected_item == "quit":
                            running = False
                        elif selected_item == "restart":
                            restart_app(current_settings_snapshot)
                        else:
                            center_freq_hz = settings_center_freq_hz
                            sample_rate_index = settings_sample_rate_index
                            gain_index = settings_gain_index
                            display_min_db = settings_min_db
                            display_max_db = settings_max_db
                            wf_speed_index = settings_wf_speed_index
                            spectrum_speed_index = settings_spectrum_speed_index
                            peak_marker_count = settings_peak_marker_count
                            wf_avg_index = settings_wf_avg_index
                            brightness_index = settings_brightness_index
                            band_index = settings_band_index
                            center_freq_hz = clamp_freq_to_band(center_freq_hz, band_index)
                            squelch_level = settings_squelch_level
                            filter_median = settings_filter_median
                            filter_temporal_avg = settings_filter_temporal_avg
                            filter_noise_floor = settings_filter_noise_floor
                            filter_peak_hold = settings_filter_peak_hold
                            filter_center_notch = settings_filter_center_notch
                            filter_adaptive_threshold = settings_filter_adaptive_threshold
                            filter_freq_smoothing = settings_filter_freq_smoothing
                            filter_impulse_blanking = settings_filter_impulse_blanking
                            filter_display_clamp = settings_filter_display_clamp

                            with shared["lock"]:
                                shared["center_freq_hz"] = center_freq_hz
                                shared["sample_rate_hz"] = sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index]
                                shared["gain_value"] = sdr_backend.GAIN_OPTIONS[gain_index]
                                shared["display_min_db"] = display_min_db
                                shared["display_max_db"] = display_max_db
                                shared["filter_median"] = filter_median
                                shared["filter_temporal_avg"] = filter_temporal_avg
                                shared["filter_noise_floor"] = filter_noise_floor
                                shared["filter_peak_hold"] = filter_peak_hold
                                shared["filter_center_notch"] = filter_center_notch
                                shared["filter_adaptive_threshold"] = filter_adaptive_threshold
                                shared["filter_freq_smoothing"] = filter_freq_smoothing
                                shared["filter_impulse_blanking"] = filter_impulse_blanking
                                shared["filter_display_clamp"] = filter_display_clamp

                            display.apply_brightness(lcd, BRIGHTNESS_OPTIONS[brightness_index])

                            settings_dirty = True
                            last_user_input_time = time.time()
                            mid_dirty = True
                            top_dirty = True
                            spectrum_frame_counter = 0
                            bot_full_dirty = True
                            settings_open = False

                        enc2_button_latch = True
                    elif not btn2.is_pressed:
                        enc2_button_latch = False
            else:
                if btn2.is_pressed:
                    if btn2_press_start is None:
                        btn2_press_start = time.time()
                    elif (not btn2_longpress_done) and ((time.time() - btn2_press_start) >= SCAN_LONG_PRESS_SECONDS):
                        scan_active = not scan_active
                        print(f"SCAN TOGGLED: {scan_active}", flush=True)
                        btn2_longpress_done = True
                        scan_pause_until = 0.0
                        last_scan_step_time = 0.0
                        scan_hit_count = 0
                        last_user_input_time = time.time()
                        top_dirty = True
                        mid_dirty = True
                        bot_full_dirty = True
                else:
                    if (btn2_press_start is not None) and (not btn2_longpress_done):
                        if scan_active:
                            scan_active = False
                            show_scan_confirm = False
                            scan_confirm_until = 0.0
                            last_user_input_time = time.time()
                            top_dirty = True
                            mid_dirty = True
                            bot_full_dirty = True
                        else:
                            enc2_mode = VIEW_MODE_ZOOM if enc2_mode == VIEW_MODE_GAIN else VIEW_MODE_GAIN
                            last_user_input_time = time.time()
                            mid_dirty = True
                    btn2_press_start = None
                    btn2_longpress_done = False

                enc1_steps = enc1.steps
                enc1_delta = enc1_steps - enc1_last_steps
                if enc1_delta != 0:
                    center_freq_hz += enc1_delta * TUNE_STEPS_HZ[tune_step_index]
                    center_freq_hz = clamp_freq_to_band(center_freq_hz, band_index)
                    enc1_last_steps = enc1_steps
                    settings_dirty = True
                    last_user_input_time = time.time()
                    mid_dirty = True
                    top_dirty = True
                    with shared["lock"]:
                        shared["center_freq_hz"] = center_freq_hz

                if btn1.is_pressed:
                    if enc1_press_start is None:
                        enc1_press_start = time.time()
                    elif (not enc1_longpress_done) and ((time.time() - enc1_press_start) >= ENC1_LONG_PRESS_SECONDS):
                        added, msg = add_favorite(favorites, center_freq_hz)
                        if added:
                            favorite_toast_text = msg
                            favorite_toast_until = time.time() + 3.0
                        enc1_longpress_done = True
                else:
                    if enc1_press_start is not None and not enc1_longpress_done:
                        tune_step_index = (tune_step_index + 1) % len(TUNE_STEPS_HZ)
                        settings_dirty = True
                        last_user_input_time = time.time()
                        mid_dirty = True
                    enc1_press_start = None
                    enc1_longpress_done = False

                enc2_steps = enc2.steps
                enc2_delta = enc2_steps - enc2_last_steps
                if enc2_delta != 0:
                    enc2_last_steps = enc2_steps
                    last_user_input_time = time.time()
                    mid_dirty = True

                    if enc2_mode == VIEW_MODE_GAIN:
                        gain_index = max(0, min(len(sdr_backend.GAIN_OPTIONS) - 1, gain_index + enc2_delta))
                        settings_dirty = True
                        with shared["lock"]:
                            shared["gain_value"] = sdr_backend.GAIN_OPTIONS[gain_index]
                    else:
                        zoom_index = max(0, min(len(ZOOM_LEVELS) - 1, zoom_index + enc2_delta))
                        peak_label_x_smooth = None
                        top_dirty = True
                        bot_full_dirty = True

            have_fresh_bins = False
            with shared["lock"]:
                if shared["fresh"]:
                    last_bins = shared["bins"]
                    shared["fresh"] = False
                    have_fresh_bins = True

            bins = last_bins
            bins = apply_visual_squelch(bins, squelch_level)
            view_bins = transform_bins_for_view(bins, ZOOM_LEVELS[zoom_index])
            now = time.time()

            if have_fresh_bins:
                recent_bins.append((now, list(bins)))
                cutoff = now - TOUCH_TUNE_HISTORY_SECONDS
                while recent_bins and recent_bins[0][0] < cutoff:
                    recent_bins.popleft()

            sig_db = get_center_signal_db(view_bins, display_min_db, display_max_db, radius=6)
            if sig_db_smoothed is None:
                sig_db_smoothed = sig_db
            else:
                sig_db_smoothed = (
                    sig_db_smoothed * (1.0 - SIG_DB_SMOOTH_ALPHA)
                    + sig_db * SIG_DB_SMOOTH_ALPHA
                )
            sig_text = f"SIG {sig_db_smoothed:.1f} dB"

            if settings_dirty and (now - last_user_input_time) >= SETTINGS_IDLE_SAVE_SECONDS:
                snapshot = current_settings_snapshot()
                if snapshot != last_saved_snapshot:
                    save_settings(snapshot)
                    last_saved_snapshot = snapshot
                    print("Settings auto-saved.", flush=True)
                settings_dirty = False

            if have_fresh_bins:
                spectrum_frame_counter += 1

            spectrum_divider = [4, 2, 1, 1][spectrum_speed_index]
            should_refresh_top = top_dirty or (have_fresh_bins and (spectrum_frame_counter % spectrum_divider == 0))

            if should_refresh_top:
                for i, v in enumerate(view_bins):
                    display_bins[i] = (display_bins[i] * (1.0 - SPECTRUM_SMOOTH_ALPHA)) + (v * SPECTRUM_SMOOTH_ALPHA)

                if display_bins:
                    peak_x_raw = max(range(len(display_bins)), key=lambda i: (display_bins[i], -abs(i - (display.WIDTH // 2))))
                    if peak_label_x_smooth is None:
                        peak_label_x_smooth = float(peak_x_raw)
                    else:
                        peak_label_x_smooth = (
                            peak_label_x_smooth * (1.0 - PEAK_LABEL_SMOOTH_ALPHA)
                            + peak_x_raw * PEAK_LABEL_SMOOTH_ALPHA
                        )

                top_surface = display.render_top_surface(
                    top_static,
                    font_big,
                    font_band,
                    display_bins,
                    center_freq_hz,
                    sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index],
                    peak_label_x_smooth,
                    peak_marker_count,
                )
                top_dirty = False

            if mid_dirty:
                gain_text = get_left_mid_text(enc2_mode, gain_index, zoom_index)
                step_text = display.format_step_mhz(TUNE_STEPS_HZ[tune_step_index])
                mid_surface = display.render_mid_surface(
                    mid_static,
                    font_mid,
                    gain_text,
                    step_text,
                    sdr_backend.SAMPLE_RATE_OPTIONS[sample_rate_index],
                )

            screen.fill(display.BLACK)
            screen.blit(top_surface, (0, 0))

            if active_repeater_location and active_repeater_freq_hz is not None:
                if abs(center_freq_hz - active_repeater_freq_hz) <= 10_000:
                    loc_img = font_tiny.render(f"{active_repeater_location} Repeater", True, display.WHITE)
                    loc_x = display.WIDTH - 36 - loc_img.get_width()
                    if loc_x < 200:
                        loc_x = 200
                    screen.blit(loc_img, (loc_x, 26))
                else:
                    active_repeater_location = ""
                    active_repeater_freq_hz = None

            if band_index != 0:
                band_limit_text = f"{BAND_PRESETS[band_index][0]} Band LOCKED"
                band_limit_img = font_tiny.render(band_limit_text, True, display.WHITE)
                band_limit_x = (display.WIDTH - band_limit_img.get_width()) // 2
                screen.blit(band_limit_img, (band_limit_x, 4))

            clock_label = font_small.render(datetime.now().strftime("%H:%M"), True, display.WHITE)
            clock_x = display.WIDTH - clock_label.get_width() - 8
            screen.blit(clock_label, (clock_x, 6))

            if is_wifi_connected():
                wifi_x = clock_x - 18
                ip_text = get_local_ip()
                ip_x = wifi_x

                if ip_text:
                    ip_font = pygame.font.SysFont("DejaVu Sans", 14)
                    ip_label = ip_font.render(ip_text, True, display.WHITE)
                    ip_x = wifi_x - 20 - ip_label.get_width()
                    screen.blit(ip_label, (ip_x, 10))

                if scan_active:
                    scan_font = pygame.font.SysFont("DejaVu Sans", 14)
                    scan_img = scan_font.render("SCAN", True, display.BLACK)
                    scan_pad_x = 5
                    scan_pad_y = 2
                    scan_w = scan_img.get_width() + (scan_pad_x * 2)
                    scan_h = scan_img.get_height() + (scan_pad_y * 2)
                    scan_x = ip_x - 10 - scan_w
                    scan_y = 8
                    scan_rect = pygame.Rect(scan_x, scan_y, scan_w, scan_h)
                    pygame.draw.rect(screen, (255, 220, 80), scan_rect, border_radius=6)
                    pygame.draw.rect(screen, display.BLACK, scan_rect, 1, border_radius=6)
                    screen.blit(scan_img, (scan_x + scan_pad_x, scan_y + scan_pad_y))

                display.draw_wifi_icon(screen, wifi_x, 14, display.WHITE)
            screen.blit(mid_surface, (0, display.TOP_H))

            if have_fresh_bins:
                waterfall_rows_per_frame = [1, 2, 4][wf_speed_index]
                row = pygame.Surface((display.WIDTH, 1))
                avg_alpha = WF_AVG_OPTIONS[wf_avg_index]

                for x, v in enumerate(view_bins):
                    new_color = display.color_from_power(v)
                    old_color = waterfall.get_at((x, 0))[:3]
                    pixel = display.lerp_color(new_color, old_color, avg_alpha)
                    row.set_at((x, 0), pixel)

                waterfall.scroll(dy=waterfall_rows_per_frame)

                for y in range(waterfall_rows_per_frame):
                    waterfall.blit(row, (0, y))

            screen.blit(waterfall, (0, display.TOP_H + display.MID_H))
            pygame.draw.line(
                screen,
                display.CENTER_LINE,
                (display.WIDTH // 2, display.TOP_H + display.MID_H),
                (display.WIDTH // 2, display.HEIGHT - 1),
                1,
            )

            sig_label = font_small.render(sig_text, True, display.WHITE)
            screen.blit(sig_label, (display.WIDTH - sig_label.get_width() - 10, display.HEIGHT - sig_label.get_height() - 8))

            status_label = font_small.render(
                f"{display_min_db:.0f}/{display_max_db:.0f} dB",
                True,
                display.WHITE,
            )
            screen.blit(status_label, (80, display.HEIGHT - status_label.get_height() - 8))

            band_text = get_band_label(center_freq_hz)
            if band_text:
                band_label = font_band.render(band_text, True, display.WHITE)
                screen.blit(
                    band_label,
                    ((display.WIDTH - band_label.get_width()) // 2,
                     display.HEIGHT - band_label.get_height() - 8),
                )

            if scan_active and not settings_open:
                now_scan = time.time()
                stop_signal = is_scan_stop_signal(last_bins, squelch_level, radius=4)

                if stop_signal:
                    scan_hit_count += 1
                else:
                    scan_hit_count = 0

                if scan_hit_count >= 2:
                    scan_pause_until = now_scan + SCAN_HANG_SECONDS
                elif now_scan >= scan_pause_until and (now_scan - last_scan_step_time) >= SCAN_STEP_INTERVAL:
                    center_freq_hz += TUNE_STEPS_HZ[tune_step_index]

                    center_freq_hz = wrap_freq_in_band(center_freq_hz, band_index)

                    with shared["lock"]:
                        shared["center_freq_hz"] = center_freq_hz

                    settings_dirty = True
                    last_user_input_time = now_scan
                    last_scan_step_time = now_scan
                    top_dirty = True
                    mid_dirty = True
                    bot_full_dirty = True

            if not settings_open:
                display.draw_gear_icon(screen, display.GEAR_RECT, display.WHITE)
            else:
                if repeaters_list_open:
                    display.draw_repeaters_screen(
                        screen,
                        font_title,
                        font_small,
                        font_band,
                        repeaters_list_selected,
                        current_repeater_band.get("repeaters", []) if current_repeater_band else [],
                    )
                elif repeaters_bands_open:
                    display.draw_repeater_bands_screen(
                        screen,
                        font_title,
                        font_small,
                        repeaters_bands_selected,
                        repeater_bands,
                    )
                elif filters_open:
                    display.draw_filter_settings_screen(
                        screen,
                        font_title,
                        font_small,
                        filters_selected,
                        settings_filter_median,
                        settings_filter_temporal_avg,
                        settings_filter_noise_floor,
                        settings_filter_peak_hold,
                        settings_filter_center_notch,
                        settings_filter_adaptive_threshold,
                        settings_filter_freq_smoothing,
                        settings_filter_impulse_blanking,
                        settings_filter_display_clamp,
                    )
                elif favorites_open:
                    display.draw_favorites_screen(
                        screen,
                        font_title,
                        font_small,
                        favorites_selected,
                        favorites,
                    )
                elif band_menu_open:
                    display.draw_simple_menu(
                        screen,
                        font_title,
                        font_small,
                        "Band Limit",
                        [bp[0] for bp in BAND_PRESETS],
                        band_menu_selected,
                    )
                elif sound_menu_open:
                    display.draw_simple_menu(
                        screen,
                        font_title,
                        font_small,
                        "Sound",
                        [
                            f"Mute: {'ON' if sound_muted else 'OFF'}",
                            f"Volume: {sound_volume}",
                            "Back",
                        ],
                        sound_menu_selected,
                    )
                elif wifi_menu_open:
                    display.draw_simple_menu(
                        screen,
                        font_title,
                        font_small,
                        "Wi-Fi",
                        ["Wi-Fi: ON", "Wi-Fi: OFF", "Cancel"],
                        wifi_menu_selected,
                    )
                else:
                    display.draw_settings_screen(
                        screen,
                        font_title,
                        font_small,
                        settings_selected,
                        settings_center_freq_hz,
                        settings_sample_rate_index,
                        settings_gain_index,
                        settings_min_db,
                        settings_max_db,
                        settings_wf_speed_index,
                        settings_spectrum_speed_index,
                        settings_peak_marker_count,
                        settings_wf_avg_index,
                        settings_brightness_index,
                        settings_band_index,
                        settings_squelch_level,
                        settings_filter_median,
                        settings_filter_temporal_avg,
                        settings_filter_noise_floor,
                        settings_filter_peak_hold,
                        settings_filter_center_notch,
                        settings_filter_adaptive_threshold,
                        settings_filter_freq_smoothing,
                        settings_filter_impulse_blanking,
                        settings_filter_display_clamp,
                        sdr_backend.SAMPLE_RATE_OPTIONS,
                        sdr_backend.GAIN_OPTIONS,
                        WF_SPEED_OPTIONS,
                        SPECTRUM_SPEED_OPTIONS,
                        WF_AVG_OPTIONS,
                        BRIGHTNESS_OPTIONS,
                        BAND_PRESETS,
                    )

                    if keypad_open:
                        display.draw_keypad_overlay(screen, font_title, font_small, font_keypad, keypad_value)

            if favorite_toast_text and time.time() < favorite_toast_until:
                draw_toast(screen, font_small, favorite_toast_text)
            elif favorite_toast_text and time.time() >= favorite_toast_until:
                favorite_toast_text = ""

            pygame.display.flip()

            if settings_open:
                display.present_to_lcd(lcd, screen, push_top=True, push_mid=True, push_bot=True)
                mid_dirty = False
            else:
                if was_settings_open and not settings_open:
                    bot_full_dirty = True

                if bot_full_dirty:
                    display.present_to_lcd(lcd, screen, push_top=True, push_mid=True, push_bot=True)
                    bot_full_dirty = False
                    mid_dirty = False
                elif have_fresh_bins:
                    display.present_to_lcd(lcd, screen, push_top=True, push_mid=mid_dirty, push_bot=True)
                    mid_dirty = False
                elif mid_dirty:
                    display.present_to_lcd(lcd, screen, push_top=False, push_mid=True, push_bot=False)
                    mid_dirty = False

            clock.tick(60)

    finally:
        stop_event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)
        if temp_sdr is not None:
            try:
                temp_sdr.close()
            except Exception:
                pass
        pygame.quit()


if __name__ == "__main__":
    main()
