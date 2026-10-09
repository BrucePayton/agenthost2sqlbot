import json

import httpx
import pytest

from scripts.sqlbot_sample_regression import run_case


@pytest.mark.asyncio
async def test_sample_keeps_query_and_analysis_ids_separate_and_drops_rows():
    def sse(events):
        return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
    def handle(request):
        if request.url.path.endswith('/open'):
            return httpx.Response(200,json={'session_id':'fresh'})
        if request.url.path.endswith('/ask/stream'):
            assert json.loads(request.content)=={'session_id':'fresh','question':'original question'}
            return sse([{'type':'id','id':1}, {'type':'sql-data','content':'execute-success'},
                {'type':'chart-type','content':'bar'}, {'type':'result','result':{
                    'recordId':1,'resultId':'r','sql':'SELECT 1','rowCount':1,
                    'chartHint':{'type':'bar'},'rows':[{'private':'must-not-persist'}]}}, {'type':'done'}])
        assert request.url.path.endswith('/actions/analysis')
        return sse([{'type':'id','id':2}, {'type':'analysis-result','content':'answer'},
                    {'type':'result','result':{'record':{},'execution':{'steps':[]}}}, {'type':'done'}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test') as client:
        report=await run_case(client,{'case_id':'test','agent_id':'agent','question':'original question'})
    assert report['record_id']==1
    assert report['analysis_record_id']==2
    assert report['query_success'] and report['analysis_success']
    assert report['chart_type']=='bar'
    assert report['answer']=='answer'
    assert 'must-not-persist' not in json.dumps(report)
