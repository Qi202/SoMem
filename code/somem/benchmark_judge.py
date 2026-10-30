"""Adapters to hash-locked scoring sources; no alternate scoring prompts."""
import ast
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import statistics
import string
from types import SimpleNamespace

LOCKS = {
    'locomo': '753889df01f381980b1d96a4aab480b5ff8b0919e0e0ac7092bbf828bdfe1dc6',
    'beam': '8215b640998d9554ccddd814ce48fdba355db575e35de5eb82f9cdbe7b195b81',
    'src/lightmem/memory_toolkits/memories/datasets/longmemeval.py':
        'da60e12e0ffecac357bb527768f1b8758646e8b7809eedec2e12437489bc345e',
    'src/lightmem/memory_toolkits/inference_utils/operators.py':
        '4ec4010e6da962d2bf8062456bdeffa8fe15936cd36108dcbc2beb1fab616e8a',
    'src/lightmem/memory_toolkits/inference_utils/prompts.py':
        '11576b65bf71b44bd131ce40f4ddd43434ae17f08544ea5f22b7cecea0e06763',
    'src/lightmem/memory_toolkits/memory_evaluation.py':
        'cff03024c6a18dcd3522a977a24b34322f96ce6ae75d517085be13401eddda21',
}


def verified_text(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'pinned Judge source hash mismatch: {path}')
    return raw.decode('utf-8')


def source_method(text, class_name, method_name):
    cls = next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name==class_name)
    method = next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method_name)
    method.decorator_list = []
    # Future annotations avoid importing LightMem's heavy inference stack.
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
             names=[ast.alias(name='annotations')],level=0),method],type_ignores=[])
    scope = {}
    exec(compile(ast.fix_missing_locations(module),'<pinned-LightMem>','exec'),scope)
    return scope[method_name]


def beam_functions():
    path = files('somem').joinpath('evaluation_sources/eval_beam_mem0.py')
    text = verified_text(path,LOCKS['beam'])
    chosen = []
    for node in ast.parse(text).body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in
                {'SYSTEM_PROMPT','JUDGE_PROMPT'} for t in node.targets):chosen.append(node)
        if isinstance(node,ast.FunctionDef) and node.name in {'clamp_score','parse_judgment'}:
            chosen.append(node)
    scope = {'re':re,'json':json}
    exec(compile(ast.Module(body=chosen,type_ignores=[]),str(path),'exec'),scope)
    return scope


class CapturedClient:
    """Keep raw provider results; reject malformed LoCoMo labels before mapping."""
    def __init__(self,client,*,check_label=False):
        self.client=client
        self.check_label=check_label
        self.label_parser=lambda text:text
        self.responses=[]
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self,**kwargs):
        response=self.client.chat.completions.create(**kwargs)
        self.responses.append(response.model_dump())
        content=response.choices[0].message.content
        if not isinstance(content,str) or not content.strip():
            raise ValueError('empty Judge response')
        if self.check_label:
            cleaned=self.label_parser(content)
            if json.loads(cleaned).get('label') not in {'CORRECT','WRONG'}:
                raise ValueError('invalid strictJudge label; item remains unscored')
        return response


