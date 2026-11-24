# Holding library and illustration evidence for requirement 8

The review found 83 supported cases in the open corpus. It matched the exact MARC occurrence and Ktiv ID for each item. It confirmed first creator UID `6935067`. Each repair can preserve the existing statements. [Production audit](https://mhm-pipeline.org/admin/wikidata-gila-repairs) · [Creator-only writes](../architecture/blocks/wikidata-studio/creator-only-writes.md)

## Scan evidence

Nine original Ktiv records link to images that Columbia University Libraries deposited in the Internet Archive. Each deposit supplies a public set of page images and a MARC record for the digital reproduction. Field 776 identifies the physical manuscript by its OCLC number. The physical CLIO record identifies the same shelfmark as Ktiv. Each live Wikidata item has one matching access statement. The repair adds a reference to that statement. [Columbia Hebrew manuscripts](https://library.columbia.edu/libraries/rbml/collecting/judaica.html) · [Library of Congress MARC 776](https://www.loc.gov/marc/bibliographic/bd776.html) · [Library of Congress MARC 856](https://www.loc.gov/marc/bibliographic/bd856.html)

| Item | Shelfmark | Physical record | Columbia scan |
| --- | --- | --- | --- |
| Q141747649 | Ms. X 893 H 459 | [CLIO 8557233](https://clio.columbia.edu/catalog/8557233) | [Archive 16001069](https://archive.org/details/ldpd_16001069_000) |
| Q141747653 | Ms. X 893 K 15 | [CLIO 7633515](https://clio.columbia.edu/catalog/7633515) | [Archive 15492588](https://archive.org/details/ldpd_15492588_000) |
| Q141747670 | Ms. X 893 Z 8 vol. 1 no. 16 | [CLIO 8306206](https://clio.columbia.edu/catalog/8306206) | [Archive 16001048](https://archive.org/details/ldpd_16001048_000) |
| Q141747671 | Ms. X 893C J 55 vol. 6 | [CLIO 7460999](https://clio.columbia.edu/catalog/7460999) | [Archive 17229167](https://archive.org/details/ldpd_17229167_000) |
| Q141747673 | Ms. X 893C J 55 vol. 16 | [CLIO 7461905](https://clio.columbia.edu/catalog/7461905) | [Archive 17230217](https://archive.org/details/ldpd_17230217_000) |
| Q141747675 | Ms. X 893C J 55 vol. 25 | [CLIO 7461932](https://clio.columbia.edu/catalog/7461932) | [Archive 17327309](https://archive.org/details/ldpd_17327309_000) |
| Q141747676 | Ms. X 893C J 55 vol. 28 | [CLIO 7461943](https://clio.columbia.edu/catalog/7461943) | [Archive 17327314](https://archive.org/details/ldpd_17327314_000) |
| Q141747696 | Ms. X 893 T 721 | [CLIO 8138838](https://clio.columbia.edu/catalog/8138838) | [Archive 15304515](https://archive.org/details/ldpd_15304515_000) |
| Q141748436 | Ms. X 893C J 55 vol. 37 | [CLIO 9160970](https://clio.columbia.edu/catalog/9160970) | [Archive 11536929](https://archive.org/details/ldpd_11536929_000) |

The Weimar manifest identifies Oct. 157 and supplies 47 canvases. The item has the matching shelfmark and no access statement. The repair adds the existing holder viewer URL from MARC 856. The Vatican manifest identifies Neofiti.33 and supplies 265 canvases. The original MARC URL lacks the required path separators. The live access statement already points to the correct viewer. The repair adds the valid manifest as a reference to that statement. [Weimar viewer](https://haab-digital.klassik-stiftung.de/viewer/image/1644807637/) · [Weimar manifest](https://haab-digital.klassik-stiftung.de/viewer/api/v1/records/1644807637/manifest/) · [Vatican viewer](https://digi.vatlib.it/view/MSS_Neofiti.33) · [Vatican manifest](https://digi.vatlib.it/iiif/MSS_Neofiti.33/manifest.json)

The review held one Columbia case. Its Ktiv shelfmark has the suffix Q, and the linked CLIO holding omits that suffix. The British Library also lists some copies in the legacy service as unavailable. The archive catalogue can identify such copies even when the images are unavailable. [Columbia M112 holding](https://clio.columbia.edu/catalog/7844784) · [British Library digitised manuscripts](https://www.bl.uk/collection/digitised-manuscripts-archives)

## Copy-level illustration

The full MARC corpus has 94 open occurrences of `Illustrated works (Manuscript)`. In 73 cases, a separate note identifies actual illustration or decoration on the physical copy. Examples include painted figures, decorated title pages, decorated text headings, and micrography. The review excluded a case that another account created. Each remaining case supports `P31` with `Q48498`. The proof binds both the genre occurrence and the exact note. The addition keeps the current item classes. [Wikidata Manuscripts data model](https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model#Classes_of_manuscripts) · [Illuminated manuscript](https://www.wikidata.org/wiki/Q48498) · [NLI manuscript with colored illustrations](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001361600205171&scope=KTIV&vid=KTIV)

## Scope and catalogue notes

The prior audit still had 1,238 subject occurrences and 323 genre occurrences open. All corresponding MARC headings name the NLI vocabulary in subfield 2. None provides an authority identifier in subfield 0 or 1, or explicit copy scope in subfield 3 or 5. Of the subject occurrences, 845 have subdivisions. Of the subject occurrences, 655 have a MARC record with a related title or contents field. A subject link needs evidence for the exact work, copy, or passage. [Library of Congress MARC 650](https://www.loc.gov/marc/bibliographic/bd650.html) · [Library of Congress MARC 655](https://www.loc.gov/marc/bibliographic/bd655.html)

The audit has 461 catalogue note occurrences. Of these, 453 identify a provisional catalogue record. The other eight concern sale prices, catalogue detail, shelfmarks, permission, or removal from a collection. These values remain available as source evidence. [Requirement 8 proof plan](2026-11-24-requirement-8-proof-plan.md)
