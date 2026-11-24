# Scan and scope evidence for requirement 8

The audit `95167685-3a8c-45e7-a033-9ef3e6809b11` lists 107 open access cases. The preflight used the full MARC records and public Wikidata snapshots. It checked the first Wikidata revision of each candidate. It made no Wikidata edits. [Production audit](https://mhm-pipeline.org/admin/wikidata-gila-repairs)

## Electronic access

The preflight found 29 exact matches between a MARC 856 URL and an unqualified `P953` statement. Each MARC field identifies an online copy. Each Wikidata item has the same National Library of Israel manuscript ID as the source record. All 29 statements lack a reference. MARC field 856 distinguishes an online copy from a component or related resource. [Library of Congress MARC 856](https://www.loc.gov/marc/bibliographic/bd856.html) · [Wikidata P953](https://www.wikidata.org/wiki/Property:P953)

| Holder | Project items | Items from other accounts | HTTPS check |
| --- | ---: | ---: | --- |
| Hungarian Academy of Sciences | 14 | 0 | The holder served 14 manuscript pages. |
| Bavarian State Library | 1 | 0 | The holder served one digitization page. |
| Hamburg State and University Library | 7 | 1 | The viewer served a bot challenge. The IIIF service returned all eight manifests. |
| Bibliothèque nationale de France | 5 | 0 | The viewer returned HTTP 403. The IIIF service returned three manifests. |
| Hebrew Union College | 1 | 0 | The connection failed. |

The 25 accessible project cases support an additional reference on the exact existing statement. The reference can cite the MARC record and the HTTPS holder record. The preflight kept the original HTTP value and its statement ID. The holder records confirm the shelfmark in each case. [Hungarian Academy of Sciences scan](https://real-ms.mtak.hu/14769/) · [Bavarian State Library manifest](https://api.digitale-sammlungen.de/iiif/presentation/v2/bsb00117629/manifest) · [Hamburg manifest](https://iiif.sub.uni-hamburg.de/object/PPN896090442/manifest) · [Gallica manifest](https://gallica.bnf.fr/iiif/ark:/12148/btv1b10540657r/manifest.json)

## Subject and genre scope

The preflight found 439 open subject or genre occurrences with an exact term in the project authority map and no applicable subdivision. None met the rules for a single work that the MARC record identifies. Of these occurrences, 30 have one distinct linked work and a live statement for the same term. All 30 MARC records also name other titles. The additional titles prevent an exact assignment of those subject or genre occurrences to the linked work. [Library of Congress MARC 650](https://www.loc.gov/marc/bibliographic/bd650.html) · [Library of Congress MARC 740](https://www.loc.gov/marc/bibliographic/bd740.html)

In 50 open cases, the 655 heading says `Autograph manuscripts`. The notes in some of these records specify a partial or probable autograph. Three records explicitly describe the complete manuscript as an autograph. Their Wikidata items have the exact manuscript ID and no existing autograph class. The first revisions show that the project created all three items. An additional `P31` value of `Q9026959` describes the manuscript as an autograph and preserves its current classifications. [Library of Congress MARC 655](https://www.loc.gov/marc/bibliographic/bd655.html) · [Wikidata autograph class](https://www.wikidata.org/wiki/Q9026959)

| Item | MARC record | Source for the complete manuscript |
| --- | --- | --- |
| Q141746950 | `990000610690205171` | `957:1` calls the manuscript an autograph and records colophons in all parts. |
| Q141747520 | `990001240470205171` | `500:11` calls the manuscript an autograph. |
| Q141747654 | `990001360250205171` | `500:1` calls the manuscript an autograph and describes the complete seven-part work. |

## Catalogue status and editions

Of 461 open catalogue notes, 453 describe the source record as provisional. This status describes the catalogue record. Preserve the note in the source evidence. [Requirement 8 proof plan](2026-11-24-requirement-8-proof-plan.md)

The Bodleian guide identifies distinct 1886, 1906, and 1994 publications. A citation with a catalogue number needs the exact edition and scope before a source repair. Keep catalogue numbers distinct from page numbers. [Bodleian catalogue guide](https://libguides.bodleian.ox.ac.uk/c.php?g=422971&p=2888528) · [Library of Congress MARC 510](https://www.loc.gov/marc/bibliographic/bd510.html)
