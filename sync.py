# -*- coding: utf-8 -*-
"""sync.py — INCREMENTAL deep crawler for multiple public Telegram channels.

Each run: for every channel in channels.json, crawl backwards until hitting ids
already stored in gallery.json (from previous runs). History therefore ACCUMULATES
sync after sync (deep backfill up to PAGES_CAP pages/run); new posts arrive within
one cron tick. @hd_usdz keeps glb+photo pairing; external channels: photo post =
{thumb, name, tags, link to the t.me post}. Public preview only: no token, no login.
"""
import json, re, os, sys, time, html
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0"}
TAG_RE = re.compile(r"#[\w\u00c0-\u1ef9]+")
MSG_RE = re.compile(r'(?=<div class="tgme_widget_message_wrap)')
PAGES_CAP = int(os.environ.get("PAGES", "20"))     # max pages fetched per run per channel
CAP_PER_CH = int(os.environ.get("CAP", "250"))     # keep newest N entries per external ch
MINE_CAP = 100000                                  # own library: keep everything

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

def _s(pat, s, g=1):
    m = re.search(pat, s)
    return m.group(g) if m else None

def parse_page(ch, h):
    msgs = []
    for b in MSG_RE.split(h)[1:]:
        post = _s(r'data-post="' + re.escape(ch) + r'/(\d+)"', b)
        if not post or "tgme_service_message" in b:
            continue
        msg = {"id": int(post), "date": (_s(r'datetime="([^"]+)"', b) or "")[:10],
               "doc": _s(r'document_title[^>]*>([^<]*)<', b),
               "size": _s(r'document_extra[^>]*>([^<]*)<', b),
               "thumb": None, "reply": None, "caption": None}
        t = _s(r"photo_wrap[^>]*background-image:url\('([^']+)'\)", b)
        if t:
            msg["thumb"] = t.replace("http://", "https://")
        r = _s(r'tgme_widget_message_reply[^>]*href="https://t\.me/' + re.escape(ch) + r'/(\d+)"', b)
        if r:
            msg["reply"] = int(r)
        c = re.search(r'js-message_text" dir="auto">(.*?)</div>', b, re.S)
        if c:
            cap = re.sub(r"<br/?>", "\n", c.group(1))
            msg["caption"] = html.unescape(re.sub(r"<[^>]+>", "", cap)).strip()
        msgs.append(msg)
    return msgs

def crawl_channel(ch, stop_below=None, pages=PAGES_CAP):
    """Walk newest->oldest; stop when page min id reaches stop_below."""
    got, before = {}, None
    for _ in range(pages):
        url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
        try:
            page = parse_page(ch, fetch(url))
        except Exception as e:
            print("  page err", ch, e, file=sys.stderr)
            time.sleep(2)
            try:
                page = parse_page(ch, fetch(url))
            except Exception:
                break
        if not page:
            break
        new = 0
        for m in page:
            if m["id"] not in got:
                got[m["id"]] = m; new += 1
        mn = min(m["id"] for m in page)
        before = mn
        time.sleep(0.25)
        if stop_below and mn <= stop_below:
            break
        if new == 0:
            break
    return list(got.values())

def clean_name(s):
    lines = [l.strip() for l in (s or "").split("\n") if l.strip()]
    for l in lines:
        if not l.startswith("#") and "tam giác" not in l:
            return re.sub(r"\.(glb|usdz?|max|zip|rar|7z)$", "", l, flags=re.I)[:120]
    return (lines[0][:120] if lines else "")

def entry_generic(ch, tab, chname, m):
    cap = m.get("caption") or ""
    name = clean_name(cap) or clean_name(m.get("doc") or "") or chname
    tags = sorted(set(t.lower() for t in TAG_RE.findall(cap)))[:24]
    return {"id": f"{ch}:{m['id']}", "ch": ch, "tab": tab, "chname": chname,
            "name": name, "meta": "", "tags": tags,
            "thumb": m.get("thumb"), "glb": None, "size": m.get("size"),
            "date": m.get("date", ""), "url": f"https://t.me/{ch}/{m['id']}"}

