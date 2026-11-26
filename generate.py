"""Génère une page HTML simple listant les événements publics de l'agenda
ESCP Alumni, pour servir de source au chatbot Brevo.

L'agenda du site est chargé par script : on l'ouvre donc dans un navigateur
invisible (Playwright), on parcourt toutes ses pages, puis on lit chaque page
d'événement pour récupérer le titre complet et la description."""
import html
import re
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

AGENDA = "https://www.escpalumni.org/fr/agenda-11076"
SORTIE = "docs/index.html"
MINIMUM = 10  # en dessous, on considère que la lecture a échoué
LONGUEUR_RESUME = 350

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

EXTRAIRE = """() => {
  const t = s => (s || '').replace(/\\s+/g, ' ').trim();
  return [...document.querySelectorAll('.event-list-item')].map(it => {
    const a = it.querySelector('h5 a') || it.querySelector('a[href*="/agenda/"]');
    return {
      date: t(it.querySelector('header.post-head')?.innerText),
      titre: t(a?.innerText),
      lien: a ? a.href : '',
      desc: t(it.querySelector('.list-e-descriptif')?.innerText),
      lieu: t(it.querySelector('.event-item-tag.place')?.innerText),
      type: t(it.querySelector('.event-item-tag.tag-type')?.innerText),
    };
  });
}"""


