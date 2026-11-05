// Local generation, authenticated assets and a durable owner gallery.
let urls=[],timer;
const el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
function chatFileInput(){return [...document.querySelectorAll('input[type=file]')].find(e=>!e.closest('#ai-hub-harness-dialog')&&e.id!=='camera-input'&&(!e.accept||e.accept.includes('image')));}
function button(text,fn){const b=el('button',text);b.type='button';b.className='hubh-button';b.onclick=fn;return b;}
export function cleanupMedia(){clearTimeout(timer);for(const u of urls)URL.revokeObjectURL(u);urls=[];}
async function imageFile(id){const r=await fetch('/ai-hub/api/media/'+id+'/file',{headers:{Authorization:'Bearer '+localStorage.token},credentials:'same-origin'});if(!r.ok)throw Error('Не удалось загрузить результат');const type=r.headers.get('content-type')||'image/png';return new File([await r.blob()],'Sasha-'+id.slice(0,8)+(type==='video/mp4'?'.mp4':'.png'),{type});}
export async function renderMedia({content,api,json,message,chatId,isCurrent}){
 cleanupMedia();const state=await api('media');if(!isCurrent())return;
 const intro=el('p','Локальная генерация · Sasha AI Workbench. Можно прямо в чате: «Нарисуй…», «Создай изображение…» или /image … — результат появится в беседе. Галерея сохраняется после обновления страницы.');content.append(intro);
 const form=el('form'),label=el('label','Модель'),select=el('select');select.name='media_model';label.className='hubh-field';label.append(select);
 for(const model of state.models.filter(m=>m.kind==='image')){const option=el('option',model.name+(model.available?'':' — не подключено'));option.value=model.id;option.disabled=!model.available;select.append(option);}
 const available=state.models.find(m=>m.id===state.default_model&&m.available)||state.models.find(m=>m.kind==='image'&&m.available);if(available)select.value=available.id;
 let editorReady=false;const notes=el('p'),describe=()=>{notes.textContent=state.models.find(m=>m.id===select.value)?.notes||'';if(editorReady)syncMode();};select.onchange=describe;describe();
 const promptLabel=el('label','Что нарисовать'),prompt=el('textarea');prompt.name='media_prompt';prompt.rows=4;prompt.maxLength=1000;prompt.required=true;prompt.placeholder='Например: уютная мастерская с роботом, мягкий свет, акварель';promptLabel.className='hubh-field';promptLabel.append(prompt);
 const seedLabel=el('label','Seed (пусто — новый вариант)'),seed=el('input');seed.type='number';seed.min='0';seed.max='4294967295';seed.name='media_seed';seedLabel.className='hubh-field';seedLabel.append(seed);
 const generate=button('Создать изображение',()=>{});generate.type='submit';generate.disabled=!available;

 const strength=el('input');strength.type='range';strength.min='0.1';strength.max='1';strength.step='0.05';strength.value='0.65';strength.name='media_strength';strength.setAttribute('aria-label','Сила изменения');const strengthLabel=el('label','Сила изменения · меньше сохраняет исходник, больше меняет');strengthLabel.append(strength);
 const mode=el('select');mode.name='media_mode';for(const [value,text] of [['text','По описанию'],['reference','По референсу · img2img'],['inpaint','Изменить выделенную область']]){const o=el('option',text);o.value=value;mode.append(o);}
 const upload=el('input');upload.type='file';upload.accept='image/png,image/jpeg,image/webp';upload.name='media_reference';
 const editor=el('div'),stage=el('div'),preview=el('canvas'),mask=el('canvas'),brush=el('input');let source64=null,sourceName='reference.png',drawing=false,last=null;
 stage.style.cssText='position:relative;max-width:100%;width:fit-content;touch-action:none';preview.style.cssText='display:block;max-width:100%;height:auto';mask.style.cssText='position:absolute;inset:0;width:100%;height:100%;opacity:.45;touch-action:none';stage.append(preview,mask);brush.type='range';brush.min='4';brush.max='160';brush.value='40';brush.setAttribute('aria-label','Размер кисти');
 const hint=el('p','Один референс на запрос. Img2img использует композицию исходника, но не гарантирует сохранение личности. Для локальной правки закрасьте область: за её пределами пиксели сохраняются точно. Результат — PNG исходного размера.');
 const clear=button('Очистить выделение',()=>mask.getContext('2d').clearRect(0,0,mask.width,mask.height));
 const file64=file=>new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});
 const setSource=async file=>{if(!file||file.size>16*1024*1024)throw Error('Выберите изображение до 16 МБ');const bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});if(Math.max(bitmap.width,bitmap.height)>4096||bitmap.width*bitmap.height>16777216){bitmap.close();throw Error('Максимум 4096 пикселей и 16 мегапикселей');}preview.width=mask.width=bitmap.width;preview.height=mask.height=bitmap.height;preview.getContext('2d').drawImage(bitmap,0,0);bitmap.close();source64=preview.toDataURL('image/png').split(',')[1];sourceName='reference.png';if(mode.value==='text')mode.value='reference';syncMode();};
 upload.onchange=async()=>{try{await setSource(upload.files[0]);}catch(e){message(e.message,true);}};
 const point=e=>{const r=mask.getBoundingClientRect();return[(e.clientX-r.left)*mask.width/r.width,(e.clientY-r.top)*mask.height/r.height];};
 const stroke=e=>{if(!drawing||mode.value!=='inpaint')return;e.preventDefault();const pt=point(e),ctx=mask.getContext('2d');ctx.strokeStyle='white';ctx.fillStyle='white';ctx.lineWidth=Number(brush.value)*mask.width/512;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo(...(last||pt));ctx.lineTo(...pt);ctx.stroke();ctx.beginPath();ctx.arc(...pt,ctx.lineWidth/2,0,Math.PI*2);ctx.fill();last=pt;};
 mask.onpointerdown=e=>{drawing=true;last=null;mask.setPointerCapture(e.pointerId);stroke(e);};mask.onpointermove=stroke;mask.onpointerup=mask.onpointercancel=()=>{drawing=false;last=null;};
 function syncMode(){editor.hidden=mode.value==='text';mask.hidden=mode.value!=='inpaint';brush.hidden=clear.hidden=mode.value!=='inpaint';const instruction=!!state.models.find(m=>m.id===select.value)?.instruction_edit;strengthLabel.hidden=instruction;hint.textContent=instruction?'FLUX редактирует содержимое референса по инструкции и может перестроить сцену. Точное сходство лица не гарантируется. Ползунок силы img2img здесь не применяется. Для правки области закрасьте участок: снаружи выделения пиксели сохраняются точно.':'Один референс на запрос. Img2img использует композицию исходника, но не гарантирует сохранение личности. Для локальной правки закрасьте область: за её пределами пиксели сохраняются точно. Результат — PNG исходного размера.';}mode.onchange=()=>{strength.value=mode.value==='inpaint'?'0.85':'0.65';syncMode();};
 function maskData(){const output=document.createElement('canvas');output.width=mask.width;output.height=mask.height;const ctx=output.getContext('2d');ctx.fillStyle='black';ctx.fillRect(0,0,output.width,output.height);ctx.drawImage(mask,0,0);return output.toDataURL('image/png').split(',')[1];}
 const attachEdit=button('Добавить референс и маску в чат',async()=>{try{if(!source64)throw Error('Загрузите исходник');const input=chatFileInput();if(!input)throw Error('Откройте + → Загрузить файлы и повторите');const dt=new DataTransfer();const make=(data,name)=>new File([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],name,{type:'image/png'});dt.items.add(make(source64,sourceName));if(mode.value==='inpaint')dt.items.add(make(maskData(),'mask.png'));input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('ai-hub-harness-dialog')?.close();message('Вложения добавлены. Напишите /image и описание изменения, затем отправьте.');}catch(e){message(e.message,true);}});
 editor.append(upload,hint,strengthLabel,stage,el('label','Размер кисти'),brush,clear,attachEdit);form.append(label,notes,mode,editor,promptLabel,seedLabel,generate);content.append(form);editorReady=true;syncMode();
 if(window.__hubMediaEditSource){const path=window.__hubMediaEditSource;delete window.__hubMediaEditSource;if(/^\/api\/v1\/files\/[^/]+\/content$/.test(path)){try{const r=await fetch(path,{headers:{Authorization:'Bearer '+localStorage.token},credentials:'same-origin'});if(!r.ok)throw Error('Изображение недоступно');await setSource(new File([await r.blob()],'reference.png',{type:'image/png'}));mode.value='inpaint';strength.value='0.85';syncMode();}catch(e){message(e.message,true);}}}

 const video=state.models.filter(m=>m.kind==='video');for(const m of video){const box=el('section');box.className='hubh-card';box.append(el('h3',m.name),el('p',m.notes));content.append(box);}
 const gallery=el('div');gallery.id='ai-hub-media-gallery';content.append(el('h3','Мои генерации'),gallery);
 const paint=async(jobs)=>{if(!isCurrent())return;gallery.replaceChildren();for(const j of jobs){const card=el('section');card.className='hubh-card';card.dataset.mediaId=j.id;const title=el('h3',j.model==='image-motion'?'Анимация картинки · плавный наезд (без ИИ-движения)':state.models.find(m=>m.id===j.model)?.name||j.model),status=el('p',j.message),description=el('p',j.prompt);description.style.whiteSpace='pre-wrap';card.append(title,status,description);
 const end=j.finished_at||Date.now()/1000,start=j.started_at||j.created_at,clock=el('small',new Date(j.created_at*1000).toLocaleString()+' · '+Math.max(0,Math.round(end-start))+' с'+(j.status==='completed'?' · '+j.width+'×'+j.height:''));card.append(clock);
 if(j.status==='completed'){try{const file=await imageFile(j.id);if(!isCurrent())return;const url=URL.createObjectURL(file);urls.push(url);const isVideo=file.type==='video/mp4';const img=el(isVideo?'video':'img');img.src=url;if(isVideo){img.controls=true;img.preload='metadata';}else img.alt=j.prompt;img.style.cssText='display:block;max-width:100%;height:auto;border-radius:10px;margin:10px 0';const download=el('a',isVideo?'Скачать MP4':'Скачать PNG');download.href=url;download.download=file.name;download.className='hubh-button';const attach=button('Добавить к запросу',async()=>{try{const input=chatFileInput();if(!input)throw Error('Откройте «+ → Загрузить файлы», затем повторите добавление. Можно также скачать PNG и приложить его вручную.');const dt=new DataTransfer();dt.items.add(file);input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('ai-hub-harness-dialog')?.close();message('Изображение добавлено во вложения. Проверьте запрос перед отправкой.');}catch(e){message(e.message,true);}});card.append(img,download,attach);if(!isVideo)card.append(button('Редактировать область',async()=>{try{await setSource(file);mode.value='inpaint';strength.value='0.85';syncMode();form.scrollIntoView({block:'start'});}catch(e){message(e.message,true);}}));if(!isVideo&&state.animation_available)card.append(button('Сделать видео: плавный наезд',async()=>{try{const chosen=await chooseCreativeModel({kind:'video',mode:'animation'},{catalog:{models:[{id:'image-motion',kind:'video',name:'Плавный наезд · без ИИ-движения',available:true,notes:'FFmpeg: 5 секунд, движение камеры по готовому изображению. Новые объекты и движения людей не генерируются.'}],default_model:'image-motion'}});if(!chosen)return;await api('media/'+j.id+'/animate',json('POST',{}));await poll();}catch(e){message(e.message,true);}}));}catch(e){card.append(el('p',e.message));}}
 if(['queued','running','submitting'].includes(j.status)){const cancel=button('Отменить ожидание',async()=>{try{await api('media/'+j.id+'/cancel',json('POST',{}));await poll();}catch(e){message(e.message,true);}});card.append(cancel);}
 const repeat=button('Изменить описание',()=>{prompt.value=j.prompt;select.value=j.model;describe();prompt.focus();});if(j.model!=='image-motion')card.append(repeat);gallery.append(card);}
 if(!jobs.length)gallery.append(el('p','Пока нет генераций.'));};
 const poll=async()=>{if(!isCurrent())return;try{const data=await api('media');const jobs=await Promise.all(data.jobs.map(async j=>['queued','running','submitting'].includes(j.status)?await api('media/'+j.id):j));cleanupMedia();await paint(jobs);if(isCurrent()&&jobs.some(j=>['queued','running','submitting'].includes(j.status)))timer=setTimeout(poll,4000);}catch(e){if(isCurrent())message(e.message,true);}};
 form.onsubmit=async e=>{e.preventDefault();generate.disabled=true;try{const data={model:select.value,prompt:prompt.value,chat_id:chatId()};if(seed.value!=='')data.seed=Number(seed.value);data.mode=mode.value;data.strength=Number(strength.value);if(mode.value!=='text'){if(!source64)throw Error('Загрузите референс');data.source_base64=source64;if(mode.value==='inpaint')data.mask_base64=maskData();}const chosen=await chooseCreativeModel({kind:'image',mode:mode.value},{catalog:{...state,default_model:select.value}});if(!chosen)return;data.model=chosen;select.value=chosen;describe();data.client_request_id=crypto.randomUUID();await api('media',json('POST',data));message('Задание принято. Можно закрыть окно и вернуться позже.');await poll();}catch(e){message(e.message,true);}finally{generate.disabled=!available;}};
 await paint(state.jobs);if(state.jobs.some(j=>['queued','running','submitting'].includes(j.status)))timer=setTimeout(poll,2000);
}

