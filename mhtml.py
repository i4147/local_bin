#!/data/data/com.termux/files/usr/bin/env python
"""Standalone, single-file MHTML-to-HTML converter.

The implementation is consolidated from the ``converter`` package. Invoke it as
``python mhtml_converter_consolidated.py input.mhtml`` to write ``input.html``
next to the source file. See the README for dependencies and API usage.
"""

import argparse
import base64
import binascii
import codecs
import hashlib
import html as html_stdlib
import ipaddress
import json
import mimetypes
import quopri
import re
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, fields
from email import policy
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from enum import Enum
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, BinaryIO, ClassVar, Self, cast
from urllib.parse import urlsplit

import tinycss2
from loguru import logger
from lxml import etree
from lxml import html as lxml_html
from lxml_html_clean import Cleaner
from tinycss2.ast import StringToken


class ConversionProfile(str, Enum):
    """Built-in conversion presets."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"
    SECURE = "secure"
    OPTIMIZED = "optimized"


ResourceHandler = Callable[["Resource"], "Resource | bytes | str | None"]
ProgressCallback = Callable[[str, float], None]


@dataclass(slots=True)
class ConverterConfig:
    """Options controlling parsing, rewriting, fetching, and output.

    External fetching is disabled by default. Enable it only when converting
    trusted documents; even with it enabled, only public HTTP(S) resources are
    fetched and optional domain allow/deny lists are enforced.
    """

    profile: ConversionProfile = ConversionProfile.STANDARD
    output_format: str = "html5"
    encoding: str = "utf-8"
    resource_handling: str = "inline"
    max_resource_size: int = 10 * 1024 * 1024
    max_input_size: int = 128 * 1024 * 1024
    fetch_external: bool = False
    fetch_timeout: float = 10.0
    max_fetch_retries: int = 2
    include_scripts: bool = True
    include_styles: bool = True
    minify_html: bool = False
    minify_css: bool = False
    compress_output: bool = False
    sanitize_html: bool = True
    remove_scripts: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    parallel_processing: bool = True
    max_workers: int = 4
    streaming_output: bool = False
    chunk_size: int = 64 * 1024
    custom_handlers: dict[str, ResourceHandler] = field(default_factory=dict)
    plugins: list[ConverterPlugin] = field(default_factory=list)
    metadata_extraction: bool = True
    progress_callback: ProgressCallback | None = None
    error_recovery: bool = True
    validation: bool = True
    strip_css_comments: bool = True
    max_import_depth: int = 10
    cache_enabled: bool = True
    cache_size: int = 100
    cache_dir: Path | None = None
    debug_mode: bool = False
    log_level: str = "INFO"
    log_file: Path | None = None

    def __post_init__(self) -> None:
        """Normalize user-friendly values, apply presets, and validate options."""
        if isinstance(self.profile, str):
            self.profile = ConversionProfile(self.profile.lower())
        if self.profile is not ConversionProfile.STANDARD:
            self.apply_profile(self.profile)
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)
        if self.log_file is not None:
            self.log_file = Path(self.log_file)
        self.allowed_domains = [domain.strip().lower().strip(".") for domain in self.allowed_domains]
        self.blocked_domains = [domain.strip().lower().strip(".") for domain in self.blocked_domains]
        self.validate()

    def apply_profile(self, profile: ConversionProfile) -> None:
        """Apply the named preset; callers may customize fields afterward."""
        if isinstance(profile, str):
            profile = ConversionProfile(profile.lower())
        self.profile = profile

        shared = {
            "remove_scripts": False,
            "include_scripts": True,
            "include_styles": True,
            "sanitize_html": True,
            "resource_handling": "inline",
            "parallel_processing": True,
            "debug_mode": False,
        }
        presets: dict[ConversionProfile, dict[str, Any]] = {
            ConversionProfile.MINIMAL: {
                **shared,
                "minify_html": True,
                "minify_css": True,
                "compress_output": True,
                "fetch_external": False,
                "include_scripts": False,
                "include_styles": False,
                "max_workers": 1,
                "log_level": "WARNING",
            },
            ConversionProfile.STANDARD: {
                **shared,
                "minify_html": False,
                "minify_css": False,
                "compress_output": False,
                "fetch_external": False,
                "max_workers": 4,
                "log_level": "INFO",
            },
            ConversionProfile.FULL: {
                **shared,
                "minify_html": False,
                "minify_css": False,
                "compress_output": False,
                "fetch_external": True,
                "resource_handling": "mixed",
                "max_workers": 8,
                "debug_mode": True,
                "log_level": "DEBUG",
            },
            ConversionProfile.SECURE: {
                **shared,
                "minify_html": True,
                "minify_css": True,
                "compress_output": True,
                "fetch_external": False,
                "remove_scripts": True,
                "include_scripts": False,
                "include_styles": False,
                "parallel_processing": False,
                "max_workers": 1,
                "log_level": "WARNING",
            },
            ConversionProfile.OPTIMIZED: {
                **shared,
                "minify_html": True,
                "minify_css": True,
                "compress_output": True,
                "fetch_external": False,
                "max_workers": 8,
                "log_level": "INFO",
            },
        }
        for name, value in presets[profile].items():
            setattr(self, name, value)

    def validate(self) -> None:
        """Raise ``ValueError`` when a setting is invalid or unsafe."""
        if self.output_format not in {"html5", "xhtml"}:
            raise ValueError("output_format must be 'html5' or 'xhtml'")
        if self.resource_handling not in {"inline", "external", "mixed"}:
            raise ValueError("resource_handling must be 'inline', 'external', or 'mixed'")
        try:
            codecs.lookup(self.encoding)
        except LookupError as error:
            raise ValueError(f"Unknown output encoding: {self.encoding}") from error
        if self.max_resource_size <= 0 or self.max_input_size <= 0:
            raise ValueError("max_resource_size and max_input_size must be positive")
        if self.fetch_timeout <= 0:
            raise ValueError("fetch_timeout must be positive")
        if self.max_fetch_retries < 0:
            raise ValueError("max_fetch_retries cannot be negative")
        if self.max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if self.max_import_depth < 0:
            raise ValueError("max_import_depth cannot be negative")
        if self.cache_size < 0:
            raise ValueError("cache_size cannot be negative")
        if self.log_level.upper() not in {
            "TRACE",
            "DEBUG",
            "INFO",
            "SUCCESS",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ValueError(f"Unsupported log level: {self.log_level}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of configuration settings."""
        values: dict[str, Any] = {config_field.name: getattr(self, config_field.name) for config_field in fields(self)}
        values["profile"] = self.profile.value
        values["allowed_domains"] = list(self.allowed_domains)
        values["blocked_domains"] = list(self.blocked_domains)
        values["cache_dir"] = str(self.cache_dir) if self.cache_dir else None
        values["log_file"] = str(self.log_file) if self.log_file else None
        values["custom_handlers"] = sorted(self.custom_handlers)
        values["plugins"] = [type(plugin).__name__ for plugin in self.plugins]
        values["progress_callback"] = self.progress_callback is not None
        return values


# ===== Consolidated from converter/logger.py =====


class ConverterLogger:
    """Scoped logging facade that does not reset the application's Loguru sinks."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Bind a component logger and optionally add a rotating file sink."""
        self.config = config or ConverterConfig()
        self.progress_callback: Callable[[str, float], None] | None = self.config.progress_callback
        self._logger = logger.bind(component="mhtml.converter")
        self._minimum_level = logger.level(self.config.log_level.upper()).no
        self._file_sink: int | None = None
        if self.config.log_file is not None:
            self.config.log_file.parent.mkdir(parents=True, exist_ok=True)
            self._file_sink = logger.add(
                self.config.log_file,
                level=self.config.log_level.upper(),
                rotation="10 MB",
                retention=3,
                enqueue=True,
                format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
            )

    def set_progress_callback(
        self,
        callback: Callable[[str, float], None] | None,
    ) -> None:
        """Set or clear the callback invoked at progress milestones."""
        self.progress_callback = callback

    def log_progress(self, message: str, progress: float) -> None:
        """Log progress and invoke the optional callback with a clamped value."""
        clamped_progress = min(1.0, max(0.0, progress))
        self.info("Progress: {:.1f}% - {}", clamped_progress * 100, message)
        if self.progress_callback is not None:
            try:
                self.progress_callback(message, clamped_progress)
            # User callback exceptions must not abort the conversion pipeline.
            except Exception:  # pylint: disable=broad-exception-caught
                self._logger.exception("Progress callback failed")

    def trace(self, message: str, *args: object) -> None:
        """Log a trace-level message when enabled by the configured threshold."""
        self._log("TRACE", message, *args)

    def debug(self, message: str, *args: object) -> None:
        """Log a debug message when enabled by the configured threshold."""
        self._log("DEBUG", message, *args)

    def info(self, message: str, *args: object) -> None:
        """Log an informational message."""
        self._log("INFO", message, *args)

    def warning(self, message: str, *args: object) -> None:
        """Log a warning message."""
        self._log("WARNING", message, *args)

    def error(self, message: str, *args: object) -> None:
        """Log an error message."""
        self._log("ERROR", message, *args)

    def exception(self, message: str, *args: object) -> None:
        """Log an error with the active exception traceback."""
        if self._minimum_level <= logger.level("ERROR").no:
            self._logger.exception(message, *args)

    def close(self) -> None:
        """Remove this instance's optional Loguru file sink."""
        if self._file_sink is not None:
            logger.remove(self._file_sink)
            self._file_sink = None

    def _log(self, level: str, message: str, *args: object) -> None:
        if logger.level(level).no >= self._minimum_level:
            self._logger.log(level, message, *args)


