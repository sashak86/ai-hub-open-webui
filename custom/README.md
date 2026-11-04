# Sasha AI Workbench for Open WebUI

A personal Open WebUI fork with a persistent agent workbench. The upstream application, copyright notices, and Open WebUI name remain present. A monochrome combination of Sasha's avatar and the upstream logo identifies this personal fork on loading, in the sidebar, and in extension settings.

This branch starts from **Open WebUI v0.12.4**, commit `33c1978d5bad52a31df75ea55143025dfad18d16`. Custom integration lives entirely in `custom/`; upstream application sources are retained. This is an independent community customization, not an Open WebUI, Anthropic, OpenAI, or Nous Research product.

## Features

- Native Codex/Claude Code tasks with durable queue, streaming progress, files, cancellation, and stable message IDs.
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
