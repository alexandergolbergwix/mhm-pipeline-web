# Scribe authority review for Requirement 5

The production baseline audit identified 305 open scribe cases. Each repair below matches an exact MARC occurrence to a person item. The review uses the full MARC record. The operator checks the first creator and current revision before each edit.

| Manuscript | Person | Source evidence |
| --- | --- | --- |
| Q141747013 | Immanuel ben Gad de Milhaud, Q132798628 | Field 957 gives the signed name in 1688. Field 700 reverses the names. Digital Scriptorium identifies the same person. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000702460205171&vid=KTIV) · [Digital Scriptorium](https://catalog.digital-scriptorium.org/wiki/Item:Q75810) |
| Q141175745 | Yeshua Azoulay, Q141175593 | Field 500 quotes the author's preface. The authority record names the early scribe. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000776020205171&vid=KTIV) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1059) |
| Q141175769 | Yosef ben Samuel Ashnavi, Q141953248 | Field 700 names him as a scribe and former owner. In field 561, he identifies the copy as his own work. [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1189) |
| Q141175772 | Shlomo Nahum ben Shabbetai, Q141748471 | Field 957 and the collection catalogue quote the Candia colophon from 1636. [Catalogue, lot 13](https://bidspirit-images.global.ssl.fastly.net/kedem/auctionDocs/531/catalog/100_-_.pdf) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1160) |
| Q141175777 | Shlomo Sidish, Q141748463 | Field 957 gives the name and copy date. HMO identifies the scribe of this control number. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990000880550205171&vid=KTIV) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1093) |
| Q141175788 | Yosef Yitzhak Amar, Q141953226 | Field 500 quotes his copy credit from 1676. The person item identifies this control number. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001089360205171&vid=KTIV) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1110) |
| Q141175794 | Joseph ben Saadia ben Joseph ben David al-Abhar, Q141748479 | Field 957 names the scribe, his lineage, and his patron. The catalogue gives two possible readings of the year. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001205840205171&vid=KTIV) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1136) |
| Q141175798 | Toviah ben Hayim Isaac Gabbai, Q141953251 | The colophon on folio 216v gives the full name and Jerusalem as the place of copy. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001254240205171&vid=KTIV) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1205) |
| Q141175886 | Yehudah ben Isaac de Digne, Q141953253 | The 1688 colophon names the person who copied the final unnumbered replacement leaf. The repair applies to that leaf. [Klagsbald collection catalogue, lot 8](https://live-kedem.circuit.auction/he/node/188519) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1213) |
| Q141175887 | Nathan Joshua ben Solomon de Bedarrides, Q141953249 | Field 957 and the Leeds catalogue identify the scribe of Roth MS 29. [Leeds catalogue](https://explore.library.leeds.ac.uk/multimedia/16519/MS_164_Roth.pdf) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1192) |
| Q141175892 | Joseph ben Jonah al-Fataihi, Q141748477 | The 1607 colophon gives the full name and distinguishes the scribe from the patrons. [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/itempage?docId=PNX_MANUSCRIPTS990001878130205171&vid=KTIV) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1180) |
| Q141175900 | Samuel ben Abraham Mondolfo, Q141953262 | Field 500 gives the copy credit. The Jewish Theological Seminary confirms the relationship for MS 10576, dated 1642. [JTS catalogue](https://makor.primo.exlibrisgroup.com/discovery/fulldisplay?docid=alma990001928240107706&context=L&vid=01JTS_INST:01JTS&lang=en) · [HMO](https://mhm-hmo.wikibase.cloud/wiki/Item:Q1056) |

## Incorrect authority identifiers

The VIAF records below identify different people. The repair deprecates the exact `P8189` statement on each project person item.

| Project item | Incorrect NLI ID | Person in the authority record |
| --- | --- | --- |
| Q141748471 | 987007258452905171 | Nahum Ben-Shlomo, born in 1957. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007258452905171) |
| Q141748463 | 987007294122705171 | Shlomo Seidman. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007294122705171) |
| Q141953226 | 987007270155705171 | Yitzhak Yosef, born in 1952. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007270155705171) |
| Q141748479 | 987007300794605171 | Saadia ben Joseph Danan. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007300794605171) |
| Q141953253 | 987007270165205171 | Isaac ben Judah ha-Levi. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007270165205171) |
| Q141748477 | 987007415787305171 | Moses ben Joseph al-Fataihi. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007415787305171) |
| Q141953262 | 987007261158205171 | Abraham Firkowitsch, 1786–1874. [VIAF](https://viaf.org/viaf/sourceID/J9U%7C987007261158205171) |

## Open evidence

The Rada'a colophon and the Yemeni authority catalogue identify Saadia ben Solomon by the same short name. The authority catalogue also lists a contemporary poet with this name. The name in the colophon lacks a family name or other unique detail. [Rada'a copyist](https://www.teman.org.il/ar/content/12545) · [Contemporary poet](https://www.teman.org.il/content/12544)

The Sassoon manuscript names Yosef ben Saadia. The candidate authority names Yosef ben Saadia ha-Kohen. The manuscript gives no priestly title or other detail to confirm that identity. The HMO bridge for Yom Tov Lipman connects to 27 Wikidata person items.

The 1672 colophon for Q141747174 names Moses ben Abraham Mushta. The MARC heading reverses the names. The review found no verified Wikidata item for the scribe. [Transcribed colophon](https://www.teman.org.il/content/12358)
