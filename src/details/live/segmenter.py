"""LiveSegmenter: cuts a stream of int16 audio into speech lines. A pure port of v1 `live_loop` (api/live_v1.py):
30 ms RMS frames against `silence_db`; a line is committed when its tail is quiet for 0.5 s or it is over 15 s (cut at
the quietest 100 ms of the last 4 s); pure silence is dropped; a partial is due about every 0.8 s of new speech.
No asyncio, no model: `feed` is synchronous and cheap. Times come from the writer's frame counter (`start_frame`), so
they match the saved file even when live dropped chunks."""
from dataclasses import dataclass

import numpy as np

CHECK_S = 0.25        # v1 looked every 0.25 s
FRAME = 0.030         # RMS frame
MIN_S, TAIL_S, TAIL_MIN_S, MAX_S = 0.5, 0.5, 0.8, 15.0
MIN_SPEECH_S, PARTIAL_S, LEAD_IN_S = 0.3, 0.8, 0.3


@dataclass
class Commit:
    start_ms: int
    end_ms: int
    audio: np.ndarray        # float32 -1..1


@dataclass
class Partial:
    start_ms: int
    end_ms: int
    audio: np.ndarray


def _db(x):
    return 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9)


class LiveSegmenter:
    def __init__(self, silence_db=-45.0, partials=True, sr=16000):
        self.silence_db, self.partials, self.sr = silence_db, partials, sr
        self.buf = np.zeros(0, np.float32)
        self.base = 0                     # absolute frame of buf[0]
        self.end = None                   # absolute frame after the last sample seen
        self.last_partial = 0             # absolute frame of the last partial
        self._checked = 0                 # absolute frame of the last look
        self.dropped_ms = 0

    # ---- helpers --------------------------------------------------------------------------------
    def _ms(self, frame):
        return int(round(frame * 1000 / self.sr))

    def _speech_secs(self, x):
        f = int(FRAME * self.sr)
        frames = x[: len(x) // f * f].reshape(-1, f) if len(x) >= f else np.zeros((0, f), np.float32)
        rms_db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
        return float((rms_db > self.silence_db).sum() * f / self.sr)

    def _commit(self, cut):
        """Commit buf[:cut] and keep the rest. -> Commit"""
        c = Commit(self._ms(self.base), self._ms(self.base + cut), self.buf[:cut].copy())
        self.buf, self.base = self.buf[cut:], self.base + cut
        self.last_partial = self.base
        return c

    # ---- API --------------------------------------------------------------------------------------
    def feed(self, pcm, start_frame):
        """pcm: int16 bytes whose first sample is frame `start_frame` of the recording. -> [Commit | Partial]."""
        out = []
        x = np.frombuffer(pcm, np.int16, len(pcm) // 2).astype(np.float32) / 32768
        if self.end is not None and start_frame > self.end:         # chunks were dropped: a boundary
            self.dropped_ms += self._ms(start_frame - self.end)
            if len(self.buf) and self._speech_secs(self.buf) >= MIN_SPEECH_S:
                out.append(self._commit(len(self.buf)))
            self.buf, self.base = np.zeros(0, np.float32), start_frame
            self.last_partial = self._checked = start_frame
        if self.end is None:
            self.base = self.last_partial = self._checked = start_frame
        self.buf = np.concatenate([self.buf, x]) if len(self.buf) else x
        self.end = start_frame + len(x)
        if self.end - self._checked >= CHECK_S * self.sr:
            self._checked = self.end
            out += self._look()
        return out

    def _look(self):
        sr, out = self.sr, []
        x, n = self.buf, self.base + len(self.buf)
        if len(x) < MIN_S * sr:
            return out
        tail_quiet = len(x) > TAIL_MIN_S * sr and _db(x[-int(TAIL_S * sr):]) < self.silence_db
        too_long = len(x) > MAX_S * sr
        has_speech = self._speech_secs(x) >= MIN_SPEECH_S
        if (tail_quiet or too_long) and has_speech:
            cut = len(x)
            if too_long and not tail_quiet:                       # cut at the quietest 100 ms of the last 4 s
                w = x[-4 * sr:]
                step, win = int(0.025 * sr), int(0.1 * sr)
                k = int(np.argmin([_db(w[i:i + win]) for i in range(0, len(w) - win, step)]))
                cut = len(x) - 4 * sr + k * step + win // 2
            out.append(self._commit(cut))
        elif tail_quiet and not has_speech:                       # drop silence, keep a little lead-in
            drop = max(0, len(x) - int(LEAD_IN_S * sr))
            self.buf, self.base = x[drop:], self.base + drop
            self.last_partial = max(self.last_partial, self.base)
        elif self.partials and has_speech and n - self.last_partial >= PARTIAL_S * sr:
            self.last_partial = n
            out.append(Partial(self._ms(self.base), self._ms(n), x.copy()))
        return out

    def flush(self):
        """End of the take: commit the pending speech, if any."""
        if len(self.buf) and self._speech_secs(self.buf) >= MIN_SPEECH_S:
            return [self._commit(len(self.buf))]
        self.buf = np.zeros(0, np.float32)
        return []