def entry_hdusdz(m, docmsg):
    cap = (m or {}).get("caption") or ""
    lines = [l.strip() for l in cap.split("\n") if l.strip()]
    name = meta = ""
    for l in lines:
        if not name and not l.startswith("#") and "tam giác" not in l:
            name = re.sub(r"\.glb$", "", l, flags=re.I)
        elif "tam giác" in l and not meta:
            meta = l
    if not name and docmsg:
        name = re.sub(r"\.(glb|usdz?)$", "", docmsg["doc"] or "", flags=re.I)
    tags = sorted(set(t.lower() for t in TAG_RE.findall(cap)))
    pm = re.search(r"([\d.,]+)\s*tam giác", meta)
    tok = [x.strip() for x in meta.split("·")]
    mid = (m or docmsg or {}).get("id")
    return {"id": f"hd_usdz:{mid}", "ch": "hd_usdz", "tab": "mine", "chname": "Thư viện của tôi",
            "name": name or "(không tên)", "meta": meta,
            "tri": int(pm.group(1).replace(",", "").replace(".", "")) if pm else None,
            "renderer": tok[1] if len(tok) > 1 else "", "cat": tok[-1] if len(tok) > 2 else "",
            "tags": tags, "thumb": (m or {}).get("thumb"), "glb": (docmsg or {}).get("doc"),
            "size": (docmsg or {}).get("size"), "date": (m or docmsg or {}).get("date", "")[:10],
            "glb_msg": (docmsg or {}).get("id"),
            "url": f"https://t.me/hd_usdz/{mid}"}

def load_prev():
    p = os.path.join(BASE, "gallery.json")
    by_ch = {}
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            for e in d.get("entries", []):
                try:
                    mid = int(str(e["id"]).rsplit(":", 1)[1])
                except Exception:
                    continue
                by_ch.setdefault(e["ch"], {})[mid] = e
        except Exception:
            pass
    return by_ch

def main():
    chs = json.load(open(os.path.join(BASE, "channels.json"), encoding="utf-8"))
    prev = load_prev()
    all_entries = []
    for c in chs:
        ch, tab, name = c["ch"], c["tab"], c.get("name", c["ch"])
        known = prev.get(ch, {})
        oldest = min(known) if known else None
        stop = (oldest - 40) if oldest else None      # 40-msg overlap for pairing safety
        msgs = crawl_channel(ch, stop_below=stop)
        fresh = {}
        if ch == "hd_usdz":
            by_id = {m["id"]: m for m in msgs}
            consumed = set()
            for m in sorted(msgs, key=lambda x: x["id"]):
                if m["thumb"] and m["reply"] and m["reply"] in by_id and by_id[m["reply"]]["doc"]:
                    d = by_id[m["reply"]]; consumed.add(d["id"]); fresh[m["id"]] = entry_hdusdz(m, d)
                elif m["thumb"]:
                    fresh[m["id"]] = entry_hdusdz(m, None)
            for m in sorted(msgs, key=lambda x: x["id"]):
                if m["doc"] and m["id"] not in consumed:
                    fresh[m["id"]] = fresh.get(m["id"]) or entry_hdusdz(None, m)
        else:
            seen = set()
            for m in sorted(msgs, key=lambda x: -x["id"]):
                if not m["thumb"] or m["id"] in seen:
                    continue
                seen.add(m["id"])
                fresh[m["id"]] = entry_generic(ch, tab, name, m)
        merged = {**known, **fresh}
        cap = MINE_CAP if ch == "hd_usdz" else CAP_PER_CH
        if len(merged) > cap:
            merged = {i: merged[i] for i in sorted(merged, reverse=True)[:cap]}
        all_entries.extend(merged.values())
        print(f"{ch:26} +{len(fresh):4} total={len(merged)}", flush=True)
    order = {"mine": 0, "models": 1, "textures": 2}
    all_entries.sort(key=lambda e: (order.get(e["tab"], 9), -int(str(e["id"]).rsplit(":", 1)[1])))
    with open(os.path.join(BASE, "gallery.json"), "w", encoding="utf-8") as f:
        # one entry per line => git deltas stay tiny across frequent syncs
        meta = {"updated": int(time.time()), "count": len(all_entries),
                "tabs": ["mine", "models", "textures"],
                "tab_labels": {"mine": "🗂 Thư viện của tôi", "models": "🧊 Model 3D", "textures": "🎨 Texture"}}
        head = json.dumps(meta, ensure_ascii=False)[:-1].rstrip()  # strip closing brace
        f.write(head + ',"entries":[\n')
        f.write(',\n'.join(json.dumps(e, ensure_ascii=False) for e in all_entries))
        f.write('\n]}')
    print(f"WROTE gallery.json: {len(all_entries)} entries", flush=True)

if __name__ == "__main__":
    main()
