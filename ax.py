#!/data/data/com.termux/files/usr/bin/env python

import contextlib
import hashlib
import html
import os
import re
import sys
import tempfile
import time
import urllib.request
import urllib.robotparser
import webbrowser
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urljoin

import diskcache
import just
import justext
import langdetect
import lxml.html
import requests
import ujson as json
from lxml.html import fromstring
from metadate import parse_date
from tldextract import tldextract

__project__ = "auto_extract"
__version__ = "0.1.3"


class lazy_property:
    def __init__(self, func: Callable) -> None:
        self.__doc__ = func.__doc__
        self.func = func

    def __get__(self, obj: Any, cls: Any) -> Any:
        if obj is None:
            return self
        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value


def fscore(x: Any, y: Any) -> float:
    try:
        z = sum([w in y for w in x]) / len(x)
        z2 = sum([w in x for w in y]) / len(y)
        return (2 * z * z2) / (z + z2)
    except ZeroDivisionError:
        return 0


def normalize(s: str) -> str:
    return re.sub(r"\s+", lambda x: "\n" if "\n" in x.group(0) else " ", s).strip()


def get_text_and_tail(node: Any) -> str:
    text = node.text or ""
    tail = node.tail or ""
    return text + " " + tail


def get_full_text(tree: Any) -> str:
    return normalize("\n".join([get_text_and_tail(x) for x in tree.iter()]))


def extract_domain(url: str) -> tuple[str, str]:
    tld = tldextract.extract(url)
    name = ".".join([x for x in tld if x])
    protocol = url.split("//", 1)[0]
    return name, protocol + "//" + name


def extract_links(tree: Any, domain: str, only_this_domain: bool = True) -> list[str]:
    links = tree.xpath("//a/@href")
    links = [urljoin(domain, x) for x in links]
    if only_this_domain:
        links = list({x for x in links if domain in x})
    return links


def extract_headings(tree: Any) -> list[str]:
    for h in ["//h1", "//h2", "//h3"]:
        headings = [x.text_content() for x in tree.xpath(h)]
        if headings:
            return headings
    return []


def get_language(tree: Any, domain: Optional[str] = None, lang_default: str = "en") -> str:
    lang = None
    if lang is None and "lang" in tree.attrib:
        lang = tree.attrib["lang"]
    if lang is None:
        page_txt = " ".join(tree.xpath("//p/text()")) + " ".join(tree.xpath("//div/text()"))
        if not page_txt:
            page_txt = tree.text_content()
        with contextlib.suppress(langdetect.lang_detect_exception.LangDetectException):
            lang = langdetect.detect(page_txt)
    if lang is None and domain is not None:
        dom = domain.split(".")[-1]
        if dom == "com":
            return "en"
        elif dom == "nl":
            return "nl"
        elif dom == "fr":
            return "fr"
        elif dom == "de":
            return "de"
    if lang is None:
        return lang_default
    return lang[:2]


def generate_rule_dictionary() -> dict:
    headers = ["h{}".format(i) for i in range(1, 5)]
    tags: list[str] = []
    atts = ["id", "class", ""]
    res = {k: {kk: {} for kk in atts} for k in headers + tags}
    it = 0
    for h in headers:
        for k in atts:
            it += 1
            res[h][k]["title"] = it
            it += 1
            res[h][k][""] = it
            it += 1
        res[h][""]["title"] = it
        it += 1
        res[h][""][""] = it
    return res


def get_score_from_title_dict(node: Any, dc: dict) -> tuple[int, str]:
    tag_found = dc.get(node.tag, "")
    if tag_found:
        maxi = 100
        for attribute in node.attrib:
            key_found = dc[node.tag].get(attribute, "")
            if key_found:
                if "title" in node.attrib[attribute]:
                    maxi = min(maxi, dc[node.tag][attribute]["title"])
                else:
                    maxi = min(maxi, dc[node.tag][attribute][""])
        return maxi, node.text_content()
    else:
        return 101, ""


def get_meta_titles(tree: Any) -> list[str]:
    res: list[str] = []
    head = tree.find("head")
    if head is not None:
        for xp in [
            ".//title/text()",
            './/meta[contains(@name, "title")]/@content',
            './/meta[contains(@property, "title")]/@content',
        ]:
            res.extend(head.xpath(xp))
    return res


