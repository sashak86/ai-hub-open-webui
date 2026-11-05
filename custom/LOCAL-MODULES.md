# Optional local VoiceStudio and OpenMuse applications

These are separate local applications opened from **Local modules** in the Workbench sidebar. They run on isolated HTTPS origins and do not receive the chat's JWT, cookies, or browser identity headers. All modules are **disabled until the operator explicitly configures them**. No models, voices, user data, account sessions, or service keys are included.

Install the optional native applications with the pinned installers in `custom/deploy/voicestudio/` and `custom/deploy/openmuse/`. Follow their READMEs for resource limits, local engine/model selection, upstream licenses and current limitations. Native servers must bind only to `127.0.0.1` and must not be exposed directly to the network.

Create a private mode-0600 file at `~/.config/sasha-workbench/local-modules.json`, or set `SASHA_WORKBENCH_MODULES_CONFIG` to its absolute path. Example (disabled):

```json
{"owners":[],"modules":{},"host":"127.0.0.1","networks":["127.0.0.0/8","::1/128"]}
```

To enable a module, add the SHA256 of its owner's **native Open WebUI user ID** to `owners`; administrator role alone is insufficient. Set `tls_cert` and `tls_key` to the operator's certificate and key files. The certificate must cover the same hostname used to open Workbench. Set `WEBUI_INTERNAL_URL` to the loopback Open WebUI origin. Example module entries:

```json
{
  "voicestudio":{"name":"VoiceStudio","upstream":"http://127.0.0.1:8771","port":8473,"health_path":"/health","description":"Local audio workspace"},
  "openmuse":{"name":"OpenMuse","upstream":"http://127.0.0.1:8797","port":8474,"health_path":"/api/health","secret_header":"X-OpenMuse-Service-Key","secret_file":"/absolute/private/path/openmuse-proxy.token","description":"Local agent workspace"}
}
```

Place these entries inside `modules`, not at the top level. `secret_file` contains only the native service key and must be readable only by the operator/gateway. Do not put the key in JavaScript, environment examples, URLs or chat messages. Choose unused TLS ports. The gateway defaults to loopback-only network access; for LAN/VPN use, explicitly configure `host` and the exact `networks` CIDRs and apply matching firewall rules. Reverse proxies must preserve the public Host/Origin and must not trust client-supplied identity or forwarding headers.

On startup the standalone bridge starts optional TLS listeners in its own process. Launch from **Local modules**: the normal WebUI JWT is validated against native auth and exchanged for an opaque, application-bound one-hour HttpOnly/Secure/SameSite cookie. Each HTTP request revalidates the native session; WebSockets also monitor revocation. All browser Cookie/Authorization/identity headers are stripped upstream. OpenMuse receives only its server-side service key. Native Set-Cookie/CORS headers are not passed through. Ports isolate localStorage from Workbench and from the other module. Application API calls, assets, downloads and WebSockets all require the owner's module session.

Use **End access to open modules** to revoke handles, or sign out from WebUI. Restarting the gateway also invalidates handles; reopen the app from Workbench. A module remains disabled in the UI if its listener or native health check is unavailable. Uploads stream with a 128 MiB bound; generation/SSE responses have no read timeout and are closed when the browser disconnects. The native applications retain their own CPU/memory/service limits.

The built-in OpenMuse local mode stores conversations, tasks and files in its own local data directory, uses a chosen Ollama model and disables cloud Intelligence/search/browser/computer integrations until separately configured. VoiceStudio's constrained local profile exposes the verified lightweight CPU TTS path; heavyweight engines and unsupported operations remain explicitly unavailable. These are practical local configurations, not a claim that all upstream engines or cloud integrations are enabled.
