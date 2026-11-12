#!/data/data/com.termux/files/usr/bin/env python

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import OrderedDict
from contextlib import closing, suppress
from io import BytesIO
from pathlib import Path
from shutil import get_terminal_size
from traceback import format_exc
from typing import Any, Optional
from urllib.parse import urljoin

import rarfile
import requests
from bs4 import BeautifulSoup
from guessit import guessit
from requests import exceptions
from requests.utils import quote

__version__ = "0.0.0"
SUB_FORMATS: list[str] = [".ass", ".srt", ".ssa", ".sub", ".sup"]
ARCHIVE_TYPES: list[str] = [".zip", ".rar", ".7z"]
VIDEO_FORMATS: list[str] = [
    ".webm",
    ".mkv",
    ".flv",
    ".vob",
    ".ogv",
    ".ogg",
    ".drc",
    ".gif",
    ".gifv",
    ".mng",
    ".avi",
    ".mov",
    ".qt",
    ".wmv",
    ".yuv",
    ".rm",
    ".rmvb",
    ".asf",
    ".amv",
    ".mp4",
    ".m4p",
    ".m4v",
    ".mpg",
    ".mp2",
    ".mpeg",
    ".mpe",
    ".mpv",
    ".m2v",
    ".svi",
    ".3gp",
    ".3g2",
    ".mxf",
    ".roq",
    ".nsv",
    ".f4v",
    ".f4p",
    ".f4a",
    ".f4b",
]


class ProgressBar:
    def __init__(self, prefix_info: str, title: str = "", total: int = 0, count_time: int = 0) -> None:
        self.title: str = title
        self.total: int = total
        self.prefix_info: str = prefix_info

    def refresh(self, cur_len: int) -> None:
        terminal_width: int = get_terminal_size().columns
        info: str = "%s '%s'...  %.2f%%" % (
            self.prefix_info,
            self.title,
            cur_len / self.total * 100,
        )
        while len(info) > terminal_width - 20:
            self.title = self.title[0:-4] + "..."
            info = "%s '%s'...  %.2f%%" % (
                self.prefix_info,
                self.title,
                cur_len / self.total * 100,
            )
        end_str: str = "\r" if cur_len < self.total else "\n"
        print(info, end=end_str)


def num_to_cn(number: str) -> str:
    assert number.isdigit()
    assert 1 <= int(number) <= 99
    trans_map: dict[str, str] = dict(zip(("123456789"), ("一二三四五六七八九")))
    if len(number) == 1:
        return trans_map[number]
    part1: str = "十" if number[0] == "1" else trans_map[number[0]] + "十"
    part2: str = trans_map[number[1]] if number[1] != "0" else ""
    return part1 + part2


def extract_name(name: str, en: bool = False) -> str:
    name, suffix = (str(Path(name).with_suffix("")), Path(name).suffix)
    c_pattern: str = "[一-鿿]"
    e_pattern: str = "[a-zA-Z]"
    c_indices: list[int] = [m.start(0) for m in re.finditer(c_pattern, name)]
    e_indices: list[int] = [m.start(0) for m in re.finditer(e_pattern, name)]
    if en or len(c_indices) <= len(e_indices):
        target, discard = e_indices, c_indices
    else:
        target, discard = c_indices, e_indices
    if len(target) == 0:
        return ""
    first_target, last_target = target[0], target[-1]
    first_discard: int = discard[0] if discard else -1
    last_discard: int = discard[-1] if discard else -1
    if last_discard < first_target:
        new_name: str = name[first_target:]
    elif last_target < first_discard:
        new_name = name[:first_discard]
    else:
        result: list[int] = [0, 1]
        start: int = -1
        end: int = 0
        while end < len(name):
            while end not in e_indices and end < len(name):
                end += 1
            if end == len(name):
                break
            start = end
            while end not in c_indices and end < len(name):
                end += 1
            if end - start > result[1] - result[0]:
                result = [start, end]
            start = end
            end += 1
        new_name = name[result[0] : result[1]]
    new_name = new_name.strip() + suffix
    return new_name


def _print_and_choose(items: list[str]) -> int:
    for i, item in enumerate(items):
        print("%3s) %s" % (i, item))
    choice: Optional[int] = None
    while choice is None:
        try:
            print()
            raw: str = input(" choose: ")
            choice = int(raw)
            assert choice < len(items)
        except ValueError:
            print(" only numbers accepted")
            choice = None
        except AssertionError:
            print(" ", end="\r")
            print("choice %d not within the range" % choice)
            choice = None
    print()
    return choice