def sorted_title_candidates(tree: Any, rule_dict: dict) -> list[str]:
    mins = []
    for node in tree.iter():
        score, ele = get_score_from_title_dict(node, rule_dict)
        stripped_ele = ele.strip()
        if stripped_ele and score != 101:
            mins.append((score, stripped_ele))
    return [x[1] for x in sorted(mins)]


rule_dc = generate_rule_dictionary()


def get_title(tree: Any, url: Optional[str], rule_dict: dict = rule_dc) -> str:
    texts = sorted_title_candidates(tree, rule_dict)
    metas = get_meta_titles(tree)
    url = url or "./"
    proposed = url.rstrip("/").split("/")[-1].split(".")[0]
    proposed = proposed.replace("-", " ").strip()
    proposed = " ".join([x if x.upper() == x else x.title() for x in proposed.split(" ")])
    if proposed:
        metas.append(proposed)
    maxs = 0
    title = ""
    for x in texts:
        xs = x.lower().split()
        for y in metas:
            ys = y.lower().split()
            newm = fscore(xs, ys)
            if newm > maxs:
                maxs = newm
                title = x
    if maxs < 0.3 and metas:
        title = metas[0]
    return title.split("|")[0].strip()


def get_title_index(tree: Any, title: str) -> Optional[int]:
    title_index = None
    for num, node in enumerate(tree.iter()):
        if normalize(get_text_and_tail(node)) == title:
            title_index = num
    if title_index is None:
        title_set = set(title.split())
        for num, node in enumerate(tree.iter()):
            text_content = get_text_and_tail(node)
            if text_content and len(text_content) < 500:
                text_set = set(text_content.split())
                if fscore(title_set, text_set) > 0.5:
                    title_index = num
                    break
    return title_index


def general_ok_img(img_candidate: Any, original_url: str, wrong_imgs: list[str]) -> bool:
    link = None
    if img_candidate.tag == "img":
        if "src" not in img_candidate.attrib:
            return False
        link = img_candidate.attrib["src"]
    elif "content" in img_candidate.attrib:
        link = img_candidate.attrib["content"]
    elif "style" in img_candidate.attrib:
        tmp = re.findall(r"background-image:[ ]*url\((http[^)]+)", img_candidate.attrib["style"])
        if tmp:
            link = tmp[0]
    if link is None:
        return False
    if len(link) > 1000:
        return False
    if not urljoin(original_url, link).startswith("http"):
        return False
    return not any(any(w in img_candidate.attrib[a] for w in wrong_imgs) for a in img_candidate.attrib)


def dimensions_ok(img_candidate: Any) -> bool:
    try:
        if "height" in img_candidate.attrib and int(img_candidate.attrib["height"]) < 100:
            return False
        if "width" in img_candidate.attrib and int(img_candidate.attrib["width"]) < 100:
            return False
    except ValueError:
        pass
    return True


def get_images(
    tree: Any,
    original_url: str,
    title_index: Optional[int],
    wrong_atts: Optional[list[str]] = None,
) -> Optional[list[str]]:
    if title_index is None:
        return None
    if wrong_atts is None:
        wrong_atts = [
            "adsense",
            "icon",
            "logo",
            "advert",
            "toolbar",
            "footer",
            "layout",
            "banner",
        ]
    img_candidates = tree.xpath("//img[string-length(@src) > 3]")
    img_candidates += tree.xpath('//meta[contains(@property, "image")]')
    img_candidates += tree.xpath('//*[contains(@style, "background-image")]')
    ok_images = []
    for img_candidate in img_candidates:
        if general_ok_img(img_candidate, original_url, wrong_atts):
            if img_candidate.tag == "img":
                if dimensions_ok(img_candidate):
                    ok_images.append(img_candidate)
            else:
                ok_images.append(img_candidate)
    images_and_indices = []
    for num, node in enumerate(tree.iter()):
        if node in ok_images:
            images_and_indices.append((node, num))
    ranked_images = sorted(images_and_indices, key=lambda x: abs(x[1] - title_index))
    images: list[str] = []
    for x in ranked_images:
        val = None
        if "src" in x[0].attrib:
            val = x[0].attrib["src"]
        elif "content" in x[0].attrib:
            val = x[0].attrib["content"]
        elif "style" in x[0].attrib:
            tmp = re.findall(r"background-image:[ ]*url\((http[^)]+)", x[0].attrib["style"])
            if tmp:
                val = tmp[0]
        if val is not None:
            val = urljoin(original_url, val)
            if val not in images:
                images.append(val)
    return images


