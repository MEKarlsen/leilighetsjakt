#!/usr/bin/env python3
"""
scrape_visning.py – Hent Tilstandsrapport (TG-grader) fra visning.ai

Bruk:
    import scrape_visning
    data = scrape_visning.scrape("460094226")
    # data = {
    #   "TG3 antall": "2",
    #   "TG2 antall": "6",
    #   "TG1 antall": "3",
    #   "tg_items": "[{\"grade\":\"TG3\",\"name\":\"...\", ...}, ...]"
    # }
"""
from __future__ import annotations

import json

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def scrape(finnkode: str) -> dict:
    """Fetch conditionGrades from visning.ai for the given finnkode.

    Returns a dict with:
        "TG3 antall"  – count of TG3 items (string, empty if none)
        "TG2 antall"  – count of TG2 items (string, empty if none)
        "TG1 antall"  – count of TG1 items (string, empty if none)
        "tg_items"    – JSON string: list of all condition grade objects

    Returns {} if visning.ai has no data for this finnkode.
    Raises RuntimeError on network/parse errors.
    """
    url = f"https://visning.ai/{finnkode}"
    try:
        r = requests.get(url, verify=False, headers=_HEADERS, timeout=20)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Nettverksfeil mot visning.ai: {exc}") from exc

    html = r.text

    # Data is embedded in self.__next_f.push([1, "<escaped-json>"]) blocks
    idx = html.find("conditionGrades")
    if idx < 0:
        return {}  # visning.ai has no analysis for this apartment

    start = html.rfind("self.__next_f.push([1,", 0, idx)
    if start < 0:
        return {}

    push_start = start + len("self.__next_f.push([1,")
    decoder = json.JSONDecoder()
    try:
        raw_str, _ = decoder.raw_decode(html, push_start)
        data = json.loads(raw_str)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Kunne ikke tolke data fra visning.ai: {exc}") from exc

    grades_raw = data.get("conditionGrades", [])
    if not grades_raw:
        return {}

    tg_counts: dict[str, int] = {"TG3": 0, "TG2": 0, "TG1": 0}
    items: list[dict] = []

    for item in grades_raw:
        cond = item.get("condition", {})
        grade = cond.get("grade", "")
        entry = {
            "grade": grade,
            "name": item.get("name", ""),
            "category": item.get("category", ""),
            "location": item.get("location", ""),
            "justification": cond.get("justification", ""),
            "risk": cond.get("risk", ""),
            "recommendedActions": cond.get("recommendedActions", ""),
            "costEstimate": cond.get("costEstimate", ""),
        }
        items.append(entry)
        if grade in tg_counts:
            tg_counts[grade] += 1

    # Highlights (Høydepunkter)
    highlights_raw = data.get("highlights", [])
    highlights = [
        {
            "emoji": h.get("emoji", ""),
            "header": h.get("header", ""),
            "details": h.get("details", ""),
            "category": h.get("category", ""),
        }
        for h in highlights_raw
    ]

    # Risks (Risikoer)
    risks_raw = data.get("risks", [])
    risks = [
        {
            "emoji": r.get("emoji", ""),
            "header": r.get("header", ""),
            "details": r.get("details", ""),
            "category": r.get("category", ""),
            "question": r.get("question", ""),
        }
        for r in risks_raw
    ]

    return {
        "TG3 antall": str(tg_counts["TG3"]) if tg_counts["TG3"] else "",
        "TG2 antall": str(tg_counts["TG2"]) if tg_counts["TG2"] else "",
        "TG1 antall": str(tg_counts["TG1"]) if tg_counts["TG1"] else "",
        "tg_items": json.dumps(items, ensure_ascii=False),
        "Høydepunkter antall": str(len(highlights)) if highlights else "",
        "hoydepunkter_items": json.dumps(highlights, ensure_ascii=False),
        "Risikoer antall": str(len(risks)) if risks else "",
        "risikoer_items": json.dumps(risks, ensure_ascii=False),
    }
