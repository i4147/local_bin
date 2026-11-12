#!/data/data/com.termux/files/usr/bin/env python

import argparse
import hashlib
import math
import random
import re
import sys
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

__all__ = [
    "DEFAULT_METRICS",
    "Cosine",
    "DamerauLevenshtein",
    "Dice",
    "DiffOp",
    "EditDistanceMetric",
    "FileComparator",
    "FileComparison",
    "FuzzyComparator",
    "Hamming",
    "Jaccard",
    "Jaro",
    "JaroWinkler",
    "LCSSimilarity",
    "Levenshtein",
    "MetaphoneMetric",
    "MinHash",
    "NGram",
    "NYSIISMetric",
    "PartialRatio",
    "SimHash",
    "SimilarityMetric",
    "SoundexMetric",
    "TokenSet",
    "TokenSort",
    "align_lines",
    "build_parser",
    "default_tokenize",
    "main",
    "metaphone",
    "normalize_text",
    "nysiis",
    "soundex",
]
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def default_tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def normalize_text(
    text: str,
    *,
    lowercase: bool = True,
    strip_punct: bool = True,
    collapse_ws: bool = True,
    strip: bool = True,
) -> str:
    if lowercase:
        text = text.lower()
    if strip_punct:
        text = _PUNCT_RE.sub(" ", text)
    if collapse_ws:
        text = _WS_RE.sub(" ", text)
    if strip:
        text = text.strip()
    return text


def ngrams(text: str, n: int) -> list[str]:
    if n <= 0:
        raise ValueError("n must be >= 1")
    if len(text) < n:
        return [text] if text else []
    return [text[i : i + n] for i in range(len(text) - n + 1)]


