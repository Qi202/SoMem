"""Rejudge existing BEAM answers with Mem0's rubric-nugget protocol.

This evaluator intentionally keeps Mem0's three-level nugget score, question
mean, and >=0.5 pass threshold.  It does not generate or modify answers.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.evaluation.evaluate import load_records, save
from scripts.evaluation.llm_clients import JUDGE_BASE, JUDGE_MODEL, judge_chat


SYSTEM_PROMPT = (
    "You are an expert evaluator assessing whether an AI assistant's response "
    "satisfies specific rubric criteria. You must be objective, fair, and "
    "consistent. Return ONLY valid JSON with the exact format requested."
)

JUDGE_PROMPT = """Evaluate whether the following LLM response demonstrates compliance with the specified RUBRIC CRITERION.

QUESTION:
{question}

LLM RESPONSE:
{response}

RUBRIC CRITERION:
{nugget}

SCORING GUIDELINES:

First, determine whether the rubric criterion is a POSITIVE requirement (the response SHOULD include something) or a NEGATIVE constraint (the response SHOULD NOT include something).
**For POSITIVE requirements** (response should contain, mention, or demonstrate something):
- **1.0 (Complete Compliance)**: The required element is present, accurate, and complete. The response fully and clearly satisfies the rubric criterion.
- **0.5 (Partial Compliance)**: The required element is partially present, has minor inaccuracies, or is incomplete. The core intent is present but not fully realized.
- **0.0 (No Compliance)**: The required element is missing, incorrect, or the response is entirely off-topic / non-responsive.
**For NEGATIVE constraints** (response should NOT contain or should avoid something):
- **1.0 (Complete Compliance)**: The response is responsive to the question AND the prohibited element is absent.
- **0.5 (Partial Compliance)**: The response is responsive but contains a borderline or ambiguous reference to the prohibited element.
- **0.0 (No Compliance)**: The prohibited element is present in the response, OR the response is non-responsive (off-topic, refusal, empty).
**Compound statement handling**: If the rubric criterion contains "and" or commas connecting multiple required elements:
- All elements present and correct = 1.0
- Some (but not all) elements present and correct = 0.5
- No elements present or correct = 0.0
EVALUATION RULES:
1. **Semantic tolerance**: Paraphrases and synonyms are acceptable. The response does not need to use the exact same words as the rubric.
2. **Numeric and date equivalence**: Treat equivalent representations as identical. "$68,000" = "68k" = "sixty-eight thousand dollars". "2 years" = "24 months". Prefer normalized comparison for numbers, currencies, dates, and durations.
3. **Case / punctuation / whitespace tolerance**: Differences in capitalization, punctuation, and whitespace must be ignored when comparing content.
4. **Hedging tolerance**: Do not penalize hedging language ("I think", "probably", "it seems"), passive voice, or verbosity if the substantive content satisfies the rubric criterion.
5. **Style neutrality**: Do not penalize for tone, formatting, or length unless the rubric criterion specifically requires a particular format.
6. **Responsiveness**: If the LLM response is completely off-topic or refuses to answer, score 0.0 for all criteria.
7. **Independence**: Evaluate this criterion in isolation — do not consider other rubric items.
8. **Specificity matters**: Vague or generic answers that could apply to any question score lower than specific, detailed answers.
STEP-BY-STEP EVALUATION:
Follow these steps in order:
1. **Understand the Requirement**: Read the rubric criterion and classify it as a positive requirement or a negative constraint.
2. **Parse Compound Statements**: If the criterion contains multiple sub-requirements joined by "and" or commas, identify each element separately.
3. **Check Compliance**: Compare the LLM response against each element, applying the tolerance rules above (semantic, numeric, case, hedging).
4. **Assign Score**: Use the appropriate scoring table (positive or negative) and compound-statement rule to determine the score.
5. **Provide Reasoning**: Write a concise explanation referencing which elements were or were not satisfied.
Return your evaluation as a JSON object with exactly two fields:
{{"score": <0.0 or 0.5 or 1.0>, "reason": "<one concise sentence explaining your score>"}}"""


def clamp_score(raw_score: float) -> float:
    if raw_score >= 0.75:
        return 1.0
    if raw_score >= 0.25:
        return 0.5
    return 0.0


def parse_judgment(text: str) -> tuple[float, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', cleaned)
    payload = json.loads(cleaned)
    return clamp_score(float(payload.get("score", 0.0))), str(payload.get("reason", ""))


def aggregate(records: list[dict]) -> dict:
    questions = [r for r in records if r.get("question_id") != "_build_stats"]
    by_type: dict[str, list[float]] = defaultdict(list)
    for record in questions:
        by_type[str(record.get("beam_category", "unknown"))].append(
            float(record["mem0_beam_score"])
        )

    def summary(scores: list[float]) -> dict:
        passed = sum(score >= 0.5 for score in scores)
        return {
            "total": len(scores),
            "passed": passed,
            "pass_rate": passed / len(scores) if scores else 0.0,
            "avg_score": statistics.mean(scores) if scores else 0.0,
        }

    return {
        "overall": summary([float(r["mem0_beam_score"]) for r in questions]),
        "by_question_type": {key: summary(scores) for key, scores in sorted(by_type.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()

    records = load_records(args.input)
    # Resume from the last atomically saved question.  The evaluator writes
    # after every question, so retaining these fields avoids re-billing or
    # changing already accepted judgments after a transient provider error.
    if args.output.is_file():
        previous = {
            str(record.get("question_id")): record
            for record in load_records(args.output)
            if record.get("question_id") is not None
            and record.get("mem0_beam_judgment") is not None
        }
        for record in records:
            prior = previous.get(str(record.get("question_id")))
            if prior is None:
                continue
            for key in (
                "mem0_beam_nugget_scores",
                "mem0_beam_score",
                "mem0_beam_judgment",
            ):
                if key in prior:
                    record[key] = prior[key]
    meta = {
        "label": args.label,
        "benchmark": "beam",
        "protocol": "mem0-rubric-nugget",
        "judge_model": JUDGE_MODEL,
        "judge_base": JUDGE_BASE,
        "input": str(args.input.resolve()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    for record in records:
        if record.get("question_id") == "_build_stats" or "answer" not in record:
            continue
        if record.get("mem0_beam_judgment") is not None:
            continue
        rubric = record.get("rubric") or []
        if isinstance(rubric, str):
            rubric = [rubric]
        nugget_scores = []
        for nugget in rubric:
            prompt = JUDGE_PROMPT.format(
                question=record.get("question", ""),
                response=record["answer"],
                nugget=nugget,
            )
            text, usage = judge_chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            score, reason = parse_judgment(text)
            nugget_scores.append({
                "nugget": nugget, "score": score, "reason": reason,
                "raw": text, "usage": usage,
            })
        score = statistics.mean(item["score"] for item in nugget_scores) if nugget_scores else 0.0
        record["mem0_beam_nugget_scores"] = nugget_scores
        record["mem0_beam_score"] = round(score, 4)
        record["mem0_beam_judgment"] = "PASS" if score >= 0.5 else "FAIL"
        save(records, meta, args.output)

    meta["aggregate"] = aggregate(records)
    meta["status"] = "complete"
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    save(records, meta, args.output)
    print(json.dumps(meta["aggregate"], indent=2))


if __name__ == "__main__":
    main()
