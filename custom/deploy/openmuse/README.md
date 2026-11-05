# OpenMuse local module

This is a pinned local fork of [CopilotKit/OpenMuse](https://github.com/CopilotKit/openmuse), upstream SHA `a4944f4c471d3dc93b8bfc060b5da309b5216ab2`, MIT. It keeps the upstream web UI, durable task/goal engine, PDF files and PGlite store. `local.patch` is the exact source delta; dependencies, builds, media and databases are not committed.

## Actual local mode

- One main conversation is persisted on the server by upstream `/api/conversation` and loaded again on reload/other devices. Cloud Rich Threads and their menu are explicitly disabled; no fake Intelligence key is used.
- Native Ollama chat uses only the configured model which must already be resident. It preserves `keep_alive=-1` and never sets `num_ctx`. SSE and tool-call arguments are translated for the upstream agent SDK. This is text-only; image/audio inputs are rejected.
- Every native API and static route except loopback `/api/health` requires `X-OpenMuse-Service-Key`. Only the owner-authenticated outer gateway holds this key; browser `Authorization`, cookies and service-key headers must be stripped before proxy injection. Inside this sealed deployment there is one owner, `local-user`. Do not expose port8797 or reuse the key for another owner.
- No CopilotKit cloud persistence, external API model, keyless Parallel search, telemetry, Google OAuth, browser worker or Docker terminal is enabled. Those features need separate deliberate configuration. The UI honestly shows the computer offline.

## Install/reproduce (target user)

1. `python3 install.py --build --install-unit` clones the exact upstream revision, applies the local patch and builds real web/server artifacts. It refuses a different existing revision. Limit build concurrency alongside other services; Expo uses one worker and Node heap1024MB.
2. `python3 configure.py --public-url https://your-hub:8474 --model your-already-resident-model` creates private `~/.config/openmuse/runtime.env` and `proxy.token`. Existing settings are not overwritten; no key is printed and no model is downloaded.
3. Configure the authenticated gateway: loopback `http://127.0.0.1:8797`, header `X-OpenMuse-Service-Key`, secret file `~/.config/openmuse/proxy.token`. Isolate the module on its own origin. The gateway must allow only the mapped owner, check Origin for mutations and strip browser-supplied identity/service headers.
4. Enable the user unit: `systemctl --user enable --now openmuse-local.service`. User lingering must already be enabled for unattended reboot startup, or be explicitly arranged by the host operator.

The unit caps CPU at100%, memory at768MB and allows writes only to module data. It does not restart or modify Ollama, NVR, existing voice services or browser profiles.

## Verify

`systemctl --user status openmuse-local.service`; `/api/health` on127.0.0.1:8797 answers `ok`. Requests to root/API/assets without the server-only key must return401. Use the authenticated outer UI for actual chat, task and PDF operations. `node --import tsx --test tests/local-ollama.test.ts` runs transport boundary/stream tests from the upstream source checkout.

State is under `~/.local/share/openmuse/data`, configuration under `~/.config/openmuse`. To back up PGlite, stop only this unit, copy the data directory privately, then start it. Never replace a live/newer database with an old checkpoint. To disable the module, stop/disable this unit and remove its gateway entry; keep data until explicitly discarded.