def choose_archive(sub_dict: dict[str, Any], sub_num: int = 5, query: bool = True) -> tuple[bool, Any]:
    should_exit: bool = False
    if not query:
        chosen_sub: str = list(sub_dict.keys())[0]
        return should_exit, chosen_sub
    items: list[str] = ["Exit. Not downloading any subtitles."]
    for i, key in enumerate(sub_dict.keys()):
        if i == sub_num:
            break
        lang_info: str = ""
        lang_info += "【简】" if 4 & sub_dict[key]["lan"] else "      "
        lang_info += "【繁】" if 2 & sub_dict[key]["lan"] else "      "
        lang_info += "【英】" if 1 & sub_dict[key]["lan"] else "      "
        lang_info += "【双】" if 8 & sub_dict[key]["lan"] else "      "
        sub_info: str = "%s  %s" % (lang_info, key)
        items.append(sub_info)
    choice: int = _print_and_choose(items)
    if choice == 0:
        should_exit = True
        return should_exit, []
    return should_exit, list(sub_dict.keys())[choice - 1]


def choose_subtitle(subtitles: list[str]) -> str:
    items: list[str] = []
    for subtitle in subtitles:
        with suppress(Exception):
            subtitle = subtitle.encode("cp437").decode("gbk")
        items.append(subtitle)
    choice: int = _print_and_choose(items)
    return subtitles[choice]


def compute_subtitle_score(video_detail: dict[str, Any], subname: str, match_episode: bool = True) -> int:
    video_name: str = video_detail["title"].lower()
    season: str = str(video_detail.get("season"))
    episode: str = str(video_detail.get("episode"))
    year: str = str(video_detail.get("year"))
    vtype: str = str(video_detail.get("type"))
    subname = subname.lower()
    score: int = 0
    sub_name_info: dict[str, Any] = guessit(subname)
    if sub_name_info.get("title"):
        sub_title: str = sub_name_info["title"].lower()
    else:
        sub_title = ""
    sub_season: str = str(sub_name_info.get("season"))
    sub_episode: str = str(sub_name_info.get("episode"))
    sub_year: str = str(sub_name_info.get("year"))
    if vtype == "movie":
        if year == sub_year:
            score += 1
        if video_name == sub_title:
            score += 1
        elif sub_title != "":
            return -1
    elif video_name == sub_title:
        if season != sub_season:
            return -1
        elif episode != sub_episode and match_episode:
            return -1
        else:
            score += 1
    elif season == sub_season and episode == sub_episode:
        if sub_title != "":
            return -1
    else:
        return -1
    if "简体" in subname or "chs" in subname or ".gb." in subname:
        score += 2
    if "繁体" in subname or "cht" in subname or ".big5." in subname:
        pass
    if "chs.eng" in subname or "chs&eng" in subname:
        score += 2
    if "中英" in subname or "简英" in subname or "双语" in subname or "简体&英文" in subname:
        score += 4
    score += ("ass" in subname or "ssa" in subname) * 2
    score += ("srt" in subname) * 1
    return score


def guess_subtitle(sublist: list[str], video_detail: dict[str, Any]) -> tuple[bool, Optional[str]]:
    if not sublist:
        return False, None
    scores: list[int] = []
    subs: list[str] = []
    for one_sub in sublist:
        _, ftype = (str(Path(one_sub).with_suffix("")), Path(one_sub).suffix)
        if ftype not in SUB_FORMATS:
            continue
        subs.append(one_sub)
        subname: str = (Path(one_sub).parent, Path(one_sub).name)[-1]
        with suppress(Exception):
            subname = subname.encode("cp437").decode("gbk")
        score: int = compute_subtitle_score(video_detail, subname)
        scores.append(score)
    max_score: int = max(scores)
    max_pos: int = scores.index(max_score)
    return max_score > 0, subs[max_pos]


def get_file_list(data: bytes, datatype: str) -> dict[str, Any]:
    sub_buff: BytesIO = BytesIO(data)
    file_handler: Any
    if datatype == ".7z":
        try:
            sub_buff.seek(0)
            file_handler = P7ZIP(sub_buff)
        except Exception:
            datatype = ".zip"
    if datatype == ".zip":
        try:
            sub_buff.seek(0)
            file_handler = zipfile.ZipFile(sub_buff, mode="r")
        except Exception:
            datatype = ".rar"
    if datatype == ".rar":
        sub_buff.seek(0)
        file_handler = rarfile.RarFile(sub_buff, mode="r")
    sub_lists_dict: dict[str, Any] = dict()
    for one_file in file_handler.namelist():
        if (str(Path(one_file).with_suffix("")), Path(one_file).suffix)[-1] in SUB_FORMATS:
            sub_lists_dict[one_file] = file_handler
            continue
        if (str(Path(one_file).with_suffix("")), Path(one_file).suffix)[-1] in ARCHIVE_TYPES:
            data = file_handler.read(one_file)
            datatype = (str(Path(one_file).with_suffix("")), Path(one_file).suffix)[-1]
            sub_lists_dict.update(get_file_list(data, datatype))
    return sub_lists_dict


def run_command(cmd: str) -> tuple[str, str, int]:
    process: subprocess.Popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, error = process.communicate()
    return output.decode(), error.decode(), process.returncode


