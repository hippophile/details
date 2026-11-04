"""Fake live STT worker (line protocol of details.live.worker). Run as a script: python live.py
Request: a JSON line {"id", "n", "start_ms", "kind"} followed by n raw int16 bytes. Reply: {"id", "text"} per request.
Prints {"ready": true} after the (fake) model load. Exits on stdin EOF. Knobs (env, inherited from the server):
  LIVE_FAKE_READY_DELAY=<s>       sleep before ready (cold model load)
  LIVE_FAKE_REPLY_DELAY=<s>       sleep before every reply (a busy / blocked worker)
  LIVE_FAKE_DIE_ON=<n>            os._exit(137) on the n-th request of this process
  LIVE_FAKE_DIE_FIRST_SPAWNS=<k>  the first k spawns (counted in LIVE_FAKE_PIDFILE) exit right after ready
  LIVE_FAKE_PIDFILE=<file>        append this pid, one per spawn
  LIVE_FAKE_CALLS=<file>          append one JSON line per request {id, kind, start_ms, samples, t}
  LIVE_FAKE_PAD=<n>               pad every reply with n extra words
Reply text: "συζητάμε το θέμα <start_ms>"."""
import json, os, sys, time


def main():
    pidfile = os.environ.get("LIVE_FAKE_PIDFILE")
    spawn = 1
    if pidfile:
        with open(pidfile, "a") as f:
            f.write(f"{os.getpid()}\n")
        spawn = len(open(pidfile).read().split())
    time.sleep(float(os.environ.get("LIVE_FAKE_READY_DELAY", "0")))
    print(json.dumps({"ready": True}), flush=True)
    if spawn <= int(os.environ.get("LIVE_FAKE_DIE_FIRST_SPAWNS", "0")):
        os._exit(137)
    die_on, n_req = int(os.environ.get("LIVE_FAKE_DIE_ON", "0")), 0
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            return 0
        req = json.loads(line)
        payload = stdin.read(req["n"])
        n_req += 1
        if os.environ.get("LIVE_FAKE_CALLS"):
            with open(os.environ["LIVE_FAKE_CALLS"], "a") as f:
                f.write(json.dumps({"id": req["id"], "kind": req.get("kind"), "start_ms": req["start_ms"],
                                    "samples": len(payload) // 2, "t": time.time()}) + "\n")
        if die_on and n_req >= die_on:
            os._exit(137)
        time.sleep(float(os.environ.get("LIVE_FAKE_REPLY_DELAY", "0")))
        text = f"συζητάμε το θέμα {req['start_ms']}" + " λέξη" * int(os.environ.get("LIVE_FAKE_PAD", "0"))
        print(json.dumps({"id": req["id"], "text": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())
