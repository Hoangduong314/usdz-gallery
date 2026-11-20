# -*- coding: utf-8 -*-
"""sync.py — crawl public preview t.me/s/hd_usdz -> gallery.json (runs in GitHub Actions).

Channel @hd_usdz is the database. Each asset = glb document message + preview
photo message REPLYing to it (caption: name / tam-giac line / #tags).
"""
import json, re, time, html, sys
import urllib.request

PREVIEW = "https://t.me/s/hd_usdz"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MSG_RE = re.compile(r'(?=<div class="tgme_widget_message_wrap)')
TAG_RE = re.compile(r"#[\w\u00c0-\u1ef9]+")

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

def _s(pat, s, g=1):
    m = re.search(pat, s)
    return m.group(g) if m else None

def parse_page(h):
    out = []
    for b in MSG_RE.split(h)[1:]:
        post = _s(r'data-post="hd_usdz/(\d+)"', b)
        if not post or "tgme_service_message" in b:
            continue
        msg = {"id": int(post), "date": _s(r'datetime="([^"]+)"', b) or "",
               "doc": _s(r'document_title[^>]*>([^<]*)<', b),
               "size": _s(r'document_extra[^>]*>([^<]*)<', b),
               "thumb": None, "reply": None, "caption": None}
        t = _s(r"photo_wrap[^>]*background-image:url\('([^']+)'\)", b)
        if t:
            msg["thumb"] = t.replace("http://", "https://")
        m = _s(r'tgme_widget_message_reply[^>]*href="https://t\.me/hd_usdz/(\d+)"', b)
        if m:
            msg["reply"] = int(m)
        c = re.search(r'js-message_text" dir="auto">(.*?)</div>', b, re.S)
        if c:
            cap = re.sub(r"<br/?>", "\n", c.group(1))
            msg["caption"] = html.unescape(re.sub(r"<[^>]+>", "", cap)).strip()
        out.append(msg)
    return out

def build_entry(photo, docmsg):
    cap = (photo or {}).get("caption") or ""
    lines = [l.strip() for l in cap.split("\n") if l.strip()]
    name = meta = ""
    for l in lines:
        if not name and not l.startswith("#") and "tam giác" not in l:
            name = re.sub(r"\.glb$", "", l, flags=re.I)
        elif "tam giác" in l and not meta:
            meta = l
    if not name and docmsg:
        name = re.sub(r"\.(glb|usdz?)$", "", docmsg["doc"] or "", flags=re.I)
    tags = sorted(set(t.lower() for t in TAG_RE.findall(cap)) or set(t.lower() for t in TAG_RE.findall(" ".join(lines))))
    tok = [x.strip() for x in meta.split("·")]
    pm = re.search(r"([\d.,]+)\s*tam giác", meta)
    mid = (photo or docmsg or {}).get("id")
    return {"id": mid, "glb_msg": (docmsg or {}).get("id"), "name": name or "(không tên)",
            "meta": meta, "tri": int(pm.group(1).replace(",", "").replace(".", "")) if pm else None,
            "renderer": tok[1] if len(tok) > 1 else "", "cat": tok[-1] if len(tok) > 2 else "",
            "tags": tags, "thumb": (photo or {}).get("thumb"),
            "glb": (docmsg or {}).get("doc"), "size": (docmsg or {}).get("size"),
            "date": (photo or docmsg or {}).get("date", ""),
            "url": f"https://t.me/hd_usdz/{mid}"}

def crawl():
    msgs, before, pages = {}, None, 0
    while pages < 60:
        url = PREVIEW + (f"?before={before}" if before else "")
        try:
            page = parse_page(fetch(url))
        except Exception as e:
            print("page err", e, file=sys.stderr)
            time.sleep(3)
            page = parse_page(fetch(url))
        if not page:
            break
        new = sum(1 for m in page if m["id"] not in msgs)
        for m in page:
            msgs[m["id"]] = m
        pages += 1
        if not new and pages > 1:
            break
        before = min(m["id"] for m in page)
        time.sleep(0.4)
    entries = {}
    consumed = set()
    for m in msgs.values():
        if m["thumb"] and m["reply"] and m["reply"] in msgs and msgs[m["reply"]]["doc"]:
            d = msgs[m["reply"]]
            entries[m["id"]] = build_entry(m, d)
            consumed.add(d["id"])
        elif m["thumb"]:
            entries[m["id"]] = build_entry(m, None)
    for m in msgs.values():
        if m["doc"] and m["id"] not in consumed:
            entries.setdefault(m["id"], build_entry(None, m))
    return list(entries.values())

if __name__ == "__main__":
    es = sorted(crawl(), key=lambda e: -e["id"])
    es = [e for e in es if not (e["glb"] and e["glb"].lower().endswith(".usdz") and not e["thumb"] and not e["tags"])]
    with open("gallery.json", "w", encoding="utf-8") as f:
        json.dump({"updated": int(time.time()), "count": len(es), "entries": es}, f, ensure_ascii=False)
    print(f"wrote gallery.json: {len(es)} entries")
