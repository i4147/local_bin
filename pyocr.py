#!/data/data/com.termux/files/usr/bin/env python
import argparse
import codecs
import contextlib
import ctypes
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.dom.minidom
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from loguru import logger

if sys.version_info < (3, 4):
    msg = "PyOCR requires Python 3.4+"
    raise ImportError(msg)
VERSION = (0, 8, 5)
__version__ = "0.8.5"
_XHTML_HEADER = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
 "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
\t<meta http-equiv="content-type" content="text/html; charset=utf-8" />
\t<title>OCR output</title>
</head>
"""


class PyocrException(Exception):
    pass


class TesseractError(PyocrException):
    def __init__(self, status, message):
        PyocrException.__init__(self, message)
        self.status = status
        self.message = message
        self.args = (status, message)


class CuneiformError(PyocrException):
    def __init__(self, status, message):
        PyocrException.__init__(self, message)
        self.status = status
        self.message = message
        self.args = (status, message)


def digits_only(string):
    match = re.match(r"\D*(?P<digits>\d+)", string)
    if match:
        return int(match.group("digits"))
    return 0


class Box:
    def __init__(self, content, position, confidence=0):
        self.content = content
        self.position = position
        self.confidence = confidence

    def get_xml_tag(self, parent_doc):
        span_tag = parent_doc.createElement("span")
        span_tag.setAttribute("class", "ocrx_word")
        span_tag.setAttribute(
            "title",
            "bbox %d %d %d %d; x_wconf %d"
            % (
                self.position[0][0],
                self.position[0][1],
                self.position[1][0],
                self.position[1][1],
                self.confidence,
            ),
        )
        txt = xml.dom.minidom.Text()
        txt.data = self.content
        span_tag.appendChild(txt)
        return span_tag

    def __str__(self):
        return "{} {} {} {} {}".format(
            self.content,
            self.position[0][0],
            self.position[0][1],
            self.position[1][0],
            self.position[1][1],
        )

    def __box_cmp(self, other):
        if other is None or getattr(other, "position", None) is None:
            return -1
        for x, y in (
            (self.position[0][1], other.position[0][1]),
            (self.position[1][1], other.position[1][1]),
            (self.position[0][0], other.position[0][0]),
            (self.position[1][0], other.position[1][0]),
        ):
            if x < y:
                return -1
            elif x > y:
                return 1
        return 0

    def __lt__(self, other):
        return self.__box_cmp(other) < 0

    def __gt__(self, other):
        return self.__box_cmp(other) > 0

    def __eq__(self, other):
        return self.__box_cmp(other) == 0

    def __le__(self, other):
        return self.__box_cmp(other) <= 0

    def __ge__(self, other):
        return self.__box_cmp(other) >= 0

    def __ne__(self, other):
        return self.__box_cmp(other) != 0

    def __hash__(self):
        position_hash = 0
        position_hash += (self.position[0][0] & 0xFF) << 0
        position_hash += (self.position[0][1] & 0xFF) << 8
        position_hash += (self.position[1][0] & 0xFF) << 16
        position_hash += (self.position[1][1] & 0xFF) << 24
        return position_hash ^ hash(self.content) ^ hash(self.content)


class LineBox:
    def __init__(self, word_boxes, position):
        self.word_boxes = word_boxes
        self.position = position

    @property
    def content(self):
        txt = ""
        for box in self.word_boxes:
            txt += box.content + " "
        return txt.strip()

    def get_xml_tag(self, parent_doc):
        span_tag = parent_doc.createElement("span")
        span_tag.setAttribute("class", "ocr_line")
        span_tag.setAttribute(
            "title",
            "bbox %d %d %d %d"
            % (
                self.position[0][0],
                self.position[0][1],
                self.position[1][0],
                self.position[1][1],
            ),
        )
        for box_idx, box in enumerate(self.word_boxes):
            if box_idx:
                space = xml.dom.minidom.Text()
                space.data = " "
                span_tag.appendChild(space)
            span_tag.appendChild(box.get_xml_tag(parent_doc))
        return span_tag

    def __str__(self):
        txt = "[\n"
        for box in self.word_boxes:
            txt += "  {} {} {} {} {}\n".format(
                box.content,
                box.position[0][0],
                box.position[0][1],
                box.position[1][0],
                box.position[1][1],
            )
        return "{}] {} {} {} {}".format(
            txt,
            self.position[0][0],
            self.position[0][1],
            self.position[1][0],
            self.position[1][1],
        )

    def __repr__(self):
        return f"LineBox({self!s})"

    def __contains__(self, text):
        return text in self.content

    def __box_cmp(self, other):
        if other is None or getattr(other, "position", None) is None:
            return -1
        for x, y in (
            (self.position[0][1], other.position[0][1]),
            (self.position[1][1], other.position[1][1]),
            (self.position[0][0], other.position[0][0]),
            (self.position[1][0], other.position[1][0]),
        ):
            if x < y:
                return -1
            elif x > y:
                return 1
        return 0

    def __lt__(self, other):
        return self.__box_cmp(other) < 0

    def __gt__(self, other):
        return self.__box_cmp(other) > 0

    def __eq__(self, other):
        return self.__box_cmp(other) == 0

    def __le__(self, other):
        return self.__box_cmp(other) <= 0

    def __ge__(self, other):
        return self.__box_cmp(other) >= 0

    def __ne__(self, other):
        return self.__box_cmp(other) != 0

    def __hash__(self):
        content = self.content
        position_hash = 0
        position_hash += (self.position[0][0] & 0xFF) << 0
        position_hash += (self.position[0][1] & 0xFF) << 8
        position_hash += (self.position[1][0] & 0xFF) << 16
        position_hash += (self.position[1][1] & 0xFF) << 24
        return position_hash ^ hash(content)


class BaseBuilder:
    def __init__(self, file_extensions, tesseract_flags, tesseract_configs, cuneiform_args):
        self.file_extensions = file_extensions
        self.tesseract_flags = tesseract_flags
        self.tesseract_configs = tesseract_configs
        self.cuneiform_args = cuneiform_args

    def read_file(self, file_descriptor):
        msg = "Implement in subclasses"
        raise NotImplementedError(msg)

    def write_file(self, file_descriptor, output):
        msg = "Implement in subclasses"
        raise NotImplementedError(msg)

    def start_line(self, box):
        msg = "Implement in subclasses"
        raise NotImplementedError(msg)

    def add_word(self, word, box, confidence=0):
        msg = "Implement in subclasses"
        raise NotImplementedError(msg)

    def end_line(self):
        msg = "Implement in subclasses"
        raise NotImplementedError(msg)

    def get_output(self):
        msg = "Implement in subclasses"
        raise NotImplementedError(msg)


class TextBuilder(BaseBuilder):
    def __init__(
        self,
        tesseract_layout=3,
        cuneiform_dotmatrix=False,
        cuneiform_fax=False,
        cuneiform_singlecolumn=False,
    ):
        tess_flags = [psm_parameter(), str(tesseract_layout)]
        file_ext = ["txt"]
        cun_args = ["-f", "text"]
        for par, arg in [
            (cuneiform_dotmatrix, "--dotmatrix"),
            (cuneiform_fax, "--fax"),
            (cuneiform_singlecolumn, "--singlecolumn"),
        ]:
            if par:
                cun_args.append(arg)
        super(TextBuilder, self).__init__(file_ext, tess_flags, [], cun_args)
        self.tesseract_layout = tesseract_layout
        self.built_text = []

    @staticmethod
    def read_file(file_descriptor):
        return file_descriptor.read().strip()

    @staticmethod
    def write_file(file_descriptor, text):
        file_descriptor.write(text)

    def start_line(self, box):
        self.built_text.append("")

    def add_word(self, word, box, confidence=0):
        if self.built_text[-1] != "":
            self.built_text[-1] += " "
        self.built_text[-1] += word

    def end_line(self):
        pass

    def get_output(self):
        return "\n".join(self.built_text)

    def __str__(self):
        return "Raw text"


class DigitBuilder(TextBuilder):
    def __init__(self, tesseract_layout=3):
        super(DigitBuilder, self).__init__(tesseract_layout)
        self.tesseract_configs.append("digits")

    def __str__(self):
        return "Digits raw text"


class _WordHTMLParser(HTMLParser):
    WORD_TAG_TYPES = {"ocr_word", "ocrx_word"}
    LINE_TAG_TYPES = {"ocr_header", "ocr_footer", "ocr_line"}

    def __init__(self):
        HTMLParser.__init__(self)
        self.__tag_types = []
        self.__current_box_position = None
        self.__current_box_text = None
        self.__current_box_confidence = None
        self.boxes = []
        self.__current_line_position = None
        self.__current_line_content = []
        self.lines = []

    @staticmethod
    def __parse_confidence(title):
        for piece in title.split("; "):
            piece = piece.strip()
            if not piece.startswith("x_wconf"):
                continue
            return int(piece.split(" ")[1])
        logger.debug("OCR confidence measure not found. Assuming 0.")
        return 0

    @staticmethod
    def __parse_position(title):
        for piece in title.split("; "):
            piece = piece.strip()
            if not piece.startswith("bbox"):
                continue
            piece = piece.split(" ")
            return ((int(piece[1]), int(piece[2])), (int(piece[3]), int(piece[4])))
        raise Exception("Invalid hocr position: %s" % title)

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        position = None
        tag_type = None
        for attr in attrs:
            if attr[0] == "class":
                tag_type = attr[1]
            if attr[0] == "title":
                position = attr[1]
        if position is None or tag_type is None:
            return
        if tag_type in self.WORD_TAG_TYPES:
            try:
                confidence = self.__parse_confidence(position)
                position = self.__parse_position(position)
                self.__current_box_confidence = confidence
                self.__current_box_position = position
            except Exception:
                self.__tag_types.append("ignore")
                return
            self.__current_box_text = ""
        elif tag_type in self.LINE_TAG_TYPES:
            self.__current_line_position = self.__parse_position(position)
            self.__current_line_content = []
        self.__tag_types.append(tag_type)

    def handle_data(self, data):
        if self.__current_box_text is None:
            return
        self.__current_box_text += data

    def handle_endtag(self, tag):
        if tag != "span":
            return
        tag_type = self.__tag_types.pop()
        if tag_type in self.WORD_TAG_TYPES:
            if self.__current_box_text is None:
                return
            box = Box(
                self.__current_box_text,
                self.__current_box_position,
                self.__current_box_confidence,
            )
            self.boxes.append(box)
            self.__current_line_content.append(box)
            self.__current_box_text = None
            return
        elif tag_type in self.LINE_TAG_TYPES:
            line = LineBox(self.__current_line_content, self.__current_line_position)
            self.lines.append(line)
            self.__current_line_content = []
            return

    def __str__(self):
        return "WordHTMLParser"


class _LineHTMLParser(HTMLParser):
    TAG_TYPE_CONTENT = 0
    TAG_TYPE_POSITIONS = 1

    def __init__(self):
        HTMLParser.__init__(self)
        self.boxes = []
        self.__line_text = None
        self.__char_positions = None

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        tag_type = -1
        for attr in attrs:
            if attr[0] == "class":
                if attr[1] == "ocr_line":
                    tag_type = self.TAG_TYPE_CONTENT
                elif attr[1] == "ocr_cinfo":
                    tag_type = self.TAG_TYPE_POSITIONS
        if tag_type == self.TAG_TYPE_CONTENT:
            self.__line_text = ""
            self.__char_positions = []
            return
        elif tag_type == self.TAG_TYPE_POSITIONS:
            for attr in attrs:
                if attr[0] == "title":
                    self.__char_positions = attr[1].split(" ")
            self.__char_positions = self.__char_positions[1:]
            if self.__char_positions[-1] == "":
                self.__char_positions = self.__char_positions[:-1]
            try:
                while True:
                    self.__char_positions.remove("-1")
            except ValueError:
                pass

    def handle_data(self, data):
        if self.__line_text is None:
            return
        self.__line_text += data

    def handle_endtag(self, tag):
        if self.__line_text is None or self.__char_positions == []:
            return
        words = self.__line_text.split(" ")
        for word in words:
            if word == "":
                continue
            positions = self.__char_positions[0 : 4 * len(word)]
            self.__char_positions = self.__char_positions[4 * len(word) :]
            left_pos = min([int(positions[x]) for x in range(0, 4 * len(word), 4)])
            top_pos = min([int(positions[x]) for x in range(1, 4 * len(word), 4)])
            right_pos = max([int(positions[x]) for x in range(2, 4 * len(word), 4)])
            bottom_pos = max([int(positions[x]) for x in range(3, 4 * len(word), 4)])
            self.boxes.append(Box(word, ((left_pos, top_pos), (right_pos, bottom_pos))))
        self.__line_text = None

    def __str__(self):
        return "LineHTMLParser"


class WordBoxBuilder(BaseBuilder):
    def __init__(self, tesseract_layout=1):
        tess_flags = [psm_parameter(), str(tesseract_layout)]
        super(WordBoxBuilder, self).__init__(["html", "hocr"], tess_flags, ["hocr"], ["-f", "hocr"])
        self.word_boxes = []
        self.tesseract_layout = tesseract_layout

    def read_file(self, file_descriptor):
        parsers = [_WordHTMLParser(), _LineHTMLParser()]
        html_str = file_descriptor.read()
        for p in parsers:
            p.feed(html_str)
            if len(p.boxes) > 0:
                if p.boxes[-1].content == "":
                    p.boxes.pop(-1)
                return p.boxes
        return []

    @staticmethod
    def write_file(file_descriptor, boxes):
        impl = xml.dom.minidom.getDOMImplementation()
        newdoc = impl.createDocument(None, "root", None)
        file_descriptor.write(_XHTML_HEADER)
        file_descriptor.write("<body>\n")
        for box in boxes:
            file_descriptor.write("<p>" + box.get_xml_tag(newdoc).toxml() + "</p>\n")
        file_descriptor.write("</body>\n</html>\n")

    def start_line(self, box):
        pass

    def add_word(self, word, box, confidence=0):
        self.word_boxes.append(Box(word, box, confidence))

    def end_line(self):
        pass

    def get_output(self):
        return self.word_boxes

    def __str__(self):
        return "Word boxes"


class LineBoxBuilder(BaseBuilder):
    def __init__(self, tesseract_layout=1):
        tess_flags = [psm_parameter(), str(tesseract_layout)]
        super(LineBoxBuilder, self).__init__(["html", "hocr"], tess_flags, ["hocr"], ["-f", "hocr"])
        self.lines = []
        self.tesseract_layout = tesseract_layout

    def read_file(self, file_descriptor):
        parsers = [
            (_WordHTMLParser(), lambda parser: parser.lines),
            (
                _LineHTMLParser(),
                lambda parser: [LineBox([box], box.position) for box in parser.boxes],
            ),
        ]
        html_str = file_descriptor.read()
        for parser, convertion in parsers:
            parser.feed(html_str)
            if len(parser.boxes) > 0:
                if parser.boxes[-1].content == "":
                    parser.boxes.pop(-1)
                return convertion(parser)
        return []

    @staticmethod
    def write_file(file_descriptor, boxes):
        impl = xml.dom.minidom.getDOMImplementation()
        newdoc = impl.createDocument(None, "root", None)
        file_descriptor.write(_XHTML_HEADER)
        file_descriptor.write("<body>\n")
        for box in boxes:
            file_descriptor.write("<p>" + box.get_xml_tag(newdoc).toxml() + "</p>\n")
        file_descriptor.write("</body>\n</html>\n")

    def start_line(self, box):
        if len(self.lines) > 0 and self.lines[-1].content == "":
            return
        self.lines.append(LineBox([], box))

    def add_word(self, word, box, confidence=0):
        self.lines[-1].word_boxes.append(Box(word, box, confidence))

    def end_line(self):
        pass

    def get_output(self):
        return self.lines

    def __str__(self):
        return "Line boxes"


class DigitLineBoxBuilder(LineBoxBuilder):
    def __init__(self, tesseract_layout=1):
        super(DigitLineBoxBuilder, self).__init__(tesseract_layout)
        self.tesseract_configs.append("digits")

    def __str__(self):
        return "Digit line boxes"


class CharBoxBuilder(BaseBuilder):
    def __init__(self):
        super(CharBoxBuilder, self).__init__(["box"], [], ["batch.nochop", "makebox"], [])
        self.tesseract_layout = 1

    @staticmethod
    def read_file(file_descriptor):
        boxes = []
        for line in file_descriptor.readlines():
            line = line.strip()
            if line == "":
                continue
            elements = line.split(" ")
            if len(elements) < 6:
                continue
            position = (
                (int(elements[1]), int(elements[2])),
                (int(elements[3]), int(elements[4])),
            )
            boxes.append(Box(elements[0], position))
        return boxes

    @staticmethod
    def write_file(file_descriptor, boxes):
        for box in boxes:
            file_descriptor.write(str(box) + " 0\n")

    def __str__(self):
        return "Character boxes"


TESSERACT_CMD = "tesseract.exe" if os.name == "nt" else "tesseract"
TESSDATA_EXTENSION = ".traineddata"
g_subprocess_startup_info = None
g_creation_flags = 0
g_version = None


def _set_environment():
    global g_subprocess_startup_info
    global g_creation_flags
    if os.name == "nt":
        g_subprocess_startup_info = subprocess.STARTUPINFO()
        g_subprocess_startup_info.wShowWindow = subprocess.SW_HIDE
        g_subprocess_startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        g_creation_flags = 0x08000000
    if getattr(sys, "frozen", False):
        if "TESSDATA_PREFIX" in os.environ:
            return
        tesspath = Path(sys._MEIPASS).joinpath("tesseract")
        tessprefix = Path(sys._MEIPASS).joinpath("data")
        logger.info("Running in packaged environment")
        if not Path(tesspath).exists():
            logger.warning("Running from container, but no tesseract ({}) found !".format(tesspath))
        else:
            logger.info("[{}] added to PATH".format(tesspath))
            os.environ["PATH"] = tesspath + os.pathsep + os.environ["PATH"]
        if not Path(Path(tessprefix).joinpath("tessdata")).exists():
            logger.warning("Running from container, but no tessdata ({}) found !".format(tessprefix))
        else:
            version = tess_get_version(set_env=False)
            if version[0] > 3:
                tessprefix = Path(tessprefix).joinpath("tessdata")
            logger.info("TESSDATA_PREFIX set to [{}]".format(tessprefix))
            os.environ["TESSDATA_PREFIX"] = tessprefix


def psm_parameter():
    try:
        version = tess_get_version()
        return "--psm" if version[0] > 3 else "-psm"
    except Exception as exc:
        logger.opt(exception=exc).warning(
            "psm_parameter(): failed to get Tesseract version. Assuming Tesseract >= 4 --> using option '--psm'",
        )
        return "--psm"


def tess_can_detect_orientation():
    version = tess_get_version()
    langs = tess_get_available_languages()
    return (version[0] > 3 or (version[0] == 3 and version[1] >= 3)) and "osd" in langs


def tess_detect_orientation(image, lang=None):
    _set_environment()
    with tempfile.TemporaryDirectory() as tmpdir:
        command = [TESSERACT_CMD, "input.bmp", "stdout", psm_parameter(), "0"]
        version = tess_get_version()
        if lang is not None:
            if version[0] < 4:
                command += ["-l", lang]
            else:
                command += ["-l", "osd"]
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(Path(tmpdir).joinpath("input.bmp"))
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            shell=False,
            startupinfo=g_subprocess_startup_info,
            creationflags=g_creation_flags,
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        with proc:
            proc.stdin.close()
            original_output = proc.stdout.read()
            proc.wait()
        original_output = original_output.decode("utf-8").strip()
        if "Could not initialize tesseract" in original_output:
            raise TesseractError(-1, "Error initializing tesseract: %s" % original_output)
        try:
            output = original_output.split("\n")
            output = [line.split(": ", 1) for line in output if (": " in line)]
            output = dict(output)
            angle = int(output.get("Rotate", output["Orientation in degrees"]))
            angle = (360 - angle) % 360
            return {
                "angle": angle,
                "confidence": float(output["Orientation confidence"]),
            }
        except Exception as ex:
            raise TesseractError(-1, "No script found in image (%s - %s)" % (str(ex), original_output))


def tess_get_name():
    return "Tesseract (sh)"


def tess_get_available_builders():
    return [
        LineBoxBuilder,
        TextBuilder,
        WordBoxBuilder,
        CharBoxBuilder,
        DigitBuilder,
        DigitLineBoxBuilder,
    ]


def tess_run_tesseract(input_filename, output_filename_base, cwd=None, lang=None, flags=None, configs=None):
    _set_environment()
    command = [TESSERACT_CMD, input_filename, output_filename_base]
    if lang is not None:
        command += ["-l", lang]
    if flags is not None:
        command += flags
    if configs is not None:
        command += configs
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        startupinfo=g_subprocess_startup_info,
        creationflags=g_creation_flags,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    with proc:
        errors = proc.stdout.read()
        ret = proc.wait()
    return (ret, errors)


def tess_cleanup(filename):
    with contextlib.suppress(OSError):
        os.remove(filename)


class ReOpenableTempfile:
    def __init__(self, suffix):
        self.name = None
        with tempfile.NamedTemporaryFile(prefix="tess_", suffix=suffix, delete=False) as fp:
            self.name = fp.name

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def close(self):
        if self.name is not None:
            os.remove(self.name)
            self.name = None


def tess_image_to_string(image, lang=None, builder=None):
    if builder is None:
        builder = TextBuilder()
    with tempfile.TemporaryDirectory() as tmpdir:
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(Path(tmpdir).joinpath("input.bmp"))
        status, errors = tess_run_tesseract(
            "input.bmp",
            "output",
            cwd=tmpdir,
            lang=lang,
            flags=builder.tesseract_flags,
            configs=builder.tesseract_configs,
        )
        if status:
            raise TesseractError(status, errors)
        tested_files = []
        for file_extension in builder.file_extensions:
            output_file_name = "%s.%s" % (
                Path(tmpdir).joinpath("output"),
                file_extension,
            )
            tested_files.append(output_file_name)
            try:
                with codecs.open(output_file_name, "r", encoding="utf-8", errors="replace") as file_desc:
                    return builder.read_file(file_desc)
            except FileNotFoundError:
                continue
            finally:
                tess_cleanup(output_file_name)
        raise TesseractError(-1, "Unable to find output file (tested {})".format(tested_files))


def tess_is_available():
    _set_environment()
    return shutil.which(TESSERACT_CMD) is not None


def tess_get_available_languages():
    _set_environment()
    proc = subprocess.Popen(
        [TESSERACT_CMD, "--list-langs"],
        startupinfo=g_subprocess_startup_info,
        creationflags=g_creation_flags,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    with proc:
        langs = proc.stdout.read().decode("utf-8").splitlines(False)
        ret = proc.wait()
        if ret != 0:
            raise TesseractError(ret, "unable to get languages")
    return [lang for lang in langs if lang and lang[-1] != ":"]


def tess_get_version(set_env=True):
    global g_version
    if g_version is not None:
        return g_version
    if set_env:
        _set_environment()
    proc = subprocess.Popen(
        [TESSERACT_CMD, "-v"],
        startupinfo=g_subprocess_startup_info,
        creationflags=g_creation_flags,
        stdout=subprocess.PIPE,
    )
    with proc:
        ver_string = proc.stdout.read().decode("utf-8")
        ret = proc.wait()
        if ret not in (0, 1):
            raise TesseractError(ret, ver_string)
    try:
        ver_string = ver_string.split(" ")[1]
        els = [digits_only(x) for x in ver_string.split(".")]
        major = els[0]
        minor = els[1]
        upd = 0
        if len(els) >= 3:
            upd = els[2]
        version = (major, minor, upd)
        if version == (0, 0, 0):
            raise TesseractError(
                ret,
                ("Unable to parse Tesseract version (not a number): [%s]" % ver_string),
            )
        g_version = version
        return version
    except IndexError:
        raise TesseractError(
            ret,
            ("Unable to parse Tesseract version (spliting failed): [%s]" % ver_string),
        )


TESSDATA_PREFIX = os.getenv("TESSDATA_PREFIX", None)
DPI_DEFAULT = 70
libnames = []
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    libnames += [Path(sys._MEIPASS).joinpath("libtesseract-4.dll")]
    libnames += [Path(sys._MEIPASS).joinpath("libtesseract-3.dll")]
    tessdata = Path(sys._MEIPASS).joinpath("data")
    if not Path(Path(tessdata).joinpath("tessdata")).exists():
        logger.warning("Running from container, but no tessdata ({}) found !".format(tessdata))
    else:
        TESSDATA_PREFIX = Path(tessdata).joinpath("tessdata")
if sys.platform[:3] == "win":
    libnames += [
        "../vs2010/DLL_Release/libtesseract302.dll",
        "libtesseract305.dll",
        "libtesseract304.dll",
        "libtesseract303.dll",
        "libtesseract302.dll",
        "libtesseract400.dll",
        "libtesseract.dll",
        "C:\\Program Files (x86)\\Tesseract-OCR\\libtesseract-4.dll",
        "C:\\Program Files (x86)\\Tesseract-OCR\\libtesseract-3.dll",
    ]
else:
    libnames += [
        "libtesseract.so.5",
        "libtesseract.so.4",
        "libtesseract.so.3",
        "libtesseract.5.dylib",
        "libtesseract.4.dylib",
    ]
g_libtesseract = None
lib_load_errors = []
for libname in libnames:
    try:
        g_libtesseract = ctypes.cdll.LoadLibrary(libname)
        lib_load_errors = []
        break
    except OSError as ex:
        if hasattr(ex, "message"):
            lib_load_errors.append((libname, ex.message))
        else:
            lib_load_errors.append((libname, str(ex)))


class PageSegMode:
    OSD_ONLY = 0
    AUTO_OSD = 1
    AUTO_ONLY = 2
    AUTO = 3
    SINGLE_COLUMN = 4
    SINGLE_BLOCK_VERT_TEXT = 5
    SINGLE_BLOCK = 6
    SINGLE_LINE = 7
    SINGLE_WORD = 8
    CIRCLE_WORD = 9
    SINGLE_CHAR = 10
    SPARSE_TEXT = 11
    SPARSE_TEXT_OSD = 12
    PSM_RAW_LINE = 13
    COUNT = 14


class Orientation:
    PAGE_UP = 0
    PAGE_RIGHT = 1
    PAGE_DOWN = 2
    PAGE_LEFT = 3


class PageIteratorLevel:
    BLOCK = 0
    PARA = 1
    TEXTLINE = 2
    WORD = 3
    SYMBOL = 4


class PolyBlockType:
    UNKNOWN = 0
    FLOWING_TEXT = 1
    HEADING_TEXT = 2
    PULLOUT_TEXT = 3
    TABLE = 4
    VERTICAL_TEXT = 5
    CAPTION_TEXT = 6
    FLOWING_IMAGE = 7
    HEADING_IMAGE = 8
    PULLOUT_IMAGE = 9
    HORZ_LINE = 10
    VERT_LINE = 11
    NOISE = 12
    COUNT = 13


class OSResults(ctypes.Structure):
    _fields_ = [
        ("orientations", ctypes.c_float * 4),
        ("scripts_na", ctypes.c_float * 4 * (116 + 1 + 2 + 1)),
        ("unicharset", ctypes.c_void_p),
        ("best_orientation_id", ctypes.c_int),
        ("best_script_id", ctypes.c_int),
        ("best_sconfidence", ctypes.c_float),
        ("best_oconfidence", ctypes.c_float),
        ("padding", ctypes.c_char * 512),
    ]


if g_libtesseract:
    g_libtesseract.TessVersion.argtypes = []
    g_libtesseract.TessVersion.restype = ctypes.c_char_p
    g_libtesseract.TessBaseAPICreate.argtypes = []
    g_libtesseract.TessBaseAPICreate.restype = ctypes.c_void_p
    g_libtesseract.TessBaseAPIDelete.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIDelete.argtypes = None
    g_libtesseract.TessBaseAPIGetDatapath.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIGetDatapath.restype = ctypes.POINTER(ctypes.c_char)
    g_libtesseract.TessBaseAPIInit1.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int,
    ]
    g_libtesseract.TessBaseAPIInit1.restype = ctypes.c_int
    g_libtesseract.TessBaseAPIInit3.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
    ]
    g_libtesseract.TessBaseAPIInit3.restype = ctypes.c_int
    g_libtesseract.TessBaseAPISetSourceResolution.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    g_libtesseract.TessBaseAPISetSourceResolution.restype = None
    g_libtesseract.TessBaseAPISetVariable.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
    ]
    g_libtesseract.TessBaseAPISetVariable.restype = ctypes.c_bool
    g_libtesseract.TessBaseAPIGetAvailableLanguagesAsVector.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIGetAvailableLanguagesAsVector.restype = ctypes.POINTER(ctypes.c_char_p)
    g_libtesseract.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    g_libtesseract.TessBaseAPISetPageSegMode.restype = None
    g_libtesseract.TessBaseAPIInitForAnalysePage.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIInitForAnalysePage.restype = None
    g_libtesseract.TessBaseAPISetImage.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    g_libtesseract.TessBaseAPISetImage.restype = None
    g_libtesseract.TessResultRendererAddImage.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    g_libtesseract.TessResultRendererAddImage.restype = ctypes.c_bool
    g_libtesseract.TessBaseAPISetInputName.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    g_libtesseract.TessBaseAPISetInputName.restype = None
    g_libtesseract.TessResultRendererBeginDocument.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
    ]
    g_libtesseract.TessResultRendererBeginDocument.restype = ctypes.c_bool
    g_libtesseract.TessResultRendererEndDocument.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessResultRendererEndDocument.restype = ctypes.c_bool
    g_libtesseract.TessPDFRendererCreate.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_bool,
    ]
    g_libtesseract.TessPDFRendererCreate.restype = ctypes.c_void_p
    g_libtesseract.TessBaseAPIRecognize.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g_libtesseract.TessBaseAPIRecognize.restype = ctypes.c_int
    g_libtesseract.TessBaseAPIGetIterator.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIGetIterator.restype = ctypes.c_void_p
    g_libtesseract.TessBaseAPIAnalyseLayout.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIAnalyseLayout.restype = ctypes.c_void_p
    g_libtesseract.TessBaseAPIGetUTF8Text.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
    g_libtesseract.TessPageIteratorDelete.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessPageIteratorDelete.restype = None
    g_libtesseract.TessPageIteratorOrientation.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_float),
    ]
    g_libtesseract.TessPageIteratorOrientation.restype = None
    g_libtesseract.TessPageIteratorNext.argtypes = [ctypes.c_void_p, ctypes.c_int]
    g_libtesseract.TessPageIteratorNext.restype = ctypes.c_bool
    g_libtesseract.TessPageIteratorIsAtBeginningOf.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    g_libtesseract.TessPageIteratorIsAtBeginningOf.restype = ctypes.c_bool
    g_libtesseract.TessPageIteratorIsAtFinalElement.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    g_libtesseract.TessPageIteratorIsAtFinalElement.restype = ctypes.c_bool
    g_libtesseract.TessPageIteratorBlockType.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessPageIteratorBlockType.restype = ctypes.c_int
    g_libtesseract.TessPageIteratorBoundingBox.args = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    g_libtesseract.TessPageIteratorBoundingBox.restype = ctypes.c_bool
    g_libtesseract.TessResultIteratorGetPageIterator.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessResultIteratorGetPageIterator.restype = ctypes.c_void_p
    g_libtesseract.TessResultIteratorGetUTF8Text.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    g_libtesseract.TessResultIteratorGetUTF8Text.restype = ctypes.c_void_p
    g_libtesseract.TessResultIteratorConfidence.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    g_libtesseract.TessResultIteratorConfidence.restype = ctypes.c_float
    g_libtesseract.TessDeleteText.argtypes = [ctypes.c_void_p]
    g_libtesseract.TessDeleteText.restype = None
    if hasattr(g_libtesseract, "TessBaseAPIDetectOrientationScript"):
        g_libtesseract.TessBaseAPIDetectOrientationScript.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_float),
        ]
        g_libtesseract.TessBaseAPIDetectOrientationScript.restype = ctypes.c_bool
    else:
        g_libtesseract.TessBaseAPIDetectOS.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(OSResults),
        ]
        g_libtesseract.TessBaseAPIDetectOS.restype = ctypes.c_bool


def raw_init(lang=None):
    assert g_libtesseract
    if raw_get_version() == "4.0.0":
        locale.setlocale(locale.LC_ALL, "C")
    handle = g_libtesseract.TessBaseAPICreate()
    try:
        if lang:
            lang = lang.encode("utf-8")
        prefix = None
        if TESSDATA_PREFIX:
            prefix = TESSDATA_PREFIX.encode("utf-8")
        g_libtesseract.TessBaseAPIInit3(ctypes.c_void_p(handle), ctypes.c_char_p(prefix), ctypes.c_char_p(lang))
        g_libtesseract.TessBaseAPISetVariable(ctypes.c_void_p(handle), b"tessedit_zero_rejection", b"F")
    except Exception:
        g_libtesseract.TessBaseAPIDelete(ctypes.c_void_p(handle))
        raise
    return handle


def raw_cleanup(handle):
    assert g_libtesseract is not None
    g_libtesseract.TessBaseAPIDelete(ctypes.c_void_p(handle))


def raw_is_available():
    return g_libtesseract is not None


def raw_get_version():
    assert g_libtesseract is not None
    return g_libtesseract.TessVersion().decode("utf-8")


def raw_get_available_languages(handle):
    assert g_libtesseract is not None
    langs = []
    c_langs = g_libtesseract.TessBaseAPIGetAvailableLanguagesAsVector(ctypes.c_void_p(handle))
    i = 0
    while c_langs[i]:
        langs.append(c_langs[i].decode("utf-8"))
        i += 1
    return langs


def raw_set_is_numeric(handle, mode):
    assert g_libtesseract is not None
    wl = b"0123456789." if mode else b""
    g_libtesseract.TessBaseAPISetVariable(ctypes.c_void_p(handle), b"tessedit_char_whitelist", wl)


def raw_set_debug_file(handle, filename):
    assert g_libtesseract is not None
    if not isinstance(filename, bytes):
        filename = filename.encode("utf-8")
    g_libtesseract.TessBaseAPISetVariable(ctypes.c_void_p(handle), b"debug_file", filename)


def raw_set_page_seg_mode(handle, mode):
    assert g_libtesseract is not None
    g_libtesseract.TessBaseAPISetPageSegMode(ctypes.c_void_p(handle), ctypes.c_int(mode))


def raw_init_for_analyse_page(handle):
    assert g_libtesseract is not None
    g_libtesseract.TessBaseAPIInitForAnalysePage(ctypes.c_void_p(handle))


def raw_set_image(handle, image):
    assert g_libtesseract is not None
    image = image.convert("RGB")
    image.load()
    imgdata = image.tobytes("raw", "RGB")
    g_libtesseract.TessBaseAPISetImage(
        ctypes.c_void_p(handle),
        imgdata,
        ctypes.c_int(image.width),
        ctypes.c_int(image.height),
        ctypes.c_int(3),
        ctypes.c_int(image.width * 3),
    )
    dpi = image.info.get("dpi", [DPI_DEFAULT])[0]
    g_libtesseract.TessBaseAPISetSourceResolution(ctypes.c_void_p(handle), dpi)


def raw_recognize(handle):
    assert g_libtesseract is not None
    return g_libtesseract.TessBaseAPIRecognize(ctypes.c_void_p(handle), ctypes.c_void_p(None))


def raw_analyse_layout(handle):
    assert g_libtesseract is not None
    return g_libtesseract.TessBaseAPIAnalyseLayout(ctypes.c_void_p(handle))


def raw_get_utf8_text(handle):
    assert g_libtesseract is not None
    ptr = g_libtesseract.TessBaseAPIGetUTF8Text(ctypes.c_void_p(handle))
    val = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
    g_libtesseract.TessDeleteText(ptr)
    return val


def raw_page_iterator_delete(iterator):
    assert g_libtesseract is not None
    return g_libtesseract.TessPageIteratorDelete(ctypes.c_void_p(iterator))


def raw_page_iterator_next(iterator, level):
    assert g_libtesseract is not None
    return g_libtesseract.TessPageIteratorNext(ctypes.c_void_p(iterator), level)


def raw_page_iterator_is_at_beginning_of(iterator, level):
    assert g_libtesseract is not None
    return g_libtesseract.TessPageIteratorIsAtBeginningOf(ctypes.c_void_p(iterator), level)


def raw_page_iterator_is_at_final_element(iterator, level, element):
    assert g_libtesseract is not None
    return g_libtesseract.TessPageIteratorIsAtFinalElement(ctypes.c_void_p(iterator), level, element)


def raw_page_iterator_block_type(iterator):
    assert g_libtesseract is not None
    return g_libtesseract.TessPageIteratorBlockType(ctypes.c_void_p(iterator))


def raw_page_iterator_bounding_box(iterator, level):
    assert g_libtesseract is not None
    left = ctypes.c_int(0)
    top = ctypes.c_int(0)
    right = ctypes.c_int(0)
    bottom = ctypes.c_int(0)
    r = g_libtesseract.TessPageIteratorBoundingBox(
        ctypes.c_void_p(iterator),
        level,
        ctypes.pointer(left),
        ctypes.pointer(top),
        ctypes.pointer(right),
        ctypes.pointer(bottom),
    )
    if not r:
        return (False, (0, 0, 0, 0))
    return (True, (left.value, top.value, right.value, bottom.value))


def raw_page_iterator_orientation(iterator):
    assert g_libtesseract is not None
    orientation = ctypes.c_int(0)
    writing_direction = ctypes.c_int(0)
    textline_order = ctypes.c_int(0)
    deskew_angle = ctypes.c_float(0.0)
    g_libtesseract.TessPageIteratorOrientation(
        ctypes.c_void_p(iterator),
        ctypes.pointer(orientation),
        ctypes.pointer(writing_direction),
        ctypes.pointer(textline_order),
        ctypes.pointer(deskew_angle),
    )
    return {
        "orientation": orientation.value,
        "writing_direction": writing_direction.value,
        "textline_order": textline_order.value,
        "deskew_angle": deskew_angle.value,
    }


def raw_get_iterator(handle):
    assert g_libtesseract is not None
    return g_libtesseract.TessBaseAPIGetIterator(ctypes.c_void_p(handle))


def raw_result_iterator_get_page_iterator(res_iterator):
    assert g_libtesseract is not None
    return g_libtesseract.TessResultIteratorGetPageIterator(ctypes.c_void_p(res_iterator))


def raw_result_iterator_get_utf8_text(iterator, level):
    assert g_libtesseract is not None
    ptr = g_libtesseract.TessResultIteratorGetUTF8Text(ctypes.c_void_p(iterator), level)
    if ptr is None:
        return None
    val = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
    g_libtesseract.TessDeleteText(ptr)
    return val


def raw_result_iterator_get_confidence(iterator, level):
    assert g_libtesseract is not None
    ptr = g_libtesseract.TessResultIteratorConfidence(ctypes.c_void_p(iterator), level)
    if ptr is None:
        return None
    return ctypes.c_float(ptr).value


def raw_detect_os(handle):
    assert g_libtesseract is not None
    if hasattr(g_libtesseract, "TessBaseAPIDetectOrientationScript"):
        orientation_deg = ctypes.c_int(0)
        orientation_confidence = ctypes.c_float(0.0)
        r = g_libtesseract.TessBaseAPIDetectOrientationScript(
            ctypes.c_void_p(handle),
            ctypes.byref(orientation_deg),
            ctypes.byref(orientation_confidence),
            None,
            None,
        )
        if not r:
            msg = "detect_orientation failed"
            raise TesseractError(
                msg,
                "TessBaseAPIDetectOrientationScript() failed",
            )
        return {
            "orientation": round(orientation_deg.value / 90),
            "confidence": orientation_confidence.value,
        }
    else:
        results = OSResults()
        r = g_libtesseract.TessBaseAPIDetectOS(ctypes.c_void_p(handle), ctypes.pointer(results))
        if not r:
            msg = "detect_orientation failed"
            raise TesseractError(msg, "TessBaseAPIDetectOS() failed")
        return {
            "orientation": results.best_orientation_id,
            "confidence": results.best_oconfidence,
        }


def raw_set_input_name(handle, input_file):
    assert g_libtesseract is not None
    g_libtesseract.TessBaseAPISetInputName(ctypes.c_void_p(handle), input_file.encode())


def raw_init_pdf_renderer(handle, output_file, textonly):
    assert g_libtesseract is not None
    tessdata_dir = g_libtesseract.TessBaseAPIGetDatapath(handle)
    return g_libtesseract.TessPDFRendererCreate(output_file.encode(), tessdata_dir, ctypes.c_bool(textonly))


def raw_begin_document(renderer, doc_name):
    assert g_libtesseract is not None
    g_libtesseract.TessResultRendererBeginDocument(ctypes.c_void_p(renderer), doc_name.encode())


def raw_add_renderer_image(handle, renderer):
    assert g_libtesseract is not None
    g_libtesseract.TessResultRendererAddImage(ctypes.c_void_p(renderer), ctypes.c_void_p(handle))


def raw_end_document(renderer):
    assert g_libtesseract is not None
    g_libtesseract.TessResultRendererEndDocument(ctypes.c_void_p(renderer))


def libtess_can_detect_orientation():
    return "osd" in libtess_get_available_languages()


def libtess_detect_orientation(image, lang=None):
    handle = raw_init(lang="osd")
    try:
        raw_set_page_seg_mode(handle, PageSegMode.OSD_ONLY)
        raw_set_image(handle, image)
        os_result = raw_detect_os(handle)
        if os_result["confidence"] <= 0:
            msg = "no script"
            raise TesseractError(msg, "no script detected")
        orientation = {
            Orientation.PAGE_UP: 0,
            Orientation.PAGE_RIGHT: 90,
            Orientation.PAGE_DOWN: 180,
            Orientation.PAGE_LEFT: 270,
        }[os_result["orientation"]]
        return {"angle": orientation, "confidence": os_result["confidence"]}
    finally:
        raw_cleanup(handle)


def libtess_get_name():
    return "Tesseract (C-API)"


def libtess_get_available_builders():
    return [
        TextBuilder,
        WordBoxBuilder,
        DigitBuilder,
        LineBoxBuilder,
        DigitLineBoxBuilder,
    ]


def _tess_box_to_pyocr_box(box):
    return ((box[0], box[1]), (box[2], box[3]))


def libtess_image_to_string(image, lang=None, builder=None):
    if builder is None:
        builder = TextBuilder()
    handle = raw_init(lang=lang)
    lvl_line = PageIteratorLevel.TEXTLINE
    lvl_word = PageIteratorLevel.WORD
    try:
        clang = lang or "eng"
        for lang_item in clang.split("+"):
            if lang_item not in raw_get_available_languages(handle):
                msg = "no lang"
                raise TesseractError(msg, "language {} is not available".format(lang_item))
        raw_set_page_seg_mode(handle, builder.tesseract_layout)
        raw_set_debug_file(handle, os.devnull)
        raw_set_image(handle, image)
        if "digits" in builder.tesseract_configs:
            raw_set_is_numeric(handle, True)
        raw_recognize(handle)
        res_iterator = raw_get_iterator(handle)
        if res_iterator is None:
            msg = "no script"
            raise TesseractError(msg, "no script detected")
        page_iterator = raw_result_iterator_get_page_iterator(res_iterator)
        while True:
            if raw_page_iterator_is_at_beginning_of(page_iterator, lvl_line):
                r, box = raw_page_iterator_bounding_box(page_iterator, lvl_line)
                assert r
                builder.start_line(_tess_box_to_pyocr_box(box))
            last_word_in_line = raw_page_iterator_is_at_final_element(page_iterator, lvl_line, lvl_word)
            word = raw_result_iterator_get_utf8_text(res_iterator, lvl_word)
            confidence = raw_result_iterator_get_confidence(res_iterator, lvl_word)
            if word is not None and confidence is not None and word != "":
                r, box = raw_page_iterator_bounding_box(page_iterator, lvl_word)
                assert r
                builder.add_word(word, _tess_box_to_pyocr_box(box), confidence)
                if last_word_in_line:
                    builder.end_line()
            if not raw_page_iterator_next(page_iterator, lvl_word):
                break
    finally:
        raw_cleanup(handle)
    return builder.get_output()


def libtess_image_to_pdf(image, output_file, lang=None, input_file="stdin", textonly=False):
    LibtesseractPdfBuilder().set_lang(lang).set_output_file(output_file).set_text_only(textonly).add_image(
        image
    ).build()


class LibtesseractPdfBuilder:
    def __init__(self):
        self.images = []
        self.output_file = None
        self.lang = None
        self.text_only = False

    def set_lang(self, lang):
        self.lang = lang
        return self

    def set_output_file(self, output_file):
        self.output_file = output_file
        return self

    def set_text_only(self, text_only):
        self.text_only = text_only
        return self

    def add_image(self, img):
        self.images.append(img)
        return self

    def __validate(self):
        if len(self.images) < 1:
            msg = "At least one image is required to build the pdf!"
            raise ValueError(msg)
        if self.output_file is None:
            msg = "An output-file is required to build the pdf!"
            raise ValueError(msg)

    def build(self):
        self.__validate()
        handle = raw_init(lang=self.lang)
        renderer = None
        try:
            raw_set_page_seg_mode(handle, PageSegMode.AUTO_OSD)
            renderer = raw_init_pdf_renderer(handle, self.output_file, self.text_only)
            assert renderer
            raw_begin_document(renderer, "")
            for image in self.images:
                raw_set_image(handle, image)
                raw_recognize(handle)
                raw_add_renderer_image(handle, renderer)
            raw_end_document(renderer)
        finally:
            raw_cleanup(handle)
            if renderer:
                raw_cleanup(renderer)


def libtess_is_available():
    if not raw_is_available():
        return False
    version = libtess_get_version()
    if version[0] < 3 or (version[0] == 3 and version[1] < 4):
        logger.warning("Unsupported version [%s]" % ".".join([str(r) for r in version]))
        return False
    return True


def libtess_get_available_languages():
    handle = raw_init()
    try:
        return raw_get_available_languages(handle)
    finally:
        raw_cleanup(handle)


def libtess_get_version():
    version = raw_get_version()
    version = version.split(" ", 1)[0]
    index = version.find("dev")
    if index != -1:
        version = version[:index]
    version = version.split(".")
    major = digits_only(version[0])
    minor = digits_only(version[1])
    upd = 0
    if len(version) >= 3:
        upd = digits_only(version[2])
    return (major, minor, upd)


CUNEIFORM_CMD = "cuneiform"
LANGUAGES_LINE_PREFIX = "Supported languages: "
LANGUAGES_SPLIT_RE = re.compile("[^a-z]")
VERSION_LINE_RE = re.compile(r"Cuneiform for \w+ (\d+).(\d+).(\d+)")


def cun_can_detect_orientation():
    return False


def cun_get_name():
    return "Cuneiform (sh)"


def cun_get_available_builders():
    return [TextBuilder, WordBoxBuilder, LineBoxBuilder]


def _cun_temp_file(suffix):
    return tempfile.NamedTemporaryFile(prefix="cuneiform_", suffix=suffix)


def cun_image_to_string(image, lang=None, builder=None):
    if builder is None:
        builder = TextBuilder()
    if "digits" in builder.tesseract_configs:
        msg = "Numerical only : This option is not available with Cuneiform"
        raise NotImplementedError(msg)
    with _cun_temp_file(builder.file_extensions[0]) as output_file:
        cmd = [CUNEIFORM_CMD]
        if lang is not None:
            cmd += ["-l", lang]
        cmd += builder.cuneiform_args
        cmd += ["-o", output_file.name]
        cmd += ["-"]
        if image.mode != "RGB":
            image = image.convert("RGB")
        img_data = BytesIO()
        image.save(img_data, format="BMP")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        proc.stdin.write(img_data.getvalue())
        proc.stdin.close()
        output = proc.stdout.read().decode("utf-8")
        retcode = proc.wait()
        if retcode:
            raise CuneiformError(retcode, output)
        with codecs.open(output_file.name, "r", encoding="utf-8", errors="replace") as file_desc:
            return builder.read_file(file_desc)


def cun_is_available():
    return shutil.which(CUNEIFORM_CMD) is not None


def cun_get_available_languages():
    proc = subprocess.Popen([CUNEIFORM_CMD, "-l"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.stdout.read().decode("utf-8")
    proc.wait()
    languages = []
    for line in output.split("\n"):
        if not line.startswith(LANGUAGES_LINE_PREFIX):
            continue
        line = line[len(LANGUAGES_LINE_PREFIX) :]
        for language in LANGUAGES_SPLIT_RE.split(line):
            if language == "":
                continue
            languages.append(language)
    return languages


def cun_get_version():
    proc = subprocess.Popen([CUNEIFORM_CMD], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.stdout.read().decode("utf-8")
    proc.wait()
    for line in output.split("\n"):
        m = VERSION_LINE_RE.match(line)
        if m is not None:
            g = m.groups()
            return (int(g[0]), int(g[1]), int(g[2]))
    return None


tesseract_ns = SimpleNamespace(
    can_detect_orientation=tess_can_detect_orientation,
    detect_orientation=tess_detect_orientation,
    get_available_builders=tess_get_available_builders,
    get_available_languages=tess_get_available_languages,
    get_name=tess_get_name,
    get_version=tess_get_version,
    image_to_string=tess_image_to_string,
    is_available=tess_is_available,
    TesseractError=TesseractError,
)
libtesseract_ns = SimpleNamespace(
    can_detect_orientation=libtess_can_detect_orientation,
    detect_orientation=libtess_detect_orientation,
    get_available_builders=libtess_get_available_builders,
    get_available_languages=libtess_get_available_languages,
    get_name=libtess_get_name,
    get_version=libtess_get_version,
    image_to_string=libtess_image_to_string,
    image_to_pdf=libtess_image_to_pdf,
    is_available=libtess_is_available,
    TesseractError=TesseractError,
)
cuneiform_ns = SimpleNamespace(
    can_detect_orientation=cun_can_detect_orientation,
    get_available_builders=cun_get_available_builders,
    get_available_languages=cun_get_available_languages,
    get_name=cun_get_name,
    get_version=cun_get_version,
    image_to_string=cun_image_to_string,
    is_available=cun_is_available,
    CuneiformError=CuneiformError,
)
TOOLS = [tesseract_ns, libtesseract_ns, cuneiform_ns]


def get_available_tools():
    return [tool for tool in TOOLS if tool.is_available()]


_BUILDERS = {
    "text": TextBuilder,
    "digit": DigitBuilder,
    "word": WordBoxBuilder,
    "line": LineBoxBuilder,
    "digit_line": DigitLineBoxBuilder,
    "char": CharBoxBuilder,
}
_TOOLS_BY_NAME = {
    "tesseract": tesseract_ns,
    "libtesseract": libtesseract_ns,
    "cuneiform": cuneiform_ns,
}


def _load_image(path):
    try:
        from PIL import Image
    except ImportError:
        msg = "Pillow is required: pip install Pillow"
        raise SystemExit(msg)
    return Image.open(path)


def _print_result(result):
    if isinstance(result, str):
        sys.stdout.write(result)
        if not result.endswith("\n"):
            sys.stdout.write("\n")
        return
    for item in result:
        sys.stdout.write(str(item) + "\n")


def _cmd_tools(args):
    for tool in TOOLS:
        status = "available" if tool.is_available() else "unavailable"
        try:
            version = tool.get_version()
        except Exception:
            version = None
        name = tool.get_name()
        ver = ".".join(str(v) for v in version) if version else "-"
        print(f"{name}\t{status}\t{ver}")


def _cmd_langs(args):
    tool = _TOOLS_BY_NAME[args.tool]
    if not tool.is_available():
        msg = f"tool {args.tool} is not available"
        raise SystemExit(msg)
    for lang in tool.get_available_languages():
        print(lang)


def _cmd_orientation(args):
    tool = _TOOLS_BY_NAME[args.tool]
    if not tool.is_available():
        msg = f"tool {args.tool} is not available"
        raise SystemExit(msg)
    if not tool.can_detect_orientation():
        msg = f"tool {args.tool} cannot detect orientation"
        raise SystemExit(msg)
    image = _load_image(args.image)
    result = tool.detect_orientation(image, lang=args.lang)
    print(f"angle: {result['angle']}")
    print(f"confidence: {result['confidence']}")


def _cmd_ocr(args):
    tool = _TOOLS_BY_NAME[args.tool]
    if not tool.is_available():
        msg = f"tool {args.tool} is not available"
        raise SystemExit(msg)
    builder = _BUILDERS[args.builder]()
    image = _load_image(args.image)
    result = tool.image_to_string(image, lang=args.lang, builder=builder)
    _print_result(result)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pyocr", description="OCR CLI")
    parser.add_argument("-v", "--version", action="version", version="pyocr " + __version__)
    sub = parser.add_subparsers(dest="command")
    p_tools = sub.add_parser("tools", help="list OCR tools")
    p_tools.set_defaults(func=_cmd_tools)
    p_langs = sub.add_parser("langs", help="list available languages")
    p_langs.add_argument("-t", "--tool", choices=list(_TOOLS_BY_NAME), default="tesseract")
    p_langs.set_defaults(func=_cmd_langs)
    p_orient = sub.add_parser("orientation", help="detect image orientation")
    p_orient.add_argument("image")
    p_orient.add_argument("-t", "--tool", choices=list(_TOOLS_BY_NAME), default="tesseract")
    p_orient.add_argument("-l", "--lang", default=None)
    p_orient.set_defaults(func=_cmd_orientation)
    p_ocr = sub.add_parser("ocr", help="run OCR on an image")
    p_ocr.add_argument("image")
    p_ocr.add_argument("-t", "--tool", choices=list(_TOOLS_BY_NAME), default="tesseract")
    p_ocr.add_argument("-l", "--lang", default=None)
    p_ocr.add_argument(
        "-b",
        "--builder",
        choices=list(_BUILDERS),
        default="text",
        help="output builder: text, digit, word, line, digit_line, char",
    )
    p_ocr.set_defaults(func=_cmd_ocr)
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        args.func(args)
    except PyocrException as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