class P7ZIP:
    def __init__(self, file: BytesIO) -> None:
        self.data: bytes = file.read()
        self.namelist()

    def _parse_list_output(self, output: str) -> list[str]:
        header_pattern: str = r"\s+Date\s+Time\s+Attr\s+Size\s+Compressed\s+Name\s+"
        body: str = re.split(header_pattern, output)[-1]
        file_names: list[str] = []
        for line in body.split("\n")[1:]:
            if line.startswith("-----"):
                break
            parts: list[str] = re.split(r"\s", line.strip())
            file_name: str = parts[-1].strip()
            if Path(file_name).name == file_name:
                continue
            file_names.append(file_name)
        return file_names

    def namelist(self) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path: str = Path(tmp_dir).joinpath("archive.7z")
            with open(file_path, "wb") as f:
                f.write(self.data)
            cmd: str = "7z l " + file_path
            output, err, status = run_command(cmd)
            if status != 0:
                raise ValueError(err)
            file_names: list[str] = self._parse_list_output(output)
        return file_names

    def read(self, name: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path: str = Path(tmp_dir).joinpath("archive.7z")
            with open(file_path, "wb") as f:
                f.write(self.data)
            cmd_lists: list[str] = ["7z", "e", file_path, "-o" + tmp_dir, name]
            cmd: str = " ".join(cmd_lists)
            _output, err, status = run_command(cmd)
            if status != 0:
                raise ValueError(err)
            sub_file_path: str = Path(tmp_dir).joinpath(Path(name).name)
            with open(sub_file_path, "rb") as f:
                sub_data: bytes = f.read()
        return sub_data


class Video:
    @classmethod
    def sub_exists(cls, video_name: str, store_path: str, identifier: str) -> bool:
        sub_types: list[str] = [identifier + sub_type for sub_type in SUB_FORMATS]
        return any(Path(Path(store_path).joinpath(video_name + sub_type)).exists() for sub_type in sub_types)

    def __init__(self, video_path: str, sub_store_path: str = "", identifier: str = "") -> None:
        self.name: str
        self.type: str
        self.name, self.type = (str(Path(Path(video_path).name).with_suffix("")), Path(Path(video_path).name).suffix)
        self.path: str = Path(Path(video_path).parent).absolute()
        self.sub_store_path: str = Path(sub_store_path).absolute() if sub_store_path else self.path
        self.sub_identifier: str = identifier
        self.has_subtitle: bool = Video.sub_exists(self.name, self.sub_store_path, self.sub_identifier)
        self.extracted_name: str = extract_name(self.name)
        self.info: dict[str, Any] = guessit(self.extracted_name + self.type)

    def delete_existed_subtitles(self) -> None:
        if not self.has_subtitle:
            return
        for one_sub_type in SUB_FORMATS:
            delete_name: str = self.name + self.sub_identifier + one_sub_type
            delete_file: str = Path(self.sub_store_path).joinpath(delete_name)
            if Path(delete_file).exists():
                os.remove(delete_file)


class Downloader:
    header: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_5) AppleWebKit 537.36 (KHTML, like Gecko) Chrome",
        "Accept-Language": "zh-CN,zh;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    service_short_names: dict[str, str] = {"amazon prime": "amzn"}

    @classmethod
    def get_keywords(cls, video: Video) -> list[str]:
        keywords: list[str] = []
        info_dict: dict[str, Any] = video.info
        title: str = info_dict["title"]
        keywords.append(title)
        if info_dict.get("season"):
            keywords.append("s%s" % str(info_dict["season"]).zfill(2))
        if info_dict.get("year") and info_dict.get("type") == "movie":
            keywords.append(str(info_dict["year"]))
        if info_dict.get("episode"):
            keywords.append("e%s" % str(info_dict["episode"]).zfill(2))
        if info_dict.get("source"):
            keywords.append(info_dict["source"].replace("-", ""))
        if info_dict.get("release_group"):
            keywords.append(info_dict["release_group"])
        if info_dict.get("streaming_service"):
            service_name: str = info_dict["streaming_service"]
            short_names: Optional[str] = cls.service_short_names.get(service_name.lower())
            if short_names:
                keywords.append(short_names)
        if info_dict.get("screen_size"):
            keywords.append(str(info_dict["screen_size"]))
        keywords = [quote(_keyword) for _keyword in keywords]
        return keywords

    def get_subtitles(self, video: Video, sub_num: int = 5) -> dict[str, Any]:
        raise NotImplementedError

    def download_file(
        self,
        file_name: str,
        sub_url: str,
        session: Optional[requests.Session] = None,
    ) -> tuple[Optional[str], Optional[bytes], str]:
        raise NotImplementedError


