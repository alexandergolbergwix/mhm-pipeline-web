# Manuscript form and subject scope evidence for requirement 8

The production audit `87d9ebf8-6593-41f4-a721-0b6cf0b72425` had 3,147 open requirement 8 cases. These included 1,238 subject occurrences, 251 genre occurrences, and 461 catalogue status notes. [Production audit](https://mhm-pipeline.org/admin/wikidata-gila-repairs)

## Copy-level form proof

Six manuscript records support the autograph class on the exact physical copy. The MARC record identifies the author as the writer in the colophon, or explicitly describes the translator's own handwritten translation. The proof binds the original 655 heading and the exact 500 or 957 occurrence. First-revision checks confirmed project creator UID `6935067` on all six targets. [MARC 655](https://www.loc.gov/marc/bibliographic/bd655.html) · [Wikidata autograph class](https://www.wikidata.org/wiki/Q9026959)

| Wikidata item | Proof |
| --- | --- |
| Q141746908 | The author Joseph Sarfati says in the colophon that he composed and wrote this copy. [NLI](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000576790205171&scope=KTIV&vid=KTIV) |
| Q141746986 | Elijah Loans identifies himself as both author and scribe. [NLI](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000665230205171&scope=KTIV&vid=KTIV) |
| Q141747097 | Moses Halevi identifies his completion of this travel text in the author's colophon. [NLI](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000801730205171&scope=KTIV&vid=KTIV) |
| Q141747233 | The catalogue identifies an autograph of the translation in the translator's hand. [NLI](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001011060205171&scope=KTIV&vid=KTIV) |
| Q141747234 | The catalogue identifies an autograph of the translation in the translator's hand. [NLI](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001011110205171&scope=KTIV&vid=KTIV) |
| Q141748102 | Hezekiah Roman identifies himself as both author and scribe. [NLI](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001794810205171&scope=KTIV&vid=KTIV) |

Eleven remaining records explicitly describe a decorated or illustrated title page of the current copy. The note or the colophon identifies the actual page. Each proof binds its exact 500 or 957 text together with the 655 occurrence. The chosen class is `Q48498`, and every proposed target has the exact Ktiv ID and project creator. The checks retain all existing classes. [Manuscript class model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model#Classes_of_manuscripts) · [NLI example with a decorated title page](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000913260205171&scope=KTIV&vid=KTIV)

## Subject scope census

All 576 `Judaism` headings contain a subdivision, and 401 contain a topical subdivision. The 54 `Jews` headings and 23 `Hebrew language` headings also contain subdivisions. The catalogue headings are useful evidence in the archive. The project rule excludes generic headings as a main subject. The original author request requires a subject or genre to identify the relevant work or passage. [MARC 650](https://www.loc.gov/marc/bibliographic/bd650.html) · [Wikidata main subject](https://www.wikidata.org/wiki/Property:P921) · [Wikidata Studio subject rules](../architecture/rules/wikidata-studio.md)

The live preflight found 389 remaining subject and genre cases with a recognized crosswalk and no subdivisions. It read 318 manuscript items and 437 work items. Of the cases, 181 had one exact title match on a source-bound work link. Of those, 43 had the exact proposed subject or genre value on the work, but the claim lacked a reference to this specific manuscript's Ktiv record. None passed the general single-work scope rule. Related titles and distinct contents still require direct evidence for the particular work or passage.

## Exact work scope proof

The preflight found 27 cases where the source describes the exact work linked from the manuscript. Each proof binds the original 650 or 655 occurrence and a separate 500 or 957 note. The manuscript has its exact Ktiv identifier. Its work link cites that same Ktiv record. One linked work has the exact title, and the project created that work. The preflight checked the exact value and qualifiers. A repair can add a source to an existing claim or add a claim that has no duplicate. [MARC 500](https://www.loc.gov/marc/bibliographic/bd500.html) · [Wikidata genre](https://www.wikidata.org/wiki/Property:P136) · [Wikidata work embodied by this item](https://www.wikidata.org/wiki/Property:P1574)

For example, the source copies the title page of *Kol Sakhal and Sha'agat Aryeh*. The title page identifies a reply to the first text's arguments against Kabbalah and the Oral Torah. The complete title names both texts. [Ktiv record](https://www.nli.org.il/he/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000790230205171&scope=KTIV&vid=KTIV)

One of the 27 cases has the catalogue genre `Piyutim`. The source quotes the title of the cycle of liturgical poems. The linked work has the exact title. The NLI authority and the Wikidata genre item use the same name. [Ktiv record](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001917110205171&scope=KTIV&vid=KTIV) · [Piyyut on Wikidata](https://www.wikidata.org/wiki/Q781402)

## Case totals

Of the 461 open catalogue notes in MARC 590, 453 say that the record is temporary. The other eight contain permission, catalogue, auction, or archive information. MARC reserves the field for local use. Preserve each note with the source record. The temporary status describes the catalogue record. [Library of Congress MARC local notes](https://loc.gov/marc/bibliographic/bd59x.html)

A 40-percentage-point gain requires at least 1,681 newly verified cases at the current total of 4,202. The 1,448 open cases outside subjects and catalogue status notes cannot reach that gain, even if each acquires sufficient evidence.