// Preflight is client-side only: no model/job call happens until explicit confirmation.
// Intercept only this fork's current-user completion; tools/title requests are untouched.
export function classifyCreativeRequest(body){
 const supplied=body.user_message||body.parent_message;const latest=supplied&&(!supplied.role||supplied.role==='user')?supplied:[...(body.messages||[])].reverse().find(m=>m.role==='user');if(!latest)return null;
 const parts=Array.isArray(latest.content)?latest.content:[],text=typeof latest.content==='string'?latest.content:parts.filter(p=>p.type==='text').map(p=>p.text||'').join('\n');
 const files=[...(body.files||[]),...(latest.files||[])];
 const image=parts.some(p=>p.type==='image_url')||files.some(f=>f.type==='image'||(f.mime||f.meta?.content_type||f.file?.meta?.content_type||'').startsWith('image/')||/\.(png|jpe?g|webp)$/i.test(f.name||f.filename||f.file?.filename||''));
 const mask=files.some(f=>(f.name||f.filename||f.file?.filename||'').toLowerCase()==='mask.png');
 const command=text.trim().replace(/^пожалуйста[, ]+/i,'');
 if(/^(?:\/video\s|(?:создай|сгенерируй|сделай|отредактируй|измени|исправь)\s+(?:видео|ролик|анимацию))/i.test(command))return {kind:'video',mode:'video',text};
 const documentFile=files.some(f=>/\.(pdf|docx?|odt|xlsx?|csv|pptx?|txt|md)$/i.test(f.name||f.filename||f.file?.filename||''));
 if(/^(?:создай|подготовь|сделай|напиши|отредактируй|измени|исправь|дополни|перепиши)\s+(?:документ|презентацию|таблицу|отч[её]т|резюме|pdf|docx|xlsx)(?:\s|$|[.,:])/i.test(command)||documentFile&&/^(?:отредактируй|измени|исправь|дополни|перепиши)\s/i.test(command))return {kind:'document',mode:'document',text};
 if(/^(?:\/(?:image|edit)(?:\s+|$)|(?:нарисуй(?:те)?|(?:создай(?:те)?|сгенерируй(?:те)?)\s+(?:изображение|картинку|рисунок|фото))\s*[:,—-]?\s+)[\s\S]+$/i.test(command)||image&&/^(?:измени|исправь|отредактируй|замени|перерисуй|убери|добавь)\s/i.test(command))return {kind:'image',mode:mask?'inpaint':image?'reference':'text',text};
 return null;
}
function modelExplanation(model,context,recommended){
 const why=model.instruction_edit&&context.kind==='image'?(context.mode==='inpaint'?'Выбран генератор с редактированием по инструкции. Он обрабатывает выделение с контекстом, а итог накладывается только по маске; остальные пиксели сохраняются точно.':context.mode==='reference'?'Эта модель умеет смысловое редактирование референса и построение новой сцены по инструкции. Поэтому она предпочтительнее SDXL img2img для замены окружения, поз и состава сцены. Точное сходство лица не гарантируется.':'Выбран подключённый FLUX klein с более точным следованием составу и действиям сцены в проверенных примерах. Это не гарантия безошибочных лиц, рук, надписей или географии.'):
 context.kind==='document'?(model.capabilities?.tools===false?'Текстовая модель может подготовить содержимое. Создание файла и правка исходного файла не гарантируются: у этой модели нет файловых инструментов.':model.capabilities?.tools!==true?'Возможности файловых инструментов не подтверждены. Модель может подготовить текст документа; готовый файл не гарантируется.':'Агент с инструментами подходит для анализа и подготовки файлов. Проверяйте сформированный документ; оригинал не следует перезаписывать без необходимости.'):
 context.mode==='animation'?'Выбрана простая анимация существующей картинки, без нейрогенерации движения.':context.kind==='video'?'Нейрогенерация видео сейчас не подключена. Плавный наезд в галерее — отдельная анимация готовой картинки, без ИИ-движения.':
 context.mode==='inpaint'?'Для этой модели используется img2img с маской. Точное сохранение остальных пикселей обеспечивает обратное наложение патча, а не сама модель. Качество сложной правки ограничено.':
 context.mode==='reference'?'Это доступная локальная модель для одного референса. Она сохраняет композицию лучше, чем личность. Для нового ракурса, двух людей и узнаваемых лиц результат может не соответствовать запросу.':'Для рисунка выбран доступный локальный генератор. Рекомендация учитывает подключение и режим, а не обещает лучший результат на любом запросе. Сложная композиция, надписи и руки могут получаться неточно.';
 return (model.notes||model.reason||model.name||'')+'\n\n'+(model.available?'Почему '+(recommended?'рекомендуется сейчас':'подходит или ограничена')+': '+why:'Не подключена: запуск недоступен. Это вариант для дальнейшей настройки, а не скрытая замена выбранной модели.');
}
export async function chooseCreativeModel(context,{catalog,models=[],current='',signal}={}){
 if(signal?.aborted)throw new DOMException('Aborted','AbortError');
 const rows=context.kind==='document'?models:(catalog?.models||[]).filter(m=>m.kind===context.kind);
 const suitable=rows.filter(m=>m.available&&m.connected!==false&&(context.kind!=='image'||context.mode==='text'||m.modes?.includes(context.mode)||['sdxl-lightning','sd15'].includes(m.id)));
 const recommended=context.kind==='document'?(suitable.find(m=>m.id===current&&m.capabilities?.tools===true)||suitable.find(m=>m.capabilities?.tools===true)||suitable.find(m=>m.id===current)||suitable[0]):suitable.find(m=>m.id===catalog?.default_model)||suitable[0];
 return new Promise((resolve,reject)=>{
  const d=el('dialog');d.id='ai-hub-creative-preflight';d.className='hubh-dialog';d.setAttribute('aria-labelledby','hub-media-choice-title');
  const head=el('header');head.className='hubh-head';const title=el('h2',context.kind==='document'?'Модель для документа':context.kind==='video'?'Модель для видео':context.mode==='text'?'Модель для изображения':context.mode==='inpaint'?'Модель для правки области':'Модель для референса');title.id='hub-media-choice-title';head.append(title);
  const panel=el('div');panel.className='hubh-content';const label=el('label','Выберите модель');label.className='hubh-field';const select=el('select');select.id='hub-media-choice-model';label.append(select);
  for(const row of rows){const o=el('option',row.name+(row.available?'':' — недоступна')+(row.id===recommended?.id?' · рекомендуется':''));o.value=row.id;select.append(o);}
  if(recommended)select.value=recommended.id;
  const row=el('div');row.className='hubh-row';const help=button('?',()=>{description.hidden=!description.hidden;help.setAttribute('aria-expanded',String(!description.hidden));});help.classList.add('hubh-help-button');help.id='hub-media-choice-help';help.setAttribute('aria-label','Описание модели и причина рекомендации');help.setAttribute('aria-expanded','false');help.setAttribute('aria-controls','hub-media-choice-description');
  const description=el('div');description.id='hub-media-choice-description';description.className='hubh-card';description.hidden=true;description.style.whiteSpace='pre-wrap';description.setAttribute('role','note');
  const warning=el('p');warning.className='hubh-muted';const launch=button('Запустить',()=>finish(select.value));launch.id='hub-media-choice-start';launch.classList.add('hubh-primary');
  const change=()=>{const model=rows.find(m=>m.id===select.value);description.textContent=model?modelExplanation(model,context,model.id===recommended?.id):'Нет доступных моделей.';launch.disabled=!suitable.some(m=>m.id===select.value);warning.textContent=!model?.available?'Модель не подключена. Генерация не будет запущена.':context.kind==='document'?'Будет выполнен один запрос выбранной модели с текущей историей и вложениями.':context.mode==='animation'?'Будет создан MP4 с плавным наездом, без ИИ-движения.':context.kind==='video'?'Генерация видео пока недоступна.':context.mode==='reference'?(model.instruction_edit?'Редактирование по инструкции; сходство лица и точность деталей не гарантируются.':'Внимание: референс здесь — img2img, без точного сохранения лица.'):context.mode==='inpaint'?'Изменяется только выделение. Исходник сохраняется отдельно.':'Результат зависит от модели. До подтверждения генерация не начинается.';};select.onchange=change;change();
  let done=false;function finish(value){if(done)return;done=true;signal?.removeEventListener('abort',abort);d.close();d.remove();resolve(value);}
  const abort=()=>{if(done)return;done=true;d.close();d.remove();reject(new DOMException('Aborted','AbortError'));};
  d.addEventListener('cancel',e=>{e.preventDefault();finish(null);});d.addEventListener('close',()=>finish(null));signal?.addEventListener('abort',abort,{once:true});
  row.append(help,el('span','Описание и почему выбрана эта модель'));const actions=el('div');actions.className='hubh-row';actions.append(button('Отмена',()=>finish(null)),launch);
  panel.append(el('p','Проверьте модель перед запуском. Рекомендуется только доступный вариант; это не гарантия качества.'),label,row,description,warning);
  if(!suitable.length)panel.append(el('p','Для этой операции нет подключённого генератора. Настройки моделей для обычного чата не подключают автоматически генерацию изображений или видео.'));
  panel.append(actions);d.append(head,panel);document.body.append(d);d.showModal();select.focus();
 });
}
async function creativeAPI(path){const r=await fetch('/ai-hub/api/'+path,{headers:{Authorization:'Bearer '+localStorage.token},credentials:'same-origin'});if(!r.ok)throw Error('Не удалось загрузить модели ('+r.status+'). Запрос не запущен.');return r.json();}
export function installCreativePreflight(){
 if(typeof window==='undefined'||window.fetch.__hubCreativePreflight)return;
 window.__hubCreativeFetchInstalled=true;const nativeFetch=window.fetch.bind(window);let pending=false;
 window.fetch=async(input,init)=>{
  let url;try{url=new URL(typeof input==='string'||input instanceof URL?input:input.url,location.href);}catch{return nativeFetch(input,init);}
  const method=String(init?.method||input?.method||'GET').toUpperCase();
  if(url.origin!==location.origin||url.pathname!=='/api/chat/completions'||method!=='POST')return nativeFetch(input,init);
  let body;try{body=JSON.parse(init?.body??await input.clone().text());}catch{return nativeFetch(input,init);}
  if(typeof body.model!=='string'||!body.model.startsWith('ai_hub.'))return nativeFetch(input,init);
  if(body.hub_creative_confirmed===true)return nativeFetch(input,init);
  const context=classifyCreativeRequest(body);if(!context)return nativeFetch(input,init);
  if(pending)return new Response(JSON.stringify({detail:'Выбор модели уже открыт. Завершите его перед новым запросом.'}),{status:409,headers:{'Content-Type':'application/json'}});
  pending=true;
  try{
   const signal=init?.signal||input?.signal,data=context.kind==='document'?await creativeAPI('harness/models'):await creativeAPI('media');
   const chosen=await chooseCreativeModel(context,{catalog:data,models:data.data,current:body.hub_api_model||body.model.slice(7),signal});
   if(!chosen)return new Response(JSON.stringify({detail:'Запуск отменён пользователем. Генерация не выполнялась.'}),{status:400,headers:{'Content-Type':'application/json'}});
   body.hub_creative_confirmed=true;
   if(context.kind==='image')body.hub_media_model=chosen;else if(context.kind==='document'){body.hub_api_model=chosen;body.params={...body.params,custom_params:{...body.params?.custom_params,hub_api_model:chosen}};}
   return nativeFetch(input,{...init,body:JSON.stringify(body)});
  }catch(e){if(e.name==='AbortError')throw e;return new Response(JSON.stringify({detail:e.message||'Не удалось выбрать модель. Запрос не запущен.'}),{status:400,headers:{'Content-Type':'application/json'}});}
  finally{pending=false;}
 };
 window.fetch.__hubCreativePreflight=true;
}
installCreativePreflight();
