"""Human-readable labels for the Wikidata properties the pipeline emits.

The Wikidata Studio's entity view mirrors the wikidata.org page, which
shows each property's *label* (e.g. "author" for P50) next to the PID
chip. Fetching labels live would add latency and require network access,
so we ship a static map covering every property the pipeline uses.

Keep this in sync with :mod:`converter.wikidata.property_mapping`.
"""

from __future__ import annotations

PROPERTY_LABELS: dict[str, str] = {
    # Instance / classification
    "P31":  "instance of",
    "P195": "collection",
    "P217": "inventory number",
    "P3959": "NNL catalog ID",
    "P279": "subclass of",
    # Terms / titles
    "P1476": "title",
    "P1448": "official name",
    "P1559": "name in native language",
    "P2093": "author name string",
    # Content
    "P407":  "language of work or name",
    "P282":  "writing system",
    "P136":  "genre",
    "P921":  "main subject",
    "P1574": "exemplar of",
    "P527":  "has parts",
    "P361":  "part of",
    # Creation / production
    "P571":  "inception",
    "P1071": "location of creation",
    "P50":   "author",
    "P11603": "transcribed by",
    "P11105": "annotator",
    "P88":   "commissioned by",
    "P110":  "illustrator",
    "P655":  "translator",
    "P1780": "school of",
    "P1774": "workshop of",
    "P12095": "fonds",
    "P9046": "commentary by",
    # Provenance
    "P127":  "owned by",
    "P580":  "start time",
    "P582":  "end time",
    "P1028": "donated by",
    "P7153": "significant place",
    "P793":  "significant event",
    # Physical description
    "P186":  "material used",
    "P2048": "height",
    "P2049": "width",
    "P1104": "number of pages",
    "P7416": "folio(s) [citation qualifier]",
    "P5816": "state of conservation",
    "P1552": "has characteristic",
    "P9302": "script style",
    "P2635": "number of parts of this work",
    # Inscription / content body
    "P1684": "inscription",
    "P7535": "scope and content",
    # Digital access
    "P973":  "described at URL",
    "P2888": "exact match",
    "P6108": "manifest URL",
    "P953":  "full work available at URL",
    "P18":   "image",
    # Authority identifiers
    "P214":  "VIAF ID",
    "P8189": "National Library of Israel J9U ID",
    "P244":  "Library of Congress authority ID",
    "P227":  "GND ID",
    "P213":  "ISNI",
    "P268":  "BnF ID",
    "P1566": "GeoNames ID",
    # References
    "P248":  "stated in",
    "P854":  "reference URL",
    "P813":  "retrieved",
    "P887":  "based on heuristic",
    # Generic qualifiers
    "P1932": "object named as",
    "P1480": "sourcing circumstances",
    "P3831": "object has role",
    "P1319": "earliest date",
    "P1326": "latest date",
    # Persons
    "P106":  "occupation",
    "P569":  "date of birth",
    "P570":  "date of death",
    "P19":   "place of birth",
    "P20":   "place of death",
    "P27":   "country of citizenship",
    "P21":   "sex or gender",
    "P1412": "languages spoken, written or signed",
    "P1343": "described by source",
    # Location
    "P17":   "country",
    "P131":  "located in the administrative territorial entity",
    # Catalog
    "P528":  "catalog code",
    "P972":  "catalog",
    # Copyright
    "P6216": "copyright status",
    "P1001": "applies to jurisdiction",
    # WikiProject
    "P5008": "on focus list of Wikimedia project",
}


# ── Known-QID labels — surface the human label next to any item-value ─────
#
# Keep narrow: only the QIDs the pipeline routinely emits (genre/subject
# mappings, hardcoded country/city, calendar models, etc.).