class ZimukuDownloader(Downloader):
    name: str = "zimuku"
    choice_prefix: str = "[ZIMUKU]"
    site_url: str = "http://www.zimuku.la"
    search_url: str = "http://www.zimuku.la/search?q="

    def get_keywords(self, video: Video) -> list[str]:
        if video.info["type"] == "episode":
            keywords: list[str] = [
                video.info["title"],
                "s%s" % str(video.info["season"]).zfill(2),
            ]
            return keywords
        else:
            return super().get_keywords(video)

    def _parse_episode_page(
        self,
        session: requests.Session,
        link: str,
        info: dict[str, Any],
        match_episode: bool = True,
    ) -> dict[str, Any]:
        def _get_archive_dowload_link(sub_page_link: str) -> str:
            r = session.get(sub_page_link)
            bs_obj = BeautifulSoup(r.text, "html.parser")
            down_page_link: str = bs_obj.find("a", {"id": "down1"}).attrs["href"]
            down_page_link = urljoin(ZimukuDownloader.site_url, down_page_link)
            r = session.get(down_page_link)
            bs_obj = BeautifulSoup(r.text, "html.parser")
            download_link: str = bs_obj.find("a", {"rel": "nofollow"}).attrs["href"]
            download_link = urljoin(ZimukuDownloader.site_url, download_link)
            return download_link

        r = session.get(link)
        bs_obj = BeautifulSoup(r.text, "html.parser")
        subs_body = bs_obj.find("div", class_="subs box clearfix").find("tbody")
        subs: dict[str, Any] = dict()
        for sub in subs_body.find_all("tr"):
            a = sub.find("a")
            name: str = extract_name(a.text, en=True)
            score: int = compute_subtitle_score(info, name, match_episode=match_episode)
            if score == -1:
                continue
            type_score: int = 0
            for img in sub.find("td", class_="tac lang").find_all("img"):
                if "uk" in img.attrs["src"]:
                    type_score += 1
                elif "hongkong" in img.attrs["src"]:
                    type_score += 2
                elif "china" in img.attrs["src"]:
                    type_score += 4
                elif "jollyroger" in img.attrs["src"]:
                    type_score += 8
            sub_page_link: str = urljoin(ZimukuDownloader.site_url, a.attrs["href"])
            download_link: str = _get_archive_dowload_link(sub_page_link)
            backup_session: requests.Session = copy.deepcopy(session)
            backup_session.headers["Referer"] = link
            subs[ZimukuDownloader.choice_prefix + name] = {
                "link": download_link,
                "lan": type_score,
                "session": backup_session,
                "score": score,
            }
        return subs

    def _parse_shooter_episode_page(self, session: requests.Session, title: str, link: str) -> dict[str, Any]:
        sub: dict[str, Any] = dict()
        r = session.get(link)
        bs_obj = BeautifulSoup(r.text, "html.parser")
        lang_box = bs_obj.find("ul", {"class": "subinfo"}).find("li")
        type_score: int = 0
        text: str = lang_box.text
        if "英" in text:
            type_score += 1
        elif "繁" in text:
            type_score += 2
        elif "简" in text:
            type_score += 4
        elif "双语" in text:
            type_score += 8
        download_link: str = bs_obj.find("a", {"id": "down1"}).attrs["href"]
        backup_session: requests.Session = copy.deepcopy(session)
        backup_session.headers["Referer"] = link
        sub[ZimukuDownloader.choice_prefix + title] = {
            "lan": type_score,
            "link": download_link,
            "session": backup_session,
        }
        return sub

    def get_subtitles(self, video: Video, sub_num: int = 10) -> dict[str, Any]:
        print("Searching ZIMUKU...", end="\r")
        keywords: list[str] = self.get_keywords(video)
        info_dict: dict[str, Any] = video.info
        s: requests.Session = requests.session()
        s.headers.update(Downloader.header)
        sub_dict: dict[str, Any] = dict()
        pattern: str = r"url\s*=\s*'([^']*)'\s*\+\s*url"
        for i in range(len(keywords), 1, -1):
            keyword: str = ".".join(keywords[:i])
            r = s.get(ZimukuDownloader.search_url + keyword, timeout=10)
            html: str = r.text
            parts: list[str] = re.findall(pattern, html)
            while parts:
                parts.reverse()
                redirect_url: str = urljoin(ZimukuDownloader.site_url, "".join(parts))
                r = s.get(redirect_url, timeout=10)
                html = r.text
                parts = re.findall(pattern, html)
            if "搜索不到相关字幕" in html:
                continue
            bs_obj = BeautifulSoup(r.text, "html.parser")
            if bs_obj.find("div", {"class": "item"}):
                for item in bs_obj.find_all("div", {"class": "item"}):
                    title_a = item.find("p", class_="tt clearfix").find("a")
                    if info_dict["type"] == "episode":
                        title: str = title_a.text
                        try:
                            season_cn1: str = re.search("第(.*)季", title).group(1).strip()
                        except AttributeError:
                            sample_title: str = item.find("td", class_="first").find("a").get("title")
                            sample_dict: dict[str, Any] = guessit(extract_name(sample_title, en=True))
                            season_cn1 = num_to_cn(str(sample_dict["season"]))
                        season_cn2: str = num_to_cn(str(info_dict["season"]))
                        if season_cn1 != season_cn2:
                            continue
                    episode_link: str = ZimukuDownloader.site_url + title_a.attrs["href"]
                    new_subs: dict[str, Any] = self._parse_episode_page(s, episode_link, info_dict)
                    if not new_subs:
                        new_subs = self._parse_episode_page(s, episode_link, info_dict, match_episode=False)
                    sub_dict.update(new_subs)
            elif bs_obj.find("div", {"class": "persub"}):
                for persub in bs_obj.find_all("div", {"class": "persub"}):
                    title = persub.h1.text.split("/")[-1]
                    score: int = compute_subtitle_score(info_dict, title)
                    if score == -1:
                        continue
                    link: str = ZimukuDownloader.site_url + persub.h1.a.attrs["href"]
                    sub: dict[str, Any] = self._parse_shooter_episode_page(s, title, link)
                    sub[list(sub.keys())[0]]["score"] = score
                    sub_dict.update(sub)
            else:
                msg = "zimuku downloader needs updates"
                raise ValueError(msg)
            if len(sub_dict) >= sub_num:
                del keywords[:]
                break
        sub_dict = OrderedDict(sorted(sub_dict.items(), key=lambda e: e[1]["score"], reverse=True))
        keys: list[str] = list(sub_dict.keys())[:sub_num]
        return {key: sub_dict[key] for key in keys}

    def download_file(
        self,
        file_name: str,
        download_link: str,
        session: Optional[requests.Session] = None,
    ) -> tuple[Optional[str], Optional[bytes], str]:
        try:
            if not session:
                session = requests.session()
            with closing(session.get(download_link, stream=True)) as response:
                filename: str = response.headers["Content-Disposition"]
                chunk_size: int = 1024
                content_size: int = int(response.headers["content-length"])
                bar: ProgressBar = ProgressBar("Get", file_name.strip(), content_size)
                sub_data_bytes: bytes = b""
                for data in response.iter_content(chunk_size=chunk_size):
                    sub_data_bytes += data
                    bar.refresh(len(sub_data_bytes))
        except requests.Timeout:
            return None, None, "false"
        datatype: str
        if ".rar" in filename:
            datatype = ".rar"
        elif ".zip" in filename:
            datatype = ".zip"
        elif ".7z" in filename:
            datatype = ".7z"
        else:
            datatype = "Unknown"
            for sub_type in SUB_FORMATS:
                if sub_type in filename:
                    datatype = sub_type
                    break
        return datatype, sub_data_bytes, ""


