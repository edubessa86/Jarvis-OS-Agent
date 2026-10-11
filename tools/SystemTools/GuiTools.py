import os
import time
import base64
import tempfile
import ctypes
import hashlib
import threading
import pyautogui
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from core.logger.logger import logger

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

_ocr_engine = None
_ocr_lock = threading.Lock()
_LAST_ELEMENTS = []
_LAST_SCREEN_HASH = None
_CACHE_LOCK = threading.Lock()
_UIA_MIN_THRESHOLD = 5
_MEANINGFUL_UIA_ROLES = (
    "Button", "Edit", "Text", "Hyperlink", "MenuItem",
    "ComboBox", "CheckBox", "RadioButton", "ListItem", "Tab",
    "TreeItem", "DataItem", "Spinner", "Slider", "Document",
)
_BROWSER_MARKERS = (
    "chrome", "edge", "firefox", "brave", "opera",
    "msedge", "chromium", "vivaldi", "safari",
)
_BROWSER_ACCESSIBLE_THRESHOLD = 15
_DEFAULT_POST_ACTION_WAIT = 0.5


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine

    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine

        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
            logger.info("RapidOCR initialized.")
        except Exception as e:
            logger.warning(f"RapidOCR init failed: {e}")
            _ocr_engine = False

    return _ocr_engine


def warmup_gui_system():
    try:
        logger.info("Loading GUI Grounding System (RapidOCR + UIA)...")

        ocr = _get_ocr()
        if ocr:
            dummy = np.zeros((64, 64, 3), dtype=np.uint8)
            try:
                ocr(dummy)
                logger.info("RapidOCR warmup complete.")
            except Exception as e:
                logger.warning(f"OCR warmup inference failed: {e}")

        try:
            import uiautomation
            _ = uiautomation.GetForegroundControl()
            logger.info("UIA warmup complete.")
        except Exception as e:
            logger.warning(f"UIA warmup failed: {e}")

        logger.info("GUI Grounding System ready.")
    except Exception as e:
        logger.warning(f"GUI system warmup failed: {e}")