HTML_OR_JS_COMMENTLINE = re.compile(r"^\s*(//.*|<!--.*-->)")


def extract_jsonld(tree: Any) -> list[Any]:
    results = []
    for script in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
        except ValueError:
            try:
                data = json.loads(HTML_OR_JS_COMMENTLINE.sub("", script))
            except ValueError:
                import json as j

                try:
                    data = j.loads(script)
                except j.JSONDecodeError:
                    data = j.loads(html.unescape(script.replace("'", "'")))
        results.append(data)
    return results


lang_to_stoplist = {
    "nl": justext.get_stoplist("Dutch"),
    "en": justext.get_stoplist("English"),
}


def edge_distance(i: int, lower: int, upper: int) -> int:
    return min(abs(lower - i), abs(upper - i))


def different(text: str, title: str, date_str: str, simple: bool = True) -> bool:
    if not simple:
        from pyxdameraulevenshtein import damerau_levenshtein_distance

        return damerau_levenshtein_distance(text, title) > 3 and damerau_levenshtein_distance(text, date_str) > 3
    text = text.strip()
    title = title.strip()
    date_str.strip()
    return text not in (title, date_str)


def get_content(response_body: bytes, title: str, language: str, url: str) -> tuple[Any, Optional[str]]:
    language = language if language in lang_to_stoplist else "en"
    stoplist = lang_to_stoplist[language]
    good_inds = []
    tree = lxml.html.fromstring(response_body)
    texts = justext.justext(response_body, stoplist)
    for line_num, x in enumerate(texts):
        x.num_ = line_num
        if not x.is_boilerplate or x.is_heading:
            good_inds.append(line_num)
    date = None
    body = None
    date_text = None
    md = parse_date(url)
    if md is not None and md.is_publish_date:
        date = md.start_date
        date_text = md.text[0]
    if good_inds:
        if date_text is None:
            lower_index, upper_index = good_inds[0], good_inds[-1]
            date_candidates = sorted(
                range(len(texts)),
                key=lambda x: edge_distance(x, lower_index, upper_index),
            )
            date_candidates = [texts[i] for i in date_candidates]
            date_candidates = [x for x in date_candidates if len(x) < 100]
            for x in date_candidates:
                md = parse_date(x.text, lang=language)
                if md is None:
                    continue
                if md.is_publish_date:
                    if language != "en":
                        emd = parse_date(x.text)
                        if emd is not None and emd.min_level < md.min_level:
                            md = emd
                    date = md.start_date
                    date_text = x.text
                    break
        date_text = date_text or "NOTGONNAMATCH"
        texts = [texts[x] for x in good_inds]
        first_ind = min(good_inds)
        body = "\n".join([
            x.text for x in texts if not x.is_boilerplate and x.num_ > first_ind and different(x.text, title, date_text)
        ])
    return date, body


CACHE = diskcache.Cache(Path("~/.auto_extract").expanduser())