class ZimuzuDownloader(Downloader):
    name: str = "zimuzu"
    choice_prefix: str = "[ZIMUZU]"
    site_url: str = "http://www.rrys2020.com"
    search_url: str = "http://www.rrys2020.com/search?keyword={0}&type=subtitle"

    def get_subtitles(self, video: Video, sub_num: int = 5) -> dict[str, Any]:
        print("Searching ZIMUZU...", end="\r")
        keywords: list[str] = Downloader.get_keywords(video)
        keyword: str = " ".join(keywords)
        sub_dict: dict[str, Any] = OrderedDict()
        s: requests.Session = requests.session()
        while True:
            r = s.get(
                ZimuzuDownloader.search_url.format(keyword),
                headers=Downloader.header,
                timeout=10,
            )
            bs_obj = BeautifulSoup(r.text, "html.parser")
            tab_text: str = bs_obj.find("div", {"class": "article-tab"}).text
            if "字幕(0)" not in tab_text:
                for one_box in bs_obj.find_all("div", {"class": "search-item"}):
                    sub_name: str = (
                        ZimuzuDownloader.choice_prefix + one_box.find("strong", {"class": "list_title"}).text
                    )
                    if video.info["type"] == "movie" and "美剧字幕" in sub_name:
                        continue
                    a = one_box.find("a")
                    text: str = a.text
                    sub_url: str = ZimuzuDownloader.site_url + a.attrs["href"]
                    type_score: int = 0
                    type_score += ("英文" in text) * 1
                    type_score += ("繁体" in text) * 2
                    type_score += ("简体" in text) * 4
                    type_score += ("中英" in text) * 8
                    sub_dict[sub_name] = {
                        "lan": type_score,
                        "link": sub_url,
                        "session": None,
                    }
                    if len(sub_dict) >= sub_num:
                        del keywords[:]
                        break
            if len(keywords) > 1:
                keyword = keyword.replace(keywords[-1], "")
                keywords.pop(-1)
                continue
            break
        if len(sub_dict.items()) > 0 and list(sub_dict.items())[0][1]["lan"] < 8:
            sub_dict = OrderedDict(sorted(sub_dict.items(), key=lambda e: e[1]["lan"], reverse=True))
        return sub_dict

    def download_file(
        self,
        file_name: str,
        sub_url: str,
        session: Optional[requests.Session] = None,
    ) -> tuple[Optional[str], Optional[bytes], str]:
        s: requests.Session = requests.session()
        header: dict[str, str] = Downloader.header.copy()
        r = s.get(sub_url, headers=Downloader.header)
        bs_obj = BeautifulSoup(r.text, "html.parser")
        a = bs_obj.find("div", {"class": "subtitle-links"}).a
        download_link: str = a.attrs["href"]
        header["Referer"] = download_link
        ajax_url: str = "http://got002.com/api/v1/static/subtitle/detail?"
        ajax_url += download_link.rsplit("?", maxsplit=1)[-1]
        r = s.get(ajax_url, headers=header)
        json_obj: dict[str, Any] = json.loads(r.text)
        download_link = json_obj["data"]["info"]["file"]
        try:
            with closing(requests.get(download_link, stream=True)) as response:
                chunk_size: int = 1024
                if response.headers.get("content-length"):
                    content_size: int = int(response.headers["content-length"])
                    bar: ProgressBar = ProgressBar("Get", file_name.strip(), content_size)
                    sub_data_bytes: bytes = b""
                    for data in response.iter_content(chunk_size=chunk_size):
                        sub_data_bytes += data
                        bar.refresh(len(sub_data_bytes))
                else:
                    bar = ProgressBar("Get", file_name.strip())
                    sub_data_bytes = b""
                    for data in response.iter_content(chunk_size=chunk_size):
                        sub_data_bytes += data
                        bar.point_wait()
                    bar.point_wait(end=True)
        except requests.Timeout:
            return None, None, ""
        datatype: str
        if "rar" in download_link:
            datatype = ".rar"
        elif "zip" in download_link:
            datatype = ".zip"
        elif "7z" in download_link:
            datatype = ".7z"
        elif ".rar" in file_name:
            datatype = ".rar"
        elif ".zip" in file_name:
            datatype = ".zip"
        elif ".7z" in file_name:
            datatype = ".7z"
        else:
            datatype = "Unknown"
        return datatype, sub_data_bytes, ""


