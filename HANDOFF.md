# Sasha AI Workbench — current state

Version 0.7.1 extends the 0.7.0 FLUX image/reference engine with native password recovery and smartphone photo handling. All customization is under `custom/`; see `custom/README.md` for startup and configuration, and `custom/VERSION.json` for source revisions and checksum manifest.

Phone references accept up to16MiB/64MiPixels/16384px per side. Original compressed bytes and EXIF orientation are retained; the model gets a bounded copy. Mask compositing still preserves decoded source pixels outside selection. Face identity and arbitrary output sizes are not guaranteed.

Password recovery is disabled until an administrator configures the private JSON path. It requires access to the actual native Open WebUI SQLite database and explicit per-user recovery mappings. SMTP uses implicit TLS. Optional AI Hub Telegram recovery requires pre-existing private user bindings and explicit paths, never supplied chat IDs from a public form. No owner credentials, images, account mappings, or runtime databases are published.

Validation: offline suite plus real private installation browser/source checks are recorded in VERSION.json. Run `PYTHONPATH=custom/bridge custom/.venv/bin/python -m pytest custom/tests -q`. Tests never load the installer's recovery configuration. Upstream image remains pinned; this update does not start services or migrate user databases automatically.

Next: administrator setup and provider credentials belong to each installation. Roll back application code using Git and reinstall the matching requirements; preserve all private state and chat databases.
