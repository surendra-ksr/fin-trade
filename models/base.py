"""Versioned model contracts and leakage-safe validation primitives."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
import pickle
from pathlib import Path
import numpy as np
class ModelBase(ABC):
    """Common serializable model contract."""
    version='1.0'
    @abstractmethod
    def fit(self,X,y): """Fit using training data only."""
    @abstractmethod
    def predict(self,X): """Predict without mutating inputs."""
    def save(self,path: str|Path)->None:
        """Persist model and version metadata."""
        Path(path).write_bytes(pickle.dumps({'version':self.version,'model':self}))
    @classmethod
    def load(cls,path: str|Path):
        """Load a serialized versioned model."""
        payload=pickle.loads(Path(path).read_bytes()); return payload['model']
@dataclass(frozen=True)
class Fold:
    """Purged train/test split with a non-zero embargo."""
    train: np.ndarray; test: np.ndarray; embargo: int

def purged_walk_forward(n:int, folds:int=5, embargo:int=1):
    """Yield expanding walk-forward folds with embargo rows removed before test."""
    if folds<2 or embargo<1: raise ValueError('folds >=2 and embargo >0 required')
    edges=np.linspace(0,n,folds+1,dtype=int)
    for i in range(1,folds):
        test=np.arange(edges[i],edges[i+1]); train=np.arange(0,max(0,edges[i]-embargo)); yield Fold(train,test,embargo)

def past_sequences(values, window:int):
    """Build sequences whose target follows, never precedes, its input window."""
    x=np.asarray(values); return np.asarray([x[i-window:i] for i in range(window,len(x))])