class DownloaderManager:
    downloaders: tuple[Downloader, ...] = (ZimukuDownloader(), ZimuzuDownloader())
    downloader_names: list[str] = [d.__class__.name for d in downloaders]

    @classmethod
    def get_downloader_by_name(cls, name: str) -> Optional[Downloader]:
        for downloader in DownloaderManager.downloaders:
            if downloader.__class__.name == name:
                return downloader
        return None

    @classmethod
    def get_downloader_by_choice_prefix(cls, choice_prefix: str) -> Optional[Downloader]:
        for downloader in DownloaderManager.downloaders:
            if downloader.__class__.choice_prefix == choice_prefix:
                return downloader
        return None


class GetSubtitles:
    def __init__(
        self,
        name: str,
        query: bool,
        single: bool,
        more: bool,
        both: bool,
        over: bool,
        plex: bool,
        debug: bool,
        sub_num: Optional[str],
        downloader: Optional[str],
        sub_path: str,
    ) -> None:
        self.arg_name: str = name
        self.both: bool = both
        self.query: bool = query
        self.single: bool = single
        self.more: bool = more
        self.over: bool = over
        if not sub_num:
            self.sub_num: int = 5
        else:
            self.sub_num = int(sub_num)
        self.plex: bool = plex
        self.debug: bool = debug
        self.s_error: str = ""
        self.f_error: str = ""
        if not downloader:
            self.downloader: tuple[Downloader, ...] = DownloaderManager.downloaders
        else:
            if downloader not in DownloaderManager.downloader_names:
                print(
                    "\nNO SUCH DOWNLOADER:",
                    "PLEASE CHOOSE FROM",
                    ", ".join(DownloaderManager.downloader_names),
                    "\n",
                )
                sys.exit(1)
            found: Optional[Downloader] = DownloaderManager.get_downloader_by_name(downloader)
            assert found is not None
            self.downloader = (found,)
        self.failed_list: list[dict[str, Any]] = []
        self.sub_identifier: str = "" if not self.plex else ".zh"
        self.sub_store_path: str = sub_path.replace('"', "")
        if not Path(self.sub_store_path).is_dir():
            if self.sub_store_path:
                print("store path is invalid: " + self.sub_store_path)
            self.sub_store_path = ""
        else:
            print("subtitles will be saved to: " + self.sub_store_path)

    def get_videos(self, raw_path: str) -> list[Video]:
        raw_path = raw_path.replace('"', "")
        videos: list[Video] = []
        if Path(raw_path).is_dir():
            for root, dirs, files in os.walk(raw_path):
                for file in files:
                    v_type: str = (str(Path(file).with_suffix("")), Path(file).suffix)[-1]
                    if v_type not in VIDEO_FORMATS:
                        continue
                    video: Video = Video(
                        Path(root).joinpath(file),
                        sub_store_path=self.sub_store_path,
                        identifier=self.sub_identifier,
                    )
                    videos.append(video)
        elif Path(raw_path).is_absolute():
            v_type = (str(Path(raw_path).with_suffix("")), Path(raw_path).suffix)[-1]
            if v_type in VIDEO_FORMATS:
                video = Video(
                    raw_path,
                    sub_store_path=self.sub_store_path,
                    identifier=self.sub_identifier,
                )
                videos.append(video)
        else:
            s_path: str = os.getcwd() if not self.sub_store_path else self.sub_store_path
            video = Video(raw_path, sub_store_path=s_path, identifier=self.sub_identifier)
            videos.append(video)
        return videos

    def get_search_results(self, video: Video) -> dict[str, Any]:
        results: dict[str, Any] = OrderedDict()
        for i, downloader in enumerate(self.downloader):
            try:
                result: dict[str, Any] = downloader.get_subtitles(video, sub_num=self.sub_num)
                results.update(result)
            except ValueError as e:
                print("error: " + str(e))
            except (exceptions.Timeout, exceptions.ConnectionError):
                print("connect timeout, search next site.")
                if i == (len(self.downloader) - 1):
                    print("PLEASE CHECK YOUR NETWORK STATUS")
                    sys.exit(0)
                else:
                    continue
            if len(results) >= self.sub_num:
                break
        return results

    def process_archive(
        self,
        video: Video,
        archive_data: bytes,
        datatype: str,
    ) -> tuple[str, list[list[str]]]:
        error: str = ""
        if datatype not in ARCHIVE_TYPES:
            error = "unsupported file type " + datatype
            return error, []
        sub_lists_dict: dict[str, Any] = get_file_list(archive_data, datatype)
        if len(sub_lists_dict) == 0:
            error = "no subtitle in this archive"
            return error, []
        if not self.single:
            success: bool
            sub_name: Optional[str]
            success, sub_name = guess_subtitle(list(sub_lists_dict.keys()), video.info)
            if not success or sub_name is None:
                error = "no guess result in auto mode"
                return error, []
        else:
            sub_name = choose_subtitle(list(sub_lists_dict.keys()))
        sub_title: str
        sub_type: str
        _sub_title, sub_type = (str(Path(sub_name).with_suffix("")), Path(sub_name).suffix)
        extract_subs: list[list[str]] = [[sub_name, sub_type]]
        if self.both:
            another_sub_type: str = ".srt" if sub_type == ".ass" else ".ass"
            another_sub: str = sub_name.replace(sub_type, another_sub_type)
            another_sub = Path(another_sub).name
            for subname in list(sub_lists_dict.keys()):
                if another_sub in subname:
                    extract_subs.append([subname, another_sub_type])
                    break
            if len(extract_subs) == 1:
                print("no %s subtitles in this archive" % another_sub_type)
        video.delete_existed_subtitles()
        for one_sub, one_sub_type in extract_subs:
            sub_new_name: str = video.name + video.sub_identifier + one_sub_type
            extract_path: str = Path(video.sub_store_path).joinpath(sub_new_name)
            with open(extract_path, "wb") as sub:
                file_handler = sub_lists_dict[one_sub]
                sub.write(file_handler.read(one_sub))
        return error, extract_subs

    def process_subtitle(self, video: Video, sub_data: bytes, datatype: str) -> tuple[str, list[list[str]]]:
        video.delete_existed_subtitles()
        sub_name: str = video.name + video.sub_identifier + datatype
        extract_path: str = Path(video.sub_store_path).joinpath(sub_name)
        with open(extract_path, "wb") as sub:
            sub.write(sub_data)
        extract_subs: list[list[str]] = [[sub_name, datatype]]
        return "", extract_subs

    def process_result(
        self,
        video: Video,
        chosen_sub: str,
        link: str,
        session: Optional[requests.Session],
    ) -> tuple[str, list[list[str]]]:
        choice_prefix: str = chosen_sub[: chosen_sub.find("]") + 1]
        downloader: Optional[Downloader] = DownloaderManager.get_downloader_by_choice_prefix(choice_prefix)
        assert downloader is not None
        datatype: Optional[str]
        data: Optional[bytes]
        error: str
        datatype, data, error = downloader.download_file(chosen_sub, link, session=session)
        if error:
            return error, []
        extract_subs: list[list[str]] = []
        if datatype in ARCHIVE_TYPES:
            assert data is not None
            error, extract_subs = self.process_archive(video, data, datatype)
        elif datatype in SUB_FORMATS:
            assert data is not None
            error, extract_subs = self.process_subtitle(video, data, datatype)
        else:
            error = "unsupported file type " + str(datatype)
        if error:
            return error, []
        for extract_sub_name, extract_sub_type in extract_subs:
            extract_sub_name = extract_sub_name.split("/")[-1]
            with suppress(Exception):
                extract_sub_name = extract_sub_name.encode("cp437").decode("gbk")
            try:
                print("\nExtracted:", extract_sub_name)
            except UnicodeDecodeError:
                print("\nExtracted:".encode("gbk"), extract_sub_name.encode("gbk"))
        if self.more and datatype in ARCHIVE_TYPES:
            archive_path: str = Path(video.sub_store_path).joinpath(chosen_sub + datatype)
            with open(archive_path, "wb") as f:
                assert data is not None
                f.write(data)
            print("save original file.")
        return "", extract_subs

    def process_video(self, video: Video) -> tuple[str, list[list[str]]]:
        extract_subs: list[list[str]] = []
        sub_dict: dict[str, Any] = self.get_search_results(video)
        if len(sub_dict) == 0:
            error: str = "no search results. "
            return error, []
        while not extract_subs and len(sub_dict) > 0:
            should_exit: bool
            chosen_sub: str
            should_exit, chosen_sub = choose_archive(sub_dict, sub_num=self.sub_num, query=self.query)
            if should_exit:
                break
            try:
                error, extract_subs = self.process_result(
                    video,
                    chosen_sub,
                    sub_dict[chosen_sub]["link"],
                    sub_dict[chosen_sub]["session"],
                )
                if error:
                    print("error: " + error + "\n")
            except Exception as e:
                print("error:" + str(e))
            finally:
                sub_dict.pop(chosen_sub)
        return "", extract_subs

    def start(self) -> dict[str, Any]:
        videos: list[Video] = self.get_videos(self.arg_name)
        for i, video in enumerate(videos):
            self.s_error = ""
            self.f_error = ""
            print("\n- Video:", video.name)
            print("- Video Path:", video.path)
            print("- Subtitles Store Path:", video.sub_store_path + "\n")
            if video.has_subtitle and not self.over:
                print("subtitle already exists, add '-o' to replace it.")
                continue
            try:
                extract_subs: list[list[str]] = []
                error: str = ""
                error, extract_subs = self.process_video(video)
                self.s_error = error
            except rarfile.RarCannotExec:
                self.s_error += "Unrar not installed?"
            except Exception as e:
                self.s_error += str(e) + ". "
                self.f_error += format_exc()
            if not extract_subs and not error:
                self.s_error += " failed to guess one subtitle,"
                self.s_error += "use '-q' to try query mode."
            if self.s_error and not self.debug:
                self.s_error += "add --debug to get more info of the error"
            if self.s_error:
                self.failed_list.append({
                    "name": video.name,
                    "path": video.path,
                    "error": self.s_error,
                    "trace_back": self.f_error,
                })
                print("ERROR:" + self.s_error)
            if i == len(videos) - 1:
                break
            print("\n========================================================")
        if len(self.failed_list):
            print("\n===============================", end="")
            print("FAILED LIST===============================\n")
            for i, one in enumerate(self.failed_list):
                print("%2s. name: %s" % (i + 1, one["name"]))
                print("%3s path: %s" % ("", one["path"]))
                print("%3s info: %s" % ("", one["error"]))
                if self.debug:
                    print("%3s TRACE_BACK: %s" % ("", one["trace_back"]))
        print(
            "\ntotal: %s  success: %s  fail: %s\n"
            % (
                len(videos),
                len(videos) - len(self.failed_list),
                len(self.failed_list),
            )
        )
        return {
            "total": len(videos),
            "success": len(videos) - len(self.failed_list),
            "fail": len(self.failed_list),
            "fail_videos": self.failed_list,
        }


