"""Optional dual-answer output protocol; this module does not score answers."""

import re


DUAL_ANSWER_INSTRUCTIONS = """Final response format (replaces any earlier answer-format or answer-length instruction):
Output exactly these two blocks in this order, with no text outside them:
<answer>A complete natural-language answer to the question.</answer>
<final_answer>The shortest complete answer with the same meaning.</final_answer>
The answer block is a user-facing answer, not a reasoning transcript.
The final_answer block must preserve every requested item, necessary qualifier,
negation, uncertainty, and any statement of insufficient evidence in answer.
It may remove explanation and repeated question wording, but must not introduce
facts, choose a different answer, resolve uncertainty, or correct answer.
Use words from the evidence where possible. Do not add citations or formatting
markup inside final_answer. Prefer a short phrase, not a full sentence: omit
the subject when the question already identifies it, introductory wording,
and background not needed to answer the question. For who/what/when/where
questions, give the requested names, items, dates, or places; for why/how,
give the essential reason or mechanism. Preserve all requested list items.
Use enough words to keep the answer complete;
there is no fixed word limit. Both blocks must come from this same response.
"""


def dual_answer_prompt(prompt: str) -> str:
    return prompt.rstrip() + "\n\n" + DUAL_ANSWER_INSTRUCTIONS


def parse_dual_answer(text: str) -> dict[str, str]:
    pattern = r"\s*<answer>\s*(.*?)\s*</answer>\s*<final_answer>\s*(.*?)\s*</final_answer>\s*"
    match = re.fullmatch(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    if not match or not all(part.strip() for part in match.groups()):
        raise ValueError("dual-answer response requires exactly two nonempty answer/final_answer blocks")
    answer, final_answer = (part.strip() for part in match.groups())
    if any(re.search(r"</?(?:answer|final_answer)\b", part, re.I) for part in (answer, final_answer)):
        raise ValueError("duplicate or nested dual-answer tags")
    return {"answer": answer, "final_answer": final_answer,
            "answer_format": "dual-answer-v1", "raw_answer_response": text}


class DualAnswerAgent:
    """Wrap generation only, keeping the underlying retrieval runtime intact."""

    def __init__(self, agent):
        self.agent = agent
        self.last_response = None

    def __getattr__(self, name):
        return getattr(self.agent, name)

    def run(self, *, prompt, **kwargs):
        result = self.agent.run(prompt=dual_answer_prompt(prompt), **kwargs)
        self.last_response = result.text
        return result

    def answer_fields(self):
        if self.last_response is None:
            raise ValueError("no dual-answer response was generated")
        return parse_dual_answer(self.last_response)
