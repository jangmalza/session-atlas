"""Aggregate collector liveness, separate from viewer refresh activity."""
from datetime import datetime,timezone
import json

def collector_status(con, at=None):
    unknown={'state':'unknown','lastScanUtc':None,'scanErrors':0}
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='collector_state'").fetchone():return unknown
    row=con.execute("SELECT value_json FROM collector_state WHERE key='heartbeat'").fetchone()
    if not row:return unknown
    try:
        value=json.loads(row[0]);stamp=datetime.fromisoformat(value['at'].replace('Z','+00:00'))
        age=((at or datetime.now(timezone.utc))-stamp).total_seconds();interval=float(value['interval'])
        errors=int(value['scanErrors'])
        if interval<1 or errors<0:return unknown
        state='stopped' if age>max(30,interval*3+10) else 'warning' if errors else 'running'
        return {'state':state,'lastScanUtc':value['at'],'scanErrors':errors,'ageSeconds':max(0,int(age))}
    except (ValueError,TypeError,KeyError,OverflowError):return unknown
