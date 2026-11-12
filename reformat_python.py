#!/data/data/com.termux/files/usr/bin/env python
"""reformat_python -- restore indentation to a flattened Python source file.

Some tools (bad formatters, mail transports that strip leading whitespace,
copy/paste through markdown, editors with a broken "trim whitespace" filter)
produce a "flattened" Python file: every logical statement is still present
and in the right *order*, but all leading whitespace has been removed.  Such
a file can no longer be parsed by Python, because the block structure is
gone.

This script reconstructs the indentation.  It works on the *token* level, so
it never has to guess whether a piece of text is a string, a comment or code.

How it works
------------

1.  The file is tokenised with :mod:`tokenize`.  Since the file carries no
    indentation at all, the tokeniser happily produces a flat token stream.

2.  Tokens are grouped into *logical lines*.  A logical line ends at a
    ``NEWLINE`` token; the physical lines in between are continuation lines
    belonging to the same logical line.  Physical lines that carry no code
    (blank lines, comment-only lines) become logical lines of their own.

3.  Every logical line is classified:
      * *block opener* -- the line ends with ``:`` at bracket depth zero and
        is not a one-liner suite (``if x: return 1``).
      * *clause*       -- the line starts with ``else``/``elif``/``except``/
        ``finally``/``case`` and therefore continues an enclosing construct.
      * *terminator*  -- ``return``/``raise``/``break``/``continue``/``pass``,
        which end the suite they appear in.
      * *decorator*   -- a line starting with ``@``; it belongs to the
        definition that follows it.

4.  A stack machine rebuilds the suite nesting.  The rules mirror the way
    Python itself decides where a suite ends:

      * a clause keyword attaches to the innermost matching construct that is
        still open and closes every suite opened inside it;
      * a terminating statement ends the suite it belongs to -- a ``return``
        only ends the block it is written in, the block around it goes on;
      * a definition (``def``/``class``) normally lives next to the
        enclosing definition; the exception is a method and a nested
        function, which the script finds by looking at the names;
      * a block header that follows a finished suite starts a new suite next
        to it.

5.  A few places in a flattened file are genuinely ambiguous: only the
    original indentation could tell whether a block header belongs to the
    suite above it or opens a nested one, and which construct a clause
    keyword continues.  The script resolves such places with two oracles:

      * CPython's own parser is the ground truth for what is and what is not
        valid Python.  The alternatives are tried until the file parses;
      * the *meaning* of the program decides the rest.  A name that is read
        has to be bound before it is read, in the scope that reads it, so
        the reading that leaves no name unbound is the right one.  The scope
        checker in this file reports the names that are read too early.

6.  The computed indentation is written back.  Continuation lines are
    indented one level deeper than their logical line so the result is both
    parseable and readable.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import io
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

__version__ = "1.0.0"

#: Statements that end the suite they appear in.
TERMINATORS = frozenset({"return", "raise", "break", "continue", "pass"})

#: Keywords that continue the suite of an enclosing construct.
CLAUSE_KEYWORDS = frozenset({"else", "elif", "except", "finally", "case"})

#: Keywords that introduce a definition.
DEFINITION_KEYWORDS = frozenset({"def", "class"})

#: Keywords whose suite may be continued by an ``else`` clause.
LOOP_KEYWORDS = frozenset({"for", "while"})

#: The indentation unit used for the output.
INDENT_UNIT = "    "

#: How far back the search looks for a block header that could repair a
#: syntax error.  The header that causes the problem is always close to
#: the statement the parser stumbled over.
_WINDOW = 40

#: How many logical lines the local region around a failure covers.
_REGION_SIZE = 60

#: How many decisions the local search flips at the same time.
_REGION_DECISIONS = 10

#: ``type Point = tuple[float, float]``  ->  ``Point: object = ...``
#:
#: Type aliases are a Python 3.12 feature.  When the script runs on an
#: older interpreter the check has to fall back to this rewrite, otherwise
#: perfectly fine 3.12 code would be reported as broken.
_TYPE_ALIAS_RE = re.compile(r"^(\s*)type\s+([A-Za-z_]\w*)\s*=\s*(.+)$")


class ReformatError(Exception):
    """Raised when the source cannot be re-indented."""


# --------------------------------------------------------------------------
# tokenisation helpers
# --------------------------------------------------------------------------


@dataclass(slots=True)
class LogicalLine:
    """One logical line: the physical lines that form a single statement."""

    #: Physical line numbers (1-based) that belong to this logical line.
    physical: list[int] = field(default_factory=list)
    #: The source text of every physical line (without the trailing newline).
    texts: list[str] = field(default_factory=list)
    #: Tokens of the logical line (comments and NL are not included).
    tokens: list[tokenize.TokenInfo] = field(default_factory=list)
    #: ``True`` when the logical line opens an indented suite.
    opens_block: bool = False
    #: ``True`` when the logical line is a one-liner suite (``if x: y``).
    inline_suite: bool = False
    #: The physical lines that belong to a multi-line string.  Their text
    #: is part of the string and must not be re-indented -- indenting them
    #: would change the value of the string.
    verbatim: set[int] = field(default_factory=set)

    @property
    def head(self) -> str:
        """The first keyword/identifier of the logical line."""
        return self.tokens[0].string if self.tokens else ""

    @property
    def is_code(self) -> bool:
        return bool(self.tokens)

    @property
    def is_decorator(self) -> bool:
        """``True`` for a decorator line (``@dataclass``)."""
        return bool(self.tokens) and self.tokens[0].type == tokenize.OP and self.tokens[0].string == "@"


def _logical_lines(source: str) -> list[LogicalLine]:
    """Group the token stream of *source* into logical lines."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError) as exc:  # pragma: no cover
        raise ReformatError(f"cannot tokenize input: {exc}") from exc

    physical = source.splitlines()
    result: list[LogicalLine] = []
    current = LogicalLine()
    used: set[int] = set()

    def close_current() -> None:
        nonlocal current
        if current.physical:
            # The first line of a logical line carries the indentation;
            # only the continuation lines of a multi-line string have to
            # keep their text unchanged.
            current.verbatim = (set(current.physical) & verbatim) - {current.physical[0]}
            _finalise(current)
            result.append(current)
        current = LogicalLine()

    #: Nesting depth of ``(``/``[``/``{``.  Inside brackets the tokeniser
    #: emits ``NL`` for every physical line break, so the depth has to be
    #: tracked to know whether a ``NL`` really ends a logical line.
    depth = 0
    #: The physical lines that belong to a multi-line string.  They must
    #: not be re-indented, because their text is part of the string.
    verbatim: set[int] = set()

    for tok in tokens:
        ttype = tok.type
        if ttype in (tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING):
            continue
        if ttype == tokenize.ENDMARKER:
            break

        if ttype == tokenize.NEWLINE:
            # A logical line ends here.  ``NEWLINE`` is only emitted when
            # the bracket depth is zero.
            close_current()
            continue

        if ttype == tokenize.NL:
            if depth > 0:
                # Inside brackets: just a physical line break of the current
                # logical line.
                continue
            # A blank line or a comment-only line.  It cannot be part of a
            # logical line, so it terminates the current one.
            if current.physical:
                close_current()
            lineno = tok.start[0]
            if lineno in used:
                continue
            text = physical[lineno - 1] if lineno - 1 < len(physical) else ""
            used.add(lineno)
            current = LogicalLine(physical=[lineno], texts=[text])
            close_current()
            continue

        if ttype == tokenize.COMMENT:
            # A comment inside a logical line does not break the line, but
            # it has to be kept: the physical line it sits on carries
            # information that the code itself does not.
            lineno = tok.start[0]
            if lineno not in used:
                used.add(lineno)
                current.physical.append(lineno)
                current.texts.append(physical[lineno - 1] if lineno - 1 < len(physical) else "")
            continue

        if ttype == tokenize.OP:
            if tok.string in "([{":
                depth += 1
            elif tok.string in ")]}":
                depth -= 1

        if ttype == tokenize.STRING and tok.start[0] != tok.end[0]:
            # A multi-line string: every line it spans is part of the
            # string and must keep its text unchanged.
            verbatim.update(range(tok.start[0], tok.end[0] + 1))

        # A real code token.
        for lineno in range(tok.start[0], tok.end[0] + 1):
            if lineno in used:
                continue
            used.add(lineno)
            current.physical.append(lineno)
            current.texts.append(physical[lineno - 1] if lineno - 1 < len(physical) else "")
        current.tokens.append(tok)

    close_current()
    return result


