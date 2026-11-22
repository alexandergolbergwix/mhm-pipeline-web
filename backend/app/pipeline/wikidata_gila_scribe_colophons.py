"""Reviewed catalogue evidence for named manuscript scribes."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_source_choices import _claim_values

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
    hebrew_name: str
    urls: tuple[str, ...]
    identifier: tuple[str, str] | None = None
    hewiki_title: str | None = None
    wrong_ids: tuple[tuple[str, str], ...] = ()

    def matches(self, item: Mapping) -> bool:
        if item.get("id") != self.qid or item.get("labels", {}).get("he", {}).get("value") != self.hebrew_name:
            return False
        if self.identifier:
            pid, value = self.identifier
            if _claim_values(item, pid) != [value]:
                return False
        if self.hewiki_title and item.get("sitelinks", {}).get("hewiki", {}).get("title") != self.hewiki_title:
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


_QATI = ReviewedScribePerson(
    "Q108378115", "משה קטיעי",
    ("https://teman.org.il/content/13032",),
    hewiki_title="משה קטיעי",
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


# Bind each identification to the full set of notes in the reviewed MARC field.
_EXTRA_SCRIBE_NOTES = {
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


def reviewed_scribe_note(
    record: Mapping, cn: str, occurrence: str, heading: str, candidate: Mapping,
) -> ReviewedScribeNote | None:
    proof = _EXTRA_SCRIBE_NOTES.get(cn)
    if not proof or proof.occurrence != occurrence or proof.heading != heading or not proof.person.matches(candidate):
        return None
    notes = _note_digests(record, proof.tag)
    return proof if notes and sha256(" ".join(notes).encode()).hexdigest() == proof.digest else None


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