def lire_agenda(page):
    """Parcourt toutes les pages de l'agenda et renvoie la liste des événements."""
    page.goto(AGENDA, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector(".event-list-item", timeout=45000)
    except Exception:
        print("Agenda non chargé. Titre de la page :", page.title())
        print(page.inner_text("body")[:3000])
        raise
    evenements, vus = [], set()
    for _ in range(50):  # garde-fou
        nouveaux = 0
        for ev in page.evaluate(EXTRAIRE):
            if ev["lien"] and ev["lien"] not in vus:
                vus.add(ev["lien"])
                evenements.append(ev)
                nouveaux += 1
        suivant = page.query_selector("a.page-link.next")
        if not suivant or nouveaux == 0:
            break
        premier = page.eval_on_selector(".event-list-item a[href*='/agenda/']", "a => a.href")
        suivant.click()
        try:
            page.wait_for_function(
                "p => { const a = document.querySelector(\".event-list-item a[href*='/agenda/']\");"
                " return a && a.href !== p; }", arg=premier, timeout=20000)
        except Exception:
            break
    return evenements


def meta(contenu, propriete):
    m = re.search(rf'<meta[^>]+property="{propriete}"[^>]+content="([^"]*)"', contenu)
    return html.unescape(m.group(1)).strip() if m else ""


def completer(page, ev):
    """Lit la page de l'événement pour avoir le titre complet et une meilleure description."""
    try:
        rep = page.request.get(ev["lien"], timeout=30000)
        if rep.ok:
            contenu = rep.text()
            ev["titre"] = meta(contenu, "og:title") or ev["titre"]
            ev["desc"] = meta(contenu, "og:description") or ev["desc"]
    except Exception as e:
        print(f"Page non lue ({ev['lien']}) : {e}")
    ev["titre"] = re.sub(r"\.\.\.$", "…", ev["titre"])
    ev["desc"] = re.sub(r"\s+", " ", ev["desc"]).strip()
    if len(ev["desc"]) > LONGUEUR_RESUME:
        ev["desc"] = ev["desc"][:LONGUEUR_RESUME].rsplit(" ", 1)[0] + "…"


def date_evenement(texte, maintenant):
    """« Jeudi 26 Novembre à 18:30 » ou « 18 Juin 2027 à 13:00 » -> datetime."""
    m = re.search(r"(\d{1,2})\s+([a-zéû]+)(?:\s+(\d{4}))?\s+à\s+(\d{1,2}):(\d{2})", texte.lower())
    if not m or m.group(2) not in MOIS:
        return None
    jour, mois = int(m.group(1)), MOIS.index(m.group(2)) + 1
    annee = int(m.group(3)) if m.group(3) else maintenant.year
    d = datetime(annee, mois, jour, int(m.group(4)), int(m.group(5)))
    if not m.group(3) and d < maintenant - timedelta(days=60):
        d = d.replace(year=annee + 1)  # janvier vu en novembre -> année suivante
    return d


def date_fr(d):
    jour = "1er" if d.day == 1 else str(d.day)
    return f"{JOURS[d.weekday()]} {jour} {MOIS[d.month - 1]} {d.year} à {d.strftime('%Hh%M')}"


def format_et_categorie(lieu, type_):
    en_ligne = lambda s: bool(re.search(r"en ligne|online|zoom|teams", s, re.I))
    if en_ligne(lieu):
        return "En ligne", type_ or "Non précisée"
    if en_ligne(type_):  # étiquette « En ligne » sur un événement avec un lieu : hybride
        return "Présentiel et en ligne", "Non précisée"
    return "Présentiel", type_ or "Non précisée"


def main():
    maintenant = datetime.now(timezone.utc).replace(tzinfo=None)
    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        page = navigateur.new_page(
            locale="fr-FR",
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
        evenements = lire_agenda(page)
        print(f"{len(evenements)} événements trouvés dans l'agenda")
        if len(evenements) < MINIMUM:
            raise SystemExit("Trop peu d'événements : la page existante est conservée.")
        for ev in evenements:
            completer(page, ev)
        navigateur.close()

    blocs = []
    for ev in evenements:
        d = date_evenement(ev["date"], maintenant)
        quand = date_fr(d) if d else ev["date"]
        fmt, cat = format_et_categorie(ev["lieu"], ev["type"])
        lieu = ev["lieu"] or "Non précisé"
        blocs.append(f"""<article>
<h2>{html.escape(ev['titre'])}</h2>
<p><strong>Date :</strong> {html.escape(quand)}</p>
<p><strong>Format :</strong> {fmt}</p>
<p><strong>Lieu :</strong> {html.escape(lieu)}</p>
<p><strong>Catégorie :</strong> {html.escape(cat)}</p>
<p><strong>Inscription :</strong> <a href="{html.escape(ev['lien'])}">{html.escape(ev['lien'])}</a></p>
<p><strong>Résumé :</strong> {html.escape(ev['desc'])}</p>
</article>""")

    maj = date_fr(maintenant + timedelta(hours=1))
    page_html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agenda ESCP Alumni : événements à venir</title>
<style>
body {{ font-family: Georgia, "Times New Roman", serif; max-width: 46rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.55; color: #1a1a2e; background: #fff; }}
h1 {{ font-size: 1.8rem; margin-bottom: .2rem; }}
h2 {{ font-size: 1.15rem; margin: 0 0 .4rem; }}
article {{ padding: 1rem 0; border-top: 1px solid #d0d0dc; }}
article p {{ margin: .2rem 0; }}
a {{ color: #1d3c8f; word-break: break-all; }}
.intro {{ color: #444; }}
</style>
</head>
<body>
<h1>Agenda ESCP Alumni : événements à venir</h1>
<p class="intro">Liste des {len(evenements)} événements publics publiés sur escpalumni.org, du plus proche au plus lointain. Mise à jour automatique le {maj} (heure de Paris).</p>
<p class="intro">Pour toute question sur l'agenda, proposer les événements pertinents de cette liste avec la date, le format (présentiel ou en ligne), le lieu et le lien d'inscription, en recopiant le lien exactement. Les catégories « Groupes Pro », « Groupes géographiques », « Groupes internationaux » et « Groupes de programme » correspondent aux groupes ESCP Alumni ; les « Webinaires Career Success » sont les webinaires carrière. Agenda complet : <a href="{AGENDA}">{AGENDA}</a>. Contact : Florian, relation membres, fcorreard@escpalumni.org, ou info@escpalumni.org.</p>
{chr(10).join(blocs)}
</body>
</html>
"""
    with open(SORTIE, "w", encoding="utf-8") as f:
        f.write(page_html)
    print(f"Page écrite : {SORTIE}")


if __name__ == "__main__":
    main()
