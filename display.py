#!/usr/bin/env python3
import numpy as np
import pygame
from PIL import Image
import math

WIDTH = 800
HEIGHT = 480

MID_H = 36
TOP_H = (HEIGHT - MID_H) // 2
BOT_H = HEIGHT - TOP_H - MID_H

BG_TOP = (10, 16, 44)
BG_MID = (0, 0, 0)
BG_BOT = (0, 0, 0)
GREEN = (80, 255, 120)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRID = (24, 34, 70)
ACCENT = (60, 60, 60)

CENTER_LINE = (255, 0, 0)

GEAR_RECT = pygame.Rect(10, HEIGHT - 55, 56, 56)
GEAR_HIT_RECT = pygame.Rect(GEAR_RECT.x - 20, GEAR_RECT.y - 20, 120, 120)


def pil_to_rgb565_bytes(img: Image.Image) -> bytes:
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    r = arr[:, :, 0].astype(np.uint16)
    g = arr[:, :, 1].astype(np.uint16)
    b = arr[:, :, 2].astype(np.uint16)
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb565.byteswap().tobytes()


def lcd_write_bytes(lcd, data: bytes) -> None:
    lcd.digital_write(lcd.GPIO_DC_PIN, True)
    mv = memoryview(data)
    for i in range(0, len(data), 4096):
        lcd.spi_writebyte(mv[i:i + 4096].tolist())


def lcd_set_window(lcd, x0: int, y0: int, x1: int, y1: int) -> None:
    lcd.command(0x2A)
    lcd.data(x0 >> 8)
    lcd.data(x0 & 0xFF)
    lcd.data(x1 >> 8)
    lcd.data(x1 & 0xFF)

    lcd.command(0x2B)
    lcd.data(y0 >> 8)
    lcd.data(y0 & 0xFF)
    lcd.data(y1 >> 8)
    lcd.data(y1 & 0xFF)

    lcd.command(0x2C)


def surface_rect_to_rgb565_bytes(surface: pygame.Surface, rect: pygame.Rect) -> bytes:
    sub = surface.subsurface(rect).copy()
    raw = pygame.image.tostring(sub, "RGB")
    img = Image.frombytes("RGB", rect.size, raw)
    img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return pil_to_rgb565_bytes(img)


def lcd_push_rect(lcd, surface: pygame.Surface, rect: pygame.Rect) -> None:
    if rect.width <= 0 or rect.height <= 0:
        return
    pix = surface_rect_to_rgb565_bytes(surface, rect)
    x0 = WIDTH - rect.right
    x1 = WIDTH - rect.left - 1
    lcd_set_window(lcd, x0, rect.top, x1, rect.bottom - 1)
    lcd_write_bytes(lcd, pix)


def present_to_lcd(lcd, surface: pygame.Surface, push_top=True, push_mid=True, push_bot=True) -> None:
    if lcd is None:
        pygame.display.flip()
        return

    lcd.command(0x36)
    lcd.data(0x78)

    if push_top:
        lcd_push_rect(lcd, surface, pygame.Rect(0, 0, WIDTH, TOP_H))
    if push_mid:
        lcd_push_rect(lcd, surface, pygame.Rect(0, TOP_H, WIDTH, MID_H))
    if push_bot:
        lcd_push_rect(lcd, surface, pygame.Rect(0, TOP_H + MID_H, WIDTH, BOT_H))


def apply_brightness(lcd, percent: int) -> None:
    percent = max(20, min(100, int(percent)))

    if lcd is not None:
        try:
            lcd.bl_DutyCycle(percent)
            print(f"Brightness set to {percent}% (SPI LCD)", flush=True)
            return
        except Exception as e:
            print(f"SPI brightness change failed: {e}", flush=True)

    try:
        from pathlib import Path
        dev = Path("/sys/class/backlight/0-0045")
        max_brightness = int((dev / "max_brightness").read_text().strip())
        min_value = max(1, round(max_brightness * 0.20))
        scale = (percent - 20) / 80.0
        value = round(min_value + scale * (max_brightness - min_value))
        value = max(min_value, min(max_brightness, value))
        (dev / "brightness").write_text(str(value))
        print(f"Brightness set to {percent}% ({value}/{max_brightness}) via {dev.name}", flush=True)
    except Exception as e:
        print(f"Brightness change failed: {e}", flush=True)


