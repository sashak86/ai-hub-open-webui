"""Запуск системных команд: с тайм-аутом, без shell, при необходимости — от имени пользователя."""
from __future__ import annotations

import asyncio
import os
import pwd
import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def user_info(username: str) -> pwd.struct_passwd:
    return pwd.getpwnam(username)


def executable_path(username: str) -> str:
    """Bounded user CLI prefixes; never source shell config or expose npm auth."""
    home=Path(user_info(username).pw_dir)
    user_bins=[home/'.local/bin',home/'.npm-global/bin',home/'.npm/bin',
               home/'.local/share/npm/bin',home/'.local/share/pnpm',home/'.bun/bin',home/'.opencode/bin']
    try:
        with (home/'.npmrc').open() as config:
            text=config.read(65537)
        if len(text)>65536: text=""
        for line in text.splitlines():
            match=re.fullmatch(r'\s*prefix\s*=\s*["\']?([^"\'\r\n]+?)["\']?\s*',line)
            if match:
                raw=match.group(1).strip().replace('${HOME}',str(home))
                if raw.startswith('~/'):raw=str(home/raw[2:])
                if '$' in raw or '`' in raw:continue
                if not Path(raw).is_absolute():continue
                prefix=Path(raw).resolve()
                if (prefix/'bin').is_dir(): user_bins.append(prefix/'bin')
    except (OSError,UnicodeError): pass
    versions=home/'.nvm/versions/node'
    try:
        candidates=[]
        with os.scandir(versions) as entries:
            for index,entry in enumerate(entries):
                if index>=128:break
                if re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',entry.name):candidates.append(Path(entry.path))
        candidates.sort(key=lambda v:tuple(map(int,v.name[1:].split('.'))),reverse=True)
        try:
            with (home/'.nvm/alias/default').open() as alias:default=alias.read(64).strip()
            if re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',default):
                chosen=versions/default
                if chosen in candidates:candidates.remove(chosen);candidates.insert(0,chosen)
        except (OSError,UnicodeError):pass
        user_bins.extend(v/'bin' for v in candidates[:16])
    except OSError: pass
    fixed=['/usr/local/sbin','/usr/local/bin','/usr/sbin','/usr/bin','/sbin','/bin']
    return ':'.join(dict.fromkeys([str(v) for v in user_bins if v.is_dir()]+fixed))


def user_env(username: str, extra: dict | None = None) -> dict:
    pw = user_info(username)
    env = {
        "HOME": pw.pw_dir,
        "USER": pw.pw_name,
        "LOGNAME": pw.pw_name,
        "SHELL": pw.pw_shell or "/bin/bash",
        "PATH": executable_path(username),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "XDG_RUNTIME_DIR": f"/run/user/{pw.pw_uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{pw.pw_uid}/bus",
    }
    if extra:
        env.update(extra)
    return env


def as_user_kwargs(username: str | None) -> dict:
    """Аргументы для subprocess, чтобы процесс работал от имени username (служба работает от root)."""
    if not username:
        return {}
    pw = user_info(username)
    if os.geteuid() == pw.pw_uid:
        return {"env": user_env(username), "cwd": pw.pw_dir}
    if os.geteuid() != 0:
        raise PermissionError(f"Нужен root, чтобы работать от имени {username}")
    return {"user": pw.pw_uid, "group": pw.pw_gid, "extra_groups": os.getgrouplist(username, pw.pw_gid),
            "env": user_env(username), "cwd": pw.pw_dir}


async def run(*cmd: str, as_user: str | None = None, timeout: float = 15, input: bytes | None = None,
              cwd: str | None = None, env: dict | None = None) -> Result:
    kw = as_user_kwargs(as_user)
    if cwd:
        kw["cwd"] = cwd
    if env:
        kw["env"] = {**(kw.get("env") or os.environ), **env}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kw,
        )
    except FileNotFoundError:
        return Result(127, "", f"Команда не найдена: {cmd[0]}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(input), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return Result(124, "", f"Тайм-аут {timeout} с: {' '.join(cmd)}")
    return Result(proc.returncode, out.decode(errors="replace"), err.decode(errors="replace"))


async def unit_states(*units: str) -> list[dict]:
    """Состояние юнитов systemd одним вызовом: активность, с какого времени, перезапуски, память."""
    props = "Id,Description,LoadState,ActiveState,SubState,Result,ActiveEnterTimestamp,NRestarts,MemoryCurrent"
    r = await run("systemctl", "show", "--no-pager", "-p", props, "--timestamp=unix", *units)
    out = []
    for block in r.out.strip().split("\n\n") if r.out.strip() else []:
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        since = kv.get("ActiveEnterTimestamp", "").lstrip("@")
        mem = kv.get("MemoryCurrent", "")
        out.append({
            "unit": kv.get("Id", ""), "description": kv.get("Description", ""),
            "found": kv.get("LoadState") == "loaded", "active": kv.get("ActiveState", ""), "sub": kv.get("SubState", ""),
            "result": kv.get("Result", ""), "since": float(since) if since.isdigit() else None,
            "restarts": int(kv["NRestarts"]) if kv.get("NRestarts", "").isdigit() else None,
            "mem": int(mem) if mem.isdigit() else None,
        })
    return out