# ===== Consolidated from converter/parser.py =====


class ResourceType(str, Enum):
    """High-level category of an extracted MIME resource."""

    HTML = "html"
    CSS = "css"
    IMAGE = "image"
    SCRIPT = "script"
    FONT = "font"
    OTHER = "other"


def classify_resource_type(content_type: str) -> ResourceType:
    """Map a MIME content type to the converter's resource categories."""
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if media_type in {"text/html", "text/htm", "application/xhtml+xml"}:
        return ResourceType.HTML
    if media_type == "text/css":
        return ResourceType.CSS
    if media_type in {
        "application/javascript",
        "application/x-javascript",
        "text/javascript",
        "text/ecmascript",
        "application/ecmascript",
    }:
        return ResourceType.SCRIPT
    if media_type.startswith("image/"):
        return ResourceType.IMAGE
    if media_type.startswith("font/") or media_type in {
        "application/font-woff",
        "application/font-woff2",
        "application/vnd.ms-fontobject",
    }:
        return ResourceType.FONT
    return ResourceType.OTHER


def resource_to_data_uri(resource: Resource) -> str:
    """Encode a resource as a base64 data URI using its MIME media type."""
    content = resource.content if isinstance(resource.content, bytes) else resource.content.encode("utf-8")
    media_type = resource.content_type.split(";", maxsplit=1)[0].strip()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


# A resource carries MIME, URL, content, and lookup metadata by design.
# pylint: disable=too-many-instance-attributes
@dataclass(slots=True)
class Resource:
    """A MIME part and the URL by which HTML/CSS refers to it."""

    url: str
    content_type: str
    content: bytes | str
    resource_type: ResourceType
    encoding: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    original_url: str | None = None
    size: int = 0

    def __post_init__(self) -> None:
        """Compute byte size and ensure each resource owns its header mapping."""
        if isinstance(self.content, bytes):
            self.size = len(self.content)
        else:
            self.size = len(self.content.encode("utf-8"))
        if self.original_url is None:
            self.original_url = self.url


def resolve_resource_url(
    url: str,
    base_url: str,
    resource_map: dict[str, Resource],
    config: ConverterConfig,
) -> str:
    """Normalize an asset URL and inline a mapped resource when configured."""
    value = url.strip()
    if not value or value.lower().startswith("data:"):
        return value
    normalized = urllib.parse.urldefrag(urllib.parse.urljoin(base_url, value))[0]
    resource = resource_map.get(normalized) or resource_map.get(value)
    if (
        resource is not None
        and config.resource_handling in {"inline", "mixed"}
        and resource.size <= config.max_resource_size
    ):
        return resource_to_data_uri(resource)
    return normalized


