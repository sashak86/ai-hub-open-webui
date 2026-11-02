import {installExtensionsUI} from "/ai-hub/extensions-ui.js";
// Native OpenWebUI queue persistence; composer settings are installed separately.
// Queue content remains local to the authenticated account and current origin.
export function persistentQueue(writable, userStore) {
  installExtensionsUI(userStore);
  const raw = writable({});
  let owner = null;
  let memory = {};
  let storageFailed = false;
  function warning() {
    setTimeout(()=>{let banner=document.getElementById('ai-hub-queue-warning');if(!banner){banner=document.createElement('div');banner.id='ai-hub-queue-warning';banner.setAttribute('role','alert');banner.style.cssText='position:fixed;bottom:12px;left:12px;right:12px;z-index:9999;padding:12px;background:#78350f;color:white;border-radius:12px';document.body?.append(banner);}banner.textContent='Не удалось сохранить очередь в браузере. Не закрывайте эту вкладку до отправки сообщений.';},0);
  }
  function compact(item) {
    return {...item,files:(item.files??[]).map(file=>{const id=file.id??file.file?.id;const out={};for(const k of ['id','itemId','name','type','content_type','size','status','error','url','collection_name'])if(file[k]!==undefined)out[k]=file[k];if(id){out.id=id;if(typeof out.url==='string'&&/^(data:|blob:)/.test(out.url))out.url='/api/v1/files/'+encodeURIComponent(id)+'/content';if(file.file)out.file={id,meta:file.file.meta?{name:file.file.meta.name,content_type:file.file.meta.content_type,size:file.file.meta.size}:undefined};}return out;})};
  }
  const prefix = () => `ai-hub:pending:v1:${encodeURIComponent(owner)}:`;
  const key = (chat, id) => prefix() + encodeURIComponent(chat) + ':' + encodeURIComponent(id);
  function load(restoring = false) {
    const result = {};
    if (storageFailed) return memory;
    if (!owner) return result;
    try { for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k?.startsWith(prefix())) continue;
      try {
        const record = JSON.parse(localStorage.getItem(k));
        if (record.owner !== owner || !record.item?.id || typeof record.chat !== 'string') continue;
        if (restoring) for (const file of record.item.files ?? []) if (file.status === 'uploading') {
          file.status = 'error';
          file.error = 'Загрузка прервана обновлением страницы. Измените сообщение и прикрепите файл повторно.';
          record.item.error = file.error;
          localStorage.setItem(k, JSON.stringify(record));
        }
        (result[record.chat] ??= []).push(record.item);
      } catch { /* Unrelated/malformed entry cannot become an outgoing message. */ }
    }
    } catch {storageFailed=true;warning();return memory;}
    for (const items of Object.values(result)) items.sort((a,b) => (a.hubQueuedAt ?? 0) - (b.hubQueuedAt ?? 0));
    return result;
  }
  function persist(next, previous) {
    if (!owner) return;
    // Separate item keys avoid overwriting concurrent additions from another tab.
    try { for (const [chat, items] of Object.entries(previous)) {
      const kept = new Set((next[chat] ?? []).map(item => item.id));
      for (const item of items) if (!kept.has(item.id)) localStorage.removeItem(key(chat,item.id));
    }
    for (const [chat, items] of Object.entries(next)) for (const item of items) {
      item.hubQueuedAt ??= Date.now();
      localStorage.setItem(key(chat,item.id), JSON.stringify({owner,chat,item:compact(item)}));
    } storageFailed=false;document.getElementById('ai-hub-queue-warning')?.remove(); } catch {storageFailed=true;warning();}
  }
  const result = {
    subscribe: raw.subscribe,
    set(next) { const previous=load();persist(next,previous);memory=next;raw.set(next); },
    update(fn) { const previous=load();const next=fn(previous);persist(next,previous);memory=next;raw.set(next); },
  };
  userStore.subscribe(user => {const next=user?.id ?? null;if(next===owner)return;owner=next;memory={};storageFailed=false;memory=load(true);raw.set(memory);});
  addEventListener('storage',event => {if (owner && event.key?.startsWith(prefix())) raw.set(load());});
  async function lock(chat, callback) {
    const act=async()=>{raw.set(load());return callback();};
    if (!owner) return;
    if (navigator.locks) return navigator.locks.request(`ai-hub:pending:${owner}:${chat}`, {mode:'exclusive'},act);
    // Stable hubq IDs also deduplicate at the durable server if Web Locks is absent.
    return act();
  }
  window.__hubPendingQueue = {lock};
  return result;
}
