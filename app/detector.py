"""System A: finds the first line where a student's math stops being valid.

The math verdict comes from SymPy, never from an LLM. Student text is only
parsed after passing a strict whitelist, because SymPy's parser uses eval().
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from sympy import Add, Poly, S, diff, expand, nan, oo, simplify, solve, symbols, zoo
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

x = symbols("x")
_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
_SAFE = re.compile(r"^[0-9x+\-*/^().\s]+$")
MAX_LINE = 80
MAX_LINES = 12
MAX_EXPONENT = 12
MAX_DEGREE = 4

PROBLEMS: Dict[str, dict] = {
    "dist": {
        "id": "dist", "course": "Algebra 1", "kind": "equation",
        "prompt": "Solve for x", "text": "3(x+2)=2x+11",
        "sample_wrong": ["3x+2=2x+11", "x+2=11", "x=9"],
        "sample_right": ["3x+6=2x+11", "x+6=11", "x=5"],
    },
    "move": {
        "id": "move", "course": "Algebra 1", "kind": "equation",
        "prompt": "Solve for x", "text": "5x-4=2x+11",
        "sample_wrong": ["5x-2x=11-4", "3x=7", "x=7/3"],
        "sample_right": ["5x-2x=11+4", "3x=15", "x=5"],
    },
    "factor": {
        "id": "factor", "course": "Algebra 2", "kind": "equation",
        "prompt": "Solve for x", "text": "x^2-5x+6=0",
        "sample_wrong": ["(x+2)(x+3)=0", "x=-2 or x=-3"],
        "sample_right": ["(x-2)(x-3)=0", "x=2 or x=3"],
    },
    "deriv": {
        "id": "deriv", "course": "Calculus 1", "kind": "derivative",
        "prompt": "Find f'(x)", "text": "4x^3-2x^2+7x-9",
        "sample_wrong": ["12x^3-4x^2+7x"],
        "sample_right": ["12x^2-4x+7"],
    },
}

MISCONCEPTIONS: Dict[str, dict] = {
    "partial_distribution": {
        "label": "Partial distribution",
        "nudge": "Look at the parentheses. What should the outside number multiply?",
        "diagnosis": "Multiplied only the first term inside the parentheses.",
        "say": "Ask them to draw an arrow from the outside number to each term inside.",
        "practice": "Solve 4(x+3)=2x+20",
        "reteach": "Area model: draw a rectangle split into x and 2, with width 3. Then find both areas.",
    },
    "sign_on_move": {
        "label": "Sign error moving a term",
        "nudge": "A term crossed the equals sign on this line. What did you do to both sides?",
        "diagnosis": "Moved a term across the equals sign without changing its sign.",
        "say": "Ask them to write the subtraction or addition on both sides instead of \"moving\" the term.",
        "practice": "Solve 7x-3=4x+9",
        "reteach": "Replace \"move it over\" with \"do the same to both sides\" and model one line on the board.",
    },
    "partial_division": {
        "label": "Divided only some terms",
        "nudge": "When you divide one side, does every term get divided?",
        "diagnosis": "Divided some terms by the coefficient but not all of them.",
        "say": "Ask them to rewrite the line with the whole side in parentheses over the divisor.",
        "practice": "Solve 2x+8=14",
        "reteach": "Show that dividing a sum divides each term, then isolate the x term before dividing.",
    },
    "inverse_operation": {
        "label": "Used the wrong inverse operation",
        "nudge": "What operation is attached to x here, and what undoes it?",
        "diagnosis": "Used the wrong operation to undo the coefficient on x.",
        "say": "Ask what 3x means in words, then what would undo it.",
        "practice": "Solve 6x=24",
        "reteach": "Pair each operation with its inverse, then check an answer by substituting it back.",
    },
    "factor_sign": {
        "label": "Factoring sign error",
        "nudge": "Multiply your factors back out. Does the middle term match?",
        "diagnosis": "Found the right factor pair but with the wrong signs.",
        "say": "Ask them to multiply the factors back out and compare the middle term.",
        "practice": "Solve x^2-7x+12=0",
        "reteach": "Sign pattern: positive constant and negative middle term mean both factors subtract.",
    },
    "zero_product_sign": {
        "label": "Zero-product sign flip",
        "nudge": "Set each factor equal to zero. What value of x makes it zero?",
        "diagnosis": "Read the roots directly from the factors without solving each one.",
        "say": "Ask them to substitute their answer into the factor and see whether it equals zero.",
        "practice": "Solve (x-4)(x+1)=0",
        "reteach": "Write each factor equal to zero and solve it as its own small equation.",
    },
    "power_rule_no_reduce": {
        "label": "Power rule: exponent not reduced",
        "nudge": "The power rule changes two things. Which one did you do?",
        "diagnosis": "Brought the exponent down but did not lower the power by one.",
        "say": "Ask them to differentiate x^2 from the limit definition, then compare.",
        "practice": "Find f'(x) for 5x^3+2x^2",
        "reteach": "Check the power rule against a slope sketch of x^2 at two points.",
    },
    "power_rule_no_multiply": {
        "label": "Power rule: exponent not multiplied",
        "nudge": "The power rule changes two things. Which one did you do?",
        "diagnosis": "Lowered the power but did not multiply by the original exponent.",
        "say": "Ask them to state the power rule aloud, then apply it to one term.",
        "practice": "Find f'(x) for 5x^3+2x^2",
        "reteach": "Run the rule on one term at a time, labeling both steps.",
    },
    "constant_kept": {
        "label": "Constant not dropped",
        "nudge": "How fast does a constant change?",
        "diagnosis": "Kept the constant term when differentiating.",
        "say": "Ask for the slope of the graph of y=9.",
        "practice": "Find f'(x) for 3x^2+8",
        "reteach": "Graph a horizontal line and read its slope as the derivative of a constant.",
    },
    "other_slip": {
        "label": "Calculation slip",
        "nudge": "Something changed between this line and the one before it. Check it term by term.",
        "diagnosis": "This line is not equivalent to the previous one, and it does not match a known pattern.",
        "say": "Ask them to explain aloud what they did between these two lines.",
        "practice": "Try the same problem again with new numbers",
        "reteach": "Have students swap work and check each other's lines by substitution.",
    },
}


class Unreadable(ValueError):
    pass


def _normalize(raw: str) -> str:
    text = raw.strip().lower()
    for old, new in (("−", "-"), ("–", "-"), ("×", "*"), ("·", "*"), ("²", "^2"), ("³", "^3")):
        text = text.replace(old, new)
    return text


def parse_side(text: str):
    """Parse one math expression. Rejects anything outside the whitelist."""
    text = text.strip()
    if not text or len(text) > MAX_LINE or not _SAFE.match(text) or "**" in text:
        raise Unreadable(text)
    for match in re.finditer(r"\^\s*(\S?)", text):
        if not match.group(1).isdigit():
            raise Unreadable(text)
    for exponent in re.findall(r"\^\s*(\d+)", text):
        if int(exponent) > MAX_EXPONENT:
            raise Unreadable(text)
    if re.search(r"\^\s*\d+\s*\^", text):
        raise Unreadable(text)
    try:
        value = parse_expr(text, transformations=_TRANSFORMS, local_dict={"x": x})
    except Exception as exc:
        raise Unreadable(text) from exc
    if not hasattr(value, "has") or value.has(zoo, nan, oo, -oo):
        raise Unreadable(text)
    return value


def _degree_ok(expr) -> bool:
    try:
        return Poly(expr, x).degree() <= MAX_DEGREE
    except Exception:
        return False


def solution_set(line: str):
    """Return (set_of_roots, difference_expr, branches). 'ALL' means every x works."""
    parts = [p for p in re.split(r"\bor\b|,", line) if p.strip()]
    roots = set()
    diffs = []
    for part in parts:
        if part.count("=") != 1:
            raise Unreadable(line)
        left, right = part.split("=")
        difference = expand(parse_side(left) - parse_side(right))
        if difference.has(zoo, nan) or not _degree_ok(difference):
            raise Unreadable(line)
        diffs.append(difference)
        if difference == 0:
            return "ALL", difference, diffs
        roots.update(solve(difference, x))
    return roots, diffs[0], diffs


def _proportional(a, b) -> bool:
    if b == 0 or a == 0:
        return a == b
    ratio = simplify(a / b)
    return ratio.is_number is True and ratio != 0


def _terms(line: str) -> List:
    left, right = line.split("=")
    return list(Add.make_args(expand(parse_side(left)))) + [
        -t for t in Add.make_args(expand(parse_side(right)))
    ]


def _classify_equation(prev: str, cur: str) -> str:
    try:
        cur_roots, cur_diff, cur_branches = solution_set(cur)
        prev_roots, prev_diff, prev_branches = solution_set(prev)
    except Unreadable:
        return "other_slip"

    # Partial distribution needs the raw text: SymPy auto-distributes 3*(x+2).
    pattern = r"(-?\d+)\s*\*?\s*\(\s*x\s*([+-])\s*(\d+)\s*\)"
    if re.search(pattern, prev) and "=" in prev:
        partial = re.sub(pattern, lambda m: f"({m.group(1)}x{m.group(2)}{m.group(3)})", prev)
        left, right = partial.split("=")
        if _proportional(cur_diff, expand(parse_side(left) - parse_side(right))):
            return "partial_distribution"

    if len(prev_branches) == 1 and "=" in prev:
        if prev_diff.coeff(x, 2) != 0 and cur_diff.coeff(x, 2) != 0:
            scale = cur_diff.coeff(x, 2) / prev_diff.coeff(x, 2)
            if (
                cur_diff.coeff(x, 1) == -scale * prev_diff.coeff(x, 1)
                and cur_diff.coeff(x, 0) == scale * prev_diff.coeff(x, 0)
            ):
                return "factor_sign"

        terms = _terms(prev)
        for term in terms:
            if _proportional(cur_diff, expand(prev_diff - 2 * term)):
                return "sign_on_move"

        coefficient = prev_diff.coeff(x, 1)
        if prev_diff.coeff(x, 2) == 0 and coefficient not in (S.Zero, S.One, S.NegativeOne):
            x_term = coefficient * x
            others = [t for t in terms if t.has(x) is False]
            only_x = expand(x_term / coefficient + sum(others))
            if _proportional(cur_diff, only_x):
                return "partial_division"
            for kept in others:
                candidate = expand((prev_diff - kept) / coefficient + kept)
                if _proportional(cur_diff, candidate):
                    return "partial_division"

            constant = -prev_diff.coeff(x, 0)
            if isinstance(cur_roots, set) and len(cur_roots) == 1:
                root = next(iter(cur_roots))
                wrong = {constant * coefficient, constant - coefficient, constant + coefficient}
                if constant != 0:
                    wrong.add(coefficient / constant)
                if root in wrong:
                    return "inverse_operation"

    if isinstance(cur_roots, set) and isinstance(prev_roots, set) and cur_roots:
        if {-r for r in prev_roots} == cur_roots:
            return "zero_product_sign"
    return "other_slip"


def _classify_derivative(f, expected, cur) -> str:
    terms = Add.make_args(expand(f))
    no_reduce = no_multiply = S.Zero
    for term in terms:
        coefficient, power = term.as_coeff_exponent(x)
        if power == 0:
            continue
        no_reduce += coefficient * power * x ** power
        no_multiply += coefficient * x ** (power - 1)
    cur = expand(cur)
    constant = expand(f).coeff(x, 0)
    # A student can stop the rule at x^1 correctly and still be on this pattern,
    # so compare only the terms with degree above one.
    if cur == expand(no_reduce):
        return "power_rule_no_reduce"
    if cur.coeff(x, 3) == no_reduce.coeff(x, 3) and cur.coeff(x, 3) != 0:
        return "power_rule_no_reduce"
    if cur == expand(no_multiply):
        return "power_rule_no_multiply"
    if constant != 0 and cur == expand(expected + constant):
        return "constant_kept"
    return "other_slip"


def _is_final(line: str) -> bool:
    return all(
        re.match(r"^\s*x\s*=\s*-?[\d./\s]+$", part)
        for part in re.split(r"\bor\b|,", line)
        if part.strip()
    )


def analyze(problem_id: str, raw_lines: List[str]) -> dict:
    problem = PROBLEMS[problem_id]
    lines = [_normalize(l) for l in raw_lines if l.strip()][:MAX_LINES]
    rows: List[dict] = []
    first_error: Optional[int] = None
    misconception: Optional[str] = None
    last_good = _normalize(problem["text"])
    any_unread = False

    if problem["kind"] == "equation":
        target_roots, _, _ = solution_set(problem["text"])
    else:
        f = parse_side(problem["text"])
        expected = expand(diff(f, x))

    final_correct = False
    for index, line in enumerate(lines):
        display = line[:MAX_LINE]
        if first_error is not None:
            rows.append({"text": display, "status": "after_error"})
            continue
        try:
            if problem["kind"] == "equation":
                roots, _, _ = solution_set(line)
                good = roots == target_roots
                final_correct = good and _is_final(line)
            else:
                value = parse_side(line.split("=")[-1])
                good = simplify(value - expected) == 0
                final_correct = good
        except Unreadable:
            rows.append({"text": display, "status": "unread"})
            any_unread = True
            final_correct = False
            continue

        if good:
            rows.append({"text": display, "status": "ok"})
            last_good = line
            continue

        first_error = index
        rows.append({"text": display, "status": "error"})
        if problem["kind"] == "equation":
            misconception = _classify_equation(last_good, line)
        else:
            misconception = _classify_derivative(f, expected, parse_side(line.split("=")[-1]))

    if first_error is not None:
        verdict = "error"
    elif final_correct:
        verdict = "correct"
    elif any_unread:
        verdict = "unreadable"
    else:
        verdict = "in_progress"

    return {
        "verdict": verdict,
        "rows": rows,
        "first_error": first_error,
        "misconception": misconception,
        "label": MISCONCEPTIONS[misconception]["label"] if misconception else None,
        "nudge": MISCONCEPTIONS[misconception]["nudge"] if misconception else None,
    }


def safe_lines_for_model(rows: List[dict]) -> Tuple[List[str], Optional[int]]:
    """Only parsed math goes to an LLM. Free text never leaves the server."""
    out: List[str] = []
    bad = None
    for row in rows:
        if row["status"] == "unread" or not _SAFE.match(re.sub(r"\bor\b|[=,]", " ", row["text"])):
            out.append("[unreadable]")
        else:
            out.append(row["text"])
        if row["status"] == "error":
            bad = len(out)
    return out, bad
