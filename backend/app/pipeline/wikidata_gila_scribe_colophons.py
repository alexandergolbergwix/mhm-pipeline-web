"""Reviewed catalogue evidence for named manuscript scribes."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_source_choices import _claim_values
from converter.wikidata.property_mapping import P_FOLIO

DELFOGIT_QID = "Q141175708"
DELFOGIT_HEADING = "דלפוגיט, אליהו"
DELFOGIT_PENN = "https://find.library.upenn.edu/catalog/9977510917903681"
DELFOGIT_PARMA = (
    "https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage"
    "?docId=PNX_MANUSCRIPTS990000810710205171&scope=KTIV&vid=KTIV"
)
DELFOGIT_WRONG_IDS = {"P8189": "987007453092705171", "P214": "33148874584149621414"}

MILHAUD_QID = "Q132798628"
MILHAUD_HEADING = "מיליאב, עמנואל בן גד"
MILHAUD_DS_ID = "Q75810"
MILHAUD_DS_URL = "https://catalog.digital-scriptorium.org/wiki/Item:Q75810"
MILHAUD_COLUMBIA = "https://exhibitions.library.columbia.edu/exhibits/show/hebrew_mss/item/7261"

COLUMBIA_EXEMPLAR = "990001371830205171"
COLUMBIA_EXEMPLAR_URL = "https://clio.columbia.edu/catalog/17229167/librarian_view"
COLUMBIA_DELFOGIT = "990001372150205171"
COLUMBIA_DELFOGIT_URL = "https://clio.columbia.edu/catalog/17327314/librarian_view"
MISATTRIBUTION = "Q105675146"


@dataclass(frozen=True)
class ReviewedScribeExclusion:
    person_qid: str
    retained_heading: str
    reference_url: str
    retained_qid: str | None = None


@dataclass(frozen=True)
class ReviewedScribePerson:
    qid: str
    hebrew_name: str | None
    urls: tuple[str, ...]
    identifier: tuple[str, str] | None = None
    sitelink: tuple[str, str] | None = None
    wrong_ids: tuple[tuple[str, str], ...] = ()

    def matches(self, item: Mapping) -> bool:
        if item.get("id") != self.qid:
            return False
        if self.hebrew_name and item.get("labels", {}).get("he", {}).get("value") != self.hebrew_name:
            return False
        if self.identifier:
            pid, value = self.identifier
            if _claim_values(item, pid) != [value]:
                return False
        if self.sitelink:
            site, title = self.sitelink
            if item.get("sitelinks", {}).get(site, {}).get("title") != title:
                return False
        return all(value not in _claim_values(item, pid) for pid, value in self.wrong_ids)


@dataclass(frozen=True)
class ReviewedScribeNote:
    person: ReviewedScribePerson
    occurrence: str
    heading: str
    digest: str
    tag: str = "957"
    former_heading: str | None = None
    corrected_heading: str | None = None
    folio: str | None = None

    @property
    def exact_qualifiers(self) -> dict[str, str]:
        qualifiers = {}
        if self.corrected_heading or self.folio:
            qualifiers["P1932"] = self.corrected_heading or self.heading
        if self.folio:
            qualifiers[P_FOLIO] = self.folio
        return qualifiers


_QATI = ReviewedScribePerson(
    "Q108378115", "משה קטיעי",
    ("https://teman.org.il/content/13032",),
    sitelink=("hewiki", "משה קטיעי"),
)
_ELADWI = ReviewedScribePerson(
    "Q141175696", "יוסף בן עמרם בן עודד אלעדוי",
    ("https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage"
     "?docId=PNX_MANUSCRIPTS990001366470205171&scope=KTIV&vid=KTIV",),
    identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1177"),
    wrong_ids=(("P8189", "987007402783005171"), ("P214", "68156495355517561081")),
)
_HAGAGI = ReviewedScribePerson(
    "Q141953242", "חיים בן שלום בן דוד בן יצחק אלחגאג",
    ("https://searcharchives.bl.uk/catalog/032-003376507",),
    identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1114"),
    wrong_ids=(("P8189", "987007312320105171"),),
)
_MEHLER = ReviewedScribePerson(
    "Q1710859", "יהודה מילר רויטלינגן",
    ("https://www.deutsche-biographie.de/sfz59820.html",),
    identifier=("P227", "136768687"),
)
_BASHIRI = ReviewedScribePerson(
    "Q22935567", "יחיא בשירי",
    ("https://id.loc.gov/authorities/names/no2008160185.html",),
    identifier=("P244", "no2008160185"),
)


_HOTER_1650 = ReviewedScribePerson(
    "Q142258354", "חטר בן דוד",
    (
        "https://www.nli.org.il/he/manuscripts/NNL_ALEPH990001878530205171/NLI",
        "https://www.nli.org.il/he/manuscripts/NNL_ALEPH990025656430205171/NLI",
        "https://viaf.org/viaf/sourceID/J9U%7C987007522648605171",
    ),
    identifier=("P8189", "987007522648605171"),
)

_BARAKA_CAIRO = ReviewedScribePerson(
    "Q142258379", "יוסף בן רצון בן שמואל בן אהרן ברכה",
    (
        "https://sammlungen.ub.uni-frankfurt.de/freimann/download/pdf/1353313#page=22",
        "https://gallica.bnf.fr/ark:/12148/btv1b10540882h",
        "https://viaf.org/viaf/sourceID/J9U%7C987010933984205171",
    ),
    identifier=("P8189", "987010933984205171"),
)


_PINHAS_BEN_GAD = ReviewedScribePerson(
    "Q142258526", "פינחס בן גד הכהן",
    (
        "https://cris.biu.ac.il/en/publications/manuscript-of-rekhev-elohim-by-r-yishaq-wannah-copied-by-r-pinhas/",
        "https://viaf.org/viaf/25146461598227731736/",
    ),
    identifier=("P8189", "987007602408705171"),
)

_JACOB_PHILIP = ReviewedScribePerson(
    "Q142258533", "יעקב חיים פיליף",
    (
        "https://viaf.org/viaf/97260049/",
        "https://www.nli.org.il/en/manuscripts/NNL_ALEPH990000572680205171/NLI",
        "https://www.nli.org.il/he/a-topic/987007307890705171",
    ),
    identifier=("P8189", "987007307890705171"),
)

_ISRAEL_TUVIA_LINGI = ReviewedScribePerson(
    "Q142258542", "ישראל שלמה בן טוביה לינגי",
    (
        "https://viaf.org/viaf/216175412666203711416/",
        "https://www.nli.org.il/he/a-topic/987013431293005171",
    ),
    identifier=("P8189", "987013431293005171"),
)


_REVIEWED_SCRIBE_QAYSI = ReviewedScribePerson(
    "Q142258799", "סעדיה בן שלמה בן סעדיה אלקיסי",
    (
        "https://teman.org.il/content/13042",
    ),
    identifier=("P973", "https://teman.org.il/content/13042"),
)

_REVIEWED_SCRIBE_MAABARI = ReviewedScribePerson(
    "Q142258800", "סעדיה בן מעודד אלמעברי",
    (
        "https://teman.org.il/content/12251",
    ),
    identifier=("P973", "https://teman.org.il/content/12251"),
)

_REVIEWED_SCRIBE_TAWILI = ReviewedScribePerson(
    "Q142258802", "שלום בן יהודה אלטוילי",
    (
        "https://teman.org.il/content/11620",
    ),
    identifier=("P973", "https://teman.org.il/content/11620"),
)

_REVIEWED_SCRIBE_MALIAH = ReviewedScribePerson(
    "Q142258805", "יהודה בן אברהם בן נחום אלמליח",
    (
        "https://teman.org.il/content/12181",
    ),
    identifier=("P973", "https://teman.org.il/content/12181"),
)

_REVIEWED_SCRIBE_SHARARA = ReviewedScribePerson(
    "Q142258808", "עמרם בן שלום בן יתר שרארה",
    (
        "https://teman.org.il/content/13290",
    ),
    identifier=("P973", "https://teman.org.il/content/13290"),
)

_REVIEWED_SCRIBE_UZANI = ReviewedScribePerson(
    "Q142258810", "זכריה בן יוסף אלעזאני",
    (
        "https://teman.org.il/content/12670",
    ),
    identifier=("P973", "https://teman.org.il/content/12670"),
)

_REVIEWED_SCRIBE_KALUFI = ReviewedScribePerson(
    "Q142258812", "זכריה בן נתן בן יהודה כלופי",
    (
        "https://searcharchives.bl.uk/catalog/032-003354975",
        "https://teman.org.il/content/10994",
    ),
    identifier=("P973", "https://teman.org.il/content/10994"),
)

_REVIEWED_SCRIBE_SAADIA_SANHANI = ReviewedScribePerson(
    "Q142258898", "סעדיה בן מעודד בן סעדיה בן יוסף",
    (
        "https://teman.org.il/content/12524",
    ),
    identifier=("P973", "https://teman.org.il/content/12524"),
)


_REVIEWED_SCRIBE_JOSEPH_SHARAFAH = ReviewedScribePerson(
    "Q142259361", "יוסף בן שלמה בן יוסף הלוי",
    (
        "https://teman.org.il/content/12047",
    ),
    identifier=("P973", "https://teman.org.il/content/12047"),
)


# Bind each identification to the full set of notes in the reviewed MARC field.
_EXTRA_SCRIBE_NOTES = {
    "990000550940205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259360", "חיים בן סעדיה",
            (
                "https://teman.org.il/content/11489",
            ),
            identifier=("P973", "https://teman.org.il/content/11489"),
        ),
        "700:1", "חיים בן סעדיה",
        "4e93740956710d7d871d9ba99dc79f96c5fdb620d1ba36ecb4e77d09b60bd346",
    ),
    "990000558280205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_JOSEPH_SHARAFAH,
        "700:1", "יוסף בן שלמה",
        "89e5cf44bd48486eab2f3b026c2f6b06530af531df2149aa6e9b186dfa66602b",
        former_heading="יוסף בן שלמה הלוי",
        corrected_heading="יוסף בן שלמה הלוי",
    ),
    "990000563810205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258810", "זכריה בן יוסף אלעזאני",
            (
                "https://teman.org.il/content/11362",
                "https://teman.org.il/content/12670",
            ),
            identifier=("P973", "https://teman.org.il/content/12670"),
        ),
        "700:1", "זכריה בן סעדיה בן שלמה",
        "4c84ec6f1323318b6a9d93c89f73db2ac31ad66ce97960f7c247634ee06feb99",
    ),
    "990000594010205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259363", "סעדיה בן שלום בן שלמה בן בניה הלוי",
            (
                "https://teman.org.il/content/12080",
            ),
            identifier=("P973", "https://teman.org.il/content/12080"),
        ),
        "700:1", "סעדיה בן שלום בן שלמה בן בניה לוי",
        "3e834e63a2dd4e784e2486dccea43d6781468f6e3e71dfebf7b32f34d19294e4",
    ),
    "990000856190205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259379", "שאול בן יוסף מררי",
            (
                "https://sammlungen.ub.uni-frankfurt.de/freimann/content/pageview/6117251",
                "https://searcharchives.bl.uk/catalog/032-003313863",
            ),
            identifier=("P973", "https://sammlungen.ub.uni-frankfurt.de/freimann/content/pageview/6117251"),
        ),
        "700:1", "מררי, שאול בן יוסף",
        "812c4c7b7a345beb7a7f39c8e6db36e2c31e692c4e3bd976476488af93a36962",
        tag="500",
    ),
    "990000928800205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q3460419", "סעדיה אזנקוט",
            (
                "https://dbs.anumuseum.org.il/skn/e24520398",
            ),
            sitelink=("enwiki", "Saadia Azankot"),
        ),
        "700:1", "אזנקוט, סעדיה בן לוי",
        "fbb7d64c6a5e2627bfe831be24b0ee79e95dbf0a858f7b6572b44d2db77ca0e0",
    ),
    "990000942930205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259306", "מעודד בן שמריה בן נחום בניחגאג",
            (
                "https://teman.org.il/content/12264",
            ),
            identifier=("P973", "https://teman.org.il/content/12264"),
        ),
        "700:1", "בניחגאג, מעודד בן שמריה בן נחום",
        "bf32e43adb9f6c6911ba54ff58e3c72b236fd5a7ea921fc136f365c6ed486f34",
        folio="pages 31–38",
    ),
    "990001011990205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259355", "שמואל בן סעדיה",
            (
                "https://teman.org.il/content/13270",
            ),
            identifier=("P973", "https://teman.org.il/content/13270"),
        ),
        "700:1", "שמואל בן סעדיה",
        "443a4eb707a87892f11c24699ecd7faf7a2c7debec967872a95d45bbcd128cdb",
    ),
    "990001022840205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259303", "טוב בן אברהם מדנואר",
            (
                "https://teman.org.il/content/11602",
            ),
            identifier=("P973", "https://teman.org.il/content/11602"),
        ),
        "700:1", "טוב בן אברהם",
        "d9e0f51b699a26b4bd5424e7fd170c0c5079264f17a1d44bfdb89e8ec02ccbd7",
    ),
    "990001022860205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259311", "אברהם בן סעדיה בן עמרם בן דוד",
            (
                "https://teman.org.il/content/10967",
            ),
            identifier=("P973", "https://teman.org.il/content/10967"),
        ),
        "700:1", "אברהם בן סעדיה בן עמרם בן דוד",
        "b28982b1d243084d39032fc025e7dbacd30f399c53e651b004ace128fcce262b",
    ),
    "990001022870205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259304", "אברהם בן זכריה מאלבאקלי",
            (
                "https://teman.org.il/content/10953",
            ),
            identifier=("P973", "https://teman.org.il/content/10953"),
        ),
        "700:1", "אברהם בן זכריה",
        "b40d1a02ce27ef8b6aa76c9c613e45dfb5137f2acd183ff5c0c554419e0a4c2a",
    ),
    "990001022880205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259366", "משה בן יוסף בן מעודד אלאשרסי",
            (
                "https://teman.org.il/content/11029",
            ),
            identifier=("P973", "https://teman.org.il/content/11029"),
        ),
        "700:1", "אלאשרסי, משה בן יוסף בן מעודד",
        "3488a627aad1fb2da6793ad524a488093c4dd78c81b299349bbe84916704e005",
    ),
    "990001062450205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259336", "ישע בן דוד בן נחום הלוי",
            (
                "https://teman.org.il/content/12061",
            ),
            identifier=("P973", "https://teman.org.il/content/12061"),
        ),
        "700:2", "ישע בן דוד בן נחום",
        "07d0d92600bbd18075efa8d122ea23cccf228dc27c90bb560b65271f1c8112de",
        folio="through folio 104r",
    ),
    "990001084150205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258814", "אברהם בן שלמה בן יונה אלכלפי",
            (
                "https://teman.org.il/content/11972",
            ),
            identifier=("P973", "https://teman.org.il/content/11972"),
        ),
        "700:1", "אלכלפי, אברהם בן שלמה בן יונה",
        "ecc8288f0626007afd18ebc32fc778845dc7282b7d02f31c5a3f775d0c821e6c",
    ),
    "990001136420205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_JOSEPH_SHARAFAH,
        "700:1", "יוסף בן שלמה",
        "5e37041d394e56a4a3f1952c7117626b9766734e1bb702470fd4d47d778b4c66",
    ),
    "990001136560205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259364", "סעדיה בן נחום מאלכלה",
            (
                "https://teman.org.il/content/12529",
            ),
            identifier=("P973", "https://teman.org.il/content/12529"),
        ),
        "700:1", "סעדיה בן נחום",
        "b5f3f9dbb3ea8353e42b6282254b02c57395cee14d8f05e13af7f72f3f65bebc",
    ),
    "990001219500205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259325", "זכריה בן סעדיה מעלם",
            (
                "https://teman.org.il/content/12279",
                "https://searcharchives.bl.uk/catalog/032-003314038",
            ),
            identifier=("P973", "https://teman.org.il/content/12279"),
        ),
        "700:1", "מעלם, זכריה בן סעדיה",
        "3efc663eabe2b63ad14f903f390896850926a3744090c27fe657a8f9c53f64ee",
    ),
    "990001239240205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259357", "דוד בן סעדיה בן דוד אלטיאני",
            (
                "https://teman.org.il/content/11624",
                "https://searcharchives.bl.uk/catalog/032-003314139",
            ),
            identifier=("P973", "https://teman.org.il/content/11624"),
        ),
        "700:1", "אלטיאני, דוד בן סעדיה",
        "c828aa48632996e5146571d9c67988bef6c4ae09eaa411c3a93c70205e2e8e89",
    ),
    "990001285000205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259340", "נתן נטע בן משה נפתלי הירש המרשלק",
            (
                "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend",
                "https://cja.huji.ac.il/browser.php?id=191010&mode=alone",
                "https://ilanot.org/diagrams/diagram-00518",
            ),
            identifier=("P973", "https://cja.huji.ac.il/?f=maker_name&id=660&mode=treefriend"),
        ),
        "700:1", "המרשלק, נתן נטע בן משה נפתלי הירש",
        "287e1e2b0e28c45f5669751b50f62474dd8a410120b0e8a9888fd0f72d84edd6",
    ),
    "990001313900205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259339", "זכריה בן יוסף אלכלפי",
            (
                "https://teman.org.il/content/11973",
            ),
            identifier=("P973", "https://teman.org.il/content/11973"),
        ),
        "700:1", "אלכלפי, זכריה בן יוסף",
        "66d378fac0e63335c5ed044993e2d6691e2daaa67497acc45aebc1eccdd6245f",
    ),
    "990001350000205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259367", "תם בן גדליה אבן יחיא",
            (
                "https://www.jewishencyclopedia.com/articles/15051-yahya#anchor25",
                "https://files.ybz.org.il/periodicals/peamim/26/Article_26.3.pdf",
            ),
            identifier=("P973", "https://www.jewishencyclopedia.com/articles/15051-yahya#anchor25"),
        ),
        "700:1", "יחיא, תם בן גדליה אבן",
        "c2d8d4c8104167dd54026560f84040d56640f7eebe03e0a1e0a4afa853ef9d59",
    ),
    "990001361140205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258898", "סעדיה בן מעודד בן סעדיה בן יוסף",
            (
                "https://teman.org.il/content/12524",
            ),
            identifier=("P973", "https://teman.org.il/content/12524"),
        ),
        "700:1", "סעיד בן מעודד",
        "d9fce5330a4abb9b030ded2f0882c769811829a6ca911b56feec0f81c8b9fe2c",
    ),
    "990001689720205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259359", "יהודה בן אברהם בן יוסף בן זכריה",
            (
                "https://teman.org.il/content/11633",
            ),
            identifier=("P973", "https://teman.org.il/content/11633"),
        ),
        "700:1", "יהודה בן אברהם בן יוסף בן זכריה",
        "75a7fb2817aef6c00e2314554685745b84848b7bf4492acd841fae31328d0d49",
    ),
    "990001762200205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259384", "דניאל בן משה בן ישעיה פירוז",
            (
                "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=12",
                "https://searcharchives.bl.uk/catalog/032-003372139",
                "https://sammlungen.ub.uni-frankfurt.de/cm/periodical/pageview/2847305",
            ),
            identifier=("P973", "https://www.orientalstudies.ru/rus/images/pdf/belkina_2024.pdf#page=12"),
        ),
        "700:1", "פירוז, דניאל בן משה בן ישעיה",
        "2ed56d9c08f9c8bfb804b7a2ec1608f0d126134279dc852f3956a493023694ac",
    ),
    "990001860230205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259385", "יהודה אריה בן יעקב פוג׳טי",
            (
                "https://sammlungen.ub.uni-frankfurt.de/freimann/content/pageview/6117264",
                "https://explore.library.leeds.ac.uk/multimedia/16519/MS_164_Roth.pdf",
            ),
            identifier=("P973", "https://sammlungen.ub.uni-frankfurt.de/freimann/content/pageview/6117264"),
        ),
        "700:1", "פוג'טי, יהודה אריה בן יעקב",
        "f97b0d77173f544c64ee53a5259d859206975f51ca1f535d689f10f12bea5af8",
        tag="500",
    ),
    "990001901520205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259358", "יוסף בן שלום בן דוד בן שלמה אלאחמר",
            (
                "https://teman.org.il/content/10987",
            ),
            identifier=("P973", "https://teman.org.il/content/10987"),
        ),
        "700:1", "אלאחמר, יוסף בן שלום בן דוד",
        "eb643d8a3f199e202d545a5982128bacd479fc21e31e89c1d47b79ea5d6bfe25",
    ),
    "990035802410205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142259305", "ישראל בן יוסף בן ישראל משתא",
            (
                "https://teman.org.il/content/13136",
            ),
            identifier=("P973", "https://teman.org.il/content/13136"),
        ),
        "700:1", "ישראל בן יוסף",
        "433b08c7c0f30f53832d8a75070976c68cc36a7804b9e093433ec5bbb2696899",
    ),
    "990000448060205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_QAYSI,
        "700:1", "אלקיסי, סעדיה בן שלמה",
        "e8d59c11ac19b838569523d1f5d6541100c32b83ca45e6e568dae385f9fe2787",
    ),
    "990000550960205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_UZANI,
        "700:1", "זכריה בן יוסף בן סעדיה",
        "db680251c533e67c69b46d573514f5a0b2bd5340c677702fff21653fd35f6a63",
    ),
    "990000554010205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_UZANI,
        "700:1", "זכריה בן יוסף",
        "21c6b2cb4819cc9535785b4121f88aa8d870065f79360d5a4d2124da9724fb7b",
    ),
    "990000598840205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_TAWILI,
        "700:1", "אלטוילי, שלום בן יהודה",
        "ed3730a1feb9ec1ff627e534bde04d32258b49a7678ed8267b9bd1433a0671a1",
    ),
    "990000898590205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_TAWILI,
        "700:1", "אלטוילי, שלום בן יהודה",
        "4dc5b3003108a9f9e21893041921d2ab978ef7d44764199618c4ab1222defa8a",
    ),
    "990000914680205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258890", "מימון בן משה בן סעדיה בן מעודד אלמוסאי",
            (
                "https://teman.org.il/content/12140",
            ),
            identifier=("P973", "https://teman.org.il/content/12140"),
        ),
        "700:1", "אלמוסאי, מימון בן משה",
        "6ea008c8559b5f3c22dec68a23ddf28eca31d16ede1b36b3984623cd2e6e23b4",
    ),
    "990000990320205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258892", "אהרן בן שלמה בן חלפון בן אביגד אלטוילי",
            (
                "https://teman.org.il/content/11611",
            ),
            identifier=("P973", "https://teman.org.il/content/11611"),
        ),
        "700:1", "אלטוילי, אהרן בן שלמה",
        "fad77b9ea230d524d3329af8d88da2583cb60e967480a56c82a32abd905cf9a0",
    ),
    "990001022890205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258894", "זכריה בן אביגד בן אברהם אלטוילי",
            (
                "https://teman.org.il/content/11613",
            ),
            identifier=("P973", "https://teman.org.il/content/11613"),
        ),
        "700:1", "אלטוילי, זכרי אבן אביגד בן אברהם",
        "3e7fef0ce3d2e85f31fdc59c96de8b03338efbc1095832aba499bc99dc793282",
        folio="Genesis–Exodus (first volume)",
    ),
    "990001121850205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_UZANI,
        "700:1", "זכריה בן יוסף",
        "af3049962f1eff7774466258eb1344c48b717d86d2a4d1a87f4b7bfb76d620bf",
    ),
    "990001137910205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_UZANI,
        "700:1", "עזאני, זכריה בן יוסף",
        "7d04ee11d2d169496cabf0748a5625cc63069143c52f21c45623469a3807165a",
    ),
    "990001153290205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_SHARARA,
        "700:1", "שרארה, עמרם בן שלום בן יתר",
        "c33fcfcaa6c2e87f9f5d3d2b92613d3fe3a635cd9a18dc98bbb98f3f384a45f7",
    ),
    "990001162290205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_QAYSI,
        "700:1", "אלקיסי, סעדיה בן שלמה בן סעדיה",
        "0e67e74a19f4c7f8ac72fdde5507fefde1be079e2ef2a4ad37b9b53dcf1f65ed",
    ),
    "990001191160205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_SHARARA,
        "700:1", "שרארה, עמרם בן שלום בן יתר",
        "39c4fafdc3d346805ca973ace837f1c0413d2e9eab35e2d9612c4f07aa561320",
    ),
    "990001192190205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258891", "יעקב בן יצחק בן עודד אלעדוי",
            (
                "https://teman.org.il/content/12568",
            ),
            identifier=("P973", "https://teman.org.il/content/12568"),
        ),
        "700:1", "אלעדוי, יעקב בן יצחק בן עודד",
        "db365b2bee5b7e10f8061629536a87d858a3e7b087e42c92efb7752f4d3271e1",
    ),
    "990001205430205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258895", "אברהם בן יוסף בן סעדיה ונה",
            (
                "https://teman.org.il/content/11334",
            ),
            identifier=("P973", "https://teman.org.il/content/11334"),
        ),
        "700:1", "ונה, אברהם בן יוסף בן סעדיה",
        "b40df620820d62f22a9f5c8b95a937b069d3958c567508e20acb03bf3e0bc3c5",
    ),
    "990001209890205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258880", "זכריה בן אברהם בן זכריה אלמסורי",
            (
                "https://teman.org.il/content/12236",
            ),
            identifier=("P973", "https://teman.org.il/content/12236"),
        ),
        "700:1", "אלמסורי, זכריה בן אברהם בן זכריה",
        "6918a2bdf7d865a304f55b2d6139dd97394fdf25050e6e2d778de80a9122834d",
    ),
    "990001239200205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_KALUFI,
        "700:1", "כלופי, זכריה בן נתן בן יהודה",
        "f9ab2502a1c35fea47290a75e075ad7193185d8177efa17534eae56441ce8028",
    ),
    "990001324600205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_SAADIA_SANHANI,
        "700:1", "סעדיה בן מעודד בן סעדיה",
        "e6d425adc56baa80436f1b2d6c9dcdc9b7dc6418f50d01baf1296a03e00af46b",
    ),
    "990001338390205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_MAABARI,
        "700:1", "אלמעברי, סעדיה בן מעודד",
        "a2464a6ffe4cc1a6b6e5cc1b44b09e496752581bfe4a194a6a9a751ad353e720",
    ),
    "990001689710205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258814", "אברהם בן שלמה בן יונה אלכלפי",
            (
                "https://teman.org.il/content/11972",
            ),
            identifier=("P973", "https://teman.org.il/content/11972"),
        ),
        "700:1", "אלכלפי, אברהם בן שלמה בן יונה",
        "39a955050cd191fc6e0fca6b863bfcbaf5ca81220673173e658b3af0b9a5d63b",
    ),
    "990001689730205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258887", "יוסף בן שלום בן סעדיה אלשיחאני",
            (
                "https://teman.org.il/content/13153",
            ),
            identifier=("P973", "https://teman.org.il/content/13153"),
        ),
        "700:1", "אלשיחאני, יוסף בן שלום",
        "37789e6d8b0c072ef3ac14d0a35a04e385f3efea6dae3d808b822a4140022c41",
    ),
    "990001689780205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258885", "זכריה בן דוד בן זכריה אלפהים",
            (
                "https://teman.org.il/content/12778",
            ),
            identifier=("P973", "https://teman.org.il/content/12778"),
        ),
        "700:1", "אלפהים, זכריה בן דוד בן זכריה",
        "ac69ba3cb562d58a44cd9f71204f929c60c5e70ef6f02bc4e87383d7bca0f10e",
    ),
    "990001797580205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q6760960", "שלמה חראזי",
            (
                "https://teman.org.il/content/11570",
            ),
            sitelink=("hewiki", "שלמה חראזי"),
        ),
        "700:1", "אלחראזי, שלמה בן סעדיה",
        "f134ef299162e444647a1349e0001974a6f128b66280c409d6c9d182aafb9e0f",
        folio="Exodus section",
    ),
    "990001799180205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258816", "שלמה בן ישראל בן יוסף בן ישראל",
            (
                "https://teman.org.il/content/13241",
            ),
            identifier=("P973", "https://teman.org.il/content/13241"),
        ),
        "700:1", "שלמה בן ישראל בן יוסף",
        "d95ac204e36dde231db84b927836ce3c8b9a076806f9bc22bb2c4e530e31ae00",
    ),
    "990001827110205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_MALIAH,
        "700:1", "אלמליח, יהודה בן אברהם בן נחום",
        "78c31a49be3a4f2f2159aeb94df5474f0332295fefa558a21fae5363cfc0774b",
    ),
    "990001827620205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_SAADIA_SANHANI,
        "700:1", "סעדיה בן מעודד בן סעדיה",
        "3bdae8cbf3d7c34e519063c79d16e66b5e41d86511f8f7cb60008ad2706927c4",
    ),
    "990001875220205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_QAYSI,
        "700:1", "אלקיסי, סעדיה בן שלמה",
        "c3a3e47d6df0ad3bda7058f686215dbae78f36f372d62311f0fc8d99293c8953",
    ),
    "990001878160205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_KALUFI,
        "700:1", "כלופי, זכריה בן נתן בן יהודה",
        "b020596d7d70c46175bc24474efd453a162dc7bc58413a447cfa4870009467be",
    ),
    "990001921540205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_MAABARI,
        "700:1", "אלמעברי, סעדיה בן מעודד",
        "e126389ce1d57a3071d3266d7fe7bdb7020514c0dd485885c92ac0050d8896e8",
    ),
    "990001952110205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258882", "יהודה בן דוד בן שלמה אלפתיחי",
            (
                "https://teman.org.il/content/12800",
            ),
            identifier=("P973", "https://teman.org.il/content/12800"),
        ),
        "700:1", "אלפתיחי, יהודה בן דוד בן שלמה",
        "e4153c9e5b9d078efee111b929dbcbc30a92483106fa499f34970d8c40532b65",
    ),
    "990001979390205171": ReviewedScribeNote(
        _REVIEWED_SCRIBE_MALIAH,
        "700:1", "אלמליח, יהודה בן אברהם בן נחום",
        "ee905d401c5c5af958935987ae197f54ac104bc8ac3b7455607bbdb59494316a",
    ),
    "990000564000205171": ReviewedScribeNote(
        _PINHAS_BEN_GAD,
        "700:1", "פינחס בן גד",
        "464481915e297b4e443c6d9246f9ccf95e7bd54ecbf10a33b395ef07a16144ea",
    ),
    "990000572680205171": ReviewedScribeNote(
        _JACOB_PHILIP,
        "700:1", "פיליפ, יעקב חיים",
        "7c867c334100afb9bb1df7b84cfb80b940caf28ada3403b23e25b279654adf7b",
        tag="500",
        folio="copy excluding the duplicate leaves on folios 54r–157v",
    ),
    "990000572690205171": ReviewedScribeNote(
        _JACOB_PHILIP,
        "700:1", "פיליפ, יעקב חיים",
        "2b1a336da69b16c178cff8e6faf1de8375fe7df48403280850c0e490725be346",
    ),
    "990000591080205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258537", "יעקב בן נחום מטישוויטץ",
            (
                "https://id.loc.gov/authorities/names/nb2007025985",
                "https://viaf.org/viaf/281074688/",
            ),
            identifier=("P8189", "987007310488605171"),
        ),
        "700:1", "יעקב בן נחום",
        "e4cf15703b7512c48faefd331e87d29efb1a69d7650fbc475e153595c23d55b4",
        former_heading="יעקב בן נחום מטישוויטץ",
        corrected_heading="יעקב בן נחום מטישוויטץ",
    ),
    "990000677600205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258539", "אליעזר ראובן חי מפאנו",
            (
                "https://www.nli.org.il/he/a-topic/987007329659105171",
                "https://viaf.org/viaf/75149616563303750267/",
            ),
            identifier=("P8189", "987007329659105171"),
        ),
        "700:1", "פנו, אליעזר ראובן חי",
        "091cc5bc7a143910110b4f0b6f63ae08f0843b41d1b205f3ecc38a2b196d471b",
    ),
    "990000790230205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q124636964", None,
            (
                "https://digitalcollections.jtsa.edu/islandora/object/jts%3A41245",
                "https://id.loc.gov/authorities/names/nb2009006055",
            ),
            identifier=("P214", "83527758"),
        ),
        "700:1", "יצחק בן יעקב",
        "b9e76b78ebdefe73b721f0b25658aec47d5fe9ddcc61c57491d693b7148cf11f",
        former_heading="יצחק בן יעקב מן הלויים",
        corrected_heading="יצחק בן יעקב מן הלויים",
        folio="main text, before the section copied by Hayyim Volterra",
    ),
    "990000818510205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258566", "משה בן אליהו הלוי הירושלמי",
            (
                "https://www.nli.org.il/en/manuscripts/NNL_ALEPH990000801730205171/NLI",
                "https://id.loc.gov/authorities/names/no2005078366",
                "https://viaf.org/viaf/2203699/",
            ),
            identifier=("P8189", "987007577585605171"),
        ),
        "700:1", "משה בן אליהו",
        "0f9696c9b77b8d5427f64dbd5e7b0c1d7bf9ba7a960d7e461b22b062d6d65811",
        former_heading="משה בן אליהו הלוי הירושלמי",
        corrected_heading="משה בן אליהו הלוי הירושלמי",
    ),
    "990000893610205171": ReviewedScribeNote(
        _ISRAEL_TUVIA_LINGI,
        "700:1", "לינגי, ישראל שלמה בן טוביה",
        "50eeea24b82dda33338acbb8cf3100b4c8da96cdf8b8e663c29aaca3688f194e",
    ),
    "990001010130205171": ReviewedScribeNote(
        _ISRAEL_TUVIA_LINGI,
        "700:1", "לינגי, ישראל שלמה בן טוביה",
        "5a4638c2f2a6c8849a28910d87b6b0c514072298a833a96903f5322c0b86213d",
    ),
    "990001064960205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258567", "שלמה אליהו אבן צור",
            (
                "https://www.nli.org.il/he/a-topic/987009538784505171",
                "https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001006730205171&scope=KTIV&vid=KTIV",
                "https://viaf.org/viaf/14161451567664102341/",
            ),
            identifier=("P8189", "987009538784505171"),
        ),
        "700:1", "אבן צור, שלמה אליהו בן יעקב",
        "44f6535b98b08850a1d347705d1ed18f571312b6642f279fa10987cd97f4458a",
        folio="later additions attributed to him from folio 55r",
    ),
    "990001164280205171": ReviewedScribeNote(
        _PINHAS_BEN_GAD,
        "700:1", "כהן, פינחס בן גד",
        "6477508fefcc71eac1e792809ed72891c10908885ad161fa875844d292d70973",
    ),
    "990001219520205171": ReviewedScribeNote(
        _PINHAS_BEN_GAD,
        "700:1", "פינחס בן גד",
        "23340f65b8b2b535b8d076fabfd5713e88d4c79886e05bc2f4dfa21cfb8fa222",
    ),
    "990001349760205171": ReviewedScribeNote(
        _PINHAS_BEN_GAD,
        "700:1", "פינחס בן גד",
        "0ce7e0ae2f0c586d9b1b17ddd2f65c42fd0e2309f3bd5af74a7c85278f9d8206",
    ),
    "990001349770205171": ReviewedScribeNote(
        _PINHAS_BEN_GAD,
        "700:1", "פינחס בן גד",
        "44384732d3f5c296da6e10754c992935a3aca3548041c4fe05b0730dfdc25d94",
        former_heading="פינחס בן גד הכהן",
    ),
    "990001792130205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258530", "מיכאל יהודה לאון",
            (
                "https://cja.huji.ac.il/browser.php?id=37632&mode=set",
                "https://thejewishmuseum.org/wp-content/uploads/2025/07/Esther_Object_Labels_FINAL_rev-1.pdf",
                "https://viaf.org/viaf/299520564/",
            ),
            identifier=("P8189", "987007311072405171"),
        ),
        "700:1", "מיכאל יהודה",
        "60179cd965fd6555f936b5727761a4c2e3d9a5d08f65a211ee6a8b4e8cf82314",
    ),
    "990000594240205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q5423435", "עזרא מפאנו",
            (
                "https://www.nli.org.il/en/manuscripts/NNL_ALEPH990000594240205171/NLI",
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990000594240205171&vid=KTIV",
                "https://cja.huji.ac.il/sch/browser.php?id=21005&mode=set",
                "https://id.loc.gov/authorities/names/nb2008014391",
            ),
            identifier=("P214", "2001250"),
        ),
        "700:1", "פאנו, עזרא בן יצחק",
        "40c49c268db38122b9316ab5fca13706f1de508271805981d8165478d0cb084c",
        tag="500",
        former_heading="פנו, עזרא בן יצחק",
        folio="Part II marginal notes and corrections",
    ),
    "990001067860205171": ReviewedScribeNote(
        _BARAKA_CAIRO,
        "700:1", "ברכה, יוסף בן רצון בן שמואל בן אהרן",
        "a6ef1c935db06f0975286472342ecb22c46c63d00bb98df1f6a376ab4ef097bd",
        tag="500",
    ),
    "990001067870205171": ReviewedScribeNote(
        _BARAKA_CAIRO,
        "700:1", "ברכה, יוסף בן רצון בן שמואל בן אהרן",
        "80bd37d2dc19fa549da92192589e3c414271995f814d93ab2a46adf639217cb9",
    ),
    "990001091450205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258381", "אגוסטינו גרימני",
            (
                "https://digi.vatlib.it/view/MSS_Vat.ebr.434",
                "https://www.nli.org.il/media/26037/vaticanhebmss.pdf",
                "https://viaf.org/viaf/sourceID/J9U%7C987007601761505171",
            ),
            identifier=("P8189", "987007601761505171"),
        ),
        "700:2", "Grimani, Agostino",
        "340898120c5bf1728dc6b3d9363f906915574ed573ddf1f3b0d725dabdad5c36",
    ),
    "990001134600205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q26954684", "אפרים פיגו",
            (
                "https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001134600205171&scope=KTIV&vid=KTIV",
                "https://tablet.otzar.org/he/book/book.php?book=619068&pagenum=133",
            ),
            sitelink=("vecwiki", "Efrem Piccio"),
        ),
        "700:1", "פיגו, אפרים",
        "35e8b6cb13fccca330e64dc50bbecad06dee1d312a954737b52f8db1ab96e163",
    ),
    "990001291720205171": ReviewedScribeNote(
        _BARAKA_CAIRO,
        "700:1", "ברכה, יוסף בן רצון בן שמואל בן אהרן",
        "d455cbb85d3429aef68f091bd590da0d390f4ff81cee148627319f4412e27642",
    ),
    "990001376840205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141748501", "יום טוב ליפמן חזן בן וייבלמן",
            (
                "https://sammlungen.ub.uni-frankfurt.de/mshebr/content/titleinfo/1931458",
                "https://sammlungen.ub.uni-frankfurt.de/download/webcache/2000/1931489",
            ),
        ),
        "700:1", "יום טוב ליפמן חזן בן וייבלמן",
        "89f6fd37a858116fe251fc8d06e204c27f5623814c611937d685089cc99afc3e",
    ),
    "990001878530205171": ReviewedScribeNote(
        _HOTER_1650,
        "700:2", "חטר בן דוד",
        "d977cf30b5cec0e14e22d78c88d7f7862a7ea4dba5063196746062d5c3fc49b1",
        former_heading="שלום בן יוסף הקרחי",
        folio="section ending with the 1650 colophon",
    ),
    "990024953100205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q79118397", None,
            (
                "https://etshaimmanuscripts.nl/items/eh-47-e-33/",
            ),
            identifier=("P244", "nr98002003"),
        ),
        "700:1", "בנימין שניאור",
        "2888cb179e3c42fc77ae1e075bbe19e41cebd2e9416c0f6044e6832430f576cd",
    ),
    "990025656430205171": ReviewedScribeNote(
        _HOTER_1650,
        "700:1", "חטר בן דוד",
        "fa68469a485a1d4f506ffc70a3190fef4cdf5f20ac74d811fadf63aa3758291f",
    ),
    "990036375040205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q142258400", "יהודה מכבי",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990036375040205171&vid=MANUSCRIPTS",
                "https://data.bibliotheken.nl/id/thes/p408625058",
                "https://viaf.org/viaf/sourceID/J9U%7C987007401772005171",
            ),
            identifier=("P8189", "987007401772005171"),
        ),
        "700:1", "מכבי, יהודה",
        "91523c0ce83703557677c338acaec69d2804374e71dd579c3fdd004ac1bbc8dd",
    ),
    "990000702460205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q132798628", None,
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990000702460205171&vid=KTIV",
                "https://catalog.digital-scriptorium.org/wiki/Item:Q75810",
            ),
            identifier=("P13311", "Q75810"),
        ),
        "700:1", "מיליאב, גד בן עמנואל",
        "360ac6e9c4a88d7e91905fa300af2b944e6c41f213b8ee7811d9a488594530f0", corrected_heading="עמנואל בר גד דמילייאב",
    ),
    "990000776020205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141175593", "ישועה אזולאי",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990000776020205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1059",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1059"),
        ),
        "700:1", "אזולאי, ישועה",
        "ffa3848b65478b2c48f87a18cbf23cf5fa642dda5e9d0a80d9d444a204524ac2", tag="500",
    ),
    "990000825080205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141953248", "יוסף בן שמואל אשנבי",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990000825080205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1189",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1189"),
        ),
        "700:1", "אשנבי, יוסף בן שמואל",
        "d2a66eacbe70d4442a791d76b3d418679bfd8c2d5701771354c106685ab99483", tag="561",
    ),
    "990000856010205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141748471", "שלמה נחום בן שבתי",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990000856010205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1160",
                "https://bidspirit-images.global.ssl.fastly.net/kedem/auctionDocs/531/catalog/100_-_.pdf#page=53",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1160"),
            wrong_ids=(("P8189", "987007258452905171"),),
        ),
        "700:1", "שלמה נחום בן שבתי",
        "dcefd3301fa9ae7746e27c3f066349c081d9210af35c346f9507fdb7025a27b0",
    ),
    "990000880550205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141748463", "שלמה סידיש",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990000880550205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1093",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1093"),
            wrong_ids=(("P8189", "987007294122705171"),),
        ),
        "700:1", "סידיש, שלמה",
        "a22b7d1c787924007588fc5847236ae05d29cab28c5cc34bea24fd5c7ad5dd9e",
    ),
    "990001089360205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141953226", "יוסף יצחק עמר — 990001089360205171",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001089360205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1110",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1110"),
            wrong_ids=(("P8189", "987007270155705171"),),
        ),
        "700:1", "עמר, יוסף יצחק",
        "8de929327ca936453e17a59083cd517542f2b40f03f0f62996b35d7276debcf8", tag="500",
    ),
    "990001205840205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141748479", "יוסף בן סעדיה בן יוסף בן דוד אבהר",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001205840205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1136",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1136"),
            wrong_ids=(("P8189", "987007300794605171"),),
        ),
        "700:1", "אבהר, יוסף בן סעדיה בן יוסף בן דוד",
        "90026d5711de0febc9610f36b6fee965e91db2fd81dbeab778669e5584c2822d",
    ),
    "990001254240205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141953251", "טוביה בן חיים יצחק גבאי",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001254240205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1205",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1205"),
        ),
        "700:1", "גבאי, טוביה בן חיים יצחק",
        "3520cb99d2cabbf8f88617fe2fc772769ff2fcc5ffe7fbbf517516a4e0ae89a8",
    ),
    "990001830400205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141953253", "יהודה בן יצחק דדיניאה",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001830400205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1213",
                "https://live-kedem.circuit.auction/he/node/188519",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1213"),
            wrong_ids=(("P8189", "987007270165205171"),),
        ),
        "700:1", "דדיניאה, יהודה בן יצחק",
        "21f9341f45405c14fd94d275008910ffdb0317234681608036f1b0b34cb59657", folio="final unnumbered replacement leaf",
    ),
    "990001856120205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141953249", "נתן יהושע בן שלמה בידרידוש",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001856120205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1192",
                "https://explore.library.leeds.ac.uk/multimedia/16519/MS_164_Roth.pdf",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1192"),
        ),
        "700:1", "בידרידוש, נתן יהושע בן שלמה",
        "a1e274629a5fc7cdc76c4130720d6fc2f5feed22a858590d4094024f1a8ba559",
    ),
    "990001878130205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141748477", "יוסף בן יונה אלפתיחי",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990001878130205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1180",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1180"),
            wrong_ids=(("P8189", "987007415787305171"),),
        ),
        "700:1", "אלפתיחי, יוסף בן יונה",
        "01daf59096d77c8b293669cc4501cd5e2a50f4d6d3bafc3922a26a807236ca72",
    ),
    "990035044030205171": ReviewedScribeNote(
        ReviewedScribePerson(
            "Q141953262", "שמואל בן אברהם מונדולפו",
            (
                "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage"
                "?docId=PNX_MANUSCRIPTS990035044030205171&vid=KTIV",
                "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1056",
                "https://makor.primo.exlibrisgroup.com/discovery/fulldisplay"
                "?docid=alma990001928240107706&context=L&vid=01JTS_INST:01JTS&lang=en",
            ),
            identifier=("P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q1056"),
            wrong_ids=(("P8189", "987007261158205171"),),
        ),
        "700:3", "מונדולפו, שמואל בן אברהם",
        "cc9f6436ce8949dfe3b777b2bdcdcd10a2c2aa7858ebefdbc524f62012f930a3", tag="500",
    ),
    "990000561550205171": ReviewedScribeNote(
        _QATI, "700:1", "אלקטיעי, משה בן סעדיה",
        "7b8f460deeac6f56020bdb1a0d6b754a9f7422370fc6673cc992fe9cb834cfb2",
    ),
    "990000566640205171": ReviewedScribeNote(
        _QATI, "700:1", "אלקטיעי, משה בן סעדיה",
        "8185bb0c81b322a523c7e9d56abe94584521e4aaa8dc30e09dbb9f112dd60c7c",
    ),
    "990000593730205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאג', חיים בן שלום בן דוד בן יצחק",
        "b4f1ed7f37d56defc1cd4f72aeed6ef326dae5aabe2c6b38174d4f6bd8e0074a",
    ),
    "990000602640205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאגי, חיים בן שלום",
        "c613d0f6e142a3eaf327a65262963879d1f4a8d9d9612d410f921e46c7216653",
    ),
    "990000610900205171": ReviewedScribeNote(
        _MEHLER, "700:1", "מילר, יהודה בן שמואל רויטלינגן",
        "5bd1780036dbf239f4da2e422a6ba48c8d41164eeed15de55043f83df4655cd2",
    ),
    "990000612200205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאגי, חיים בן שלום",
        "8efe2fd70ac6f1e6b5a04dd790ad55625801688cf7e966ef1875d1985df70990",
    ),
    "990001088900205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאג', חיים בן שלום בן דוד בן יצחק",
        "ba89fb7b2703b9502a97a2655b9177e8dd92f0950800e56afd1cfb372b49c9e4",
    ),
    "990001122020205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאגי, חיים בן שלום",
        "af3599b1ad82bc53fee2f049072b480321ede9012af2716470f388cbf36ce5e1",
    ),
    "990001189730205171": ReviewedScribeNote(
        _QATI, "700:1", "אלקטיעי, משה בן סעדיה",
        "1b21cf12e43d12b6f74fc6206125b321a252ef340226b28119b783f8eb3440ab",
    ),
    "990001211120205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאגי, חיים בן שלום",
        "387ff17540fe65e315cdd19f632b3f529d00092de25b08185c15d7a7cb21c740",
    ),
    "990001219490205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאג', חיים בן שלום בן דוד בן יצחק",
        "e497e158adf35a47e3dd12eed7ca8225c004a46990dd99c89a176f5ff2f30a2e",
    ),
    "990001273580205171": ReviewedScribeNote(
        _HAGAGI, "700:1", "אלחגאגי, חיים בן שלום",
        "d51ea54692d31c20503c8c33f9d3e2ea39352266df25f5cd30363bf4535090ab",
    ),
    "990001349520205171": ReviewedScribeNote(
        _QATI, "700:2", "אלקטיעי, משה בן סעדיה",
        "215ba3da2f44b7b1a5599c3e4a91d861ff3ff568eff8a9fb82beb7001d6508e9",
    ),
    "990001366470205171": ReviewedScribeNote(
        _ELADWI, "700:1", "אלעדוי, יוסף בן עמרם בן עודד",
        "ab9f533a4e28b6141327797baa2668b2f12fd99445d9a5bff5122b2f641d6246", former_heading="יוסף בן עמרם בן עודד בן זכריה בן יהודה",
    ),
    "990001758470205171": ReviewedScribeNote(
        _MEHLER, "700:1", "מילר, יהודה בן שמואל רויטלינגן",
        "bd331d2b7ea43262fe66947c65fe837d540a0455f121bfe195a6e0d1e6fe37a6",
    ),
    "990001772940205171": ReviewedScribeNote(
        _QATI, "700:1", "אלקטיעי, משה בן סעדיה",
        "5c88a2026ca773f9ba8de7d3f9963ad7d9433b69de964169cecfbb18a976a4d9",
    ),
    "990001827870205171": ReviewedScribeNote(
        _ELADWI, "700:1", "אלעדוי, יוסף בן עמרם בן עודד",
        "c5f3fd1b0cdf4f64808129af6f8ea7e8e257db4286c1769a2e6f151c30f957d3",
    ),
    "990001827880205171": ReviewedScribeNote(
        _ELADWI, "700:1", "אלעדוי, יוסף בן עמרם בן עודד",
        "7d0211799546474f659e7c37206fe724b5fe6a1a4f7be9a3b72dd7ff92c62605",
    ),
    "990001845770205171": ReviewedScribeNote(
        _QATI, "700:1", "אלקטיעי, משה בן סעדיה",
        "c72076b07841f7d62feba23e330f1ea00339e2c57613fd46aed58023bb7b55b3",
    ),
    "990001863380205171": ReviewedScribeNote(
        _QATI, "700:1", "אלקטיעי, משה בן סעדיה",
        "2b9e79e4b3de77e10907cc159ce86b8aa280dd86dcdb283e4a391bc0ddfc28ee",
    ),
    "990001901770205171": ReviewedScribeNote(
        _ELADWI, "700:1", "אלעדוי, יוסף בן עמרם בן עודד",
        "0cdf14b6e516fcf195716136e64ddef0202a8e7bdce898c4bc1220e775a0153a",
    ),
    "990038692590205171": ReviewedScribeNote(
        _BASHIRI, "700:3", "בשירי, יחיא",
        "3405ab7b7d492400d0c29302a7ef437258103637d65cdd336a24b071edf9f7d5", former_heading="בשירי, יחיא הלוי",
    ),
}


def reviewed_scribe_source(
    record: Mapping, cn: str, occurrence: str, heading: str,
) -> ReviewedScribeNote | None:
    proof = _EXTRA_SCRIBE_NOTES.get(cn)
    if not proof or proof.occurrence != occurrence or proof.heading != heading:
        return None
    notes = _note_digests(record, proof.tag)
    return proof if notes and sha256(" ".join(notes).encode()).hexdigest() == proof.digest else None


def reviewed_scribe_note(
    record: Mapping, cn: str, occurrence: str, heading: str, candidate: Mapping,
) -> ReviewedScribeNote | None:
    proof = reviewed_scribe_source(record, cn, occurrence, heading)
    return proof if proof and proof.person.matches(candidate) else None


# The review binds the MARC occurrence to the exact text of field 957.
_COLOPHONS = {
    "990000532010205171": ("700:1", "ed8220706cddd0bcfb9fa71985d51af04d5dbbe9f9b6776761d642943907089e"),
    "990000574480205171": ("700:1", "ff0735ad866eef72958262284c40fb375c3ee05ecf65c43f4acd9cc1e400f388"),
    "990000605740205171": ("700:1", "ae62aa87166eeaa4088941329a2281d80d57b5a83d9f4dad0880465762696969"),
    "990000810710205171": ("700:1", "ee4dfc0d69bcc0a3184a0b05393f63f44faeb3adf29321b2392a40276b1d9b98"),
    "990001065200205171": ("700:1", "f78832d5fd95a03f6de6e330014434ae9e37f6bd86d2f0a2cb679910e94f830a"),
    "990001087660205171": ("700:1", "0f495b7d210faf1f5e8328e800e58de80dc91bd277eb872292706c2b44a76517"),
    "990001089510205171": ("700:1", "badcb364e70ffa3f046e5ef9e4372bb40fa362fbe13e55ed7996d174dfbc3a15"),
    "990001140550205171": ("700:1", "c9fc1c81950c05ab0c2afb5f687df59f62c1bba800f52bbb77cff88d2aa047d1"),
    "990001236420205171": ("700:1", "cd7ef45473abffe8298246efc24b1f2989b13e43a89e09393fbc5766fbd5cdac"),
    "990001269950205171": ("700:1", "4f050e5bf80857d737ca15038d7a6410041f14a50a5d0fe3c5640c93f6e04b2e"),
    "990001270690205171": ("700:1", "568a9cc4dbbe45d970ba1273a5aa1e790072397615b78b2ceb11db604386e8a1"),
    "990001371830205171": ("700:2", "3c1d85f0308c99cfe0b0be4b78435b40312547f6239f577987aba9431e97a557"),
    "990001372150205171": ("700:2", "4a526a6d82270fa79508cd7bc26e174f6d6548c63c9d18a3b484f9c1bb3b776c"),
    "990001465880205171": ("700:1", "b6634155151a9baef7539c230f2958bbece8a65abfd2ac0e82cd7204ecf2a770"),
    "990001499320205171": ("700:1", "8202862604f432065a65e111a35a2e06a786e4f72d018c28d562cfa54dbffabd"),
    "990001679010205171": ("700:1", "2d3d24ec40df864009fc68c38ce59cdbab37e9571766047e022c1f4c5d03cef3"),
    "990001793780205171": ("700:1", "ee6610af7dabc40c5a01acf65cbefecb8e6f44b3d55c8ee84941cb01b62b1f13"),
    "990001831110205171": ("700:1", "a07d666e141dbf5ea3520986abbf73963c2a39ffed07621455242f443a377cfa"),
    "990001831120205171": ("700:1", "7360687f94eeff5b9d809c0f6b07adcb6104c2a61cabaf11f3adf12b2dfa1427"),
    "990001848360205171": ("700:1", "329267495a2f892af818f737a2f1b23175e347d59d0772e11b815626c08b543f"),
    "990001925450205171": ("700:1", "453f13f9d38b01109afdcbf61222e50618ffd7342149debbbb9ba05797825ec4"),
}
_EXEMPLAR_PROVENANCE = "2b0e766f0ca46157443e62b3301a828adfc5ab56bbfc800b8571434938b9b1b5"
_HOLDING_CATALOGUES = {
    "990000532010205171": "https://ccfr.bnf.fr/portailccfr/ark:/16871/004D54030776",
    "990001372150205171": "https://clio.columbia.edu/catalog/17327314/librarian_view",
    "990001679010205171": "https://unapi.k10plus.de/?id=gvk:ppn:896085228&format=marcxml",
}


# The review also uses the title-page note in field 500 for Columbia volume 37.
_MILHAUD_COLOPHONS = {
    "990000538270205171": ("700:1", "957", "4cb91de3d3c01aa2a8fc262fb26c3b7da25c53b5643425e415de314154f3a156"),
    "990000927450205171": ("700:1", "957", "be4917268893894bf105b3e84986e3bc61fc0cddbc876196c2c3bcc976a7c263"),
    "990000998970205171": ("700:1", "957", "6ff265d7de7a09754db7658120e0c6988e775a159af00c5b30501b20f26358c0"),
    "990001065130205171": ("700:1", "957", "d263efc59109f120633b4c2f7260e73d875b928320d8145f992a0690cefd0f58"),
    "990001065660205171": ("700:1", "957", "fd8280cb6ec2c9ff201992cfd8b324e5fadc51082cace29a44aca73b5ad9d5ae"),
    "990001079140205171": ("700:1", "957", "24497ce8b3604f636ea7d81c2a97e0ed9e6f774e3de929a2c7190b4ffdeafc9d"),
    "990001079640205171": ("700:1", "957", "e72a6e046c7aa258ba803410cfdbbb58c936c17632b2bb1307d196a17acd02b8"),
    "990001084010205171": ("700:1", "957", "ab271bcda2ec3b4b2445dfff449852192ec9de89e0d7fe77712858d6f919c299"),
    "990001087630205171": ("700:1", "957", "545f59288e7881a936fdb0b86fe0ca0e5bc8326568ffde2c085acee11b0aed27"),
    "990001111570205171": ("700:1", "957", "6df6320fa79ac70fbe0b2daf2b837ee7a5722f87302f0a9aab1cb18eb7dda93f"),
    "990001117480205171": ("700:1", "957", "c3fde2177b6eef600eee17db03926ca6df711c43a58b1d80c242a1d5160ba467"),
    "990001117500205171": ("700:1", "957", "5f177064b7983aac40745ae6fd44ace8dac4fa05a781e03fcfa7d23e67ffcf93"),
    "990001141100205171": ("700:1", "957", "2e5ab7f5286175c4649e8a663402d8a61a92340bbddb98f01e878e38aca1d399"),
    "990001167280205171": ("700:1", "957", "44d3461d012317596dc3cbb30f09493b17d7ffc7d566e12cbeb6c362b6ed789e"),
    "990001219330205171": ("700:1", "957", "13d7e6f334f0a960f51d132c9c30a399ae943f937af5ed2df1fbaf00c58c4c0c"),
    "990001333740205171": ("700:1", "957", "1e23cef049b57c6592be0b1a520158cfd3440b9ef1d46a4397591115b9406256"),
    "990001343980205171": ("700:1", "957", "968975972f63333e7778e60d1fcd8ec01a072eec1a427c8c8fd2b186fe7442c5"),
    "990001371830205171": ("700:1", "957", "6cc5c6862b6bc9da1eb91b1eaeaeaba09836277264b4d7534fc0849f2be59852"),
    "990001371930205171": ("700:1", "957", "ad1817af4c70608075e8c9545edbcacc263d61717102a40593fdbe9a3747a486"),
    "990001372120205171": ("700:1", "957", "3fab4c77c1fe5dd3bcaa606854602dd1b58b92d237a2136388d222b5b2e4b7e0"),
    "990001372150205171": ("700:1", "957", "06ab6d897ab842a0fbb4881555b2cc89652cb1db1e26eae02340b27d88888cd7"),
    "990001510630205171": ("700:1", "957", "9f92f11fde95546bc47e13e099e7059e60c40087eaa0f81f0870d281ea819b4d"),
    "990001738610205171": ("700:1", "957", "8bf9e4f653618c31fbe92f1955aa1f9c575c7379c4bd0d2eb12bc66309774397"),
    "990001831030205171": ("700:1", "957", "b22859affdccca4b51c98adc5fb3facfd2cadcf3068c94a66cab8b67671098f4"),
    "990001831070205171": ("700:1", "957", "8b21be87163426e97b258554487895bf8af2e2bae1e8be953a0e1e28e7a4a598"),
    "990001855990205171": ("700:1", "957", "ee8733ecc79b285b6b0556bda885ba9d4ff7ce2a98981cd429c5708f460c369c"),
    "990001856020205171": ("700:1", "957", "d630777bf49df494ed7ef08d16c8dd3c551bb3238b8c179d11f2662e469f3052"),
    "990001989170205171": ("700:2", "957", "21cdf3194c2b3f4c83f6c42739f8a4a0980b1405c682f10721e9cc1c46eae4fe"),
    "990034170610205171": ("700:1", "500", "b6aa38f13732994615f12d72d9d7b8f96c22657f2b42dd83a81c926e157cde09"),
}


def _note_digests(record: Mapping, tag: str) -> list[str]:
    return [
        sha256(value.encode()).hexdigest()
        for row in marc.occurrences(record) if row["tag"] == tag
        for value in marc.values(row, "a")
    ]


def matches_reviewed_colophon(record: Mapping, cn: str, occurrence: str, heading: str) -> bool:
    proof = _COLOPHONS.get(cn)
    return bool(
        proof and heading == DELFOGIT_HEADING and occurrence == proof[0]
        and _note_digests(record, "957") == [proof[1]]
    )


def matches_reviewed_milhaud(record: Mapping, cn: str, occurrence: str, heading: str) -> bool:
    proof = _MILHAUD_COLOPHONS.get(cn)
    if not proof or heading != MILHAUD_HEADING or occurrence != proof[0]:
        return False
    notes = _note_digests(record, proof[1])
    return sha256(" ".join(notes).encode()).hexdigest() == proof[2]


def reviewed_colophon_urls(record: Mapping, cn: str, occurrence: str, heading: str) -> tuple[str, ...]:
    if heading == MILHAUD_HEADING:
        if cn == COLUMBIA_DELFOGIT or not matches_reviewed_milhaud(record, cn, occurrence, heading):
            return ()
        return (MILHAUD_COLUMBIA, MILHAUD_DS_URL)
    if cn == COLUMBIA_EXEMPLAR or not matches_reviewed_colophon(record, cn, occurrence, heading):
        return ()
    holding_catalogue = _HOLDING_CATALOGUES.get(cn)
    return (DELFOGIT_PENN, holding_catalogue) if holding_catalogue else (DELFOGIT_PENN,)


def matches_reviewed_exemplar(record: Mapping, cn: str, occurrence: str, heading: str) -> bool:
    return (
        cn == COLUMBIA_EXEMPLAR and matches_reviewed_colophon(record, cn, occurrence, heading)
        and _note_digests(record, "561").count(_EXEMPLAR_PROVENANCE) == 1
    )


def reviewed_scribe_exclusion(
    record: Mapping, cn: str, occurrence: str, heading: str,
) -> ReviewedScribeExclusion | None:
    if matches_reviewed_exemplar(record, cn, occurrence, heading):
        return ReviewedScribeExclusion(DELFOGIT_QID, MILHAUD_HEADING, COLUMBIA_EXEMPLAR_URL)
    if cn == COLUMBIA_DELFOGIT and matches_reviewed_milhaud(record, cn, occurrence, heading):
        return ReviewedScribeExclusion(MILHAUD_QID, DELFOGIT_HEADING, COLUMBIA_DELFOGIT_URL, DELFOGIT_QID)
    return None
