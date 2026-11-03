"""Score Greek+English speech-to-text against the test meeting script.
Usage: make score AUDIO=sessions/<file>.wav [MODELS="turbo large"]
Models: turbo | turbo+prompt | large   (default: all three)
Reference = the '**Name:** text' lines of script_meeting_el_en.md."""
import json, re, sys, time, unicodedata, subprocess, pathlib
import jiwer

HERE = pathlib.Path(__file__).parent
SCRIPT = HERE / "script_meeting_el_en.md"
REPOS = {"turbo": "mlx-community/whisper-large-v3-turbo",
         "large": "mlx-community/whisper-large-v3-mlx"}
# prompt = vocabulary hint (Whisper allows ~224 tokens); tells it English tech words stay in English
PROMPT = ("Συνάντηση για AI adoption, agents, local LLM, RAG, embeddings, PostgreSQL, pgvector, MCP, "
          "AI Act, GDPR, Azure OpenAI, Docker, CI/CD, GitHub Actions, pull request, sprint, .NET, Blazor, KPIs, Jira, backlog.")
HESITATIONS = ["εεε", "εμμ"]
FILLERS = ["λοιπον", "δηλαδη", "ας πουμε", "ξερεις", "καπως", "you know", "like", "basically", "actually", "οκ"]
TECH = ["ai adoption", "agents", "agent", "proof of concept", "on prem", "llm", "gemma", "macbook", "latency",
        "stakeholders", "tokens", "context window", "rag", "embeddings", "documents", "prompt", "postgresql",
        "pgvector", "tool calling", "mcp", "compliance", "ai act", "gdpr", "risk assessment", "governance document",
        "use cases", "high risk", "legal", "budget", "cloud", "azure openai", "hardware", "deployment", "vm",
        "docker container", "docker", "ci cd", "pipeline", "github actions", "pull request", "unit tests", "rollout",
        "staging", "sprint", "sprints", "backend", "net", "blazor", "dashboard", "kpis", "triage", "accuracy",
        "fine tuning", "prompt engineering", "few shot examples", "dataset", "gpu", "overkill", "action items",
        "tickets", "jira", "backlog"]
NUMBERS = ["15 νοεμβριου", "3000 ευρω", "70 τοις εκατο", "90 τοις εκατο", "30000 tokens", "40 tokens", "10 το πρωι"]


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # strip tonos
    s = s.replace("ς", "σ").replace("%", " τοισ εκατο ")
    s = re.sub(r"(?<=\d)[.,](?=\d{3})", "", s)                     # 3.000 -> 3000
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(ok|okay|οκει|οκευ)\b", "οκ", s)
    s = re.sub(r"\b(ε+|ε+μ+|ε+μμ+|μμ+|αα+|eh+|uh+|um+|hmm+)\b", lambda m: "εμμ" if "μ" in m.group(0) or "m" in m.group(0) else "εεε", s)
    return " ".join(s.split())


def count(term, text):
    return len(re.findall(rf"(?<!\w){re.escape(norm(term))}(?!\w)", text))


def recall(terms, ref, hyp):
    want = sum(count(t, ref) for t in terms)
    got = sum(min(count(t, ref), count(t, hyp)) for t in terms)
    missed = [t for t in terms if count(t, hyp) < count(t, ref)]
    return (round(100 * got / want) if want else None), missed


def reference():
    lines = re.findall(r"^\*\*[^*:]+:\*\*\s*(.+)$", SCRIPT.read_text(), re.M)
    return norm(" ".join(lines))


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return float(out)


def run(audio, model):
    import mlx_whisper
    repo = REPOS["large" if model == "large" else "turbo"]
    t = time.time()
    r = mlx_whisper.transcribe(str(audio), path_or_hf_repo=repo, language="el", condition_on_previous_text=False,
                               initial_prompt=PROMPT if model.endswith("+prompt") else None)
    return r["text"].strip(), time.time() - t


def score(ref, hyp_raw):
    hyp = norm(hyp_raw)
    strip = lambda s: " ".join(w for w in s.split() if w not in HESITATIONS)
    return {"wer_all": round(100 * jiwer.wer(ref, hyp), 1),
            "wer_no_hesitations": round(100 * jiwer.wer(strip(ref), strip(hyp)), 1),
            "tech_terms": recall(TECH, ref, hyp), "fillers_kept": recall(FILLERS, ref, hyp),
            "hesitations_kept": recall(HESITATIONS, ref, hyp), "numbers": recall(NUMBERS, ref, hyp)}


if __name__ == "__main__":
    audio = pathlib.Path(sys.argv[1])
    models = sys.argv[2:] or ["turbo", "turbo+prompt", "large"]
    ref, mins = reference(), duration(audio) / 60
    outdir = HERE.parent / "sessions" / "out"; outdir.mkdir(parents=True, exist_ok=True)
    results = {}
    for m in models:
        hyp, secs = run(audio, m)
        (outdir / f"{audio.stem}.{m}.txt").write_text(hyp)
        s = score(ref, hyp); s["secs"] = round(secs, 1); s["secs_per_audio_min"] = round(secs / mins, 1)
        results[m] = s
        print(f"\n== {m} ==  {secs:.0f}s for {mins:.1f} min audio ({secs / mins:.1f}s per min)")
        print(f"  words wrong: {s['wer_all']}%   (ignoring εεε/εμμ: {s['wer_no_hesitations']}%)")
        for k, label in (("tech_terms", "tech words right"), ("numbers", "numbers right"),
                         ("fillers_kept", "fillers kept"), ("hesitations_kept", "εεε/εμμ kept")):
            pct, missed = s[k]
            print(f"  {label:17} {pct}%   missed: {', '.join(missed[:12]) or '-'}")
    (outdir / f"{audio.stem}.results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\ntranscripts + results in {outdir}/")