class Article:
    def __init__(self, response_body: bytes, response_url: str) -> None:
        self.response_body = response_body
        self.tree = fromstring(response_body)
        self.url = response_url
        self.id = response_url

    @lazy_property
    def title(self) -> str:
        return get_title(self.tree, self.url)

    @lazy_property
    def image(self) -> str:
        images = get_images(self.tree, self.url, get_title_index(self.tree, self.title))
        image = images[0] if images else ""
        return image

    @lazy_property
    def _date_and_article_text(self) -> tuple[Any, Optional[str]]:
        return get_content(self.response_body, self.title, self.language, self.url)

    @property
    def publish_date(self) -> Any:
        publish_date, _ = self._date_and_article_text
        return publish_date

    @property
    def article_text(self) -> Optional[str]:
        _, article_text = self._date_and_article_text
        return article_text

    @lazy_property
    def domain(self) -> str:
        full_domain, _ = extract_domain(self.url)
        return full_domain

    @lazy_property
    def language(self) -> str:
        return get_language(self.tree)

    @lazy_property
    def full_text(self) -> str:
        return get_full_text(self.tree)

    @lazy_property
    def code(self) -> list[str]:
        return [x.text_content() for x in self.tree.xpath("//code")]

    @lazy_property
    def links(self) -> list[str]:
        return extract_links(self.tree, self.domain)

    @lazy_property
    def heading(self) -> list[str]:
        return extract_headings(self.tree)

    @lazy_property
    def extruct(self) -> Any:
        import extruct

        key = (self.response_hash, "extruct")
        if key in CACHE:
            return CACHE[key]
        result = extruct.extract(self.response_body)
        CACHE[key] = result
        return result

    @lazy_property
    def jsonld(self) -> list[Any]:
        return extract_jsonld(self.tree)

    @lazy_property
    def response_hash(self) -> str:
        return hashlib.md5(self.response_body.encode("utf-8")).hexdigest()

    @lazy_property
    def microdata(self) -> Any:
        from extruct.w3cmicrodata import MicrodataExtractor

        me = MicrodataExtractor()
        return me.extract_items(self.tree, self.url)

    def __repr__(self) -> str:
        obj = {
            "title": self.title,
            "domain": self.domain,
            "url": self.url,
            "publish_date": self.publish_date,
        }
        return "Article({})".format(obj)

    @classmethod
    def from_url(cls, url: str, session: requests.Session) -> "Article":
        response = session.get(url)
        return cls(response.content, url)

    def to_dict(
        self,
        keys: Iterable[str] = (
            "title",
            "publish_date",
            "url",
            "id",
            "article_text",
            "code",
            "links",
            "image",
        ),
        skip_if_empty: bool = False,
    ) -> dict:
        return {x: getattr(self, x) for x in keys if getattr(self, x) or skip_if_empty}

    def view(self) -> None:
        from requests_viewer import view_html

        view_html(self.response_body)


def parse_article(response_body: bytes, response_url: str) -> Article:
    return Article(response_body, response_url)


def extract_url(url: str) -> Article:
    response = requests.get(url)
    return parse_article(response.content, response.url)


def test_url(url: str) -> Article:
    return extract_url(url)


YYYYMMDD = re.compile(r"20\d{2}\d{2}\d{2}[/_-]")
YYYY_MM_DD = re.compile(r"20\d{2}[/_-]\d{2}[/_-]\d{2}[/_-]")
YYYY_MM = re.compile(r"20\d{2}[/_-]*\d{2}[/_-]")
YYYY = re.compile(r"[/_-]20\d{2}[/_-]")


def re_count(char: str, min_count: int, max_count: int) -> re.Pattern:
    min_count_s = str(min_count)
    max_count_s = str(max_count)
    r = "^" + "([^{0}]*{0}[^{0}]*)".format(char) + "{" + min_count_s + "," + max_count_s + "}" + "$"
    return re.compile(r)


def make_regex(x: Any) -> re.Pattern:
    if isinstance(x, str):
        return re.compile(x)
    return x