def color_from_power(v: float):
    v = max(0.0, min(1.0, v))
    if v < 0.20:
        return (0, 0, int(40 + v * 150))
    if v < 0.45:
        t = (v - 0.20) / 0.25
        return (0, int(80 * t), int(160 + 60 * t))
    if v < 0.70:
        t = (v - 0.45) / 0.25
        return (int(40 * t), int(120 + 80 * t), int(220 - 120 * t))
    if v < 0.88:
        t = (v - 0.70) / 0.18
        return (int(120 + 100 * t), int(200 + 40 * t), int(80 - 40 * t))
    t = (v - 0.88) / 0.12
    return (255, int(240 - 120 * t), int(40 - 40 * t))


def lerp_color(a, b, t):
    return (
        int(a[0] * (1.0 - t) + b[0] * t),
        int(a[1] * (1.0 - t) + b[1] * t),
        int(a[2] * (1.0 - t) + b[2] * t),
    )


def draw_text_outline(surf, font, text, x, y, fg, outline):
    for ox, oy in [(-2, -2), (-2, 0), (-2, 2), (0, -2), (0, 2), (2, -2), (2, 0), (2, 2)]:
        img = font.render(text, True, outline)
        surf.blit(img, (x + ox, y + oy))
    img = font.render(text, True, fg)
    surf.blit(img, (x, y))


def draw_gear_icon(surf: pygame.Surface, rect: pygame.Rect, color):
    left = rect.x + 10
    right = rect.right - 10

    y1 = rect.y + 14
    y2 = rect.y + 24
    y3 = rect.y + 34

    pygame.draw.line(surf, color, (left, y1), (right, y1), 4)
    pygame.draw.line(surf, color, (left, y2), (right, y2), 4)
    pygame.draw.line(surf, color, (left, y3), (right, y3), 4)


def draw_wifi_icon(surf: pygame.Surface, x: int, y: int, color):
    # Small upright wifi icon for the tiny display
    pygame.draw.circle(surf, color, (x, y + 6), 1)

    pygame.draw.arc(surf, color, pygame.Rect(x - 4, y + 1, 8, 8), 0.79, 2.36, 1)
    pygame.draw.arc(surf, color, pygame.Rect(x - 7, y - 2, 14, 14), 0.79, 2.36, 1)
    pygame.draw.arc(surf, color, pygame.Rect(x - 10, y - 5, 20, 20), 0.79, 2.36, 1)


