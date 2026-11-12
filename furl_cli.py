#!/data/data/com.termux/files/usr/bin/env python

import abc
import re
import urllib.parse
import warnings
from copy import deepcopy
from posixpath import normpath
from typing import Any, Callable, Final, Self

try:
    from icecream import ic
except ImportError:

    def ic(*a: Any) -> Any:
        return None if not a else (a[0] if len(a) == 1 else a)


from orderedmultidict import omdict1D

__title__: Final[str] = "furl"
__version__: Final[str] = "2.1.4"
__license__: Final[str] = "Unlicense"
__author__: Final[str] = "Ansgar Grunseid"
__contact__: Final[str] = "grunseid@gmail.com"
__url__: Final[str] = "https://github.com/gruns/furl"
__copyright__: Final[str] = "Copyright Ansgar Grunseid"
__description__: Final[str] = "URL manipulation made simple."
absent: Final[Any] = object()
DEFAULT_PORTS: Final[dict[str, int]] = {
    "acap": 674,
    "afp": 548,
    "dict": 2628,
    "dns": 53,
    "ftp": 21,
    "git": 9418,
    "gopher": 70,
    "hdl": 2641,
    "http": 80,
    "https": 443,
    "imap": 143,
    "ipp": 631,
    "ipps": 631,
    "irc": 194,
    "ircs": 6697,
    "ldap": 389,
    "ldaps": 636,
    "mms": 1755,
    "msrp": 2855,
    "mtqp": 1038,
    "nfs": 111,
    "nntp": 119,
    "nntps": 563,
    "pop": 110,
    "prospero": 1525,
    "redis": 6379,
    "rsync": 873,
    "rtsp": 554,
    "rtsps": 322,
    "rtspu": 5005,
    "sftp": 22,
    "sip": 5060,
    "sips": 5061,
    "smb": 445,
    "snews": 563,
    "snmp": 161,
    "ssh": 22,
    "svn": 3690,
    "telnet": 23,
    "tftp": 69,
    "ventrilo": 3784,
    "vnc": 5900,
    "wais": 210,
    "ws": 80,
    "wss": 443,
    "xmpp": 5222,
}
PERCENT_REGEX: Final[str] = r"\%[a-fA-F\d][a-fA-F\d]"
INVALID_HOST_CHARS: Final[str] = "!@#$%^&'\"*()+=:;/"


def callable_attr(obj: Any, attr: str) -> bool:
    return hasattr(obj, attr) and callable(getattr(obj, attr))


def is_iterable_but_not_string(v: Any) -> bool:
    return callable_attr(v, "__iter__") and not isinstance(v, (str, bytes))


def lget(lst: Any, index: int, default: Any = None) -> Any:
    try:
        return lst[index]
    except IndexError:
        return default


def attemptstr(o: Any) -> Any:
    try:
        return str(o)
    except Exception:
        return o


def utf8(o: Any, default: Any = absent) -> Any:
    try:
        if isinstance(o, str):
            return o.encode("utf8")
        if hasattr(o, "encode"):
            return o.encode("utf8")
        return o
    except Exception:
        return o if default is absent else default


def non_string_iterable(o: Any) -> bool:
    return callable_attr(o, "__iter__") and not isinstance(o, (str, bytes))


def idna_encode(o: Any) -> Any:
    if callable_attr(o, "encode"):
        return str(o.encode("idna").decode("utf8"))
    return o


def idna_decode(o: Any) -> Any:
    o_utf8 = utf8(o)
    if callable_attr(o_utf8, "decode"):
        return o_utf8.decode("idna")
    return o


def is_valid_port(port: Any) -> bool:
    port_str = str(port)
    return port_str.isdigit() and 0 < int(port_str) <= 65535


