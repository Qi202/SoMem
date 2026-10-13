"""Bounded Writer recovery driven by candidate state, not historical obligations."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from dataclasses import dataclass
import posixpath

from .tools import WriterStagnationError, WriterContextBoundary


@dataclass
class RecoveryBudget:
    """One bound shared by Writer, validation repairs and context rotations."""
    limit: int
    repairs: int = 0
    continuations: int = 0

    def repair(self):
        if self.repairs >= self.limit:
            raise WriterStagnationError('Tool recovery budget exhausted; batch must not advance')
        self.repairs += 1


def unresolved_mutations(audit):
    """Recent failed attempts are hints, never mandatory pending writes."""
    pending = {}
    for row in audit:
        if row.get('tool') not in {'Edit', 'Write', 'Move', 'Remove'}:
            continue
        args = dict(row.get('arguments', {}))
        for field in ('path', 'source', 'destination'):
            if field in args:
                args[field] = posixpath.normpath(str(args[field]))
        path = args.get('path') or args.get('source')
        if not path:
            continue
        output = row.get('output', {})
        if isinstance(output, dict) and output.get('recovery', {}).get('retryable') is False:
            continue
        # Historical records predate structured forbidden-write diagnostics.
        if path != 'core.md' and not path.startswith('topics/'):
            continue
        key = (row['tool'], json.dumps(args, sort_keys=True, ensure_ascii=False))
        if row.get('status') == 'error':
            pending[key] = row
        elif row.get('status') == 'ok':
            pending.pop(key, None)
    return pending


def candidate_digest(workspace):
    digest = hashlib.sha256()
    root = workspace.stage_dir
    for path in [root / 'core.md', *sorted((root / 'topics').rglob('*.md'))]:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def preflight_candidate(workspace):
    """Run the existing authoritative validator on an isolated disposable copy."""
    from .workspace import MemoryWorkspace
    from .agent import _commit_turn, _committed_baseline
    with tempfile.TemporaryDirectory(prefix='writer-preflight-') as temp:
        memory = Path(temp) / 'memory'
        shutil.copytree(workspace.memory_dir, memory)
        preview = MemoryWorkspace(memory, config=workspace.config)
        baseline = _committed_baseline(preview)
        shutil.rmtree(preview.stage_dir)
        shutil.copytree(workspace.stage_dir, preview.stage_dir)
        preview.core_edits_allowed = workspace.core_edits_allowed
        failure = _commit_turn(preview, baseline, [])
        if failure is not None:
            return {'valid': False, 'error': failure.message,
                    'findings': list(failure.diagnostics)}
        return {'valid': True, 'findings': []}


def run_with_tool_recovery(workspace, audit, task, run, log, emit,
                           preflight=preflight_candidate, digest=candidate_digest,
                           *, budget=None):
    """A normal end proceeds to authoritative commit validation, including no-ops.

    Rejected tool calls do not imply invalid content or a coverage obligation.
    Interrupted calls retain their stage and receive bounded fresh context.
    """
    results = []
    budget = budget or RecoveryBudget(workspace.config.generic_repair_max_trajectories)
    initial_repairs = budget.repairs
    continued = False
    prompt = task
    while True:
        start = len(audit)
        before = digest(workspace)
        try:
            result = run(prompt, continued)
            log(result)
            results.append(result)
            # _commit_turn remains the acceptance authority. No coverage gate.
            return results, budget.repairs - initial_repairs
        except WriterStagnationError as exc:
            failed = getattr(exc, 'somem_result', None)
            if failed is not None:
                log(failed)
                results.append(failed)
            progress = digest(workspace) != before
            if isinstance(exc, WriterContextBoundary) and progress and budget.continuations < 2:
                budget.continuations += 1
                kind = 'writer_continuation'
            else:
                budget.repair()
                kind = 'tool_recovery'
            candidate = preflight(workspace)
            details = []
            for row in unresolved_mutations(audit[start:]).values():
                args = row.get('arguments', {})
                details.append({'tool': row['tool'], 'arguments': args,
                                'output': row.get('output', {})})
            # Keep complete findings on disk; bound only the prompt presentation.
            (workspace.stage_dir / 'repair-diagnostics.json').write_text(
                json.dumps({'preflight': candidate, 'recent_failures': details}, ensure_ascii=False, indent=2))
            event = {'tool': kind, 'status': 'started', 'trajectory': budget.repairs,
                     'continuations': budget.continuations, 'trigger_error': str(exc),
                     'candidate_changed': progress, 'candidate_valid': candidate['valid'],
                     'tool_events_this_attempt': len(audit) - start}
            audit.append(event)
            emit(event)
            workspace.writer_stagnation_reason = None
            if hasattr(workspace, 'mutation_watch'):
                del workspace.mutation_watch
            continued = True
            prompt = (
                'Continue against the retained staged candidate. Prior successful edits remain. '
                'Acceptance requires correctly formed, supported recorded facts, NOT exhaustive '
                'coverage of every input fact. Do not invent facts or evidence to achieve coverage. '
                'Historical failed operations are diagnostic examples, not a checklist to execute. '
                'Never write timeline or other derived views: Runtime generates them from Topics. '
                'Discard that prohibited operation; do not retry it. Sources are read-only. '
                'Prioritize current candidate defects below. Read staged files for exact edit text; '
                'ReadCommitted provides original fact/ID/evidence, not the current staged text. '
                'Preserve existing IDs on their original facts and preserve supported old evidence. '
                'For a distinct new fact append a separate paragraph; do not replace an old citation '
                'with new prose. Use a unique nonblank literal match and a genuinely different replacement. '
                'Stop once your chosen updates are finished and the recorded content is consistent. '
                'A failed optional operation may be abandoned; do not force it merely to clear history. '
                'Full diagnostics are in repair-diagnostics.json (paginated Read). Preflight ran on '
                'a disposable copy: verify any normalized IDs against actual staged or committed files.\n'
                + 'Current candidate check:\n' + json.dumps(candidate, ensure_ascii=False)[:6000]
                + '\nRecent failed examples:\n' + json.dumps(details, ensure_ascii=False)[:6000]
                + '\nOriginal input for context:\n' + task
            )
