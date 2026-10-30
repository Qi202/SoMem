"""Convert user-supplied benchmark data; never download datasets implicitly."""
import ast
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re

CATEGORIES = ('information_extraction', 'multi_session_reasoning', 'temporal_reasoning',
              'knowledge_update', 'contradiction_resolution', 'event_ordering',
              'preference_following', 'instruction_following', 'summarization', 'abstention')


def _literal(value):
    return ast.literal_eval(value) if isinstance(value, str) else value


def _date(raw):
    text = str(raw or '')
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        return date(*map(int, m.groups())).isoformat()
    months = {name: i for i, name in enumerate(
        ('january','february','march','april','may','june','july','august',
         'september','october','november','december'), 1)}
    m = re.search(r'([A-Za-z]+)[-\s](\d{1,2})[-,\s]+(\d{4})', text)
    if m and m[1].lower() in months:
        return date(int(m[3]), months[m[1].lower()], int(m[2])).isoformat()
    raise ValueError(f'unrecognized observation date: {raw!r}')


def read_rows(path):
    path = Path(path)
    if path.suffix == '.json':
        rows = json.loads(path.read_text())
    elif path.suffix in {'.jsonl', '.ndjson'}:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        try:
            from datasets import Dataset
        except ImportError as exc:
            raise ValueError('Arrow/Parquet input requires pip install ".[benchmarks]"') from exc
        rows = list(Dataset.from_parquet(str(path)) if path.suffix == '.parquet'
                    else Dataset.from_file(str(path)))
    if not isinstance(rows, list) or not rows:
        raise ValueError('dataset must contain a non-empty list of records')
    return rows


def adapt_rows(benchmark, rows, *, allow_subset=False):
    samples = []
    for index, row in enumerate(rows):
        sample_id = str(row.get('sample_id', row.get('conversation_id', row.get('question_id', index))))
        if benchmark == 'locomo':
            conversation = row['conversation']
            questions = [dict(q) for q in row['qa'] if int(q['category']) in (1,2,3,4)]
        elif benchmark.startswith('beam-'):
            if 'conversation' in row and 'qa' in row:
                conversation, questions = row['conversation'], [dict(q) for q in row['qa']]
            else:
                conversation = {'speaker_a': 'User', 'speaker_b': 'Assistant'}
                for n, session in enumerate(_literal(row['chat']), 1):
                    conversation[f'session_{n}'] = [
                        {'speaker': m['role'], 'text': str(m['content']), 'dia_id': f'D{n}:{t}',
                         'beam_id': str(m.get('id', ''))} for t, m in enumerate(session, 1)]
                    anchor = next((m['time_anchor'] for m in session if m.get('time_anchor')), None)
                    if anchor is None:
                        raise ValueError('BEAM session has no time_anchor')
                    conversation[f'session_{n}_date_time'] = _date(anchor)
                probing = _literal(row['probing_questions'])
                if set(probing) - set(CATEGORIES):
                    raise ValueError('unknown BEAM question category')
                questions = []
                for category in CATEGORIES:
                    for original in probing.get(category, []):
                        q = dict(original)
                        gold = next((q[k] for k in ('answer','ideal_answer','ideal_response',
                                     'ideal_summary','expected_compliance') if q.get(k)), None)
                        if gold is None:
                            raise ValueError('BEAM question has no reference answer')
                        rubric = q.get('rubric', [])
                        if isinstance(rubric, str) and rubric.strip().startswith('['):
                            rubric = _literal(rubric)
                        q.update(answer=str(gold), rubric=rubric, beam_category=category,
                                 abstention=category == 'abstention')
                        questions.append(q)
        elif benchmark == 'longmemeval-s':
            sessions, dates = row['haystack_sessions'], row['haystack_dates']
            if not sessions or len(sessions) != len(dates):
                raise ValueError('LongMemEval session/date mismatch')
            conversation = {'speaker_a': 'user', 'speaker_b': 'assistant'}
            for n, (session, observed) in enumerate(zip(sessions, dates), 1):
                if not session:
                    raise ValueError('empty LongMemEval session')
                conversation[f'session_{n}'] = [
                    {'speaker': m['role'], 'text': m['content'], 'dia_id': f'D{n}:{t}'}
                    for t, m in enumerate(session, 1)]
                conversation[f'session_{n}_date_time'] = _date(observed)
            questions = [{k: row[k] for k in ('question_id','question','answer',
                          'question_type','question_date')}]
        else:
            raise ValueError(f'unknown benchmark: {benchmark}')
        for qi, q in enumerate(questions):
            q['question_id'] = str(q.get('question_id', f'{sample_id}:q{qi}'))
            if not isinstance(q.get('question'), str) or not q['question'].strip():
                raise ValueError('empty question')
            if 'answer' not in q:
                raise ValueError('missing reference answer')
        samples.append({'sample_id': sample_id, 'conversation': conversation, 'qa': questions})
    ids = [q['question_id'] for s in samples for q in s['qa']]
    if len(ids) != len(set(ids)) or len({s['sample_id'] for s in samples}) != len(samples):
        raise ValueError('duplicate sample or question IDs')
    expected = {'locomo': (10,1540), 'beam-100k': (20,400),
                'beam-1m': (35,700), 'longmemeval-s': (500,500)}[benchmark]
    if not allow_subset:
        if (len(samples),len(ids)) != expected:
            raise ValueError(f'expected {expected} samples/questions; use --allow-subset for a subset')
        if benchmark == 'locomo':
            counts = Counter(int(q['category']) for s in samples for q in s['qa'])
            if counts != {1:282,2:321,3:96,4:841}:
                raise ValueError('LoCoMo category counts differ from the agreed scope')
        if benchmark == 'longmemeval-s' and sum(
                sum(k.startswith('session_') and not k.endswith('_date_time')
                    for k in s['conversation']) for s in samples) / len(samples) < 20:
            raise ValueError('input appears to be LongMemEval-oracle, not LongMemEval-S')
    return {'schema':'somem-benchmark-input-v1', 'benchmark':benchmark,
            'coverage':'subset' if allow_subset else 'full', 'samples':samples}


def prepare(benchmark, source, output, *, allow_subset=False):
    document = adapt_rows(benchmark, read_rows(source), allow_subset=allow_subset)
    document['source_sha256'] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    path = Path(output)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
    return document
