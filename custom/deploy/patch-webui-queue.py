#!/usr/bin/env python3
"""Reproducible minimal patch for pinned OpenWebUI0.12.4 native pending queue.

Usage: python patch-webui-queue.py INPUT_BUILD_DIR OUTPUT_DIR
Only reviewed pinned JS assets are accepted, never heuristic patching new versions.
Mount OUTPUT/chunks/{files} over corresponding /app/build files and queue-store.js
at /app/build/ai-hub/queue-store.js. No source build or dependency installation.
"""
import hashlib
from pathlib import Path
import sys

STORE = 'CqEczJQs.js'
CHAT = 'CNDyKy5F.js'
EXPECTED = {'CqEczJQs.js': '42633dc8d958121268283a22da0783d767733f82de0d51dcd3c1048d4ac1fa99', 'CNDyKy5F.js': '15c885a1adbd752e11e3805249972a0e4c9443bc06b6bac3bab4cbac7c242892'}


def patch_shell(index):
    if index.count('</body>') != 1:
        raise RuntimeError('Pinned WebUI HTML body anchor changed')
    if 'sasha-fork-shell-script' not in index:
        shell = Path(__file__).with_name('brand-shell.html').read_text()
        index = index.replace('</body>', shell + '\n</body>')
    return index


def patch(source, output):
    source, output = Path(source), Path(output)
    assets = source / '_app/immutable/chunks'
    store = (assets / STORE).read_text()
    chat = (assets / CHAT).read_text()
    for name, data in [(STORE,store),(CHAT,chat)]:
        if hashlib.sha256(data.encode()).hexdigest() != EXPECTED[name]:
            raise RuntimeError('Pinned OpenWebUI asset changed: ' + name)
    def replace(text, before, after):
        if text.count(before) != 1:
            raise RuntimeError('Pinned patch anchor missing/ambiguous: ' + before[:60])
        return text.replace(before,after)
    store = replace(store,'Ld=q({})','Ld=__hubPersistentQueue(q,Of)')
    store = 'import{persistentQueue as __hubPersistentQueue}from"/ai-hub/queue-store.js";\n' + store
    for files in ['Q','r(Ar)','lt']:
        chat = replace(chat,'{id:cn(),prompt:y,files:'+files+'}', '{id:cn(),prompt:y,files:'+files+',models:[...r(Ge)]}')
    # Native queue currently combines every item and removes them BEFORE submission.
    # Preserve one stable item until acknowledged; lock shared tabs per owner/chat.
    chat = replace(chat,'const Hi=async y=>{','H(()=>{window.__hubChatModels?.([...r(Ge)])});const Hi=async y=>An.has(y)?void 0:window.__hubPendingQueue.lock(y,()=>__hubHi(y));const __hubHi=async y=>{')
    chat = replace(chat,'Ee=ne===0?A.slice(0,1):ne<0?[...A]:A.slice(0,ne)','Ee=A.slice(0,1)')
    chat = replace(chat,'aa.update(it=>({...it,[y]:(it[y]??[]).filter(Qe=>!je.has(Qe.id))})),await Po(Ne,Pe)',
                   'await Po(Ne,Pe,{userMessageId:"hubq-"+Ee[0].id,modelId:Ee[0].models?.length===1?Ee[0].models[0]:null}),aa.update(it=>({...it,[y]:(it[y]??[]).filter(Qe=>!je.has(Qe.id))}))')
    chat = replace(chat,'qo=async y=>{','qo=async y=>window.__hubPendingQueue.lock(u(),()=>__hubNow(y)),__hubNow=async y=>{')
    chat = replace(chat,'aa.update(Ee=>({...Ee,[ne]:A.filter(je=>je.id!==y)})),await sa(!1),await sr(),await Po(Q.prompt,Q.files)',
                   'await sa(!1),await sr(),await Po(Q.prompt,Q.files,{userMessageId:"hubq-"+Q.id,modelId:Q.models?.length===1?Q.models[0]:null}),aa.update(Ee=>({...Ee,[ne]:(Ee[ne]??[]).filter(je=>je.id!==y)}))')
    chat = replace(chat,'pt!=null&&pt.statusHistory?pt.statusHistory.push(lt):pt.statusHistory=[lt]',
                   'pt!=null&&pt.statusHistory?(lt?.action==="ai_hub_job"&&lt?.stage_key&&pt.statusHistory.at(-1)?.action==="ai_hub_job"&&pt.statusHistory.at(-1)?.job_id===lt.job_id&&pt.statusHistory.at(-1)?.stage_key===lt.stage_key?pt.statusHistory[pt.statusHistory.length-1]=lt:pt.statusHistory.push(lt)):pt.statusHistory=[lt]')
    chat = replace(chat,'if(l(Nr,null),Q!=null&&Q.parentId',
                   'if(u()&&Q?.model?.startsWith("ai_hub.")){try{const __stop=await fetch("/api/chat/actions/ai_hub_stop",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+localStorage.token},body:JSON.stringify({chat_id:u(),id:Q?.id,model:Q?.model,session_id:"",messages:[]})});if(!__stop.ok)throw new Error("Не удалось остановить агента. Повторите остановку.")}catch(__err){kt.error(String(__err));return}}if(l(Nr,null),Q!=null&&Q.parentId')
    (output/'chunks').mkdir(parents=True,exist_ok=True)
    (output/'ai-hub').mkdir(parents=True,exist_ok=True)
    (output/'chunks'/STORE).write_text(store)
    (output/'chunks'/CHAT).write_text(chat)
    (output/'ai-hub/queue-store.js').write_text(Path(__file__).with_name('queue-store.js').read_text())
    (output/'ai-hub/extensions-ui.js').write_text(Path(__file__).with_name('extensions-ui.js').read_text())
    (output/'ai-hub/artifacts-ui.js').write_text(Path(__file__).with_name('artifacts-ui.js').read_text())
    (output/'ai-hub/media-ui.js').write_text(Path(__file__).with_name('media-ui.js').read_text())
    (output/'ai-hub/harness-ui.js').write_text(Path(__file__).with_name('harness-ui.js').read_text())
    (output/'ai-hub/assets').mkdir(exist_ok=True)
    (output/'ai-hub/assets/sasha-avatar.png').write_bytes(Path(__file__).with_name('assets').joinpath('sasha-avatar.png').read_bytes())
    if (source/'index.html').is_file():
        index = patch_shell((source/'index.html').read_text())
        (output/'index.html').write_text(index)
    print('Pinned native queue patched; owner/chat persistence and stable server IDs enabled')


if __name__=='__main__':patch(*sys.argv[1:])
