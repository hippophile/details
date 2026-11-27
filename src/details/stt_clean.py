"""Pure transcript cleaner run before a finished STT run is stored. Segments are dicts (start_ms, end_ms, text, ...).
Known limits: a real line containing a junk phrase ("subtitles" inside a sentence) is dropped; counts say how often."""
import re

from .llm.quotes import norm

JUNK = ("υπότιτλοι", "υποτιτλοι", "authorwave", "subtitles")      # known Whisper hallucinations on silence (as in v1)
LOOP_MIN, LOOP_MAX_UNIT = 8, 4      # a 1-4 word unit repeated 8+ times in a row is a decoder loop (5 can be speech)
JOIN_SLACK_S = 2                    # a join duplicate starts within overlap_s + this of the window join


def _norm(w):
    return re.sub(r"\W+", "", w.lower())


def collapse_loops(text):
    """-> (text with every repeat loop reduced to one copy of its unit, number of loops collapsed)."""
    words, norm, out, n, i = text.split(), [_norm(w) for w in text.split()], [], 0, 0
    while i < len(words):
        for u in range(1, LOOP_MAX_UNIT + 1):
            unit, reps = norm[i:i + u], 1
            if len(unit) < u or not any(unit):
                continue
            while norm[i + reps * u:i + (reps + 1) * u] == unit:
                reps += 1
            if reps >= LOOP_MIN:
                out += words[i:i + u]
                i, n = i + reps * u, n + 1
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(out), n


def drop_join_dupes(segs, window_s, overlap_s):
    """-> (segs, n dropped): of two consecutive segments on opposite sides of a window join (k * window_s) that both
    start within overlap_s + JOIN_SLACK_S of it, the shorter is dropped when its text is contained in the other's
    (ties keep the earlier). One drop per pair: the survivor is not compared with the next segment. Paraphrases stay."""
    w, near, out, n, hit = window_s * 1000, (overlap_s + JOIN_SLACK_S) * 1000, [], 0, False
    for s in segs:
        a = out[-1] if out else None
        j = s["start_ms"] // w * w
        if a and not hit and j and a["start_ms"] < j and j - a["start_ms"] <= near and s["start_ms"] - j <= near:
            na, nb = norm(a["text"]), norm(s["text"])
            if na and nb and (na in nb or nb in na):
                n += 1
                if len(nb) > len(na):
                    out[-1] = s
                hit = True
                continue
        hit = False
        out.append(s)
    return out, n


def clean(segments, duration_ms, window_s=None, overlap_s=0):
    """-> (cleaned copies of the segments, counts {junk, loops, capped, trimmed, join_dupes}); input not modified.
    The join-duplicate rule needs window_s and overlap_s > 0."""
    c, kept = {"junk": 0, "loops": 0, "capped": 0, "trimmed": 0, "join_dupes": 0}, []
    for s in segments:
        if any(j in s["text"].lower() for j in JUNK):
            c["junk"] += 1
            continue
        text, n = collapse_loops(s["text"])
        c["loops"] += n
        if not text.strip() or s["start_ms"] >= duration_ms:
            continue
        s = {**s, "text": text}
        if s["end_ms"] > duration_ms:
            s["end_ms"] = max(duration_ms, s["start_ms"] + 1)
            c["capped"] += 1
        kept.append(s)
    kept.sort(key=lambda s: s["start_ms"])
    if window_s and overlap_s:
        kept, c["join_dupes"] = drop_join_dupes(kept, window_s, overlap_s)
    for s, nxt in zip(kept, kept[1:]):
        if s["end_ms"] > nxt["start_ms"]:
            s["end_ms"] = max(nxt["start_ms"], s["start_ms"] + 1)
            c["trimmed"] += 1
    return kept, c