def _finalise(ll: LogicalLine) -> None:
    """Classify a logical line (block opener / one-liner suite)."""
    if not ll.tokens:
        ll.opens_block = False
        ll.inline_suite = False
        return

    depth = 0
    last = None
    colon_at: int | None = None
    for idx, tok in enumerate(ll.tokens):
        if tok.type == tokenize.OP:
            if tok.string in "([{":
                depth += 1
            elif tok.string in ")]}":
                depth -= 1
            elif tok.string == ":" and depth == 0:
                colon_at = idx
        if tok.type not in (tokenize.COMMENT, tokenize.NL):
            last = tok

    if last is None:
        ll.opens_block = False
        ll.inline_suite = False
        return

    if last.type == tokenize.OP and last.string == ":" and depth == 0:
        ll.opens_block = True
        ll.inline_suite = False
    else:
        ll.opens_block = False
        ll.inline_suite = colon_at is not None and colon_at < len(ll.tokens) - 1


# --------------------------------------------------------------------------
# block structure inference
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Frame:
    """One open suite in the reconstructed block structure."""

    #: Indentation level of the suite *body*.
    level: int
    #: Keyword that opened the frame (``if``, ``for``, ``def``, ...).
    keyword: str
    #: Indentation level of the header that opened the suite.
    header_level: int = 0
    #: The logical line the frame was opened by.
    opened_at: int = -1
    #: ``True`` when the suite already contains a terminating statement, so
    #: the next statement cannot belong to it any more.
    terminated: bool = False
    #: ``True`` when the suite already received at least one statement.
    filled: bool = False
    #: ``True`` when the frame was opened by a clause keyword (``elif``,
    #: ``else``, ``except``, ``finally``).  Such a suite may still be
    #: continued by another clause of the same chain.
    from_clause: bool = False
    #: The keyword of the construct this frame belongs to.  For a frame
    #: that was opened by a clause keyword this stays the keyword of the
    #: *original* construct (``if`` for an ``if``/``elif``/``else`` chain),
    #: so that the next clause of the chain can be recognised.
    chain: str = ""
    #: ``True`` when the chain of the construct is closed, so that no
    #: further clause can continue it.  An ``else`` closes the chain of an
    #: ``if``/``elif``; a ``finally`` closes the chain of a ``try``.
    chain_closed: bool = False

    def __post_init__(self) -> None:
        if not self.chain:
            self.chain = self.keyword


def _accepts_clause(keyword: str, head: str) -> bool:
    """``True`` when a clause keyword can continue a construct."""
    if head in ("except", "finally"):
        return keyword == "try"
    if head == "elif":
        # ``elif`` continues the chain an ``if`` started.
        return keyword in ("if", "elif")
    if head == "else":
        # ``else`` closes an ``if``/``elif`` chain or a loop.
        return keyword in ("if", "elif") or keyword in LOOP_KEYWORDS
    if head == "case":
        return keyword == "match"
    return False


