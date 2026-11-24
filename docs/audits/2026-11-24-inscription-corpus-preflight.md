# Inscription proof for the corpus preflight

The preflight reviewed 444 open inscription occurrences with a matching Wikidata manuscript and full MARC record. Of these, 424 case rows pointed to items that already had a `P1684` claim, and 20 case rows had none. The live National Library of Israel SRU service confirmed the exact `957$a` text for the 11 proof rows. Each Wikidata item had one exact `P3959` value and first creator UID `6935067`. [Ktiv catalogue guide](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/ktiv-version) · [Inscription property](https://www.wikidata.org/wiki/Property:P1684)

## Six new transcriptions

The MARC field explicitly identifies each passage as a colophon. Each item had zero `P1684` claims at the live check. The proof preserves the literal text and catalogue marks for unreadable portions. It uses the colophon role and leaves the folio blank when the catalogue does not provide one.

| Wikidata item | Proof in the MARC record | Catalogue record |
| --- | --- | --- |
| Q141747637 | The record names the owner as the writer and marks the quotation. The location is page 5. | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001347470205171) |
| Q141175801 | The catalogue gives the quoted Aramaic date at the end of Leviticus. | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001360630205171) |
| Q141748078 | The catalogue gives the quoted Hebrew date at the end of the manuscript. | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001761840205171) |
| Q141748273 | The catalogue introduces the exact Latin colophon after a colon. | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001909500205171) |
| Q141748279 | The catalogue identifies the colophon and its quotation after a separate title-page quotation. | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001914650205171) |
| Q141748419 | The catalogue identifies an incomplete colophon and gives the surviving quotation. | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990026143620205171) |

## Five exact inscriptions already in Wikidata

Each item has one active claim with the exact quotation, colophon role, folio, and Ktiv reference. The public folio has a doubled quote where the catalogue has one quote. The proof matches the current claim and preserves its raw text and qualifiers. [Folio property](https://www.wikidata.org/wiki/Property:P7416)

| Wikidata item | Folio in the MARC record | Catalogue record |
| --- | --- | --- |
| Q141746871 | שכח ע"ב | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000554010205171) |
| Q141746961 | רמד ע"א | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000621640205171) |
| Q141747152 | קצ ע"ב | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000829710205171) |
| Q141175891 | רפט ע"ב | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001875220205171) |
| Q141748412 | קטו ע"ב | [Ktiv](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990025843430205171) |

The audit and repair simulation verified the five existing claims and all six proposed additions. The six edits preserve every existing claim and reference. Cases with broad prose, page numbers in the folio property, competing scopes, or uncertain quotation boundaries need source review.
