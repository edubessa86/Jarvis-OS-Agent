import json
import time
import urllib.parse
import re
import os
from collections import OrderedDict
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt5.QtCore import Qt, QUrl, QTimer, QRectF, QPointF
from PyQt5.QtWidgets import QTextBrowser, QFrame, QSizePolicy
from PyQt5.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen,
    QTextDocument, QPalette, QDesktopServices, QFont,
    QFontMetrics, QLinearGradient
)


class AsyncTextBrowser(QTextBrowser):
    def __init__(self, parent_popup):
        super().__init__(parent_popup.inner_island)
        self.parent_popup = parent_popup
        self.network_manager = QNetworkAccessManager(self)
        self.network_manager.finished.connect(self.on_network_reply)
        self.image_cache = OrderedDict()
        self.failed_urls = {}
        self.pending_requests = set()
        self.MAX_CACHE_SIZE = 100
        self.CACHE_TTL = 30

        self.cache_dir = "Data/cache"
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
        self.meta_cache_file = os.path.join(self.cache_dir, "meta_cache.json")
        self.meta_cache = {}
        if os.path.exists(self.meta_cache_file):
            try:
                with open(self.meta_cache_file, "r", encoding="utf-8") as f:
                    self.meta_cache = json.load(f)
            except Exception:
                pass

        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.handle_link_click)

        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: transparent; border: none; outline: none;")

        palette = self.palette()
        palette.setColor(QPalette.Highlight, QColor(191, 90, 242, 110))
        palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255, 255))
        self.setPalette(palette)

    def handle_link_click(self, url):
        QDesktopServices.openUrl(url)

    def _create_placeholder(self, text, w=420, h=240):
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(34, 35, 44, 210))
        grad.setColorAt(1.0, QColor(20, 21, 27, 230))
        painter.fillPath(path, grad)

        painter.setPen(QColor(175, 175, 190))
        font = QFont(getattr(self.parent_popup, "eng_font", "Segoe UI"), 10, QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(img.rect(), Qt.AlignCenter, text)

        pen = QPen(QColor(255, 255, 255, 32), 1.2)
        painter.strokePath(path, pen)
        painter.end()
        return img

    def save_meta_cache(self):
        try:
            with open(self.meta_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.meta_cache, f)
        except Exception:
            pass

    def loadResource(self, type, name):
        if type == QTextDocument.ImageResource:
            url = name.toString()
            current_time = time.time()

            if url in self.failed_urls:
                if current_time - self.failed_urls[url] < self.CACHE_TTL:
                    return self._create_placeholder("Preview Unavailable", h=180)
                else:
                    del self.failed_urls[url]

            if url in self.image_cache:
                self.image_cache.move_to_end(url)
                return self.image_cache[url]

            if url in self.pending_requests:
                return self._create_placeholder("Loading Preview...", h=220)

            self.pending_requests.add(url)

            if url.startswith("preview://"):
                actual_url = url[10:]
                if actual_url in self.meta_cache:
                    meta = self.meta_cache[actual_url]
                    img_url = meta.get("img_url")
                    if img_url:
                        self._request_image(img_url, url, meta)
                        return self._create_placeholder("Loading Preview...", h=280)
                    else:
                        self.pending_requests.discard(url)
                        styled = self._draw_card(None, url, meta)
                        self._cache_and_update(url, styled)
                        return styled

                api_url = f"https://api.microlink.io?url={urllib.parse.quote(actual_url)}"
                req = QNetworkRequest(QUrl(api_url))
                req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
                reply = self.network_manager.get(req)
                reply.setProperty("is_microlink", True)
                reply.setProperty("original_url", url)
                reply.setProperty("actual_url", actual_url)
                return self._create_placeholder("Fetching Link Preview...", h=280)

            elif url.startswith("http"):
                req = QNetworkRequest(QUrl(url))
                req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
                reply = self.network_manager.get(req)
                reply.setProperty("is_direct_image", True)
                reply.setProperty("original_url", url)
                return self._create_placeholder("Loading Media...", h=236)

            elif url.startswith("file://"):
                local_path = QUrl(url).toLocalFile()
                raw_image = QImage(local_path)
                self.pending_requests.discard(url)

                if not raw_image.isNull():
                    styled_img = self._style_simple_image(raw_image, url)
                    self._cache_and_update(url, styled_img)
                    return styled_img
                else:
                    self.failed_urls[url] = current_time
                    return self._create_placeholder("Local Image Not Found", h=180)

        return super().loadResource(type, name)

    def _request_image(self, img_url, original_url, meta_data=None):
        req = QNetworkRequest(QUrl(img_url))
        req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
        reply = self.network_manager.get(req)
        reply.setProperty("is_preview_img", True)
        reply.setProperty("original_url", original_url)
        if meta_data:
            reply.setProperty("meta_data", meta_data)

    def _cache_and_update(self, url, img):
        if len(self.image_cache) >= self.MAX_CACHE_SIZE:
            self.image_cache.popitem(last=False)
        self.image_cache[url] = img
        self.document().addResource(QTextDocument.ImageResource, QUrl(url), img)
        self._trigger_debounced_reflow()

    def _style_simple_image(self, raw_image, url):
        target_w = 420
        if raw_image.width() > target_w:
            raw_image = raw_image.scaledToWidth(target_w, Qt.SmoothTransformation)
        w, h = raw_image.width(), raw_image.height()

        styled_img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        styled_img.fill(Qt.transparent)
        painter = QPainter(styled_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)
        painter.fillPath(path, QColor(22, 23, 28))
        painter.setClipPath(path)
        painter.drawImage(0, 0, raw_image)
        painter.setClipping(False)

        if "img.youtube.com" in url:
            pill_w, pill_h = 56, 38
            pill_x, pill_y = (w - pill_w) / 2.0, (h - pill_h) / 2.0
            path_pill = QPainterPath()
            path_pill.addRoundedRect(pill_x, pill_y, pill_w, pill_h, 10.0, 10.0)
            painter.fillPath(path_pill, QColor(255, 15, 35, 230))
            triangle = QPainterPath()
            tx, ty = pill_x + 22.0, pill_y + 11.0
            triangle.moveTo(tx, ty)
            triangle.lineTo(tx + 15.0, ty + 8.0)
            triangle.lineTo(tx, ty + 16.0)
            triangle.closeSubpath()
            painter.fillPath(triangle, QColor(255, 255, 255))

        pen = QPen(QColor(255, 255, 255, 42), 1.3)
        painter.strokePath(path, pen)
        painter.end()
        return styled_img

    def _draw_card(self, raw_image, url, meta):
        w, h = 420, 286
        img_h = 192
        if raw_image is None or raw_image.isNull():
            raw_image = QImage(w, img_h, QImage.Format_ARGB32_Premultiplied)
            raw_image.fill(QColor(32, 33, 42))
        else:
            raw_image = raw_image.scaled(w, img_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

        styled_img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        styled_img.fill(Qt.transparent)
        painter = QPainter(styled_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)
        painter.fillPath(path, QColor(22, 23, 30, 248))
        painter.setClipPath(path)

        img_rect = raw_image.rect()
        img_x = (w - img_rect.width()) // 2
        img_y = (img_h - img_rect.height()) // 2
        painter.drawImage(img_x, img_y, raw_image)

        painter.setClipping(False)

        title = meta.get("title") or "Web Link"
        desc = meta.get("description") or ""
        domain = meta.get("domain") or ""

        eng_font = getattr(self.parent_popup, "eng_font", "Segoe UI")

        painter.setPen(QColor(255, 255, 255, 245))
        title_font = QFont(eng_font, 10, QFont.Bold)
        painter.setFont(title_font)
        fm_title = QFontMetrics(title_font)
        elided_title = fm_title.elidedText(title, Qt.ElideRight, w - 28)
        painter.drawText(14, img_h + 26, elided_title)

        painter.setPen(QColor(175, 175, 190, 225))
        desc_font = QFont(eng_font, 9, QFont.Normal)
        painter.setFont(desc_font)
        fm_desc = QFontMetrics(desc_font)
        elided_desc = fm_desc.elidedText(desc, Qt.ElideRight, w - 28)
        painter.drawText(14, img_h + 50, elided_desc)

        if domain:
            painter.setPen(QColor(196, 138, 249, 230))
            dom_font = QFont(eng_font, 8, QFont.DemiBold)
            painter.setFont(dom_font)
            painter.drawText(14, img_h + 75, f"{domain} ↗")

        pen = QPen(QColor(255, 255, 255, 34), 1.2)
        painter.strokePath(path, pen)

        line_pen = QPen(QColor(255, 255, 255, 22), 1.0)
        painter.setPen(line_pen)
        painter.drawLine(QPointF(1.5, float(img_h)), QPointF(float(w - 1.5), float(img_h)))

        painter.end()
        return styled_img

    def _trigger_debounced_reflow(self):
        QTimer.singleShot(15, self.parent_popup.update_layout_height)

    def on_network_reply(self, reply):
        if not self.isVisible() or self.document() is None:
            reply.deleteLater()
            return

        url = reply.request().url().toString()
        is_microlink = reply.property("is_microlink")
        is_fallback = reply.property("is_fallback")
        is_preview_img = reply.property("is_preview_img")
        original_url = reply.property("original_url")
        actual_url = reply.property("actual_url")
        meta_data = reply.property("meta_data")
        current_time = time.time()

        target_cleanup_url = original_url if original_url else url

        content_length = reply.header(QNetworkRequest.ContentLengthHeader)
        if content_length and int(content_length) > 5 * 1024 * 1024:
            self.pending_requests.discard(target_cleanup_url)
            self.failed_urls[target_cleanup_url] = current_time
            self.document().addResource(
                QTextDocument.ImageResource,
                QUrl(target_cleanup_url),
                self._create_placeholder("Media File Too Large", h=180),
            )
            self._trigger_debounced_reflow()
            reply.deleteLater()
            return

        if reply.error() != QNetworkReply.NoError:
            if is_microlink:
                req = QNetworkRequest(QUrl(actual_url))
                req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
                fb_reply = self.network_manager.get(req)
                fb_reply.setProperty("is_fallback", True)
                fb_reply.setProperty("original_url", original_url)
                fb_reply.setProperty("actual_url", actual_url)
                reply.deleteLater()
                return
            elif is_fallback:
                self.pending_requests.discard(target_cleanup_url)
                meta = {"title": actual_url, "domain": urllib.parse.urlparse(actual_url).netloc}
                styled = self._draw_card(None, original_url, meta)
                self._cache_and_update(original_url, styled)
                reply.deleteLater()
                return
            elif "maxresdefault.jpg" in url:
                fallback_url = original_url if original_url else url
                req = QNetworkRequest(QUrl(url.replace("maxresdefault.jpg", "hqdefault.jpg")))
                req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
                fb_img = self.network_manager.get(req)
                fb_img.setProperty("original_url", fallback_url)
                reply.deleteLater()
                return
            else:
                self.pending_requests.discard(target_cleanup_url)
                self.failed_urls[target_cleanup_url] = current_time
                self.document().addResource(
                    QTextDocument.ImageResource,
                    QUrl(target_cleanup_url),
                    self._create_placeholder("Preview Unavailable", h=180),
                )
                self._trigger_debounced_reflow()
                reply.deleteLater()
                return

        if is_microlink:
            try:
                data = json.loads(reply.readAll().data().decode("utf-8"))
                img_url = data.get("data", {}).get("image", {}).get("url")
                title = data.get("data", {}).get("title")
                desc = data.get("data", {}).get("description")
                domain = data.get("data", {}).get("publisher") or urllib.parse.urlparse(actual_url).netloc

                meta = {
                    "title": title or actual_url,
                    "description": desc or "",
                    "domain": domain,
                    "img_url": img_url,
                }
                self.meta_cache[actual_url] = meta
                self.save_meta_cache()

                if img_url:
                    self._request_image(img_url, original_url, meta)
                else:
                    self.pending_requests.discard(original_url)
                    styled = self._draw_card(None, original_url, meta)
                    self._cache_and_update(original_url, styled)
            except Exception:
                req = QNetworkRequest(QUrl(actual_url))
                req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
                fb_reply = self.network_manager.get(req)
                fb_reply.setProperty("is_fallback", True)
                fb_reply.setProperty("original_url", original_url)
                fb_reply.setProperty("actual_url", actual_url)
            reply.deleteLater()
            return

        if is_fallback:
            html = reply.readAll().data().decode("utf-8", errors="ignore")
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            desc_match = re.search(
                r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']',
                html,
                re.IGNORECASE,
            )
            img_match = re.search(
                r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']',
                html,
                re.IGNORECASE,
            )

            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else actual_url
            desc = desc_match.group(1).strip() if desc_match else ""
            img_url = img_match.group(1).strip() if img_match else None
            domain = urllib.parse.urlparse(actual_url).netloc

            meta = {"title": title, "description": desc, "domain": domain, "img_url": img_url}
            self.meta_cache[actual_url] = meta
            self.save_meta_cache()

            if img_url:
                if img_url.startswith("/"):
                    img_url = f"https://{domain}{img_url}"
                self._request_image(img_url, original_url, meta)
            else:
                self.pending_requests.discard(original_url)
                styled = self._draw_card(None, original_url, meta)
                self._cache_and_update(original_url, styled)
            reply.deleteLater()
            return

        raw_data = reply.readAll()
        raw_image = QImage.fromData(raw_data)
        target_url = original_url or url

        self.pending_requests.discard(target_cleanup_url)

        if raw_image.isNull():
            if is_preview_img and meta_data:
                styled = self._draw_card(None, target_url, meta_data)
                self._cache_and_update(target_url, styled)
            else:
                self.failed_urls[target_url] = current_time
                self.document().addResource(
                    QTextDocument.ImageResource,
                    QUrl(target_url),
                    self._create_placeholder("Invalid Image Data", h=180),
                )
                self._trigger_debounced_reflow()
            reply.deleteLater()
            return

        if is_preview_img and meta_data:
            styled_img = self._draw_card(raw_image, target_url, meta_data)
        else:
            styled_img = self._style_simple_image(raw_image, target_url)

        self._cache_and_update(target_url, styled_img)
        reply.deleteLater()