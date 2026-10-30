import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from somem.benchmark_data import adapt_rows, prepare
from somem.benchmark_judge import make_scorer, verified_text


def locomo():
    return [{'sample_id':'sample','conversation':{'session_1':[
        {'speaker':'Alice','text':'tea','dia_id':'D1:1'}]},'qa':[
        {'question':'drink?','answer':'tea','category':4},
        {'question':'excluded','answer':'x','category':5}]}]


def test_locomo_excludes_category5_and_requires_explicit_subset(tmp_path):
    with pytest.raises(ValueError,match='expected'):
        adapt_rows('locomo',locomo())
    adapted=adapt_rows('locomo',locomo(),allow_subset=True)
    assert len(adapted['samples'][0]['qa'])==1
    path=tmp_path/'raw.json';path.write_text(json.dumps(locomo()))
    prepare('locomo',path,tmp_path/'prepared.json',allow_subset=True)
    with pytest.raises(FileExistsError):prepare('locomo',path,tmp_path/'prepared.json',allow_subset=True)


def test_beam_raw_literals_preserve_multiline_messages_and_rubrics():
    row={'conversation_id':7,'chat':[[{'role':'user','content':'a\n\nb',
         'time_anchor':'March-15-2024'}]],'probing_questions':str({
         'information_extraction':[{'question':'q','answer':'a','rubric':['criterion']}]})}
    sample=adapt_rows('beam-100k',[row],allow_subset=True)['samples'][0]
    assert sample['conversation']['session_1'][0]['text']=='a\n\nb'
    assert sample['conversation']['session_1_date_time']=='2024-03-15'
    assert sample['qa'][0]['rubric']==['criterion']


def test_lme_session_dates_and_question_metadata_are_preserved():
    row={'question_id':'q_abs','question_type':'multi-session','question':'q',
         'answer':'unknown','question_date':'2024/02/03',
         'haystack_sessions':[[{'role':'user','content':'message'}]],
         'haystack_dates':['2024/02/01']}
    sample=adapt_rows('longmemeval-s',[row],allow_subset=True)['samples'][0]
    assert sample['conversation']['session_1_date_time']=='2024-02-01'
    assert sample['qa'][0]['question_date']=='2024/02/03'
    row['haystack_dates']=[]
    with pytest.raises(ValueError,match='mismatch'):
        adapt_rows('longmemeval-s',[row],allow_subset=True)


class FakeClient:
    def __init__(self,responses):
        self.texts=iter(responses);self.requests=[]
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self,**kwargs):
        self.requests.append(kwargs)
        text=next(self.texts)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            model_dump=lambda:{'model':'gpt-4o-mini','choices':[{'message':{'content':text}}]})


def test_locked_beam_scoring_uses_original_nuggets_and_full_answer():
    client=FakeClient(['{"score":0.5,"reason":"partial"}','{"score":1,"reason":"yes"}'])
    score=make_scorer('beam-100k',client)
    result=score({'question':'q','gold':'a','answer':'full\n\nanswer','rubric':['a','b']})
    assert result['score']==0.75 and result['passed']
    assert len(result['raw_responses'])==2
    assert 'full\n\nanswer' in client.requests[0]['messages'][1]['content']
    assert client.requests[0]['max_tokens']==300
    assert client.requests[0]['temperature']==0


def test_hash_mismatch_stops_before_source_execution(tmp_path):
    p=tmp_path/'source.py';p.write_text('raise RuntimeError("must not execute")')
    with pytest.raises(ValueError,match='hash mismatch'):
        verified_text(p,'0'*64)


def test_batch_passes_only_question_to_reader_and_uses_protocol(tmp_path,monkeypatch):
    import somem
    from somem.benchmark import run_batch
    document=adapt_rows('locomo',locomo(),allow_subset=True)
    monkeypatch.setenv('SOMEM_BASE_URL','https://example.test/v1')
    monkeypatch.setenv('SOMEM_API_KEY','test')
    monkeypatch.setattr(somem,'OpenAIChatAgent',lambda config:object())
    captured=[]
    def build(conv,memory,**kwargs):
        memory.mkdir()
        captured.append(kwargs['config'])
    monkeypatch.setattr(somem,'build_memory',build)
    def answer(runtime,request,memory,index,**kwargs):
        assert request=={'question':'drink?'}
        return [],1,'complete answer',[]
    monkeypatch.setattr(somem,'collect_answer',answer)
    assert run_batch(document,tmp_path/'build','build')==0
    assert captured[0].writer_input_token_cap==15000
    assert run_batch(document,tmp_path/'qa','qa',memory_root=tmp_path/'build')==0
    rows=json.loads((tmp_path/'qa/0000/answers.json').read_text())
    assert rows[0]['gold']=='tea' and rows[0]['answer']=='complete answer'
    with pytest.raises(FileExistsError):run_batch(document,tmp_path/'qa','qa',memory_root=tmp_path/'build')
