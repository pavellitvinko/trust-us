"""Write the hand-read ImageReading fixtures (pixel boxes on a 710x1536 screenshot)."""
import json, sys
W, H = 710, 1536
FIELDS = ["visible_text","person_names","brands_or_offers","claimed_publishers","advertiser_pages",
          "visible_domains","cta_labels","logos","proof_objects","imitated_interfaces"]
def it(v, x, y, w, h, c=0.95):
    return {"value": v, "region": {"x": round(x/W,4), "y": round(y/H,4), "width": round(w/W,4), "height": round(h/H,4)}, "confidence": c}
def reading(**f):
    return {k: f.get(k, []) for k in FIELDS}
FEED_TAIL = lambda dy, caption=True: ([it("2", 76, 1196+dy, 14, 24), it("1 wyświetlenie", 22, 1260+dy, 176, 26)]
    + ([it("Poznaj wszystkie szczegóły 👉", 22, 1300+dy, 345, 26), it("Czas ucieka!", 22, 1334+dy, 148, 26)] if caption else []))
R = {
 "IMG_3631.webp": reading(
   visible_text=[it("www.facebook.com",250,100,240,30), it("Instagram",305,138,100,22), it("Link do reklamy",44,272,250,32),
     it("Ta reklama pochodzi z linku z adresem URL",44,340,445,24), it("Aktywna",150,430,90,26),
     it("Identyfikator biblioteki: 2186769435230928",104,476,420,26),
     it("Data zamieszczenia: 11 sie 2026 · Łączny czas aktywności: 8 godz.",104,518,455,50),
     it("Platformy",104,594,95,24), it("Reklama ma kilka wersji.",104,636,240,24), it("Transparentność w UE",104,680,215,24),
     it("Zobacz szczegóły reklamy",220,746,300,28), it("Sustain Studio",176,856,168,28), it("Sponsorowane",176,892,146,24),
     it("Czas ucieka!",110,936,122,24), it("Zamknij",546,1236,92,26),
     it("Liczba reklam wykorzystujących ten materiał reklamowy i tekst: 5",74,1328,520,60,0.8)],
   advertiser_pages=[it("Sustain Studio",176,856,168,28)],
   visible_domains=[it("www.facebook.com",250,100,240,30)],
   logos=[it("Meta",36,210,160,40,0.9)],
   imitated_interfaces=[it("TV news broadcast frame",242,978,255,218,0.6)]),
 "IMG_3632.webp": reading(
   visible_text=[it("www.facebook.com",250,100,240,30), it("Instagram",305,138,100,22), it("Szczegóły reklamy",44,288,240,30),
     it("Aktywna",106,396,90,24), it("Identyfikator biblioteki: 2186769435230928",58,442,422,26),
     it("Data zamieszczenia: 11 sie 2026 · Łączny czas aktywności: 8 godz.",58,484,592,50),
     it("Platformy",58,560,95,22), it("Reklama ma kilka wersji.",58,602,240,24),
     it("Sustain Studio",172,670,168,28), it("Sponsorowane",172,706,146,24),
     it("Identyfikator biblioteki:",172,742,222,24), it("2186769435230928",172,770,200,24),
     it("Czas ucieka!",60,822,166,30), it("Zamknij",546,1296,92,26)],
   advertiser_pages=[it("Sustain Studio",172,670,168,28)],
   visible_domains=[it("www.facebook.com",250,100,240,30)],
   logos=[it("Meta",36,210,160,40,0.9), it("polsat news",598,880,50,40,0.6)],
   imitated_interfaces=[it("TV news broadcast frame",60,870,590,385,0.7)]),
 "IMG_3627.webp": reading(
   visible_text=[it("Nie będzie żadnej... więcej",120,40,420,30,0.6), it("Sustain Studio",95,138,175,28), it("Sponsorowane",95,170,120,22),
     it("polsat news",640,256,60,40,0.8), it("WYDARZENIA.INTERIA.PL",530,970,165,16,0.8),
     it("tym, jak zwykła Polka o",155,1000,400,42), it("Dowiedz się więcej",30,1116,232,28)] + FEED_TAIL(0),
   advertiser_pages=[it("Sustain Studio",95,138,175,28)],
   visible_domains=[it("WYDARZENIA.INTERIA.PL",530,970,165,16,0.8)],
   cta_labels=[it("Dowiedz się więcej",30,1116,232,28)],
   logos=[it("polsat news",640,205,60,90,0.9)],
   claimed_publishers=[it("Polsat News",640,205,60,90,0.8)],
   imitated_interfaces=[it("TV news broadcast with lower-third caption",0,200,710,890,0.8)]),
 "IMG_3628.webp": reading(
   visible_text=[it("Nie będzie żadnej... więcej",120,40,420,30,0.6), it("Sustain Studio",95,138,175,28), it("Sponsorowane",95,170,120,22),
     it("polsat news",640,256,60,40,0.8), it("WYDARZENIA.INTERIA.PL",530,970,165,16,0.8),
     it("imieniu Maria opublikowała na",95,1000,520,42), it("Dowiedz się więcej",30,1116,232,28)] + FEED_TAIL(0),
   person_names=[it("Maria",215,1000,95,42,0.7)],
   advertiser_pages=[it("Sustain Studio",95,138,175,28)],
   visible_domains=[it("WYDARZENIA.INTERIA.PL",530,970,165,16,0.8)],
   cta_labels=[it("Dowiedz się więcej",30,1116,232,28)],
   logos=[it("polsat news",640,205,60,90,0.9)],
   claimed_publishers=[it("Polsat News",640,205,60,90,0.8)],
   imitated_interfaces=[it("TV news broadcast with lower-third caption",0,200,710,890,0.8)]),
 "IMG_3626.webp": reading(
   visible_text=[it("Sustain Studio",95,82,175,28), it("Sponsorowane",95,114,150,22), it("Dowiedz się więcej",30,1072,232,28)]
     + FEED_TAIL(-40) + [it("Fresh Perspectives",95,1372,232,28,0.8)],
   advertiser_pages=[it("Sustain Studio",95,82,175,28), it("Fresh Perspectives",95,1372,232,28,0.6)],
   cta_labels=[it("Dowiedz się więcej",30,1072,232,28)]),
 "IMG_3625.webp": reading(
   visible_text=[it("8353",70,72,60,26), it("1113",168,72,60,26), it("145",290,72,46,26), it("302",380,72,46,26),
     it("Nie będzie żadnej... więcej",120,128,440,28,0.8), it("Sustain Studio",95,212,175,28), it("Sponsorowane",95,244,120,22),
     it("6:44",630,318,55,22,0.5), it("Dowiedz się więcej",30,1202,232,28), it("2",76,1286,14,24), it("1 wyświetlenie",22,1346,176,26),
     it("Fresh Perspectives",95,1488,232,26,0.7)],
   advertiser_pages=[it("Sustain Studio",95,212,175,28), it("Fresh Perspectives",95,1488,232,26,0.5)],
   cta_labels=[it("Dowiedz się więcej",30,1202,232,28)]),
}
json.dump(R, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
