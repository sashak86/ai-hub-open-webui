// Module applications run on separate origins; no chat JWT is sent to them.
export async function renderLocalModules({content,api,json,message,isCurrent}){
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
 const data=await api('local-modules');if(!isCurrent())return;
 content.replaceChildren(el('h3','Локальные модули'),el('p','VoiceStudio и OpenMuse открываются в отдельной вкладке. Этот чат остаётся на месте; чтобы вернуться, переключитесь на его вкладку.'));
 if(!data.modules?.length)content.append(el('p','Локальные модули ещё не настроены на этом сервере.'));
 for(const item of data.modules||[]){
  const card=el('section',null,'hubh-card');card.dataset.localModule=item.id;
  card.append(el('h3',item.name),el('p',item.description),el('p',item.available?'Работает локально':'Запускается или недоступен','hubh-badge'));
  if(item.notice)card.append(el('p',item.notice,'hubh-muted'));
  const open=el('button','Открыть '+item.name,'hubh-button hubh-primary');open.disabled=!item.available;
  open.onclick=async()=>{const tab=window.open('about:blank','_blank');if(tab)tab.opener=null;open.disabled=true;try{const r=await api('local-modules/'+encodeURIComponent(item.id)+'/open',json('POST',{}));const url=new URL(r.url);if(url.protocol!=='https:'||url.hostname!==location.hostname||url.port!==String(item.port))throw Error('Неверный адрес локального модуля.');if(tab)tab.location.replace(url.href);else{const link=el('a','Открыть '+item.name,'hubh-button');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';card.append(link);message('Нажмите ссылку: браузер заблокировал новую вкладку.');}}catch(e){tab?.close();message(e.message,true);}finally{open.disabled=false;}};
  card.append(open);content.append(card);
 }
 const close=el('button','Завершить доступ к открытым модулям','hubh-button');close.onclick=async()=>{close.disabled=true;try{await api('local-modules/close',json('POST',{}));message('Доступ к модулям завершён. Для нового входа откройте модуль из этого окна.');}catch(e){message(e.message,true);}finally{close.disabled=false;}};content.append(close);
}