class Indenter:
    """Computes the indentation level of every logical line.

    The reconstruction is driven by a small set of rules that mirror the
    way Python itself decides where a suite ends:

    * A *clause* keyword (``else``, ``elif``, ``except``, ``finally``,
      ``case``) continues the innermost matching construct that is still
      open and closes every suite opened inside it.
    * A *terminating* statement (``return``, ``raise``, ``break``,
      ``continue``, ``pass``) ends the suite it belongs to.
    * A *definition* (``def``/``class``) never nests inside another
      definition.  The only exception is a method, i.e. a ``def`` that is
      the first statement of a class body.
    * A block header that follows a finished suite starts a new suite next
      to it.  Whether the suite is really finished is not always visible
      in a flattened file, so those places are recorded as decisions and can
      be flipped by the caller.

    Two kinds of decisions are not always visible in a flattened file and
    are therefore made configurable:

    ``nested``
        Does a block header open a nested suite or start a new suite next
        to the enclosing one?
    ``clause_of``
        Which construct does a clause keyword continue?
    """

    def __init__(self, lines: Sequence[LogicalLine]) -> None:
        self.lines = list(lines)
        self.levels: list[int] = [0] * len(self.lines)
        #: The block headers whose nesting was a decision.
        self.decisions: list[int] = []
        #: The clause keywords and the construct they continue.
        self.clause_targets: dict[int, int] = {}
        #: The construct a clause belongs to, together with the construct
        #: that encloses it.  The enclosing construct is the place where a
        #: standalone region around the clause starts.
        self.clause_parents: dict[int, int] = {}

    # -- classification --------------------------------------------------
    @staticmethod
    def _is_clause(ll: LogicalLine) -> bool:
        """``True`` for ``else``/``elif``/``except``/``finally``/``case``."""
        head = ll.head
        if head == "except":
            return True
        if head not in CLAUSE_KEYWORDS:
            return False
        # ``else``/``elif``/``finally``/``case`` must really start the
        # statement, not be used as a plain name inside an expression.
        if len(ll.tokens) >= 2 and ll.tokens[1].type == tokenize.OP:
            if ll.tokens[1].string not in ("(", ":"):
                return False
        return True

    @staticmethod
    def _is_terminator(ll: LogicalLine) -> bool:
        return ll.head in TERMINATORS

    @staticmethod
    def _keyword_of(ll: LogicalLine) -> str:
        head = ll.head
        if head == "async":
            for tok in ll.tokens[1:]:
                if tok.type == tokenize.NAME and tok.string in ("def", "for", "with"):
                    return tok.string
        return head

    # -- forward look-ahead ----------------------------------------------
    def _try_has_handlers(self, index: int) -> bool:
        """``True`` when the ``try`` at *index* has an except/finally.

        A ``return`` inside a ``try`` block that has a handler does not
        end the function: the handler may catch the exception and let the
        execution continue behind the ``try`` statement.
        """
        for j in range(index + 1, len(self.lines)):
            ll = self.lines[j]
            if not ll.is_code:
                continue
            if ll.head in ("except", "finally"):
                return True
            if ll.head in CLAUSE_KEYWORDS:
                # A clause of another construct: the ``try`` has no
                # handler of its own.
                return False
            if ll.opens_block and self._keyword_of(ll) in ("def", "class"):
                return False
        return False

    def _chain_is_continued(self, index: int) -> bool:
        """``True`` when the construct at *index* gets a clause keyword.

        The question decides how a block header inside the construct is
        interpreted: a construct that is continued by an ``elif``/``else``
        keeps its suite open, so a header that follows it opens a *nested*
        suite; a construct that is not continued ends at that header, so
        the header starts a suite *next to* it.

        The scan walks forward and keeps a stack of the constructs that are
        open.  A clause keyword belongs to the construct that owns the
        statement in front of it -- the innermost open construct whose
        chain accepts the clause and that is not hidden behind a
        definition.
        """
        lines = self.lines
        # The stack holds the keyword of every construct that is open.
        # The construct at *index* is marked with a ``*`` so that it can
        # be recognised in the stack.  A construct whose chain is closed
        # (an ``else``/``finally`` was already seen) is marked with ``!``.
        stack: list[str] = []
        #: The stack position that owns the statement in front of the
        #: clause.  A clause continues the construct that owns the
        #: statement before it -- that is how Python itself decides.
        owner: int | None = None

        # The construct the scan starts at is open right away.  When the
        # scan starts at a clause keyword, the construct it continues is
        # the one in front of it.
        start_keyword = self._keyword_of(lines[index])
        starts_at_clause = lines[index].head in CLAUSE_KEYWORDS
        if starts_at_clause:
            for j in range(index - 1, -1, -1):
                if lines[j].is_code and lines[j].opens_block:
                    start_keyword = self._keyword_of(lines[j])
                    break
        stack.append(start_keyword + "*")

        for j in range(index, len(lines)):
            ll = lines[j]
            if not ll.is_code:
                continue
            head = ll.head

            if j == index and not starts_at_clause:
                # The construct at the start is already on the stack.
                owner = len(stack) - 1
                continue

            if head in CLAUSE_KEYWORDS:
                if not stack:
                    continue
                # A clause cannot live inside a definition: it continues a
                # construct that encloses the definition.
                barrier = None
                for position in range(len(stack) - 1, -1, -1):
                    if stack[position].rstrip("*!") in DEFINITION_KEYWORDS:
                        barrier = position
                        break

                # The clause continues the construct that owns the
                # statement in front of it.  When that construct does not
                # accept the clause, the clause belongs to an outer one.
                start = owner if owner is not None else len(stack) - 1
                while stack:
                    position = min(start, len(stack) - 1)
                    innermost = stack[position]
                    closed = innermost.endswith("!")
                    marked = innermost.endswith("*")
                    keyword = innermost.rstrip("*!")
                    inside_definition = barrier is not None and position > barrier
                    if not closed and not inside_definition and _accepts_clause(keyword, head):
                        # The clause replaces the suite of the construct.
                        if marked:
                            return True
                        # Close the suites that were opened inside it.
                        del stack[position + 1 :]
                        stack[position] = keyword
                        if head in ("else", "finally"):
                            # The chain is closed: no further clause can
                            # continue this construct.
                            stack[position] += "!"
                        owner = position
                        break
                    # The construct cannot be continued: close it.
                    if position < len(stack) - 1:
                        del stack[position + 1 :]
                    stack.pop()
                    owner = None
                    if marked:
                        # The construct we are looking at is over.
                        return False
                    start = min(start, len(stack) - 1)
                continue

            keyword = self._keyword_of(ll)
            if ll.opens_block and not ll.inline_suite:
                if keyword in DEFINITION_KEYWORDS:
                    # A definition closes the suites that were opened
                    # inside the enclosing definition.
                    while stack:
                        innermost = stack[-1]
                        marked = innermost.endswith("*")
                        if innermost.rstrip("*!") in DEFINITION_KEYWORDS:
                            break
                        if marked:
                            # The construct we are looking at may well
                            # contain the definition, so it stays open.
                            break
                        stack.pop()
                stack.append(keyword + ("*" if j == index else ""))
                # The header itself belongs to the suite it opens.
                owner = len(stack) - 1
            elif ll.opens_block:
                # A one-liner suite does not open a suite of its own.
                owner = len(stack) - 1
            else:
                # A simple statement belongs to the innermost open suite.
                owner = len(stack) - 1
                if keyword in TERMINATORS and stack:
                    # A terminating statement ends the innermost suite.
                    innermost = stack[-1]
                    if innermost.endswith("*"):
                        return False
                    stack.pop()
                    owner = len(stack) - 1
        return False

    # -- main ------------------------------------------------------------
    def run(
        self,
        nested: set[int] | None = None,
        clause_of: dict[int, int] | None = None,
        sibling: set[int] | None = None,
        escaped: set[int] | None = None,
    ) -> list[int]:
        """Compute the indentation levels.

        *nested* contains the indices of the block headers that have to open
        a nested suite.

        *sibling* contains the indices of the block headers that start a
        new suite *next to* the enclosing one.  A header that is in neither
        set opens a nested suite: a suite can only be closed when it is
        provably over, and in a flattened file that is rarely the case.

        *clause_of* maps the index of a clause keyword to the index of the
        construct it continues.

        *escaped* contains the indices of the statements that leave the
        suite they are written in and start a new suite *next to* it.  A
        constant that follows the body of a class may well belong to the
        module and not to the class.
        """
        nested = nested or set()
        clause_of = clause_of or {}
        sibling = sibling or set()
        escaped = escaped or set()
        #: Headers that the forward look-ahead proved to open a nested
        #: suite.  They are forced to be nested, whatever the caller says.
        forced_nested: set[int] = set()
        #: The constructs that a clause keyword continues.  Their suite must
        #: not be closed before the clause shows up.
        sticky = set(clause_of.values())
        lines = self.lines
        n = len(lines)
        self.levels = [0] * n
        self.decisions = []
        self.clause_targets = {}
        #: The construct whose body every statement belongs to.
        self.owners: dict[int, int] = {}
        #: The keyword of the construct whose body a statement belongs to.
        self.owner_keywords: dict[int, str] = {}
        #: Stack of open suites.  ``frames[-1]`` is the innermost suite.
        frames: list[Frame] = []
        #: Every suite that was opened, by the line that opened it.  It
        #: allows a closed suite to be revived when a clause keyword shows
        #: up that belongs to it.
        known: dict[int, Frame] = {}

        i = 0
        while i < n:
            ll = lines[i]
            if not ll.is_code:
                # Blank lines and comments belong to the current suite.
                self.levels[i] = frames[-1].level if frames else 0
                i += 1
                continue

            if self._is_clause(ll):
                i = self._handle_clause(lines, i, frames, clause_of, sticky)
                continue

            if ll.is_decorator:
                # A decorator belongs to the definition that follows it and
                # shares its indentation level.
                i = self._handle_decorator(lines, i, frames, nested)
                continue

            keyword = self._keyword_of(ll)
            is_header = ll.opens_block
            is_definition = is_header and keyword in DEFINITION_KEYWORDS

            # -- close the suites that cannot hold this statement --------
            if frames:
                top = frames[-1]
                if is_definition:
                    # A ``def`` in the body of a class is a method.  It
                    # is a method when it is the first statement of the
                    # class body; a ``def`` that follows another member
                    # may well be a function of the module.  The caller
                    # decides -- the scope oracle can tell the two apart.
                    is_method = top.keyword == "class" and keyword == "def" and not top.filled and not top.terminated
                    if not is_method:
                        # A definition starts a new suite next to the
                        # enclosing one: close every suite that was
                        # opened inside it.
                        #
                        # A definition may also live inside a block -- a
                        # function inside an ``if`` is quite normal -- or
                        # inside another definition as a nested function.
                        # That reading is recorded as a decision, so that
                        # the caller can choose it.
                        self.decisions.append(i)
                        if i in nested:
                            # The definition opens a nested suite: the
                            # blocks around it are closed, the
                            # definitions around it stay open.  A
                            # function inside a function is a nested
                            # function.
                            while frames:
                                frame = frames[-1]
                                if frame.opened_at in sticky:
                                    break
                                if frame.keyword in DEFINITION_KEYWORDS:
                                    break
                                frames.pop()
                        else:
                            # The definition starts a new suite next to
                            # the enclosing one: close every suite that
                            # was opened inside it.
                            while frames:
                                frame = frames[-1]
                                if frame.opened_at in sticky:
                                    break
                                frames.pop()
                                if frame.keyword in DEFINITION_KEYWORDS:
                                    break
                elif is_header:
                    # A plain block header never closes a definition: a
                    # function or class body usually contains ``if``/``for``
                    # statements of its own.  A suite that was opened by a
                    # clause keyword may still be continued by the next
                    # clause of the same chain, so it is kept as well.  A
                    # ``try`` suite is always continued by an ``except``/
                    # ``finally`` clause, so it is kept as well.
                    #
                    # A definition whose suite was left by a ``return`` or
                    # a ``raise`` *is* closed though.
                    if (
                        (top.keyword not in DEFINITION_KEYWORDS or top.terminated)
                        and not (top.from_clause and not top.chain_closed)
                        and top.keyword != "try"
                        and top.opened_at not in sticky
                        and (top.filled or top.terminated)
                    ):
                        # The suite looks finished, so the header starts a
                        # new suite next to it -- unless the caller decided
                        # that it nests after all.
                        #
                        # An ``if``/``elif`` construct whose chain is
                        # continued by another clause keeps its suite
                        # open: the header belongs *inside* that suite.
                        if (
                            top.chain in ("if", "elif")
                            and not top.chain_closed
                            and self._chain_is_continued(top.opened_at)
                        ):
                            # The header is nested inside the construct
                            # whose chain continues; that is not a decision
                            # the caller may overrule.
                            forced_nested.add(i)
                        elif i in sibling:
                            self.decisions.append(i)
                            frames.pop()
                        elif i in nested:
                            pass
                        else:
                            self.decisions.append(i)
                            frames.pop()
                elif top.terminated and top.opened_at not in sticky:
                    # The suite above is over: the statement starts a new
                    # suite next to it.  A ``return`` or a ``raise`` ends
                    # every suite up to the enclosing definition, so all
                    # of them have to be closed.
                    self.decisions.append(i)
                    frames.pop()
                elif i in escaped and top.keyword in DEFINITION_KEYWORDS:
                    # The statement leaves the body of the definition it
                    # is written in and starts a new suite next to it.
                    # A constant that follows a class may well belong to
                    # the module and not to the class.
                    self.decisions.append(i)
                    frames.pop()

            level = frames[-1].level if frames else 0
            self.levels[i] = level

            if frames:
                frames[-1].filled = True
                #: The construct whose body the statement belongs to.
                self.owners[i] = frames[-1].opened_at
                self.owner_keywords[i] = frames[-1].keyword

            if is_header:
                frame = Frame(
                    level=level + 1,
                    keyword=keyword,
                    header_level=level,
                    opened_at=i,
                )
                known[i] = frame
                frames.append(frame)
            elif self._is_terminator(ll) and frames:
                # The suite this statement belongs to cannot contain any
                # further statement.  A ``return`` or ``raise`` leaves the
                # whole function, so every suite up to the enclosing
                # definition is over; a ``break`` or ``continue`` leaves
                # the innermost loop.
                head = ll.head
                if head in ("return", "raise"):
                    # A ``return`` or a ``raise`` ends the suite it is
                    # written in -- the block around it may well continue:
                    #
                    #     if not p:
                    #         return None
                    #     x = 1
                    frames[-1].terminated = True
                elif head in ("break", "continue"):
                    # A ``break`` or a ``continue`` leaves the innermost
                    # loop.  The suite it was written in ends, and so
                    # does the suite of the loop -- unless the statement
                    # sits inside the body of a clause (``elif``,
                    # ``else``, ``except``), which may well continue.
                    frames[-1].terminated = True
                    if not frames[-1].from_clause:
                        for frame in reversed(frames[:-1]):
                            if frame.from_clause:
                                # The body of a clause may still be
                                # continued by another clause of the
                                # same chain.
                                break
                            frame.terminated = True
                            if frame.keyword in LOOP_KEYWORDS:
                                break
                            if frame.keyword in DEFINITION_KEYWORDS:
                                break
                else:
                    # ``pass`` only ends its own suite.
                    frames[-1].terminated = True

            i += 1

        return self.levels

    # -- decorator handling -----------------------------------------------
    def _handle_decorator(
        self,
        lines: list[LogicalLine],
        i: int,
        frames: list[Frame],
        nested: set[int],
    ) -> int:
        """Give a decorator the level of the definition it decorates."""
        # Find the definition the decorator belongs to.
        j = i + 1
        while j < len(lines) and not lines[j].is_code:
            j += 1
        target = None
        if j < len(lines):
            keyword = self._keyword_of(lines[j])
            if lines[j].opens_block and keyword in DEFINITION_KEYWORDS:
                target = keyword

        if target is not None:
            # Close the definition frames the same way the definition itself
            # would, so that the decorator and its definition end up on the
            # same level.
            if frames:
                top = frames[-1]
                if not (top.keyword == "class" and target == "def" and not top.filled):
                    while frames:
                        frame = frames.pop()
                        if frame.keyword in DEFINITION_KEYWORDS:
                            break

        level = frames[-1].level if frames else 0
        self.levels[i] = level
        if frames:
            frames[-1].filled = True
        return i + 1

    # -- clause handling -------------------------------------------------
    def _handle_clause(
        self,
        lines: list[LogicalLine],
        i: int,
        frames: list[Frame],
        clause_of: dict[int, int],
        sticky: set[int] | None = None,
    ) -> int:
        """Handle a clause keyword such as ``else`` or ``except``."""
        ll = lines[i]

        # The caller may have decided which construct the clause continues.
        target_idx = None
        wanted = clause_of.get(i)
        if wanted is not None:
            for index in range(len(frames) - 1, -1, -1):
                if frames[index].opened_at == wanted:
                    target_idx = index
                    break

        if target_idx is None:
            target_idx = self._matching_frame(frames, ll)
        if target_idx is None:
            # No matching construct -- treat the clause as a plain statement.
            self.levels[i] = frames[-1].level if frames else 0
            return i + 1

        # Close every suite that was opened inside the finished construct.
        target = frames[target_idx]
        del frames[target_idx + 1 :]
        clause_level = target.header_level  # the clause shares the header level
        self.levels[i] = clause_level
        clause_level = target.header_level  # the clause shares the header level
        self.levels[i] = clause_level

        # The clause opens a fresh suite at the same level as the construct.
        # The chain keyword is inherited from the construct the clause
        # continues, so that a second clause of the same chain (``elif``
        # after ``else``, ``else`` after ``elif``) is recognised, too.
        #
        # A closing clause (``else``, ``finally``) ends the chain: no
        # further clause can continue it.
        chain_closed = ll.head in ("else", "finally")
        frames[target_idx] = Frame(
            level=clause_level + 1,
            keyword=ll.head,
            header_level=clause_level,
            opened_at=target.opened_at,
            from_clause=True,
            chain=target.chain,
            chain_closed=chain_closed,
        )
        # Remember which construct the clause continues.
        self.clause_targets[i] = target.opened_at
        # The construct that encloses the one the clause continues.  A
        # standalone region around the clause has to start there, so that
        # the clause and the construct it continues are siblings.
        if target_idx > 0:
            self.clause_parents[i] = frames[target_idx - 1].opened_at
        else:
            self.clause_parents[i] = -1
        return i + 1

    @staticmethod
    def _matching_frame(frames: list[Frame], ll: LogicalLine) -> int | None:
        """Index of the innermost frame a clause keyword belongs to.

        A clause keyword (``elif``, ``else``, ``except``, ``finally``) is
        written at the same indentation level as the header of the construct
        it continues.  Therefore the frame it belongs to is the innermost
        frame that accepts the keyword *and* whose header level is lower
        than the header level of every frame above it -- a frame that sits
        at the same level as one of the frames above it belongs to a
        different chain.
        """
        head = ll.head
        for idx in range(len(frames) - 1, -1, -1):
            frame = frames[idx]
            if frame.chain_closed:
                continue
            if not _accepts_clause(frame.chain, head):
                continue
            if all(above.header_level > frame.header_level for above in frames[idx + 1 :]):
                return idx
        return None