QID_LABELS: dict[str, str] = {
    "Q33513": "Hebrew alphabet",
    "Q9190": "Exodus",
    "Q188915": "National Library of Israel",
    "Q46815": "Israel Museum",
    # Top-level classes
    "Q5":       "human",
    "Q43229":   "organization",
    "Q87167":   "manuscript",
    "Q47461344": "written work",
    "Q871232":  "editorial collective",     # placeholder used for some colls
    # Countries / places (pipeline hardcodes)
    "Q801":     "Israel",
    "Q1218":    "Jerusalem",
    # Calendar models
    "Q1985727": "proleptic Gregorian calendar",
    "Q1985786": "Julian calendar",
    # Hebrew / languages
    "Q9288":    "Hebrew",
    # Gender
    "Q6581097": "male",
    "Q6581072": "female",
    # Catalog
    "Q118384267": "Ktiv",
    # Common roles
    "Q916292":  "scribe",
    "Q333634":  "translator",
    "Q106313281": "commentator",
    "Q1773840": "provenance",
    # Source heuristics
    "Q2539":    "machine learning",
    # Sourcing circumstances
    "Q18122778": "presumably",
    "Q30230067": "possibly",
    # Copyright
    "Q19652":   "public domain",
    # Verified static genre and subject targets
    "Q102786": "abbreviation",
    "Q107427": "Halakha",
    "Q11190": "medicine",
    "Q123006": "Kabbalah",
    "Q133492": "letter",
    "Q1543943": "ketubah",
    "Q155321": "Land of Israel",
    "Q1631107": "bibliography",
    "Q170539": "parody",
    "Q172331": "liturgy",
    "Q1749541": "commentary",
    "Q180115": "Purim",
    "Q1845": "Bible",
    "Q18562479": "vital record",
    "Q191825": "Mishnah",
    "Q208398": "Karaite Judaism",
    "Q2095829": "pinkasim",
    "Q223681": "apostasy",
    "Q2350579": "Sefer Torah",
    "Q247034": "mezuzah",
    "Q25372": "drama",
    "Q25538572": "will",
    "Q28807008": "bar mitzvah",
    "Q333": "astronomy",
    "Q3348095": "register of deaths",
    "Q3359388": "negotiable instrument",
    "Q3427762": "Rabbinic responsa",
    "Q34362": "astrology",
    "Q34990": "Torah",
    "Q3595842": "phlebotomy",
    "Q36279": "biography",
    "Q36348": "dream",
    "Q381885": "tomb",
    "Q40953": "prayer",
    "Q43290": "Talmud",
    "Q44722": "Hebrew calendar",
    "Q47054": "riddle",
    "Q471894": "Siddur",
    "Q482": "poetry",
    "Q48498": "illuminated manuscript",
    "Q485228": "family register",
    "Q49084": "short story",
    "Q49848": "document",
    "Q5043": "Christianity",
    "Q55017318": "biblical literature",
    "Q56055312": "sepulchral monument",
    "Q5891": "philosophy",
    "Q60797": "sermon",
    "Q6674": "devil",
    "Q7325": "Jewish people",
    "Q7487201": "shaliah",
    "Q781402": "piyyut",
    "Q7944": "earthquake",
    "Q79719": "license",
    "Q804154": "business record",
    "Q8091": "grammar",
    "Q814999": "conversion to Christianity",
    "Q8242": "literature",
    "Q837795": "Jewish philosophy",
    "Q840378": "gematria",
    "Q3850835": "Masorah",
    "Q848599": "brit milah",
    "Q861258": "shechita",
    "Q9026959": "autograph",
    # Fetched live 2026-08-05 (`wbgetentities`, English labels as returned) so
    # every QID the projection can emit carries a gloss (Rule W-80). The audit
    # that produced this list is what surfaced 24 wrong P/Q constants in
    # BIBLE_BOOK_TO_QID and TALMUD_TRACTATE_TO_QID.
    "Q104378399": "dubious",
    "Q1063210": "Yoma",
    "Q106379705": "damaged",
    "Q106959824": "unlocated, probably destroyed",
    "Q107256474": "leaf",
    "Q107531416": "mildly damaged",
    "Q1136474": "Costas loop",
    "Q11472": "paper",
    "Q121094898": "Ashkenazic Script (Hebrew script)",
    "Q121094936": "Yemenite Script (hebrew script)",
    "Q122901270": "lower script",
    "Q122901275": "upper script",
    "Q123078816": "WikiProject Manuscripts",
    "Q125576": "papyrus",
    "Q1264302": "Nazir",
    "Q131458": "Isaiah",
    "Q131590": "Jeremiah",
    "Q1321": "Spanish",
    "Q133177480": "Sepharadic script (Hebrew script)",
    "Q133327488": "Oriental Script (hebrew Script)",
    "Q133370075": "Italian script (Hebrew script)",
    "Q133370466": "Byzantine script (Hebrew script)",
    "Q136350185": "poor",
    "Q13955": "Arabic",
    "Q150": "French",
    "Q1561132": "Josippon",
    "Q1641020": "palm-leaf manuscript",
    "Q17051386": "Nedarim",
    "Q181620": "Books of Samuel",
    "Q1860": "English",
    "Q188": "German",
    "Q19602268": "chained book",
    "Q1974785": "Pesahim",
    "Q201029": "Mishneh Torah",
    "Q205388": "Zohar",
    "Q20734200": "not completed",
    "Q213924": "codex",
    "Q2211504": "Berakhot",
    "Q226697": "parchment",
    "Q234460": "text",
    "Q2358436": "Massechet Sanhedrin",
    "Q2363125": "Shevu'ot",
    "Q25285": "Tatar",
    "Q256": "Turkish",
    "Q2703125": "Mishnah Shabbat",
    "Q274076": "palimpsest",
    "Q2740944": "Tikkun Chatzot",
    "Q28602": "Aramaic",
    "Q30103158": "manuscript fragment",
    "Q3299332": "Ryo Kanazawa",
    "Q33308141": "composite manuscript",
    "Q33367": "Judeo-Persian",
    "Q35497": "Ancient Greek",
    "Q36196": "Judaeo-Spanish",
    "Q36510": "Modern Greek",
    "Q372474": "colophon",
    "Q3749265": "fragment",
    "Q37733": "Judeo-Arabic",
    "Q378274": "vellum",
    "Q397": "Latin",
    "Q41064": "Psalms",
    "Q41490": "Leviticus",
    "Q41719": "hypothesis",
    "Q4224666": "Books of Kings",
    "Q42614": "Deuteronomy",
    "Q43099": "Book of Numbers",
    "Q4577": "Book of Job",
    "Q4579": "Proverbs",
    "Q47680": "Joshua",
    "Q482980": "author",
    "Q50423863": "copyrighted",
    "Q5146": "Portuguese",
    "Q56556915": "demolished or destroyed",
    "Q56557591": "preserved",
    "Q571": "book",
    "Q5727902": "circa",
    "Q6124976": "Pirush Hamishnayot",
    "Q61962974": "disassembled",
    "Q623354": "Haggadah",
    "Q652": "Italian",
    "Q657535": "Tractate Ketubot",
    "Q66890153": "unknown preservation status",
    "Q7411": "Dutch",
    "Q75505084": "restored",
    "Q791251": "Avodah Zarah",
    "Q811988": "Bava Batra",
    "Q811989": "Bava Kamma",
    "Q822206": "Shulchan Aruch",
    "Q83367": "Tanakh",
    "Q860740": "learning disability",
    "Q8641": "Yiddish",
    "Q9168": "Persian",
    "Q9184": "Book of Genesis",
    "Q927314": "Sotah",
    "Q927378": "Tractate Kiddushin",
    "Q1069725": "page",
    "Q113016548": "papyrus scroll",
    "Q1145267": "Curt Paul Janz",
    "Q15632617": "fictional human",
    "Q179808": "Palme d'Or",
    "Q21857942": "Stolpersteine in Upper Austria",
    "Q284465": "lectionary",
    "Q3884": "Amazon",
    "Q54919": "Virtual International Authority File",
    "Q95065857": "papyrus fragment",
}


def property_label(pid: str) -> str:
    """Return the best known label for *pid* (falls back to the PID itself)."""
    return PROPERTY_LABELS.get(pid, pid)


def qid_label(qid: str) -> str:
    """Return the best known label for *qid* (falls back to the QID itself).

    Holding institutions are NOT duplicated here: ``holding_institutions`` already
    records a verified English label beside every QID it resolves (Rule W-143), so
    that table is consulted rather than copied. Without this, a P195 on
    Q1256981 rendered `value_label: null` in the verify pack even though the label
    "San Francisco State University" was sitting one module away (Rule W-80).
    """
    label = QID_LABELS.get(qid)
    if label:
        return label
    from converter.wikidata.holding_institutions import (  # noqa: PLC0415
        institution_label,
    )

    return institution_label(qid) or qid