block_list = [
    "#",
    "/@",
    "/#",
    "/tag",
    "/api",
    "\\.pdf",
    "/job",
    "/faq",
    "/.pdf",
    "/home",
    "/feed",
    "/devs",
    "/team",
    "[?]m=",
    "lang=",
    "/page",
    "/sign",
    "/terms",
    "/login",
    "/[?]m=",
    "/trend",
    "/about",
    "/stats",
    "/brand",
    "/logout",
    "/author",
    "/tagged",
    "/latest",
    "[?]cat=",
    "/people",
    "/upload",
    "/comment",
    "/careers",
    "careers[.]",
    "/welcome",
    "/support",
    "/privacy",
    "/keyword",
    "/sitemap",
    "/[?]cat=",
    "/contact",
    "irc:///",
    "/archive",
    "/tutorial",
    "/follower",
    "/support/",
    "/category",
    "/trending",
    "/register",
    "/download",
    "/newsfeed",
    "/wp-login",
    "/password",
    "/community",
    "/following",
    "/categories",
    "/docs/following",
    "/search",
    "/label",
    "{{",
    "/getting-started",
    "/profile",
    "/channel/",
    "/video/",
    "/videos/",
]
iso2_countries = r"/(af|ax|al|dz|as|ad|ao|aq|ag|ag|ar|am|aw|au|at|az|bs|bh|bd|bb|by|be|bz|bj|bm|bt|bo|ba|bw|bv|br|vg|bn|bg|bf|bi|ko|kh|cm|ca|cv|ky|cf|td|cl|cn|hk|mo|cx|cc|cc|co|km|ck|cr|ci|hr|cu|cy|cz|dk|dj|dm|do|ec|eg|sv|gq|er|ee|et|fk|fk|fo|fj|fi|fr|gf|pf|ga|gm|ge|de|gh|gi|gr|gl|gd|gp|gu|gt|gg|gn|gw|gy|ht|hm|va|va|hn|hu|is|in|id|ir|iq|ie|im|il|it|jm|jp|je|jo|kz|ke|ki|kr|kw|kg|la|lv|lb|ls|lr|ly|li|lt|lu|mk|mg|mw|my|mv|ml|mt|mh|mq|mr|mu|yt|mx|fm|md|mc|mn|me|ms|ma|mz|mm|nr|np|nl|an|nc|nz|ni|ne|ng|nu|nf|mp|no|om|pk|pw|ps|pa|pg|py|pe|ph|pn|pl|pt|pr|qa|ro|ru|rw|bl|sh|kn|lc|mf|pm|vc|ws|sm|st|sa|sn|rs|sc|sl|sg|sk|si|sb|so|za|gs|gs|ss|es|lk|sd|sr|sj|sj|sz|se|ch|sy|tw|tj|tz|th|tl|tg|tk|to|tt|tt|tn|tr|tm|tc|tv|tw|ug|ua|ae|uy|uz|vu|ve|vn|vn|vi|wf|wf|eh|xh|ye|zm|zw|zh)([/.?_-]|$)"
iso2_countries_re = re.compile(iso2_countries)


def should_be_blocked(url: str) -> bool:
    for b in block_list:
        if re.search(b, url):
            return True
    return bool(iso2_countries_re.search(url))


class Storage:
    def __init__(
        self,
        project_name: str,
        send_initially: bool = False,
        root: str = "~/.tradex",
        notify: Callable[[list[Article]], None] = lambda articles: None,
    ) -> None:
        self.root = root
        self.project_name = project_name
        self._articles = None
        self._seen = None
        self.send_initially = send_initially
        self.notify = notify
        if not send_initially and not self.index_path_exists:
            self.notify = False

    def __len__(self) -> int:
        return len(self.seen)

    def __repr__(self) -> str:
        return '{}(path="{}", num_articles={})'.format(self.__class__.__name__, self.path, len(self))

    @property
    def path(self) -> str:
        return Path(self.root).joinpath(self.project_name)

    @property
    def index_path(self) -> str:
        return Path(self.path).joinpath("index.jsonl")

    @property
    def articles_path(self) -> str:
        return Path(self.path).joinpath("articles.jsonl")

    @property
    def index_path_exists(self) -> bool:
        return Path(Path(self.index_path).expanduser()).is_file()

    def add_articles(self, articles: list[Article]) -> int:
        if articles:
            print("{} new articles in {}".format(len(articles), self.project_name))
            if self.notify:
                print("handling new articles!")
                self.notify(articles)
            else:
                print("ignoring")
        for article in articles:
            just.append(article.id, self.index_path)
            self.articles.append(article.to_dict())
            just.append(article.to_dict(), self.articles_path)
        return len(self.articles)

    @property
    def seen(self) -> set:
        if self._seen is None:
            self._seen = set(just.iread(self.index_path, no_exist=[]))
        return self._seen

    @property
    def articles(self) -> list:
        if self._articles is None:
            self._articles = list(just.iread(self.articles_path, no_exist=[]))
        return self._articles

    def clear(self) -> None:
        just.remove(self.path, recursive=True)
        self._seen = None
        self._articles = None
        print("Removed {}".format(self.path))

    def drop_one(self) -> None:
        article_id = self._seen.pop()
        if self._articles is not None:
            self._articles = [x for x in self._articles if x.id != article_id]