# --------------------------------------------------------------------------
# rendering and validation
# --------------------------------------------------------------------------


def render(lines: Sequence[LogicalLine], levels: Sequence[int]) -> str:
    """Render the logical lines with the computed indentation."""
    return render_with_map(lines, levels)[0]


def render_with_map(lines: Sequence[LogicalLine], levels: Sequence[int]) -> tuple[str, list[int]]:
    """Render the logical lines and return a line number map.

    The map has one entry per rendered line; the entry is the index of the
    logical line the rendered line belongs to (``-1`` for a blank line).
    The map makes it possible to translate the line number of a syntax
    error in the rendered text back to the logical line it came from.
    """
    out: list[str] = []
    mapping: list[int] = []
    for index, (ll, level) in enumerate(zip(lines, levels)):
        if not ll.is_code:
            for text in ll.texts:
                out.append("" if text.strip() == "" else INDENT_UNIT * level + text.strip())
                mapping.append(index)
            continue
        first = True
        for lineno, text in zip(ll.physical, ll.texts):
            stripped = text.strip()
            if stripped == "":
                out.append("")
                mapping.append(index)
                continue
            if lineno in ll.verbatim:
                # The line is part of a multi-line string.  Its text is
                # part of the value of the string, so it must not be
                # re-indented.
                out.append(text.rstrip())
                mapping.append(index)
                continue
            if first:
                out.append(INDENT_UNIT * level + stripped)
                first = False
            else:
                # Continuation lines get one extra level: they belong to the
                # same logical line, so any indentation is syntactically
                # irrelevant -- but this keeps the result readable.
                out.append(INDENT_UNIT * (level + 1) + stripped)
            mapping.append(index)
    return "\n".join(out) + "\n", mapping


