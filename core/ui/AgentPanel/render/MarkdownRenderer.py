import re
import html

from ..core.FontRegistry import get_code_font
from ..core.Theme import (
    SYNTAX_TOKENS, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_BODY,
    SURFACE_CODE_BG,
)


class MarkdownRenderer:
    TOKEN_MASTER_RE = re.compile(
        r'(?P<comment>#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/)'
        r'|(?P<string>"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
        r'|(?P<decorator>@[a-zA-Z_]\w*)'
        r'|(?P<keyword>\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|pass|break|continue|and|or|not|in|is|lambda|yield|async|await|True|False|None|const|let|var|function|export|default|switch|case|new|this|typeof|interface|type|struct|enum|pub|fn|impl|use|mod|match|where|select|insert|update|delete|create|table|drop|alter|raise|global|nonlocal|del|assert)\b)'
        r'|(?P<builtin>\b(?:self|cls|print|len|range|enumerate|zip|map|filter|int|str|float|list|dict|set|tuple|bool|super|open|isinstance|getattr|setattr|hasattr|Exception|ValueError|TypeError|KeyError|RuntimeError|console|window|document|Promise|JSON|Math|Object|Array)\b)'
        r'|(?P<func>\b[a-zA-Z_]\w*(?=\s*\())'
        r'|(?P<number>\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b)'
    )

    CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)[ \t]*\n?(.*?)```", re.DOTALL)
    INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
    LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
    BOLD_ITALIC_RE = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*")
    BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
    ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*|\s)(.+?)(?<!\*|\s)\*(?!\*)")
    HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
    BULLET_RE = re.compile(r"^(\s*)[-*•]\s+(.+)$")
    NUMBERED_RE = re.compile(r"^(\s*)(\d+)\.\s+(.+)$")
    LONG_TOKEN_RE = re.compile(r"\S{65,}")

    _highlight_cache = {}
    _HTML_CACHE_LIMIT = 200
    _HIGHLIGHT_CACHE_LIMIT = 400
    _html_cache = {}

    @classmethod
    def _soft_wrap_long_tokens(cls, text: str) -> str:
        def inject_zwsp(match):
            s = match.group(0)
            return "\u200b".join(s[i:i + 45] for i in range(0, len(s), 45))
        return cls.LONG_TOKEN_RE.sub(inject_zwsp, text)

    @classmethod
    def _apply_bold_italic(cls, text: str) -> str:
        text = cls.BOLD_ITALIC_RE.sub(
            f'<b style="color: {TEXT_PRIMARY};"><i>\\1</i></b>', text
        )
        text = cls.BOLD_RE.sub(
            lambda m: f'<b style="color: {TEXT_PRIMARY};">{m.group(1) or m.group(2)}</b>',
            text,
        )
        text = cls.ITALIC_RE.sub(
            f'<i style="color: {TEXT_SECONDARY};">\\1</i>', text
        )
        return text

    @classmethod
    def auto_heal_markdown(cls, text: str) -> str:
        fence_count = len(re.findall(r"```", text))
        if fence_count % 2 != 0:
            text = text + "\n```"
        return text

    @classmethod
    def highlight_code(cls, code: str) -> str:
        result = []
        last_idx = 0

        for match in cls.TOKEN_MASTER_RE.finditer(code):
            start, end = match.span()
            if start > last_idx:
                chunk = code[last_idx:start]
                result.append(html.escape(chunk))
            group_name = match.lastgroup
            token_text = html.escape(match.group(0))
            style = SYNTAX_TOKENS.get(group_name, SYNTAX_TOKENS["default"])
            result.append(f'<span style="{style}">{token_text}</span>')
            last_idx = end

        if last_idx < len(code):
            chunk = code[last_idx:]
            result.append(html.escape(chunk))

        highlighted = "".join(result)
        highlighted = highlighted.replace("\t", "    ")
        return highlighted

    @classmethod
    def highlight_code_cached(cls, code: str) -> str:
        key = (hash(code), get_code_font())
        cached = cls._highlight_cache.get(key)
        if cached is not None:
            return cached
        result = cls.highlight_code(code)
        if len(cls._highlight_cache) > cls._HIGHLIGHT_CACHE_LIMIT:
            cls._highlight_cache.clear()
        cls._highlight_cache[key] = result
        return result

    @classmethod
    def format_inline(cls, text: str, accent_hex: str = "rgb(191, 90, 242)") -> str:
        inline_vault = []
        link_vault = []
        code_family = get_code_font()

        def save_inline_code(m):
            idx = len(inline_vault)
            code_content = html.escape(m.group(1))
            inline_vault.append(
                f'<span style="background-color: rgba(255, 255, 255, 0.11); '
                f'color: {SYNTAX_TOKENS["keyword"].split("color: ")[1].split(";")[0]}; '
                f'font-family: \'{code_family}\', \'Consolas\', monospace; '
                f'font-size: 9.5pt;">&nbsp;{code_content}&nbsp;</span>'
            )
            return f"@@INLINECODE_{idx}@@"

        def save_link(m):
            idx = len(link_vault)
            raw_text = html.escape(m.group(1))
            styled_text = cls._apply_bold_italic(raw_text)
            safe_url = html.escape(m.group(2), quote=True)
            link_vault.append(
                f'<a href="{safe_url}" style="color: {accent_hex}; '
                f'text-decoration: underline; font-weight: 500;">{styled_text}</a>'
            )
            return f"@@LINK_{idx}@@"

        processed = cls.INLINE_CODE_RE.sub(save_inline_code, text)
        processed = cls.LINK_RE.sub(save_link, processed)
        processed = html.escape(cls._soft_wrap_long_tokens(processed))
        processed = cls._apply_bold_italic(processed)

        for idx, link_html in enumerate(link_vault):
            processed = processed.replace(f"@@LINK_{idx}@@", link_html)

        for idx, badge_html in enumerate(inline_vault):
            processed = processed.replace(f"@@INLINECODE_{idx}@@", badge_html)

        return processed

    @classmethod
    def to_html(cls, raw_text: str, font_family: str, base_pt: int, accent_rgb: list) -> str:
        if not raw_text.strip():
            return ""

        cache_key = (raw_text, font_family, base_pt, tuple(accent_rgb), get_code_font())
        cached = cls._html_cache.get(cache_key)
        if cached is not None:
            return cached

        result = cls._build_html(raw_text, font_family, base_pt, accent_rgb)

        if len(cls._html_cache) > cls._HTML_CACHE_LIMIT:
            cls._html_cache.clear()
        cls._html_cache[cache_key] = result
        return result

    @classmethod
    def _build_html(cls, raw_text: str, font_family: str, base_pt: int, accent_rgb: list) -> str:
        clean = raw_text.strip()
        r, g, b = [max(0, min(255, int(c))) for c in accent_rgb]
        accent_hex = f"rgb({r}, {g}, {b})"
        code_family = get_code_font()

        healed = cls.auto_heal_markdown(clean)
        code_blocks = []

        def extract_code_block(match):
            lang = (match.group(1) or "code").strip().upper()
            raw_code = match.group(2).rstrip("\n")
            highlighted = cls.highlight_code_cached(raw_code)
            block_html = (
                f'<div style="background-color: {SURFACE_CODE_BG}; '
                f'font-family: \'{font_family}\', sans-serif; font-size: 7.5pt; '
                f'font-weight: bold; color: {accent_hex}; letter-spacing: 0.8px;">'
                f'{html.escape(lang)}</div>'
                f'<pre style="background-color: {SURFACE_CODE_BG}; margin: 0px; '
                f'font-family: \'{code_family}\', \'Consolas\', \'Courier New\', monospace; '
                f'font-size: 9.5pt; color: {SYNTAX_TOKENS["default"].split("color: ")[1].split(";")[0]}; '
                f'white-space: pre-wrap; word-wrap: break-word;">{highlighted}</pre>'
            )
            idx = len(code_blocks)
            code_blocks.append(block_html)
            return f"@@CODEBLOCK_{idx}@@"

        staged = cls.CODE_BLOCK_RE.sub(extract_code_block, healed)
        lines = staged.split("\n")
        out_lines = []
        body_pt = max(10, base_pt - 1)

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("@@CODEBLOCK_") and stripped.endswith("@@"):
                idx_str = stripped.replace("@@CODEBLOCK_", "").replace("@@", "")
                if idx_str.isdigit() and int(idx_str) < len(code_blocks):
                    out_lines.append(code_blocks[int(idx_str)])
                continue

            if not stripped:
                out_lines.append('<div style="height: 5px; font-size: 1pt;">&nbsp;</div>')
                continue

            if stripped in ("---", "***", "___"):
                out_lines.append(
                    '<hr style="border: none; background-color: rgba(255, 255, 255, 0.12); '
                    'height: 1px; margin: 6px 0px;" />'
                )
                continue

            h_match = cls.HEADING_RE.match(stripped)
            if h_match:
                level = len(h_match.group(1))
                h_text = cls.format_inline(h_match.group(2), accent_hex)
                h_size = max(body_pt, body_pt + (3 - level))
                h_color = TEXT_PRIMARY if level <= 2 else SYNTAX_TOKENS["builtin"].split("color: ")[1].split(";")[0]
                out_lines.append(
                    f'<div style="margin-top: 6px; margin-bottom: 3px; font-size: {h_size}pt; '
                    f'font-weight: bold; color: {h_color};">{h_text}</div>'
                )
                continue

            if stripped.startswith(">"):
                q_text = cls.format_inline(stripped.lstrip(">").strip(), accent_hex)
                out_lines.append(
                    f'<div style="margin: 3px 0px 3px 8px; color: rgba(210, 214, 235, 0.85); '
                    f'font-style: italic;"><span style="color: {accent_hex}; font-weight: bold;">'
                    f'▎ </span>{q_text}</div>'
                )
                continue

            b_match = cls.BULLET_RE.match(line)
            if b_match:
                indent_px = 6 + min(36, len(b_match.group(1)) * 6)
                b_text = cls.format_inline(b_match.group(2), accent_hex)
                out_lines.append(
                    f'<div style="margin-left: {indent_px}px; margin-top: 2px; margin-bottom: 2px;">'
                    f'<span style="color: {accent_hex}; font-weight: bold;">•&nbsp;</span>{b_text}</div>'
                )
                continue

            n_match = cls.NUMBERED_RE.match(line)
            if n_match:
                indent_px = 6 + min(36, len(n_match.group(1)) * 6)
                num_str = n_match.group(2)
                n_text = cls.format_inline(n_match.group(3), accent_hex)
                out_lines.append(
                    f'<div style="margin-left: {indent_px}px; margin-top: 2px; margin-bottom: 2px;">'
                    f'<span style="color: {accent_hex}; font-weight: bold;">{num_str}.&nbsp;</span>{n_text}</div>'
                )
                continue

            out_lines.append(
                f'<div style="margin-top: 2px; margin-bottom: 2px; line-height: 1.46; '
                f'word-wrap: break-word;">{cls.format_inline(line, accent_hex)}</div>'
            )

        body_html = "".join(out_lines)
        return (
            f'<div style="font-family: \'{font_family}\', \'Segoe UI\', sans-serif; '
            f'font-size: {body_pt}pt; font-weight: 450; color: {TEXT_BODY}; '
            f'border-left: 0px solid {accent_hex}; '
            f'text-align: left; padding-top: 2px;">{body_html}</div>'
        )