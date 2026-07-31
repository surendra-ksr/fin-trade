"""Explicit, inspectable automation jobs; no live trading by default."""
from dataclasses import dataclass
@dataclass
class Job: name:str; callback:object; enabled:bool=True
class Scheduler:
 def __init__(self): self.jobs={}
 def add(self,name,callback): self.jobs[name]=Job(name,callback); return self.jobs[name]
 def run_once(self): return {n:j.callback() for n,j in self.jobs.items() if j.enabled}
