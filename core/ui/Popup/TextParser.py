import re
import urllib.parse
import pygments.util
from PyQt5.QtCore import QThread, pyqtSignal
from markdown2 import markdown
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter


class ParserWorker(QThread):
    finished_signal = pyqtSignal(list, str)

    IMAGE_EXT_RE = re.compile(
        r"\.(?:png|jpg|jpeg|gif|webp|bmp|svg|avif|ico|tiff)(?:[?#][^\s<>\"')]*)?$",
        re.IGNORECASE,
    )
    KNOWN_IMG_HOST_RE = re.compile(
        r"^https?://(?:"
        r"images\.unsplash\.com/(?:photo|flagged/photo)-[a-zA-Z0-9_-]+|"
        r"plus\.unsplash\.com/premium_photo-[a-zA-Z0-9_-]+|"
        r"pbs\.twimg\.com/media/[a-zA-Z0-9_-]+|"
        r"i\.imgur\.com/[a-zA-Z0-9_-]+|"
        r"media\d*\.giphy\.com/media/[a-zA-Z0-9_/-]+|"
        r"picsum\.photos/[^\s<>\"')]+"
        r")(?:[?#][^\s<>\"')]*)?$",
        re.IGNORECASE,
    )
    QUERY_IMG_FORMAT_RE = re.compile(
        r"[?&](?:format|ext|fm)=(?:jpg|jpeg|png|webp|gif|avif)(?:&|$)",
        re.IGNORECASE,
    )

    def __init__(self, full_text, eng_font, hin_font, is_streaming=False):
        super().__init__()
        self.full_text = str(full_text or "")
        self.eng_font = eng_font
        self.hin_font = hin_font
        self.is_streaming = bool(is_streaming)
        self.protected_elements = {}
        self.is_cancelled = False

    def protect(self, html):
        token = f"<jarvis-token-{len(self.protected_elements)}/>"
        self.protected_elements[token] = html
        return token

    def strip_leading_emotion_tag(self, text):
        return re.sub(r"^\s*\[([a-zA-Z_ -]{2,20})\]\s*", "", text, count=1)

    def is_image_url(self, url):
        clean = str(url or "").strip()
        if not clean:
            return False
        if self.IMAGE_EXT_RE.search(clean):
            return True
        if self.KNOWN_IMG_HOST_RE.match(clean):
            return True
        if clean.startswith(("http://", "https://")) and self.QUERY_IMG_FORMAT_RE.search(clean):
            return True
        return False

    def format_local_or_web_url(self, url):
        formatted_url = str(url or "").strip()
        if formatted_url.startswith(("http://", "https://", "file://")):
            return formatted_url
        raw_path = formatted_url.replace("\\", "/")
        encoded_path = urllib.parse.quote(raw_path, safe=":/")
        if re.match(r"^[a-zA-Z]:", formatted_url):
            return "file:///" + encoded_path
        return "file://" + encoded_path

    def build_image_card_html(self, img_url, alt="img_preview", caption_text="", caption_url=""):
        safe_url = self.format_local_or_web_url(img_url)
        header_html = ""
        if caption_text and caption_url:
            header_html = (
                f'<a href="{caption_url}" style="text-decoration:none; color:#C48AF9; '
                f'font-weight:600;">{caption_text} ↗</a><br>'
            )
        return self.protect(
            f'<p style="margin-top: 10px; margin-bottom: 10px; line-height: 1.25;">'
            f'{header_html}'
            f'<a href="imgpreview://{safe_url}" style="text-decoration:none;">'
            f'<img src="img://{safe_url}" width="420" height="236" alt="{alt}"></a></p>'
        )

    def build_loading_card_html(self, kind="preview"):
        if kind == "image":
            return self.protect(
                '<p style="margin-top: 10px; margin-bottom: 10px; line-height: 1.0;">'
                '<img src="loading://image" width="420" height="236" alt="loading_image"></p>'
            )
        return self.protect(
            '<p style="margin-top: 10px; margin-bottom: 8px; line-height: 1.0;">'
            '<img src="loading://preview" width="420" height="286" alt="loading_preview"></p>'
        )

    def normalize_streaming_fences(self, text):
        fence_matches = list(re.finditer(r"(?m)^[ ]{0,3}```", text))
        if len(fence_matches) % 2 == 1:
            return text + "\n```"
        return text

    def stabilize_streaming_tail(self, text):
        if not self.is_streaming or not text:
            return text

        text = re.sub(
            r"!\[[^\]]*\]\([^\)\s]*$",
            lambda _: self.build_loading_card_html("image"),
            text,
        )
        text = re.sub(
            r"!\[[^\]]*$",
            lambda _: self.build_loading_card_html("image"),
            text,
        )

        def tail_md_link_replacer(match):
            partial_url = match.group(1)
            if self.is_image_url(partial_url):
                return self.build_loading_card_html("image")
            return self.build_loading_card_html("preview")

        text = re.sub(r"\[[^\]]+\]\((https?://[^\)\s]*)$", tail_md_link_replacer, text)

        def tail_raw_url_replacer(match):
            partial_url = match.group(1)
            if self.is_image_url(partial_url):
                return self.build_loading_card_html("image")
            return self.build_loading_card_html("preview")

        text = re.sub(r'(?<!=["\'])(https?://[^\s<>"\')]+)$', tail_raw_url_replacer, text)
        return text

    def process_markdown_code_blocks(self, text):
        pattern = r"(?m)^[ ]{0,3}```([^\n]*)\n(.*?)^[ ]{0,3}```"

        def replacer(match):
            lang = match.group(1).strip().lower()
            raw_code = match.group(2).rstrip("\n")
            formatter = HtmlFormatter(style="monokai", noclasses=True, nowrap=True)

            if not lang:
                lang = "code"
            try:
                lexer = get_lexer_by_name(lang, stripall=False)
            except pygments.util.ClassNotFound:
                lexer = get_lexer_by_name("text")

            highlighted = highlight(raw_code, lexer, formatter)
            html = (
                f'<table width="100%" cellspacing="0" cellpadding="1" bgcolor="#32333D" '
                f'style="margin-top: 12px; margin-bottom: 14px;">'
                f'<tr><td>'
                f'<table width="100%" cellspacing="0" cellpadding="0" bgcolor="#131419">'
                f'<tr><td bgcolor="#1F2028" style="padding: 7px 14px;">'
                f'<span style="color:#FF5F56; font-size:13px;">●</span>&nbsp;'
                f'<span style="color:#FFBD2E; font-size:13px;">●</span>&nbsp;'
                f'<span style="color:#27C93F; font-size:13px;">●</span>'
                f'&nbsp;&nbsp;&nbsp;<span style="color: #8E8E98; font-size: 9.5pt; font-weight: bold; '
                f'font-family: \'{self.eng_font}\', sans-serif; letter-spacing: 0.8px;">{lang.upper()}</span>'
                f'</td></tr>'
                f'<tr><td style="padding: 13px 15px;">'
                f'<pre style="margin: 0; font-family: \'Cascadia Code\', Consolas, monospace; '
                f'font-size: 10.5pt; line-height: 1.45; color: #E5E5EA; white-space: pre-wrap; word-wrap: break-word;">'
                f'{highlighted}</pre>'
                f'</td></tr></table>'
                f'</td></tr></table>'
            )
            return self.protect(html)

        return re.sub(pattern, replacer, text, flags=re.DOTALL)

    def process_md_images(self, text):
        pattern = r"!\[([^\]]*)\]\(([^)]+)\)"

        def replacer(match):
            alt, url = match.groups()
            clean_url = url.strip().strip("\"'")
            return self.build_image_card_html(clean_url, alt=alt or "img_preview")

        return re.sub(pattern, replacer, text)

    def process_md_links(self, text):
        pattern = r"\[([^\]]+)\]\(([^)]+)\)"

        def replacer(match):
            display_text, url = match.groups()
            url = url.strip().strip("\"'")
            if "<jarvis-token-" in display_text:
                return self.protect(f'<p style="margin-top: 10px;"><a href="{url}">{display_text}</a></p>')

            if "youtube.com" in url or "youtu.be" in url:
                yt_match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([a-zA-Z0-9_-]{11})", url)
                if yt_match:
                    vid_id = yt_match.group(1)
                    return self.protect(
                        f'<p style="margin-top: 10px; margin-bottom: 8px; line-height: 1.25;">'
                        f'<a href="{url}" style="text-decoration:none; color:#C48AF9; font-weight:600;">{display_text} ↗</a><br>'
                        f'<a href="{url}" style="text-decoration:none;">'
                        f'<img src="https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg" width="420" height="236" alt="yt_preview"></a></p>'
                    )

            clean_disp = display_text.strip()
            if self.is_image_url(url):
                if clean_disp == url or clean_disp.startswith(("http://", "https://", "file://")):
                    return self.build_image_card_html(url, alt="img_preview")
                return self.build_image_card_html(url, alt=clean_disp, caption_text=clean_disp, caption_url=url)

            if clean_disp == url or clean_disp.startswith(("http://", "https://")):
                return self.protect(
                    f'<p style="margin-top: 10px; margin-bottom: 8px; line-height: 1.0;">'
                    f'<a href="{url}" style="text-decoration:none;">'
                    f'<img src="preview://{url}" width="420" height="286" alt="link_preview"></a></p>'
                )

            return self.protect(
                f'<a href="{url}" style="color: #C48AF9; text-decoration: none; font-weight: 600;">{display_text} ↗</a>'
            )

        return re.sub(pattern, replacer, text)

    def process_raw_direct_images(self, text):
        web_pattern = (
            r'(?<!=["\'])(https?://[^\s<>"\')]+?\.(?:png|jpg|jpeg|gif|webp|bmp|svg|avif|ico|tiff)'
            r'(?:[?#][^\s<>"\')]*)?)(?!\))'
        )

        def web_replacer(match):
            url = match.group(1).rstrip(".,;)")
            return self.build_image_card_html(url, alt="img_preview")

        text = re.sub(web_pattern, web_replacer, text, flags=re.IGNORECASE)

        win_pattern = r'(?<!=["\'])([a-zA-Z]:[\\/][^\n<>"\']*?\.(?:png|jpg|jpeg|gif|webp|bmp|svg|avif))(?!\))'

        def win_replacer(match):
            raw_path = match.group(1).strip()
            return self.build_image_card_html(raw_path, alt="img_preview")

        return re.sub(win_pattern, win_replacer, text, flags=re.IGNORECASE)

    def process_youtube_links(self, text):
        pattern = (
            r'(?<!=["\'])(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)'
            r'([a-zA-Z0-9_-]{11})(?:[&?][a-zA-Z0-9_=%&.-]*)?)'
        )
        return re.sub(
            pattern,
            lambda m: self.protect(
                f'<p style="margin-top: 10px; margin-bottom: 8px; line-height: 1.0;">'
                f'<a href="{m.group(1)}" style="text-decoration:none;">'
                f'<img src="https://img.youtube.com/vi/{m.group(2)}/maxresdefault.jpg" width="420" height="236" alt="yt_preview"></a></p>'
            ),
            text,
        )

    def process_raw_links(self, text):
        pattern = r'(?<!=["\'])(https?://[^\s<>"\']+)'

        def replacer(match):
            url = match.group(1).rstrip(".,;)")
            if self.is_image_url(url):
                return self.build_image_card_html(url, alt="img_preview")
            return self.protect(
                f'<p style="margin-top: 10px; margin-bottom: 8px; line-height: 1.0;">'
                f'<a href="{url}" style="text-decoration:none;">'
                f'<img src="preview://{url}" width="420" height="286" alt="link_preview"></a></p>'
            )

        return re.sub(pattern, replacer, text)

    def get_styled_html(self, content):
        return f"""
        <html><head><style>
            body {{
                margin: 0px;
                padding: 2px 4px 4px 2px;
                color: #ECECF2;
                font-family: '{self.eng_font}', '{self.hin_font}', 'Segoe UI', sans-serif;
                font-size: 12.2pt;
                line-height: 1.62;
                word-wrap: break-word;
            }}
            h1 {{
                font-size: 17.5pt;
                color: #FFFFFF;
                font-weight: 700;
                margin-top: 12px;
                margin-bottom: 10px;
                border-bottom: 1px solid rgba(255,255,255,0.09);
                padding-bottom: 6px;
            }}
            h2 {{
                font-size: 14.8pt;
                color: #F7F7FC;
                font-weight: 700;
                margin-top: 12px;
                margin-bottom: 8px;
            }}
            h3 {{
                font-size: 13pt;
                color: #E2E2F0;
                font-weight: 600;
                margin-top: 10px;
                margin-bottom: 6px;
            }}
            p {{
                margin-top: 0px;
                margin-bottom: 10px;
            }}
            blockquote {{
                margin: 10px 0px 14px 0px;
                padding: 10px 14px;
                border-left: 3px solid #BF5AF2;
                background-color: rgba(191, 90, 242, 0.09);
                color: #DCDCE8;
                font-style: italic;
            }}
            ul, ol {{
                margin-top: 4px;
                margin-bottom: 12px;
                padding-left: 22px;
            }}
            li {{
                margin-bottom: 6px;
            }}
            hr {{
                border: none;
                border-top: 1px solid rgba(255,255,255,0.09);
                margin: 14px 0px;
            }}
            table {{
                border-collapse: collapse;
                margin-top: 8px;
                margin-bottom: 14px;
                width: 100%;
            }}
            th, td {{
                border: 1px solid rgba(255,255,255,0.10);
                padding: 9px 12px;
                text-align: left;
                font-size: 11.2pt;
            }}
            th {{
                background-color: rgba(255,255,255,0.07);
                font-weight: 700;
                color: #FFFFFF;
            }}
            strong, b {{
                color: #FFFFFF;
                font-weight: 700;
            }}
            code {{
                background-color: #23242D;
                color: #E5A4FA;
                padding: 2px 5px;
                font-family: 'Cascadia Code', Consolas, monospace;
                font-size: 10.8pt;
            }}
            pre code {{
                background-color: transparent;
                padding: 0;
            }}
            a {{
                color: #C48AF9;
                text-decoration: none;
                font-weight: 600;
            }}
            img {{
                display: block;
                margin-bottom: 0px;
            }}
        </style></head><body>{content}</body></html>
        """

    def run(self):
        if self.is_cancelled:
            return

        text = self.strip_leading_emotion_tag(self.full_text)
        text = self.normalize_streaming_fences(text)
        text = self.process_markdown_code_blocks(text)
        text = self.stabilize_streaming_tail(text)
        text = self.process_md_images(text)
        text = self.process_md_links(text)
        text = self.process_raw_direct_images(text)
        text = self.process_youtube_links(text)
        text = self.process_raw_links(text)

        md_html = markdown(
            text,
            extras=[
                "tables",
                "cuddled-lists",
                "strike",
                "break-on-newline",
                "html-classes",
                "fenced-code-blocks",
            ],
        )
        md_html = re.sub(r"<p>\s*(<jarvis-token-\d+/>)\s*</p>", r"\1", md_html)

        for token, value in self.protected_elements.items():
            md_html = md_html.replace(token, value)

        if self.is_cancelled:
            return

        final_html = self.get_styled_html(md_html)
        if not self.is_cancelled:
            self.finished_signal.emit([], final_html)