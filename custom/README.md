# Sasha AI Workbench for Open WebUI

A personal Open WebUI fork with a persistent agent workbench. The upstream application, copyright notices, and Open WebUI name remain present. A monochrome combination of Sasha's avatar and the upstream logo identifies this personal fork on loading, in the sidebar, and in extension settings.

This branch starts from **Open WebUI v0.12.4**, commit `33c1978d5bad52a31df75ea55143025dfad18d16`. Custom integration lives entirely in `custom/`; upstream application sources are retained. This is an independent community customization, not an Open WebUI, Anthropic, OpenAI, or Nous Research product.

## Features

- Optional separate-origin local VoiceStudio/OpenMuse modules with owner-only SSO, isolated browser storage and protected HTTP/WebSocket/SSE/upload gateways. Modules are disabled by default; see [LOCAL-MODULES.md](LOCAL-MODULES.md).

- Native Codex/Claude Code tasks with durable queue, streaming progress, a separate final result, UTC timestamps and task duration, files, cancellation, and stable message IDs.
- Interactive tables, line/bar/pie charts, quizzes and dashboards with a fullscreen viewer and JSON/CSV exports; provider HTML/JavaScript never executes.
- Personal Telegram, Slack, Discord, Instagram Professional, Facebook Pages, Gmail, Outlook, IMAP/SMTP, HTTP and MCP connections. Accounts require your own credentials and API permissions; a catalog entry is not a connected account.
- Compact usage ring or dollar balance, with independent quota windows and reset times where providers supply them. Unknown values remain unknown.
- A harness panel after the extension button: manual/automatic model routing, complexity preferences, available quotas, token limits, and credit policies. Cross-model continuation carries saved context and marks interrupted work rather than blindly repeating side effects.
- Project memory, plans, checkpoints, reusable learning notes, and portable Markdown `HANDOFF.md` export. These are stored artifacts and a reuse loop, not model weight training.
- Provider-specific connection guidance and private API-key configuration. Account sign-in still requires the account owner to complete the provider's login flow.
- MCP, SKILL.md, plugin bundles, and a configurable discovery/context-selection catalog. Trust policy controls automatic installation; installing a skill does not automatically authorize arbitrary executable packages.
- Sidebar project management, file upload/download/edit/move, external local roots and HTTPS WebDAV browsing, and verified project backups.

The exact checked source revisions and offline validation are in [`VERSION.json`](VERSION.json). Installations need their own accounts or keys; none are bundled.

## Install (Linux)

Requires Python 3.12+, Docker Compose, and whichever agent CLIs you intend to use. Run the bridge as the normal Linux account that owns those CLI logins. Do not run an unrestricted root agent service.

```sh
git clone --branch sasha-harness https://github.com/sashak86/ai-hub-open-webui.git
cd ai-hub-open-webui
python3 -m venv custom/.venv
custom/.venv/bin/pip install -r custom/requirements.txt
custom/.venv/bin/python custom/prepare.py
```

Preparation downloads the pinned image, copies the compiled static build, verifies the two reviewed chunk hashes, and creates `custom/runtime/private.env` with mode `0600`. It does **not** start a service. Keep this directory private and out of version control. Set your administrator email and password there before first startup. The initial administrator values are consumed by Open WebUI on its first startup.

```sh
docker compose -f custom/compose.yaml up -d
custom/.venv/bin/python custom/run-bridge.py
```

The frontend proxy and agent bridge listen on `http://127.0.0.1:8088`; the underlying WebUI listens on `127.0.0.1:3080`. Linux host networking lets the container reach the host CLI service. Do not expose the underlying Docker port directly. For access from other devices, place your existing HTTPS reverse proxy in front of port 8088 and set `HUB_PUBLIC_ORIGINS` in the private environment to your public hostname and port. Preserve Authorization and WebSocket headers. The backend's origin check requires the proxy to preserve the actual host and HTTPS scheme; use a trusted proxy configuration rather than accepting arbitrary forwarded headers.

In another terminal, after both services are healthy, install the native Pipe and Stop action:

```sh
custom/.venv/bin/python custom/deploy/install-chat-pipe.py
```

