# Tasks

1. [x] Tag `archive/live-v1` on current main.
2. [x] Tests first: `/` → `/library`; `/live` `/models` `/ws` → 404; Record page without `href="/live"`; WS origin test on `/api/record/ws`. Run, observe failures.
3. [x] Code: root redirect, remove live_v1 routes/module/page, Record link, README/Makefile/startup print. Run changed tests green.
4. [x] Full `make test` green.
5. [ ] Restart the running server (owner OK), verify `/` lands on Library in the browser.
6. [ ] Archive the change; fix app-foundation Purpose line.