def remove_hash_after_last_dash(article_url: str) -> str:
    return re.sub("-[a-f]*[0-9]+[a-f]+[0-9]+[a-f0-9]+$", "", article_url)


file_extensions = {".jpg", ".png", ".zip", ".dmg", ".exe", ".gz"}


class UpfrontRobotFileParser(urllib.robotparser.RobotFileParser):
    def read(self, as_user_agent: str) -> None:
        try:
            r = requests.get(self.url, headers={"User-Agent": as_user_agent}, timeout=1)
            if r.status_code in (401, 403):
                self.disallow_all = True
            elif r.status_code >= 400 and r.status_code < 500:
                self.allow_all = True
            else:
                self.parse(r.text.splitlines())
        except Exception:
            self.allow_all = True


class Crawler:
    def __init__(
        self,
        seed_urls: Any,
        name: str,
        info_type: str,
        all_required_regexes: Optional[list] = None,
        any_exclude_regexes: Optional[list] = None,
        url_xpath: str = "//a/@href",
        remove_query_param: bool = True,
        get_id: Callable[[str], str] = remove_hash_after_last_dash,
        notify: Callable[[list[Article], "Crawler"], None] = lambda articles, crawler: None,
        add_query_param_nonce: bool = False,
        auto_detect_fn: Optional[Callable] = None,
    ) -> None:
        self.user_agent = "Upfrontbot/1.0 (+https://www.upfront.ai/)"
        self.headers = {"User-Agent": self.user_agent}
        self.seed_urls = seed_urls if isinstance(seed_urls, list) else [seed_urls]
        self.storage = Storage(name, notify=lambda articles: notify(articles, self))
        self.name = name
        self.info_type = info_type
        self.domain_names: list[str] = []
        self.domains: list[str] = []
        self.robot_parsers: list = []
        self.setup_seeds(seed_urls)
        all_required_regexes = all_required_regexes or []
        self.all_required_regexes = [make_regex(x) for x in all_required_regexes]
        any_exclude_regexes = any_exclude_regexes or []
        self.any_exclude_regexes = [make_regex(x) for x in any_exclude_regexes]
        self.url_xpath = url_xpath
        self.remove_query_param = remove_query_param
        self.get_id = get_id
        self.s = requests.Session()
        self.s.headers.update(self.headers)
        self.add_query_param_nonce = add_query_param_nonce
        self.auto_detect_fn = auto_detect_fn
        self.seed_error = 0

    def setup_seeds(self, seed_urls: list[str]) -> None:
        for seed_url in seed_urls:
            if not seed_url:
                continue
            if any(x in seed_url.split("/")[2] for x in ["tumblr", "ink.one"] if len(seed_url.split("/")) > 2):
                continue
            domain_name, domain = extract_domain(seed_url)
            self.domain_names.append(domain_name)
            self.domains.append(domain)
            self.robot_parsers.append(self.get_robo_parser_domain(domain))

    def get_robo_parser_domain(self, domain: str) -> Any:
        rp = UpfrontRobotFileParser()
        robot_url = urllib.request.urljoin(domain, "robots.txt")
        rp.set_url(robot_url)
        try:
            rp.read(self.user_agent)
        except Exception as e:
            print(e)

            class rp:
                def can_fetch(self, user_agent: str, url: str) -> bool:
                    return True

        return rp

    def robots_can_fetch(self, url: str) -> bool:
        return any(robot_parser.can_fetch(self.user_agent, url) for robot_parser in self.robot_parsers)

    @property
    def invalid_seeds(self) -> bool:
        return self.seed_error == len(self.seed_urls)

    def within_domain(self, url: str) -> bool:
        parts = url.split("/")
        if len(parts) < 3:
            return False
        return any(domain_name in url.split("/")[2] for domain_name in self.domain_names)

    def get(self, url: str) -> Optional[bytes]:
        try:
            response = self.s.get(url, timeout=3)
        except Exception:
            return None
        if response.status_code > 399:
            return None
        return response.content

    def pre_process_url(self, url: str) -> str:
        if self.remove_query_param:
            url = url.split("?", maxsplit=1)[0]
        return url

    def view_tree(self) -> None:
        from requests_viewer import view_tree

        for seed_url in self.seed_urls:
            view_tree(lxml.html.fromstring(self.get(seed_url)))

    def return_seed_url_with_query_nonce(self, seed_url: str) -> str:
        semi_random_nonce = str(int(time.time()))
        separator = "&" if "?" in seed_url else "?"
        return seed_url + separator + "additionals_param=" + semi_random_nonce

    def get_links(self) -> dict[str, str]:
        links: dict[str, str] = {}
        self.seed_error = 0
        for seed_url, domain in zip(self.seed_urls, self.domains):
            if self.add_query_param_nonce:
                seed_url = self.return_seed_url_with_query_nonce(seed_url)
            content = self.get(seed_url)
            if content is None:
                print("no content in seed_url {}".format(seed_url))
                self.seed_error += 1
                continue
            tree = lxml.html.fromstring(content)
            tree.make_links_absolute(domain)
            web_urls = tree.xpath(self.url_xpath)
            auto_pattern = None
            if self.auto_detect_fn:
                auto_pattern = self.auto_detect_fn(web_urls)
            for link in web_urls:
                link = self.pre_process_url(link)
                if not link:
                    continue
                link_id = self.get_id(link)
                if link_id in self.storage.seen:
                    continue
                if not self.within_domain(link):
                    continue
                if should_be_blocked(link):
                    continue
                if link == seed_url:
                    continue
                if any(link.endswith(x) for x in file_extensions):
                    continue
                if not self.robots_can_fetch(link):
                    continue
                if self.any_exclude_regexes and any(x.search(link) for x in self.any_exclude_regexes):
                    continue
                if self.all_required_regexes and not all(x.search(link) for x in self.all_required_regexes):
                    continue
                if self.auto_detect_fn and auto_pattern and not auto_pattern.search(link):
                    continue
                self.storage.seen.add(link_id)
                links[link_id] = link
        return links

    def save_articles_from_links(self, links: dict[str, str]) -> int:
        articles = []
        for link_id, link in list(links.items()):
            article = Article.from_url(link, self.s)
            article.id = link_id
            articles.append(article)
        return self.storage.add_articles(articles)

    def start(self) -> int:
        num_new = self.save_articles_from_links(self.get_links())
        return num_new


