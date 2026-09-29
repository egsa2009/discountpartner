import re, sys

path = "vercel-trigger/public/index.html"
content = open(path, encoding="utf-8").read()

# 1. Add name field styling
old_css = """    .deal-card.has-url .price-hint { display: block; }"""
new_css = """    .deal-card.has-url .price-hint { display: block; }
    .deal-name-wrap {
      padding: 0 12px 10px;
    }
    .deal-name-wrap input {
      font-size: 0.82rem; padding: 7px 9px;
    }"""
assert old_css in content, "CSS anchor not found"
content = content.replace(old_css, new_css)

# 2. Add name field in deal card template (after closing prices div)
old_html = """      </div>\`;

    document.getElementById('dealList').appendChild(card);"""
new_html = """      </div>
      <div class="deal-name-wrap" id="namewrap-${id}" style="display:none">
        <label class="lbl">Nombre del producto <span style="color:var(--accent);font-size:0.65rem">(cópialo de Amazon)</span></label>
        <input type="text" id="name-${id}" placeholder="Ej: Nike Air Max 270 Talla 10..." />
      </div>\`;

    document.getElementById('dealList').appendChild(card);"""
assert old_html in content, "HTML anchor not found"
content = content.replace(old_html, new_html)

# 3. Show name field when URL typed
old_show = """    if (val.length > 5) {
      prices.style.display = 'grid';
      card.classList.add('has-url');
    } else {
      prices.style.display = 'none';
      card.classList.remove('has-url');
    }"""
new_show = """    const namewrap = document.getElementById(`namewrap-${id}`);
    if (val.length > 5) {
      prices.style.display = 'grid';
      if (namewrap) namewrap.style.display = 'block';
      card.classList.add('has-url');
    } else {
      prices.style.display = 'none';
      if (namewrap) namewrap.style.display = 'none';
      card.classList.remove('has-url');
    }"""
assert old_show in content, "Show anchor not found"
content = content.replace(old_show, new_show)

# 4. Include name in getDeals()
old_push = """      deals.push({ url, sale_price: sale, orig_price: orig, discount_pct: disc });"""
new_push = """      const name = (document.getElementById(`name-${id}`)?.value || '').trim();
      deals.push({ url, name, sale_price: sale, orig_price: orig, discount_pct: disc });"""
assert old_push in content, "Push anchor not found"
content = content.replace(old_push, new_push)

open(path, "w", encoding="utf-8").write(content)
print("✅ index.html v4 done")
