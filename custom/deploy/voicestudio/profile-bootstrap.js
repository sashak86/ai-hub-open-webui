// The hosted CPU module is provisioned by the administrator with a real local
// Piper model. Skip the desktop-only model-pack installer, never enable analytics.
try {
  localStorage.setItem('voicestudio.setup.complete.v1', '1');
  localStorage.removeItem('voicestudio.setup.in-progress.v1');
  if (!localStorage.getItem('i18nextLng')) localStorage.setItem('i18nextLng', 'ru');
} catch (_) {}

// Upstream's Save as button is Electron-only. A browser receives the same
// generated WAV through its own authenticated origin; offer a native download.
function syncBrowserDownload() {
  const audio = document.querySelector('audio');
  const source = audio && (audio.currentSrc || audio.src);
  let link = document.querySelector('[data-ai-hub-download]');
  if (!source) { if (link) link.hidden = true; return; }
  let url;
  try { url = new URL(source, location.href); } catch (_) { return; }
  if (!((url.origin === location.origin && ['http:', 'https:', 'blob:'].includes(url.protocol)))) return;
  if (!link) {
    link = document.createElement('a');
    link.dataset.aiHubDownload = '1';
    link.textContent = 'Скачать WAV';
    link.style.cssText = 'display:inline-flex;align-items:center;min-height:36px;padding:6px 10px;border:1px solid #888;border-radius:8px;font:inherit;font-size:12px;text-decoration:none;color:inherit;white-space:nowrap';
    const section = audio.closest('section') || audio.parentElement;
    const native = section && [...section.querySelectorAll('button')].find(x => x.disabled && /Save as|Сохранить/.test(x.textContent || ''));
    if (native) native.insertAdjacentElement('afterend', link);
    else if (section) section.append(link);
  }
  link.href = url.href;
  link.download = 'voicestudio-piper.wav';
  link.hidden = false;
}
function enableBrowserDownload() {
  new MutationObserver(syncBrowserDownload).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['src']});
  document.addEventListener('loadedmetadata', syncBrowserDownload, true);
  syncBrowserDownload();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', enableBrowserDownload, {once:true});
else enableBrowserDownload();

// AGPL network users can obtain the exact upstream and this local adaptation.
function exposeVoiceStudioSources() {
  if (document.querySelector('[data-ai-hub-voice-sources]')) return;
  const source = document.createElement('a');
  source.dataset.aiHubVoiceSources = '1';
  source.href = 'https://github.com/sashak86/ai-hub-open-webui/tree/sasha-harness-v0.9.0/custom/deploy/voicestudio';
  source.target = '_blank'; source.rel = 'noopener noreferrer';
  source.textContent = 'Исходники VoiceStudio (AGPL)';
  source.title = 'Точный upstream, локальные изменения, лицензия и воспроизведение установки';
  source.style.cssText = 'position:fixed;bottom:4px;right:6px;z-index:1000;max-width:180px;padding:4px 7px;border:1px solid #8886;border-radius:7px;background:#f8f8f8;color:#333;font:10px/1.3 system-ui;text-decoration:underline;opacity:.9';
  document.body.append(source);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', exposeVoiceStudioSources, {once:true});
else exposeVoiceStudioSources();
