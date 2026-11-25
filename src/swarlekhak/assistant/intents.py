"""Allow-listed intent grammar. Text (spoken or typed) -> Command, or None.

This is deliberately a closed set of regular patterns: there is no free-form
fallback, no LLM, no web/shell intent. Anything unmatched yields a short
clarification from the orchestrator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from swarlekhak.assistant.states import S
from swarlekhak.contracts import Command
from swarlekhak.engines.numbers import ORDINALS, parse_int

INTERACTIVE = frozenset(s.value for s in (S.MUTED, S.PASSIVE_WAKE, S.COMMAND_LISTEN, S.CONFIRM,
                                           S.SPEAKING, S.VERIFY_PENDING, S.RECOVERABLE_ERROR))
DURING_DICTATION = frozenset({S.DICTATING.value, S.DICTATION_STARTING.value})
POST_SUBMIT = frozenset({S.SUBMITTED.value})


@dataclass(frozen=True)
class IntentSpec:
    name: str
    patterns: tuple[str, ...]
    allowed: frozenset[str] = INTERACTIVE
    confirm: bool = False
    needs_exam: bool = True


_ORD = r"(?P<ord>first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))"

SPECS: list[IntentSpec] = [
    IntentSpec("help", (r"^(help|what can i say|commands)$",), INTERACTIVE | POST_SUBMIT, needs_exam=False),
    IntentSpec("confirm_yes", (r"^(yes|yeah|yep|confirm|correct|okay|ok|yes please|go ahead|do it|yes submit|confirm submission|that's right|that is right)$",), needs_exam=False),
    IntentSpec("cancel", (r"^(no|nope|cancel|stop|never mind|nevermind|quiet|silence|pause)$",), INTERACTIVE | POST_SUBMIT, needs_exam=False),
    IntentSpec("start_exam", (r"^(start|begin|open|resume) (my |the )?(exam|examination|test)$",), needs_exam=False),
    IntentSpec("dismiss", (r"^(thanks|thank you|thank you swar|thanks swar|that's all|that is all|goodbye|bye|"
                            r"ok thanks|okay thanks|okay thank you|done for now)$",), INTERACTIVE | POST_SUBMIT,
               needs_exam=False),
    IntentSpec("status", (r"^(status|where am i)$",), INTERACTIVE | POST_SUBMIT, needs_exam=False),
    IntentSpec("time_left", (r"^(how much )?time (is )?(left|remaining)$", r"^time left$"), INTERACTIVE),
    IntentSpec("go_to_sleep", (r"^(go to sleep|sleep)$",), INTERACTIVE | POST_SUBMIT, needs_exam=False),
    IntentSpec("mute", (r"^(mute|stop listening|mute microphone)$",), INTERACTIVE | POST_SUBMIT | DURING_DICTATION, needs_exam=False),
    IntentSpec("speech_rate", (r"^(speak |talk |read )?(?P<dir>slower|faster)$",), INTERACTIVE | POST_SUBMIT, needs_exam=False),
    # navigation
    IntentSpec("read_question", (r"^(read|repeat) (the )?question(?: (number )?(?P<n>.+))?$",)),
    IntentSpec("go_question", (r"^(go to|open|jump to) question (number )?(?P<n>.+)$",)),
    IntentSpec("next_question", (r"^(next question|go to (the )?next question)$",)),
    IntentSpec("previous_question", (r"^(previous question|go to (the )?previous question)$",)),
    # dictation
    IntentSpec("start_writing", (r"^(start|begin|resume|continue) (writing|dictation|dictating|answering)$",)),
    IntentSpec("finish_writing", (r"^(finish|stop|end|done) (writing|dictation|dictating)$", r"^i'?m done( writing)?$"),
               INTERACTIVE | DURING_DICTATION),
    IntentSpec("cancel_writing", (r"^cancel (writing|dictation)$",), INTERACTIVE | DURING_DICTATION),
    # structured: equations
    IntentSpec("equation_mode", (r"^start (an )?equation$",)),
    IntentSpec("end_equation", (r"^end equation$",)),
    IntentSpec("equation", (r"^(?:start |insert |write |new )?equation (?P<body>.+?)(?: end equation)?$",)),
    # structured: diagram
    IntentSpec("diagram_create", (r"^(create|add|make) (a )?node (?P<name>.+)$",)),
    IntentSpec("diagram_rename", (r"^rename node (?P<old>.+?) to (?P<new>.+)$",)),
    IntentSpec("diagram_delete", (r"^(delete|remove) node (?P<name>.+)$",), confirm=True),
    IntentSpec("diagram_connect", (r"^connect (node )?(?P<a>.+?) to (node )?(?P<b>.+?)(?: (labelled|labeled|with label|label) (?P<label>.+))?$",)),
    IntentSpec("diagram_disconnect", (r"^disconnect (node )?(?P<a>.+?) from (node )?(?P<b>.+)$",)),
    IntentSpec("diagram_list_nodes", (r"^(list|read) (the |all )?nodes$",)),
    IntentSpec("diagram_list_edges", (r"^(list|read) (the |all )?(connections|edges|arrows)$",)),
    IntentSpec("diagram_read", (r"^(read|describe) (the |my )?diagram$",)),
    # structured: chemistry, tables, pseudocode (explicit content only; no balancing, no execution)
    IntentSpec("chemistry", (r"^(?:chemical|chemistry)(?: equation)? (?P<body>.+)$",)),
    IntentSpec("table_create", (r"^(create|make|add|insert|new) (a )?table (with )?(?P<n>\w+(?: \w+)?) columns?$",)),
    IntentSpec("table_heading", (r"^(set|call|name) (the )?(heading|header|title) (of )?(column )?(?P<c>\w+) (to|as) (?P<text>.+)$",
                                 r"^(set )?column (?P<c>\w+) (heading|header|title) (to|as|is) (?P<text>.+)$")),
    IntentSpec("table_add_row", (r"^add (a |another )?row$",)),
    IntentSpec("table_set", (r"^(set|put|write) row (?P<r>\w+) column (?P<c>\w+) (to|as) (?P<text>.+)$",
                             r"^row (?P<r>\w+) column (?P<c>\w+) (is |equals |to )?(?P<text>.+)$")),
    IntentSpec("table_read_row", (r"^read row (?P<r>\w+)$",)),
    IntentSpec("table_read", (r"^(read|describe) (the |my )?table$",)),
    IntentSpec("table_delete_row", (r"^delete row (?P<r>\w+)$",), confirm=True),
    IntentSpec("code_add_line", (r"^(add|new|write) (code |pseudocode )?line (?P<text>.+)$",)),
    IntentSpec("code_indent", (r"^indent( next line)?$",)),
    IntentSpec("code_outdent", (r"^(outdent|dedent|unindent)( next line)?$",)),
    IntentSpec("code_delete_line", (r"^delete (code )?line (?P<r>\w+)$",), confirm=True),
    IntentSpec("code_read", (r"^(read|describe) (the |my )?(code|pseudocode|pseudo code)$",)),
    # approved visual material / sonification
    IntentSpec("play_graph", (r"^play (the )?graph$",)),
    IntentSpec("graph_next_point", (r"^next point$",)),
    IntentSpec("graph_previous_point", (r"^previous point$",)),
    IntentSpec("read_graph", (r"^read (the )?graph( data)?$",)),
    IntentSpec("describe_visual", (r"^(describe|read) (the )?(figure|visual|table|image|picture)$",)),
    # review
    IntentSpec("play_original", (r"^play (the )?original( audio| recording)?$", r"^play (it|that) back$"),),
    IntentSpec("read_review", (r"^(next|read) (review|passage|flag)$", r"^what needs (a )?review$"),),
    IntentSpec("keep_original", (r"^keep (the )?(original|it|as is|written|that)$",)),
    IntentSpec("skip_review", (r"^(skip|defer) (the )?(review|this|it|passage)$",)),
    # audio cursor / editing
    IntentSpec("read_answer", (r"^read (my |the )?answer$",)),
    IntentSpec("read_sentence", (r"^(read )?(the )?(?P<which>next|previous|current|this) sentence$",)),
    IntentSpec("read_paragraph", (r"^read paragraph (?P<n>.+)$",)),
    IntentSpec("spell_word", (r"^spell (the |this |that |current )?word$",)),
    IntentSpec("find_word", (r"^(find|select) (the )?(word )?(?P<target>.+)$",)),
    IntentSpec("replace", (rf"^replace (the )?(?:{_ORD} (occurrence of )?)?(?P<old>.+?) with (?P<new>.+)$",)),
    IntentSpec("insert_text", (r"^insert (?P<new>.+?) (?P<where>before|after) (?P<target>.+)$",)),
    IntentSpec("delete_text", (rf"^delete (the )?(?:{_ORD} (occurrence of )?)?(word )?(?P<target>.+)$",), confirm=True),
    IntentSpec("undo", (r"^undo( that| last change| it)?$",)),
    # submission
    IntentSpec("submit", (r"^submit (my |the )?(exam|examination|test|answers)$",), confirm=True),
]

_COMPILED = [(spec, [re.compile(p, re.IGNORECASE) for p in spec.patterns]) for spec in SPECS]
_SPEC_BY_NAME = {s.name: s for s in SPECS}


def clean(text: str, keep_case: bool = False) -> str:
    """Normalize spoken/typed command text. Keeps math symbols and decimal points for equations."""
    text = text.replace("’", "'")
    if not keep_case:
        text = text.lower()
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    text = re.sub(r"[^\w\s'\-+=*/^()²³.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def ordinal_to_int(word: str | None) -> int | None:
    if not word:
        return None
    if word in ORDINALS:
        return ORDINALS[word]
    m = re.match(r"(\d+)(st|nd|rd|th)$", word)
    return int(m.group(1)) if m else None


def spec(name: str) -> IntentSpec:
    return _SPEC_BY_NAME[name]


def parse(text: str, source: str = "voice") -> Command | None:
    t = clean(text, keep_case=True)
    if not t:
        return None
    for sp, regs in _COMPILED:
        for rx in regs:
            m = rx.match(t)
            if not m:
                continue
            # free-text arguments keep the student's casing (equation variables, replacement words);
            # identifier-like arguments are lower-cased
            args = {k: (v if k in ("body", "new", "old", "target", "label", "text") else v.lower())
                    for k, v in m.groupdict().items() if v is not None}
            bad = False
            for key in ("n", "c", "r"):
                if key in args:
                    n = parse_int(args[key])
                    if n is None:
                        bad = True   # "read question banana" is not a number -> try other patterns
                        break
                    args[key] = n
            if bad:
                continue
            if "ord" in args:
                args["ord"] = ordinal_to_int(args["ord"])
            return Command(intent=sp.name, args=args, allowed_states=sp.allowed,
                           confirmation_required=sp.confirm, source=source, raw_text=text)
    return None
