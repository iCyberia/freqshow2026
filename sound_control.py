import re
import shutil
import subprocess


class SoundControl:
    def __init__(self):
        self.backend = self._detect_backend()

    def _detect_backend(self):
        if shutil.which("amixer"):
            return "amixer"
        if shutil.which("wpctl"):
            return "wpctl"
        return None

    def available(self):
        return self.backend is not None

    def _run(self, cmd):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            return ((e.stdout or "") + (e.stderr or "")).strip()

    def get_state(self):
        if self.backend == "amixer":
            out = self._run(["amixer", "get", "Master"])
            vol_match = re.findall(r"\[(\d{1,3})%\]", out)
            mute_match = re.findall(r"\[(on|off)\]", out)
            volume = int(vol_match[-1]) if vol_match else 50
            muted = (mute_match[-1] == "off") if mute_match else False
            return max(0, min(99, volume)), muted

        if self.backend == "wpctl":
            out = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            vol_match = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", out)
            muted = "[MUTED]" in out
            volume = int(round(float(vol_match.group(1)) * 100)) if vol_match else 50
            return max(0, min(99, volume)), muted

        return 50, False

    def set_volume(self, volume):
        volume = max(0, min(99, int(volume)))

        if self.backend == "amixer":
            self._run(["amixer", "set", "Master", f"{volume}%"])
            return True

        if self.backend == "wpctl":
            self._run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume / 100:.2f}"])
            return True

        return False

    def set_mute(self, mute):
        if self.backend == "amixer":
            self._run(["amixer", "set", "Master", "mute" if mute else "unmute"])
            return True

        if self.backend == "wpctl":
            self._run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"])
            return True

        return False