def main() -> None:
    arg_parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="GetSubtitles",
        epilog="getsub %s\n\n@guoyuhang" % (__version__),
        description="download subtitles easily",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    arg_parser.add_argument("name", help="the video's name or full path or a dir with videos")
    arg_parser.add_argument(
        "-p",
        "--directory",
        default="",
        action="store",
        help="set specified subtitle download path",
    )
    arg_parser.add_argument(
        "-q",
        "--query",
        action="store_true",
        help="show search results and choose one to download",
    )
    arg_parser.add_argument(
        "-s",
        "--single",
        action="store_true",
        help="show subtitles in the compacted file and choose one to download",
    )
    arg_parser.add_argument("-o", "--over", action="store_true", help="replace the subtitle already exists")
    arg_parser.add_argument("-m", "--more", action="store_true", help="save original download file.")
    arg_parser.add_argument(
        "-n",
        "--number",
        action="store",
        help="set max number of subtitles to be choosen when in query mode",
    )
    arg_parser.add_argument(
        "-b",
        "--both",
        action="store_true",
        help="save .srt and .ass subtitles at the same time if two types exist in the same archive",
    )
    arg_parser.add_argument(
        "-d",
        "--downloader",
        action="store",
        help="choose downloader from " + ", ".join(DownloaderManager.downloader_names),
    )
    arg_parser.add_argument("--debug", action="store_true", help="show more info of the error")
    arg_parser.add_argument(
        "--plex",
        action="store_true",
        help="add .zh to the subtitle's name for plex to recognize",
    )
    args: argparse.Namespace = arg_parser.parse_args()
    if args.over:
        print("\nThe script will replace the old subtitles if exist...\n")
    GetSubtitles(
        args.name,
        args.query,
        args.single,
        args.more,
        args.both,
        args.over,
        args.plex,
        args.debug,
        sub_num=args.number,
        downloader=args.downloader,
        sub_path=args.directory,
    ).start()


if __name__ == "__main__":
    main()
