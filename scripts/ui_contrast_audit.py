"""UI contrast / legibility audit (Playwright). Usage:
    py -3.12 scripts/ui_contrast_audit.py <base_url> [--routes trading,portfolio,...] [--width 1440]

Walks every visible text node on each route and computes the effective text colour (alpha
composited over the element's nearest opaque background) and its WCAG contrast ratio against
that background. Reports text that is dim or low-contrast — Edgar's rule: "texto blanco que se
vea, nada oscuro o difuminado". Thresholds: alpha >= 0.70 for any visible text, contrast >= 4.5:1
for text < 18 px, >= 3:1 for larger text. Exit code 1 if any route has offenders.
"""
import argparse
import json
import sys

from playwright.sync_api import sync_playwright

JS = r"""() => {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s)); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = ({r, g, b}) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const over = (fg, bg) => ({r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1});
  const bgOf = (el) => { let cur = el; let acc = null;
    while (cur && cur !== document.documentElement) { const c = parse(getComputedStyle(cur).backgroundColor);
      if (c && c.a > 0) { acc = acc ? over(acc, c) : c; if (acc.a >= 0.99) return acc; } cur = cur.parentElement; }
    const body = parse(getComputedStyle(document.body).backgroundColor) || {r: 10, g: 10, b: 10, a: 1};
    return acc ? over(acc, body) : body; };
  const opacityOf = (el) => { let o = 1; let cur = el; while (cur && cur !== document.documentElement) { o *= parseFloat(getComputedStyle(cur).opacity || '1'); cur = cur.parentElement; } return o; };
  const out = []; const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n; let total = 0;
  while ((n = walker.nextNode())) {
    const t = n.textContent.replace(/\s+/g, ' ').trim(); if (!t || t.length < 2) continue;
    const el = n.parentElement; if (!el) continue;
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;
    if (r.bottom < 0 || r.top > innerHeight * 3) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const fg0 = parse(cs.color); if (!fg0) continue;
    total++;
    const alpha = fg0.a * opacityOf(el);
    const bg = bgOf(el); const fg = over({...fg0, a: alpha}, bg);
    const l1 = lum(fg), l2 = lum(bg); const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const size = parseFloat(cs.fontSize); const need = size >= 18 ? 3 : 4.5;
    const disabled = el.closest('[disabled],[aria-disabled="true"],.disabled') !== null;
    if (!disabled && (alpha < 0.7 || ratio < need)) {
      out.push({text: t.slice(0, 40), alpha: +alpha.toFixed(2), ratio: +ratio.toFixed(2), size, color: cs.color,
                bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`, tag: el.tagName.toLowerCase(),
                cls: (el.className && el.className.baseVal === undefined ? String(el.className) : '').slice(0, 60)});
    }
  }
  return {total, offenders: out};
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--routes", default="trading,portfolio,strategies,risk,backtest,data,settings,system")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--wait", type=int, default=7000)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    results = {}
    bad_total = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": args.width, "height": args.height})
        page = ctx.new_page()
        for route in [r.strip() for r in args.routes.split(",") if r.strip()]:
            page.goto(f"{args.base.rstrip('/')}/#/{route}", wait_until="domcontentloaded")
            page.wait_for_timeout(args.wait)
            res = page.evaluate(JS)
            results[route] = res
            offs = res["offenders"]
            bad_total += len(offs)
            print(f"[{route}] text nodes={res['total']} offenders={len(offs)}")
            seen = set()
            for o in offs:
                key = (o["color"], o["cls"][:30])
                if key in seen:
                    continue
                seen.add(key)
                print(f"   alpha={o['alpha']:.2f} ratio={o['ratio']:.2f} size={o['size']:.0f}px color={o['color']} bg={o['bg']} "
                      f"<{o['tag']} .{o['cls'][:40]}> '{o['text']}'")
        b.close()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
    print(f"TOTAL offenders: {bad_total}")
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main())