def _sanitize(source: str) -> str:
    """Rewrite Python 3.12-only syntax for older parsers."""
    out = []
    for line in source.splitlines():
        match = _TYPE_ALIAS_RE.match(line)
        if match:
            out.append(f"{match.group(1)}{match.group(2)}: object = {match.group(3)}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def check_syntax(source: str) -> SyntaxError | None:
    """Return ``None`` when *source* parses, else the syntax error."""
    error: SyntaxError | None = None
    for candidate in (source, _sanitize(source)):
        try:
            ast.parse(candidate)
            return None
        except SyntaxError as exc:
            error = exc
    return error


# --------------------------------------------------------------------------
# the scope oracle
# --------------------------------------------------------------------------
#
# A flattened file often leaves one question open: does a statement belong
# to the block in front of it or to the block above?  Both readings are
# valid Python, so the parser cannot decide.  The *meaning* of the
# program can: a name that is read has to be bound before it is read, in
# the scope that reads it.
#
# The checker below walks the syntax tree with a scope stack and counts
# the names that are read before they are bound.  A layout that leaves no
# name unbound is more likely to be the original one.


class _Scope:
    """One lexical scope."""

    __slots__ = ("parent", "kind", "names", "globals", "nonlocals")

    def __init__(self, parent: "_Scope | None" = None, kind: str = "module"):
        self.parent = parent
        self.kind = kind
        self.names: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def bind(self, name: str) -> None:
        self.names.add(name)

    def knows(self, name: str) -> bool:
        scope: "_Scope | None" = self
        while scope is not None:
            if name in scope.names:
                return True
            scope = scope.parent
        return False


class _ScopeChecker:
    """Count the names that are read before they are bound."""

    def __init__(self) -> None:
        self.undefined: list[tuple[str, int]] = []
        self.scope = _Scope(None, "module")
        self.builtins = set(dir(builtins))
        #: The names the module binds.  They are visible everywhere, even
        #: before they are bound: a function body runs later than the
        #: module.
        self.module_names: set[str] = set()

    # -- helpers ---------------------------------------------------------
    def _bind(self, name: str) -> None:
        if name in self.scope.globals:
            self.module_names.add(name)
            return
        if name in self.scope.nonlocals:
            scope = self.scope.parent
            while scope is not None:
                if scope.kind == "function" or name in scope.nonlocals:
                    scope.bind(name)
                    return
                scope = scope.parent
            self.scope.bind(name)
            return
        self.scope.bind(name)
        if self.scope.kind == "module":
            self.module_names.add(name)

    def _bind_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value)

    def _collect_target_names(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.module_names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._collect_target_names(element)
        elif isinstance(target, ast.Starred):
            self._collect_target_names(target.value)

    def _collect_module_names(self, node: ast.stmt) -> None:
        """The names a top level statement binds."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.module_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                self._collect_target_names(target)
        elif isinstance(node, ast.AnnAssign):
            self._collect_target_names(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._collect_target_names(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self._collect_target_names(item.optional_vars)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                self.module_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                self.module_names.add(alias.asname or alias.name)

    # -- the walk --------------------------------------------------------
    def run(self, tree: ast.AST) -> list[tuple[str, int]]:
        body = tree.body if isinstance(tree, ast.Module) else []
        for statement in body:
            self._collect_module_names(statement)
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                self.module_names.update(node.names)
        self._body(body, self.scope)
        return self.undefined

    def _body(self, body: list[ast.stmt], scope: _Scope) -> None:
        previous = self.scope
        self.scope = scope
        for statement in body:
            self._statement(statement)
        self.scope = previous

    def _statement(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._bind(node.name)
            args = node.args
            inner = _Scope(self.scope, "function")
            for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                inner.bind(arg.arg)
            if args.vararg:
                inner.bind(args.vararg.arg)
            if args.kwarg:
                inner.bind(args.kwarg.arg)
            for decorator in node.decorator_list:
                self._expr(decorator)
            for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
                self._expr(default)
            self._body(node.body, inner)
            return
        if isinstance(node, ast.ClassDef):
            self._bind(node.name)
            inner = _Scope(self.scope, "class")
            for decorator in node.decorator_list:
                self._expr(decorator)
            for base in node.bases:
                self._expr(base)
            for keyword in node.keywords:
                self._expr(keyword.value)
            self._body(node.body, inner)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._bind(alias.asname or alias.name.split(".")[0])
            return
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                self._bind(alias.asname or alias.name)
            return
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._bind_target(target)
            self._expr(node.value)
            return
        if isinstance(node, ast.AnnAssign):
            self._bind_target(node.target)
            if node.value is not None:
                self._expr(node.value)
            return
        if isinstance(node, ast.AugAssign):
            self._expr(node.target)
            self._expr(node.value)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self._expr(node.iter)
            self._bind_target(node.target)
            self._body(node.body, self.scope)
            self._body(node.orelse, self.scope)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self._expr(item.context_expr)
                if item.optional_vars is not None:
                    self._bind_target(item.optional_vars)
            self._body(node.body, self.scope)
            return
        if isinstance(node, ast.Try):
            self._body(node.body, self.scope)
            for handler in node.handlers:
                if handler.type is not None:
                    self._expr(handler.type)
                if handler.name:
                    self._bind(handler.name)
                self._body(handler.body, self.scope)
            self._body(node.orelse, self.scope)
            self._body(node.finalbody, self.scope)
            return
        if isinstance(node, ast.If):
            self._expr(node.test)
            self._body(node.body, self.scope)
            self._body(node.orelse, self.scope)
            return
        if isinstance(node, ast.While):
            self._expr(node.test)
            self._body(node.body, self.scope)
            self._body(node.orelse, self.scope)
            return
        if isinstance(node, (ast.Expr, ast.Return, ast.Delete, ast.Assert)):
            for child in ast.iter_child_nodes(node):
                self._expr(child)
            return
        if isinstance(node, ast.Match):
            self._expr(node.subject)
            for case in node.cases:
                if case.guard is not None:
                    self._expr(case.guard)
                self._body(case.body, self.scope)
            return
        if isinstance(node, ast.Raise):
            if node.exc is not None:
                self._expr(node.exc)
            if node.cause is not None:
                self._expr(node.cause)
            return
        if isinstance(node, ast.Global):
            self.scope.globals.update(node.names)
            self.module_names.update(node.names)
            return
        if isinstance(node, ast.Nonlocal):
            self.scope.nonlocals.update(node.names)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self._statement(child)
            elif isinstance(child, ast.expr):
                self._expr(child)

    def _expr(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                if not self.scope.knows(node.id) and node.id not in self.builtins and node.id not in self.module_names:
                    self.undefined.append((node.id, node.lineno))
            else:
                self._bind(node.id)
            return
        if isinstance(node, ast.Lambda):
            args = node.args
            inner = _Scope(self.scope, "function")
            for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                inner.bind(arg.arg)
            if args.vararg:
                inner.bind(args.vararg.arg)
            if args.kwarg:
                inner.bind(args.kwarg.arg)
            for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
                self._expr(default)
            previous = self.scope
            self.scope = inner
            self._expr(node.body)
            self.scope = previous
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            # A comprehension has a scope of its own.
            inner = _Scope(self.scope, "comprehension")
            previous = self.scope
            self.scope = inner
            for generator in node.generators:
                self._expr(generator.iter)
                self._bind_target(generator.target)
                for condition in generator.ifs:
                    self._expr(condition)
            if isinstance(node, ast.DictComp):
                self._expr(node.key)
                self._expr(node.value)
            else:
                self._expr(node.elt)
            self.scope = previous
            return
        if isinstance(node, ast.NamedExpr):
            self._expr(node.value)
            if self.scope.kind == "comprehension":
                # A walrus in a comprehension binds in the enclosing scope.
                if self.scope.parent is not None:
                    self.scope.parent.bind(node.target.id)  # type: ignore[attr-defined]
            else:
                self._bind_target(node.target)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._statement(node)
            return
        if isinstance(node, ast.ClassDef):
            self._statement(node)
            return
        for child in ast.iter_child_nodes(node):
            self._expr(child)


def undefined_names(source: str) -> list[tuple[str, int]]:
    """The names *source* reads before it binds them."""
    tree = ast.parse(_sanitize(source))
    return _ScopeChecker().run(tree)


def analyse(source: str) -> tuple[SyntaxError | None, list[tuple[str, int]]]:
    """Parse *source* once and report the syntax error and the unbound names.

    Doing both at once saves a second parse of the whole file, which
    matters: the search parses the file thousands of times.
    """
    error: SyntaxError | None = None
    tree: ast.AST | None = None
    for candidate in (source, _sanitize(source)):
        try:
            tree = ast.parse(candidate)
            break
        except SyntaxError as exc:
            error = exc
    if tree is None:
        return error, []
    return None, _ScopeChecker().run(tree)


def _clause_candidates(lines: Sequence[LogicalLine], indenter: Indenter, index: int) -> list[int]:
    """The constructs a clause keyword at *index* could continue.

    The candidates are the block headers in front of the clause that accept
    the keyword, the closest one first.
    """
    ll = lines[index]
    head = ll.head
    candidates = []
    for j in range(index - 1, -1, -1):
        candidate = lines[j]
        if not candidate.is_code or not candidate.opens_block:
            continue
        keyword = indenter._keyword_of(candidate)
        if _accepts_clause(keyword, head):
            candidates.append(j)
    return candidates


def _clauses_after(lines: Sequence[LogicalLine], indenter: Indenter, index: int) -> list[int]:
    """The clause keywords that could continue the construct at *index*.

    A ``try`` statement is continued by an ``except``/``finally`` clause
    and a loop may be followed by an ``else``.  The candidates are the
    clause keywords that follow the construct, the closest one first.

    Clauses that belong to a *nested* construct do not count: the ``else``
    of an ``if`` that lives inside the ``try`` suite belongs to that
    ``if``, not to the ``try``.  The scan therefore keeps track of the
    constructs that are open.
    """
    wanted = {"except", "finally"} if lines[index].head == "try" else {"else"}
    candidates = []
    #: The constructs that are open inside the suite of *index*.
    stack: list[str] = []

    for j in range(index + 1, len(lines)):
        candidate = lines[j]
        if not candidate.is_code:
            continue
        head = candidate.head

        if head in CLAUSE_KEYWORDS:
            if not stack:
                # The clause is at the level of the construct itself.
                if head in wanted:
                    candidates.append(j)
                else:
                    break
                continue
            # The clause belongs to the innermost open construct.
            keyword = stack[-1]
            if _accepts_clause(keyword, head):
                if head in ("else", "finally"):
                    # The chain is closed.
                    stack[-1] = keyword + "!"
                continue
            # The clause closes the suites that were opened inside the
            # construct; look for the construct it belongs to.
            while stack:
                keyword = stack[-1].rstrip("!")
                if _accepts_clause(keyword, head):
                    break
                stack.pop()
            if not stack:
                # The clause belongs to the construct itself.
                if head in wanted:
                    candidates.append(j)
                else:
                    break
                stack.append(keyword + ("!" if head in ("else", "finally") else ""))
            continue

        keyword = indenter._keyword_of(candidate)
        if candidate.opens_block and not candidate.inline_suite:
            stack.append(keyword)
        elif candidate.opens_block:
            continue
        elif keyword in TERMINATORS and stack:
            stack.pop()
    return candidates


def _region(lines: Sequence[LogicalLine], levels: Sequence[int], start: int, end: int) -> str:
    """Render the logical lines *start*..*end* as a standalone program."""
    out: list[str] = []
    base = levels[start]
    for ll, level in zip(lines[start : end + 1], levels[start : end + 1]):
        if not ll.is_code:
            for text in ll.texts:
                out.append("" if text.strip() == "" else INDENT_UNIT * (level - base) + text.strip())
            continue
        first = True
        for lineno, text in zip(ll.physical, ll.texts):
            stripped = text.strip()
            if stripped == "":
                out.append("")
                continue
            if lineno in ll.verbatim:
                # The line belongs to a multi-line string and must not be
                # re-indented.
                out.append(text.rstrip())
                continue
            out.append(INDENT_UNIT * (level - base if first else level - base + 1) + stripped)
            first = False
    return "\n".join(out) + "\n"


def _depth(levels: Sequence[int]) -> int:
    """The deepest indentation level of *levels*."""
    return max(levels) if levels else 0


def _escape_candidates(
    lines: Sequence[LogicalLine],
    indenter: Indenter,
    levels: Sequence[int],
) -> list[int]:
    """The statements that could leave the body of a definition.

    A constant that follows the body of a class may well belong to the
    module and not to the class.  Only the statements that are written
    directly in the body of a ``def`` or a ``class`` can do that, and
    only the *last* such statement of a body is worth trying -- the
    others would leave the body in the middle.
    """
    #: The last statement of every definition body.
    last_of: dict[int, int] = {}
    for index, ll in enumerate(lines):
        if not ll.is_code:
            continue
        owner = indenter.owners.get(index)
        if owner is None:
            continue
        if indenter.owner_keywords.get(index) not in DEFINITION_KEYWORDS:
            continue
        last_of[owner] = index

    candidates: list[int] = []
    for owner, index in last_of.items():
        ll = lines[index]
        if ll.opens_block or ll.head in CLAUSE_KEYWORDS:
            continue
        candidates.append(index)
    return candidates


def reformat(source: str) -> str:
    """Re-indent *source* and return the result."""
    lines = _logical_lines(source)
    indenter = Indenter(lines)

    #: The block headers that open a nested suite.
    nested: set[int] = set()
    #: The block headers that start a new suite next to the enclosing one.
    sibling: set[int] = set()
    #: The statements that leave the body of the definition they are
    #: written in.
    escaped: set[int] = set()
    #: The construct every clause keyword continues.
    clause_of: dict[int, int] = {}

    def attempt_error() -> tuple[list[int], SyntaxError | None, list[int]]:
        """Compute the levels and check whether they produce valid Python."""
        levels = indenter.run(nested, clause_of, sibling, escaped)
        text, mapping = render_with_map(lines, levels)
        return levels, check_syntax(text), mapping

    def failing_index(error: SyntaxError, mapping: Sequence[int]) -> int:
        """The logical line the parser stumbled over.

        The line number of the error refers to the *rendered* text, so it
        has to be translated back with the map produced by
        :func:`render_with_map}.
        """
        lineno = error.lineno or 0
        index: int | None = None
        if 1 <= lineno <= len(mapping):
            candidate = mapping[lineno - 1]
            if candidate >= 0:
                index = candidate
        if index is None:
            return len(lines) - 1
        return index

    def error_position(error: SyntaxError, mapping: Sequence[int]) -> int:
        """A number that grows with the position of the error.

        The number is the index of the logical line the parser stumbled
        over.  Two errors are compared by this number: the error that
        happens further into the file is the better one.
        """
        lineno = error.lineno or 0
        if 1 <= lineno <= len(mapping):
            candidate = mapping[lineno - 1]
            if candidate >= 0:
                return candidate
        return len(lines)

    def reported_index(error: SyntaxError, mapping: Sequence[int]) -> int:
        """The logical line the error message points at.

        Some messages name the *offending* construct rather than the
        statement that follows it -- "expected an indented block after
        function definition on line N" points at the ``def`` on line N.
        """
        lineno = error.lineno or 0
        if 1 <= lineno <= len(mapping):
            candidate = mapping[lineno - 1]
            if candidate >= 0:
                return candidate
        return 0

    #: The states that were already tried, to avoid endless loops.
    tried: set[tuple] = set()
    #: The number of search steps; the search gives up when it explodes.
    budget = 20000

    def local_region(index: int) -> tuple[int, int]:
        """The region around *index* that is searched for a repair.

        The region starts at a block header that encloses the failure and
        ends far enough behind it to contain the whole construct.  It is
        rendered as a standalone program, so it has to start at a place
        where the enclosing suite begins.
        """
        # The region starts at the closest block header in front of the
        # failure that is not itself nested too deeply.
        start = index
        while start > 0:
            candidate = start - 1
            if not lines[candidate].is_code or not lines[candidate].opens_block:
                start = candidate
                continue
            if index - candidate >= _REGION_SIZE:
                break
            start = candidate
            break
        end = index
        while end < len(lines) - 1:
            end += 1
            if end - index >= _REGION_SIZE:
                break
        return start, end

    def scope_pass() -> None:
        """Improve the layout with the help of the scope oracle.

        The parser is happy with many readings of a flattened file, but
        most of them leave names unbound: a constant that ends up inside a
        function is not visible where it is used any more.  Every
        decision that is still open is therefore tried once more, and
        the reading that binds the most names is kept.

        The pass runs over the decisions in the order in which they
        appear in the file.  A decision that does not change the number
        of unbound names is left alone -- there is no reason to prefer
        one reading over the other then.  The whole file is swept again
        and again as long as something changes.

        Only the names that are read *near* the decision are counted: a
        decision far away from a name cannot change whether the name is
        bound, and counting every name of the file would make the pass
        very slow.
        """
        for _round in range(3):
            best_levels, _error, _mapping = attempt_error()
            best_text = render(lines, best_levels)
            error, problems = analyse(best_text)
            if error is not None:
                return
            improved = False
            #: The number of unbound names of the layout that is in place.
            best_score = len(problems)
            #: The deepest indentation the layout may reach.  The first
            #: reading of the file gives the rough shape of the program;
            #: a reading that nests the file much deeper than that is
            #: almost certainly wrong, even when it binds more names.
            depth_limit = _depth(best_levels) + 4

            decisions = list(indenter.decisions)
            for decision in decisions:
                # Try the reading that is not in place at the moment.  A
                # header that is in neither set starts a suite next to
                # the enclosing one, so that is the reading that is
                # tried first.
                was_nested = decision in nested
                was_sibling = decision in sibling
                if was_nested:
                    nested.discard(decision)
                    sibling.add(decision)
                else:
                    sibling.discard(decision)
                    nested.add(decision)
                trial_levels = indenter.run(nested, clause_of, sibling, escaped)
                trial_text, trial_mapping = render_with_map(lines, trial_levels)
                score = None
                if check_syntax(trial_text) is None and _depth(trial_levels) <= depth_limit:
                    score = len(undefined_names(trial_text))
                if score is None:
                    # The reading does not even parse -- or it nests the
                    # file far deeper than any real program would.  Keep
                    # the old one.
                    nested.discard(decision)
                    sibling.discard(decision)
                    if was_nested:
                        nested.add(decision)
                    elif was_sibling:
                        sibling.add(decision)
                    continue
                if score < best_score:
                    best_score = score
                    best_levels = trial_levels
                    improved = True
                else:
                    # The other reading was better: undo the change and
                    # restore the state the decision was in.
                    nested.discard(decision)
                    sibling.discard(decision)
                    if was_nested:
                        nested.add(decision)
                    elif was_sibling:
                        sibling.add(decision)

            # -- statements that leave the body of a definition -------
            # A constant that follows the body of a class may well belong
            # to the module and not to the class.  Every statement that
            # is written directly in the body of a definition is
            # therefore tried once as a statement that leaves that body.
            candidates = _escape_candidates(lines, indenter, best_levels)
            for index in candidates:
                if index in escaped:
                    continue
                escaped.add(index)
                trial_levels = indenter.run(nested, clause_of, sibling, escaped)
                trial_text, trial_mapping = render_with_map(lines, trial_levels)
                score = None
                if check_syntax(trial_text) is None and _depth(trial_levels) <= depth_limit:
                    score = len(undefined_names(trial_text))
                if score is None:
                    escaped.discard(index)
                    continue
                if score < best_score:
                    best_score = score
                    best_levels = trial_levels
                    improved = True
                else:
                    escaped.discard(index)

            if not improved:
                break

    def greedy_pass() -> bool:
        """Repair the file by deciding one ambiguity after the other.

        The ambiguities are visited in the order in which they appear in
        the file.  For every one of them both possibilities are tried: the
        block header either opens a *nested* suite or starts a new suite
        *next to* the enclosing one; a clause keyword either continues the
        construct in front of it or one of the constructs further up.

        The possibility that moves the first syntax error further into the
        file wins.  When neither possibility does, the more likely one --
        the nested suite -- is kept.

        Working through the file from the front keeps the number of parses
        small: an ambiguity that comes after the first error cannot change
        it, so it is not tried at all.  The whole file is parsed after
        every step, which makes the procedure quadratic in the number of
        ambiguities -- but with a very small constant, because only the
        *first* error is looked at.
        """
        # The decisions that have been looked at already.  The cursor
        # makes sure that every decision is tried at most once per round.
        cursor = 0

        while True:
            levels, error, mapping = attempt_error()
            if error is None:
                return True
            baseline = failing_index(error, mapping)

            changed = False

            # -- a try that misses its except/finally -------------------
            # "expected 'except' or 'finally' block" points at the
            # statement behind the ``try``; the construct that misses its
            # clause is the ``try`` in front of it.
            if "except" in error.msg and "finally" in error.msg:
                try_index = None
                for j in range(baseline - 1, -1, -1):
                    if lines[j].is_code and lines[j].head == "try":
                        try_index = j
                        break
                if try_index is not None:
                    for clause in _clauses_after(lines, indenter, try_index):
                        previous = clause_of.get(clause)
                        clause_of[clause] = try_index
                        trial_levels = indenter.run(nested, clause_of, sibling, escaped)
                        text, trial_mapping = render_with_map(lines, trial_levels)
                        trial_error = check_syntax(text)
                        if trial_error is None:
                            return True
                        trial_index = error_position(trial_error, trial_mapping)
                        if trial_index > baseline:
                            changed = True
                            baseline = trial_index
                            break
                        if previous is None:
                            clause_of.pop(clause, None)
                        else:
                            clause_of[clause] = previous

            if changed:
                continue

            # -- the clause that could not be attached ------------------
            ll = lines[baseline]
            if ll.is_code and ll.head in CLAUSE_KEYWORDS:
                # The construct the clause belongs to is normally found by
                # the rules of the language: the innermost open construct
                # that accepts the clause.  That choice is tried first --
                # it is the one that reproduces the original indentation
                # in the overwhelming majority of the cases.
                candidates = _clause_candidates(lines, indenter, baseline)
                default = indenter.clause_targets.get(baseline)
                if default is not None and default in candidates:
                    candidates.remove(default)
                    candidates.insert(0, default)
                for candidate in candidates:
                    previous = clause_of.get(baseline)
                    clause_of[baseline] = candidate
                    trial_levels = indenter.run(nested, clause_of, sibling, escaped)
                    text, trial_mapping = render_with_map(lines, trial_levels)
                    trial_error = check_syntax(text)
                    if trial_error is None:
                        return True
                    trial_index = error_position(trial_error, trial_mapping)
                    if trial_index > baseline:
                        changed = True
                        baseline = trial_index
                        break
                    if previous is None:
                        clause_of.pop(baseline, None)
                    else:
                        clause_of[baseline] = previous

            if changed:
                continue

            # -- a block header on the wrong level ----------------------
            # The header either opens a nested suite or starts a new suite
            # next to the enclosing one.  The nested suite is the default;
            # it is only given up when the sibling suite brings the first
            # error further into the file.
            decisions = indenter.decisions
            while cursor < len(decisions):
                decision = decisions[cursor]
                cursor += 1
                if decision > baseline:
                    # The header comes after the error; it cannot help
                    # yet.  It is looked at again in the next round.
                    cursor -= 1
                    break
                if decision in nested:
                    # The nested suite is already chosen; try the sibling
                    # suite instead.
                    nested.discard(decision)
                    sibling.add(decision)
                    trial_levels = indenter.run(nested, clause_of, sibling, escaped)
                    text, trial_mapping = render_with_map(lines, trial_levels)
                    trial_error = check_syntax(text)
                    if trial_error is None:
                        return True
                    trial_index = error_position(trial_error, trial_mapping)
                    sibling.discard(decision)
                    if trial_index > baseline:
                        sibling.add(decision)
                        baseline = trial_index
                        changed = True
                        break
                    nested.add(decision)
                    continue

                # The sibling suite is in place; try the nested one.
                sibling.discard(decision)
                nested.add(decision)
                trial_levels = indenter.run(nested, clause_of, sibling, escaped)
                text, trial_mapping = render_with_map(lines, trial_levels)
                trial_error = check_syntax(text)
                if trial_error is None:
                    return True
                trial_index = error_position(trial_error, trial_mapping)
                nested.discard(decision)
                if trial_index > baseline:
                    # The nested suite moves the error forward: keep it.
                    nested.add(decision)
                    baseline = trial_index
                    changed = True
                    break
                sibling.add(decision)

            if changed:
                continue

            if cursor >= len(decisions):
                # Every decision has been looked at and none of them
                # brings the file closer to a valid program.
                return False

    levels, error, _mapping = attempt_error()
    if error is not None and not greedy_pass():
        # The greedy pass got stuck: fall back to the exhaustive search,
        # which explores the remaining possibilities.
        if not _exhaustive_search():
            raise ReformatError("could not find an indentation that produces valid Python")

    levels, error, _mapping = attempt_error()
    if error is not None:
        raise ReformatError("could not find an indentation that produces valid Python")

    # -- the scope pass --------------------------------------------------
    # The parser is happy with many readings of a flattened file, but
    # most of them leave names unbound.  Every decision that is still
    # open is therefore tried once more: the reading that binds the
    # most names wins.
    scope_pass()

    levels, error, _mapping = attempt_error()
    if error is not None:
        raise ReformatError("could not find an indentation that produces valid Python")
    return render(lines, levels)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reformat_python",
        description="Restore indentation to a flattened Python source file.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="the Python file whose indentation should be restored",
    )
    parser.add_argument("-o", "--output", type=Path, help="write the result to this file instead of stdout")
    parser.add_argument("-i", "--in-place", action="store_true", help="overwrite the input file with the result")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="skip the final ast.parse() check",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if args.source is None:
        parser.error("a source file is required")

    text = args.source.read_text(encoding="utf-8")
    try:
        result = reformat(text)
    except ReformatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.in_place:
        args.source.write_text(result, encoding="utf-8")
    elif args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