class MHTMLParser:
    """Parse MHTML data using Python's MIME parser plus compatibility helpers."""

    BOUNDARY_RE = re.compile(
        r"boundary\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))",
        re.IGNORECASE,
    )
    CHARSET_RE = re.compile(r"charset\s*=\s*['\"]?([^\s;'\"]+)", re.IGNORECASE)
    HTML_URL_RE = re.compile(
        r"\b(?:src|href|poster|background|action|data|xlink:href)\s*=\s*" r"(?P<quote>[\"'])(?P<url>.*?)(?P=quote)",
        re.IGNORECASE | re.DOTALL,
    )
    CSS_URL_RE = re.compile(
        r"url\(\s*(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|(?P<plain>[^)]*?))\s*\)",
        re.IGNORECASE | re.DOTALL,
    )
    CSS_IMPORT_RE = re.compile(
        r"@import\s+(?:url\(\s*)?(?:\"([^\"]*)\"|'([^']*)'|([^\s)]+))",
        re.IGNORECASE,
    )
    SRCSET_RE = re.compile(r"(?:^|,)\s*([^\s,]+)(?:\s+[^,]+)?")

    BOM_UTF8 = b"\xef\xbb\xbf"
    BOM_UTF16BE = b"\xfe\xff"
    BOM_UTF16LE = b"\xff\xfe"

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a parser using the supplied configuration."""
        self.config = config or ConverterConfig()
        self.parser = BytesParser(policy=policy.default)

    def _detect_encoding(self, content: bytes, content_type: str = "") -> tuple[str, bytes]:
        """Detect a BOM or MIME charset and return the charset and BOM-free bytes."""
        if content.startswith(self.BOM_UTF8):
            return "utf-8", content[len(self.BOM_UTF8) :]
        if content.startswith(self.BOM_UTF16BE):
            return "utf-16-be", content[len(self.BOM_UTF16BE) :]
        if content.startswith(self.BOM_UTF16LE):
            return "utf-16-le", content[len(self.BOM_UTF16LE) :]

        charset_match = self.CHARSET_RE.search(content_type)
        charset = charset_match.group(1) if charset_match else self.config.encoding
        try:
            content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return "utf-8", content
        return charset, content

    def decode_text(self, content: bytes | str, content_type: str = "") -> str:
        """Decode text with BOM/charset detection and a replacement fallback."""
        if isinstance(content, str):
            return content.removeprefix("\ufeff")
        charset, data = self._detect_encoding(content, content_type)
        try:
            return data.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return data.decode(self.config.encoding, errors="replace")

    def _decode_content(
        self,
        content: bytes,
        content_type: str,
        encoding: str,
    ) -> bytes:
        """Decode MIME transfer encodings while preserving text as raw bytes."""
        if not content:
            return b""
        transfer_encoding = encoding.strip().lower()
        if transfer_encoding in {"base64", "b64"}:
            compact = re.sub(rb"\s+", b"", content)
            try:
                return base64.b64decode(compact, validate=False)
            except (binascii.Error, ValueError):
                return content
        if transfer_encoding in {"quoted-printable", "qp"}:
            return quopri.decodestring(content)
        # 7bit/8bit/binary parts already contain their transfer-decoded bytes.
        del content_type
        return content

    @staticmethod
    def _get_resource_type(content_type: str) -> ResourceType:
        """Compatibility wrapper for MIME resource classification."""
        return classify_resource_type(content_type)

    def parse_mhtml(
        self,
        raw_data: bytes,
    ) -> tuple[Resource, dict[str, Resource], dict[str, Any]]:
        """Parse a full MHTML message into its main part, assets, and metadata.

        Raises:
            ValueError: If the input is empty or has no MIME content-type header.
        """
        if not raw_data.strip() or b"content-type:" not in raw_data.lower():
            raise ValueError("Invalid MHTML data: missing MIME Content-Type header")
        if len(raw_data) > self.config.max_input_size:
            raise ValueError(f"MHTML input exceeds the {self.config.max_input_size}-byte limit")

        message = self.parser.parsebytes(raw_data)
        main_part = self._find_main_part(message)
        main_resource = self._resource_from_part(main_part)
        resources = self._extract_resources(message, main_part)
        metadata = self._extract_metadata(message, main_resource, resources)
        return main_resource, resources, metadata

    @staticmethod
    def _find_main_part(message: Message) -> Message:
        """Select the first HTML part, or the first leaf if no HTML part exists."""
        if not message.is_multipart():
            return message
        parts = list(message.walk())
        for part in parts:
            if part.get_content_type().lower() in {
                "text/html",
                "text/htm",
                "application/xhtml+xml",
            }:
                return part
        return next((part for part in parts if not part.is_multipart()), message)

    def _resource_from_part(self, part: Message) -> Resource:
        """Build a resource from one MIME part, honoring its own MIME headers."""
        payload = part.get_payload(decode=False)
        if isinstance(payload, bytes):
            raw_content = payload
        elif isinstance(payload, str):
            raw_content = payload.encode("utf-8", errors="replace")
        else:
            raw_content = b""

        transfer_encoding = str(part.get("Content-Transfer-Encoding", "7bit"))
        content = self._decode_content(
            raw_content,
            str(part.get("Content-Type", part.get_content_type())),
            transfer_encoding,
        )
        url = str(part.get("Content-Location", "") or part.get("Content-ID", ""))
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]

        return Resource(
            url=url,
            content_type=part.get_content_type(),
            content=content,
            resource_type=self._get_resource_type(part.get_content_type()),
            encoding=transfer_encoding,
            headers={str(key): str(value) for key, value in part.items()},
            original_url=url,
        )

    def _extract_resources(
        self,
        message: Message,
        main_part: Message,
    ) -> dict[str, Resource]:
        """Extract all non-main MIME parts and index Content-ID aliases."""
        resources: dict[str, Resource] = {}
        if not message.is_multipart():
            return resources

        for part in message.walk():
            if part is message or part is main_part or part.is_multipart():
                continue
            resource = self._resource_from_part(part)
            if not resource.url:
                continue
            resources[resource.url] = resource
            content_id = str(part.get("Content-ID", "")).strip()
            if content_id.startswith("<") and content_id.endswith(">"):
                content_id = content_id[1:-1]
            if content_id:
                resources[f"cid:{content_id}"] = resource
        return resources

    def _extract_metadata(
        self,
        message: Message,
        main_resource: Resource,
        resources: dict[str, Resource],
    ) -> dict[str, Any]:
        """Extract MIME headers, structural details, and lightweight asset metadata."""
        metadata: dict[str, Any] = {
            "content_type": message.get_content_type(),
            "boundary": self._extract_boundary(message),
            "part_count": sum(1 for part in message.walk() if not part.is_multipart()),
            "main_resource_type": main_resource.resource_type.value,
            "resource_count": len({id(resource) for resource in resources.values()}),
        }
        if self.config.metadata_extraction:
            metadata.update({
                "subject": self._decode_header_value(message.get("Subject", "")),
                "from": self._decode_header_value(message.get("From", "")),
                "to": self._decode_header_value(message.get("To", "")),
                "date": self._decode_header_value(message.get("Date", "")),
                "content_location": main_resource.url,
                "resources": [
                    {
                        "url": resource.url,
                        "content_type": resource.content_type,
                        "resource_type": resource.resource_type.value,
                        "size": resource.size,
                    }
                    for resource in self._unique_resources(resources.values())
                ],
            })
        return metadata

    @staticmethod
    def _unique_resources(resources: Any) -> list[Resource]:
        """Return a stable unique list, accounting for CID aliases."""
        result: list[Resource] = []
        seen: set[int] = set()
        for resource in resources:
            identity = id(resource)
            if identity not in seen:
                result.append(resource)
                seen.add(identity)
        return result

    @staticmethod
    def _decode_header_value(value: Any) -> str:
        """Decode RFC 2047 encoded MIME headers into Unicode text."""
        if not value:
            return ""
        pieces: list[str] = []
        for chunk, charset in decode_header(str(value)):
            if isinstance(chunk, bytes):
                pieces.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                pieces.append(chunk)
        return "".join(pieces)

    def _extract_boundary(self, message: Message) -> str | None:
        """Extract the MIME boundary from a real or compatible message object."""
        content_type = str(message.get("Content-Type", ""))
        match = self.BOUNDARY_RE.search(content_type)
        if match is None:
            return None
        return next((value for value in match.groups() if value), None)

    def extract_urls_from_content(
        self,
        content: bytes | str,
        content_type: str,
    ) -> set[str]:
        """Find URL references in HTML and CSS, including srcset and CSS imports."""
        text = self.decode_text(content, content_type)
        urls: set[str] = set()
        media_type = content_type.split(";", maxsplit=1)[0].lower()
        if "html" in media_type or media_type.endswith("xhtml+xml"):
            urls.update(
                match.group("url").strip() for match in self.HTML_URL_RE.finditer(text) if match.group("url").strip()
            )
            for match in re.finditer(
                r"\bsrcset\s*=\s*([\"'])(.*?)\1",
                text,
                re.IGNORECASE | re.DOTALL,
            ):
                urls.update(
                    item.group(1).strip() for item in self.SRCSET_RE.finditer(match.group(2)) if item.group(1).strip()
                )
        if "css" in media_type or "html" in media_type:
            for match in self.CSS_URL_RE.finditer(text):
                url = (match.group("quoted") or match.group("plain") or "").strip().strip("\"'")
                if url:
                    urls.add(url)
        if "css" in media_type:
            for match in self.CSS_IMPORT_RE.finditer(text):
                url = next((value for value in match.groups() if value), "").strip("\"'")
                if url:
                    urls.add(url)
        return urls


# ===== Consolidated from converter/resource_manager.py =====


class ResourceCache:
    """Thread-safe, bounded LRU cache with optional persistent disk storage."""

    def __init__(self, max_size: int = 100, cache_dir: Path | None = None) -> None:
        """Create a cache holding at most ``max_size`` in-memory resources."""
        self.max_size = max_size
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._entries: OrderedDict[str, Resource] = OrderedDict()
        self._lock = threading.RLock()

    def put(self, key: str, resource: Resource) -> None:
        """Insert or replace a resource, persisting it when configured."""
        if self.max_size == 0:
            return
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = resource
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)
        self._write_disk(key, resource)

    def get(self, key: str) -> Resource | None:
        """Retrieve a resource and update its LRU position."""
        with self._lock:
            resource = self._entries.pop(key, None)
            if resource is not None:
                self._entries[key] = resource
                return resource
        resource = self._read_disk(key)
        if resource is not None:
            with self._lock:
                self._entries[key] = resource
                while len(self._entries) > self.max_size:
                    self._entries.popitem(last=False)
        return resource

    def clear(self, *, clear_disk: bool = False) -> None:
        """Clear memory entries and optionally remove persisted cache files."""
        with self._lock:
            self._entries.clear()
        if clear_disk and self.cache_dir is not None and self.cache_dir.exists():
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink(missing_ok=True)
                cache_file.with_suffix(".bin").unlink(missing_ok=True)

    def _cache_paths(self, key: str) -> tuple[Path, Path] | None:
        if self.cache_dir is None:
            return None
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{filename}.json", self.cache_dir / f"{filename}.bin"

    def _write_disk(self, key: str, resource: Resource) -> None:
        """Write one resource atomically to the optional disk cache."""
        paths = self._cache_paths(key)
        cache_dir = self.cache_dir
        if paths is None or cache_dir is None:
            return
        metadata_path, content_path = paths
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            is_text = isinstance(resource.content, str)
            content = resource.content.encode("utf-8") if isinstance(resource.content, str) else resource.content
            metadata = {
                "url": resource.url,
                "content_type": resource.content_type,
                "resource_type": resource.resource_type.value,
                "encoding": resource.encoding,
                "headers": resource.headers,
                "original_url": resource.original_url,
                "is_text": is_text,
            }
            content_tmp = content_path.with_suffix(".tmp")
            metadata_tmp = metadata_path.with_suffix(".tmp")
            content_tmp.write_bytes(content)
            metadata_tmp.write_text(json.dumps(metadata), encoding="utf-8")
            content_tmp.replace(content_path)
            metadata_tmp.replace(metadata_path)
        except OSError as error:
            logger.warning("Unable to persist resource cache entry {}: {}", key, error)

    def _read_disk(self, key: str) -> Resource | None:
        """Read and validate one resource from the optional disk cache."""
        paths = self._cache_paths(key)
        if paths is None:
            return None
        metadata_path, content_path = paths
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            raw_content = content_path.read_bytes()
            content: bytes | str = raw_content
            if metadata.get("is_text"):
                content = raw_content.decode("utf-8", errors="replace")
            return Resource(
                url=metadata["url"],
                content_type=metadata["content_type"],
                content=content,
                resource_type=ResourceType(metadata["resource_type"]),
                encoding=metadata.get("encoding"),
                headers=metadata.get("headers", {}),
                original_url=metadata.get("original_url"),
            )
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to URLs outside the resource manager's policy."""

    def __init__(self, is_allowed: Callable[[str], bool]) -> None:
        super().__init__()
        self._is_allowed = is_allowed

    # Signature is fixed by urllib.request.HTTPRedirectHandler.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not self._is_allowed(newurl):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "Redirect target rejected by resource security policy",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ResourceManager:
    """Resolve URLs against MHTML resources and optionally fetch public assets."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Set up cache and fetch settings from the converter configuration."""
        self.config = config or ConverterConfig()
        cache_dir = self.config.cache_dir if self.config.cache_enabled else None
        cache_size = self.config.cache_size if self.config.cache_enabled else 0
        self.cache = ResourceCache(cache_size, cache_dir)
        self._opener = urllib.request.build_opener(_SafeRedirectHandler(self._is_safe_external_url))

    @staticmethod
    def normalize_url(url: str, base_url: str) -> str:
        """Resolve relative URLs and discard fragments for asset lookup."""
        value = url.strip()
        if value.lower().startswith(("data:", "cid:")):
            return value
        normalized = urllib.parse.urljoin(base_url, value)
        return urllib.parse.urldefrag(normalized)[0]

    @staticmethod
    def _normalize_url(url: str, base_url: str) -> str:
        """Backwards-compatible alias for :meth:`normalize_url`."""
        return ResourceManager.normalize_url(url, base_url)

    @staticmethod
    def _convert_to_data_uri(resource: Resource) -> str:
        """Encode a resource as an RFC 2397 base64 data URI."""
        return resource_to_data_uri(resource)

    @staticmethod
    # Ordered signature checks make the MIME fallback straightforward to review.
    # pylint: disable=too-many-return-statements
    def _guess_content_type(url: str, content: bytes) -> str:
        """Guess MIME type from URL extension or a few common file signatures."""
        path = urllib.parse.urlsplit(url).path
        content_type, _ = mimetypes.guess_type(path)
        if content_type:
            return content_type
        if content.lstrip().lower().startswith((b"<html", b"<!doctype html")):
            return "text/html"
        if content.lstrip().startswith((b"body {", b"@import", b"@media")):
            return "text/css"
        if content.lstrip().startswith((b"function", b"console", b"const ", b"let ")):
            return "application/javascript"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return "application/octet-stream"

    def process_resources_parallel(
        self,
        urls: list[str],
        base_url: str,
    ) -> dict[str, Resource | None]:
        """Resolve the unique URLs in parallel where enabled, using the cache."""
        normalized_urls = list(dict.fromkeys(self.normalize_url(url, base_url) for url in urls))
        result: dict[str, Resource | None] = {}
        pending: list[str] = []
        for url in normalized_urls:
            cached = self.cache.get(url)
            if cached is None:
                pending.append(url)
            else:
                result[url] = cached

        if self.config.parallel_processing and len(pending) > 1:
            with ThreadPoolExecutor(max_workers=min(self.config.max_workers, len(pending))) as pool:
                futures = {pool.submit(self._fetch_resource, url, base_url): url for url in pending}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        result[url] = future.result()
                    except (OSError, ValueError, urllib.error.URLError) as error:
                        logger.debug("Resource fetch failed for {}: {}", url, error)
                        result[url] = None
        else:
            for url in pending:
                try:
                    result[url] = self._fetch_resource(url, base_url)
                except (OSError, ValueError, urllib.error.URLError) as error:
                    logger.debug("Resource fetch failed for {}: {}", url, error)
                    result[url] = None
        return result

    # Explicit guard clauses keep network limits and security policy obvious.
    # pylint: disable=too-many-return-statements
    def _fetch_resource(self, url: str, base_url: str) -> Resource | None:
        """Fetch a data URI or permitted public HTTP(S) asset within size limits."""
        normalized_url = self.normalize_url(url, base_url)
        if normalized_url.lower().startswith("data:"):
            return self._parse_data_uri(normalized_url)
        if normalized_url.lower().startswith("cid:") or not self.config.fetch_external:
            return None
        if not self._is_safe_external_url(normalized_url):
            logger.warning("Refusing unsafe external resource URL: {}", normalized_url)
            return None

        request = urllib.request.Request(
            normalized_url,
            headers={"User-Agent": "MHTML-Converter/1.0"},
        )
        for attempt in range(self.config.max_fetch_retries + 1):
            try:
                with self._opener.open(request, timeout=self.config.fetch_timeout) as response:
                    final_url = response.geturl()
                    if not self._is_safe_external_url(final_url):
                        logger.warning("Refusing unsafe redirect target: {}", final_url)
                        return None
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.config.max_resource_size:
                        logger.warning("Resource exceeds configured size limit: {}", final_url)
                        return None
                    content = response.read(self.config.max_resource_size + 1)
                    if len(content) > self.config.max_resource_size:
                        logger.warning("Resource exceeds configured size limit: {}", final_url)
                        return None
                    content_type = response.headers.get_content_type()
                    resource = Resource(
                        url=final_url,
                        content_type=content_type or self._guess_content_type(final_url, content),
                        content=content,
                        resource_type=self._get_resource_type(content_type),
                        encoding=response.headers.get("Content-Transfer-Encoding"),
                        headers=dict(response.headers.items()),
                        original_url=url,
                    )
                    transformed = self.transform_resource(resource)
                    self.cache.put(normalized_url, transformed)
                    return transformed
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                if attempt >= self.config.max_fetch_retries:
                    logger.debug("Could not fetch {}: {}", normalized_url, error)
                    return None
        return None

    # Early returns make the URL policy easy to audit as a sequence of checks.
    # pylint: disable=too-many-return-statements
    def _is_safe_external_url(self, url: str) -> bool:
        """Allow public HTTP(S) hosts that satisfy the configured domain policy."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        host = parts.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            return False
        if self._domain_matches(host, self.config.blocked_domains):
            return False
        if self.config.allowed_domains and not self._domain_matches(host, self.config.allowed_domains):
            return False

        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(result[4][0])
                    for result in socket.getaddrinfo(host, parts.port or 80, type=socket.SOCK_STREAM)
                ]
            except (OSError, ValueError):
                return False
        return bool(addresses) and all(address.is_global for address in addresses)

    @staticmethod
    def _domain_matches(host: str, domains: list[str]) -> bool:
        """Match an exact hostname or a subdomain of a configured domain."""
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    # Each malformed data-URI condition returns safely without extra nesting.
    # pylint: disable=too-many-return-statements
    def _parse_data_uri(self, data_uri: str) -> Resource | None:
        """Decode a data URI, rejecting malformed or oversized payloads."""
        if not data_uri.lower().startswith("data:"):
            return None
        header, separator, data = data_uri[5:].partition(",")
        if not separator:
            return None
        header_parts = header.split(";")
        content_type = header_parts[0] or "text/plain"
        is_base64 = any(part.lower() == "base64" for part in header_parts[1:])
        try:
            if is_base64:
                content = base64.b64decode(re.sub(r"\s+", "", data), validate=False)
            else:
                content = urllib.parse.unquote_to_bytes(data)
        except (ValueError, binascii.Error):
            return None
        if len(content) > self.config.max_resource_size:
            return None
        resource = Resource(
            url=data_uri,
            content_type=content_type,
            content=content,
            resource_type=self._get_resource_type(content_type),
            encoding="base64" if is_base64 else "7bit",
            original_url=data_uri,
        )
        return self.transform_resource(resource)

    @staticmethod
    def _get_resource_type(content_type: str) -> ResourceType:
        """Compatibility wrapper for shared MIME resource classification."""
        return classify_resource_type(content_type)

    def transform_resource(self, resource: Resource) -> Resource:
        """Apply the first matching user handler to a resource."""
        media_type = resource.content_type.split(";", maxsplit=1)[0].lower()
        handler = next(
            (
                candidate
                for key, candidate in self.config.custom_handlers.items()
                if key.lower()
                in {
                    media_type,
                    resource.resource_type.value,
                    f"{media_type.split('/')[0]}/*",
                }
            ),
            None,
        )
        if handler is None:
            return resource
        transformed = handler(resource)
        if transformed is None:
            return resource
        if isinstance(transformed, Resource):
            return transformed
        return Resource(
            url=resource.url,
            content_type=resource.content_type,
            content=transformed,
            resource_type=resource.resource_type,
            encoding=resource.encoding,
            headers=resource.headers.copy(),
            original_url=resource.original_url,
        )


# ===== Consolidated from converter/css_processor.py =====


class CSSProcessor:
    """Rewrite CSS asset references with tinycss2 while preserving valid syntax."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a CSS processor bound to a converter configuration."""
        self.config = config or ConverterConfig()

    def rewrite_css(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
        depth: int = 0,
    ) -> str:
        """Resolve CSS ``url()`` and ``@import`` references against resources.

        Comments are removed when ``strip_css_comments`` is enabled. At or past
        ``max_import_depth``, imports are left as normalized references and are
        not expanded further.
        """
        if depth > self.config.max_import_depth:
            return content
        rules = tinycss2.parse_stylesheet(
            content,
            skip_comments=self.config.strip_css_comments,
            skip_whitespace=False,
        )
        self._rewrite_tokens(rules, base_url, resource_map, depth)
        result = cast(str, tinycss2.serialize(rules))
        if self.config.minify_css:
            result = self._conservative_minify(result)
        return result

    def extract_urls(self, content: str) -> set[str]:
        """Return URL tokens and string-form ``@import`` references in CSS."""
        rules = tinycss2.parse_stylesheet(content, skip_comments=True, skip_whitespace=False)
        urls: set[str] = set()
        self._collect_urls(rules, urls)
        return urls

    @staticmethod
    def parse_css(content: str) -> list[Any]:
        """Parse a stylesheet and expose tinycss2 error tokens for validation."""
        return cast(
            list[Any],
            tinycss2.parse_stylesheet(content, skip_comments=False, skip_whitespace=False),
        )

    def _rewrite_tokens(
        self,
        tokens: list[Any],
        base_url: str,
        resource_map: dict[str, Resource],
        depth: int,
    ) -> None:
        """Mutate tinycss2 tokens recursively, rewriting nested URL values."""
        for token in tokens:
            token_type = getattr(token, "type", "")
            if token_type == "url":
                token.value = self._rewrite_url(token.value, base_url, resource_map)
                token.representation = self._url_representation(token.value)
            elif token_type == "function" and token.name.lower() == "url":
                url = self._function_url_value(token)
                rewritten = self._rewrite_url(url, base_url, resource_map)
                token.arguments = [
                    StringToken(
                        token.source_line,
                        token.source_column,
                        rewritten,
                        self._string_representation(rewritten),
                    )
                ]
            elif token_type == "at-rule":
                if token.lower_at_keyword == "import":
                    self._rewrite_import(token, base_url, resource_map, depth)
                else:
                    self._rewrite_tokens(token.prelude, base_url, resource_map, depth)
                if token.content is not None:
                    self._rewrite_tokens(token.content, base_url, resource_map, depth)
            else:
                nested = getattr(token, "content", None)
                if isinstance(nested, list):
                    self._rewrite_tokens(nested, base_url, resource_map, depth)
                prelude = getattr(token, "prelude", None)
                if isinstance(prelude, list):
                    self._rewrite_tokens(prelude, base_url, resource_map, depth)
                arguments = getattr(token, "arguments", None)
                if isinstance(arguments, list) and token_type != "function":
                    self._rewrite_tokens(arguments, base_url, resource_map, depth)

    def _rewrite_import(
        self,
        token: Any,
        base_url: str,
        resource_map: dict[str, Resource],
        depth: int,
    ) -> None:
        """Rewrite the first URL in an ``@import`` prelude."""
        if depth >= self.config.max_import_depth:
            return
        for item in token.prelude:
            if item.type in {"whitespace", "comment"}:
                continue
            if item.type == "string":
                rewritten = self._rewrite_url(item.value, base_url, resource_map)
                item.value = rewritten
                item.representation = self._string_representation(rewritten)
            elif item.type == "url":
                item.value = self._rewrite_url(item.value, base_url, resource_map)
                item.representation = self._url_representation(item.value)
            elif item.type == "function" and item.name.lower() == "url":
                url = self._function_url_value(item)
                rewritten = self._rewrite_url(url, base_url, resource_map)
                item.arguments = [
                    StringToken(
                        item.source_line,
                        item.source_column,
                        rewritten,
                        self._string_representation(rewritten),
                    )
                ]
            break

    def _rewrite_url(
        self,
        url: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Return an inlined URI or normalized safe URL for one asset reference."""
        value = url.strip().strip("\"'")
        if not value or value.lower().startswith("data:"):
            return value
        scheme = urllib.parse.urlsplit(value).scheme.lower()
        if scheme in {"javascript", "vbscript"}:
            return ""
        return resolve_resource_url(value, base_url, resource_map, self.config)

    @staticmethod
    def _function_url_value(token: Any) -> str:
        """Extract the unquoted URL from a parsed ``url(...)`` function token."""
        if len(token.arguments) == 1 and hasattr(token.arguments[0], "value"):
            return str(token.arguments[0].value)
        serialized = cast(str, tinycss2.serialize(token.arguments))
        return serialized.strip().strip("\"'")

    @staticmethod
    def _string_representation(value: str) -> str:
        """Quote a CSS string, escaping delimiters and backslashes."""
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ")
        return f'"{escaped}"'

    @classmethod
    def _url_representation(cls, value: str) -> str:
        """Serialize a URL token without allowing its value to break CSS syntax."""
        return f"url({cls._string_representation(value)})"

    def _collect_urls(self, tokens: list[Any], urls: set[str]) -> None:
        """Walk a tinycss2 token tree and collect URLs and import strings."""
        for token in tokens:
            self._collect_token_urls(token, urls)

    def _collect_token_urls(self, token: Any, urls: set[str]) -> None:
        """Collect direct URL values and recursively visit a token's children."""
        token_type = getattr(token, "type", "")
        if token_type == "url" and token.value:
            urls.add(token.value)
        elif token_type == "function" and token.name.lower() == "url":
            url = self._function_url_value(token)
            if url:
                urls.add(url)
        elif token_type == "at-rule" and token.lower_at_keyword == "import":
            self._collect_import_url(token.prelude, urls)
        self._collect_nested_urls(token, urls)

    def _collect_import_url(self, prelude: list[Any], urls: set[str]) -> None:
        """Collect the URL-like first item of an ``@import`` prelude."""
        for item in prelude:
            if item.type in {"whitespace", "comment"}:
                continue
            if item.type in {"string", "url"} and item.value:
                urls.add(item.value)
            elif item.type == "function" and item.name.lower() == "url":
                url = self._function_url_value(item)
                if url:
                    urls.add(url)
            return

    def _collect_nested_urls(self, token: Any, urls: set[str]) -> None:
        """Recursively inspect nested component values, rules, and function args."""
        for name in ("content", "prelude", "arguments"):
            nested = getattr(token, name, None)
            if isinstance(nested, list):
                self._collect_urls(nested, urls)

    def _conservative_minify(self, content: str) -> str:
        """Reduce redundant spaces only around punctuation safe in CSS syntax."""
        result = re.sub(r"\s*([{}:;,>])\s*", r"\1", content)
        return result.strip()

    def _rewrite_with_regex_fallback(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Compatibility helper for consumers that need a regex-only fallback."""
        url_pattern = re.compile(
            r"url\(\s*(?P<quote>[\"']?)(?P<url>[^)\"']+)(?P=quote)\s*\)",
            re.IGNORECASE,
        )

        def replace(match: re.Match[str]) -> str:
            original = match.group(0)
            rewritten = self._rewrite_url(match.group("url"), base_url, resource_map)
            return f"url({self._string_representation(rewritten)})" if rewritten else original

        stripped = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL) if self.config.strip_css_comments else content
        return url_pattern.sub(replace, stripped)


# ===== Consolidated from converter/html_processor.py =====

_VOID_TAGS = frozenset({
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
})
_SAFE_EXTRA_ATTRIBUTES = frozenset({
    "style",
    "srcset",
    "sizes",
    "loading",
    "decoding",
    "fetchpriority",
    "crossorigin",
    "integrity",
    "referrerpolicy",
    "download",
    "poster",
    "background",
    "data",
    "form",
    "formaction",
    "formenctype",
    "formmethod",
    "formnovalidate",
    "formtarget",
    "placeholder",
    "pattern",
    "minlength",
    "maxlength",
    "autocomplete",
    "autofocus",
    "required",
    "multiple",
    "novalidate",
    "step",
    "min",
    "max",
    "controls",
    "loop",
    "muted",
    "autoplay",
    "preload",
    "sandbox",
    "allowfullscreen",
    "frameborder",
    "scrolling",
    "marginwidth",
    "marginheight",
    "xlink:href",
})


# This compatibility model mirrors the legacy public node constructor.
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments
class HTMLNode:
    """Small mutable HTML node model retained for the legacy tree API."""

    def __init__(
        self,
        tag: str = "",
        is_text: bool = False,
        text: str = "",
        is_comment: bool = False,
        comment: str = "",
        is_doctype: bool = False,
        attributes: dict[str, str] | None = None,
    ) -> None:
        """Initialize a node and its relationship fields."""
        self.tag = tag
        self.is_text = is_text
        self.text = text
        self.is_comment = is_comment
        self.comment = comment
        self.is_doctype = is_doctype
        self.attributes = attributes.copy() if attributes else {}
        self.children: list[HTMLNode] = []
        self.parent: HTMLNode | None = None

    def add_child(self, child: HTMLNode) -> None:
        """Append a child and update its parent pointer."""
        child.parent = self
        self.children.append(child)

    def to_string(self) -> str:
        """Serialize this node with text/attribute escaping."""
        if self.is_doctype:
            return ""
        if self.is_comment:
            safe_comment = self.comment.replace("--", "- -")
            return f"<!--{safe_comment}-->"
        if self.is_text:
            return html_stdlib.escape(self.text, quote=False)
        if not self.tag:
            return "".join(child.to_string() for child in self.children)

        attributes = "".join(
            f' {name}="{html_stdlib.escape(value, quote=True)}"' for name, value in self.attributes.items()
        )
        children = "".join(child.to_string() for child in self.children)
        if self.tag.lower() in _VOID_TAGS:
            return f"<{self.tag}{attributes} />"
        return f"<{self.tag}{attributes}>{children}</{self.tag}>"


# Maintains the old API shape while exposing one public ``feed`` operation.
# pylint: disable=too-few-public-methods
class HTMLTreeParser:
    """Compatibility tree parser that exposes :class:`HTMLNode` instances."""

    def __init__(self) -> None:
        """Initialize an empty document root."""
        self.root = HTMLNode()
        self.current = self.root

    def feed(self, html_content: str) -> None:
        """Parse an HTML document, retaining comments and meaningful text."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=False)
        try:
            tree = lxml_html.fromstring(html_content, parser=parser)
        except (ValueError, etree.LxmlError):
            self.root = HTMLNode()
            self.root.add_child(HTMLNode(tag="html"))
            self.current = self.root
            return
        self.root = HTMLNode()
        self.root.add_child(self._convert_lxml_to_node(tree))
        self.current = self.root

    def _convert_lxml_to_node(self, element: Any) -> HTMLNode:
        """Copy an lxml element and its mixed text content into HTMLNode form."""
        if isinstance(element, lxml_html.HtmlComment):
            comment_text = element.text or ""
            return HTMLNode(is_comment=True, comment=comment_text, text=comment_text)

        node = HTMLNode(tag=str(element.tag), attributes=dict(element.attrib))
        if element.text and element.text.strip():
            node.add_child(HTMLNode(is_text=True, text=element.text))
        for child in element:
            node.add_child(self._convert_lxml_to_node(child))
            if child.tail and child.tail.strip():
                node.add_child(HTMLNode(is_text=True, text=child.tail))
        return node


class _MHTMLCleaner(Cleaner):
    """Preserve opted-in inlined assets while filtering active URL schemes."""

    safe_attrs: frozenset[str]

    def __init__(
        self,
        allow_inline_scripts: bool,
        allow_inline_styles: bool,
        allow_external_stylesheets: bool,
        **options: Any,
    ) -> None:
        self.allow_inline_scripts = allow_inline_scripts
        self.allow_inline_styles = allow_inline_styles
        self.allow_external_stylesheets = allow_external_stylesheets
        super().__init__(**options)

    @staticmethod
    def _data_uri_media_type(url: str) -> str:
        """Return a data URI media type, ignoring parameters."""
        if not url.lower().startswith("data:"):
            return ""
        return url[5:].split(",", maxsplit=1)[0].split(";", maxsplit=1)[0].lower()

    def sanitize_url(self, link: str) -> str:
        """Reject scriptable URL schemes and allow intended data resources."""
        compact = re.sub(r"[\s\x00-\x20]+", "", urllib.parse.unquote(link).strip()).lower()
        media_type = self._data_uri_media_type(compact)
        if compact.startswith("data:"):
            allowed_media_type = (
                (
                    self.allow_inline_scripts
                    and media_type
                    in {
                        "application/javascript",
                        "application/x-javascript",
                        "text/javascript",
                    }
                )
                or (self.allow_inline_styles and media_type == "text/css")
                or (media_type.startswith("image/") and media_type != "image/svg+xml")
                or media_type in {"font/woff", "font/woff2", "application/font-woff"}
            )
            return link if allowed_media_type else ""
        scheme = urllib.parse.urlsplit(compact).scheme
        return "" if scheme in {"javascript", "vbscript", "file", "filesystem", "blob"} else link

    def _remove_javascript_link(self, link: str) -> str:
        """Adapt lxml's callback to the public URL sanitizer."""
        return self.sanitize_url(link)

    def allow_element(self, el: Any) -> bool:
        """Keep only enabled stylesheets when lxml checks embedded links."""
        if el.tag == "link" and "stylesheet" in el.get("rel", "").lower():
            href = el.get("href", "")
            media_type = self._data_uri_media_type(href)
            if self.allow_inline_styles and media_type == "text/css":
                return True
            if self.allow_external_stylesheets:
                return urllib.parse.urlsplit(href).scheme.lower() in {"http", "https"}
        return False


class HTMLProcessor:
    """Parse, validate, rewrite, and sanitize HTML content."""

    URL_ATTRIBUTES: ClassVar[tuple[str, ...]] = (
        "src",
        "href",
        "poster",
        "background",
        "action",
        "formaction",
        "data",
        "xlink:href",
    )
    RESOURCE_TAGS: ClassVar[dict[str, frozenset[str]]] = {
        "img": frozenset({"src", "srcset"}),
        "script": frozenset({"src"}),
        "link": frozenset({"href"}),
        "source": frozenset({"src", "srcset"}),
        "video": frozenset({"src", "poster"}),
        "audio": frozenset({"src"}),
        "track": frozenset({"src"}),
        "iframe": frozenset({"src"}),
        "embed": frozenset({"src"}),
        "object": frozenset({"data"}),
        "body": frozenset({"background"}),
        "table": frozenset({"background"}),
        "td": frozenset({"background"}),
        "th": frozenset({"background"}),
        "input": frozenset({"src"}),
    }

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create an HTML processor and its CSS rewriter."""
        self.config = config or ConverterConfig()
        self.css_processor = CSSProcessor(self.config)
        self.cleaner = _MHTMLCleaner(
            allow_inline_scripts=self.config.include_scripts and not self.config.remove_scripts,
            allow_inline_styles=self.config.include_styles,
            allow_external_stylesheets=(self.config.resource_handling in {"external", "mixed"}),
            safe_attrs_only=True,
            safe_attrs=frozenset(getattr(Cleaner, "safe_attrs", frozenset())) | _SAFE_EXTRA_ATTRIBUTES,
            scripts=not self.config.include_scripts or self.config.remove_scripts,
            style=not self.config.include_styles,
            links=False,
            meta=False,
            page_structure=False,
            forms=False,
            kill_tags=("base",),
            remove_unknown_tags=False,
        )

    def parse_basic_html(self, content: str) -> Any:
        """Parse an HTML fragment or document with error recovery enabled."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=False)
        if not content.strip():
            return lxml_html.fromstring("<html><body></body></html>", parser=parser)
        return lxml_html.fromstring(content, parser=parser)

    def to_string(self, element: Any) -> str:
        """Serialize an lxml element using the configured HTML/XHTML method."""
        method = "xml" if self.config.output_format == "xhtml" else "html"
        return cast(str, lxml_html.tostring(element, encoding="unicode", method=method))

    def extract_resource_urls(self, content: str) -> set[str]:
        """Find fetchable assets in HTML, excluding ordinary anchor navigation."""
        try:
            tree = self.parse_basic_html(content)
        except (ValueError, etree.LxmlError):
            return set()
        urls: set[str] = set()
        for element in tree.iter():
            tag = str(element.tag).lower()
            attributes: set[str] = set(self.RESOURCE_TAGS.get(tag, frozenset()))
            if tag == "script" and (not self.config.include_scripts or self.config.remove_scripts):
                attributes = set()
            if tag == "link":
                rel = element.get("rel", "").lower().split()
                allowed_rel = {"icon", "preload", "modulepreload", "manifest"}
                if self.config.include_styles:
                    allowed_rel.add("stylesheet")
                if not allowed_rel.intersection(rel):
                    attributes = set()
            for name in attributes:
                value = element.get(name)
                if not value:
                    continue
                if name == "srcset":
                    urls.update(url for url, _ in self._parse_srcset(value))
                else:
                    urls.add(value.strip())
            style = element.get("style")
            if style:
                urls.update(self.css_processor.extract_urls(f"x{{{style}}}"))
            if tag == "style" and element.text:
                urls.update(self.css_processor.extract_urls(element.text))
        return {url for url in urls if url and not url.lower().startswith("data:")}

    def rewrite_html(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Rewrite assets in HTML and sanitize the result when configured."""
        try:
            tree = self.parse_basic_html(content)
        except (ValueError, etree.LxmlError):
            return self._rewrite_with_regex_fallback(content, base_url, resource_map)

        for element in tree.iter():
            for attribute in self.URL_ATTRIBUTES:
                if attribute in element.attrib:
                    element.attrib[attribute] = self._rewrite_url(element.attrib[attribute], base_url, resource_map)
            if "srcset" in element.attrib:
                candidates = self._parse_srcset(element.attrib["srcset"])
                element.attrib["srcset"] = ", ".join(
                    f"{self._rewrite_url(url, base_url, resource_map)}{descriptor}" for url, descriptor in candidates
                )
            if self.config.include_styles and "style" in element.attrib:
                declaration = element.attrib["style"]
                rewritten = self.css_processor.rewrite_css(f"mhtml-inline{{{declaration}}}", base_url, resource_map)
                element.attrib["style"] = self._extract_rule_body(rewritten)
            if self.config.include_styles and str(element.tag).lower() == "style" and element.text:
                element.text = self.css_processor.rewrite_css(element.text, base_url, resource_map)

        if self.config.sanitize_html:
            self._extend_safe_attributes(tree)
            tree = self.cleaner.clean_html(tree)
        return self.to_string(tree)

    def validate_html(self, content: str) -> list[str]:
        """Return parser diagnostics for an HTML document or fragment."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=False)
        try:
            lxml_html.fromstring(content or "<html></html>", parser=parser)
        except (ValueError, etree.LxmlError) as error:
            return [str(error)]
        return [str(entry) for entry in parser.error_log]

    def minify_html(self, content: str) -> str:
        """Remove comments and serialize compactly while preserving text spacing."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=True)
        try:
            tree = lxml_html.fromstring(content or "<html></html>", parser=parser)
        except (ValueError, etree.LxmlError):
            return content
        method = "xml" if self.config.output_format == "xhtml" else "html"
        return cast(str, lxml_html.tostring(tree, encoding="unicode", method=method))

    def _extend_safe_attributes(self, tree: Any) -> None:
        """Preserve data-/ARIA attributes while dropping event-handler attributes."""
        safe_attributes = set(getattr(self.cleaner, "safe_attrs", frozenset()))
        for element in tree.iter():
            for name in tuple(element.attrib):
                normalized = name.lower()
                if normalized.startswith(("data-", "aria-")):
                    safe_attributes.add(name)
        self.cleaner.safe_attrs = frozenset(safe_attributes)

    @staticmethod
    def _extract_rule_body(css: str) -> str:
        """Extract the declaration block from a temporary selector wrapper."""
        opening = css.find("{")
        closing = css.rfind("}")
        if opening < 0 or closing < opening:
            return css
        return css[opening + 1 : closing].strip()

    @staticmethod
    def _parse_srcset(value: str) -> list[tuple[str, str]]:
        """Parse basic srcset candidates without splitting commas inside data URLs."""
        candidates: list[tuple[str, str]] = []
        length = len(value)
        cursor = 0
        while cursor < length:
            while cursor < length and (value[cursor].isspace() or value[cursor] == ","):
                cursor += 1
            start = cursor
            while cursor < length and not value[cursor].isspace():
                cursor += 1
            url = value[start:cursor].rstrip(",")
            if not url:
                continue
            descriptor_start = cursor
            while cursor < length and value[cursor] != ",":
                cursor += 1
            descriptor = value[descriptor_start:cursor].strip()
            candidates.append((url, f" {descriptor}" if descriptor else ""))
            cursor += cursor < length
        return candidates

    def _rewrite_url(
        self,
        url: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Return an inlined data URI or a normalized URL for an HTML attribute."""
        value = url.strip()
        if not value:
            return value
        if self.config.sanitize_html and not self.cleaner.sanitize_url(value):
            return ""
        return resolve_resource_url(value, base_url, resource_map, self.config)

    def _rewrite_with_regex(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Regex-only URL rewrite retained for compatibility and parse recovery."""
        pattern = re.compile(
            r"(?P<prefix>\b(?:src|href|poster|background|action|data)\s*=\s*)"
            r"(?P<quote>[\"'])(?P<url>.*?)(?P=quote)",
            re.IGNORECASE | re.DOTALL,
        )
        return pattern.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}"
                f"{self._rewrite_url(match.group('url'), base_url, resource_map)}"
                f"{match.group('quote')}"
            ),
            content,
        )

    def _rewrite_with_regex_fallback(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Alias kept for backwards compatibility with the original scripts."""
        rewritten = self._rewrite_with_regex(content, base_url, resource_map)
        if not self.config.sanitize_html:
            return rewritten
        rewritten = re.sub(
            r"\s+on[a-z]+\s*=\s*([\"']).*?\1",
            "",
            rewritten,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return re.sub(
            r"\b(href|src|action)\s*=\s*([\"'])\s*(?:javascript|vbscript):.*?\2",
            r'\1=""',
            rewritten,
            flags=re.IGNORECASE | re.DOTALL,
        )


# ===== Consolidated from converter/plugins.py =====


class ConverterPlugin:
    """Base class for optional converter extensions.

    Subclasses can override any hook. Hooks run in registration order. The
    default implementations leave data untouched.
    """

    def before_parse(self, data: bytes, config: ConverterConfig) -> bytes:
        """Transform MHTML bytes before parsing."""
        del config
        return data

    def process_resource(self, resource: Resource) -> Resource:
        """Transform an extracted resource before it is rewritten into HTML."""
        return resource

    def after_convert(self, output: bytes, metadata: dict[str, Any]) -> bytes:
        """Transform final output bytes after conversion and validation."""
        del metadata
        return output


# ===== Consolidated from converter/converter.py =====


@dataclass(slots=True)
class _ParsedDocument:
    """Internal aggregate of the selected MIME part and its companion assets."""

    main_resource: Resource
    resources: dict[str, Resource]
    metadata: dict[str, Any]


@dataclass(slots=True)
class _CSSRewriteState:
    """Mutable traversal state for recursive CSS rewriting and cycle detection."""

    resource_map: dict[str, Resource]
    fallback_base: str
    visiting: set[int] = field(default_factory=set)
    processed: set[int] = field(default_factory=set)


class MHTMLConverter:
    """Coordinate MIME parsing, resource processing, rewriting, and output."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a converter and its stateful cache/resource services."""
        self.config = config or ConverterConfig()
        self.logger = ConverterLogger(self.config)
        self.parser = MHTMLParser(self.config)
        self.resource_manager = ResourceManager(self.config)
        self.html_processor = HTMLProcessor(self.config)
        self.css_processor = CSSProcessor(self.config)

    def __enter__(self) -> Self:
        """Allow use as a context manager for timely logger cleanup."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Release this converter's optional Loguru file sink."""
        self.close()

    def close(self) -> None:
        """Close this converter's optional logging sink."""
        self.logger.close()

    def convert(
        self,
        mhtml_data: bytes | str,
        base_url: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Convert in-memory MHTML data to HTML bytes and a metadata dictionary.

        The parser retains MIME part payloads in memory. ``max_input_size`` and
        ``max_resource_size`` provide predictable bounds; use ``convert_stream``
        or ``convert_file`` to write the final output incrementally.
        """
        callback = progress_callback if progress_callback is not None else self.config.progress_callback
        previous_callback = self.logger.progress_callback
        self.logger.set_progress_callback(callback)
        try:
            return self._convert(mhtml_data, base_url)
        finally:
            self.logger.set_progress_callback(previous_callback)

    def _convert(
        self,
        mhtml_data: bytes | str,
        base_url: str | None,
    ) -> tuple[bytes, dict[str, Any]]:
        raw_data = mhtml_data.encode(self.config.encoding) if isinstance(mhtml_data, str) else mhtml_data
        if len(raw_data) > self.config.max_input_size:
            raise ValueError(f"MHTML input exceeds the {self.config.max_input_size}-byte limit")

        self.logger.info("Starting MHTML conversion")
        self.logger.debug(f"Configuration: {self.config.to_dict()}")
        self.logger.log_progress("Parsing MHTML", 0.1)
        try:
            prepared_data = self._run_before_parse_plugins(raw_data)
            main_resource, resources, metadata = self.parser.parse_mhtml(prepared_data)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.logger.exception("MHTML parsing or input plugin failed")
            if not self.config.error_recovery:
                raise
            return self._fallback_conversion(raw_data, error)

        parsed = _ParsedDocument(main_resource, resources, metadata)
        return self._convert_parsed(raw_data, parsed, base_url)

    def _convert_parsed(
        self,
        raw_data: bytes,
        parsed: _ParsedDocument,
        base_url: str | None,
    ) -> tuple[bytes, dict[str, Any]]:
        main_resource = parsed.main_resource
        resolved_base = self._resolve_base_url(base_url, main_resource.url)
        resource_map = self._normalize_resource_map(parsed.resources, resolved_base)
        self.logger.log_progress("Processing resources", 0.3)

        main_content = self._resource_text(main_resource)
        assets = self._extract_main_asset_urls(main_resource, main_content)
        self._fetch_missing_resources(assets, resolved_base, resource_map)
        self._fetch_nested_css_resources(main_resource, resource_map, resolved_base)
        self._apply_resource_plugins(resource_map)
        self._rewrite_css_resources(resource_map, resolved_base)

        self.logger.log_progress("Rewriting main content", 0.6)
        output = self._render_main_resource(
            main_resource,
            main_content,
            resolved_base,
            resource_map,
        )
        output = self._run_after_convert_plugins(output, parsed.metadata)
        validation = self._validate_output(output, main_resource.resource_type)
        self.logger.log_progress("Validating and finalizing output", 0.9)

        metadata: dict[str, Any] = {
            "original_size": len(raw_data),
            "output_size": len(output),
            "resource_count": len(self._unique_resources(resource_map.values())),
            "main_resource_type": main_resource.resource_type.value,
            "content_type": main_resource.content_type,
            "profile": self.config.profile.value,
            "metadata": parsed.metadata,
            "config": self.config.to_dict(),
            "validation": validation,
            "output_format": self.config.output_format,
        }
        self.logger.log_progress("Conversion complete", 1.0)
        self.logger.info("MHTML conversion completed successfully")
        return output, metadata

    def _resolve_base_url(self, base_url: str | None, resource_url: str) -> str:
        """Use a caller URL or the main part's URL when it is a valid HTTP base."""
        if base_url:
            return base_url
        if resource_url and urlsplit(resource_url).scheme.lower() in {"http", "https"}:
            return resource_url
        return "http://localhost/"

    def _render_main_resource(
        self,
        main_resource: Resource,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> bytes:
        """Rewrite a main HTML/CSS part and encode its output."""
        if main_resource.resource_type == ResourceType.HTML:
            rewritten = self.html_processor.rewrite_html(content, base_url, resource_map)
            if self.config.minify_html or self.config.compress_output:
                rewritten = self.html_processor.minify_html(rewritten)
            return rewritten.encode(self.config.encoding, errors="replace")
        if main_resource.resource_type == ResourceType.CSS:
            rewritten = self.css_processor.rewrite_css(content, base_url, resource_map)
            return rewritten.encode(self.config.encoding, errors="replace")
        if isinstance(main_resource.content, bytes):
            return main_resource.content
        return main_resource.content.encode(self.config.encoding, errors="replace")

    def convert_file(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        base_url: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Convert a file, writing ``<input-stem>.html`` beside it by default."""
        source = Path(input_path)
        if not source.is_file():
            raise FileNotFoundError(f"Input MHTML file does not exist: {source}")
        if source.stat().st_size > self.config.max_input_size:
            raise ValueError(f"MHTML input exceeds the {self.config.max_input_size}-byte limit")
        destination = Path(output_path) if output_path is not None else source.with_suffix(".html")
        if source.resolve() == destination.resolve():
            raise ValueError("Output path must not overwrite the input MHTML file")

        with source.open("rb") as input_file:
            result, metadata = self.convert(
                input_file.read(self.config.max_input_size + 1),
                base_url=base_url,
                progress_callback=progress_callback,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output_file:
            for chunk in self._chunks(result):
                output_file.write(chunk)
        metadata["output_path"] = str(destination)
        return destination, metadata

    def convert_stream(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        base_url: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Read an input stream within limits and write output in bounded chunks.

        The MIME parser needs a complete message for reliable related-part
        lookup, so input is buffered up to ``max_input_size``; output is written
        incrementally according to ``chunk_size``.
        """
        input_buffer = bytearray()
        while True:
            next_chunk = source.read(self.config.chunk_size)
            if not next_chunk:
                break
            input_buffer.extend(next_chunk)
            if len(input_buffer) > self.config.max_input_size:
                raise ValueError(f"MHTML input exceeds the {self.config.max_input_size}-byte limit")
        result, metadata = self.convert(
            bytes(input_buffer),
            base_url=base_url,
            progress_callback=progress_callback,
        )
        destination.writelines(self._chunks(result))
        return metadata

    def iter_convert(
        self,
        mhtml_data: bytes | str,
        base_url: str | None = None,
    ) -> Iterator[bytes]:
        """Yield the converted output in ``chunk_size`` byte pieces."""
        output, _metadata = self.convert(mhtml_data, base_url=base_url)
        yield from self._chunks(output)

    def _chunks(self, content: bytes) -> Iterator[bytes]:
        for offset in range(0, len(content), self.config.chunk_size):
            yield content[offset : offset + self.config.chunk_size]

    def _run_before_parse_plugins(self, data: bytes) -> bytes:
        """Run each plugin's input hook in the order it was registered."""
        for plugin in self.config.plugins:
            data = plugin.before_parse(data, self.config)
            if not isinstance(data, bytes):
                raise TypeError("plugin before_parse hooks must return bytes")
        return data

    def _run_after_convert_plugins(
        self,
        output: bytes,
        metadata: dict[str, Any],
    ) -> bytes:
        """Run each plugin's output hook in registration order."""
        for plugin in self.config.plugins:
            output = plugin.after_convert(output, metadata)
            if not isinstance(output, bytes):
                raise TypeError("plugin after_convert hooks must return bytes")
        return output

    def _normalize_resource_map(
        self,
        resources: dict[str, Resource],
        base_url: str,
    ) -> dict[str, Resource]:
        """Canonicalize MIME resource keys and apply custom resource handlers."""
        normalized: dict[str, Resource] = {}
        transformed_by_id: dict[int, Resource] = {}
        for key, resource in resources.items():
            transformed = transformed_by_id.get(id(resource))
            if transformed is None:
                transformed = self.resource_manager.transform_resource(resource)
                transformed_by_id[id(resource)] = transformed
            if key.lower().startswith(("cid:", "data:")):
                normalized[key] = transformed
            else:
                normalized[self.resource_manager.normalize_url(key, base_url)] = transformed
            if resource.url:
                if resource.url.lower().startswith(("cid:", "data:")):
                    normalized[resource.url] = transformed
                else:
                    normalized[self.resource_manager.normalize_url(resource.url, base_url)] = transformed
        return normalized

    def _extract_main_asset_urls(
        self,
        main_resource: Resource,
        content: str,
    ) -> set[str]:
        """Discover fetchable HTML/CSS assets, excluding ordinary anchor links."""
        if main_resource.resource_type == ResourceType.HTML:
            return self.html_processor.extract_resource_urls(content)
        if main_resource.resource_type == ResourceType.CSS:
            return self.css_processor.extract_urls(content)
        return set()

    def _fetch_missing_resources(
        self,
        urls: set[str],
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> None:
        """Fetch external assets only when not already present in the MIME map."""
        missing = [
            self.resource_manager.normalize_url(url, base_url)
            for url in urls
            if self.resource_manager.normalize_url(url, base_url) not in resource_map
        ]
        fetched = self.resource_manager.process_resources_parallel(missing, base_url)
        resource_map.update((url, resource) for url, resource in fetched.items() if resource is not None)

    def _fetch_nested_css_resources(
        self,
        main_resource: Resource,
        resource_map: dict[str, Resource],
        base_url: str,
    ) -> None:
        """Resolve nested CSS URLs/imports iteratively up to the configured depth."""
        css_resources = [
            resource
            for resource in self._unique_resources(resource_map.values())
            if resource.resource_type == ResourceType.CSS
        ]
        if main_resource.resource_type == ResourceType.CSS:
            css_resources.append(main_resource)

        for _ in range(self.config.max_import_depth):
            pending = self._find_missing_css_urls(css_resources, resource_map, base_url)
            if not pending:
                return
            added = self._fetch_resource_batch(pending, base_url, resource_map)
            if not added:
                return
            css_resources.extend(resource for resource in added if resource.resource_type == ResourceType.CSS)

    def _find_missing_css_urls(
        self,
        css_resources: Iterable[Resource],
        resource_map: dict[str, Resource],
        fallback_base: str,
    ) -> set[str]:
        """Collect uncached CSS URLs with each stylesheet's own base URL."""
        pending: set[str] = set()
        for resource in css_resources:
            css_base = resource.url or fallback_base
            for raw_url in self.css_processor.extract_urls(self._resource_text(resource)):
                normalized = self.resource_manager.normalize_url(raw_url, css_base)
                if normalized not in resource_map and not normalized.lower().startswith(("data:", "cid:")):
                    pending.add(normalized)
        return pending

    def _fetch_resource_batch(
        self,
        urls: set[str],
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> list[Resource]:
        """Fetch unresolved CSS dependencies and add successful responses to the map."""
        fetched = self.resource_manager.process_resources_parallel(sorted(urls), base_url)
        added = [resource for resource in fetched.values() if resource is not None]
        resource_map.update((url, resource) for url, resource in fetched.items() if resource is not None)
        return added

    def _apply_resource_plugins(self, resource_map: dict[str, Resource]) -> None:
        """Apply resource hooks once per unique MIME resource and preserve aliases."""
        transformed: dict[int, Resource] = {}
        for key, resource in list(resource_map.items()):
            replacement = transformed.get(id(resource))
            if replacement is None:
                replacement = resource
                for plugin in self.config.plugins:
                    replacement = plugin.process_resource(replacement)
                    if not isinstance(replacement, Resource):
                        raise TypeError("plugin process_resource hooks must return Resource")
                transformed[id(resource)] = replacement
            resource_map[key] = replacement

    def _rewrite_css_resources(
        self,
        resource_map: dict[str, Resource],
        base_url: str,
    ) -> None:
        """Rewrite CSS assets recursively, guarding cycles and import depth."""
        state = _CSSRewriteState(resource_map, base_url)
        for resource in self._unique_resources(resource_map.values()):
            if resource.resource_type == ResourceType.CSS:
                self._rewrite_css_resource(resource, state, 0)

    def _rewrite_css_resource(
        self,
        resource: Resource,
        state: _CSSRewriteState,
        depth: int,
    ) -> None:
        """Rewrite one stylesheet after rewriting mapped CSS dependencies."""
        identity = id(resource)
        if identity in state.processed or identity in state.visiting:
            return
        state.visiting.add(identity)
        css_text = self._resource_text(resource)
        css_base = resource.url or state.fallback_base
        if depth < self.config.max_import_depth:
            self._rewrite_css_dependencies(css_text, state, css_base, depth)

        safe_map = {key: value for key, value in state.resource_map.items() if id(value) not in state.visiting}
        rewritten = self.css_processor.rewrite_css(
            css_text,
            css_base,
            safe_map,
            depth=depth,
        )
        resource.content = rewritten.encode(self.config.encoding, errors="replace")
        resource.size = len(resource.content)
        state.visiting.remove(identity)
        state.processed.add(identity)

    def _rewrite_css_dependencies(
        self,
        css_text: str,
        state: _CSSRewriteState,
        css_base: str,
        depth: int,
    ) -> None:
        """Rewrite all mapped stylesheet dependencies before their parent."""
        for raw_url in self.css_processor.extract_urls(css_text):
            normalized = self.resource_manager.normalize_url(raw_url, css_base)
            dependency = state.resource_map.get(normalized)
            if dependency and dependency.resource_type == ResourceType.CSS:
                self._rewrite_css_resource(dependency, state, depth + 1)

    def _resource_text(self, resource: Resource) -> str:
        """Decode textual MIME content using its part-level charset and BOM."""
        content_type = resource.headers.get("Content-Type", resource.content_type)
        return self.parser.decode_text(resource.content, content_type)

    def _validate_output(
        self,
        output: bytes,
        resource_type: ResourceType,
    ) -> dict[str, Any]:
        """Validate generated HTML/CSS and return actionable diagnostics."""
        if not self.config.validation:
            return {"enabled": False, "valid": True, "errors": []}
        if resource_type == ResourceType.HTML:
            text = self.parser.decode_text(output, self.config.encoding)
            errors = self.html_processor.validate_html(text)
        elif resource_type == ResourceType.CSS:
            text = self.parser.decode_text(output, self.config.encoding)
            rules = self.css_processor.parse_css(text)
            errors = [str(rule) for rule in rules if getattr(rule, "type", "") == "error"]
        else:
            errors = []
        return {"enabled": True, "valid": not errors, "errors": errors}

    def _fallback_conversion(
        self,
        data: bytes,
        error: Exception,
    ) -> tuple[bytes, dict[str, Any]]:
        """Recover invalid MHTML into safe HTML rather than returning a broken MIME file."""
        text = data.decode(self.config.encoding, errors="replace")
        start = text.lower().find("<html")
        end = text.lower().rfind("</html>")
        if 0 <= start <= end:
            recovered = text[start : end + len("</html>")]
            safe_html = self.html_processor.rewrite_html(
                recovered,
                "http://localhost/",
                {},
            )
            output = safe_html.encode(self.config.encoding, errors="replace")
        else:
            safe_text = html_stdlib.escape(text, quote=False)
            output = (
                f'<!doctype html><html><head><meta charset="utf-8"></head><body><pre>{safe_text}</pre></body></html>'
            ).encode()
        validation = self._validate_output(output, ResourceType.HTML)
        metadata = {
            "original_size": len(data),
            "output_size": len(output),
            "resource_count": 0,
            "main_resource_type": "other",
            "content_type": "text/html",
            "profile": self.config.profile.value,
            "metadata": {},
            "config": self.config.to_dict(),
            "validation": validation,
            "output_format": self.config.output_format,
            "error": str(error),
        }
        self.logger.warning(f"Returning recovered HTML after parse error: {error}")
        self.logger.log_progress("Conversion recovered", 1.0)
        return output, metadata

    @staticmethod
    def _unique_resources(resources: Iterable[Resource]) -> list[Resource]:
        """Return resources once even when the map has URL and CID aliases."""
        unique: list[Resource] = []
        seen: set[int] = set()
        for resource in resources:
            identity = id(resource)
            if identity not in seen:
                unique.append(resource)
                seen.add(identity)
        return unique


# ------------------------------ Command-line interface ------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    """Create the standalone script's command-line argument parser."""
    parser = argparse.ArgumentParser(description="Convert an MHTML archive to a self-contained HTML document.")
    parser.add_argument("input", type=Path, help="Input .mhtml or .mht file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output HTML path (default: beside input, with a .html suffix)",
    )
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in ConversionProfile],
        default=ConversionProfile.STANDARD.value,
        help="Conversion preset (default: standard)",
    )
    parser.add_argument(
        "--fetch-external",
        action="store_true",
        help="Allow fetching referenced public HTTP(S) assets",
    )
    parser.add_argument(
        "--allow-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="Restrict external fetches to this domain; may be repeated",
    )
    parser.add_argument(
        "--block-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="Block external fetches to this domain; may be repeated",
    )
    parser.add_argument(
        "--resource-handling",
        choices=["inline", "external", "mixed"],
        help="Override the profile's resource handling strategy",
    )
    parser.add_argument(
        "--minify",
        action="store_true",
        help="Minify HTML and CSS output",
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
        help="Set Loguru's minimum log level",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print conversion progress to standard error",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone converter and save output beside the input by default."""
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    try:
        config = ConverterConfig(
            profile=ConversionProfile(arguments.profile),
            allowed_domains=arguments.allow_domain,
            blocked_domains=arguments.block_domain,
        )
        config.fetch_external = arguments.fetch_external or config.fetch_external
        if arguments.resource_handling is not None:
            config.resource_handling = arguments.resource_handling
        if arguments.minify:
            config.minify_html = True
            config.minify_css = True
        if arguments.log_level is not None:
            config.log_level = arguments.log_level
        if arguments.progress:
            config.progress_callback = lambda message, value: print(f"{value:6.1%} {message}", file=sys.stderr)

        with MHTMLConverter(config) as converter:
            output_path, metadata = converter.convert_file(
                arguments.input,
                output_path=arguments.output,
            )
    except (OSError, ValueError) as error:
        print(f"mhtml-converter: {error}", file=sys.stderr)
        return 2

    print(f"Saved {metadata['output_size']} bytes to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