class SimilarityMetric(ABC):
    name: str = "metric"

    @abstractmethod
    def similarity(self, a: str, b: str) -> float: ...
    def distance(self, a: str, b: str) -> float:
        return 1.0 - self.similarity(a, b)

    def __call__(self, a: str, b: str) -> float:
        return self.similarity(a, b)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class EditDistanceMetric(SimilarityMetric):
    @abstractmethod
    def edit_distance(self, a: str, b: str) -> int: ...
    def similarity(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        denom = max(len(a), len(b))
        if denom == 0:
            return 1.0
        return 1.0 - self.edit_distance(a, b) / denom


class Levenshtein(EditDistanceMetric):
    name = "levenshtein"

    def edit_distance(self, a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        if len(a) > len(b):
            a, b = b, a
        prev = list(range(len(a) + 1))
        for i, cb in enumerate(b, 1):
            curr = [i]
            for j, ca in enumerate(a, 1):
                cost = 0 if ca == cb else 1
                curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
            prev = curr
        return prev[-1]


class DamerauLevenshtein(EditDistanceMetric):
    name = "damerau"

    def edit_distance(self, a: str, b: str) -> int:
        la, lb = len(a), len(b)
        if a == b:
            return 0
        if not la:
            return lb
        if not lb:
            return la
        d = [[0] * (lb + 1) for _ in range(la + 1)]
        for i in range(la + 1):
            d[i][0] = i
        for j in range(lb + 1):
            d[0][j] = j
        for i in range(1, la + 1):
            for j in range(1, lb + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
                if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                    d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
        return d[la][lb]


class Hamming(EditDistanceMetric):
    name = "hamming"

    def __init__(self, strict: bool = False) -> None:
        self.strict = strict

    def edit_distance(self, a: str, b: str) -> int:
        if self.strict and len(a) != len(b):
            raise ValueError("Hamming distance requires equal-length strings")
        common = min(len(a), len(b))
        mismatches = sum(1 for i in range(common) if a[i] != b[i])
        return mismatches + abs(len(a) - len(b))


class Jaro(SimilarityMetric):
    name = "jaro"

    def similarity(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        la, lb = len(a), len(b)
        if la == 0 or lb == 0:
            return 0.0
        window = max(la, lb) // 2 - 1
        if window < 0:
            window = 0
        a_flags = [False] * la
        b_flags = [False] * lb
        matches = 0
        for i, ca in enumerate(a):
            lo = max(0, i - window)
            hi = min(i + window + 1, lb)
            for j in range(lo, hi):
                if b_flags[j] or a[i] != b[j]:
                    continue
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
        if matches == 0:
            return 0.0
        k = transpositions = 0
        for i in range(la):
            if not a_flags[i]:
                continue
            while not b_flags[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
        transpositions //= 2
        return (matches / la + matches / lb + (matches - transpositions) / matches) / 3.0


class JaroWinkler(Jaro):
    name = "jaro_winkler"

    def __init__(self, prefix_scale: float = 0.1, max_prefix: int = 4) -> None:
        self.prefix_scale = prefix_scale
        self.max_prefix = max_prefix

    def similarity(self, a: str, b: str) -> float:
        j = super().similarity(a, b)
        if j <= 0.7:
            return j
        prefix = 0
        for ca, cb in zip(a, b):
            if ca != cb or prefix >= self.max_prefix:
                break
            prefix += 1
        return j + prefix * self.prefix_scale * (1.0 - j)

    def __repr__(self) -> str:
        return f"JaroWinkler(prefix_scale={self.prefix_scale}, max_prefix={self.max_prefix})"


class Jaccard(SimilarityMetric):
    name = "jaccard"

    def __init__(self, tokenizer: Callable[[str], list[str]] = default_tokenize) -> None:
        self.tokenizer = tokenizer

    def similarity(self, a: str, b: str) -> float:
        ta, tb = set(self.tokenizer(a)), set(self.tokenizer(b))
        if not ta and not tb:
            return 1.0
        union = ta | tb
        return len(ta & tb) / len(union) if union else 0.0


class Dice(SimilarityMetric):
    name = "dice"

    def __init__(self, tokenizer: Callable[[str], list[str]] = default_tokenize) -> None:
        self.tokenizer = tokenizer

    def similarity(self, a: str, b: str) -> float:
        ta, tb = set(self.tokenizer(a)), set(self.tokenizer(b))
        if not ta and not tb:
            return 1.0
        if not ta or not tb:
            return 0.0
        return 2.0 * len(ta & tb) / (len(ta) + len(tb))


class Cosine(SimilarityMetric):
    name = "cosine"

    def __init__(
        self,
        tokenizer: Callable[[str], list[str]] = default_tokenize,
        idf: dict[str, float] | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.idf = idf or {}

    def _vector(self, text: str) -> dict[str, float]:
        tf = Counter(self.tokenizer(text))
        if not self.idf:
            return dict(tf)
        return {t: c * self.idf.get(t, 1.0) for t, c in tf.items()}

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._vector(a), self._vector(b)
        if not va and not vb:
            return 1.0
        if not va or not vb:
            return 0.0
        common = set(va) & set(vb)
        dot = sum(va[t] * vb[t] for t in common)
        na = math.sqrt(sum(v * v for v in va.values()))
        nb = math.sqrt(sum(v * v for v in vb.values()))
        return dot / (na * nb) if na and nb else 0.0


class LCSSimilarity(SimilarityMetric):
    name = "lcs"

    def lcs_length(self, a: str, b: str) -> int:
        if not a or not b:
            return 0
        if len(a) < len(b):
            a, b = b, a
        prev = [0] * (len(b) + 1)
        for ca in a:
            curr = [0]
            for j, cb in enumerate(b, 1):
                if ca == cb:
                    curr.append(prev[j - 1] + 1)
                else:
                    curr.append(max(prev[j], curr[j - 1]))
            prev = curr
        return prev[-1]

    def similarity(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        denom = max(len(a), len(b))
        if denom == 0:
            return 1.0
        return self.lcs_length(a, b) / denom


class NGram(SimilarityMetric):
    name = "ngram"

    def __init__(self, n: int = 2) -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        self.n = n

    def similarity(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        ga = Counter(ngrams(a, self.n))
        gb = Counter(ngrams(b, self.n))
        if not ga and not gb:
            return 1.0
        if not ga or not gb:
            return 0.0
        overlap = sum((ga & gb).values())
        total = sum(ga.values()) + sum(gb.values())
        return 2.0 * overlap / total

    def __repr__(self) -> str:
        return f"NGram(n={self.n})"


_SOUNDEX_CODES = {
    "B": "1",
    "F": "1",
    "P": "1",
    "V": "1",
    "C": "2",
    "G": "2",
    "J": "2",
    "K": "2",
    "Q": "2",
    "S": "2",
    "X": "2",
    "Z": "2",
    "D": "3",
    "T": "3",
    "L": "4",
    "M": "5",
    "N": "5",
    "R": "6",
}


def soundex(name: str) -> str:
    letters = [c for c in name.upper() if c.isalpha()]
    if not letters:
        return ""
    first = letters[0]
    prev = _SOUNDEX_CODES.get(first, "")
    out = [first]
    for ch in letters[1:]:
        if ch in ("H", "W"):
            continue
        code = _SOUNDEX_CODES.get(ch, "")
        if code:
            if code != prev:
                out.append(code)
            prev = code
        else:
            prev = ""
    return ("".join(out) + "000")[:4]


def nysiis(name: str) -> str:
    name = re.sub(r"[^A-Z]", "", name.upper())
    if not name:
        return ""
    for pre, rep in (("MAC", "MCC"), ("KN", "NN"), ("K", "C"), ("PH", "FF"), ("PF", "FF"), ("SCH", "SSS")):
        if name.startswith(pre):
            name = rep + name[len(pre) :]
            break
    for suf, rep in (("EE", "Y"), ("IE", "Y"), ("DT", "D"), ("RT", "D"), ("RD", "D"), ("NT", "D"), ("ND", "D")):
        if name.endswith(suf):
            name = name[: -len(suf)] + rep
            break
    if name.startswith("EV"):
        name = "AF" + name[2:]
    first = "A" if name[0] in "AEIOU" else name[0]
    key = [first]
    prev = first
    i = 1
    while i < len(name):
        ch = name[i]
        if name[i : i + 2] == "EV":
            ch = "AF"
            i += 2
        elif name[i : i + 2] == "SCH":
            ch = "SSS"
            i += 2
        elif name[i : i + 2] == "KN":
            ch = "NN"
            i += 2
        else:
            i += 1
        if ch in "AEIOU":
            ch = "A"
        elif ch == "Q":
            ch = "G"
        elif ch == "Z":
            ch = "S"
        elif ch == "M":
            ch = "N"
        elif ch == "K":
            ch = "C"
        if ch != prev:
            key.append(ch)
            prev = ch
    result = "".join(key)
    if result.endswith("AY"):
        result = result[:-2] + "Y"
    if result.endswith("A"):
        result = result[:-1]
    if len(result) >= 2 and result[-1] == result[-2]:
        result = result[:-1]
    return result


_METAPHONE_VOWELS = set("AEIOU")


def metaphone(word: str, max_length: int = 4) -> str:
    w = re.sub(r"[^A-Z]", "", word.upper())
    if not w:
        return ""
    if w[:2] in ("AE", "GN", "KN", "PN", "WR"):
        w = w[1:]
    elif w[0] == "X":
        w = "S" + w[1:]
    elif w[:2] == "WH":
        w = "W" + w[2:]
    out: list[str] = []
    i, n = 0, len(w)
    while i < n and len(out) < max_length:
        c = w[i]
        nxt = w[i + 1] if i + 1 < n else ""
        prv = out[-1] if out else ""
        if c in _METAPHONE_VOWELS:
            if i == 0:
                out.append("A")
        elif c == "B":
            if not (i == n - 1 and prv == "M"):
                out.append("B")
        elif c == "C":
            if nxt == "H":
                out.append("X")
                i += 1
            elif nxt in ("E", "I", "Y"):
                out.append("S")
            else:
                out.append("K")
        elif c == "D":
            if nxt == "G" and i + 2 < n and w[i + 2] in "EIY":
                out.append("J")
                i += 2
            else:
                out.append("T")
        elif c == "F":
            out.append("F")
        elif c == "G":
            if nxt == "H":
                if not (i > 0 and w[i - 1] in _METAPHONE_VOWELS):
                    out.append("K")
                i += 1
            elif nxt in ("E", "I", "Y"):
                out.append("J")
            else:
                out.append("K")
        elif c == "H":
            if i > 0 and w[i - 1] in _METAPHONE_VOWELS and nxt not in _METAPHONE_VOWELS:
                out.append("H")
        elif c == "J":
            out.append("J")
        elif c == "K":
            if i == 0 or w[i - 1] != "C":
                out.append("K")
        elif c == "L":
            out.append("L")
        elif c == "M":
            out.append("M")
        elif c == "N":
            out.append("N")
        elif c == "P":
            if nxt == "H":
                out.append("F")
                i += 1
            else:
                out.append("P")
        elif c == "Q":
            out.append("K")
        elif c == "R":
            out.append("R")
        elif c == "S":
            if nxt == "H":
                out.append("X")
                i += 1
            elif nxt == "C" and i + 2 < n and w[i + 2] in "EIY":
                out.append("S")
                i += 1
            else:
                out.append("S")
        elif c == "T":
            if nxt == "H":
                out.append("0")
                i += 1
            elif nxt == "I" and i + 2 < n and w[i + 2] in "AO":
                out.append("X")
            else:
                out.append("T")
        elif c == "V":
            out.append("F")
        elif c == "W":
            if nxt in _METAPHONE_VOWELS:
                out.append("W")
        elif c == "X":
            out.append("KS")
        elif c == "Y":
            if nxt in _METAPHONE_VOWELS:
                out.append("Y")
        elif c == "Z":
            out.append("S")
        if len(out) >= 2 and out[-1] == out[-2]:
            out.pop()
        i += 1
    return "".join(out)


class _PhoneticMetric(SimilarityMetric):
    encoder: Callable[[str], str] = staticmethod(lambda s: s)

    def __init__(self, code_metric: SimilarityMetric | None = None) -> None:
        self.code_metric = code_metric or JaroWinkler()

    def code(self, text: str) -> str:
        return self.encoder(text)

    def similarity(self, a: str, b: str) -> float:
        ca, cb = self.code(a), self.code(b)
        if not ca and not cb:
            return 1.0
        if not ca or not cb:
            return 0.0
        return self.code_metric.similarity(ca, cb)


class SoundexMetric(_PhoneticMetric):
    name = "soundex"
    encoder = staticmethod(soundex)


class NYSIISMetric(_PhoneticMetric):
    name = "nysiis"
    encoder = staticmethod(nysiis)


class MetaphoneMetric(_PhoneticMetric):
    name = "metaphone"
    encoder = staticmethod(metaphone)


_MERSENNE_61 = (1 << 61) - 1


def _stable_hash(data: str, seed: int = 0) -> int:
    h = hashlib.blake2b(data.encode("utf-8"), digest_size=8, key=seed.to_bytes(8, "big")).digest()
    return int.from_bytes(h, "big")


class MinHash(SimilarityMetric):
    name = "minhash"

    def __init__(
        self,
        num_perm: int = 128,
        shingle_size: int = 3,
        seed: int = 42,
        tokenizer: Callable[[str], list[str]] | None = None,
    ) -> None:
        if num_perm < 1:
            raise ValueError("num_perm must be >= 1")
        self.num_perm = num_perm
        self.shingle_size = shingle_size
        self.seed = seed
        self.tokenizer = tokenizer
        rng = random.Random(seed)
        self._a = [rng.randrange(1, _MERSENNE_61) for _ in range(num_perm)]
        self._b = [rng.randrange(0, _MERSENNE_61) for _ in range(num_perm)]

    def _shingles(self, text: str) -> set[str]:
        if self.tokenizer is not None:
            tokens = self.tokenizer(text)
        else:
            tokens = list(text)
        n = self.shingle_size
        if len(tokens) < n:
            return {"".join(tokens)} if tokens else set()
        return {"".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}

    def signature(self, text: str) -> list[int]:
        shingles = self._shingles(text)
        if not shingles:
            return [0] * self.num_perm
        base = [_stable_hash(s, self.seed) for s in shingles]
        sig = []
        for a, b in zip(self._a, self._b):
            sig.append(min((a * h + b) % _MERSENNE_61 for h in base))
        return sig

    def similarity(self, a: str, b: str) -> float:
        sa, sb = self.signature(a), self.signature(b)
        equal = sum(1 for x, y in zip(sa, sb) if x == y)
        return equal / self.num_perm

    def __repr__(self) -> str:
        return f"MinHash(num_perm={self.num_perm}, shingle_size={self.shingle_size})"


class SimHash(SimilarityMetric):
    name = "simhash"

    def __init__(
        self,
        bits: int = 64,
        tokenizer: Callable[[str], list[str]] = default_tokenize,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.bits = bits
        self.tokenizer = tokenizer
        self.weights = weights or {}

    def fingerprint(self, text: str) -> int:
        tokens = self.tokenizer(text)
        if not tokens:
            return 0
        counts = Counter(tokens)
        vector = [0.0] * self.bits
        for token, count in counts.items():
            w = count * self.weights.get(token, 1.0)
            h = _stable_hash(token)
            for i in range(self.bits):
                vector[i] += w if (h >> i) & 1 else -w
        fp = 0
        for i in range(self.bits):
            if vector[i] > 0:
                fp |= 1 << i
        return fp

    def similarity(self, a: str, b: str) -> float:
        fa, fb = self.fingerprint(a), self.fingerprint(b)
        if fa == 0 and fb == 0:
            return 1.0
        return 1.0 - bin(fa ^ fb).count("1") / self.bits

    def __repr__(self) -> str:
        return f"SimHash(bits={self.bits})"


class TokenSort(SimilarityMetric):
    name = "token_sort"

    def __init__(
        self,
        base: SimilarityMetric | None = None,
        tokenizer: Callable[[str], list[str]] = default_tokenize,
    ) -> None:
        self.base = base or Levenshtein()
        self.tokenizer = tokenizer

    def similarity(self, a: str, b: str) -> float:
        ta = " ".join(sorted(self.tokenizer(a)))
        tb = " ".join(sorted(self.tokenizer(b)))
        if not ta and not tb:
            return 1.0
        return self.base.similarity(ta, tb)


class TokenSet(SimilarityMetric):
    name = "token_set"

    def __init__(
        self,
        base: SimilarityMetric | None = None,
        tokenizer: Callable[[str], list[str]] = default_tokenize,
    ) -> None:
        self.base = base or Levenshtein()
        self.tokenizer = tokenizer

    def similarity(self, a: str, b: str) -> float:
        ta, tb = set(self.tokenizer(a)), set(self.tokenizer(b))
        if not ta and not tb:
            return 1.0
        if not ta or not tb:
            return 0.0
        inter = ta & tb
        s_inter = " ".join(sorted(inter))
        s_a = " ".join(sorted(inter | (ta - tb)))
        s_b = " ".join(sorted(inter | (tb - ta)))
        return max(
            self.base.similarity(s_inter, s_a),
            self.base.similarity(s_inter, s_b),
            self.base.similarity(s_a, s_b),
        )


class PartialRatio(SimilarityMetric):
    name = "partial"

    def __init__(self, base: SimilarityMetric | None = None) -> None:
        self.base = base or Levenshtein()

    def similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        best = 0.0
        for i in range(len(b) - len(a) + 1):
            s = self.base.similarity(a, b[i : i + len(a)])
            if s > best:
                best = s
                if best == 1.0:
                    break
        return best


DEFAULT_METRICS: dict[str, Callable[..., SimilarityMetric]] = {
    "levenshtein": Levenshtein,
    "damerau": DamerauLevenshtein,
    "hamming": Hamming,
    "jaro": Jaro,
    "jaro_winkler": JaroWinkler,
    "jaccard": Jaccard,
    "dice": Dice,
    "cosine": Cosine,
    "lcs": LCSSimilarity,
    "ngram": NGram,
    "soundex": SoundexMetric,
    "nysiis": NYSIISMetric,
    "metaphone": MetaphoneMetric,
    "minhash": MinHash,
    "simhash": SimHash,
    "token_sort": TokenSort,
    "token_set": TokenSet,
    "partial": PartialRatio,
}


class FuzzyComparator:
    def __init__(
        self,
        metric: str | SimilarityMetric = "jaro_winkler",
        *,
        normalize: bool = False,
        **metric_kwargs: object,
    ) -> None:
        self.normalize = normalize
        self.metric = self._resolve(metric, **metric_kwargs)

    @staticmethod
    def _resolve(metric: str | SimilarityMetric, **kwargs: object) -> SimilarityMetric:
        if isinstance(metric, SimilarityMetric):
            return metric
        try:
            factory = DEFAULT_METRICS[metric]
        except KeyError as exc:
            raise KeyError(f"Unknown metric {metric!r}. Available: {sorted(DEFAULT_METRICS)}") from exc
        return factory(**kwargs)

    def _prep(self, text: str) -> str:
        return normalize_text(text) if self.normalize else text

    def similarity(self, a: str, b: str) -> float:
        return self.metric.similarity(self._prep(a), self._prep(b))

    def distance(self, a: str, b: str) -> float:
        return self.metric.distance(self._prep(a), self._prep(b))

    def compare_all(
        self,
        a: str,
        b: str,
        metrics: Sequence[str] | None = None,
        **overrides: dict[str, object],
    ) -> dict[str, float]:
        names = list(metrics) if metrics else list(DEFAULT_METRICS)
        results: dict[str, float] = {}
        for name in names:
            m = self._resolve(name, **overrides.get(name, {}))
            results[name] = m.similarity(self._prep(a), self._prep(b))
        return results

    def best_match(
        self,
        query: str,
        candidates: Iterable[str],
    ) -> tuple[str | None, float]:
        best, best_score = None, -1.0
        for cand in candidates:
            s = self.similarity(query, cand)
            if s > best_score:
                best, best_score = cand, s
        return best, max(best_score, 0.0)

    def matches_above(
        self,
        query: str,
        candidates: Iterable[str],
        threshold: float = 0.8,
    ) -> list[tuple[str, float]]:
        scored = [(c, self.similarity(query, c)) for c in candidates]
        return sorted(
            ((c, s) for c, s in scored if s >= threshold),
            key=lambda t: t[1],
            reverse=True,
        )

    def rank(
        self,
        query: str,
        candidates: Iterable[str],
    ) -> list[tuple[str, float]]:
        scored = [(c, self.similarity(query, c)) for c in candidates]
        return sorted(scored, key=lambda t: t[1], reverse=True)

    def __call__(self, a: str, b: str) -> float:
        return self.similarity(a, b)

    def __repr__(self) -> str:
        return f"FuzzyComparator(metric={self.metric!r}, normalize={self.normalize})"


@dataclass
class DiffOp:
    tag: str
    line_a: str | None
    line_b: str | None
    index_a: int
    index_b: int
    score: float = 0.0

    def __str__(self) -> str:
        marker = {"equal": "  ", "replace": "~ ", "delete": "- ", "insert": "+ "}[self.tag]
        if self.tag == "equal":
            return f"{marker}{self.line_a}"
        if self.tag == "delete":
            return f"{marker}{self.line_a}"
        if self.tag == "insert":
            return f"{marker}{self.line_b}"
        return f"{marker}{self.line_a}  ==>  {self.line_b}  ({self.score:.2f})"


@dataclass
class FileComparison:
    path_a: str
    path_b: str
    metric: str
    similarity: float
    line_similarity: float
    lines_a: int
    lines_b: int
    equal: int = 0
    replaced: int = 0
    deleted: int = 0
    inserted: int = 0
    ops: list[DiffOp] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return self.equal == self.lines_a == self.lines_b and not (self.replaced or self.deleted or self.inserted)

    def report(self, show_diff: bool = False) -> str:
        pct = f"{self.similarity * 100:.1f}%"
        lines = [
            f"Comparing:  {self.path_a}",
            f"        vs: {self.path_b}",
            f"Metric:     {self.metric}",
            f"Similarity: {pct}   (line-avg {self.line_similarity * 100:.1f}%)",
            f"Lines:      {self.lines_a} vs {self.lines_b}",
            f"  equal     {self.equal}",
            f"  replaced  {self.replaced}",
            f"  deleted   {self.deleted}",
            f"  inserted  {self.inserted}",
        ]
        if show_diff:
            lines.append("")
            lines.extend(str(op) for op in self.ops)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.report()


def align_lines(
    lines_a: Sequence[str],
    lines_b: Sequence[str],
    metric: SimilarityMetric,
    threshold: float = 0.6,
    window: int = 200,
) -> list[DiffOp]:
    n, m = len(lines_a), len(lines_b)
    ops: list[DiffOp] = []
    i = j = 0

    def _score(x: str, y: str) -> float:
        return metric.similarity(x, y) if x != y else 1.0

    while i < n and j < m:
        score = _score(lines_a[i], lines_b[j])
        if score >= threshold:
            tag = "equal" if lines_a[i] == lines_b[j] else "replace"
            ops.append(DiffOp(tag, lines_a[i], lines_b[j], i, j, score))
            i += 1
            j += 1
            continue
        best: tuple[str, int] | None = None
        for di in range(1, min(window, n - i)):
            if _score(lines_a[i + di], lines_b[j]) >= threshold:
                best = ("delete", di)
                break
        for dj in range(1, min(window, m - j)):
            if _score(lines_a[i], lines_b[j + dj]) >= threshold:
                if best is None or dj < best[1]:
                    best = ("insert", dj)
                break
        if best is None:
            ops.append(DiffOp("replace", lines_a[i], lines_b[j], i, j, score))
            i += 1
            j += 1
        elif best[0] == "delete":
            for k in range(best[1]):
                ops.append(DiffOp("delete", lines_a[i + k], None, i + k, -1, 0.0))
            i += best[1]
        else:
            for k in range(best[1]):
                ops.append(DiffOp("insert", None, lines_b[j + k], -1, j + k, 0.0))
            j += best[1]
    while i < n:
        ops.append(DiffOp("delete", lines_a[i], None, i, -1, 0.0))
        i += 1
    while j < m:
        ops.append(DiffOp("insert", None, lines_b[j], -1, j, 0.0))
        j += 1
    return ops


class _NormalizedWrapper(SimilarityMetric):
    def __init__(self, base: SimilarityMetric) -> None:
        self.base = base
        self.name = base.name

    def similarity(self, a: str, b: str) -> float:
        return self.base.similarity(normalize_text(a), normalize_text(b))


class FileComparator:
    def __init__(
        self,
        metric: str | SimilarityMetric = "jaro_winkler",
        *,
        normalize: bool = True,
        normalize_lines: bool = False,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> None:
        self.comparator = FuzzyComparator(metric, normalize=normalize)
        self.metric = self.comparator.metric
        self.normalize = normalize
        self.normalize_lines = normalize_lines
        self.encoding = encoding
        self.errors = errors

    def _read(self, path: str | Path) -> str:
        return Path(path).read_text(encoding=self.encoding, errors=self.errors)

    def compare(
        self,
        path_a: str | Path,
        path_b: str | Path,
        *,
        threshold: float = 0.6,
        window: int = 200,
    ) -> FileComparison:
        path_a, path_b = Path(path_a), Path(path_b)
        text_a = self._read(path_a)
        text_b = self._read(path_b)
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()
        whole = self.comparator.similarity(text_a, text_b)
        align_metric: SimilarityMetric = _NormalizedWrapper(self.metric) if self.normalize_lines else self.metric
        ops = align_lines(lines_a, lines_b, align_metric, threshold, window)
        equal = sum(1 for op in ops if op.tag == "equal")
        replaced = sum(1 for op in ops if op.tag == "replace")
        deleted = sum(1 for op in ops if op.tag == "delete")
        inserted = sum(1 for op in ops if op.tag == "insert")
        scored = [op.score for op in ops if op.tag in ("equal", "replace")]
        line_avg = sum(scored) / len(scored) if scored else 1.0
        return FileComparison(
            path_a=str(path_a),
            path_b=str(path_b),
            metric=self.metric.name,
            similarity=whole,
            line_similarity=line_avg,
            lines_a=len(lines_a),
            lines_b=len(lines_b),
            equal=equal,
            replaced=replaced,
            deleted=deleted,
            inserted=inserted,
            ops=ops,
        )

    def diff_lines(
        self,
        path_a: str | Path,
        path_b: str | Path,
        *,
        threshold: float = 0.6,
        window: int = 200,
    ) -> list[DiffOp]:
        return self.compare(path_a, path_b, threshold=threshold, window=window).ops

    def fuzzy_grep(
        self,
        path: str | Path,
        query: str,
        *,
        threshold: float = 0.7,
    ) -> list[tuple[int, str, float]]:
        hits: list[tuple[int, str, float]] = []
        for i, line in enumerate(self._read(path).splitlines(), 1):
            s = self.comparator.similarity(query, line)
            if s >= threshold:
                hits.append((i, line, s))
        return sorted(hits, key=lambda t: t[2], reverse=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fuzzycmp", description="Fuzzy text comparison CLI")
    parser.add_argument("--version", action="version", version="fuzzycmp 1.0.0")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sim = sub.add_parser("similarity", help="Compare two strings with one metric")
    p_sim.add_argument("--metric", required=True, choices=sorted(DEFAULT_METRICS))
    p_sim.add_argument("--a", required=True)
    p_sim.add_argument("--b", required=True)
    p_sim.add_argument("--normalize", action="store_true")
    p_all = sub.add_parser("compare-all", help="Score two strings with every metric")
    p_all.add_argument("--a", required=True)
    p_all.add_argument("--b", required=True)
    p_all.add_argument("--normalize", action="store_true")
    p_best = sub.add_parser("best-match", help="Find the best candidate for a query")
    p_best.add_argument("--metric", required=True, choices=sorted(DEFAULT_METRICS))
    p_best.add_argument("--query", required=True)
    p_best.add_argument("--candidate", action="append", required=True, dest="candidates")
    p_best.add_argument("--threshold", type=float, default=0.0)
    p_best.add_argument("--normalize", action="store_true")
    p_best.add_argument("--rank", action="store_true")
    p_files = sub.add_parser("files", help="Compare two files")
    p_files.add_argument("--metric", required=True, choices=sorted(DEFAULT_METRICS))
    p_files.add_argument("--a", required=True)
    p_files.add_argument("--b", required=True)
    p_files.add_argument("--threshold", type=float, default=0.6)
    p_files.add_argument("--window", type=int, default=200)
    p_files.add_argument("--encoding", default="utf-8")
    p_files.add_argument("--diff", action="store_true")
    p_files.add_argument("--normalize", action="store_true")
    p_files.add_argument("--normalize-lines", action="store_true")
    p_files.add_argument("--json", action="store_true")
    p_grep = sub.add_parser("grep", help="Fuzzy-grep a file for a query")
    p_grep.add_argument("--metric", required=True, choices=sorted(DEFAULT_METRICS))
    p_grep.add_argument("--file", required=True)
    p_grep.add_argument("--query", required=True)
    p_grep.add_argument("--threshold", type=float, default=0.7)
    p_grep.add_argument("--encoding", default="utf-8")
    p_grep.add_argument("--normalize", action="store_true")
    p_phon = sub.add_parser("phonetic", help="Show phonetic codes for a word")
    p_phon.add_argument("--word", required=True)
    return parser


def _cmd_similarity(args: argparse.Namespace) -> int:
    fc = FuzzyComparator(args.metric, normalize=args.normalize)
    print(f"{fc.similarity(args.a, args.b):.6f}")
    return 0


def _cmd_compare_all(args: argparse.Namespace) -> int:
    fc = FuzzyComparator("levenshtein", normalize=args.normalize)
    results = fc.compare_all(args.a, args.b)
    width = max(len(k) for k in results)
    for name in sorted(results, key=lambda k: results[k], reverse=True):
        print(f"{name:<{width}}  {results[name]:.6f}")
    return 0


def _cmd_best_match(args: argparse.Namespace) -> int:
    fc = FuzzyComparator(args.metric, normalize=args.normalize)
    if args.rank:
        for cand, score in fc.matches_above(args.query, args.candidates, args.threshold):
            print(f"{score:.6f}\t{cand}")
        return 0
    best, score = fc.best_match(args.query, args.candidates)
    if score < args.threshold:
        print(f"no match above {args.threshold:.3f}", file=sys.stderr)
        return 1
    print(f"{score:.6f}\t{best}")
    return 0


def _cmd_files(args: argparse.Namespace) -> int:
    fc = FileComparator(
        args.metric,
        normalize=args.normalize,
        normalize_lines=args.normalize_lines,
        encoding=args.encoding,
    )
    result = fc.compare(args.a, args.b, threshold=args.threshold, window=args.window)
    if args.json:
        import json

        payload = {
            "path_a": result.path_a,
            "path_b": result.path_b,
            "metric": result.metric,
            "similarity": result.similarity,
            "line_similarity": result.line_similarity,
            "lines_a": result.lines_a,
            "lines_b": result.lines_b,
            "equal": result.equal,
            "replaced": result.replaced,
            "deleted": result.deleted,
            "inserted": result.inserted,
            "identical": result.identical,
            "ops": [
                {
                    "tag": op.tag,
                    "line_a": op.line_a,
                    "line_b": op.line_b,
                    "index_a": op.index_a,
                    "index_b": op.index_b,
                    "score": op.score,
                }
                for op in result.ops
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(result.report(show_diff=args.diff))
    return 0 if not result.identical else 0


def _cmd_grep(args: argparse.Namespace) -> int:
    fc = FileComparator(args.metric, normalize=args.normalize, encoding=args.encoding)
    hits = fc.fuzzy_grep(args.file, args.query, threshold=args.threshold)
    if not hits:
        return 1
    for line_no, line, score in hits:
        print(f"{line_no}\t{score:.4f}\t{line}")
    return 0


def _cmd_phonetic(args: argparse.Namespace) -> int:
    word = args.word
    print(f"soundex   {soundex(word)}")
    print(f"nysiis    {nysiis(word)}")
    print(f"metaphone {metaphone(word)}")
    return 0


_DISPATCH: dict[str, Callable[[argparse.Namespace], int]] = {
    "similarity": _cmd_similarity,
    "compare-all": _cmd_compare_all,
    "best-match": _cmd_best_match,
    "files": _cmd_files,
    "grep": _cmd_grep,
    "phonetic": _cmd_phonetic,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