This uses the private administrator credentials, preserves an earlier Pipe configuration in a private local backup, and does not replace chat history. Refresh the browser and choose an `AI Hub` model. The first account is the administrator. Workbench configuration is restricted to administrators unless user IDs are explicitly listed in `SASHA_WORKBENCH_ALLOWED_USERS` for the bridge.

## Agent accounts and API providers

Install and sign in to Codex/Claude Code using their official instructions; account subscriptions and paid API keys are separate billing methods. The connection panel reports what is missing and guides the corresponding flow. Codex device/account sign-in still requires you to open the official verification page; the service cannot create or purchase an account.

API keys entered through the workbench remain in private server files and are masked in responses. No key is put in a browser's persistent settings or a public repository. Provider balances and subscription windows are best-effort read-only data and can become unavailable; the UI does not invent zeros or reset times.

Routing applies only to providers and models you have connected. An explicit dollar budget excludes models whose maximum cost is unknown; an estimated task cost is not a provider's hard spend cap. Review the settings before enabling automatic model selection or context-dependent extension installation.

## Shared add-ons

Open **Extensions → Workbench add-ons** to install, enable, configure and test the reviewed integrations. Installation, activation and a successful connection test are separate states; an installed package does not create provider accounts or credits. Each browser user's configuration and installed service data live in a separate private namespace. Package services need Linux systemd; use the normal account's user service manager in a standalone installation.