def make_scorer(benchmark,client,*,lightmem_dir=None,locomo_judge=None):
    captured=CapturedClient(client,check_label=benchmark=='locomo')
    model='openai/gpt-4o-mini'
    if benchmark=='locomo':
        path=(Path(locomo_judge) if locomo_judge is not None else
              files('somem').joinpath('evaluation_sources/pinned/locomo_judge.py'))
        text=verified_text(path,LOCKS['locomo'])
        scope={'__name__':'_somem_locked_locomo_judge'}
        exec(compile(text,str(path),'exec'),scope)
        module=SimpleNamespace(**scope)
        captured.label_parser=module.extract_json
        def score(row):
            if int(row['category']) not in (1,2,3,4):raise ValueError('LoCoMo category outside 1–4')
            return {'is_correct':bool(module.evaluate_llm_judge(
                row['question'],str(row['gold']),row['answer'],client_obj=captured,model_name=model))}
    elif benchmark.startswith('beam-'):
        scope=beam_functions()
        def score(row):
            rubric=row['rubric']
            if isinstance(rubric,str):rubric=[rubric]
            if not rubric:raise ValueError('missing BEAM rubric; item remains unscored')
            nuggets=[]
            for nugget in rubric:
                response=captured.create(model=model,temperature=0,max_tokens=300,messages=[
                    {'role':'system','content':scope['SYSTEM_PROMPT']},
                    {'role':'user','content':scope['JUDGE_PROMPT'].format(
                        question=row['question'],response=row['answer'],nugget=nugget)}])
                value,reason=scope['parse_judgment'](response.choices[0].message.content)
                nuggets.append({'nugget':nugget,'score':value,'reason':reason})
            value=statistics.mean(n['score'] for n in nuggets)
            return {'nuggets':nuggets,'score':round(value,4),'passed':value>=0.5}
    else:
        bundled={
            'longmemeval.py':'longmemeval_dispatch.py',
            'operators.py':'longmemeval_operators.py',
            'prompts.py':'longmemeval_prompts.py',
            'memory_evaluation.py':'longmemeval_evaluation.py',
        }
        texts={p:verified_text(
            Path(lightmem_dir)/p if lightmem_dir is not None else
            files('somem').joinpath('evaluation_sources/pinned/'+bundled[Path(p).name]),h)
            for p,h in LOCKS.items() if p.startswith('src/')}
        dispatch=source_method(texts['src/lightmem/memory_toolkits/memories/datasets/longmemeval.py'],
                               'LongMemEval','get_judge_prompt_info')
        preprocess=source_method(texts['src/lightmem/memory_toolkits/inference_utils/operators.py'],
                                 'LLMExactMatch','_preprocess')
        scope={}
        exec(compile(texts['src/lightmem/memory_toolkits/inference_utils/prompts.py'],
                     '<pinned-LightMem-templates>','exec'),scope)
        templates=scope['_INCOMPLETE_PROMPT_COLLECTIONS']
        tree=ast.parse(texts['src/lightmem/memory_toolkits/memory_evaluation.py'])
        parser=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='is_correct' for t in n.targets))
        verdict=compile(ast.Expression(body=parser),'<pinned-LightMem-parser>','eval')
        def score(row):
            qa=SimpleNamespace(metadata={'id':row['question_id'],'question_type':row['question_type']})
            prompt_name,_=dispatch(None,qa)
            messages=preprocess(SimpleNamespace(_prompt=string.Template(templates[prompt_name])),
                                [row['question']],[[str(row['gold'])]],[row['answer']])[0]
            response=captured.create(model=model,messages=messages,temperature=0,top_p=0.95)
            content=response.choices[0].message.content
            return {'is_correct':bool(eval(verdict,{'content':content})), 'prompt_name':prompt_name}
    def wrapped(row):
        captured.responses=[]
        try:
            result=score(row)
        except Exception as exc:
            exc.judge_responses=list(captured.responses)
            raise
        return {**result,'raw_responses':captured.responses}
    return wrapped


def judge_answers(benchmark,answers,output,*,lightmem_dir=None,locomo_judge=None):
    from openai import OpenAI
    from .benchmark import write_new
    source=Path(answers)
    paths=sorted(source.glob('*/answers.json')) if source.is_dir() else [source]
    rows=[r for p in paths for r in json.loads(p.read_text())]
    ids=[r['question_id'] for r in rows]
    if not rows or len(ids)!=len(set(ids)):raise ValueError('empty answers or duplicate question IDs')
    if any(not isinstance(r.get('answer'),str) or not r['answer'].strip() for r in rows):
        raise ValueError('incomplete answers cannot be judged')
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    client=OpenAI(base_url='https://openrouter.ai/api/v1',
                  api_key=os.environ['OPENROUTER_API_KEY'],max_retries=0)
    scorer=make_scorer(benchmark,client,lightmem_dir=lightmem_dir,locomo_judge=locomo_judge)
    write_new(output/'run.json',{'benchmark':benchmark,'judge':'openai/gpt-4o-mini',
        'input_questions':len(rows),'input_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                                                 for p in paths},
        'source_locks':LOCKS,'automatic_resume':False,'aggregation':False})
    failed=False
    for i,row in enumerate(rows):
        try:
            result=scorer(row)
            write_new(output/f'{i:05d}.json',{'question_id':row['question_id'],
                'status':'scored','result':result})
        except Exception as exc:
            failed=True
            write_new(output/f'{i:05d}.json',{'question_id':row['question_id'],
                'status':'pending','error_type':type(exc).__name__,'error':str(exc),
                'raw_responses':getattr(exc,'judge_responses',[])})
    return int(failed)