def _compute_screen_hash(img):
    small = img.resize((160, 90))
    quantized = small.point(lambda p: (p // 16) * 16)
    return hashlib.md5(quantized.tobytes()).hexdigest()


def _invalidate_cache():
    global _LAST_SCREEN_HASH
    with _CACHE_LOCK:
        _LAST_SCREEN_HASH = None


def _count_meaningful_uia(uia_els):
    count = 0
    for el in uia_els:
        role = el.get("role", "") or ""
        text = (el.get("text", "") or "").strip()
        if not text:
            continue
        if any(r in role for r in _MEANINGFUL_UIA_ROLES):
            count += 1
    return count


def _is_browser_focused():
    try:
        import uiautomation as auto
        fg = auto.GetForegroundControl()
        if not fg:
            return False
        cls = (fg.ClassName or "").lower()
        name = (fg.Name or "").lower()
        return any(b in cls or b in name for b in _BROWSER_MARKERS)
    except Exception:
        return False


def _detect_text_elements(img):
    ocr = _get_ocr()
    if not ocr:
        return []

    orig_w, orig_h = img.size
    scale = 1.0
    if orig_w > 1280:
        scale = 1280 / orig_w
        new_size = (int(orig_w * scale), int(orig_h * scale))
        img = img.resize(new_size, Image.LANCZOS)

    arr = np.array(img)
    elements = []

    try:
        result, _ = ocr(arr)
        if result:
            for item in result:
                try:
                    box, text, conf = item[0], item[1], item[2]
                    if conf < 0.5 or not str(text).strip():
                        continue
                    xs = [p[0] / scale for p in box]
                    ys = [p[1] / scale for p in box]
                    elements.append({
                        "text": str(text).strip(),
                        "bbox": (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
                        "role": "text",
                        "source": "ocr",
                    })
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"RapidOCR inference failed: {e}")

    return elements


def _detect_uia_elements():
    try:
        import uiautomation as auto
    except ImportError:
        return []

    elements = []
    try:
        root = auto.GetForegroundControl()
        if not root:
            return []

        def walk(ctrl, depth=0):
            if depth > 5:
                return
            try:
                for child in ctrl.GetChildren():
                    try:
                        name = (child.Name or "").strip()
                        rect = child.BoundingRectangle
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if not name or w <= 0 or h <= 0:
                            walk(child, depth + 1)
                            continue
                        if w < 3 or h < 3:
                            walk(child, depth + 1)
                            continue
                        elements.append({
                            "text": name,
                            "bbox": (rect.left, rect.top, rect.right, rect.bottom),
                            "role": child.ControlTypeName or "control",
                            "source": "uia",
                        })
                    except Exception:
                        pass
                    walk(child, depth + 1)
            except Exception:
                pass

        walk(root)
    except Exception as e:
        logger.warning(f"UIA detection failed: {e}")
    return elements


def _merge_elements(text_els, uia_els):
    merged = list(uia_els)
    for t in text_els:
        tx, ty, tx2, ty2 = t["bbox"]
        t_area = max(1, (tx2 - tx) * (ty2 - ty))
        dup = False
        for u in uia_els:
            ux, uy, ux2, uy2 = u["bbox"]
            u_area = max(1, (ux2 - ux) * (uy2 - uy))
            if u_area > 4 * t_area:
                continue
            ix = max(0, min(tx2, ux2) - max(tx, ux))
            iy = max(0, min(ty2, uy2) - max(ty, uy))
            if (ix * iy) > 0.5 * t_area:
                dup = True
                break
        if not dup:
            merged.append(t)
    merged.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    return merged


def _load_font(size):
    candidates = [
        "arialbd.ttf", "arial.ttf", "segoeuib.ttf",
        "segoeui.ttf", "calibrib.ttf", "calibri.ttf",
        "tahomabd.ttf", "tahoma.ttf", "verdana.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _draw_marks(img, elements):
    if img.mode != "RGB":
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    font_small = _load_font(13)

    for i, el in enumerate(elements, start=1):
        x1, y1, x2, y2 = el["bbox"]
        pad = 2
        x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad

        draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0, 255), width=2)

        badge = str(i)
        try:
            bbox = draw.textbbox((0, 0), badge, font=font_small)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
        except Exception:
            bw, bh = 10, 12

        bx1 = x1
        by1 = max(0, y1 - bh - 8)
        bx2 = x1 + bw + 12
        by2 = by1 + bh + 8
        draw.rectangle([bx1, by1, bx2, by2], fill=(255, 220, 0, 255))
        draw.text((bx1 + 6, by1 + 3), badge, fill=(0, 0, 0, 255), font=font_small)

    return img


def _capture_grounded_screen():
    global _LAST_ELEMENTS, _LAST_SCREEN_HASH

    screenshot = pyautogui.screenshot().convert("RGB")
    curr_hash = _compute_screen_hash(screenshot)

    with _CACHE_LOCK:
        if curr_hash == _LAST_SCREEN_HASH and _LAST_ELEMENTS:
            logger.info(f"Screen unchanged - reusing {len(_LAST_ELEMENTS)} cached elements.")
            marked = _draw_marks(screenshot, _LAST_ELEMENTS)
            out_path = os.path.join(tempfile.gettempdir(), "jarvis_som_screen.png")
            marked.save(out_path)
            return out_path, _LAST_ELEMENTS, screenshot.size

    uia_els = _detect_uia_elements()
    meaningful_count = _count_meaningful_uia(uia_els)
    browser_active = _is_browser_focused()

    force_ocr = False
    reason = ""

    if browser_active and meaningful_count < _BROWSER_ACCESSIBLE_THRESHOLD:
        force_ocr = True
        reason = f"browser foreground, weak UIA ({meaningful_count} meaningful)"
    elif meaningful_count < _UIA_MIN_THRESHOLD:
        force_ocr = True
        reason = f"weak UIA ({meaningful_count} meaningful)"

    if force_ocr:
        logger.info(f"Running OCR fallback: {reason}...")
        text_els = _detect_text_elements(screenshot)
        elements = _merge_elements(text_els, uia_els)
        logger.info(
            f"UIA+OCR detection: {len(text_els)} OCR + {len(uia_els)} UIA "
            f"= {len(elements)} merged."
        )
    else:
        elements = uia_els
        logger.info(
            f"UIA-only detection: {len(uia_els)} raw "
            f"({meaningful_count} meaningful)."
        )

    with _CACHE_LOCK:
        _LAST_ELEMENTS = elements
        _LAST_SCREEN_HASH = curr_hash

    marked = _draw_marks(screenshot, elements)
    out_path = os.path.join(tempfile.gettempdir(), "jarvis_som_screen.png")
    marked.save(out_path)

    return out_path, elements, screenshot.size


def _element_center(el):
    x1, y1, x2, y2 = el["bbox"]
    return (x1 + x2) // 2, (y1 + y2) // 2


def _snapshot_hash():
    try:
        return _compute_screen_hash(pyautogui.screenshot().convert("RGB"))
    except Exception:
        return None


def _resolve_target_coords(gui_cmd, action_name):
    x = gui_cmd.get("x")
    y = gui_cmd.get("y")
    eid = gui_cmd.get("element_id")

    if (x is None or y is None) and eid is not None:
        try:
            eid = int(eid)
        except (ValueError, TypeError):
            eid = None
        if eid is not None and 1 <= eid <= len(_LAST_ELEMENTS):
            el = _LAST_ELEMENTS[eid - 1]
            x, y = _element_center(el)
            logger.info(f"Resolved element_id #{eid} '{el['text'][:40]}' -> ({x},{y})")
        else:
            return None, None, (
                f"Observation: Error -> element_id {eid} invalid. "
                f"Valid range 1-{len(_LAST_ELEMENTS)}. Call 'observe' first."
            )

    if x is None or y is None:
        return None, None, (
            f"Observation: Error -> action='{action_name}' requires either 'element_id' "
            f"OR both 'x' and 'y' as integers."
        )

    try:
        return int(x), int(y), None
    except (ValueError, TypeError):
        return None, None, "Observation: Error -> 'x' and 'y' must be valid integers."


def _verify_expected_text(eid, expected_text):
    el = _LAST_ELEMENTS[eid - 1]
    actual = str(el["text"]).strip().lower()
    expected_lower = expected_text.strip().lower()

    if not expected_lower:
        return None

    if expected_lower == actual or expected_lower in actual or actual in expected_lower:
        return None

    candidates = []
    for idx, e in enumerate(_LAST_ELEMENTS, 1):
        e_text = str(e["text"]).strip().lower()
        if expected_lower in e_text or e_text in expected_lower:
            candidates.append(f"#{idx} '{e['text'][:40]}'")
            if len(candidates) >= 5:
                break

    hint = (
        f" Close matches: {', '.join(candidates)}."
        if candidates
        else " No close matches found."
    )

    return (
        f"Observation: Error -> element_id #{eid} actual text is '{el['text'][:50]}' "
        f"but you expected '{expected_text}'. CLICK REJECTED to prevent wrong action."
        f"{hint} Re-run 'observe' and pick the correct element_id, or verify the "
        f"'expected_text' matches the exact label shown in the observe list."
    )


def handle_gui_controller(gui_cmd: dict):
    global _LAST_ELEMENTS
    try:
        action = gui_cmd.get("action")
        wait_time = float(gui_cmd.get("wait_after_action", 0.0))

        if action == "observe":
            filepath, elements, (w, h) = _capture_grounded_screen()
            _LAST_ELEMENTS = elements

            with open(filepath, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            elem_lines = []
            for i, el in enumerate(elements, 1):
                cx, cy = _element_center(el)
                role = el.get("role", "text")
                text_preview = el["text"][:70].replace("\n", " ")
                elem_lines.append(f"#{i} [{role}] \"{text_preview}\" @({cx},{cy})")

            listing = (
                "\n".join(elem_lines[:150])
                if elem_lines
                else "(no elements detected)"
            )

            return {
                "type": "image_payload",
                "data": [{"mime_type": "image/png", "data": img_b64}],
                "observation": (
                    f"Observation: Screen captured ({w}x{h}). "
                    f"{len(elements)} interactive elements detected and numbered "
                    f"(red boxes with yellow badges).\n\n"
                    f"DETECTED ELEMENTS:\n{listing}\n\n"
                    f"WORKFLOW:\n"
                    f"1. To click any element above, call gui_controller with "
                    f"action='click_by_id', element_id=<number>, AND "
                    f"expected_text='<exact label from list>'.\n"
                    f"2. The system verifies expected_text against the actual element label. "
                    f"If they do not match, the click is REJECTED.\n"
                    f"3. For double-click use 'double_click_by_id', for right-click use "
                    f"'right_click_by_id' — always with expected_text.\n"
                    f"4. Only use raw 'click' with x,y coordinates if the target is NOT "
                    f"in the list above (e.g., canvas, image area, custom drawing).\n"
                    f"5. To type text, use action='type' with 'text' parameter. Optionally "
                    f"include 'x','y' or 'element_id' to click first. If omitted, text is "
                    f"typed into the currently focused element.\n"
                    f"6. After every action, call 'observe' again to verify the UI changed "
                    f"correctly.\n"
                    f"7. IMPORTANT: If a click response warns that the screen did NOT "
                    f"change, do NOT retry the same element_id or coordinates. Try pressing "
                    f"'enter'/'tab', or observe again and pick a different element."
                ),
            }

        if action in ["click_by_id", "double_click_by_id", "right_click_by_id"]:
            eid = gui_cmd.get("element_id")
            if eid is None:
                return "Observation: Error -> 'element_id' is required for click_by_id actions."
            try:
                eid = int(eid)
            except (ValueError, TypeError):
                return "Observation: Error -> 'element_id' must be an integer."
            if eid < 1 or eid > len(_LAST_ELEMENTS):
                return (
                    f"Observation: Error -> element_id {eid} out of range. "
                    f"Valid range: 1-{len(_LAST_ELEMENTS)}. Call 'observe' first."
                )

            expected_text = gui_cmd.get("expected_text", "")
            if expected_text:
                verify_err = _verify_expected_text(eid, str(expected_text))
                if verify_err:
                    logger.warning(
                        f"Element verification failed for #{eid}. "
                        f"Expected '{expected_text}'."
                    )
                    return verify_err

            el = _LAST_ELEMENTS[eid - 1]
            x, y = _element_center(el)

            logger.info(f"{action} -> element #{eid} '{el['text'][:50]}' at ({x},{y})")

            before_hash = _snapshot_hash()

            pyautogui.moveTo(x, y, duration=0.15)

            if action == "click_by_id":
                pyautogui.click()
            elif action == "double_click_by_id":
                pyautogui.doubleClick()
            elif action == "right_click_by_id":
                pyautogui.rightClick()

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else _DEFAULT_POST_ACTION_WAIT
            time.sleep(sleep_for)

            after_hash = _snapshot_hash()
            warning = ""
            if before_hash and after_hash and before_hash == after_hash:
                logger.warning(
                    f"Click on #{eid} produced no visible change (screen hash identical)."
                )
                warning = (
                    f" WARNING: Screen looks IDENTICAL after the click. The action may "
                    f"not have registered, the element may be non-interactive, or a popup "
                    f"may be blocking. DO NOT repeat the same element_id or coordinates. "
                    f"Options: (a) press 'enter' or 'tab' via 'press_key', "
                    f"(b) call 'observe' and pick a different element, "
                    f"(c) use raw 'click' with different coordinates."
                )

            return (
                f"Observation: Performed {action} on element #{eid} "
                f"'{el['text'][:50]}' at ({x},{y}).{warning} "
                f"Call 'observe' to verify the result."
            )

        if action == "type":
            text = gui_cmd.get("text", "")
            if not text:
                return "Observation: Error -> 'type' action requires a non-empty 'text' parameter."

            x = gui_cmd.get("x")
            y = gui_cmd.get("y")
            eid = gui_cmd.get("element_id")

            if (x is None or y is None) and eid is not None:
                try:
                    eid = int(eid)
                except (ValueError, TypeError):
                    eid = None
                if eid is not None and 1 <= eid <= len(_LAST_ELEMENTS):
                    el = _LAST_ELEMENTS[eid - 1]
                    x, y = _element_center(el)
                    logger.info(
                        f"Type target resolved from element_id #{eid} -> ({x},{y})"
                    )
                else:
                    return (
                        f"Observation: Error -> element_id {eid} invalid. "
                        f"Valid range 1-{len(_LAST_ELEMENTS)}. Call 'observe' first."
                    )

            before_hash = _snapshot_hash()

            if x is not None and y is not None:
                try:
                    x, y = int(x), int(y)
                except (ValueError, TypeError):
                    return "Observation: Error -> 'x' and 'y' must be valid integers."
                logger.info(f"Clicking at ({x},{y}) before typing.")
                pyautogui.moveTo(x, y, duration=0.15)
                pyautogui.click()
                time.sleep(0.2)
            else:
                logger.info("Typing directly into currently focused element.")

            pyautogui.write(text, interval=0.02)

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else 0.35
            time.sleep(sleep_for)

            after_hash = _snapshot_hash()
            warning = ""
            if before_hash and after_hash and before_hash == after_hash:
                logger.warning("Type action produced no visible change on screen.")
                warning = (
                    " WARNING: Screen looks IDENTICAL after typing. The text may not have "
                    "been accepted (field not focused, disabled, or the target moved). "
                    "Call 'observe' and try clicking the field again, or use 'press_key' "
                    "with 'tab' to move focus."
                )

            return (
                f"Observation: Successfully typed {len(text)} characters.{warning}"
            )

        if action in ["click", "left_click", "right_click", "double_click", "hover"]:
            x, y, err = _resolve_target_coords(gui_cmd, action)
            if err:
                return err

            logger.info(f"Executing {action} at ({x},{y})")

            before_hash = _snapshot_hash()

            pyautogui.moveTo(x, y, duration=0.2)

            if action in ["click", "left_click"]:
                pyautogui.click()
            elif action == "right_click":
                pyautogui.rightClick()
            elif action == "double_click":
                pyautogui.doubleClick()
            elif action == "hover":
                pass

            _invalidate_cache()

            if action == "hover":
                if wait_time > 0:
                    time.sleep(wait_time)
                return f"Observation: Successfully hovered at ({x},{y})."

            sleep_for = wait_time if wait_time > 0 else _DEFAULT_POST_ACTION_WAIT
            time.sleep(sleep_for)

            after_hash = _snapshot_hash()
            warning = ""
            if before_hash and after_hash and before_hash == after_hash:
                logger.warning(
                    f"Raw {action} at ({x},{y}) produced no visible change."
                )
                warning = (
                    f" WARNING: Screen looks IDENTICAL after this {action}. The target "
                    f"may not be interactive or the coordinates may be off. Try a "
                    f"different approach — call 'observe' and use 'click_by_id' with "
                    f"element_id + expected_text if the target appears in the list."
                )

            return (
                f"Observation: Performed {action} at ({x},{y}).{warning}"
            )

        elif action == "drag_and_drop":
            sx = gui_cmd.get("start_x")
            sy = gui_cmd.get("start_y")
            ex = gui_cmd.get("end_x")
            ey = gui_cmd.get("end_y")

            if None in [sx, sy, ex, ey]:
                return "Observation: Error -> drag_and_drop requires start_x, start_y, end_x, end_y."

            try:
                sx, sy, ex, ey = int(sx), int(sy), int(ex), int(ey)
            except (ValueError, TypeError):
                return "Observation: Error -> Coordinates must be valid integers."

            logger.info(f"Dragging from ({sx},{sy}) to ({ex},{ey})")
            pyautogui.moveTo(sx, sy, duration=0.2)
            pyautogui.dragTo(ex, ey, duration=0.5, button="left")

            _invalidate_cache()

            if wait_time > 0:
                time.sleep(wait_time)

            return (
                f"Observation: Successfully dragged from ({sx},{sy}) to ({ex},{ey}). "
                f"Call 'observe' to verify the result."
            )

        elif action == "press_key":
            key = gui_cmd.get("key")
            if not key:
                return "Observation: Error -> 'press_key' requires a 'key' parameter."

            logger.info(f"Pressing key: {key}")

            before_hash = _snapshot_hash()

            pyautogui.press(key)

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else 0.4
            time.sleep(sleep_for)

            after_hash = _snapshot_hash()
            warning = ""
            if before_hash and after_hash and before_hash == after_hash:
                logger.info(f"Key '{key}' produced no visible screen change.")
                warning = (
                    f" NOTE: Screen looks IDENTICAL after pressing '{key}'. If you "
                    f"expected a form submission or navigation, it may not have "
                    f"registered. Try a different key or click the target directly."
                )

            return f"Observation: Pressed key '{key}'.{warning}"

        elif action == "hotkey":
            keys = gui_cmd.get("keys", [])
            if not isinstance(keys, list) or not keys:
                return "Observation: Error -> 'keys' array is missing or empty."

            logger.info(f"Pressing hotkey: {keys}")

            before_hash = _snapshot_hash()

            pyautogui.hotkey(*keys)

            _invalidate_cache()

            sleep_for = wait_time if wait_time > 0 else 0.4
            time.sleep(sleep_for)

            after_hash = _snapshot_hash()
            warning = ""
            if before_hash and after_hash and before_hash == after_hash:
                logger.info(f"Hotkey {keys} produced no visible screen change.")
                warning = (
                    f" NOTE: Screen looks IDENTICAL after hotkey {keys}. If a UI change "
                    f"was expected, verify the target window is focused."
                )

            return f"Observation: Pressed hotkey combo {keys}.{warning}"

        elif action in ["scroll_down", "scroll_up"]:
            amount = -500 if action == "scroll_down" else 500
            logger.info(f"Scrolling screen: {action}")

            x = gui_cmd.get("x")
            y = gui_cmd.get("y")
            if x is not None and y is not None:
                try:
                    pyautogui.moveTo(int(x), int(y), duration=0.15)
                except (ValueError, TypeError):
                    pass

            pyautogui.scroll(amount)

            _invalidate_cache()

            if wait_time > 0:
                time.sleep(wait_time)
            else:
                time.sleep(0.3)

            return (
                "Observation: Scrolled screen. You MUST call 'observe' again to see "
                "the updated UI."
            )

        return f"Observation: Unknown GUI action '{action}' requested."

    except pyautogui.FailSafeException:
        logger.warning("PyAutoGUI FailSafe Triggered.")
        return (
            "Observation: Error -> Action aborted. Mouse pushed to corner "
            "triggering FailSafe."
        )
    except Exception as e:
        logger.error(f"Failed to process GUI action: {e}")
        return f"Observation: Error executing GUI action -> {e}"