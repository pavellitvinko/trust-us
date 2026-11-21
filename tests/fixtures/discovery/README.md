# Discovery fixtures

Recorded from the public Meta Ad Library on 2026-11-21 (country PL, logged out), trimmed to
the fields the parser reads; signed CDN query strings removed.

| File | What |
| --- | --- |
| `treekollo_page.txt` | GraphQL response for advertiser page `1183383648198728` (TreeKollo), the page behind sample case 5. Ads in several enforcement states |
| `treekollo_single_ad.html` | The same page's ad `1982065065827084` as the single-ad page embeds it in HTML, wrapped in a different nesting |
| `search_inwestycja.txt` | Three results of the keyword search `inwestycja`; ordinary businesses, used as negatives |
| `synthetic_healthlinkq.txt` | **Synthetic**, written by hand for tests: an ad whose link points to `healthlinkq.pro`, the landing domain of sample cases 2 and 3. Not a real ad |
| `captcha.html` | **Synthetic**: a page body that sets `captcha_required: true` |
