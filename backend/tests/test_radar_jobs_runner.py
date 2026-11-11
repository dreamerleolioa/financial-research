import pytest
from run_daily_radar_jobs import run


def test_resume_uses_stored_dates_and_stops_without_progress(tmp_path):
    def read(url,token,**kw):
        return {'enabled':True,'version':'radar-durable-v1'} if url.endswith('capabilities') else {'jobs':[{'job_id':7,'run_date':'2026-11-06'}]}
    def write(url,token,payload,**kw):
        assert url.endswith('/7/advance') and payload=={}
        return {'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','advanced_count':0,'data_ready':False}
    result=run(endpoint='https://example.test',token='secret',output_dir=tmp_path,resume=True,read=read,write=write)
    assert result['advanced_requests']==1 and result['status']=='recorded_or_advanced'
    assert (tmp_path/'jobs.json').is_file()


def test_accepted_intent_does_not_pass_deadline(tmp_path):
    def read(url,token,**kw):
        if url.endswith('capabilities'):return {'enabled':True,'version':'radar-durable-v1'}
        return {'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','publication':{},'validation':{}}
    def write(url,token,payload,**kw):
        return {'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','advanced_count':0}
    result=run(endpoint='https://example.test',token='secret',output_dir=tmp_path,run_date='2026-11-06',require_lanes=['publication'],read=read,write=write)
    assert result['status']=='failed' and not result['readiness_deadline_passed']


def test_disabled_capability_and_wrong_date_fail_closed(tmp_path):
    with pytest.raises(ValueError,match='not_ready'):
        run(endpoint='https://example.test',token='',output_dir=tmp_path,resume=True,read=lambda *a,**k:{'enabled':False})
    with pytest.raises(ValueError,match='date_mismatch'):
        run(endpoint='https://example.test',token='',output_dir=tmp_path,run_date='2026-11-06',
            read=lambda *a,**k:{'enabled':True,'version':'radar-durable-v1'},write=lambda *a,**k:{'effective_run_date':'2026-11-07'})


def test_closed_publication_is_independent_of_unfinished_validation(tmp_path):
    def read(url,token,**kw):
        if url.endswith('capabilities'):return {'enabled':True,'version':'radar-durable-v1'}
        return {'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','publication':{},'validation':{}}
    def write(url,token,payload,**kw):
        return {'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','advanced_count':0,
                'state':'pending','publication':{'state':'closed'}}
    kwargs=dict(endpoint='https://example.test',token='secret',output_dir=tmp_path,run_date='2026-11-06',read=read,write=write)
    assert run(**kwargs,require_lanes=['publication'])['status']=='recorded_or_advanced'
    assert run(**kwargs,require_lanes=['publication','next_open'])['status']=='failed'


def test_audit_requeue_continues_even_without_a_completed_work_unit(tmp_path):
    advances=[]
    def read(url,token,**kw):return {'enabled':True,'version':'radar-durable-v1'}
    def write(url,token,payload,**kw):
        result={'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06'}
        if url.endswith('/jobs'):return result
        advances.append(url)
        return result|{'advanced_count':0,'runnable_now':len(advances)==1}
    result=run(endpoint='https://example.test',token='secret',output_dir=tmp_path,run_date='2026-11-06',read=read,write=write)
    assert len(advances)==2 and result['advanced_requests']==2