def static_vars(**kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        for key, value in kwargs.items():
            setattr(func, key, value)
        return func

    return decorator


def create_quote_fn(safe_charset: str, quote_plus: bool) -> Callable[[Any, Any], str]:
    def quote_fn(s: Any, dont_quote: Any) -> str:
        if dont_quote is True:
            safe = safe_charset
        elif dont_quote is False:
            safe = ""
        else:
            safe = str(dont_quote)
        safe = "".join(set(safe) & set(safe_charset))
        quoted = urllib.parse.quote(str(s), safe)
        if quote_plus:
            quoted = quoted.replace("%20", "+")
        return quoted

    return quote_fn


@static_vars(regex=re.compile(r"^([\w%s]|(%s))*$" % (re.escape("-.~:@!$&'()*+,;="), PERCENT_REGEX)))
def is_valid_encoded_path_segment(segment: str) -> bool:
    return is_valid_encoded_path_segment.regex.match(segment) is not None


@static_vars(regex=re.compile(r"^([\w%s]|(%s))*$" % (re.escape("-.~:@!$&'()*+,;/?"), PERCENT_REGEX)))
def is_valid_encoded_query_key(key: str) -> bool:
    return is_valid_encoded_query_key.regex.match(key) is not None


@static_vars(regex=re.compile(r"^([\w%s]|(%s))*$" % (re.escape("-.~:@!$&'()*+,;/?="), PERCENT_REGEX)))
def is_valid_encoded_query_value(value: str) -> bool:
    return is_valid_encoded_query_value.regex.match(value) is not None


@static_vars(regex=re.compile(r"[a-zA-Z][a-zA-Z\-\.\+]*"))
def is_valid_scheme(scheme: str) -> bool:
    return is_valid_scheme.regex.match(scheme) is not None


@static_vars(regex=re.compile("[%s]" % re.escape(INVALID_HOST_CHARS)))
def is_valid_host(hostname: str) -> bool:
    toks = hostname.split(".")
    if toks[-1] == "":
        toks.pop()
    for tok in toks:
        if is_valid_host.regex.search(tok) is not None:
            return False
    return "" not in toks


def get_scheme(url: str) -> str | None:
    if url.startswith(":"):
        return ""
    no_fragment = url.split("#", 1)[0]
    no_query = no_fragment.split("?", 1)[0]
    no_path_or_netloc = no_query.split("/", 1)[0]
    scheme = url[: max(0, no_path_or_netloc.find(":"))] or None
    if scheme is not None and not is_valid_scheme(scheme):
        return None
    return scheme


def strip_scheme(url: str) -> str:
    scheme = get_scheme(url) or ""
    url = url[len(scheme) :]
    url = url.removeprefix(":")
    return url


def set_scheme(url: str, scheme: str | None) -> str:
    after_scheme = strip_scheme(url)
    if scheme is None:
        return after_scheme
    else:
        return f"{scheme}:{after_scheme}"


def has_netloc(url: str) -> bool:
    scheme = get_scheme(url)
    return url.startswith("//" if scheme is None else f"{scheme}://")


def urlsplit(url: str) -> urllib.parse.SplitResult:
    original_scheme = get_scheme(url)
    if original_scheme is not None:
        url = set_scheme(url, "http")
    scheme, netloc, path, query, fragment = urllib.parse.urlsplit(url)
    after_scheme = strip_scheme(url)
    if after_scheme.startswith("//"):
        netloc = netloc or ""
    else:
        netloc = ""
    scheme = original_scheme or ""
    return urllib.parse.SplitResult(scheme, netloc, path, query, fragment)


def urljoin(base: str, url: str) -> str:
    base_scheme = get_scheme(base) if has_netloc(base) else None
    url_scheme = get_scheme(url) if has_netloc(url) else None
    if base_scheme is not None:
        root = set_scheme(base, "http")
    else:
        root = base
    joined = urllib.parse.urljoin(root, url)
    new_scheme = url_scheme if url_scheme is not None else base_scheme
    if new_scheme is not None and has_netloc(joined):
        joined = set_scheme(joined, new_scheme)
    return joined


def join_path_segments(*args: list[str]) -> list[str]:
    finals: list[str] = []
    for segments in args:
        if not segments or segments == [""]:
            continue
        elif not finals:
            finals.extend(segments)
        else:
            if finals[-1] == "" and (segments[0] != "" or len(segments) > 1):
                finals.pop(-1)
            elif finals[-1] != "" and segments[0] == "" and len(segments) > 1:
                segments = segments[1:]
            finals.extend(segments)
    return finals


def remove_path_segments(segments: list[str], remove: list[str]) -> list[str]:
    if segments == [""]:
        segments.append("")
    if remove == [""]:
        remove.append("")
    ret: list[str] | None = None
    if remove == segments:
        ret = []
    elif len(remove) > len(segments):
        ret = segments
    else:
        toremove = list(remove)
        if len(remove) > 1 and remove[0] == "":
            toremove.pop(0)
        if toremove and toremove == segments[-1 * len(toremove) :]:
            ret = segments[: len(segments) - len(toremove)]
            if remove[0] != "" and ret:
                ret.append("")
        else:
            ret = segments
    return ret or []


def quacks_like_a_path_with_segments(obj: Any) -> bool:
    return hasattr(obj, "segments") and is_iterable_but_not_string(obj.segments)


class Path:
    SAFE_SEGMENT_CHARS: Final[str] = ":@-._~!$&'()*+,;="

    def __init__(
        self,
        path: Any = "",
        force_absolute: Callable[[Any], bool] = lambda _: False,
        strict: bool = False,
    ) -> None:
        self.segments: list[str] = []
        self.strict = strict
        self._isabsolute = False
        self._force_absolute = force_absolute
        self.load(path)

    def load(self, path: Any) -> Self:
        if not path:
            segments: list[str] = []
        elif quacks_like_a_path_with_segments(path):
            segments = path.segments
        elif is_iterable_but_not_string(path):
            segments = list(path)
        else:
            segments = self._segments_from_path(path)
        if self._force_absolute(self):
            self._isabsolute = True if segments else False
        else:
            self._isabsolute = bool(segments and segments[0] == "")
        if self.isabsolute and len(segments) > 1 and segments[0] == "":
            segments.pop(0)
        self.segments = segments
        return self

    def add(self, path: Any) -> Self:
        if quacks_like_a_path_with_segments(path):
            newsegments = path.segments
        elif is_iterable_but_not_string(path):
            newsegments = list(path)
        else:
            newsegments = self._segments_from_path(path)
        if self.segments == [""] and newsegments and newsegments[0] != "":
            newsegments.insert(0, "")
        segments = self.segments.copy()
        if self.isabsolute and self.segments and self.segments[0] != "":
            segments.insert(0, "")
        self.load(join_path_segments(segments, newsegments))
        return self

    def set(self, path: Any) -> Self:
        self.load(path)
        return self

    def remove(self, path: Any) -> Self:
        if path is True:
            self.load("")
        else:
            if is_iterable_but_not_string(path):
                segments = list(path)
            else:
                segments = self._segments_from_path(path)
            base = ([""] if self.isabsolute else []) + self.segments
            self.load(remove_path_segments(base, segments))
        return self

    def normalize(self) -> Self:
        if str(self):
            normalized = normpath(str(self)) + ("/" * self.isdir)
            if normalized.startswith("//"):
                normalized = "/" + normalized.lstrip("/")
            self.load(normalized)
        return self

    def asdict(self) -> dict[str, Any]:
        return {
            "encoded": str(self),
            "isdir": self.isdir,
            "isfile": self.isfile,
            "segments": self.segments,
            "isabsolute": self.isabsolute,
        }

    @property
    def isabsolute(self) -> bool:
        if self._force_absolute(self):
            return True
        return self._isabsolute

    @isabsolute.setter
    def isabsolute(self, isabsolute: bool) -> None:
        if self._force_absolute(self):
            s = (
                "Path.isabsolute is True and read-only for URLs with a netloc"
                " (a username, password, host, and/or port). A URL path must "
                "start with a '/' to separate itself from a netloc."
            )
            raise AttributeError(s)
        self._isabsolute = isabsolute

    @property
    def isdir(self) -> bool:
        return self.segments == [] or (bool(self.segments) and self.segments[-1] == "")

    @property
    def isfile(self) -> bool:
        return not self.isdir

    def __truediv__(self, path: Any) -> Self:
        copy = deepcopy(self)
        return copy.add(path)

    def __eq__(self, other: object) -> bool:
        return str(self) == str(other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __bool__(self) -> bool:
        return len(self.segments) > 0

    def __str__(self) -> str:
        segments = list(self.segments)
        if self.isabsolute:
            if not segments:
                segments = ["", ""]
            else:
                segments.insert(0, "")
        return self._path_from_segments(segments)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self!s}')"

    def _segments_from_path(self, path: str) -> list[str]:
        segments: list[Any] = []
        for segment in path.split("/"):
            if not is_valid_encoded_path_segment(segment):
                segment = urllib.parse.quote(utf8(segment))
                if self.strict:
                    s = "Improperly encoded path string received: '%s'. Proceeding, but did you mean '%s'?" % (
                        path,
                        self._path_from_segments(segments),
                    )
                    warnings.warn(s, UserWarning)
            segments.append(utf8(segment))
        del segment
        segments_decoded = [segment.decode("utf8") if isinstance(segment, bytes) else segment for segment in segments]
        return [urllib.parse.unquote(segment) for segment in segments_decoded]

    def _path_from_segments(self, segments: list[str]) -> str:
        safe_segments = [urllib.parse.quote(utf8(attemptstr(segment)), self.SAFE_SEGMENT_CHARS) for segment in segments]
        return "/".join(safe_segments)


class PathCompositionInterface(abc.ABC):
    def __init__(self, strict: bool = False) -> None:
        self._path = Path(force_absolute=self._force_absolute, strict=strict)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def pathstr(self) -> str:
        s = (
            "furl.pathstr is deprecated. Use str(furl.path) instead. There "
            "should be one, and preferably only one, obvious way to serialize"
            " a Path object to a string."
        )
        warnings.warn(s, DeprecationWarning)
        return str(self._path)

    @abc.abstractmethod
    def _force_absolute(self, path: Any) -> bool:
        pass

    def __setattr__(self, attr: str, value: Any) -> bool:
        if attr == "_path":
            self.__dict__[attr] = value
            return True
        elif attr == "path":
            self._path.load(value)
            return True
        return False


class URLPathCompositionInterface(PathCompositionInterface):
    def __init__(self, strict: bool = False) -> None:
        PathCompositionInterface.__init__(self, strict=strict)

    def _force_absolute(self, path: Any) -> bool:
        return bool(path) and bool(getattr(self, "netloc", None))


class FragmentPathCompositionInterface(PathCompositionInterface):
    def __init__(self, strict: bool = False) -> None:
        PathCompositionInterface.__init__(self, strict=strict)

    def _force_absolute(self, path: Any) -> bool:
        return False


class Query:
    SAFE_KEY_CHARS: Final[str] = "/?:@-._~!$'()*+,;"
    SAFE_VALUE_CHARS: Final[str] = SAFE_KEY_CHARS + "="

    def __init__(self, query: Any = "", strict: bool = False) -> None:
        self.strict = strict
        self._params = omdict1D()
        self.load(query)

    def load(self, query: Any) -> Self:
        items = self._items(query)
        self.params.load(items)
        return self

    def add(self, args: Any) -> Self:
        for param, value in self._items(args):
            self.params.add(param, value)
        return self

    def set(self, mapping: Any) -> Self:
        self.params.updateall(mapping)
        return self

    def remove(self, query: Any) -> Self:
        if query is True:
            self.load("")
            return self
        items = [query]
        if callable_attr(query, "items"):
            items = self._items(query)
        elif non_string_iterable(query):
            items = list(query)
        for item in items:
            if non_string_iterable(item) and len(item) == 2:
                key, value = item
                self.params.popvalue(key, value, None)
            else:
                key = item
                self.params.pop(key, None)
        return self

    @property
    def params(self) -> Any:
        return self._params

    @params.setter
    def params(self, params: Any) -> None:
        items = self._items(params)
        self._params.clear()
        for key, value in items:
            self._params.add(key, value)

    def encode(
        self,
        delimiter: str = "&",
        quote_plus: bool = True,
        dont_quote: Any = "",
        delimeter: Any = absent,
    ) -> str:
        if delimeter is not absent:
            delimiter = delimeter
        quote_key = create_quote_fn(self.SAFE_KEY_CHARS, quote_plus)
        quote_value = create_quote_fn(self.SAFE_VALUE_CHARS, quote_plus)
        pairs = []
        for key, value in self.params.iterallitems():
            utf8key = utf8(key, utf8(attemptstr(key)))
            quoted_key = quote_key(utf8key, dont_quote)
            if value is None:
                pair = quoted_key
            else:
                utf8value = utf8(value, utf8(attemptstr(value)))
                quoted_value = quote_value(utf8value, dont_quote)
                if not quoted_key:
                    quoted_value = quoted_value.replace("%3D", "=")
                pair = f"{quoted_key}={quoted_value}"
            pairs.append(pair)
        return delimiter.join(pairs)

    def asdict(self) -> dict[str, Any]:
        return {
            "encoded": str(self),
            "params": list(self.params.allitems()),
        }

    def __eq__(self, other: object) -> bool:
        return str(self) == str(other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __bool__(self) -> bool:
        return len(self.params) > 0

    def __str__(self) -> str:
        return self.encode()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self!s}')"

    def _items(self, items: Any) -> list[tuple[Any, Any]]:
        if not items:
            items = []
        elif callable_attr(items, "allitems"):
            items = list(items.allitems())
        elif callable_attr(items, "iterallitems"):
            items = list(items.iterallitems())
        elif callable_attr(items, "items"):
            items = list(items.items())
        elif callable_attr(items, "iteritems"):
            items = list(items.items())
        elif isinstance(items, (str, bytes)):
            items = self._extract_items_from_querystr(str(items))
        else:
            items = list(items)
        return items

    def _extract_items_from_querystr(self, querystr: str) -> list[tuple[str, str | None]]:
        items = []
        pairstrs = querystr.split("&")
        pairs = [item.split("=", 1) for item in pairstrs]
        padded_pairs = [(p[0], lget(p, 1, "")) for p in pairs]
        for pairstr, (key, value) in zip(pairstrs, padded_pairs):
            valid_key = is_valid_encoded_query_key(key)
            valid_value = is_valid_encoded_query_value(value)
            if self.strict and (not valid_key or not valid_value):
                msg = "Incorrectly percent encoded query string received: '%s'. Proceeding, but did you mean '%s'?" % (
                    querystr,
                    urllib.parse.urlencode(padded_pairs),
                )
                warnings.warn(msg, UserWarning)
            key_decoded = urllib.parse.unquote(key.replace("+", " "))
            if key == pairstr:
                value_decoded = None
            else:
                value_decoded = urllib.parse.unquote(value.replace("+", " "))
            items.append((key_decoded, value_decoded))
        return items


class QueryCompositionInterface(abc.ABC):
    def __init__(self, strict: bool = False) -> None:
        self._query = Query(strict=strict)

    @property
    def query(self) -> Query:
        return self._query

    @property
    def querystr(self) -> str:
        s = (
            "furl.querystr is deprecated. Use str(furl.query) instead. There "
            "should be one, and preferably only one, obvious way to serialize"
            " a Query object to a string."
        )
        warnings.warn(s, DeprecationWarning)
        return str(self._query)

    @property
    def args(self) -> Any:
        return self._query.params

    def __setattr__(self, attr: str, value: Any) -> bool:
        if attr in ("args", "query"):
            self._query.load(value)
            return True
        return False


class Fragment(FragmentPathCompositionInterface, QueryCompositionInterface):
    def __init__(self, fragment: Any = "", strict: bool = False) -> None:
        FragmentPathCompositionInterface.__init__(self, strict=strict)
        QueryCompositionInterface.__init__(self, strict=strict)
        self.strict = strict
        self.separator = True
        self.load(fragment)

    def load(self, fragment: Any) -> None:
        self.path.load("")
        self.query.load("")
        if fragment is None:
            fragment = ""
        toks = str(fragment).split("?", 1)
        if len(toks) == 0:
            self._path.load("")
            self._query.load("")
        elif len(toks) == 1:
            if "=" in str(fragment):
                self._query.load(fragment)
            else:
                self._path.load(fragment)
        elif "=" in toks[1]:
            self._path.load(toks[0])
            self._query.load(toks[1])
        else:
            self._path.load(fragment)

    def add(self, path: Any = absent, args: Any = absent) -> Self:
        if path is not absent:
            self.path.add(path)
        if args is not absent:
            self.query.add(args)
        return self

    def set(self, path: Any = absent, args: Any = absent, separator: Any = absent) -> Self:
        if path is not absent:
            self.path.load(path)
        if args is not absent:
            self.query.load(args)
        if separator is True or separator is False:
            self.separator = separator
        return self

    def remove(self, fragment: Any = absent, path: Any = absent, args: Any = absent) -> Self:
        if fragment is True:
            self.load("")
        if path is not absent:
            self.path.remove(path)
        if args is not absent:
            self.query.remove(args)
        return self

    def asdict(self) -> dict[str, Any]:
        return {
            "encoded": str(self),
            "separator": self.separator,
            "path": self.path.asdict(),
            "query": self.query.asdict(),
        }

    def __eq__(self, other: object) -> bool:
        return str(self) == str(other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __setattr__(self, attr: str, value: Any) -> None:
        if not PathCompositionInterface.__setattr__(self, attr, value) and not QueryCompositionInterface.__setattr__(
            self, attr, value
        ):
            object.__setattr__(self, attr, value)

    def __bool__(self) -> bool:
        return bool(self.path) or bool(self.query)

    def __str__(self) -> str:
        path, query = str(self._path), str(self._query)
        if path and (not query or not self.separator):
            path = path.replace("%3F", "?")
        separator = "?" if path and query and self.separator else ""
        return path + separator + query

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self!s}')"


class FragmentCompositionInterface(abc.ABC):
    def __init__(self, strict: bool = False) -> None:
        self._fragment = Fragment(strict=strict)

    @property
    def fragment(self) -> Fragment:
        return self._fragment

    @property
    def fragmentstr(self) -> str:
        s = (
            "furl.fragmentstr is deprecated. Use str(furl.fragment) instead. "
            "There should be one, and preferably only one, obvious way to "
            "serialize a Fragment object to a string."
        )
        warnings.warn(s, DeprecationWarning)
        return str(self._fragment)

    def __setattr__(self, attr: str, value: Any) -> bool:
        if attr == "fragment":
            self.fragment.load(value)
            return True
        return False


class furl(URLPathCompositionInterface, QueryCompositionInterface, FragmentCompositionInterface):
    def __init__(
        self,
        url: Any = "",
        args: Any = absent,
        path: Any = absent,
        fragment: Any = absent,
        scheme: Any = absent,
        netloc: Any = absent,
        origin: Any = absent,
        fragment_path: Any = absent,
        fragment_args: Any = absent,
        fragment_separator: Any = absent,
        host: Any = absent,
        port: Any = absent,
        query: Any = absent,
        query_params: Any = absent,
        username: Any = absent,
        password: Any = absent,
        strict: bool = False,
    ) -> None:
        URLPathCompositionInterface.__init__(self, strict=strict)
        QueryCompositionInterface.__init__(self, strict=strict)
        FragmentCompositionInterface.__init__(self, strict=strict)
        self.strict = strict
        self.load(url)
        self.set(
            args=args,
            path=path,
            fragment=fragment,
            scheme=scheme,
            netloc=netloc,
            origin=origin,
            fragment_path=fragment_path,
            fragment_args=fragment_args,
            fragment_separator=fragment_separator,
            host=host,
            port=port,
            query=query,
            query_params=query_params,
            username=username,
            password=password,
        )

    def load(self, url: Any) -> Self:
        self.username = self.password = None
        self._host = self._port = self._scheme = None
        if url is None:
            url = ""
        if not isinstance(url, (str, bytes)):
            url = str(url)
        tokens = urlsplit(url)
        self.netloc = tokens.netloc
        self.scheme = tokens.scheme
        if not self.port:
            self._port = DEFAULT_PORTS.get(self.scheme or "")
        self.path.load(tokens.path)
        self.query.load(tokens.query)
        self.fragment.load(tokens.fragment)
        return self

    @property
    def scheme(self) -> str | None:
        return self._scheme

    @scheme.setter
    def scheme(self, scheme: Any) -> None:
        if callable_attr(scheme, "lower"):
            scheme = scheme.lower()
        self._scheme = scheme

    @property
    def host(self) -> str | None:
        return self._host

    @host.setter
    def host(self, host: Any) -> None:
        urllib.parse.urlsplit(f"http://{host}/")
        resembles_ipv6_literal = host is not None and lget(host, 0) == "[" and ":" in host and lget(host, -1) == "]"
        if host is not None and not resembles_ipv6_literal and not is_valid_host(host):
            errmsg = (
                "Invalid host '%s'. Host strings must have at least one "
                "non-period character, can't contain any of '%s', and can't "
                "have adjacent periods."
            )
            raise ValueError(errmsg % (host, INVALID_HOST_CHARS))
        if callable_attr(host, "lower"):
            host = host.lower()
        if callable_attr(host, "startswith") and host.startswith("xn--"):
            host = idna_decode(host)
        self._host = host

    @property
    def port(self) -> int | None:
        return self._port or DEFAULT_PORTS.get(self.scheme or "")

    @port.setter
    def port(self, port: Any) -> None:
        if port is None:
            self._port = DEFAULT_PORTS.get(self.scheme or "")
        elif is_valid_port(port):
            self._port = int(str(port))
        else:
            msg = f"Invalid port '{port}'."
            raise ValueError(msg)

    @property
    def netloc(self) -> str:
        userpass = urllib.parse.quote(utf8(self.username) or "", safe="")
        if getattr(self, "password", None) is not None:
            userpass += ":" + urllib.parse.quote(utf8(self.password), safe="")
        if userpass or getattr(self, "username", None) is not None:
            userpass += "@"
        netloc = idna_encode(self.host)
        if self.port and self.port != DEFAULT_PORTS.get(self.scheme or ""):
            netloc = (netloc or "") + (":" + str(self.port))
        if userpass or netloc:
            netloc = (userpass or "") + (netloc or "")
        return netloc

    @netloc.setter
    def netloc(self, netloc: str | None) -> None:
        urllib.parse.urlsplit(f"http://{netloc}/")
        username = password = host = port = None
        if netloc and "@" in netloc:
            userpass, netloc = netloc.split("@", 1)
            if ":" in userpass:
                username, password = userpass.split(":", 1)
            else:
                username = userpass
        if netloc and ":" in netloc:
            if "]" in netloc:
                colonpos, bracketpos = netloc.rfind(":"), netloc.rfind("]")
                if colonpos > bracketpos and colonpos != bracketpos + 1:
                    msg = f"Invalid netloc '{netloc}'."
                    raise ValueError(msg)
                elif colonpos > bracketpos and colonpos == bracketpos + 1:
                    host, port = netloc.rsplit(":", 1)
                else:
                    host = netloc
            else:
                host, port = netloc.rsplit(":", 1)
        else:
            host = netloc
        self.port = port
        self.host = host
        self.username = None if username is None else urllib.parse.unquote(username)
        self.password = None if password is None else urllib.parse.unquote(password)

    @property
    def origin(self) -> str:
        port = ""
        scheme = self.scheme or ""
        host = idna_encode(self.host) or ""
        if self.port and self.port != DEFAULT_PORTS.get(self.scheme or ""):
            port = f":{self.port}"
        return f"{scheme}://{host}{port}"

    @origin.setter
    def origin(self, origin: str | None) -> None:
        if origin is None:
            self.scheme = self.netloc = None
        else:
            toks = origin.split("://", 1)
            if len(toks) == 1:
                host_port = origin
            else:
                self.scheme, host_port = toks
            if ":" in host_port:
                self.host, self.port = host_port.split(":", 1)
            else:
                self.host = host_port

    @property
    def url(self) -> str:
        return self.tostr()

    @url.setter
    def url(self, url: Any) -> None:
        self.load(url)

    def add(
        self,
        args: Any = absent,
        path: Any = absent,
        fragment_path: Any = absent,
        fragment_args: Any = absent,
        query_params: Any = absent,
    ) -> Self:
        if args is not absent and query_params is not absent:
            s = (
                "Both <args> and <query_params> provided to furl.add(). "
                "<args> is a shortcut for <query_params>, not to be used "
                "with <query_params>. See furl.add() documentation for more "
                "details."
            )
            warnings.warn(s, UserWarning)
        if path is not absent:
            self.path.add(path)
        if args is not absent:
            self.query.add(args)
        if query_params is not absent:
            self.query.add(query_params)
        if fragment_path is not absent or fragment_args is not absent:
            self.fragment.add(path=fragment_path, args=fragment_args)
        return self

    def set(
        self,
        args: Any = absent,
        path: Any = absent,
        fragment: Any = absent,
        scheme: Any = absent,
        netloc: Any = absent,
        origin: Any = absent,
        fragment_path: Any = absent,
        fragment_args: Any = absent,
        fragment_separator: Any = absent,
        host: Any = absent,
        port: Any = absent,
        query: Any = absent,
        query_params: Any = absent,
        username: Any = absent,
        password: Any = absent,
    ) -> Self:
        if args is not absent and query_params is not absent:
            warnings.warn(
                "Both <args> and <query_params> provided to furl.set(). "
                "<args> is a shortcut for <query_params>, not to be used "
                "with <query_params>. See furl.set() documentation for more details.",
                UserWarning,
            )
        if origin is not absent:
            self.origin = origin
        if scheme is not absent:
            self.scheme = scheme
        if netloc is not absent:
            self.netloc = netloc
        if host is not absent:
            self.host = host
        if port is not absent:
            self.port = port
        if username is not absent:
            self.username = username
        if password is not absent:
            self.password = password
        if path is not absent:
            self.path.set(path)
        if query is not absent:
            self.query.set(query)
        if args is not absent:
            self.query.set(args)
        if query_params is not absent:
            self.query.set(query_params)
        if fragment is not absent:
            self.fragment.set(fragment)
        if fragment_path is not absent or fragment_args is not absent or fragment_separator is not absent:
            self.fragment.set(path=fragment_path, args=fragment_args, separator=fragment_separator)
        return self

    def remove(
        self,
        args: Any = absent,
        path: Any = absent,
        fragment: Any = absent,
        fragment_path: Any = absent,
        fragment_args: Any = absent,
        query_params: Any = absent,
    ) -> Self:
        if path is not absent:
            self.path.remove(path)
        if args is not absent:
            self.query.remove(args)
        if query_params is not absent:
            self.query.remove(query_params)
        if fragment is not absent:
            self.fragment.remove(fragment)
        if fragment_path is not absent or fragment_args is not absent:
            self.fragment.remove(path=fragment_path, args=fragment_args)
        return self

    def copy(self) -> Self:
        return deepcopy(self)

    def tostr(self) -> str:
        res = ""
        if self.scheme is not None:
            res += f"{self.scheme}:"
        if self.netloc or self.scheme is not None:
            res += f"//{self.netloc or ''}"
        path_str = str(self.path)
        if self.netloc and path_str and not path_str.startswith("/"):
            path_str = "/" + path_str
        res += path_str
        query_str = str(self.query)
        if query_str:
            res += f"?{query_str}"
        fragment_str = str(self.fragment)
        if fragment_str:
            res += f"#{fragment_str}"
        return res

    def __setattr__(self, attr: str, value: Any) -> None:
        if (
            not URLPathCompositionInterface.__setattr__(self, attr, value)
            and not QueryCompositionInterface.__setattr__(self, attr, value)
            and not FragmentCompositionInterface.__setattr__(self, attr, value)
        ):
            object.__setattr__(self, attr, value)

    def __str__(self) -> str:
        return self.tostr()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self.tostr()}')"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.tostr() == other
        elif isinstance(other, furl):
            return self.tostr() == other.tostr()
        return False

    def __ne__(self, other: object) -> bool:
        return not self == other


if __name__ == "__main__":
    raise SystemExit(0)
