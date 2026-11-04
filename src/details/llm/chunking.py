"""Chunking at segment boundaries and the on-disk chunk cache (<data_dir>/cache/llm/<key>.json)."""
import hashlib, json, os


def estimate_tokens(text, chars_per_token):
    """Estimate, not measured: chars / chars_per_token."""
    return int(len(text) / chars_per_token)


def split_segments(segments, chunk_tokens, overlap_tokens, chars_per_token):
    """Greedy split into chunks of about chunk_tokens, never inside a segment. Each next chunk starts with the
    trailing whole segments that fit in overlap_tokens. A segment longer than a chunk is its own chunk."""
    limit, ov = chunk_tokens * chars_per_token, overlap_tokens * chars_per_token
    chunks, cur, size = [], [], 0
    for seg in segments:
        if cur and size + len(seg) > limit:
            chunks.append(cur)
            tail, t = [], 0
            for s in reversed(cur):
                if t + len(s) > ov:
                    break
                tail.insert(0, s)
                t += len(s)
            if t + len(seg) > limit:
                tail, t = [], 0
            cur, size = tail, t
        cur.append(seg)
        size += len(seg)
    if cur:
        chunks.append(cur)
    return chunks


def cache_key(kind, template_sha, model, params, chunk_text):
    blob = json.dumps([kind, template_sha, model, params, chunk_text], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _path(data_dir, key):
    return data_dir / "cache" / "llm" / f"{key}.json"


def cache_get(data_dir, key):
    try:
        return json.loads(_path(data_dir, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache_put(data_dir, key, value):
    p = _path(data_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
