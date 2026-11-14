#!/data/data/com.termux/files/usr/bin/env python
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import textwrap
import threading
import time
import tomllib
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

import requests
from pygments import highlight
from pygments.formatters import TerminalFormatter
from pygments.lexers import JsonLexer

try:
    import idna
except ImportError:
    idna = None

try:
    import yaml
except ImportError:
    yaml = None

try:
    from jsonpath_ng.ext import parse as jsonpath_parse
except ImportError:
    jsonpath_parse = None

VERSION = "0.2.0"
MYNAME = (sys.argv[0] if sys.argv else "bestwhois").replace("./", "")
CHANGELOG = f"""bestwhois {VERSION}
- Added batch, parallel, and rate-limited execution
- Added YAML, CSV, JSONPath, and raw-whois output formats
- Added reverse, IP, and DNS query modes
- Added TOML config, profiles, dotenv, and caching
- Added retries, timeouts, proxy, and TLS controls
- Added shell completion, manpage, and --version-full
"""
TEXT_FIELDS = ("rawText", "strippedText", "cleanText", "header", "footer")
HISTORY_BASE = "https://whois-history-api.whoisxmlapi.com/api/v1"
WHOIS_BASE = "https://www.whoisxmlapi.com/whoisserver/WhoisService"
DNS_BASE = "https://www.whoisxmlapi.com/whoisserver/DNSService"
REVERSE_BASE = "https://reverse-whois-api.whoisxmlapi.com/api/v2"
ACCOUNT_BASE = "https://user.whoisxmlapi.com"
RC_FILE_LOCS: tuple[Path, ...] = (
    Path(".bestwhoisrc"),
    Path("~/.bestwhoisrc").expanduser(),
    Path("data/data/com.termux/files/usr/etc/bestwhois/bestwhoisrc"),
)


@dataclass
class QueryResult:
    domain: str
    records: list[dict[str, Any]]
    record_count: int
    is_history: bool
    error: str | None = None


class RateLimiter:
    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.interval:
                time.sleep(self.interval - delta)
            self._last = time.monotonic()


def redact_key(url: str) -> str:
    return re.sub(r"(apiKey=)[^&]+", r"\1***", url)


def valid_date(s: str) -> str:
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date: {s} (expected YYYY-MM-DD)")
    return s


def is_empty_field(field: Any, *, keep_null: bool = False) -> bool:
    if field is None:
        return not keep_null
    return isinstance(field, (str, dict, list)) and not field


