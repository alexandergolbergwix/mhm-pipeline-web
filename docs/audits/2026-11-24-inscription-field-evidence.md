# Requirement 8 proof for more inscriptions

The v870 repair verified nine inscriptions. This review checked 475 open inscription cases against the current Wikidata items. Of these cases, 51 have items without a `P1684` claim. The original catalogue supports 31 additional inscriptions on 28 items. The first Wikidata revision of each item identifies the project account, UID `6935067`. Each item's `P3959` matches one National Library of Israel record. [Ktiv catalogue guide](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/ktiv-version) · [Previous inscription proof](2026-11-24-inscription-proof-evidence.md)

## Proof for the 31 cases

For each case, the live NLI SRU service returned the exact text of MARC `957$a`. A proof entry binds the item and the MARC occurrence to the hash of that text. The new claim uses the exact passage as transcribed in the catalogue. The passage keeps the catalogue's brackets and ellipses. The claim excludes the catalogue prose before or after that passage. Each claim uses `P3831` for the colophon role. The 13 claims with an exact folio also use `P7416`. [Wikidata P1684](https://www.wikidata.org/wiki/Property:P1684) · [Wikidata P3831](https://www.wikidata.org/wiki/Property:P3831) · [Wikidata P7416](https://www.wikidata.org/wiki/Property:P7416)

The first group uses Hebrew for 26 inscriptions, Aramaic for two, Judeo-Arabic for two, and Latin for one. Wikidata accepts all four language codes for monolingual text. The Latin passage contains the copyist's name, the place Oxford, and the date. The Judeo-Arabic passages belong to different copyists and different portions of the manuscript. [Wikidata language codes](https://www.wikidata.org/w/api.php?action=query&meta=wbcontentlanguages&wbclcontext=monolingualtext&format=json) · [Latin colophon catalogue record](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001205540205171) · [Judeo-Arabic colophons catalogue record](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001207830205171)

| Item | Exact distinction between the two colophons | Original record |
|---|---|---|
| Q141746887 | One colophon gives the start of the copy. A second colophon gives its completion. | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000563810205171) |
| Q141746903 | One colophon is at folio 108ב after Pesahim. The second is at folio 193א after Hagigah. | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000572680205171) |
| Q141747484 | The first scribe's colophon is at folio 142ב. The second scribe's colophon is in a separate field. | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001207830205171) |

The review held cases with competing or duplicate live claims. It also held records that give a date or a name in catalogue prose, or contain several inscriptions in the same MARC field. Wikidata retains the other claims and their references.
