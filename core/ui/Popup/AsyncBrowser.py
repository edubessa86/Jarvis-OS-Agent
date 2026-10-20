import html as html_lib
import json
import os
import re
import time
import urllib.parse
from collections import OrderedDict
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt5.QtCore import Qt, QUrl, QTimer, QRectF, QPointF, QByteArray, QSizeF
from PyQt5.QtWidgets import QTextBrowser, QFrame, QSizePolicy, QApplication
from PyQt5.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen,
    QTextDocument, QPalette, QDesktopServices, QFont,
    QFontMetrics, QLinearGradient
)
from PyQt5.QtSvg import QSvgRenderer


class AsyncTextBrowser(QTextBrowser):
    CARD_W = 420
    CARD_H = 286
    IMG_W = 420
    IMG_H = 236

    USER_AGENT = (
        b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        b"AppleWebKit/537.36 (KHTML, like Gecko) "
        b"Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, parent_popup):
        super().__init__(parent_popup.inner_island)
        self.parent_popup = parent_popup
        self.network_manager = QNetworkAccessManager(self)
        self.network_manager.finished.connect(self.on_network_reply)
        self.image_cache = OrderedDict()
        self.raw_image_cache = OrderedDict()
        self.static_placeholders = {}
        self.failed_urls = {}
        self.pending_requests = set()
        self.active_replies = {}
        self.MAX_CACHE_SIZE = 100
        self.MAX_RAW_CACHE_SIZE = 35
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
        self.document().setDocumentMargin(4.0)

        palette = self.palette()
        palette.setColor(QPalette.Highlight, QColor(191, 90, 242, 110))
        palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255, 255))
        self.setPalette(palette)

    def _get_dpr(self):
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        return max(1.0, float(dpr))

    def _new_canvas(self, w, h):
        dpr = self._get_dpr()
        img = QImage(
            int(round(w * dpr)),
            int(round(h * dpr)),
            QImage.Format_ARGB32_Premultiplied,
        )
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        return img, dpr

    def _store_raw_image(self, key, raw_image):
        if raw_image is None or raw_image.isNull():
            return
        if key in self.raw_image_cache:
            self.raw_image_cache.move_to_end(key)
        self.raw_image_cache[key] = raw_image
        while len(self.raw_image_cache) > self.MAX_RAW_CACHE_SIZE:
            self.raw_image_cache.popitem(last=False)

    def handle_link_click(self, url):
        url_str = url.toString()
        if url_str.startswith("imgpreview://"):
            target_url = url_str[len("imgpreview://"):]
            raw_img = (
                self.raw_image_cache.get(target_url)
                or self.raw_image_cache.get("img://" + target_url)
                or self.raw_image_cache.get("preview://" + target_url)
            )
            if raw_img is not None and not raw_img.isNull() and hasattr(self.parent_popup, "open_image_preview"):
                self.parent_popup.open_image_preview(raw_img, target_url)
                return
            QDesktopServices.openUrl(QUrl(target_url))
            return
        QDesktopServices.openUrl(url)

    def _create_placeholder(self, text, w=420, h=236, is_card=False):
        cache_key = (text, int(w), int(h), bool(is_card), round(self._get_dpr(), 2))
        if cache_key in self.static_placeholders:
            return self.static_placeholders[cache_key]

        img, _ = self._new_canvas(w, h)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(32, 33, 42, 235))
        grad.setColorAt(1.0, QColor(18, 19, 25, 245))
        painter.fillPath(path, grad)

        if is_card:
            hero_h = 192.0
            shimmer_rect = QRectF(1.5, 1.5, w - 3.0, hero_h)
            painter.save()
            painter.setClipPath(path)
            painter.fillRect(shimmer_rect, QColor(255, 255, 255, 8))
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
            painter.drawLine(QPointF(1.5, hero_h), QPointF(w - 1.5, hero_h))

            bar1 = QPainterPath()
            bar1.addRoundedRect(QRectF(14.0, hero_h + 16.0, w * 0.55, 12.0), 6.0, 6.0)
            painter.fillPath(bar1, QColor(255, 255, 255, 24))

            bar2 = QPainterPath()
            bar2.addRoundedRect(QRectF(14.0, hero_h + 38.0, w * 0.78, 10.0), 5.0, 5.0)
            painter.fillPath(bar2, QColor(255, 255, 255, 14))

            bar3 = QPainterPath()
            bar3.addRoundedRect(QRectF(14.0, hero_h + 60.0, w * 0.30, 9.0), 4.5, 4.5)
            painter.fillPath(bar3, QColor(191, 90, 242, 42))
            painter.restore()

            text_rect = QRectF(0.0, 0.0, float(w), 192.0)
        else:
            text_rect = QRectF(0.0, 0.0, float(w), float(h))

        painter.setPen(QColor(180, 182, 198, 220))
        font = QFont(getattr(self.parent_popup, "eng_font", "Segoe UI"), 9, QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(text_rect, Qt.AlignCenter, text)

        pen = QPen(QColor(255, 255, 255, 34), 1.2)
        painter.strokePath(path, pen)
        painter.end()

        self.static_placeholders[cache_key] = img
        return img

    def save_meta_cache(self):
        try:
            with open(self.meta_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.meta_cache, f)
        except Exception:
            pass

    def _build_request(self, target_url, accept_images=False):
        req = QNetworkRequest(QUrl(target_url))
        req.setRawHeader(b"User-Agent", self.USER_AGENT)
        if accept_images:
            req.setRawHeader(
                b"Accept",
                b"image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            )
        if hasattr(QNetworkRequest, "FollowRedirectsAttribute"):
            req.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
        if hasattr(QNetworkRequest, "RedirectPolicyAttribute") and hasattr(
            QNetworkRequest, "NoLessSafeRedirectPolicy"
        ):
            req.setAttribute(
                QNetworkRequest.RedirectPolicyAttribute,
                QNetworkRequest.NoLessSafeRedirectPolicy,
            )
        return req

    def _send_get(self, req, timeout_ms=12000, **props):
        reply = self.network_manager.get(req)
        for k, v in props.items():
            reply.setProperty(k, v)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda r=reply: self._abort_stalled_reply(r))
        timer.start(timeout_ms)
        self.active_replies[reply] = timer
        return reply

    def _abort_stalled_reply(self, reply):
        self.active_replies.pop(reply, None)
        try:
            if reply is not None and reply.isRunning():
                reply.abort()
        except Exception:
            pass

    def _decode_image_bytes(self, raw_bytes):
        if not raw_bytes:
            return QImage()
        img = QImage.fromData(raw_bytes)
        if not img.isNull():
            return img
        try:
            renderer = QSvgRenderer(QByteArray(raw_bytes))
            if renderer.isValid():
                default_size = renderer.defaultSize()
                w = max(420, default_size.width() if default_size.width() > 0 else 420)
                h = max(236, default_size.height() if default_size.height() > 0 else 236)
                svg_img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
                svg_img.fill(Qt.transparent)
                p = QPainter(svg_img)
                p.setRenderHint(QPainter.Antialiasing, True)
                renderer.render(p, QRectF(0, 0, w, h))
                p.end()
                return svg_img
        except Exception:
            pass
        return QImage()

    def _load_local_image(self, local_path):
        img = QImage(local_path)
        if not img.isNull():
            return img
        if os.path.exists(local_path):
            try:
                with open(local_path, "rb") as f:
                    return self._decode_image_bytes(f.read())
            except Exception:
                pass
        return QImage()

    def loadResource(self, type, name):
        if type == QTextDocument.ImageResource:
            url = name.toString()
            current_time = time.time()

            if url == "loading://image":
                return self._create_placeholder("Loading Image...", w=self.IMG_W, h=self.IMG_H, is_card=False)
            if url == "loading://preview":
                return self._create_placeholder("Loading Preview...", w=self.CARD_W, h=self.CARD_H, is_card=True)

            if url in self.image_cache:
                self.image_cache.move_to_end(url)
                return self.image_cache[url]

            is_preview_scheme = url.startswith("preview://")
            is_img_scheme = url.startswith("img://")

            if url in self.failed_urls:
                if current_time - self.failed_urls[url] < self.CACHE_TTL:
                    if is_preview_scheme:
                        actual_url = url[10:]
                        meta = {
                            "title": actual_url,
                            "description": "Click to open link in browser",
                            "domain": urllib.parse.urlparse(actual_url).netloc or "Web Link",
                        }
                        return self._draw_card(None, url, meta)
                    return self._create_placeholder("Image Unavailable", w=self.IMG_W, h=self.IMG_H, is_card=False)
                else:
                    del self.failed_urls[url]

            if url in self.pending_requests:
                if is_preview_scheme:
                    return self._create_placeholder("Loading Link Preview...", w=self.CARD_W, h=self.CARD_H, is_card=True)
                return self._create_placeholder("Loading Media...", w=self.IMG_W, h=self.IMG_H, is_card=False)

            if is_preview_scheme:
                actual_url = url[10:]
                if actual_url in self.meta_cache:
                    meta = self.meta_cache[actual_url]
                    img_url = meta.get("img_url")
                    if img_url:
                        self.pending_requests.add(url)
                        self._request_image(img_url, url, meta)
                        return self._create_placeholder("Loading Link Preview...", w=self.CARD_W, h=self.CARD_H, is_card=True)
                    else:
                        styled = self._draw_card(None, url, meta)
                        self._cache_and_update(url, styled)
                        return styled

                self.pending_requests.add(url)
                api_url = f"https://api.microlink.io?url={urllib.parse.quote(actual_url, safe='')}"
                req = self._build_request(api_url, accept_images=False)
                self._send_get(
                    req,
                    is_microlink=True,
                    original_url=url,
                    actual_url=actual_url,
                )
                return self._create_placeholder("Loading Link Preview...", w=self.CARD_W, h=self.CARD_H, is_card=True)

            actual_media_url = url[6:] if is_img_scheme else url

            if actual_media_url.startswith("file://"):
                local_path = QUrl(actual_media_url).toLocalFile()
                raw_image = self._load_local_image(local_path)
                if not raw_image.isNull():
                    self._store_raw_image(actual_media_url, raw_image)
                    self._store_raw_image(url, raw_image)
                    styled_img = self._style_simple_image(raw_image, actual_media_url)
                    self._cache_and_update(url, styled_img)
                    return styled_img
                else:
                    self.failed_urls[url] = current_time
                    return self._create_placeholder("Local Image Not Found", w=self.IMG_W, h=self.IMG_H, is_card=False)

            if actual_media_url.startswith(("http://", "https://")):
                self.pending_requests.add(url)
                req = self._build_request(actual_media_url, accept_images=True)
                self._send_get(
                    req,
                    is_direct_image=True,
                    original_url=url,
                    actual_url=actual_media_url,
                )
                return self._create_placeholder("Loading Media...", w=self.IMG_W, h=self.IMG_H, is_card=False)

        return super().loadResource(type, name)

    def _request_image(self, img_url, original_url, meta_data=None):
        req = self._build_request(img_url, accept_images=True)
        self._send_get(
            req,
            is_preview_img=True,
            original_url=original_url,
            meta_data=meta_data,
        )

    def _cache_and_update(self, url, img):
        if url in self.image_cache:
            self.image_cache.move_to_end(url)
        elif len(self.image_cache) >= self.MAX_CACHE_SIZE:
            self.image_cache.popitem(last=False)
        self.image_cache[url] = img

        doc = self.document()
        if doc is not None:
            doc.addResource(QTextDocument.ImageResource, QUrl(url), img)
            doc.markContentsDirty(0, max(1, doc.characterCount()))
        self.viewport().update()
        self._trigger_debounced_reflow()

    def _style_simple_image(self, raw_image, url):
        w, h = self.IMG_W, self.IMG_H
        styled_img, dpr = self._new_canvas(w, h)

        painter = QPainter(styled_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)
        painter.fillPath(path, QColor(16, 17, 23))
        painter.setClipPath(path)

        orig_w = max(1, raw_image.width())
        orig_h = max(1, raw_image.height())
        img_ratio = orig_w / float(orig_h)
        is_yt = "img.youtube.com" in url

        if is_yt or (1.45 <= img_ratio <= 2.15 and orig_w >= 260):
            scaled_fill = raw_image.scaled(
                int(round(w * dpr)),
                int(round(h * dpr)),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            sw = scaled_fill.width() / dpr
            sh = scaled_fill.height() / dpr
            dx = (w - sw) / 2.0
            dy = (h - sh) / 2.0
            painter.drawImage(QRectF(dx, dy, sw, sh), scaled_fill)
        else:
            tiny = raw_image.scaled(28, 16, Qt.IgnoreAspectRatio, Qt.FastTransformation)
            blurred_bg = tiny.scaled(
                int(round(w * dpr)),
                int(round(h * dpr)),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )
            painter.drawImage(QRectF(0.0, 0.0, float(w), float(h)), blurred_bg)
            painter.fillRect(QRectF(0.0, 0.0, float(w), float(h)), QColor(12, 13, 18, 188))

            avail_w = w - 20.0
            avail_h = h - 20.0
            scaled_fit = raw_image.scaled(
                int(round(avail_w * dpr)),
                int(round(avail_h * dpr)),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            fw = scaled_fit.width() / dpr
            fh = scaled_fit.height() / dpr
            fx = (w - fw) / 2.0
            fy = (h - fh) / 2.0

            inner_rect = QRectF(fx, fy, fw, fh)
            inner_path = QPainterPath()
            inner_path.addRoundedRect(inner_rect, 8.0, 8.0)
            painter.save()
            painter.setClipPath(inner_path, Qt.IntersectClip)
            painter.drawImage(inner_rect, scaled_fit)
            painter.restore()
            painter.setPen(QPen(QColor(255, 255, 255, 32), 1.0))
            painter.drawRoundedRect(inner_rect, 8.0, 8.0)

        if is_yt:
            pill_w, pill_h = 58.0, 40.0
            pill_x, pill_y = (w - pill_w) / 2.0, (h - pill_h) / 2.0
            path_pill = QPainterPath()
            path_pill.addRoundedRect(pill_x, pill_y, pill_w, pill_h, 11.0, 11.0)
            painter.fillPath(path_pill, QColor(255, 15, 35, 235))
            triangle = QPainterPath()
            tx, ty = pill_x + 23.0, pill_y + 12.0
            triangle.moveTo(tx, ty)
            triangle.lineTo(tx + 15.0, ty + 8.0)
            triangle.lineTo(tx, ty + 16.0)
            triangle.closeSubpath()
            painter.fillPath(triangle, QColor(255, 255, 255))
        else:
            footer_grad = QLinearGradient(0.0, h - 46.0, 0.0, float(h))
            footer_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            footer_grad.setColorAt(1.0, QColor(8, 9, 13, 185))
            painter.fillRect(QRectF(0.0, h - 46.0, float(w), 46.0), footer_grad)

            eng_font = getattr(self.parent_popup, "eng_font", "Segoe UI")
            domain = urllib.parse.urlparse(url).netloc if url.startswith("http") else "Local Image"
            info_label = f"{domain}  •  {orig_w}×{orig_h}" if domain else f"{orig_w}×{orig_h}"

            badge_font = QFont(eng_font, 8, QFont.DemiBold)
            painter.setFont(badge_font)
            painter.setPen(QColor(235, 235, 245, 225))
            painter.drawText(QRectF(12.0, h - 28.0, w - 95.0, 20.0), Qt.AlignLeft | Qt.AlignVCenter, info_label)

            zoom_rect = QRectF(w - 76.0, h - 29.0, 64.0, 20.0)
            zoom_path = QPainterPath()
            zoom_path.addRoundedRect(zoom_rect, 6.0, 6.0)
            painter.fillPath(zoom_path, QColor(255, 255, 255, 28))
            painter.setPen(QPen(QColor(255, 255, 255, 45), 0.8))
            painter.drawPath(zoom_path)

            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(zoom_rect, Qt.AlignCenter, "Preview ⤢")

        painter.setClipping(False)
        pen = QPen(QColor(255, 255, 255, 42), 1.3)
        painter.strokePath(path, pen)
        painter.end()
        return styled_img

    def _draw_card(self, raw_image, url, meta):
        w, h = self.CARD_W, self.CARD_H
        img_h = 192
        styled_img, dpr = self._new_canvas(w, h)

        painter = QPainter(styled_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14.0, 14.0)
        painter.fillPath(path, QColor(22, 23, 30, 248))
        painter.setClipPath(path)

        if raw_image is None or raw_image.isNull():
            hero_grad = QLinearGradient(0.0, 0.0, float(w), float(img_h))
            hero_grad.setColorAt(0.0, QColor(38, 34, 54))
            hero_grad.setColorAt(1.0, QColor(24, 25, 34))
            painter.fillRect(QRectF(0.0, 0.0, float(w), float(img_h)), hero_grad)

            domain_label = (meta.get("domain") or "WEB").upper()
            eng_font = getattr(self.parent_popup, "eng_font", "Segoe UI")
            watermark_font = QFont(eng_font, 14, QFont.Bold)
            painter.setFont(watermark_font)
            painter.setPen(QColor(196, 138, 249, 95))
            painter.drawText(QRectF(0.0, 0.0, float(w), float(img_h)), Qt.AlignCenter, domain_label)
        else:
            scaled_hero = raw_image.scaled(
                int(round(w * dpr)),
                int(round(img_h * dpr)),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            sw = scaled_hero.width() / dpr
            sh = scaled_hero.height() / dpr
            img_x = (w - sw) / 2.0
            img_y = (img_h - sh) / 2.0
            painter.save()
            painter.setClipRect(QRectF(0.0, 0.0, float(w), float(img_h)), Qt.IntersectClip)
            painter.drawImage(QRectF(img_x, img_y, sw, sh), scaled_hero)
            painter.restore()

        painter.setClipping(False)

        title = html_lib.unescape(str(meta.get("title") or "Web Link")).strip()
        desc = html_lib.unescape(str(meta.get("description") or "")).strip()
        domain = html_lib.unescape(str(meta.get("domain") or "")).strip()

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
        QTimer.singleShot(12, self.parent_popup.update_layout_height)

    def on_network_reply(self, reply):
        timer = self.active_replies.pop(reply, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

        url = reply.request().url().toString()
        is_microlink = bool(reply.property("is_microlink"))
        is_fallback = bool(reply.property("is_fallback"))
        is_preview_img = bool(reply.property("is_preview_img"))
        original_url = str(reply.property("original_url") or "")
        actual_url = str(reply.property("actual_url") or "")
        meta_data = reply.property("meta_data")
        current_time = time.time()

        target_cleanup_url = original_url if original_url else url

        if self.document() is None:
            self.pending_requests.discard(target_cleanup_url)
            reply.deleteLater()
            return

        content_length = reply.header(QNetworkRequest.ContentLengthHeader)
        if content_length and int(content_length) > 12 * 1024 * 1024:
            self.pending_requests.discard(target_cleanup_url)
            self.failed_urls[target_cleanup_url] = current_time
            is_card = target_cleanup_url.startswith("preview://")
            ph = self._create_placeholder(
                "Media File Too Large",
                w=self.CARD_W if is_card else self.IMG_W,
                h=self.CARD_H if is_card else self.IMG_H,
                is_card=is_card,
            )
            self._cache_and_update(target_cleanup_url, ph)
            reply.deleteLater()
            return

        if reply.error() != QNetworkReply.NoError:
            if is_microlink and actual_url:
                req = self._build_request(actual_url, accept_images=True)
                self._send_get(
                    req,
                    is_fallback=True,
                    original_url=original_url,
                    actual_url=actual_url,
                )
                reply.deleteLater()
                return
            elif is_fallback:
                self.pending_requests.discard(target_cleanup_url)
                meta = {
                    "title": actual_url,
                    "description": "Click to open link in browser",
                    "domain": urllib.parse.urlparse(actual_url).netloc or "Web Link",
                }
                styled = self._draw_card(None, original_url, meta)
                self._cache_and_update(original_url, styled)
                reply.deleteLater()
                return
            elif "maxresdefault.jpg" in url:
                fallback_url = original_url if original_url else url
                req = self._build_request(url.replace("maxresdefault.jpg", "hqdefault.jpg"), accept_images=True)
                self._send_get(
                    req,
                    is_direct_image=True,
                    original_url=fallback_url,
                    actual_url=actual_url or fallback_url,
                )
                reply.deleteLater()
                return
            elif is_preview_img and meta_data:
                self.pending_requests.discard(target_cleanup_url)
                styled = self._draw_card(None, target_cleanup_url, meta_data)
                self._cache_and_update(target_cleanup_url, styled)
                reply.deleteLater()
                return
            else:
                self.pending_requests.discard(target_cleanup_url)
                self.failed_urls[target_cleanup_url] = current_time
                is_card = target_cleanup_url.startswith("preview://")
                ph = self._create_placeholder(
                    "Preview Unavailable",
                    w=self.CARD_W if is_card else self.IMG_W,
                    h=self.CARD_H if is_card else self.IMG_H,
                    is_card=is_card,
                )
                self._cache_and_update(target_cleanup_url, ph)
                reply.deleteLater()
                return

        if is_microlink:
            try:
                raw_json = reply.readAll().data().decode("utf-8", errors="ignore")
                data = json.loads(raw_json)
                if data.get("status") != "success":
                    raise ValueError("Microlink non-success")
                payload = data.get("data") or {}
                img_obj = payload.get("image")
                img_url = img_obj.get("url") if isinstance(img_obj, dict) else None
                if not img_url:
                    logo_obj = payload.get("logo")
                    img_url = logo_obj.get("url") if isinstance(logo_obj, dict) else None

                title = payload.get("title")
                desc = payload.get("description")
                domain = payload.get("publisher") or urllib.parse.urlparse(actual_url).netloc

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
                req = self._build_request(actual_url, accept_images=True)
                self._send_get(
                    req,
                    is_fallback=True,
                    original_url=original_url,
                    actual_url=actual_url,
                )
            reply.deleteLater()
            return

        if is_fallback:
            raw_bytes = reply.readAll().data()
            content_type = str(reply.header(QNetworkRequest.ContentTypeHeader) or "").lower()
            domain = urllib.parse.urlparse(actual_url).netloc

            if content_type.startswith("image/"):
                raw_img = self._decode_image_bytes(raw_bytes)
                self.pending_requests.discard(original_url)
                if not raw_img.isNull():
                    self._store_raw_image(actual_url, raw_img)
                    meta = {
                        "title": os.path.basename(urllib.parse.urlparse(actual_url).path) or domain or "Image",
                        "description": f"{raw_img.width()}×{raw_img.height()} Image",
                        "domain": domain,
                        "img_url": actual_url,
                    }
                    styled = self._draw_card(raw_img, original_url, meta)
                    self._cache_and_update(original_url, styled)
                    reply.deleteLater()
                    return

            html = raw_bytes.decode("utf-8", errors="ignore")
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            desc_match = re.search(
                r'<meta\s+(?:property|name)=["\'](?:og:description|description)["\']\s+content=["\'](.*?)["\']',
                html,
                re.IGNORECASE,
            )
            img_match = re.search(
                r'<meta\s+(?:property|name)=["\'](?:og:image|twitter:image)["\']\s+content=["\'](.*?)["\']',
                html,
                re.IGNORECASE,
            )

            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else actual_url
            desc = desc_match.group(1).strip() if desc_match else ""
            img_url = img_match.group(1).strip() if img_match else None

            meta = {"title": title, "description": desc, "domain": domain, "img_url": img_url}
            self.meta_cache[actual_url] = meta
            self.save_meta_cache()

            if img_url:
                img_url = urllib.parse.urljoin(actual_url, img_url)
                self._request_image(img_url, original_url, meta)
            else:
                self.pending_requests.discard(original_url)
                styled = self._draw_card(None, original_url, meta)
                self._cache_and_update(original_url, styled)
            reply.deleteLater()
            return

        raw_bytes = reply.readAll().data()
        raw_image = self._decode_image_bytes(raw_bytes)
        target_url = original_url or url

        self.pending_requests.discard(target_cleanup_url)

        if raw_image.isNull():
            if is_preview_img and meta_data:
                styled = self._draw_card(None, target_url, meta_data)
                self._cache_and_update(target_url, styled)
            else:
                self.failed_urls[target_url] = current_time
                ph = self._create_placeholder(
                    "Invalid Image Data",
                    w=self.IMG_W,
                    h=self.IMG_H,
                    is_card=False,
                )
                self._cache_and_update(target_url, ph)
            reply.deleteLater()
            return

        if is_preview_img and meta_data:
            styled_img = self._draw_card(raw_image, target_url, meta_data)
        else:
            media_key = actual_url or (target_url[6:] if target_url.startswith("img://") else target_url)
            self._store_raw_image(media_key, raw_image)
            self._store_raw_image(target_url, raw_image)
            styled_img = self._style_simple_image(raw_image, media_key)

        self._cache_and_update(target_url, styled_img)
        reply.deleteLater()