| Integration | Upstream | How this fork uses it |
| --- | --- | --- |
| OmniRoute | [cheaper-inference/OmniRoute](https://github.com/cheaper-inference/OmniRoute), MIT | Private gateway connection and discovered text API models. An explicit local setup uses an already-loaded Ollama model with a key restricted to that model and connection; it never downloads models or copies cloud credentials. Other providers require configuration in the gateway. Native Codex/Claude account authentication remains separate. |
| Claude mem | [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem), Apache-2.0 | Owner/project-scoped recall and explicit note storage in the original worker. Optional completed-answer capture is off initially; saved observations are reference data. |
| Headroom | [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom), Apache-2.0 | Original local compression library in lossless agent mode, preserving the latest user message and falling back to full context on failure. Counts use a local reference tokenizer, not provider billing. No ML model installation is needed. |
| Claude Code Setup | [Anthropic's official plugin](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-code-setup), Apache-2.0 | Original read-only automation recommendation skill, applied as shared agent instructions. Recommendations do not install hooks automatically. |
| Task Observer | [rebelytics/one-skill-to-rule-them-all](https://github.com/rebelytics/one-skill-to-rule-them-all), CC BY 4.0 | Original skill and references, with Workbench instructions for proposed improvements and handoff. Installed skills are not silently rewritten. Attribution remains with rebelytics and upstream contributors. |

The shared turn hooks run with the harness switch either on or off. Native coding agents can use the downloaded skill references on disk. Text-only API models receive the bounded instructions and recalled context but have no filesystem tools, Claude hooks or Cowork delivery features. Headroom processes the context supplied by Workbench before the call; this does not automatically intercept every internal tool response of an independently running CLI. Memory writes use the worker's explicit save API, without launching a paid summarization model.

Installation recipes pin the reviewed package version and keep a private installation log. Review the installed service and connection status before use. Third-party package licenses apply separately from this fork's code license.

## Interactive results and personal services

A completed task saves a final answer separately from streaming progress. Start/end timestamps use UTC; queue time, execution duration and total time are distinct. Cancelled, interrupted and failed jobs keep an honest incomplete result. Interactive data is validated against bounded declarative table/chart/quiz/dashboard schemas. Open the result window to sort/filter a table, inspect a chart, answer a quiz, or expand the view; exports retain the private owner boundary. CSV export neutralizes spreadsheet formula prefixes. The fork does not execute arbitrary model-generated HTML/JavaScript applications.

Open **Services** inside extension settings to create an owner-private connection. Saving means configured; **Check** performs the real provider request before reporting connected. Tokens stay server-side and are masked. No server-global accounts, bot tokens or native CLI credentials are imported into a browser user's service list. New native Codex/Claude tasks expose permitted read tools through a local MCP adapter. Text-only API models still have no autonomous function calling: explicitly add a read result to the next prompt when useful; external content remains untrusted data.

| Service | Setup and supported operations |
| --- | --- |
| Telegram | Your BotFather token; getMe/getChat and explicitly approved sendMessage. No personal account history, getUpdates polling, webhook or offset changes. |
| Slack / Discord | Your own workspace app/user token or invited Discord bot; permitted search/channel reads and explicitly approved sends. Slack search requires a user token with search:read. |
| Instagram Professional | Business/Creator account, Meta app and Instagram Login token/account ID; profile and media reads only. Personal accounts, publishing and DM are not implemented. Instagram Login does not require a linked Facebook Page. Other-user access may require Meta app review. |
| Facebook Pages | Page ID/access token; page reads and an explicitly approved text post. Appropriate pages_read_engagement/pages_manage_posts permissions remain required. |
| Gmail / Outlook | Your Web OAuth app with an exact registered HTTPS redirect. Read-only mailbox access by default; optionally permit draft creation. Gmail gmail.compose or Microsoft Mail.ReadWrite enables drafts; these adapters never send email. |
| IMAP / SMTP | Your mail host, SMTP host, username and app password; TLS IMAP993 INBOX search/read with no flag changes, TLS SMTP465 send only after approval. |
| HTTP / MCP | Explicit HTTPS endpoint; bearer token optional for services without authentication. HTTP relative GET/approved POST. MCP initializes a JSON Streamable HTTP session; SSE-only/stdio remote servers are unsupported. All remote tool calls require approval. |

OAuth setup requires your own registered client_id, client_secret and exact redirect_uri. Save those fields without an access token, start OAuth, open the provider URL and paste the returned callback URL into the connection panel. No provider app is created on your behalf. Google/Microsoft use PKCE S256; owner-bound state expires after ten minutes and is single-use. A refresh action uses your granted refresh token; if none was issued, sign in again. Instagram long-lived token exchange/refresh is not implemented; supply a valid token or repeat login. Restricted Google scopes and Meta app-review requirements still apply.

Each write or draft displays the exact tool arguments and requires explicit confirmation. Approval expires after three minutes, is owner-bound, cannot be reused and cannot authorize changed arguments. The local native MCP server publishes read tools only. Network errors never automatically replay writes. Remove deletes local credentials; revoke access separately in the provider's settings. Explicit HTTP/MCP endpoints can reach your configured private network, so they are not a network sandbox. Native coding agents share their OS user's trust boundary as in the rest of this workbench.

Useful references: [Telegram](https://core.telegram.org/bots/api), [Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login), [Gmail API](https://developers.google.com/workspace/gmail/api/reference/rest), [Microsoft draft messages](https://learn.microsoft.com/en-us/graph/api/user-post-messages?view=graph-rest-1.0), [MCP HTTP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).

## Projects, external storage, and backups

New projects live under the workbench's private data directory. `SASHA_WORKBENCH_DATA` and `SASHA_WORKBENCH_PROJECTS` can override its default user-home locations.

External local directories require a server-side allowlist:

```sh
export AI_HUB_WORKSPACE_ROOTS=/path/to/allowed/projects:/path/to/external/mount
```

This permits registering already-mounted storage; the workbench does not mount devices or alter system mounts. Explicit existing-project import keeps the original directory in place. Imported roots support display rename and explicit physical rename/move. Renaming a root beside its current location requires its parent to be server-allowlisted; moving to another configured local storage is also supported. Individual files remain manageable. Active tasks prevent project movement, deletion, or restore; a folder used as a running process's working directory cannot be moved.

HTTPS WebDAV supports directory listing, download, upload, new directories, movement, deletion, connection tests, and backup export. Credentials stay server-side. SFTP and S3 are **not** implemented adapters in this version. WebDAV is a remote file browser and archive destination, not a mounted working directory for native agent processes.

Manual and scheduled backups store ZIP archives with SHA-256 verification. The scheduler scans persisted registries after service startup, even before a browser reconnects. Limits are 256 MiB and 20,000 source files per archive; scheduling is one hour or longer with explicit retention. Upload/download operations are capped at 32 MiB. A backup export may therefore exceed the remote single-request cap and will be rejected rather than silently truncated.

These are **project code/content backups**, not full machine or credential backups: `.env`, credentials, secret directories, private keys, dependency caches, and Git object data are excluded. Preserve credentials through your own private backup system. Restore verifies the archive, rejects traversal and symlinks, retains executable scripts, and creates a rollback copy first. Excluded local secret files remain in place. Local deletion uses a private recycle directory; WebDAV deletion follows the remote server's deletion behavior.

## Test and update

```sh
custom/.venv/bin/pip install pytest
PYTHONPATH=custom/bridge custom/.venv/bin/python -m pytest custom/tests -q
```

The offline tests use a synthetic native provider and HTTP transport fixtures. They test real queue/file/backup/proxy logic without spending tokens or using real cloud credentials. A real user's WebDAV account and paid model access must be checked with their own configured connection.

Static patching is intentionally pinned: a newer Open WebUI build must receive a reviewed compatibility update. Keep the source version and image digest together. Do not rerun preparation against unrelated images or overwrite a mounted hot-asset by renaming its inode in a running container.

For upgrades, first export current workbench handoffs, take consistent backups of the WebUI data volume and private workbench data, update the source, and rerun offline tests. Stop the relevant services only after running tasks finish or are deliberately cancelled. Database rollback must not overwrite newer chat history.

## Continue work in another model

The harness keeps plans, progress, memory, and checkpoint artifacts on the server, not only in the current model context. Use its export action before a model/account limit. The export is private conversation/project content; save it securely and supply it to the next model. Source maintenance notes belong in your own private handoff files; they are not committed into this public fork.

## Licensing and branding

Upstream files retain the [Open WebUI License](../LICENSE) and its branding conditions. Additive Sasha branding does not remove the upstream name/logo. If a deployment changes upstream identifiers, it must meet the exceptions or obtain permission described in that license. The customization's code license is in [LICENSE](LICENSE). The supplied personal avatar is included for this personal fork with the owner's authorization and is not a general trademark endorsement.

## Local images and bounded video animation (0.4.0)

The sidebar and Harness settings include **Images and video**. Local image generation uses a separately installed ComfyUI with a fixed SD1.5 512×512 workflow. Put `v1-5-pruned-emaonly.safetensors` in ComfyUI `models/checkpoints`; obtain the model from its official repository and review its license. Start ComfyUI on a private reachable address, then set `SASHA_WORKBENCH_COMFY_URL=http://127.0.0.1:8188` for the bridge and restart it. When ComfyUI is in another container, use its private container-network address. The browser never accesses ComfyUI directly. No model or GPU package is automatically installed; no public ComfyUI port is required. Without this setting, generation remains explicitly disconnected.

Jobs and PNGs are private to their authenticated owner and survive page refresh. Ambiguous submissions are not replayed. Only one local generation runs at a time. A stale job absent from both ComfyUI history and queue becomes interrupted, allowing a new explicit request. The gallery can download files or add them through the native Open WebUI attachment input, without sending a chat message.

**Image animation is a separate feature**, available when `ffmpeg` with `libx264` is on the bridge PATH: a fixed five-second 512×512 smooth zoom, 24fps, without audio. This is camera motion on a still image, **not neural video generation**. Only an owned generated PNG can be animated; filters, paths and duration are not supplied by a browser. ffmpeg uses two threads and low CPU scheduling priority.

Wan neural video generation is **not enabled** in this release. The UI states this explicitly. Krea is also not enabled by the portable configuration. Model quality/speed depend on hardware; SD1.5 is an economical baseline, not a claim of parity with current cloud image models. The optional ai-hub dispatcher in the shared module is not enabled by the portable adapter and must not be enabled on other hosts.

## Better local images and normal chat (0.5.0)

Type **`/image a cinematic portrait ...`** in the ordinary composer. Russian explicit commands **Нарисуй… / Создай изображение… / Сгенерируй картинку…** also route to local generation. The native Pipe returns an actual owner-private image in the assistant message, using Open WebUI Files/Storage; history and image remain after reload. It does not ask the selected text agent to pretend an image was generated. Quoted prompts, negation and questions about drawing are not image commands. Image editing from attachments is explicitly unsupported rather than silently ignored.

The new local default is **ByteDance SDXL-Lightning 8-step**, full all-in-one checkpoint `sdxl_lightning_8step.safetensors` in ComfyUI `models/checkpoints`. Official source: https://huggingface.co/ByteDance/SDXL-Lightning . Pinned model revision `c9a24f48e1c025556787b0c58dd67a091ece2e44`, SHA256 `43f0501ac4ffcef84f3fc32a47779f24c181647fa97ef7f5ec4428107d732ae9`, size 6,938,040,682 bytes. The fixed workflow uses 1024×1024, Euler/sgm_uniform, eight steps, CFG1 and tiled VAE decoding. Review the model license before use. There is no automatic weight download or silent fallback.

Upgrade from0.4: install the SDXL checkpoint before selecting it. `SASHA_WORKBENCH_IMAGE_MODEL=sdxl-lightning` is the new default; set it to `sd15` explicitly to keep the legacy economical baseline. Generation still requires `SASHA_WORKBENCH_COMFY_URL`. English descriptions work directly. To translate Russian descriptions locally, optionally set `SASHA_WORKBENCH_MEDIA_TRANSLATOR` to an **already resident** Ollama model and `SASHA_WORKBENCH_MEDIA_OLLAMA_URL` to its endpoint. The service verifies residency, preserves the resident context length and keepalive, uses a bounded request, and shows the translated prompt in the answer. It does not cold-load a translator into occupied memory. Without a resident translator, use English descriptions; failures are explicit.

SDXL is a clear improvement over the earlier SD1.5 baseline on tested samples, not a claim of parity with top cloud image models. Complex hands, exact text and multi-subject composition still need review. The five-second smooth zoom is still a separate non-neural animation; Wan video remains disconnected.

## 0.6.0: reference and local mask editing

Attach one PNG/JPEG/WebP and send `/image description` (or `/edit description`) for SDXL img2img. For a masked edit, attach a second same-sized black/white PNG named `mask.png`: white edits, black preserves. Use Images and video → selected area for the touch brush, strength, preview, or attach both files to the chat. Generated chat images include an Edit area button. Original dimensions are preserved; outside-mask decoded RGB pixels are unchanged exactly. This is masked SDXL img2img with crop context, not a dedicated face-identity or multi-reference model. One reference, up to16MiB/4096px/16MP. Source/mask are owner-private; unknown images/URLs and arbitrary workflows are rejected. Pillow12.0.0 is required in the bridge venv.

## Model choice before creative requests (0.6.1)

Explicit image generation, reference/mask edits and document creation/editing in the chat open a model dropdown before execution. The available local model is recommended automatically; the circled question mark explains the recommendation and limitations. Canceling starts no provider request. The same confirmation is used by the image editor and simple image animation. Documents use a selected text/CLI model; a text-only API model cannot promise file tools.

Qwen Image Edit, FLUX.1 Kontext dev and LaMa are informational, unavailable entries, not bundled engines. No new weights are installed by this release. SDXL img2img does not preserve face identity or reliably turn a selfie into a complex scene. LaMa is a non-diffusion masked object-removal/background-fill option, not a general prompt-based editor. Exact unselected RGB pixels remain protected by patch compositing in the existing masked editing path. Neural video is still unconfigured; existing MP4 animation is only a camera pan/zoom.

## FLUX.2 klein local generation and instruction editing (0.7.0)

A real low-memory recipe uses the distilled FLUX.2 klein 4B Q4_0 model, Qwen3-4B Q4_K_M **text encoder**, and Flux2 VAE. This is not the 20B Qwen-Image-Edit model. FLUX renders a new scene conditioned on a reference rather than applying SDXL img2img noise to the original selfie. Face identity, exact geography and perfect hands/text are not guaranteed.

Use a recent ComfyUI with Flux2 support and a compatible ComfyUI-GGUF loader. Tested engine: ComfyUI `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24`, GGUF loader `RealRebelAI/ComfyUI-GGUF_KREA-2` at `3b4c20dcfed26d2bdce3cef62c09e92221c47973`. Install pinned public weights outside git:

```sh
python custom/install-flux-klein.py --models-dir /path/to/ComfyUI/models
export SASHA_WORKBENCH_COMFY_URL=http://127.0.0.1:8188
export SASHA_WORKBENCH_IMAGE_MODEL=flux-klein4b
```

The installer verifies every SHA256 from [`models/flux-klein-4b.json`](models/flux-klein-4b.json); it does not install packages, start services or download a model implicitly through a chat request. The standalone default stays SDXL until you opt in.

The recipe was measured on RTX 4060 8GB with other GPU services running, total24GB system RAM, ComfyUI `--lowvram --reserve-vram 1.0`, an 8GiB generator RAM cap and at most4CPU cores. Text512 cold and768 warm each took about37s on that machine; a512 reference jacket recolor took39s. A masked edit took44s and changed0RGB pixels outside the mask. These are individual samples, not universal speed or quality promises. Mask changes are cropped with context and composited exactly onto selected original pixels; an incomplete selection can leave the unselected parts of an object unchanged.


## Password recovery (optional administrator setup)

Version 0.7.1 adds **Забыли пароль?** to the native sign-in page. Delivery is disabled until configured. Set `SASHA_WORKBENCH_PASSWORD_RESET_CONFIG` to a private JSON file (0600), and restart the standalone bridge after changing it. The administrator must map existing native Open WebUI user IDs to their verified recovery destination; supplying an email address or choosing Telegram in the form cannot create a binding.

Example SMTP-only configuration (replace placeholders locally):

```json
{
  "webui_db": "/absolute/path/to/shared-webui-data/webui.db",
  "state_db": "/absolute/path/to/private/password-reset.db",
  "accounts": {"EXISTING_WEBUI_USER_ID": {"email_enabled": true}},
  "smtp": {
    "host": "smtp.example.com",
    "port": 465,
    "from": "workbench@example.com",
    "username": "workbench@example.com",
    "password_file": "/absolute/path/to/private/smtp-password"
  }
}
```

`webui_db` must refer to the **same database used by the running WebUI container**, accessible for writing by the ordinary bridge user, including SQLite WAL files. The default Compose named volume is not automatically exposed to the host bridge; configure a shared bind mount and matching permissions if enabling recovery. Do not copy an unrelated database or run the bridge as root to bypass permissions. Existing installations remain unchanged until configured.

The optional AI Hub Telegram adapter also requires explicit `hub_db`, `telegram_token_file`, and `accounts[WEBUI_USER_ID].hub_user_id`. It sends only to the unique private Telegram identity already bound to that enabled Hub user, never to a chat ID entered in the reset form. No Hub database, bot token, SMTP account, or owner mapping is included here.

Recovery codes expire in 10 minutes, are stored hashed, and are one-use. Requests have IP/email rate limits and the same response for known and unknown accounts. Successful recovery replaces the password, revokes native session stamps, clears transient MFA challenges, and preserves the MFA secret. SMTP uses verified implicit TLS. For managed/OAuth-only accounts, use the identity provider's recovery flow.

Phone JPEG references now accept up to 16 MiB / 64 MiPixels / 16384 pixels per side. Original bytes and EXIF orientation survive sidebar upload; the model still receives a bounded 768 px FLUX or 1024 px SDXL copy. These input limits do not promise improved face likeness or unlimited output size.


## Composio and Claude in Chrome

Composio is available under **Plugins and skills → Services** for all supported models through the shared connector layer. Supply your own project API key and explicit toolkit list, run the health check, then use `connect` to authorize each app. No accounts, keys or subscriptions are bundled. Native Codex/Claude can execute an explicit read-only tool allowlist through MCP; general actions require exact single-use confirmation in the service UI. Text-only API models use the existing result-to-draft workflow. See [COMPOSIO.md](COMPOSIO.md). Instant purchases, remote Workbench and proxy execution are disabled.

The **Claude in Chrome** setup card provides official installation links and honest readiness checks. On the same desktop machine as Claude Code, run `python3 custom/install-claude-chrome-host.py`, install the official Chrome extension and sign in, then run `claude-chrome` and verify `/chrome`. This does not install the browser extension, authorize sites or connect a remote computer automatically. It is a Claude-specific integration; other models do not inherit Claude browser access. The installer preserves existing launchers and browser profiles.

## Optional local applications (0.9.0)

VoiceStudio and OpenMuse can run as separate local applications opened from the Workbench sidebar. [LOCAL-MODULES.md](LOCAL-MODULES.md) describes explicit owner mapping, TLS/network configuration and the optional pinned installers. No application, model, voice, service key or user data is silently enabled or bundled.

The tested VoiceStudio profile is local Russian Piper CPU TTS with playback and WAV export; cloning, ASR and neural dubbing are not installed. OpenMuse uses a local Ollama model with one server-persisted main conversation, tasks and files; cloud Rich Threads/search/browser/computer services are disabled. Each upstream application's own license remains in its deployment directory; VoiceStudio and its separate integration files are AGPL-3.0-only.
