# archive-live-v1

## Driver
Owner, 2026-11-17: "this is the main app, archive the old" — Library/Record/Analyse is the app; the v1 `/live` test page goes.

## Measured blast radius (2026-11-17, `grep -rn "live_v1\|live-v1\|/live\b\|/models\b\|/ws\b"` over src, web, tests, README, Makefile)
| Where | Hits |
|---|---|
| `src/details/app.py` | import + `add_live_routes` call + root redirect to `/live` |
| `src/details/api/live_v1.py` | whole file (routes `/live`, `/models`, `/ws`) — imported only by app.py |
| `src/details/__main__.py` | startup line prints `/live` |
| `src/details/live/segmenter.py` | docstring mentions the v1 port (history note, left as is) |
| `web/live-v1/index.html` | whole page |
| `web/record/index.html` | one `href="/live"` hint |
| tests | `test_app.py` (2 tests), `test_live_page.py` (1 test + 1 needle), `test_record_page.py` (1 needle), `test_security.py` (WS origin test uses `/ws`) |
| README.md, Makefile | one line each |

## Decision
1. Tag the current `main` as `archive/live-v1` (restore with `git checkout archive/live-v1`).
2. `GET /` redirects to `/library`.
3. Delete `web/live-v1/`, `src/details/api/live_v1.py`, and the `/live`, `/models`, `/ws` routes.
4. Remove the "v1 live page" hint link from the Record page.
5. Re-point the cross-origin WebSocket security test to `/api/record/ws` so the guard stays covered.
6. README, Makefile help and startup print point to `/library`.

## Verification
- `GET /` → 302 `Location: /library`.
- `GET /live`, `/models`, `/ws` → 404 (negative case).
- Record page has no `href="/live"`.
- Foreign-Origin upgrade to `/api/record/ws` → 403; same origin → upgrade accepted.
- Full `make test` green.

## Caveats
- Bookmarks to `/live` break.
- No speech test without a recording any more; Record's Live assist covers it once Start is pressed.
- The app-foundation spec Purpose line still mentions the v1 page; deltas cannot edit Purpose — fixed by hand after archive.
