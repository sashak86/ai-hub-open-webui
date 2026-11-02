"""Portable service configuration; no host-specific defaults or private hub code."""
from dataclasses import dataclass
from pathlib import Path
import os,pwd
@dataclass
class Settings:
    desktop_user:str=pwd.getpwuid(os.getuid()).pw_name
    testing:bool=False
    data_dir:Path=Path(os.environ.get('SASHA_WORKBENCH_DATA',str(Path.home()/'.local/state/sasha-workbench')))
def load_settings():return Settings()