def build_top_static_surface() -> pygame.Surface:
    surf = pygame.Surface((WIDTH, TOP_H))
    surf.fill(BG_TOP)

    for y in range(16, TOP_H, 20):
        pygame.draw.line(surf, GRID, (0, y), (WIDTH, y), 1)
    for x in range(0, WIDTH, 64):
        pygame.draw.line(surf, GRID, (x, 0), (x, TOP_H), 1)

    pygame.draw.line(surf, (180, 180, 180), (WIDTH // 2, 0), (WIDTH // 2, TOP_H - 1), 1)
    pygame.draw.line(surf, CENTER_LINE, (WIDTH // 2, 0), (WIDTH // 2, TOP_H - 1), 1)
    return surf


def render_top_surface(top_static: pygame.Surface, font_big, font_small, bins, center_freq_hz, sample_rate_hz, peak_label_x=None, peak_marker_count=1) -> pygame.Surface:
    surf = top_static.copy()

    pts = []
    for x, v in enumerate(bins):
        y = int((TOP_H - 8) - (v * (TOP_H - 18)))
        pts.append((x, y))

    if len(pts) > 1:
        # Build polygon under the trace down to the bottom of the top pane.
        poly = [(pts[0][0], TOP_H - 1)] + pts + [(pts[-1][0], TOP_H - 1)]

        # Gradient surface (only for the top pane)
        grad = pygame.Surface((WIDTH, TOP_H), pygame.SRCALPHA)
        c1 = (40, 110, 210, 255)  # deeper light blue
        c2 = (5, 20, 90, 255)     # deeper dark blue

        trace_top = min(y for _, y in pts)
        grad_top = max(0, trace_top)
        grad_bottom = TOP_H - 1

        for y in range(grad_top, grad_bottom + 1):
            tt = (y - grad_top) / max(1, (grad_bottom - grad_top))
            color = (
                int(c1[0] * (1.0 - tt) + c2[0] * tt),
                int(c1[1] * (1.0 - tt) + c2[1] * tt),
                int(c1[2] * (1.0 - tt) + c2[2] * tt),
                255,
            )
            pygame.draw.line(grad, color, (0, y), (WIDTH, y), 1)

        # Mask surface: only keep gradient inside polygon under the trace
        mask = pygame.Surface((WIDTH, TOP_H), pygame.SRCALPHA)
        pygame.draw.polygon(mask, (255, 255, 255, 255), poly)
        grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # Blit the masked gradient under the line
        surf.blit(grad, (0, 0))

        # Draw the spectrum line on top
        pygame.draw.lines(surf, WHITE, False, pts, 1)

        # Peak markers: draw the strongest local peaks, but keep the label on the strongest one.
        candidate_peaks = []
        min_separation = max(8, WIDTH // 40)

        for i in range(1, len(pts) - 1):
            x, y = pts[i]
            prev_y = pts[i - 1][1]
            next_y = pts[i + 1][1]

            # local maximum in signal terms = lower y than neighbors
            if y <= prev_y and y <= next_y:
                strength = TOP_H - y
                candidate_peaks.append((strength, x, y))

        # fallback if no local maxima found
        if not candidate_peaks and pts:
            x, y = max(pts, key=lambda p: (TOP_H - p[1], -abs(p[0] - (WIDTH // 2))))
            candidate_peaks.append((TOP_H - y, x, y))

        # sort strongest first, prefer closer to center on ties
        candidate_peaks.sort(key=lambda item: (-item[0], abs(item[1] - (WIDTH // 2))))

        selected_peaks = []
        for strength, x, y in candidate_peaks:
            if all(abs(x - sx) >= min_separation for _, sx, sy in selected_peaks):
                selected_peaks.append((strength, x, y))
            if len(selected_peaks) >= max(0, int(peak_marker_count)):
                break

        # draw markers for all selected peaks
        if peak_marker_count > 0:
            for idx, (strength, peak_x, peak_y) in enumerate(selected_peaks):
                marker_y = max(10, peak_y - 8)
                marker = [
                    (peak_x, marker_y),
                    (peak_x - 4, marker_y - 6),
                    (peak_x + 4, marker_y - 6),
                ]
                pygame.draw.polygon(surf, (255, 220, 80), marker)
                pygame.draw.polygon(surf, BLACK, marker, 1)

        # Label only the strongest peak
        if peak_marker_count > 0 and selected_peaks:
            _strength, strongest_x, strongest_y = selected_peaks[0]

            hz_per_pixel = sample_rate_hz / max(1, (WIDTH - 1))
            left_freq_hz = center_freq_hz - (sample_rate_hz / 2.0)

            label_x = strongest_x if peak_label_x is None else int(round(peak_label_x))
            label_x = max(0, min(WIDTH - 1, label_x))

            peak_freq_hz = left_freq_hz + (label_x * hz_per_pixel)

            peak_text = f"{peak_freq_hz / 1_000_000:.3f}"
            peak_img = font_small.render(peak_text, True, BLACK)

            pad_x = 6
            pad_y = 3
            box_w = peak_img.get_width() + (pad_x * 2)
            box_h = peak_img.get_height() + (pad_y * 2)

            box_x = label_x + 8
            if box_x + box_w > WIDTH - 4:
                box_x = label_x - box_w - 8
            if box_x < 4:
                box_x = 4

            box_y = 6
            box_rect = pygame.Rect(box_x, box_y, box_w, box_h)

            pygame.draw.rect(surf, (255, 220, 80), box_rect, border_radius=6)
            pygame.draw.rect(surf, BLACK, box_rect, 1, border_radius=6)
            surf.blit(peak_img, (box_x + pad_x, box_y + pad_y))

    # Redraw center line last so it stays above the fill
    pygame.draw.line(surf, (180, 180, 180), (WIDTH // 2, 0), (WIDTH // 2, TOP_H - 1), 1)
    pygame.draw.line(surf, CENTER_LINE, (WIDTH // 2, 0), (WIDTH // 2, TOP_H - 1), 1)

    draw_text_outline(surf, font_big, f"{center_freq_hz / 1_000_000:.3f} MHz", 10, 8, WHITE, BLACK)
    return surf


def build_mid_static_surface() -> pygame.Surface:
    surf = pygame.Surface((WIDTH, MID_H))
    surf.fill(BG_MID)
    pygame.draw.line(surf, ACCENT, (0, 0), (WIDTH, 0), 1)
    pygame.draw.line(surf, ACCENT, (0, MID_H - 1), (WIDTH, MID_H - 1), 1)
    return surf


def render_mid_surface(mid_static: pygame.Surface, font_mid, gain_text, step_text, sample_rate_hz) -> pygame.Surface:
    surf = mid_static.copy()
    gain_label = font_mid.render(f"Gain {gain_text}", True, WHITE)
    step_label = font_mid.render(f"Step {step_text}", True, WHITE)
    sr_label = font_mid.render(f"SR {sample_rate_hz / 1_000_000:.3f}M", True, WHITE)
    y = (MID_H - gain_label.get_height()) // 2
    surf.blit(gain_label, (20, y))
    surf.blit(step_label, ((WIDTH - step_label.get_width()) // 2, y))
    surf.blit(sr_label, (WIDTH - sr_label.get_width() - 20, y))
    return surf


def settings_items():
    return [
        "band_limit",
        "repeaters",
        "favorites",
        "squelch",
        "filters",
        "center_freq",
        "sample_rate",
        "gain",
        "min_db",
        "max_db",
        "wf_speed",
        "spectrum_speed",
        "peak_markers",
        "wf_average",
        "brightness",
        "wifi",
        "restart",
        "quit",
    ]


def filter_settings_items():
    return [
        "filter_median",
        "filter_temporal_avg",
        "filter_noise_floor",
        "filter_peak_hold",
        "filter_center_notch",
        "filter_adaptive_threshold",
        "filter_freq_smoothing",
        "filter_impulse_blanking",
        "filter_display_clamp",
    ]


def setting_label(item):
    labels = {
        "band_limit": "Band Limit",
        "repeaters": "Repeaters",
        "favorites": "Favorites",
        "squelch": "Squelch",
        "filters": "Filters",
        "center_freq": "Center Freq",
        "sample_rate": "Sample Rate",
        "gain": "Gain",
        "min_db": "Min",
        "max_db": "Max",
        "wf_speed": "WF Speed",
        "spectrum_speed": "Spectrum Speed",
        "peak_markers": "Peak Markers",
        "wf_average": "WF Average",
        "brightness": "Brightness",
        "wifi": "Wi-Fi",
        "filter_median": "Median Filter (SLOW)",
        "filter_temporal_avg": "Temporal Avg",
        "filter_noise_floor": "Noise Floor Sub (SLOW)",
        "filter_peak_hold": "Peak Hold",
        "filter_center_notch": "Center Notch",
        "filter_adaptive_threshold": "Adaptive Threshold (SLOW)",
        "filter_freq_smoothing": "Freq Smoothing",
        "filter_impulse_blanking": "Impulse Blanking",
        "filter_display_clamp": "Display Clamp",
        "restart": "Restart",
        "quit": "Quit",
    }
    return labels.get(item, item)


def setting_value_text(
    item,
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
    band_index,
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
    sample_rate_options,
    gain_options,
    wf_speed_options,
    spectrum_speed_options,
    wf_avg_options,
    brightness_options,
    band_presets,
):
    if item == "band_limit":
        return str(band_presets[band_index][0])
    if item == "repeaters":
        return ""
    if item == "favorites":
        return ""
    if item == "filters":
        return ""
    if item == "center_freq":
        return f"{settings_center_freq_hz / 1_000_000:.3f} MHz"
    if item == "sample_rate":
        return f"{sample_rate_options[sample_rate_index] / 1_000_000:.3f} MHz"
    if item == "gain":
        return f"{gain_options[gain_index]}"
    if item == "min_db":
        return f"{settings_min_db:.0f} dB"
    if item == "max_db":
        return f"{settings_max_db:.0f} dB"
    if item == "wf_speed":
        return f"{wf_speed_options[wf_speed_index]}"
    if item == "spectrum_speed":
        return f"{spectrum_speed_options[spectrum_speed_index]}"
    if item == "peak_markers":
        return f"{peak_marker_count}"
    if item == "wf_average":
        return f"{wf_avg_options[wf_avg_index]:.2f}"
    if item == "brightness":
        return f"{brightness_options[brightness_index]}%"
    if item == "wifi":
        return ""
    if item == "squelch":
        return f"{squelch_level}"
    if item == "filter_median":
        return "ON" if filter_median else "OFF"
    if item == "filter_temporal_avg":
        return "ON" if filter_temporal_avg else "OFF"
    if item == "filter_noise_floor":
        return "ON" if filter_noise_floor else "OFF"
    if item == "filter_peak_hold":
        return "ON" if filter_peak_hold else "OFF"
    if item == "filter_center_notch":
        return "ON" if filter_center_notch else "OFF"
    if item == "filter_adaptive_threshold":
        return "ON" if filter_adaptive_threshold else "OFF"
    if item == "filter_freq_smoothing":
        return "ON" if filter_freq_smoothing else "OFF"
    if item == "filter_impulse_blanking":
        return "ON" if filter_impulse_blanking else "OFF"
    if item == "filter_display_clamp":
        return "ON" if filter_display_clamp else "OFF"
    if item == "quit":
        return ""
    return ""




def build_settings_layout(selected_index=0):
    items = settings_items()
    row_top = 52
    row_h = 38
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    rows = []
    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)
        rows.append({"item": item, "index": actual_idx, "rect": row_rect})

    return {"items": items, "row_top": row_top, "row_h": row_h, "footer_h": footer_h, "visible_rows": visible_rows, "scroll_offset": scroll_offset, "rows": rows}


def draw_settings_screen(
    screen,
    font_title,
    font_item,
    selected_index,
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
    band_index,
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
    sample_rate_options,
    gain_options,
    wf_speed_options,
    spectrum_speed_options,
    wf_avg_options,
    brightness_options,
    band_presets,
):
    screen.fill(BLACK)

    title = font_title.render("Settings", True, WHITE)
    screen.blit(title, (16, 12))

    items = settings_items()
    row_top = 52
    row_h = 38
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        if actual_idx == selected_index:
            bg = (40, 40, 40) if item != "quit" else (70, 20, 20)
        else:
            bg = BLACK

        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        label = font_item.render(setting_label(item), True, WHITE)
        screen.blit(label, (14, y + (row_h - label.get_height()) // 2))

        value_text = setting_value_text(
            item,
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
            band_index,
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
            sample_rate_options,
            gain_options,
            wf_speed_options,
            spectrum_speed_options,
            wf_avg_options,
            brightness_options,
            band_presets,
        )
        if value_text:
            value = font_item.render(value_text, True, WHITE)
            screen.blit(value, (WIDTH - value.get_width() - 14, y + (row_h - value.get_height()) // 2))

    bottom_y = row_top + visible_rows * row_h
    pygame.draw.line(screen, (60, 60, 60), (0, bottom_y), (WIDTH, bottom_y), 1)

    

def draw_filter_settings_screen(
    screen,
    font_title,
    font_item,
    selected_index,
    filter_median,
    filter_temporal_avg,
    filter_noise_floor,
    filter_peak_hold,
    filter_center_notch,
    filter_adaptive_threshold,
    filter_freq_smoothing,
    filter_impulse_blanking,
    filter_display_clamp,
):
    screen.fill(BLACK)

    title = font_title.render("Filters", True, WHITE)
    screen.blit(title, (16, 12))

    items = filter_settings_items()
    row_top = 52
    row_h = 38
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        bg = (40, 40, 40) if actual_idx == selected_index else BLACK
        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        label = font_item.render(setting_label(item), True, WHITE)
        screen.blit(label, (14, y + (row_h - label.get_height()) // 2))

        value_text = "OFF"
        if item == "filter_median":
            value_text = "ON" if filter_median else "OFF"
        elif item == "filter_temporal_avg":
            value_text = "ON" if filter_temporal_avg else "OFF"
        elif item == "filter_noise_floor":
            value_text = "ON" if filter_noise_floor else "OFF"
        elif item == "filter_peak_hold":
            value_text = "ON" if filter_peak_hold else "OFF"
        elif item == "filter_center_notch":
            value_text = "ON" if filter_center_notch else "OFF"
        elif item == "filter_adaptive_threshold":
            value_text = "ON" if filter_adaptive_threshold else "OFF"
        elif item == "filter_freq_smoothing":
            value_text = "ON" if filter_freq_smoothing else "OFF"
        elif item == "filter_impulse_blanking":
            value_text = "ON" if filter_impulse_blanking else "OFF"
        elif item == "filter_display_clamp":
            value_text = "ON" if filter_display_clamp else "OFF"

        value = font_item.render(value_text, True, WHITE)
        screen.blit(value, (WIDTH - value.get_width() - 14, y + (row_h - value.get_height()) // 2))

    bottom_y = row_top + visible_rows * row_h
    pygame.draw.line(screen, (60, 60, 60), (0, bottom_y), (WIDTH, bottom_y), 1)

    help_text = font_item.render("Enc1 scroll  Enc2 adjust/back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))




def build_band_menu_layout(selected_index, band_presets):
    row_top = 58
    row_h = 46
    footer_h = 34
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    rows = []
    visible_items = band_presets[scroll_offset:scroll_offset + visible_rows]
    for i, band in enumerate(visible_items):
        actual_idx = scroll_offset + i
        y = row_top + i * row_h
        rows.append({
            "index": actual_idx,
            "rect": pygame.Rect(0, y, WIDTH, row_h),
            "label": str(band[0]),
        })

    return {"rows": rows}


def draw_band_menu_screen(screen, font_title, font_small, selected_index, band_presets):
    screen.fill(BLACK)

    title = font_title.render("Band Limit", True, WHITE)
    screen.blit(title, (16, 12))

    row_top = 58
    row_h = 46
    footer_h = 34
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = band_presets[scroll_offset:scroll_offset + visible_rows]

    for i, band in enumerate(visible_items):
        actual_idx = scroll_offset + i
        y = row_top + i * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        if actual_idx == selected_index:
            pygame.draw.rect(screen, (35, 55, 110), row_rect)

        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        label = font_small.render(str(band[0]), True, WHITE)
        screen.blit(label, (16, y + (row_h - label.get_height()) // 2))

    bottom_y = row_top + len(visible_items) * row_h
    pygame.draw.line(screen, (60, 60, 60), (0, bottom_y), (WIDTH, bottom_y), 1)

    help_text = font_small.render("Press/tap to select • Back to cancel", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))

def draw_favorites_screen(screen, font_title, font_item, selected_index, favorites):
    screen.fill(BLACK)

    title = font_title.render("Favorites", True, WHITE)
    screen.blit(title, (16, 12))

    row_top = 52
    row_h = 38
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    items = favorites if favorites else [{"name": "(No favorites)", "freq_hz": None}]

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        bg = (40, 40, 40) if actual_idx == selected_index else BLACK
        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        label_text = item.get("name", "Unknown")
        label = font_item.render(label_text, True, WHITE)
        screen.blit(label, (14, y + (row_h - label.get_height()) // 2))

    bottom_y = row_top + visible_rows * row_h
    pygame.draw.line(screen, (60, 60, 60), (0, bottom_y), (WIDTH, bottom_y), 1)

    help_text = font_item.render("Enc1 scroll/select  Enc2 press=back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))


def draw_repeater_bands_screen(screen, font_title, font_item, selected_index, bands):
    screen.fill(BLACK)

    title = font_title.render("Repeater Bands", True, WHITE)
    screen.blit(title, (16, 12))

    row_top = 52
    row_h = 38
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    items = bands if bands else [{"label": "(No bands)"}]

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        bg = (40, 40, 40) if actual_idx == selected_index else BLACK
        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        label_text = str(item.get("label", "Unknown"))
        label = font_item.render(label_text, True, WHITE)
        screen.blit(label, (14, y + (row_h - label.get_height()) // 2))

    help_text = font_item.render("Enc1 scroll/select  Enc2 press=back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))


def draw_repeaters_screen(screen, font_title, font_item, font_small, selected_index, repeaters):
    screen.fill(BLACK)

    title = font_title.render("Repeaters", True, WHITE)
    screen.blit(title, (16, 8))

    header_y = 40
    header = font_small.render("Freq   Off  Access  Location", True, WHITE)
    screen.blit(header, (10, header_y))

    row_top = 62
    row_h = 30
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    items = repeaters if repeaters else [{
        "freq_mhz": None,
        "offset": "",
        "access": "",
        "location": "(No repeaters)",
        "status": "Unknown"
    }]

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        bg = (40, 40, 40) if actual_idx == selected_index else BLACK
        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        freq_text = "" if item.get("freq_mhz") is None else f'{float(item["freq_mhz"]):.4f}'
        off_text = str(item.get("offset", ""))
        access_text = str(item.get("access", ""))
        location_text = str(item.get("location", ""))

        freq_img = font_small.render(freq_text, True, WHITE)
        off_img = font_small.render(off_text, True, WHITE)
        access_img = font_small.render(access_text, True, WHITE)
        loc_img = font_small.render(location_text, True, WHITE)

        screen.blit(freq_img, (8, y + 5))
        screen.blit(off_img, (112, y + 5))
        screen.blit(access_img, (150, y + 5))
        screen.blit(loc_img, (232, y + 5))

        status = str(item.get("status", "Unknown"))
        status_color = (0, 220, 0) if status == "Operational" else (220, 0, 0)
        pygame.draw.circle(screen, status_color, (468, y + 15), 6)

    help_text = font_item.render("Enc1 scroll/select  Enc2 press=back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))


def draw_repeater_bands_screen(screen, font_title, font_item, selected_index, bands):
    screen.fill(BLACK)

    title = font_title.render("Repeater Bands", True, WHITE)
    screen.blit(title, (16, 12))

    row_top = 52
    row_h = 38
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    items = bands if bands else [{"label": "(No bands)"}]

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        bg = (40, 40, 40) if actual_idx == selected_index else BLACK
        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        label_text = str(item.get("label", "Unknown"))
        label = font_item.render(label_text, True, WHITE)
        screen.blit(label, (14, y + (row_h - label.get_height()) // 2))

    help_text = font_item.render("Enc1 scroll/select  Enc2 press=back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))


def draw_repeaters_screen(screen, font_title, font_item, font_small, selected_index, repeaters):
    screen.fill(BLACK)

    title = font_title.render("Repeaters", True, WHITE)
    screen.blit(title, (16, 8))

    header_y = 40
    header = font_small.render("Freq     Off  Access   Location", True, WHITE)
    screen.blit(header, (8, header_y))

    row_top = 62
    row_h = 30
    footer_h = 28
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    items = repeaters if repeaters else [{
        "freq_mhz": None,
        "offset": "",
        "access": "",
        "location": "(No repeaters)",
        "status": "Unknown"
    }]

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(0, y, WIDTH, row_h)

        bg = (40, 40, 40) if actual_idx == selected_index else BLACK
        pygame.draw.rect(screen, bg, row_rect)
        pygame.draw.line(screen, (60, 60, 60), (0, y), (WIDTH, y), 1)

        freq_text = "" if item.get("freq_mhz") is None else f'{float(item["freq_mhz"]):.4f}'
        off_text = str(item.get("offset", ""))
        access_text = str(item.get("access", ""))
        location_text = str(item.get("location", ""))

        freq_img = font_small.render(freq_text, True, WHITE)
        off_img = font_small.render(off_text, True, WHITE)
        access_img = font_small.render(access_text, True, WHITE)
        loc_img = font_small.render(location_text, True, WHITE)

        screen.blit(freq_img, (8, y + 5))
        screen.blit(off_img, (110, y + 5))
        screen.blit(access_img, (146, y + 5))
        screen.blit(loc_img, (238, y + 5))

        status = str(item.get("status", "Unknown"))
        status_color = (0, 220, 0) if status == "Operational" else (220, 0, 0)
        pygame.draw.circle(screen, status_color, (468, y + 15), 6)

    help_text = font_item.render("Enc1 scroll/select  Enc2 press=back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))


def draw_simple_menu(screen, font_title, font_item, title_text, items, selected_index):
    screen.fill(BLACK)

    title = font_title.render(title_text, True, WHITE)
    screen.blit(title, (16, 12))

    row_top = 64
    row_h = 44
    footer_h = 34
    visible_rows = max(1, (HEIGHT - row_top - footer_h) // row_h)

    scroll_offset = 0
    if selected_index >= visible_rows:
        scroll_offset = selected_index - visible_rows + 1

    visible_items = items[scroll_offset:scroll_offset + visible_rows]

    for visible_idx, item in enumerate(visible_items):
        actual_idx = scroll_offset + visible_idx
        y = row_top + visible_idx * row_h
        row_rect = pygame.Rect(16, y, WIDTH - 32, row_h - 4)

        if actual_idx == selected_index:
            pygame.draw.rect(screen, (40, 40, 40), row_rect, border_radius=8)
            pygame.draw.rect(screen, (120, 120, 120), row_rect, 2, border_radius=8)
        else:
            pygame.draw.rect(screen, (18, 18, 18), row_rect, border_radius=8)
            pygame.draw.rect(screen, (70, 70, 70), row_rect, 1, border_radius=8)

        label = font_item.render(str(item), True, WHITE)
        screen.blit(
            label,
            (row_rect.x + 14, row_rect.y + (row_rect.height - label.get_height()) // 2),
        )

    help_text = font_item.render("Enc1 scroll/select  Enc2 press=back", True, WHITE)
    screen.blit(help_text, (14, HEIGHT - help_text.get_height() - 6))

def build_keypad_layout():
    modal_rect = pygame.Rect(0, 0, WIDTH, HEIGHT)

    margin = 16
    gap = 12
    side_btn_w = 150
    entry_h = 64

    title_rect = pygame.Rect(0, 0, WIDTH, 0)

    entry_rect = pygame.Rect(
        margin,
        18,
        WIDTH - (margin * 2) - (side_btn_w * 2) - (gap * 2),
        entry_h,
    )

    clear_rect = pygame.Rect(entry_rect.right + gap, entry_rect.top, side_btn_w, entry_h)
    cancel_rect = pygame.Rect(clear_rect.right + gap, entry_rect.top, side_btn_w, entry_h)

    grid_top = entry_rect.bottom + 18
    grid_bottom = HEIGHT - 18
    grid_left = margin
    grid_right = WIDTH - margin

    cols = 3
    rows = 4

    total_w = grid_right - grid_left
    total_h = grid_bottom - grid_top

    btn_w = (total_w - (gap * (cols - 1))) // cols
    btn_h = (total_h - (gap * (rows - 1))) // rows

    labels = [
        ["1", "2", "3"],
        ["4", "5", "6"],
        ["7", "8", "9"],
        [".", "0", "OK"],
    ]

    buttons = {}
    for r, row in enumerate(labels):
        for c, label in enumerate(row):
            x = grid_left + c * (btn_w + gap)
            y = grid_top + r * (btn_h + gap)
            buttons[label] = pygame.Rect(x, y, btn_w, btn_h)

    return {
        "modal_rect": modal_rect,
        "title_rect": title_rect,
        "entry_rect": entry_rect,
        "clear_rect": clear_rect,
        "cancel_rect": cancel_rect,
        "buttons": buttons,
    }


def draw_keypad_overlay(screen, font_title, font_item, font_value, entry_value):
    layout = build_keypad_layout()
    title_rect = layout["title_rect"]
    entry_rect = layout["entry_rect"]
    clear_rect = layout["clear_rect"]
    cancel_rect = layout["cancel_rect"]
    buttons = layout["buttons"]

    screen.fill((8, 8, 8))

    pygame.draw.rect(screen, (0, 0, 0), entry_rect, border_radius=8)
    pygame.draw.rect(screen, (130, 130, 130), entry_rect, 2, border_radius=8)

    display_text = f"{entry_value} MHz" if entry_value else " MHz"
    value_img = font_value.render(display_text, True, WHITE)
    screen.blit(
        value_img,
        (entry_rect.x + 10, entry_rect.y + (entry_rect.height - value_img.get_height()) // 2),
    )

    pygame.draw.rect(screen, (55, 20, 20), clear_rect, border_radius=10)
    pygame.draw.rect(screen, (140, 80, 80), clear_rect, 2, border_radius=10)
    clear_img = font_item.render("Clear", True, WHITE)
    screen.blit(
        clear_img,
        (clear_rect.x + (clear_rect.width - clear_img.get_width()) // 2,
         clear_rect.y + (clear_rect.height - clear_img.get_height()) // 2),
    )

    pygame.draw.rect(screen, (35, 35, 35), cancel_rect, border_radius=10)
    pygame.draw.rect(screen, (140, 140, 140), cancel_rect, 2, border_radius=10)
    cancel_img = font_item.render("Cancel", True, WHITE)
    screen.blit(
        cancel_img,
        (cancel_rect.x + (cancel_rect.width - cancel_img.get_width()) // 2,
         cancel_rect.y + (cancel_rect.height - cancel_img.get_height()) // 2),
    )

    for label, rect in buttons.items():
        fill = (28, 28, 28)
        border = (130, 130, 130)

        if label == "OK":
            fill = (20, 55, 20)
            border = (90, 150, 90)

        pygame.draw.rect(screen, fill, rect, border_radius=10)
        pygame.draw.rect(screen, border, rect, 2, border_radius=10)

        txt = font_value.render(label, True, WHITE)
        screen.blit(
            txt,
            (rect.x + (rect.width - txt.get_width()) // 2,
             rect.y + (rect.height - txt.get_height()) // 2),
        )

    return layout


def format_step_mhz(step_hz: int) -> str:
    if step_hz == 1_000:
        return "0.001 MHz"
    if step_hz == 10_000:
        return "0.010 MHz"
    if step_hz == 100_000:
        return "0.100 MHz"
    if step_hz == 1_000_000:
        return "1.000 MHz"
    if step_hz == 10_000_000:
        return "10.00 MHz"
    if step_hz == 100_000_000:
        return "100.0 MHz"
    return f"{step_hz / 1_000_000:.3f} MHz"
