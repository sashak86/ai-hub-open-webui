"""Declarative bounded artifacts; never execute provider HTML or JavaScript."""
import csv
import io
import json
import math
import re

INSTRUCTIONS='''\n\nПосле выполнения дай самостоятельный итоговый ответ отдельно от сообщений о ходе работы. Не выдавай план за результат. Если полезны интерактивные график, таблица, тест или dashboard, добавь блок ```ai-hub-artifact с JSON {"type":"table","title":"Название","data":{"columns":["Столбец"],"rows":[["значение"]]}}. Другие схемы: chart data {"kind":"line|bar|pie","labels":["A"],"series":[{"name":"Ряд","values":[1]}]}; quiz data {"questions":[{"prompt":"Вопрос","options":["A","B"],"answer":0,"explanation":"Почему"}]}; dashboard data {"cards":[{"label":"Всего","value":1}],"charts":[]}. Только достоверные данные, без HTML/JS. Не добавляй визуализацию без пользы.'''

def scalar(v):
    return v is None or isinstance(v,(str,bool,int,float)) and (not isinstance(v,float) or math.isfinite(v)) and (not isinstance(v,str) or len(v)<=10000)

def chart(d):
    if not (isinstance(d,dict) and d.get('kind') in ('line','bar','pie')):raise ValueError("Invalid artifact")
    labels=d.get('labels');series=d.get('series')
    if not (isinstance(labels,list) and 0<len(labels)<=1000 and all(isinstance(v,str) and len(v)<=300 for v in labels)):raise ValueError("Invalid artifact")
    if not (isinstance(series,list) and 0<len(series)<=20):raise ValueError("Invalid artifact")
    for s in series:
        if not (isinstance(s,dict) and set(s)=={'name','values'} and isinstance(s.get('name'),str) and len(s['name'])<=200):raise ValueError("Invalid artifact")
        if not (isinstance(s.get('values'),list) and len(s['values'])==len(labels)):raise ValueError("Invalid artifact")
        if not (all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in s['values'])):raise ValueError("Invalid artifact")
    if d['kind']=='pie' and (len(series)!=1 or any(v<0 for v in series[0]['values'])):raise ValueError('Invalid pie')
    return {k:d[k] for k in ('kind','labels','series')}

def validate(value):
    if not (isinstance(value,dict) and set(value)<= {'id','type','title','data'}):raise ValueError("Invalid artifact")
    typ=value.get('type');d=value.get('data');title=value.get('title','')
    if not (isinstance(title,str) and 0<len(title)<=200 and isinstance(d,dict)):raise ValueError("Invalid artifact")
    if typ=='table':
        if not (set(d)=={'columns','rows'} and isinstance(d['columns'],list) and 0<len(d['columns'])<=50):raise ValueError("Invalid artifact")
        if not (all(isinstance(c,str) and len(c)<=200 for c in d['columns'])):raise ValueError("Invalid artifact")
        if not (isinstance(d['rows'],list) and len(d['rows'])<=5000):raise ValueError("Invalid artifact")
        if not (all(isinstance(r,list) and len(r)==len(d['columns']) and all(scalar(v) for v in r) for r in d['rows'])):raise ValueError("Invalid artifact")
    elif typ=='chart':d=chart(d)
    elif typ=='quiz':
        if not (set(d)=={'questions'} and isinstance(d['questions'],list) and 0<len(d['questions'])<=100):raise ValueError("Invalid artifact")
        for q in d['questions']:
            if not (isinstance(q,dict) and set(q)<={'prompt','options','answer','explanation'}):raise ValueError("Invalid artifact")
            if not (isinstance(q.get('prompt'),str) and 0<len(q['prompt'])<=2000):raise ValueError("Invalid artifact")
            if not (isinstance(q.get('options'),list) and 2<=len(q['options'])<=10 and all(isinstance(v,str) and len(v)<=2000 for v in q['options'])):raise ValueError("Invalid artifact")
            if not (type(q.get('answer')) is int and 0<=q['answer']<len(q['options'])):raise ValueError("Invalid artifact")
            if not (isinstance(q.get('explanation',''),str) and len(q.get('explanation',''))<=4000):raise ValueError("Invalid artifact")
    elif typ=='dashboard':
        if not (set(d)<={'cards','charts'} and isinstance(d.get('cards'),list) and len(d['cards'])<=30):raise ValueError("Invalid artifact")
        if not (all(isinstance(c,dict) and set(c)=={'label','value'} and isinstance(c['label'],str) and len(c['label'])<=200 and scalar(c['value']) for c in d['cards'])):raise ValueError("Invalid artifact")
        if not (isinstance(d.get('charts',[]),list) and len(d.get('charts',[]))<=12):raise ValueError("Invalid artifact")
        d={**d,'charts':[chart(c) for c in d.get('charts',[])]}
    else:raise AssertionError('artifact_type')
    if not (len(json.dumps(d,ensure_ascii=False))<=500000):raise ValueError("Invalid artifact")
    return {'type':typ,'title':title,'data':d}

def extract(text):
    artifacts=[]
    def block(m):
        if len(artifacts)>=12:return m.group(0)
        try:
            if len(m.group(1))>500000:raise ValueError('Artifact too large')
            v=validate(json.loads(m.group(1)))
        except (AssertionError,ValueError,TypeError,KeyError,OverflowError):return m.group(0)
        v['id']=str(len(artifacts)+1);artifacts.append(v);return ''
    return re.sub(r'```ai-hub-artifact\s*\n([\s\S]*?)\n```',block,text).strip(),artifacts

def csv_export(artifact):
    buf=io.StringIO();writer=csv.writer(buf)
    def safe(v):
        s='' if v is None else str(v)
        return "'"+s if s.lstrip().startswith(('=','+','-','@')) else s
    writer.writerow([safe(v) for v in artifact['data']['columns']])
    writer.writerows([[safe(v) for v in r] for r in artifact['data']['rows']]);return buf.getvalue()
