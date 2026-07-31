"""Offline behavioral tests for Phase 2 provider boundaries and quality flags."""
from datetime import timedelta
import pandas as pd
from data.quality import DataQuality
from agents.data_agent import DataAgent

class Resp:
    def __init__(self,payload): self.payload=payload
    def raise_for_status(self): return None
    def json(self): return self.payload

def agent():
 a=DataAgent.__new__(DataAgent); a._cfg=type('C',(),{'data':type('D',(),{'request_timeout_seconds':1})()})(); a._log=type('L',(),{'warning':lambda *x:None})(); return a

def test_alpha_key_absent_skips(monkeypatch):
 monkeypatch.delenv('ALPHAVANTAGE_API_KEY',raising=False); assert agent().fetch_alpha_vantage_fundamentals('AAPL')=={}
def test_alpha_key_present_parses(monkeypatch):
 monkeypatch.setenv('ALPHAVANTAGE_API_KEY','x'); monkeypatch.setattr('agents.data_agent.requests.get',lambda *a,**k:Resp({'Symbol':'AAPL'})); assert agent().fetch_alpha_vantage_fundamentals('AAPL')['Symbol']=='AAPL'
def test_sec_company_facts_normalizes_request(monkeypatch):
 monkeypatch.setattr('agents.data_agent.requests.get',lambda *a,**k:Resp({'facts':{'us-gaap':{}}})); assert agent().fetch_sec_company_facts('320193')['facts']
def test_options_surface_and_empty():
 a=agent(); a._provider=type('P',(),{'get_option_chain':lambda self,s: (pd.DataFrame({'strike':[100],'impliedVolatility':[.2]}),pd.DataFrame(), '2025-01-01')})(); x=a.options_iv_surface('AAPL'); assert x.iloc[0].impliedVolatility==.2
 a._provider=type('P',(),{'get_option_chain':lambda self,s: (pd.DataFrame(),pd.DataFrame(),None)})(); assert a.options_iv_surface('AAPL').empty
def test_news_dedup(monkeypatch):
 rows=[]; a=agent(); a._db=type('D',(),{'insert_news':lambda *args,**kwargs: rows.append(1) or (1 if len(rows)==1 else 0)})(); item={'symbol':'AAPL','published_at':'2025-01-01','headline':'x'}; assert a.ingest_news([item,item])==1
def bars(index):
 c=pd.Series(range(1,len(index)+1),index=index,dtype=float); return pd.DataFrame({'open':c,'high':c+1,'low':c-1,'close':c,'volume':1},index=index)
def test_quality_gap_stale_jump_and_clean():
 idx=pd.to_datetime(['2025-01-01','2025-01-03']); d=bars(idx); d.loc[idx[-1],'close']=100; r=DataQuality().inspect(d,expected_interval=timedelta(days=1),stale_after=timedelta(seconds=0),corporate_jump=.2); assert any('gaps' in i for i in r.issues); assert any('stale' in i for i in r.issues); assert any('corporate' in i for i in r.issues); assert len(DataQuality().clean(d))==2