def purge_empty_fields(structure: dict[str, Any], *, keep_null: bool = False, recurse: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, value in structure.items():
        if recurse and isinstance(value, dict):
            value = purge_empty_fields(value, keep_null=keep_null, recurse=recurse)
        if not is_empty_field(value, keep_null=keep_null):
            result[field] = value
    return result


def _format_text(structure: dict[str, Any], depth: int, lines: list[str]) -> None:
    for field, value in structure.items():
        tabs = "\t" * depth
        if isinstance(value, list):
            lines.append(f"{tabs}{field}: {', '.join(str(item) for item in value)}")
        elif isinstance(value, dict):
            lines.append(f"{tabs}{field}: ")
            _format_text(value, depth + 1, lines)
        else:
            lines.append(f"{tabs}{field}: {value}")


def format_text(structure: dict[str, Any]) -> str:
    lines: list[str] = []
    _format_text(structure, 0, lines)
    return "\n".join(lines)


def get_dotted(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def set_dotted(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def del_dotted(d: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    cur: Any = d
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def select_fields(record: dict[str, Any], include: list[str] | None, exclude: list[str] | None) -> dict[str, Any]:
    if include:
        result: dict[str, Any] = {}
        for field in include:
            field = field.strip()
            if not field:
                continue
            v = get_dotted(record, field)
            if v is not None:
                set_dotted(result, field, v)
        record = result
    if exclude:
        for field in exclude:
            del_dotted(record, field.strip())
    return record


def flatten(d: Any, parent: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    if out is None:
        out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{parent}.{k}" if parent else k
            flatten(v, key, out)
    elif isinstance(d, list):
        out[parent] = json.dumps(d, ensure_ascii=False)
    else:
        out[parent] = d
    return out


def encode_domain(domain: str) -> str:
    if idna is not None:
        return idna.encode(domain).decode("utf-8")
    if not all(ord(c) < 128 for c in domain):
        sys.stderr.write(
            'Please install the "idna" Python package to query non-ASCII unicode domain names.\nExiting.\n'
        )
        sys.exit(3)
    return domain


def cache_path(cache_dir: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    return cache_dir / f"{key}.json"


def cache_get(cache_dir: Path, url: str, ttl: int) -> dict[str, Any] | None:
    if ttl <= 0:
        return None
    path = cache_path(cache_dir, url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - data.get("ts", 0) > ttl:
        return None
    cached = data.get("data")
    return cached if isinstance(cached, dict) else None


def cache_put(cache_dir: Path, url: str, data: dict[str, Any]) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path(cache_dir, url).write_text(json.dumps({"ts": time.time(), "data": data}))
    except OSError:
        pass


def default_cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path("~/.cache").expanduser()))
    return base / "bestwhois"


def load_config(path: Path | None) -> dict[str, Any]:
    if path is not None:
        candidates = [path]
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path("~/.config").expanduser()))
        candidates = [
            xdg / "bestwhois" / "config.toml",
            Path("~/.bestwhoisrc.toml").expanduser(),
        ]
    for c in candidates:
        if not c.exists():
            continue
        try:
            return tomllib.loads(c.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
    return {}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        content = path.read_text()
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def find_api_key(args: argparse.Namespace, profile: dict[str, Any]) -> tuple[str | None, str | None]:
    if args.apikey:
        return args.apikey, "--apikey"
    env_key = os.environ.get("WHOISXML_API_KEY")
    if env_key:
        return env_key, "env:WHOISXML_API_KEY"
    candidates: list[Path] = []
    if args.rcfile is not None:
        candidates.append(args.rcfile)
    env_rc = os.environ.get("BESTWHOIS_RC")
    if env_rc:
        candidates.append(Path(env_rc))
    candidates.extend(RC_FILE_LOCS)
    for rc in candidates:
        if not rc.exists():
            continue
        try:
            key = rc.read_text().strip()
        except OSError:
            continue
        if key:
            return key, f"rc:{rc}"
    cfg_key = profile.get("apikey")
    if cfg_key:
        return str(cfg_key), "config"
    return None, None


def build_url_for_target(target: str, mode: str, api_key: str, args: argparse.Namespace) -> tuple[str, bool]:
    if mode == "reverse":
        if ":" not in target:
            raise ValueError(f"--reverse expects TYPE:VALUE, got {target!r}")
        type_, value = target.split(":", 1)
        params: dict[str, Any] = {
            "apiKey": api_key,
            "searchType": "current",
            "mode": "purchase",
            "basicSearchTerms": json.dumps({type_: [value]}),
        }
        return f"{REVERSE_BASE}?{urlencode(params)}", False

    if mode == "ip":
        params = {"apiKey": api_key, "outputformat": "JSON", "ip": target}
        return f"{WHOIS_BASE}?{urlencode(params)}", False

    if mode == "dns":
        params = {"apiKey": api_key, "domainName": target, "type": "_all", "outputFormat": "JSON"}
        return f"{DNS_BASE}?{urlencode(params)}", False

    history = args.history or any((
        args.since_date,
        args.created_date_from,
        args.created_date_to,
        args.updated_date_from,
        args.updated_date_to,
        args.expired_date_from,
        args.expired_date_to,
    ))
    domain = encode_domain(target)
    if history:
        params = {"apiKey": api_key, "outputformat": "JSON", "mode": "purchase", "domainName": domain}
        if args.since_date:
            params["sinceDate"] = args.since_date
        if args.created_date_from:
            params["createdDateFrom"] = args.created_date_from
        if args.created_date_to:
            params["createdDateTo"] = args.created_date_to
        if args.updated_date_from:
            params["updatedDateFrom"] = args.updated_date_from
        if args.updated_date_to:
            params["updatedDateTo"] = args.updated_date_to
        if args.expired_date_from:
            params["expiredDateFrom"] = args.expired_date_from
        if args.expired_date_to:
            params["expiredDateTo"] = args.expired_date_to
        if args.history_limit is not None:
            params["maxRecords"] = args.history_limit
        if args.history_offset is not None:
            params["offset"] = args.history_offset
        if args.history_sort:
            params["sort"] = args.history_sort
        return f"{HISTORY_BASE}?{urlencode(params)}", True
    params = {"apiKey": api_key, "outputformat": "JSON", "ip": 1, "domainName": domain}
    return f"{WHOIS_BASE}?{urlencode(params)}", False


def fetch(
    url: str,
    *,
    timeout: float,
    retries: int,
    backoff: float,
    verify: bool | str,
    proxies: dict[str, str] | None,
    headers: dict[str, str] | None,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, verify=verify, proxies=proxies, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff * (2**attempt))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
                continue
            raise
    raise last_exc if last_exc else RuntimeError("fetch failed")


def postprocess_record(record: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if not args.keep_empty:
        record = purge_empty_fields(record, keep_null=args.keep_null, recurse=not args.no_purge_nested)

    if args.rawtext == "stripped":
        for field in TEXT_FIELDS:
            if isinstance(record.get(field), str):
                record[field] = record[field][:64] + "..."
        for value in record.values():
            if isinstance(value, dict):
                for field in TEXT_FIELDS:
                    if isinstance(value.get(field), str):
                        value[field] = value[field][:64] + "..."
    elif args.rawtext == "none":
        for field in TEXT_FIELDS:
            record.pop(field, None)
        for value in record.values():
            if isinstance(value, dict):
                for field in TEXT_FIELDS:
                    value.pop(field, None)

    if args.fields:
        record = select_fields(record, args.fields.split(","), None)
    if args.exclude_fields:
        record = select_fields(record, None, args.exclude_fields.split(","))

    return record


def parse_response(
    target: str,
    data: dict[str, Any],
    mode: str,
    is_history: bool,
    args: argparse.Namespace,
) -> QueryResult:
    if mode in ("ip", "reverse", "dns"):
        record = data if isinstance(data, dict) else {"value": data}
        return QueryResult(
            domain=target,
            records=[postprocess_record(record, args)],
            record_count=1,
            is_history=False,
        )

    if is_history:
        count = int(data.get("recordsCount", 0) or 0)
        if count == 0:
            return QueryResult(domain=target, records=[], record_count=0, is_history=True, error="no history records")
        records = [postprocess_record(r, args) for r in data.get("records", [])]
        return QueryResult(domain=target, records=records, record_count=count, is_history=True)

    raw = data.get("WhoisRecord")
    if raw is None:
        return QueryResult(
            domain=target, records=[], record_count=0, is_history=False, error="no WhoisRecord in response"
        )
    return QueryResult(
        domain=target,
        records=[postprocess_record(raw, args)],
        record_count=1,
        is_history=False,
    )


def query_one(
    target: str,
    mode: str,
    api_key: str,
    args: argparse.Namespace,
    *,
    timeout: float,
    retries: int,
    retry_backoff: float,
    verify: bool | str,
    proxies: dict[str, str] | None,
    headers: dict[str, str] | None,
    cache_dir: Path | None,
    cache_ttl: int,
    rate_limiter: RateLimiter | None,
) -> QueryResult:
    url, is_history = build_url_for_target(target, mode, api_key, args)
    if args.verbose:
        sys.stderr.write(f"[verbose] GET {redact_key(url)}\n")
    if cache_dir is not None and cache_ttl > 0:
        cached = cache_get(cache_dir, url, cache_ttl)
        if cached is not None:
            if args.verbose:
                sys.stderr.write("[verbose] cache hit\n")
            return parse_response(target, cached, mode, is_history, args)
    if rate_limiter is not None:
        rate_limiter.wait()
    started = time.monotonic()
    r = fetch(
        url, timeout=timeout, retries=retries, backoff=retry_backoff, verify=verify, proxies=proxies, headers=headers
    )
    if args.verbose:
        sys.stderr.write(
            f"[verbose] HTTP {r.status_code} in {time.monotonic() - started:.2f}s ({len(r.content)} bytes)\n"
        )
    if args.trace:
        sys.stderr.write(f"[trace] url={redact_key(url)}\n")
        for k, v in r.headers.items():
            sys.stderr.write(f"[trace] response-header: {k}: {v}\n")
    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError(f"invalid JSON response from API: {exc}") from exc
    if cache_dir is not None and cache_ttl > 0:
        cache_put(cache_dir, url, data)
    return parse_response(target, data, mode, is_history, args)


def diff_records(old: Any, new: Any, prefix: str = "") -> list[str]:
    changes: list[str] = []
    if type(old) is not type(new):
        return [f"~ {prefix}: type {type(old).__name__} -> {type(new).__name__}"]
    if isinstance(old, dict) and isinstance(new, dict):
        for k in old:
            path = f"{prefix}.{k}" if prefix else k
            if k not in new:
                changes.append(f"- {path}")
            else:
                changes.extend(diff_records(old[k], new[k], path))
        for k in new:
            if k not in old:
                path = f"{prefix}.{k}" if prefix else k
                changes.append(f"+ {path}")
        return changes
    if isinstance(old, list) and isinstance(new, list):
        if old != new:
            changes.append(f"~ {prefix}")
        return changes
    if old != new:
        changes.append(f"~ {prefix}: {old!r} -> {new!r}")
    return changes


def resolve_color(mode: str) -> bool:
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if sys.platform == "win32":
        return False
    return sys.stdout.isatty()


def wrap_output(text: str, width: int) -> str:
    if width <= 0:
        return text
    return "\n".join(textwrap.fill(line, width) if line else line for line in text.splitlines())


def render_json(record: dict[str, Any], args: argparse.Namespace, color_on: bool, theme: str) -> str:
    indent: int | None = 1
    separators: tuple[str, str] | None = None
    if args.compact:
        indent = None
        separators = (",", ":")
    elif args.indent is not None:
        indent = args.indent
    s = json.dumps(record, indent=indent, sort_keys=args.sort_keys, ensure_ascii=False, separators=separators)
    if color_on and not args.json:
        return highlight(s, JsonLexer(), TerminalFormatter(style=theme))
    return s


def render_record(record: dict[str, Any], args: argparse.Namespace, color_on: bool, theme: str) -> str:
    if args.raw_whois:
        return str(record.get("rawText", ""))
    if args.text:
        s = format_text(record)
    elif args.yaml:
        if yaml is None:
            raise RuntimeError("PyYAML is not installed; install with `pip install pyyaml`")
        s = yaml.safe_dump(record, sort_keys=args.sort_keys, allow_unicode=True)
    elif args.jsonpath:
        if jsonpath_parse is None:
            raise RuntimeError("jsonpath-ng is not installed; install with `pip install jsonpath-ng`")
        matches = [m.value for m in jsonpath_parse(args.jsonpath).find(record)]
        payload: Any = matches[0] if len(matches) == 1 else matches
        s = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=args.sort_keys)
    else:
        s = render_json(record, args, color_on, theme)
    if (args.text or args.raw_whois) and args.wrap:
        s = wrap_output(s, args.wrap)
    return s


def format_single_result(result: QueryResult, args: argparse.Namespace, color_on: bool, theme: str) -> str:
    if result.error and not result.records:
        if not args.quiet:
            return f"# {result.domain}: {result.error}"
        return ""
    parts: list[str] = []
    for i, record in enumerate(result.records, 1):
        if result.is_history and not args.no_header:
            parts.append(f"Historic record no. {i} of {result.record_count} for {result.domain}:\n------------\n")
        parts.append(render_record(record, args, color_on, theme))
    return "\n".join(parts)


def write_csv_results(results: list[QueryResult], stream: Any) -> None:
    rows: list[dict[str, Any]] = []
    for result in results:
        for record in result.records:
            row = flatten(record)
            row["domain"] = result.domain
            rows.append(row)
    if not rows:
        return
    fieldnames = ["domain"]
    seen = {"domain"}
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def write_output(results: list[QueryResult], args: argparse.Namespace, color_on: bool, theme: str) -> None:
    if args.output and args.output != "-":
        with open(args.output, "w", encoding="utf-8") as f:
            _write_to_stream(results, args, color_on, theme, f)
        return
    if args.pager and sys.stdout.isatty() and sys.platform != "win32" and not args.csv:
        try:
            proc = subprocess.Popen(os.environ.get("PAGER", "less -R"), shell=True, stdin=subprocess.PIPE, text=True)
            try:
                _write_to_stream(results, args, color_on, theme, proc.stdin)
            finally:
                proc.stdin.close()  # type: ignore[union-attr]
                proc.wait()
            return
        except OSError:
            pass
    _write_to_stream(results, args, color_on, theme, sys.stdout)


def _write_to_stream(
    results: list[QueryResult],
    args: argparse.Namespace,
    color_on: bool,
    theme: str,
    stream: Any,
) -> None:
    if args.csv:
        write_csv_results(results, stream)
        stream.flush()
        return
    if args.batch or len(results) > 1:
        for result in results:
            if result.error:
                if not args.quiet:
                    stream.write(
                        json.dumps({"domain": result.domain, "error": result.error}, ensure_ascii=False) + "\n"
                    )
                continue
            for record in result.records:
                if args.csv:
                    continue
                if args.text:
                    stream.write(format_single_result(result, args, color_on, theme) + "\n")
                    break
                payload = {"domain": result.domain, "record": record}
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stream.flush()
        return
    parts = [format_single_result(r, args, color_on, theme) for r in results]
    text = "\n".join(p for p in parts if p)
    if text and not text.endswith("\n"):
        text += "\n"
    stream.write(text)
    stream.flush()


def collect_domains(args: argparse.Namespace) -> list[str]:
    domains: list[str] = []
    if args.domainName:
        name = args.domainName
        if name.startswith("@") and len(name) > 1:
            domains.extend(read_domains_file(Path(name[1:])))
        elif name == "-":
            domains.extend(read_domains_stdin())
        else:
            domains.append(name)
    if args.file:
        domains.extend(read_domains_file(args.file))
    if args.stdin and args.domainName != "-":
        domains.extend(read_domains_stdin())
    return list(dict.fromkeys(domains))


def read_domains_file(path: Path) -> list[str]:
    try:
        content = path.read_text()
    except OSError as exc:
        sys.stderr.write(f"cannot read {path}: {exc}\n")
        return []
    return [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]


def read_domains_stdin() -> list[str]:
    return [line.strip() for line in sys.stdin.read().splitlines() if line.strip() and not line.startswith("#")]


def execute_targets(
    targets: list[str],
    mode: str,
    api_key: str,
    args: argparse.Namespace,
    *,
    parallel: int,
    timeout: float,
    retries: int,
    retry_backoff: float,
    verify: bool | str,
    proxies: dict[str, str] | None,
    headers: dict[str, str] | None,
    cache_dir: Path | None,
    cache_ttl: int,
    rate_limiter: RateLimiter | None,
) -> list[QueryResult]:
    def run(target: str) -> QueryResult:
        try:
            return query_one(
                target,
                mode,
                api_key,
                args,
                timeout=timeout,
                retries=retries,
                retry_backoff=retry_backoff,
                verify=verify,
                proxies=proxies,
                headers=headers,
                cache_dir=cache_dir,
                cache_ttl=cache_ttl,
                rate_limiter=rate_limiter,
            )
        except Exception as exc:
            if args.debug:
                traceback.print_exc()
            return QueryResult(domain=target, records=[], record_count=0, is_history=False, error=str(exc))

    results: list[QueryResult] = []
    if parallel > 1 and len(targets) > 1:
        indexed: dict[int, QueryResult] = {}
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(run, t): i for i, t in enumerate(targets)}
            for fut in as_completed(futures):
                idx = futures[fut]
                indexed[idx] = fut.result()
                if indexed[idx].error and args.stop_on_error:
                    for pending in futures:
                        pending.cancel()
                    break
        for i in range(len(targets)):
            if i in indexed:
                results.append(indexed[i])
            elif args.stop_on_error and results and results[-1].error:
                break
        return results

    for target in targets:
        result = run(target)
        results.append(result)
        if result.error and args.stop_on_error:
            break
    return results


def run_check_key(api_key: str, args: argparse.Namespace) -> int:
    url = f"{ACCOUNT_BASE}/service/account-balance?apiKey={api_key}"
    verify: bool | str = False if args.no_verify else (args.ca_bundle if args.ca_bundle else True)
    try:
        r = requests.get(url, timeout=args.timeout or 30, verify=verify)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        sys.stderr.write(f"check failed: {exc}\n")
        return 1
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def print_config(
    args: argparse.Namespace, profile: dict[str, Any], api_key: str | None, key_source: str | None
) -> None:
    redacted = "***" if not api_key else ("***" + api_key[-4:] if len(api_key) > 4 else "***")
    payload = {
        "version": VERSION,
        "profile": args.profile,
        "apikey": redacted,
        "apikey_source": key_source,
        "cli": {
            "config": str(args.config) if args.config else None,
            "rcfile": str(args.rcfile) if args.rcfile else None,
        },
        "settings": {
            "timeout": args.timeout,
            "retries": args.retries,
            "retry_backoff": args.retry_backoff,
            "color": args.color,
            "theme": args.theme,
            "cache_dir": str(args.cache_dir) if args.cache_dir else None,
            "cache_ttl": args.cache_ttl,
            "no_cache": args.no_cache,
            "parallel": args.parallel,
            "rate_limit": args.rate_limit,
            "output": args.output,
            "pager": args.pager,
            "rawtext": args.rawtext,
        },
        "profile_table": profile,
    }
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))


def version_full() -> str:
    return f"{MYNAME} {VERSION}\nPython {platform.python_version()}\n{platform.platform()}\n(c) WhoisXML API Inc."


def generate_manpage(parser: argparse.ArgumentParser) -> str:
    lines = [
        f".TH {parser.prog.upper()} 1",
        ".SH NAME",
        f"{parser.prog} \\- {parser.description or ''}",
        ".SH SYNOPSIS",
        f".B {parser.prog}",
        "[options] [domainName]",
        ".SH OPTIONS",
    ]
    for action in parser._actions:  # noqa: SLF001
        if not action.option_strings:
            continue
        opts = ", ".join(action.option_strings)
        help_text = (action.help or "").replace("\n", " ")
        lines.extend((".TP", f".B {opts}", help_text))
    return "\n".join(lines)


def generate_completion(parser: argparse.ArgumentParser, shell: str) -> str:
    options = [opt for action in parser._actions for opt in action.option_strings]  # noqa: SLF001
    joined = " ".join(options)
    if shell == "bash":
        return (
            f"_{parser.prog}_completion() {{\n"
            f'    local cur="${{COMP_WORDS[COMP_CWORD]}}"\n'
            f'    COMPREPLY=( $(compgen -W "{joined}" -- "$cur") )\n'
            f"}}\n"
            f"complete -F _{parser.prog}_completion {parser.prog}\n"
        )
    if shell == "zsh":
        return (
            f"#compdef {parser.prog}\n"
            f"_{parser.prog}() {{\n"
            f"    compadd -- {joined}\n"
            f"}}\n"
            f"compdef _{parser.prog} {parser.prog}\n"
        )
    if shell == "fish":
        return (
            "\n".join(
                f"complete -c {parser.prog} -l {opt.lstrip('-').replace('-', '_')}"
                for opt in options
                if opt.startswith("--")
            )
            + "\n"
        )
    return ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=MYNAME,
        description="Command-line utility to query domains in the WhoisXML API WHOIS service similarly to the whois command.",
    )
    p.add_argument(
        "domainName", nargs="?", help="The domain to be queried (or @FILE, or - for stdin). Unicode/IDN accepted."
    )
    p.add_argument("--version", action="version", version=f"{MYNAME} ver. {VERSION}\n    (c) WhoisXML API Inc.")
    p.add_argument(
        "--version-full", action="store_true", help="Print version, Python version, and platform, then exit."
    )
    p.add_argument("--changelog", action="store_true", help="Print changelog and exit.")
    p.add_argument("--rcfile", type=Path, help="Use this rc file (plain text API key). Overrides default rc locations.")
    p.add_argument("--config", type=Path, help="Use this TOML config file.")
    p.add_argument("--profile", help="Named section in the TOML config to use.")
    p.add_argument("--dotenv", type=Path, help="Load environment from this .env file.")
    p.add_argument("--apikey", help="Directly specify the API key. Overrides any other source.")
    p.add_argument("--print-key-source", action="store_true", help="Print which source the API key came from.")
    p.add_argument("--print-config", action="store_true", help="Print resolved configuration and exit.")
    p.add_argument("--check-key", action="store_true", help="Check API key and print account balance, then exit.")
    p.add_argument("--balance", action="store_true", help="Alias for --check-key.")
    p.add_argument("--file", type=Path, help="Read domains from file (one per line, '#' comments allowed).")
    p.add_argument("--stdin", action="store_true", help="Read domains from standard input.")
    p.add_argument(
        "--batch", action="store_true", help="Emit one NDJSON object per domain (default for multi-domain inputs)."
    )
    p.add_argument(
        "--parallel", type=int, default=None, metavar="N", help="Process N domains concurrently (default 1)."
    )
    p.add_argument("--rate-limit", type=float, default=None, metavar="N", help="Global requests-per-second limit.")
    p.add_argument("--stop-on-error", action="store_true", help="Stop processing on the first error.")
    p.add_argument("--skip-empty", action="store_true", help="Skip records that resolve to empty results.")
    p.add_argument("--ip", metavar="ADDRESS", help="Query an IP address instead of a domain.")
    p.add_argument("--dns", action="store_true", help="Query the DNS API instead of WHOIS.")
    p.add_argument(
        "--reverse",
        metavar="TYPE:VALUE",
        help='Reverse WHOIS lookup, e.g. email:user@example.com or org:"Example Inc".',
    )
    p.add_argument(
        "--history", action="store_true", help="Get historic records from the history API. Implied by date filters."
    )
    p.add_argument(
        "--since-date",
        type=valid_date,
        metavar="YYYY-MM-DD",
        help="(history) Search activities discovered since this date.",
    )
    p.add_argument(
        "--created-date-from", type=valid_date, metavar="YYYY-MM-DD", help="(history) Domains created after this date."
    )
    p.add_argument(
        "--created-date-to", type=valid_date, metavar="YYYY-MM-DD", help="(history) Domains created before this date."
    )
    p.add_argument(
        "--updated-date-from", type=valid_date, metavar="YYYY-MM-DD", help="(history) Domains updated after this date."
    )
    p.add_argument(
        "--updated-date-to", type=valid_date, metavar="YYYY-MM-DD", help="(history) Domains updated before this date."
    )
    p.add_argument(
        "--expired-date-from", type=valid_date, metavar="YYYY-MM-DD", help="(history) Domains expired after this date."
    )
    p.add_argument(
        "--expired-date-to", type=valid_date, metavar="YYYY-MM-DD", help="(history) Domains expired before this date."
    )
    p.add_argument("--history-limit", type=int, metavar="N", help="(history) Maximum number of records to return.")
    p.add_argument("--history-offset", type=int, metavar="N", help="(history) Pagination offset.")
    p.add_argument("--history-sort", metavar="FIELD", help="(history) Sort field if supported by the API.")

    raw_group = p.add_mutually_exclusive_group()
    raw_group.add_argument(
        "--rawtext",
        choices=["none", "stripped", "full"],
        default=None,
        help="Raw-text handling: none (default), stripped (first 64 chars), full.",
    )
    raw_group.add_argument("--fullrawtext", action="store_true", help=argparse.SUPPRESS)
    raw_group.add_argument("--strippedrawtext", action="store_true", help=argparse.SUPPRESS)

    p.add_argument("--keep-empty", action="store_true", help="Keep and display fields with empty and null values.")
    p.add_argument(
        "--keep-null", action="store_true", help="Keep null fields while still removing empty strings/collections."
    )
    p.add_argument(
        "--no-purge-nested", action="store_true", help="Only purge empty fields at the top level; do not recurse."
    )
    p.add_argument("--text", action="store_true", help="Print a textual output instead of JSON.")
    p.add_argument("--json", action="store_true", help="Force machine-readable JSON (disables coloring).")
    p.add_argument("--yaml", action="store_true", help="Print YAML instead of JSON.")
    p.add_argument("--csv", action="store_true", help="Print CSV (flattened records, one row each).")
    p.add_argument("--raw-whois", action="store_true", help="Print only the raw WHOIS text.")
    p.add_argument("--jsonpath", metavar="EXPR", help="Select a subset of the record using a JSONPath expression.")
    p.add_argument("--fields", metavar="A,B.C,...", help="Only output the given dotted-path fields.")
    p.add_argument("--exclude-fields", metavar="A,B.C,...", help="Drop the given dotted-path fields.")
    p.add_argument("--compact", action="store_true", help="Compact JSON output.")
    p.add_argument("--indent", type=int, default=None, metavar="N", help="JSON indent (default 1).")
    p.add_argument("--sort-keys", action="store_true", help="Sort JSON keys.")
    p.add_argument("--theme", default=None, metavar="NAME", help="Pygments style name (e.g. monokai, dracula).")
    p.add_argument("--nocolor", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto", help="Colorize output.")
    p.add_argument("--no-header", action="store_true", help="Suppress the historic-record header.")
    p.add_argument("--quiet", "-q", action="store_true", help="Suppress informational output.")
    p.add_argument("--verbose", "-v", action="store_true", help="Print verbose info to stderr.")
    p.add_argument("--debug", action="store_true", help="Print full tracebacks on error.")
    p.add_argument("--trace", action="store_true", help="Print HTTP request/response details to stderr.")
    p.add_argument("--wrap", type=int, default=0, metavar="N", help="Wrap text/raw output at N columns (0 = no wrap).")
    p.add_argument("--pager", "-P", action="store_true", help="Pipe output through $PAGER when stdout is a tty.")
    p.add_argument("--output", "-o", metavar="FILE", help="Write output to FILE ('-' for stdout).")
    p.add_argument("--timeout", type=float, default=None, metavar="SECS", help="Per-request timeout (default 30s).")
    p.add_argument(
        "--retries", type=int, default=None, metavar="N", help="Retry count for transient failures (default 0)."
    )
    p.add_argument(
        "--retry-backoff", type=float, default=None, metavar="SECS", help="Base delay between retries (default 0.5s)."
    )
    p.add_argument("--no-verify", action="store_true", help="Disable TLS certificate verification.")
    p.add_argument("--ca-bundle", metavar="PATH", help="Custom CA bundle for TLS verification.")
    p.add_argument("--user-agent", metavar="UA", help="Override the HTTP User-Agent header.")
    p.add_argument("--proxy", metavar="URL", help="HTTP(S) proxy to use for requests.")
    p.add_argument("--dry-run", action="store_true", help="Print effective URLs (API key redacted) and exit.")
    p.add_argument("--cache-dir", type=Path, help="Cache directory (default: XDG cache).")
    p.add_argument("--no-cache", action="store_true", help="Disable response caching.")
    p.add_argument("--cache-ttl", type=int, default=None, metavar="SECS", help="Cache time-to-live (default 0 = off).")
    p.add_argument(
        "--reconcile",
        type=Path,
        metavar="PATH",
        help="Compare results against a saved JSON file and report changes on stderr.",
    )
    p.add_argument(
        "--shell-completion", choices=["bash", "zsh", "fish"], help="Print shell completion script and exit."
    )
    p.add_argument("--manpage", action="store_true", help="Print a manpage and exit.")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.nocolor:
        args.color = "never"
    if args.rawtext is None:
        if args.fullrawtext:
            args.rawtext = "full"
        elif args.strippedrawtext:
            args.rawtext = "stripped"
        else:
            args.rawtext = "none"
    if args.quiet:
        args.no_header = True
    if args.balance:
        args.check_key = True

    if args.version_full:
        print(version_full())
        return 0
    if args.changelog:
        print(CHANGELOG)
        return 0
    if args.shell_completion:
        print(generate_completion(parser, args.shell_completion))
        return 0
    if args.manpage:
        print(generate_manpage(parser))
        return 0

    config = load_config(args.config)
    profile = config.get(args.profile, {}) if args.profile else config

    if args.dotenv:
        load_dotenv(args.dotenv)

    api_key, key_source = find_api_key(args, profile)

    if args.print_config:
        print_config(args, profile, api_key, key_source)
        return 0

    if api_key is None:
        sys.stderr.write(
            "No API key found. Check rc files, TOML config, env var WHOISXML_API_KEY, or specify --apikey.\n"
        )
        return 1

    if args.print_key_source:
        print(f"API key source: {key_source}")

    if args.check_key:
        return run_check_key(api_key, args)

    if args.reverse:
        targets = [args.reverse]
        mode = "reverse"
    elif args.ip:
        targets = [args.ip]
        mode = "ip"
    else:
        domains = collect_domains(args)
        if not domains:
            parser.error("domainName is required (or use --file, --stdin, --ip, --reverse)")
        targets = domains
        mode = "dns" if args.dns else "whois"

    timeout = args.timeout if args.timeout is not None else float(profile.get("timeout", 30.0))
    retries = args.retries if args.retries is not None else int(profile.get("retries", 0))
    retry_backoff = args.retry_backoff if args.retry_backoff is not None else float(profile.get("retry_backoff", 0.5))
    theme = args.theme if args.theme is not None else str(profile.get("theme", "monokai"))
    parallel = args.parallel if args.parallel is not None else int(profile.get("parallel", 1))
    rate_limit = args.rate_limit if args.rate_limit is not None else float(profile.get("rate_limit", 0.0))
    cache_ttl = args.cache_ttl if args.cache_ttl is not None else int(profile.get("cache_ttl", 0))

    color_on = resolve_color(args.color)

    if args.no_verify:
        verify: bool | str = False
    elif args.ca_bundle:
        verify = args.ca_bundle
    else:
        verify = True

    headers: dict[str, str] = {}
    user_agent = args.user_agent or profile.get("user_agent")
    if user_agent:
        headers["User-Agent"] = str(user_agent)

    proxies: dict[str, str] | None = None
    if args.proxy:
        proxies = {"http": args.proxy, "https": args.proxy}

    cache_dir: Path | None = None
    if not args.no_cache:
        cache_dir = args.cache_dir or default_cache_dir()

    rate_limiter = RateLimiter(rate_limit) if rate_limit > 0 else None

    if args.dry_run:
        for target in targets:
            try:
                url, _ = build_url_for_target(target, mode, api_key, args)
            except (ValueError, UnicodeError) as exc:
                sys.stderr.write(f"{target}: {exc}\n")
                continue
            print(redact_key(url))
        return 0

    results = execute_targets(
        targets,
        mode,
        api_key,
        args,
        parallel=parallel,
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
        verify=verify,
        proxies=proxies,
        headers=headers,
        cache_dir=cache_dir,
        cache_ttl=cache_ttl,
        rate_limiter=rate_limiter,
    )

    if args.reconcile:
        try:
            old = json.loads(args.reconcile.read_text())
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"reconcile: cannot read {args.reconcile}: {exc}\n")
        else:
            for result in results:
                for record in result.records:
                    changes = diff_records(old, record)
                    if changes:
                        sys.stderr.write(f"Reconcile changes for {result.domain}:\n")
                        for change in changes:
                            sys.stderr.write(f"  {change}\n")
                    elif not args.quiet:
                        sys.stderr.write(f"Reconcile: no changes for {result.domain}\n")

    write_output(results, args, color_on, theme)

    return 1 if any(r.error for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