class MediumCrawler(Crawler):
    def __init__(self, pages: Any, name: str, *args: Any, **kwargs: Any) -> None:
        pages = pages if isinstance(pages, list) else [pages]
        seed_urls = ["https://medium.com/{}/latest".format(page) for page in pages]
        super().__init__(
            seed_urls=seed_urls,
            name="medium_{}".format(name),
            any_exclude_regexes=[
                "/about",
                "/archive",
                "/welcome",
                "/tagged",
                "/trending",
            ],
            all_required_regexes=[re_count("/", 4, 4)],
            *args,
            **kwargs,
        )


class SteemitCrawler(Crawler):
    def __init__(self, pages: Any, name: str, *args: Any, **kwargs: Any) -> None:
        pages = pages if isinstance(pages, list) else [pages]
        seed_urls = ["https://steemit.com/{}".format(page) for page in pages]
        super().__init__(
            seed_urls=seed_urls,
            name="medium_{}".format(name),
            all_required_regexes=[re_count("/", 5, 5)],
            any_exclude_regexes=["#"],
            *args,
            **kwargs,
        )


VIEW_HTML = """
<html>
<body>
<div style="margin: 24px auto 12px; max-width: 1055px;">
<h1>{0}</h1>
<p>Published at: {1}</p>
<center><img src="{2}" width="400px" /></center>
<p>{3}</p>
See at <a href="{4}">original URL</a>
</div>
<iframe src="{4}" style="width: 100%; height: 600px"></iframe>
</body>
</html>
"""


def article(title: str, date: str, image: str, body: str, url: str) -> str:
    return VIEW_HTML.format(title, date, image, body, url)


def view_html(x: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(x)
        f.flush()
        webbrowser.open("file://" + f.name)
        time.sleep(1)


def main() -> None:
    from pprint import pprint

    pprint(test_url(sys.argv[1]))


if __name__ == "__main__":
    main()
