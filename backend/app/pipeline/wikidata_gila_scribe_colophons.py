"""Reviewed catalogue evidence for the Delpuget manuscript scribe cases."""

from collections.abc import Mapping
from hashlib import sha256

from app.pipeline import marc_occurrences as marc

DELFOGIT_QID = "Q141175708"
DELFOGIT_HEADING = "דלפוגיט, אליהו"
DELFOGIT_PENN = "https://find.library.upenn.edu/catalog/9977510917903681"
DELFOGIT_PARMA = (
    "https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage"
    "?docId=PNX_MANUSCRIPTS990000810710205171&scope=KTIV&vid=KTIV"
)
DELFOGIT_WRONG_IDS = {"P8189": "987007453092705171", "P214": "33148874584149621414"}

COLUMBIA_EXEMPLAR = "990001371830205171"
COLUMBIA_EXEMPLAR_URL = "https://clio.columbia.edu/catalog/17229167/librarian_view"
MISATTRIBUTION = "Q105675146"

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


def reviewed_colophon_urls(record: Mapping, cn: str, occurrence: str, heading: str) -> tuple[str, ...]:
    if cn == COLUMBIA_EXEMPLAR or not matches_reviewed_colophon(record, cn, occurrence, heading):
        return ()
    holding_catalogue = _HOLDING_CATALOGUES.get(cn)
    return (DELFOGIT_PENN, holding_catalogue) if holding_catalogue else (DELFOGIT_PENN,)


def matches_reviewed_exemplar(record: Mapping, cn: str, occurrence: str, heading: str) -> bool:
    return (
        cn == COLUMBIA_EXEMPLAR and matches_reviewed_colophon(record, cn, occurrence, heading)
        and _note_digests(record, "561").count(_EXEMPLAR_PROVENANCE) == 1
    )
