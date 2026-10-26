"""EasyOCR-local CRAFT component masks with upstream boxes and ordering.

Adapted from EasyOCR 1.7.2 craft_utils.getDetBoxes_core, originally
Copyright (c) 2019-present NAVER Corp., MIT License (see NOTICE).
Only the mask domain changes: each component uses the upstream dilation
window instead of allocating/scanning a full map for every component.
Imports of optional OCR dependencies remain deferred.
"""
from __future__ import annotations
import math


def component_boxes(textmap, linkmap, text_threshold, link_threshold, low_text):
    import cv2
    import numpy as np
    _, text_score = cv2.threshold(textmap, low_text, 1, 0)
    _, link_score = cv2.threshold(linkmap, link_threshold, 1, 0)
    combined = np.clip(text_score + link_score, 0, 1).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(combined, connectivity=4)
    link_only = None
    height, width = textmap.shape
    detections, mapper = [], []
    for k in range(1, count):
        size = stats[k, cv2.CC_STAT_AREA]
        if size < 10:
            continue
        x, y, w, h = (stats[k, i] for i in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                            cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        # Apply the upstream rejection gate before dilation arithmetic. A
        # large link-only component must not evaluate that arithmetic at all.
        tight_component = labels[y:y+h, x:x+w] == k
        if np.max(textmap[y:y+h, x:x+w][tight_component]) < text_threshold:
            continue
        if link_only is None:
            link_only = np.logical_and(link_score == 1, text_score == 0)
        niter = int(math.sqrt(size * min(w, h) / (w * h)) * 2)
        sx, ex = max(0, x-niter), min(width, x+w+niter+1)
        sy, ey = max(0, y-niter), min(height, y+h+niter+1)
        component = labels[sy:ey, sx:ex] == k
        segmentation = component.astype(np.uint8) * 255
        segmentation[link_only[sy:ey, sx:ex]] = 0
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1+niter, 1+niter))
        segmentation = cv2.dilate(segmentation, kernel)
        ys, xs = np.where(segmentation != 0)
        contours = np.column_stack((xs + sx, ys + sy))
        rectangle = cv2.minAreaRect(contours)
        box = cv2.boxPoints(rectangle)
        bw, bh = np.linalg.norm(box[0]-box[1]), np.linalg.norm(box[1]-box[2])
        if abs(1-max(bw,bh)/(min(bw,bh)+1e-5)) <= .1:
            left,right = min(contours[:,0]),max(contours[:,0])
            top,bottom = min(contours[:,1]),max(contours[:,1])
            box = np.array([[left,top],[right,top],[right,bottom],[left,bottom]],dtype=np.float32)
        start = box.sum(axis=1).argmin()
        detections.append(np.array(np.roll(box,4-start,0)))
        mapper.append(k)
    return detections, labels, mapper


def _easyocr_version():
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("easyocr")
    except PackageNotFoundError:
        return ""


def _clone(function, namespace):
    from types import FunctionType
    clone = FunctionType(function.__code__, namespace, function.__name__,
                         function.__defaults__, function.__closure__)
    clone.__kwdefaults__ = function.__kwdefaults__
    clone.__annotations__ = dict(function.__annotations__)
    clone.__module__ = function.__module__
    return clone


def bind_craft_postprocessing(reader):
    """Bind a private call graph to this Reader; never patch EasyOCR globals.

    The network forward, resize and grouping stay in installed EasyOCR code.
    Unsupported releases/call graphs and polygon/character-count operations
    retain the installed implementation. The supported release is validated
    against that implementation, including score-map views and boundaries.
    """
    from types import FunctionType
    state = getattr(reader, "__dict__", None)
    if not isinstance(state, dict):
        return False
    if state.get("_pdfextractor_bounded_craft") is True:
        return True
    if _easyocr_version() != "1.7.2":
        return False
    entry = getattr(reader, "get_textbox", None)
    if not isinstance(entry, FunctionType) or entry.__module__ != "easyocr.detection":
        return False
    network = entry.__globals__.get("test_net")
    if (not isinstance(network, FunctionType)
            or "test_net" not in entry.__code__.co_names
            or "getDetBoxes" not in network.__code__.co_names):
        return False
    upstream = network.__globals__.get("getDetBoxes")
    if (not isinstance(upstream, FunctionType)
            or upstream.__module__ != "easyocr.craft_utils"
            or upstream.__code__.co_varnames[:upstream.__code__.co_argcount] != (
                "textmap", "linkmap", "text_threshold", "link_threshold", "low_text",
                "poly", "estimate_num_chars")):
        return False

    def bounded_boxes(textmap, linkmap, text_threshold, link_threshold, low_text,
                      poly=False, estimate_num_chars=False):
        if poly or estimate_num_chars:
            return upstream(textmap, linkmap, text_threshold, link_threshold,
                            low_text, poly, estimate_num_chars)
        boxes, _labels, mapper = component_boxes(
            textmap, linkmap, text_threshold, link_threshold, low_text)
        return boxes, [None] * len(boxes), mapper

    network_namespace = dict(network.__globals__, getDetBoxes=bounded_boxes)
    private_network = _clone(network, network_namespace)
    entry_namespace = dict(entry.__globals__, test_net=private_network)
    reader.get_textbox = _clone(entry, entry_namespace)
    reader._pdfextractor_bounded_craft = True
    return True
