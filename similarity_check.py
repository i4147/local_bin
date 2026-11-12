#!/data/data/com.termux/files/usr/bin/env python

import argparse
import ast
import collections
import datetime as dt
import difflib
import hashlib
import html
import io
import json
import keyword
import math
import pathlib
import re
import subprocess
import sys
import tokenize
import zlib
from typing import Any, Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be
    because been before being below between both but by can cannot could did do
    does doing down during each few for from further had has have having he her
    here hers herself him himself his how i if in into is it its itself just me
    more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their
    theirs them themselves then there these they this those through to too under
    until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves
    args argv arg argument arguments call called code command commands data
    default defaults file filename files function functions get help import
    input line lines list main module name names none null option options output
    path paths print program return returns run script scripts self set str
    string strings sys true false type types use used user value values var
    version versions via working work your cli tool tools utility utils
    com termux usr local home data files env python3 python env bin sbin opt tmp
    etc dev var coding utf indent expandtab tabstop shiftwidth fileencoding
    http https www html wwwwww newline endcode operator todo note fixme
    """.split()
)

GENERIC_STEM_TOKENS = frozenset(
    """to from into and the a an of for with by on in at is are new old v1 v2 v3
    py python simple fast auto mini test tests tools tool utils util utility
    cli x xx extra super better best small big my run runner file files dir dirs
    the2 s es er ing ed 2 3 4 5 10 one two three
    """.split()
)

STEM_SEED = frozenset(
    """to md js ts rs c cpp cc cc py ipynb txt csv json xml yaml yml toml ini cfg
    sql sqlite db pdf png jpg jpeg gif webp svg img image images html htm css
    lua rst srt vcf pkl whl tar zip xz gz bz2 rar 7z ttf otf woff woff2
    url uri http https www web api cli gui ai ml nlp ocr tts wav mp3 mp4 mkv
    avi mov vid video audio font fonts doc docs docx xls xlsx ppt pptx
    git gh repo repos pip pypi pkg pkgs lib libs env venv req reqs txt
    github gitlab gist ghpy dh log logs hash dup dedup dups dir size sizes
    b64 base64 hex bin oct elf elf64 qr qrc em emoji utf ascii unicode
    trans transwords translate translator langdict dic dict word words list
    clean cleaner cleanup fmt format formatter pretty min minify strip stripper
    rmc rmcts rmca rmcst eximports imports importz importtools
    csv2json pkl2json sqltojson sqlite2json xml_yaml2json vcf2json
    """.split()
)

FEATURE_WEIGHTS = {
    "lex": 1.0,
    "anon": 0.7,
    "ast": 0.9,
    "call": 2.0,
    "imp": 2.2,
    "opt": 3.0,
    "word": 1.1,
    "stem": 3.2,
    "def": 1.2,
}

API_PREFIXES = ("call", "imp", "opt")
LEX_N = 5
AST_N = 6
SHINGLE_SAMPLE = 3


def _hash_shingle(s: str) -> int:
    return zlib.crc32(s.encode("utf-8", "replace")) & 0xFFFFFFFF


def discover_files(root: pathlib.Path) -> list[pathlib.Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.splitlines()
        files = [root / p for p in out if "/" not in p and p.endswith(".py")]
    except (OSError, subprocess.SubprocessError):
        files = []
    if not files:
        files = [p for p in root.glob("*.py") if p.is_file()]
    return sorted(p for p in files if p.is_file())


def read_source(path: pathlib.Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"


def build_stem_lexicon(names: Iterable[str]) -> set[str]:
    counts: collections.Counter[str] = collections.Counter()
    for name in names:
        stem = (name[:-3] if name.endswith(".py") else name).lower()
        for tok in re.findall(r"[a-z]{2,}", re.sub(r"[0-9]+", " ", stem)):
            counts[tok] += 1
        for tok in re.findall(r"[a-z]{2,}", stem):
            for part in re.findall(r"[a-z]{2,}", re.sub(r"([a-z])([A-Z])", r"\1 \2", tok).lower()):
                counts[part] += 1
    lex = {t for t, c in counts.items() if c >= 2 and 2 <= len(t) <= 18}
    lex |= {t for t in STEM_SEED if re.fullmatch(r"[a-z]{2,18}", t)}
    return lex


def normalise_stem(name: str, lexicon: set[str]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    stem = (name[:-3] if name.endswith(".py") else name).lower()
    letters = re.sub(r"[^a-z]", "", stem)
    best: list[tuple[float, list[str]]] = [(0.0, [])] + [(-1e9, [])] * len(letters)
    for i in range(len(letters)):
        if best[i][0] < -1e8:
            continue
        for j in range(i + 2, min(len(letters), i + 18) + 1):
            cand = letters[i:j]
            gain = (len(cand) ** 1.15 + 2.5) if cand in lexicon else -1.1 * len(cand)
            val = best[i][0] + gain
            if val > best[j][0]:
                best[j] = (val, best[i][1] + [cand])
    toks = tuple(best[len(letters)][1]) if letters else ()
    core = tuple(t for t in toks if t not in GENERIC_STEM_TOKENS and len(t) >= 3)
    return letters, toks, core


def _words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]{2,}", text.lower()):
        w = w.strip("_-'")
        parts = re.findall(r"[a-z]+|[A-Z][a-z]*|[0-9]+", w)
        for p in parts:
            p = p.lower()
            if len(p) >= 3 and p not in STOPWORDS and not p.isdigit():
                out.append(p)
    return out


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append("()")
    return ".".join(reversed(parts))


def _lex_tokens(text: str) -> list[str]:
    toks: list[str] = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            tt, s = t.type, t.string
            if tt in (
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.ERRORTOKEN,
            ):
                continue
            if tt == tokenize.NAME:
                toks.append("kw:" + s if keyword.iskeyword(s) else "n:" + s.lower())
            elif tt == tokenize.NUMBER:
                toks.append("NUM")
            elif tt == tokenize.STRING:
                toks.append("STR")
            elif tt == tokenize.OP:
                toks.append("op:" + s)
            elif s.strip():
                toks.append("op:" + s.strip())
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        pass
    if not toks:
        toks = [
            "kw:" + m if keyword.iskeyword(m) else "n:" + m.lower() for m in re.findall(r"[A-Za-z_]\w*|[^\s\w]", text)
        ]
    return toks


def _shingles(seq: Sequence[str], n: int, prefix: str, sampled: bool = False) -> set[int]:
    if len(seq) < n:
        return {_hash_shingle(prefix + "|".join(seq))} if seq else set()
    out: set[int] = set()
    for i in range(len(seq) - n + 1):
        h = _hash_shingle(prefix + "|".join(seq[i : i + n]))
        if not sampled or h % SHINGLE_SAMPLE == 0:
            out.add(h)
    return out


def extract(path: pathlib.Path, lexicon: set[str]) -> dict[str, Any]:
    text, enc = read_source(path)
    info: dict[str, Any] = {
        "name": path.name,
        "bytes": len(text.encode("utf-8", "replace")),
        "lines": text.count("\n") + 1,
        "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
        "encoding": enc,
        "ast_ok": True,
        "parse_error": None,
        "imports": [],
        "calls": [],
        "options": [],
        "defs": [],
        "doc_words": [],
        "docstring": "",
        "lex_shingles": set(),
        "features": {},
    }

    letters, stem_toks, stem_core = normalise_stem(path.name, lexicon)
    info["stem"] = letters
    info["stem_tokens"] = list(stem_toks)
    info["stem_core"] = list(stem_core)

    feats: dict[str, float] = collections.defaultdict(float)
    api: dict[str, float] = collections.defaultdict(float)

    def add(prefix: str, value: str, weight: float = 1.0, api_feature: bool = False):
        if not value:
            return
        key = f"{prefix}:{value}"
        feats[key] += FEATURE_WEIGHTS[prefix] * weight
        if api_feature or prefix in API_PREFIXES:
            api[key] += FEATURE_WEIGHTS[prefix] * weight

    lex = _lex_tokens(text)
    info["token_count"] = len(lex)
    lex_sh = _shingles(lex, LEX_N, "lex:")
    anon = ["ID" if t.startswith("n:") else t for t in lex]
    info["lex_shingles"] = lex_sh
    for h in _shingles(lex, LEX_N, "lex:", sampled=True):
        add("lex", str(h), 1.0)
    for h in _shingles(anon, LEX_N, "anon:", sampled=True):
        add("anon", str(h), 1.0)

    raw_words = _words(" ".join(re.findall(r"#[^\n]*", text[:4000])))
    info["doc_words"] = list(dict.fromkeys(raw_words))[:40]
    for w, c in collections.Counter(raw_words).most_common(60):
        add("word", w, 1.0 + math.log(c))
    for t in stem_toks:
        add("stem", t, 0.7)
    for t in stem_core:
        add("stem", t, 1.0)
    if letters:
        add("stem", letters, 1.2)

    try:
        tree = ast.parse(text, filename=path.name)
    except (SyntaxError, ValueError) as exc:
        info["ast_ok"] = False
        info["parse_error"] = f"{type(exc).__name__}: {exc.msg if isinstance(exc, SyntaxError) else exc}"
        tree = None

    if tree is not None:
        doc = ast.get_docstring(tree) or ""
        info["docstring"] = " ".join(doc.split())[:300]
        doc_words = _words(doc)
        for w, c in collections.Counter(doc_words).most_common(60):
            add("word", w, 1.5 + 1.5 * math.log(c))

        skeleton: list[str] = []
        stack = [tree]
        while stack:
            node = stack.pop()
            skeleton.append(type(node).__name__)
            for child in ast.iter_child_nodes(node):
                stack.append(child)
                if isinstance(child, ast.Call):
                    dotted = _dotted(child.func)
                    if dotted:
                        info["calls"].append(dotted)
                        add("call", dotted, 1.0)
                        tail = dotted.split(".")[-1]
                        add("call", tail, 0.6)
                    for arg in child.args[:3]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            v = arg.value.strip()
                            if v.startswith("-") and len(v) <= 40:
                                info["options"].append(v)
                                add("opt", v, 1.0)
                            elif len(v) < 40 and not v.startswith(("http", "/")):
                                words = _words(v)
                                info["doc_words"].extend(words)
                                for w in words[:6]:
                                    add("word", w, 0.5)
                elif isinstance(child, ast.Import):
                    for a in child.names:
                        info["imports"].append(a.name)
                        add("imp", a.name.split(".")[0], 1.0)
                        add("imp", a.name, 0.6)
                elif isinstance(child, ast.ImportFrom):
                    if child.module:
                        info["imports"].append(child.module)
                        add("imp", child.module.split(".")[0], 1.0)
                elif isinstance(child, ast.Attribute):
                    d = _dotted(child)
                    if d:
                        add("call", d, 0.4)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    info["defs"].append(child.name)
                    for w in _words(child.name):
                        add("def", w, 1.0)
        for h in _shingles(skeleton, AST_N, "ast:", sampled=True):
            add("ast", str(h), 1.0)

    for k in ("imports", "calls", "options", "defs"):
        info[k] = sorted(set(info[k]))
    info["doc_words"] = [w for w in dict.fromkeys(info["doc_words"]) if w not in STOPWORDS][:40]

    for d in (feats, api):
        for key in list(d):
            if key.startswith("call:"):
                d[key] = min(d[key], 5.0)
            elif key.startswith("word:"):
                d[key] = min(d[key], 5.0)

    purpose: dict[str, float] = collections.defaultdict(float)
    for imp in info["imports"]:
        purpose["imp:" + imp.split(".")[0]] += 3.0
        purpose["imp:" + imp] += 1.2
    for call in info["calls"]:
        purpose["call:" + call] += 0.9
        purpose["call:" + call.split(".")[-1]] += 0.4
    for opt in info["options"]:
        purpose["opt:" + opt] += 3.0
    for dname in info["defs"]:
        for w in set(_words(dname)):
            purpose["def:" + w] += 1.0
    for w in info["doc_words"]:
        purpose["word:" + w] += 1.0
    for t in set(stem_toks):
        purpose["stem:" + t] += 1.5
    for t in set(stem_core):
        purpose["stem:" + t] += 2.0
    if letters:
        purpose["stem:" + letters] += 1.0

    info["features"] = dict(feats)
    info["api_features"] = dict(api)
    info["purpose_features"] = dict(purpose)
    return info


def cosine_pairs(vecs: list[dict[str, float]], n: int, min_score: float, max_df: int) -> dict[tuple[int, int], float]:
    postings: dict[str, list[tuple[int, float]]] = collections.defaultdict(list)
    for i, vec in enumerate(vecs):
        for feat, w in vec.items():
            postings[feat].append((i, w))
    accum: dict[tuple[int, int], float] = collections.defaultdict(float)
    norms = [0.0] * n
    for feat, pl in postings.items():
        df = len(pl)
        if df < 2 or df > max_df or df > n * 0.45:
            continue
        idf = math.log((n + 1) / (df + 1)) + 0.15
        vals = [(i, w * idf) for i, w in pl]
        for i, w in vals:
            norms[i] += w * w
        for a in range(df):
            i, wi = vals[a]
            for b in range(a + 1, df):
                j, wj = vals[b]
                accum[(i, j) if i < j else (j, i)] += wi * wj
    out: dict[tuple[int, int], float] = {}
    for (i, j), dot in accum.items():
        if norms[i] > 0 and norms[j] > 0:
            cos = dot / math.sqrt(norms[i] * norms[j])
            if cos >= min_score:
                out[(i, j)] = cos
    return out


def name_candidates(infos: list[dict[str, Any]]) -> set[tuple[int, int]]:
    groups: dict[str, list[int]] = collections.defaultdict(list)
    for i, inf in enumerate(infos):
        for tok in set(inf.get("stem_core") or inf["stem_tokens"]):
            if len(tok) >= 3:
                groups[tok].append(i)
        for tok in set(inf["stem_tokens"]):
            if len(tok) >= 5:
                groups["w" + tok].append(i)
        s = inf["stem"]
        if len(s) >= 4:
            groups["^" + s[:4]].append(i)
        if len(s) >= 6:
            groups["^" + s[:6]].append(i)
    pairs: set[tuple[int, int]] = set()
    for idxs in groups.values():
        if len(idxs) > 400:
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                pairs.add((min(idxs[a], idxs[b]), max(idxs[a], idxs[b])))
    return pairs


def name_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    sa, sb = a["stem"], b["stem"]
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 1.0
    seq = difflib.SequenceMatcher(None, sa, sb).ratio()
    ta = set(a.get("stem_core") or a["stem_tokens"])
    tb = set(b.get("stem_core") or b["stem_tokens"])
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    sim = 0.55 * seq + 0.45 * jac
    if ta and ta == tb and seq >= 0.55:
        sim = max(sim, 0.70 + 0.20 * seq)
    if min(len(sa), len(sb)) >= 3 and (sa.startswith(sb) or sb.startswith(sa) or sa.endswith(sb) or sb.endswith(sa)):
        sim = max(sim, 0.62 + 0.18 * seq)
    return min(1.0, sim)


def containment(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 0.0
    m = min(len(a), len(b))
    return len(a & b) / m


def approx_containment_pairs(sets: list[set[int]], min_score: float, max_df: int = 180) -> dict[tuple[int, int], float]:
    postings: dict[str, list[int]] = collections.defaultdict(list)
    for i, s in enumerate(sets):
        for sh in s:
            postings[sh].append(i)
    overlap: dict[tuple[int, int], int] = collections.defaultdict(int)
    for pl in postings.values():
        df = len(pl)
        if df < 2 or df > max_df:
            continue
        for a in range(df):
            for b in range(a + 1, df):
                i, j = pl[a], pl[b]
                overlap[(i, j) if i < j else (j, i)] += 1
    sizes = [len(s) for s in sets]
    return {
        p: c / max(1, min(sizes[p[0]], sizes[p[1]]))
        for p, c in overlap.items()
        if c / max(1, min(sizes[p[0]], sizes[p[1]])) >= min_score
    }


def final_score(cos_all: float, cont: float, cos_api: float, cos_purpose: float, name: float) -> float:
    code = 0.55 * cos_all + 0.30 * cont + 0.15 * cos_api
    purpose = 0.50 * cos_purpose + 0.25 * cos_api + 0.25 * name
    s = 0.52 * code + 0.48 * purpose
    if name >= 0.72:
        evidence = max(cos_purpose, cos_api, cos_all, cont)
        s = max(s, 0.47 + 0.40 * evidence)
    if cont >= 0.85 and cos_all >= 0.55:
        s = max(s, 0.86 + 0.12 * cont)
    return min(1.0, s)


def cluster(n: int, scores: dict[tuple[int, int], float], threshold: float) -> tuple[list[list[int]], list[float]]:
    score_of = lambda a, b: scores.get((a, b) if a < b else (b, a), 0.0)
    neighbours: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for (i, j), s in scores.items():
        if s >= threshold * 0.6:
            neighbours[i].append((j, s))
            neighbours[j].append((i, s))
    for nb in neighbours:
        nb.sort(key=lambda x: -x[1])
    density = [sum(s for _, s in nb) for nb in neighbours]
    order = sorted(range(n), key=lambda i: (density[i], len(neighbours[i])), reverse=True)

    groups: list[list[int]] = []
    assigned: dict[int, int] = {}
    for leader in order:
        if leader in assigned:
            continue
        gid = len(groups)
        group = [leader]
        groups.append(group)
        assigned[leader] = gid
        frontier = [leader]
        while frontier:
            nxt: list[int] = []
            for member in frontier:
                for j, s in neighbours[member]:
                    if j in assigned or s < threshold:
                        continue
                    links = [score_of(j, k) for k in group]
                    strong = [v for v in links if v > 0]
                    avg_strong = sum(strong) / len(strong) if strong else 0.0
                    avg_all = sum(links) / len(links)
                    if len(group) >= 5 and avg_all < threshold * 0.28:
                        continue
                    if avg_strong >= threshold * 0.45:
                        assigned[j] = gid
                        group.append(j)
                        nxt.append(j)
            frontier = nxt

    for i in range(n):
        cur = assigned[i]
        if len(groups[cur]) < 2:
            continue
        cur_links = [score_of(i, m) for m in groups[cur] if m != i]
        cur_avg = sum(cur_links) / len(cur_links) if cur_links else 0.0
        best, best_avg = cur, cur_avg
        for gid, g in enumerate(groups):
            if gid == cur or len(g) < 2:
                continue
            links = [score_of(i, m) for m in g]
            avg = sum(links) / len(links)
            if avg > best_avg:
                best, best_avg = gid, avg
        if best != cur and best_avg >= threshold * 0.85 and best_avg > cur_avg + 0.15:
            groups[cur].remove(i)
            groups[best].append(i)
            assigned[i] = best
    return [g for g in groups if g], density


def group_label(members: list[dict[str, Any]]) -> tuple[str, list[str], str]:
    name_counts: collections.Counter[str] = collections.Counter()
    word_counts: collections.Counter[str] = collections.Counter()
    for m in members:
        for t in set(m.get("stem_core") or m["stem_tokens"]):
            name_counts[t] += 1
        for w in set(m["doc_words"][:25]):
            word_counts[w] += 1
    shared = [t for t, c in name_counts.most_common() if c >= 2 or (len(members) == 2 and c >= 1)][:3]
    purpose = [w for w, c in word_counts.most_common() if c >= max(2, len(members) // 3) and w not in shared][:5]
    base = "-".join(shared) if shared else ""
    label = base or members[0]["name"]
    if purpose:
        label += "  ·  " + ", ".join(purpose[:4])
    return label, purpose[:6], base


def build_report(root: pathlib.Path, threshold: float) -> dict[str, Any]:
    files = discover_files(root)
    n = len(files)
    print(f"[1/5] extracting features from {n} Python files ...", flush=True)
    lexicon = build_stem_lexicon([p.name for p in files])
    infos = [extract(p, lexicon) for p in files]

    print("[2/5] computing IDF-weighted cosine similarity ...", flush=True)
    cos_all = cosine_pairs([i["features"] for i in infos], n, 0.05, 300)
    cos_api = cosine_pairs([i["api_features"] for i in infos], n, 0.12, 400)
    cos_purpose = cosine_pairs([i["purpose_features"] for i in infos], n, 0.12, 400)
    print(f"      cos_all: {len(cos_all)}, cos_api: {len(cos_api)}, cos_purpose: {len(cos_purpose)} pairs", flush=True)

    print("[3/5] building candidate pairs and scoring ...", flush=True)
    name_pairs = name_candidates(infos)
    lex_sets = [i["lex_shingles"] for i in infos]
    approx = approx_containment_pairs(lex_sets, 0.30)
    candidates = set(cos_all) | set(cos_api) | set(cos_purpose) | name_pairs | set(approx)
    print(f"      candidate pairs: {len(candidates)}", flush=True)

    scores: dict[tuple[int, int], float] = {}
    components: dict[tuple[int, int], dict[str, float]] = {}
    for i, j in candidates:
        c_all = cos_all.get((i, j), 0.0)
        c_api = cos_api.get((i, j), 0.0)
        c_purp = cos_purpose.get((i, j), 0.0)
        cont = containment(lex_sets[i], lex_sets[j])
        name = name_similarity(infos[i], infos[j])
        s = final_score(c_all, cont, c_api, c_purp, name)
        if s >= 0.22:
            scores[(i, j)] = s
            components[(i, j)] = {
                "cos_all": c_all,
                "containment": cont,
                "cos_api": c_api,
                "cos_purpose": c_purp,
                "name_sim": name,
            }

    print(f"[4/5] clustering {len(scores)} scored pairs ...", flush=True)
    raw_groups, density = cluster(n, scores, threshold)
    multi = [g for g in raw_groups if len(g) >= 2]
    multi.sort(key=lambda g: (-len(g), -sum(density[m] for m in g)))
    singles = [g[0] for g in raw_groups if len(g) == 1]

    members_of = {i: gi for gi, g in enumerate(multi) for i in g}
    score_of = lambda a, b: scores.get((a, b) if a < b else (b, a), 0.0)

    groups_out: list[dict[str, Any]] = []
    for gi, g in enumerate(multi, 1):
        medoid = max(g, key=lambda m: sum(score_of(m, x) for x in g if x != m))
        mem_infos = [infos[i] for i in g]
        label, purpose, base = group_label(mem_infos)
        intra = [score_of(g[a], g[b]) for a in range(len(g)) for b in range(a + 1, len(g))]
        shared_imports = sorted(set.intersection(*[set(m["imports"]) for m in mem_infos])) if len(g) > 1 else []
        shared_opts = sorted(set.intersection(*[set(m["options"]) for m in mem_infos])) if len(g) > 1 else []
        shared_defs = sorted(set.intersection(*[set(m["defs"]) for m in mem_infos])) if len(g) > 1 else []
        members = []
        for i in g:
            nearest, ns = max(
                ((j, score_of(i, j)) for j in g if j != i),
                key=lambda x: x[1],
                default=(None, 0.0),
            )
            members.append({
                "file": infos[i]["name"],
                "lines": infos[i]["lines"],
                "bytes": infos[i]["bytes"],
                "sha256": infos[i]["sha256"],
                "ast_ok": infos[i]["ast_ok"],
                "nearest_in_group": infos[nearest]["name"] if nearest is not None else None,
                "nearest_score": round(ns, 4),
                "imports": infos[i]["imports"][:18],
                "options": infos[i]["options"][:18],
                "top_functions": infos[i]["defs"][:18],
                "docstring": infos[i]["docstring"],
            })
        members.sort(key=lambda m: (m["file"] != infos[medoid]["name"], m["file"]))
        pairs = sorted(
            (
                (score_of(g[a], g[b]), infos[g[a]]["name"], infos[g[b]]["name"])
                for a in range(len(g))
                for b in range(a + 1, len(g))
            ),
            reverse=True,
        )[:6]
        avg = sum(intra) / len(intra) if intra else 0.0
        strong_pairs = [
            (g[a], g[b]) for a in range(len(g)) for b in range(a + 1, len(g)) if score_of(g[a], g[b]) >= threshold * 0.6
        ]

        def comp_avg(key: str) -> float:
            vals = []
            for a, b in strong_pairs:
                c = components.get((a, b) if a < b else (b, a))
                if c:
                    vals.append(c[key])
            return sum(vals) / len(vals) if vals else 0.0

        cohesion = sum(score_of(a, b) for a, b in strong_pairs) / max(1, len(strong_pairs))
        av_code = (comp_avg("cos_all") + comp_avg("containment")) / 2
        av_purpose = (comp_avg("cos_purpose") + comp_avg("name_sim")) / 2
        if av_code >= 0.65:
            basis = "near-duplicate / heavily shared code"
        elif av_code >= 0.40:
            basis = "mixed: shared code + shared purpose"
        elif av_purpose >= 0.60:
            basis = "same job family, different implementations"
        else:
            basis = "loosely related (review before merging)"
        confidence = "high" if cohesion >= 0.72 else "medium" if cohesion >= 0.58 else "low"
        groups_out.append({
            "signal_averages": {
                "cos_code": round(comp_avg("cos_all"), 4),
                "containment": round(comp_avg("containment"), 4),
                "cos_api": round(comp_avg("cos_api"), 4),
                "cos_purpose": round(comp_avg("cos_purpose"), 4),
                "name_sim": round(comp_avg("name_sim"), 4),
            },
            "id": f"G{gi:03d}",
            "label": label,
            "keywords": purpose,
            "stem_family": base,
            "size": len(g),
            "medoid": infos[medoid]["name"],
            "basis": basis,
            "confidence": confidence,
            "cohesion": round(cohesion, 4),
            "strong_link_density": round(len(strong_pairs) / max(1, len(intra)), 4),
            "avg_similarity": round(avg, 4),
            "min_similarity": round(min(intra), 4) if intra else 0.0,
            "max_similarity": round(max(intra), 4) if intra else 0.0,
            "shared_imports": shared_imports[:15],
            "shared_cli_options": shared_opts[:15],
            "shared_definitions": shared_defs[:15],
            "recommendation": (
                "near-duplicate: consolidate into one script"
                if (intra and max(intra) >= 0.88)
                else (
                    "strong overlap: pick one canonical script, retire the rest"
                    if (intra and sum(intra) / len(intra) >= 0.7)
                    else "review: scripts appear to do the same job"
                )
            ),
            "members": members,
            "top_pairs": [{"a": a, "b": b, "score": round(s, 4)} for s, a, b in pairs],
        })

    top_pairs = sorted(
        (
            {
                "a": infos[i]["name"],
                "b": infos[j]["name"],
                "score": round(s, 4),
                "cos_all": round(components[(i, j)]["cos_all"], 4),
                "containment": round(components[(i, j)]["containment"], 4),
                "cos_api": round(components[(i, j)]["cos_api"], 4),
                "cos_purpose": round(components[(i, j)]["cos_purpose"], 4),
                "name_sim": round(components[(i, j)]["name_sim"], 4),
                "same_group": (i in members_of and j in members_of and members_of[i] == members_of[j]),
            }
            for (i, j), s in scores.items()
            if s >= 0.45
        ),
        key=lambda x: -x["score"],
    )[:80]

    by_hash: dict[str, list[str]] = collections.defaultdict(list)
    for inf in infos:
        by_hash[inf["sha256"]].append(inf["name"])
    exact = sorted((v for v in by_hash.values() if len(v) > 1), key=lambda v: v[0])

    grouped_names = {infos[i]["name"] for g in multi for i in g}
    domains: dict[str, list[str]] = collections.defaultdict(list)
    for inf in infos:
        for tok in set(inf["stem_core"]):
            if len(tok) >= 3 and tok not in GENERIC_STEM_TOKENS:
                domains[tok].append(inf["name"])
    functional_families = [
        {
            "keyword": tok,
            "scripts": len(v),
            "in_groups": sum(1 for x in v if x in grouped_names),
            "examples": sorted(v)[:8],
        }
        for tok, v in domains.items()
        if len(v) >= 5
    ]
    functional_families.sort(key=lambda d: (-d["scripts"], d["keyword"]))

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repository": "i4147/bin_oct",
        "root": str(root),
        "method": {
            "goal": "group scripts that do nearly the same job",
            "signals": {k: v for k, v in FEATURE_WEIGHTS.items()},
            "score_formula": (
                "0.52*(0.55*cos_all + 0.30*containment + 0.15*cos_api) + "
                "0.48*(0.50*cos_purpose + 0.25*cos_api + 0.25*name_sim); "
                "plus a filename-variant boost (name_sim >= 0.72) and a "
                "near-literal-copy boost (containment >= 0.85)"
            ),
            "idf": "log((N+1)/(df+1)) + 0.15; features with df > 300 or df > 45% of N are dropped",
            "candidate_generation": [
                "cos_all >= 0.05",
                "cos_api >= 0.12",
                "cos_purpose >= 0.12",
                "normalised filename variants (corpus-segmented words + prefixes)",
                "approx. token containment >= 0.30",
            ],
            "clustering": "frontier expansion from the densest script (a group may grow through "
            "any member) with a cohesion test and one conservative refinement pass",
            "group_threshold": threshold,
            "score_components": {
                "cos_all": "IDF cosine over lexical/structural/semantic features",
                "containment": "5-gram token overlap / smaller file",
                "cos_api": "cosine over imports, called APIs and CLI flags",
                "cos_purpose": "cosine over imports, calls, CLI flags, defined names, "
                "doc/comment words and filename words (no code shingles)",
                "name_sim": "similarity of normalised, corpus-segmented filename stems",
            },
            "caveats": [
                "Static analysis only: no script was executed.",
                "Files that fail to parse on Python 3.11 use lexical features only.",
                "Similarity measures implementation/purpose overlap, not proven equivalence.",
            ],
        },
        "summary": {
            "files_analyzed": n,
            "groups": len(groups_out),
            "files_in_groups": sum(g["size"] for g in groups_out),
            "singletons": len(singles),
            "largest_group": groups_out[0]["size"] if groups_out else 0,
            "scored_pairs": len(scores),
            "pairs_at_or_above_threshold": sum(1 for s in scores.values() if s >= threshold),
            "near_duplicate_pairs": sum(1 for s in scores.values() if s >= 0.88),
            "exact_duplicate_sets": len(exact),
            "parse_failures": sum(1 for i in infos if not i["ast_ok"]),
            "total_lines": sum(i["lines"] for i in infos),
        },
        "exact_duplicate_sets": exact,
        "functional_families": functional_families,
        "unparseable_files": [i["name"] for i in infos if not i["ast_ok"]],
        "groups": groups_out,
        "top_pairs": top_pairs,
        "singletons": sorted(infos[i]["name"] for i in singles),
        "files": [
            {
                "name": i["name"],
                "lines": i["lines"],
                "bytes": i["bytes"],
                "sha256": i["sha256"],
                "encoding": i["encoding"],
                "ast_ok": i["ast_ok"],
                "parse_error": i["parse_error"],
                "stem": i["stem"],
                "stem_words": i["stem_core"],
                "docstring": i["docstring"],
                "imports": i["imports"][:25],
                "options": i["options"][:25],
            }
            for i in infos
        ],
        "pair_scores_for_heatmap": [
            {
                "a": infos[i]["name"],
                "b": infos[j]["name"],
                "score": round(s, 4),
                "cos_all": round(components[(i, j)]["cos_all"], 4),
                "containment": round(components[(i, j)]["containment"], 4),
                "cos_api": round(components[(i, j)]["cos_api"], 4),
                "cos_purpose": round(components[(i, j)]["cos_purpose"], 4),
                "name_sim": round(components[(i, j)]["name_sim"], 4),
            }
            for (i, j), s in scores.items()
        ],
    }
    return report


def render_markdown(rep: dict[str, Any], out: pathlib.Path) -> None:
    s = rep["summary"]
    L = [
        "# Python script similarity report",
        "",
        f"Generated {rep['generated_at']} · **{s['files_analyzed']} scripts** · "
        f"**{s['groups']} groups** · {s['files_in_groups']} scripts belong to a group "
        f"({s['singletons']} singletons).",
        "",
        "Scripts that do nearly the same job are placed in the same group. "
        "The machine-readable version is `similarity_report.json`; the visual "
        "version is `similarity_report.svg`.",
        "",
        "## Method (short)",
        "",
        "Each script is turned into a weighted feature vector built from normalised "
        "token 5-grams, an AST node-type skeleton, imports, called APIs, CLI flags, "
        "function names and documentation/filename vocabulary. Features are IDF "
        "weighted so that boilerplate does not create false matches. Pairs are scored "
        f"with `{rep['method']['score_formula']}`, and pairs at or above "
        f"`{rep['method']['group_threshold']}` are grouped by density-based leader "
        "clustering around a medoid script.",
        "",
        "## Overview",
        "",
        "| Group | Scripts | Avg. score | Medoid (best keeper) | What they do |",
        "|---|---:|---:|---|---|",
    ]
    for g in rep["groups"][:60]:
        label = g["label"].replace("|", "\\|")
        L.append(f"| {g['id']} | {g['size']} | {g['avg_similarity']:.3f} | `{g['medoid']}` | {label} |")
    if len(rep["groups"]) > 60:
        L.append(f"\n_…and {len(rep['groups']) - 60} more groups; see the JSON report._")

    L += ["", "## Groups in detail", ""]
    for g in rep["groups"]:
        L += [
            f"### {g['id']} — {g['label']}",
            "",
            f"- **{g['size']} scripts**, avg similarity **{g['avg_similarity']:.3f}** "
            f"(range {g['min_similarity']:.3f}–{g['max_similarity']:.3f})",
            f"- **Suggested keeper:** `{g['medoid']}`",
            f"- **Basis:** {g['basis']} (confidence: {g['confidence']}, cohesion {g['cohesion']:.2f})",
            f"- **Recommendation:** {g['recommendation']}",
        ]
        if g["shared_imports"]:
            L.append(f"- **Shared imports:** {', '.join('`' + x + '`' for x in g['shared_imports'][:12])}")
        if g["shared_cli_options"]:
            L.append(f"- **Shared CLI flags:** {', '.join('`' + x + '`' for x in g['shared_cli_options'][:12])}")
        if g["shared_definitions"]:
            L.append(f"- **Shared definitions:** {', '.join('`' + x + '`' for x in g['shared_definitions'][:12])}")
        L += ["", "<details><summary>members</summary>", ""]
        for m in g["members"]:
            L.append(
                f"- `{m['file']}` — {m['lines']} lines"
                + (f", nearest `{m['nearest_in_group']}` ({m['nearest_score']:.2f})" if m["nearest_in_group"] else "")
            )
        L += ["", "</details>", ""]

    L += ["", "## Strongest pairs overall", "", "| A | B | score | containment | name |", "|---|---|---:|---:|---:|"]
    for p in rep["top_pairs"][:30]:
        L.append(f"| `{p['a']}` | `{p['b']}` | {p['score']:.3f} | {p['containment']:.3f} | {p['name_sim']:.3f} |")
    L += [
        "",
        "## Functional domains",
        "",
        "Broad filename-derived job keywords, including scripts that remained "
        "singletons (useful for deciding where to start consolidating).",
        "",
        "| Keyword | Scripts | In a group | Examples |",
        "|---|---:|---:|---|",
    ]
    for d in rep["functional_families"][:40]:
        L.append(
            f"| `{d['keyword']}` | {d['scripts']} | {d['in_groups']} | "
            + ", ".join(f"`{x}`" for x in d["examples"][:4])
            + " |"
        )
    L += ["", "## Caveats", ""] + [f"- {c}" for c in rep["method"]["caveats"]]
    out.write_text("\n".join(L) + "\n", encoding="utf-8")


PALETTE = [
    "#5eead4",
    "#93c5fd",
    "#fca5a5",
    "#fcd34d",
    "#c4b5fd",
    "#86efac",
    "#f9a8d4",
    "#67e8f9",
    "#fdba74",
    "#a5b4fc",
    "#bef264",
    "#d8b4fe",
]

E = lambda x: html.escape(str(x))


def rect(x: float, y: float, w: float, h: float, fill: str, **kw: Any) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{E(v)}"' for k, v in kw.items())
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(0, w):.1f}" height="{max(0, h):.1f}" fill="{fill}" {extra}/>'


def text(x: float, y: float, s: str, size: int = 12, fill: str = "#cbd5e1", anchor: str = "start", **kw: Any) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{E(v)}"' for k, v in kw.items())
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" {extra}>{E(s)}</text>'
    )


def score_color(s: float) -> str:
    stops = [
        (0.0, (17, 30, 58)),
        (0.25, (30, 64, 120)),
        (0.5, (14, 116, 144)),
        (0.7, (45, 175, 150)),
        (0.85, (250, 204, 21)),
        (1.0, (248, 113, 113)),
    ]
    s = max(0.0, min(1.0, s))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if s <= b:
            t = (s - a) / (b - a) if b > a else 0
            c = tuple(round(ca[k] + t * (cb[k] - ca[k])) for k in range(3))
            return "#%02x%02x%02x" % c
    return "#f87171"


def squarify(
    items: list[tuple[float, Any]], x: float, y: float, w: float, h: float
) -> list[tuple[Any, float, float, float, float]]:
    out: list[tuple[Any, float, float, float, float]] = []
    items = sorted(items, key=lambda it: -it[0])
    total = sum(v for v, _ in items)
    if total <= 0 or not items:
        return out
    rows = max(1, min(len(items), round(math.sqrt(len(items) * h / w))))
    rows = max(rows, 2) if len(items) > 3 else rows
    target = total / rows
    strips: list[list[tuple[float, Any]]] = []
    cur: list[tuple[float, Any]] = []
    cur_sum = 0.0
    for val, key in items:
        if cur and len(strips) < rows - 1 and cur_sum + val / 2 > target:
            strips.append(cur)
            cur, cur_sum = [], 0.0
        cur.append((val, key))
        cur_sum += val
    if cur:
        strips.append(cur)
    sums = [sum(v for v, _ in st) for st in strips]
    yy = y
    for strip, ssum in zip(strips, sums):
        rh = h * ssum / total
        if not strip or rh <= 0:
            continue
        xx = x
        for val, key in strip:
            cw = w * val / ssum
            out.append((key, xx, yy, cw, rh))
            xx += cw
        yy += rh
    return out


def render_svg(rep: dict[str, Any], out: pathlib.Path) -> None:
    s = rep["summary"]
    W, H = 1920, 1980
    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,DejaVu Sans Mono,monospace">',
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0%" stop-color="#080e1d"/><stop offset="100%" stop-color="#101a30"/>'
        "</linearGradient></defs>",
        rect(0, 0, W, H, "url(#bg)"),
    ]
    svg += [
        text(48, 62, "bin_oct  /  Python script similarity", 30, "#f1f5f9", font_weight="700"),
        text(
            48,
            92,
            "Scripts that do nearly the same job are grouped together  ·  "
            "IDF-weighted code + AST + API + CLI + purpose similarity",
            15,
            "#94a3b8",
        ),
        text(W - 48, 62, rep["generated_at"].replace("+00:00", "Z"), 14, "#64748b", anchor="end"),
        text(
            W - 48,
            86,
            f"threshold {rep['method']['group_threshold']}  ·  "
            f"{s['files_analyzed']} files  ·  {s['total_lines']:,} lines",
            14,
            "#64748b",
            anchor="end",
        ),
        rect(48, 112, W - 96, 2, "#1e293b"),
    ]
    kpis = [
        ("SCRIPTS", s["files_analyzed"], "#e2e8f0"),
        ("GROUPS", s["groups"], "#5eead4"),
        ("IN A GROUP", s["files_in_groups"], "#93c5fd"),
        ("LARGEST GROUP", s["largest_group"], "#fcd34d"),
        ("NEAR-DUPLICATE PAIRS", s["near_duplicate_pairs"], "#fb923c"),
        ("SINGLETONS", s["singletons"], "#94a3b8"),
    ]
    kw = (W - 96 - 5 * 16) / 6
    for i, (label, val, col) in enumerate(kpis):
        x = 48 + i * (kw + 16)
        svg += [
            rect(x, 136, kw, 92, "#111c33", rx=10, stroke="#22314f"),
            text(x + 18, 168, label, 12, "#64748b"),
            text(x + 18, 210, f"{val:,}", 32, col, font_weight="700"),
        ]

    def frame(x, y, w, h, title, sub=""):
        svg.append(rect(x, y, w, h, "#0d1729", rx=12, stroke="#22314f"))
        svg.append(text(x + 20, y + 32, title, 18, "#e2e8f0", font_weight="700"))
        if sub:
            svg.append(text(x + 20, y + 54, sub, 12, "#64748b"))

    groups = rep["groups"]
    frame(
        48,
        250,
        1100,
        690,
        "Cluster map",
        "each block = one group of scripts doing the same job · area ≈ number of scripts",
    )
    frame(
        1172,
        250,
        700,
        690,
        "Similarity network",
        "top groups · each ring of nodes is a group · lines = strong pairwise similarity",
    )
    frame(
        48,
        962,
        1100,
        700,
        "Similarity heatmap",
        "pairwise scores for the largest groups · blocks along the diagonal are the groups",
    )
    frame(1172, 962, 700, 700, "Strongest pairs & largest groups", "score = combined similarity")
    frame(
        48,
        1680,
        W - 96,
        240,
        "Functional domains across all scripts",
        "filename-derived job keywords · teal = scripts already in a group, grey = singletons",
    )

    shown = groups[:18]
    other = groups[18:]
    sizes = [(max(1.0, g["size"] ** 0.85), i) for i, g in enumerate(shown)]
    cells = squarify(sizes, 68, 320, 1060, 558)
    svg.append(
        text(
            68,
            908,
            f"+ {len(other)} smaller groups ({sum(g['size'] for g in other)} scripts) "
            f"and {s['singletons']} singletons — full list in similarity_report.json",
            12,
            "#64748b",
        )
    )
    for key, x, y, w, h in cells:
        col = "#334155" if key < 0 else PALETTE[key % len(PALETTE)]
        dark = "#152238" if key < 0 else "#132038"
        svg.append(rect(x + 2, y + 2, w - 4, h - 4, dark, rx=7, stroke=col, stroke_opacity="0.55"))
        svg.append(rect(x + 2, y + 2, 5, h - 4, col, rx=2))
        if key == -1:
            svg.append(text(x + 16, y + 26, f"{s['singletons']} singletons", 15, "#94a3b8"))
            svg.append(text(x + 16, y + 46, "no close counterpart found", 11, "#64748b"))
        elif key == -2:
            svg.append(text(x + 16, y + 26, f"{len(other)} smaller groups", 15, "#94a3b8"))
            svg.append(text(x + 16, y + 46, f"{sum(g['size'] for g in other)} scripts · see JSON", 11, "#64748b"))
        else:
            g = shown[key]
            header = f"{g['id']} · {g['size']}" if w < 150 else f"{g['id']} · {g['size']} scripts"
            svg.append(text(x + 14, y + 25, header, 13 if w < 150 else 14, col, font_weight="700"))
            if w < 118 or h < 62:
                continue
            maxrows = max(0, int((h - 36) // 15))
            fontsize = 10 if w < 200 else 11
            maxcols = max(6, int((w - 24) / (fontsize * 0.62)))
            for k, m in enumerate(g["members"][:maxrows]):
                nm = m["file"]
                if len(nm) > maxcols:
                    nm = nm[: maxcols - 1] + "…"
                svg.append(text(x + 14, y + 45 + k * 15, nm, fontsize, "#cbd5e1"))
            if len(g["members"]) > maxrows:
                svg.append(text(x + 14, y + 45 + maxrows * 15, f"+{len(g['members']) - maxrows} more", 10, "#64748b"))

    net_groups = groups[:14]
    node_names: list[str] = []
    node_group: list[int] = []
    for gi, g in enumerate(net_groups):
        for m in g["members"][: min(9, len(g["members"]))]:
            node_names.append(m["file"])
            node_group.append(gi)
    N = len(node_names)
    cx, cy, R = 1522, 630, 232
    total = sum(len([x for x in node_group if x == gi]) for gi in range(len(net_groups)))
    pos: dict[str, tuple[float, float]] = {}
    angle = -math.pi / 2
    for gi in range(len(net_groups)):
        idxs = [i for i, g in enumerate(node_group) if g == gi]
        if not idxs:
            continue
        span = 2 * math.pi * len(idxs) / max(1, total)
        for k, i in enumerate(idxs):
            a = angle + span * (k + 0.5) / len(idxs)
            r = R - (k % 3) * 18
            pos[node_names[i]] = (cx + r * math.cos(a), cy + r * math.sin(a))
        am = angle + span / 2
        lx, ly = cx + (R + 34) * math.cos(am), cy + (R + 34) * math.sin(am)
        anchor = "start" if math.cos(am) >= 0 else "end"
        glabel = (net_groups[gi]["label"].split("·")[0].strip() or net_groups[gi]["medoid"])[:22]
        svg.append(text(lx, ly, glabel, 10, PALETTE[gi % len(PALETTE)], anchor=anchor))
        angle += span

    scoremat: dict[frozenset[str], float] = {}
    for p in rep["pair_scores_for_heatmap"]:
        scoremat[frozenset((p["a"], p["b"]))] = p["score"]
    edges = 0
    for i in range(N):
        for j in range(i + 1, N):
            v = scoremat.get(frozenset((node_names[i], node_names[j])), 0.0)
            if v < 0.55:
                continue
            x1, y1 = pos[node_names[i]]
            x2, y2 = pos[node_names[j]]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            mx = mx * 0.82 + cx * 0.18
            my = my * 0.82 + cy * 0.18
            svg.append(
                f'<path d="M{x1:.1f},{y1:.1f} Q{mx:.1f},{my:.1f} {x2:.1f},{y2:.1f}" '
                f'fill="none" stroke="{score_color(v)}" stroke-width="{0.6 + 2.6 * v:.2f}" '
                f'stroke-opacity="{0.18 + 0.5 * v:.2f}"/>'
            )
            edges += 1
    for i, nm in enumerate(node_names):
        x, y = pos[nm]
        col = PALETTE[node_group[i] % len(PALETTE)]
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.2" fill="{col}" stroke="#0d1729" stroke-width="1"/>')
    svg.append(text(cx, cy - 12, f"{N} scripts", 22, "#e2e8f0", anchor="middle", font_weight="700"))
    svg.append(text(cx, cy + 14, f"{edges} strong links", 12, "#64748b", anchor="middle"))

    hm_groups = groups[:22]
    order: list[str] = []
    bounds: list[int] = [0]
    for g in hm_groups:
        take = min(len(g["members"]), 8)
        order.extend(m["file"] for m in g["members"][:take])
        bounds.append(len(order))
        if len(order) >= 88:
            break
    m = len(order)
    hx, hy = 300, 1058
    cell = min(9.0, 824 / max(1, m), 590 / max(1, m))
    svg.append(rect(hx - 6, hy - 6, m * cell + 12, m * cell + 12, "#0a1322", rx=4, stroke="#1e293b"))
    for i in range(m):
        for j in range(m):
            if i == j:
                col = "#fcd34d"
            else:
                col = score_color(scoremat.get(frozenset((order[i], order[j])), 0.0))
            svg.append(rect(hx + j * cell, hy + i * cell, cell + 0.4, cell + 0.4, col))
    for gi in range(1, len(bounds)):
        b = bounds[gi]
        if b > m:
            break
        p = b * cell
        svg.append(
            f'<line x1="{hx - 2}" y1="{hy + p}" x2="{hx + m * cell + 2}" y2="{hy + p}" '
            'stroke="#f8fafc" stroke-width="1.1" stroke-opacity="0.7"/>'
        )
        svg.append(
            f'<line x1="{hx + p}" y1="{hy - 2}" x2="{hx + p}" y2="{hy + m * cell + 2}" '
            'stroke="#f8fafc" stroke-width="1.1" stroke-opacity="0.7"/>'
        )
    for gi in range(len(bounds) - 1):
        a, b = bounds[gi], min(m, bounds[gi + 1])
        if a >= m:
            break
        col = PALETTE[gi % len(PALETTE)]
        svg.append(rect(hx + a * cell, hy - 16, (b - a) * cell - 1, 8, col, rx=2))
        if (b - a) * cell > 42:
            svg.append(text(hx + a * cell + 3, hy - 20, hm_groups[gi]["id"], 9, col))
    fontsize = 9 if m > 70 else 10
    for i, nm in enumerate(order):
        if cell >= 7 or i % 2 == 0:
            svg.append(
                text(
                    hx - 10,
                    hy + i * cell + cell * 0.78,
                    nm if len(nm) <= 28 else nm[:27] + "…",
                    fontsize,
                    "#94a3b8",
                    anchor="end",
                )
            )
    svg.append(
        text(
            hx + m * cell,
            hy + m * cell + 26,
            f"{m} scripts from {len(bounds) - 1} groups · columns in the same order",
            11,
            "#64748b",
            anchor="end",
        )
    )
    lx, ly = 70, 1040
    svg.append(text(lx, ly, "SIMILARITY", 12, "#94a3b8"))
    for k in range(11):
        v = k / 10
        svg.append(rect(lx, ly + 12 + k * 20, 18, 18, score_color(v)))
        svg.append(text(lx + 26, ly + 26 + k * 20, f"{v:.1f}", 11, "#94a3b8"))
    svg.append(text(lx, ly + 258, "yellow diagonal", 11, "#fcd34d"))
    svg.append(text(lx, ly + 276, "= self-match", 11, "#64748b"))
    svg.append(text(lx, ly + 308, "White lines mark", 11, "#94a3b8"))
    svg.append(text(lx, ly + 324, "group boundaries.", 11, "#94a3b8"))

    px, py = 1192, 1046
    svg.append(text(px, py, "TOP PAIRS", 12, "#64748b"))
    for k, p in enumerate(rep["top_pairs"][:16]):
        y = py + 26 + k * 21
        svg.append(text(px, y, f"{p['a']}  ↔  {p['b']}"[:52], 11, "#cbd5e1"))
        svg.append(rect(px + 410, y - 10, 180, 11, "#16233b", rx=3))
        svg.append(rect(px + 410, y - 10, 180 * p["score"], 11, score_color(p["score"]), rx=3))
        svg.append(text(px + 598, y, f"{p['score']:.2f}", 11, "#94a3b8"))
    gy = py + 26 + 16 * 21 + 10
    svg.append(text(px, gy, "LARGEST GROUPS", 12, "#64748b"))
    for k, g in enumerate(groups[:10]):
        y = gy + 26 + k * 20
        label = g["label"][:46]
        svg.append(rect(px, y - 11, 11, 11, PALETTE[k % len(PALETTE)], rx=2))
        svg.append(text(px + 20, y, f"{g['id']}  {label}"[:60], 11, "#cbd5e1"))
        svg.append(text(px + 640, y, str(g["size"]), 12, PALETTE[k % len(PALETTE)], anchor="end", font_weight="700"))

    fams = rep["functional_families"][:24]
    if fams:
        maxv = max(d["scripts"] for d in fams)
        for k, d in enumerate(fams):
            col, row = k // 8, k % 8
            bx = 68 + col * 600
            by = 1744 + row * 21
            total_w = 300 * d["scripts"] / maxv
            grouped_w = 300 * d["in_groups"] / maxv
            svg.append(text(bx, by + 10, d["keyword"][:14], 11, "#cbd5e1"))
            svg.append(rect(bx + 108, by, total_w, 13, "#2b3b55", rx=3))
            if grouped_w > 0:
                svg.append(rect(bx + 108, by, grouped_w, 13, PALETTE[col % len(PALETTE)], rx=3))
            svg.append(text(bx + 416, by + 11, f"{d['scripts']:>2} scripts · {d['in_groups']} grouped", 10, "#94a3b8"))

    svg.append(rect(48, H - 52, W - 96, 1, "#1e293b"))
    svg.append(
        text(
            48,
            H - 24,
            "score = code duplication (shingles, AST, API) blended with purpose (imports, CLI, names)"
            "   |   static analysis only — no script was executed"
            "   |   see similarity_report.json for full data",
            12,
            "#64748b",
        )
    )
    svg.append("</svg>")
    out.write_text("\n".join(svg) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=pathlib.Path, default=ROOT, help="repository root")
    ap.add_argument("--out-dir", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent)
    ap.add_argument("--threshold", type=float, default=0.55, help="minimum score for two scripts to share a group")
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--no-md", action="store_true")
    args = ap.parse_args()

    out_dir: pathlib.Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = build_report(args.root.resolve(), args.threshold)

    jp = out_dir / "similarity_report.json"
    jp.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[5/5] wrote {jp} ({jp.stat().st_size / 1024:.0f} KiB)", flush=True)
    if not args.no_md:
        mp = out_dir / "similarity_report.md"
        render_markdown(rep, mp)
        print(f"      wrote {mp}", flush=True)
    if not args.no_svg:
        sp = out_dir / "similarity_report.svg"
        render_svg(rep, sp)
        print(f"      wrote {sp} ({sp.stat().st_size / 1024:.0f} KiB)", flush=True)
    s = rep["summary"]
    print(
        f"done: {s['groups']} groups covering {s['files_in_groups']}/{s['files_analyzed']} "
        f"scripts; {s['singletons']} singletons",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
