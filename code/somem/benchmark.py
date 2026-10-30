"""Minimal batch entry points: prepare, build, qa, judge. No automatic resume."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .protocols import load_benchmark_protocol


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def run_batch(document, output, phase, *, workers=1, memory_root=None):
    from somem import build_memory, collect_answer, OpenAIChatAgent, OpenAIChatConfig, Runtime
    if workers < 1:
        raise ValueError('workers must be positive')
    if document.get('schema') != 'somem-benchmark-input-v1':
        raise ValueError('use prepare to create the input first')
    protocol = load_benchmark_protocol(document['benchmark'])
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    endpoint, key = os.environ['SOMEM_BASE_URL'], os.environ['SOMEM_API_KEY']
    writer_options = dict(protocol.writer_options)
    reader_options = dict(protocol.reader_options)
    writer_options['model'] = os.environ.get('SOMEM_WRITER_MODEL', writer_options['model'])
    reader_options['model'] = os.environ.get('SOMEM_READER_MODEL', reader_options['model'])
    prepared_hash = hashlib.sha256(json.dumps(document,sort_keys=True).encode()).hexdigest()
    if phase == 'qa':
        prior = json.loads((Path(memory_root)/'run.json').read_text())
        if prior.get('phase') != 'build' or prior.get('prepared_sha256') != prepared_hash:
            raise ValueError('memory build does not match the prepared dataset')
    write_new(output/'run.json', {'phase':phase, 'benchmark':protocol.name,
        'protocol_version':protocol.version, 'coverage':document['coverage'],
        'source_sha256':document.get('source_sha256'),
        'prepared_sha256':prepared_hash,
        'writer':writer_options,'reader':reader_options,
        'build':asdict(protocol.build_config),'query':asdict(protocol.query_config)})
    def work(pair):
        index, sample = pair
        directory = output/f'{index:04d}'
        directory.mkdir()
        try:
            if phase == 'build':
                writer = OpenAIChatAgent(OpenAIChatConfig(base_url=endpoint,api_key=key,**writer_options))
                build_memory(sample['conversation'],directory/'memory',agent=writer,
                    model=writer_options['model'],config=protocol.build_config,
                    checkpoint_path=directory/'build-checkpoint.json')
                write_new(directory/'built.json',{'status':'complete','sample_id':sample['sample_id']})
            else:
                memory = Path(memory_root).resolve()/f'{index:04d}'/'memory'
                built = json.loads((memory.parent/'built.json').read_text())
                if built.get('status') != 'complete' or built['sample_id'] != sample['sample_id']:
                    raise ValueError('memory is not a completed build of this sample')
                reader = OpenAIChatAgent(OpenAIChatConfig(
                    base_url=os.environ.get('SOMEM_READER_BASE_URL',endpoint),
                    api_key=os.environ.get('SOMEM_READER_API_KEY',key),**reader_options))
                runtime = Runtime(reader,reader_options['model'],query_config=protocol.query_config)
                records = []
                for qi, q in enumerate(sample['qa']):
                    # Golds, rubrics, task type and evidence never enter the Reader.
                    request = {'question':q['question']}
                    if protocol.name == 'longmemeval-s':
                        request['question_date'] = q['question_date']
                    evidence,steps,answer,trace = collect_answer(
                        runtime,request,memory,{},config=protocol.query_config)
                    if not str(answer).strip():
                        raise ValueError(f"no completed answer: {q['question_id']}")
                    records.append({**q,'gold':str(q['answer']),'answer':str(answer),
                                    'sample_id':sample['sample_id'],'steps':steps,'trace':trace})
                    write_new(directory/f'q{qi:04d}.json',records[-1])
                write_new(directory/'answers.json',records)
                write_new(directory/'calls.json',runtime.call_log)
            return True
        except Exception as exc:
            write_new(directory/'error.json',{'status':'failed','sample_id':sample['sample_id'],
                      'error_type':type(exc).__name__,'error':str(exc)})
            return False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(work,enumerate(document['samples'])))
    return 0 if all(outcomes) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='phase',required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--benchmark',required=True,choices=['locomo','beam-100k','beam-1m','longmemeval-s'])
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-subset',action='store_true')
    for phase in ['build','qa']:
        p = commands.add_parser(phase)
        p.add_argument('--input',type=Path,required=True)
        p.add_argument('--output',type=Path,required=True)
        p.add_argument('--workers',type=int,default=1)
        if phase == 'qa':p.add_argument('--memory-root',type=Path,required=True)
    p = commands.add_parser('judge')
    p.add_argument('--benchmark',required=True,choices=['locomo','beam-100k','beam-1m','longmemeval-s'])
    p.add_argument('--answers',type=Path,required=True,help='One answers.json, or QA output root')
    p.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    if args.phase == 'prepare':
        from .benchmark_data import prepare
        prepare(args.benchmark,args.input,args.output,allow_subset=args.allow_subset)
        return 0
    if args.phase == 'judge':
        from .benchmark_judge import judge_answers
        return judge_answers(args.benchmark,args.answers,args.output)
    document = json.loads(args.input.read_text())
    return run_batch(document,args.output,args.phase,workers=args.workers,
                     memory_root=getattr(args,'memory_root',None))


if __name__ == '__main__':
    raise SystemExit(main())
