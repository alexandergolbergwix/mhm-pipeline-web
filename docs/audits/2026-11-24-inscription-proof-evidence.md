# Requirement 8 inscription evidence

The audit lists 32 inscription cases with a quotation and a parenthetical location in the original `957$a` field. Five cases support an addition. The other 27 items already have inscription claims. Each of the five additions has a named folio, a clear quotation boundary, and a unique match on `P3959`.

## Five cases with proof

The National Library of Israel SRU service returns the exact source text at `957:1:1` in all five records. Each item has zero `P1684` claims, one exact `P3959` value, and first creator UID `6935067`. Each addition uses `P1684` in Hebrew, `P3831` for the colophon role, and `P7416` for the folio. The NLI record supports the text and both qualifiers.

| Item | MARC control number | Folio | Observed revision | Original record |
| --- | --- | --- | --- | --- |
| Q141746842 | 990000413570205171 | 22א | 2571950766 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000413570205171) |
| Q141747304 | 990001084110205171 | קלא | 2571969247 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001084110205171) |
| Q141747934 | 990001689730205171 | 131א | 2573979701 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001689730205171) |
| Q141748129 | 990001831110205171 | 38ב | 2573724530 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001831110205171) |
| Q141748333 | 990001949060205171 | 169א | 2573687880 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001949060205171) |

## Why the cases stay open

The source parser checks for a close quote at the end of the field. The five `957$a` fields put editorial text after the close quote. Other fields can contain multiple quotations or commentary with quote marks. A match on the last quote can include editorial text in `P1684`. The reviewed proof fixes the exact quote and requires a match on the source text and field occurrence. [Source parser](../../backend/app/pipeline/wikidata_gila_sources.py)

The other 27 items already contain inscription text. The live claims contain part of the same quote, editorial notes, mixed fields, or more than one of these. An addition could duplicate text. The review found no exact match for a citation-only edit in these 27 cases. Two examples of mixed or duplicate text are Q141747228 and Q141747246. [Q141747228](https://www.wikidata.org/wiki/Q141747228), [Q141747246](https://www.wikidata.org/wiki/Q141747246)

The location for Q141748143 describes a probable later addition. Q141747612 names page 233 and has a second quote in the same field. Q141746974 has five `957` fields and one combined inscription claim. `P7416` specifies a folio number. [Q141748143](https://www.wikidata.org/wiki/Q141748143), [Q141747612](https://www.wikidata.org/wiki/Q141747612), [Q141746974](https://www.wikidata.org/wiki/Q141746974), [P7416](https://www.wikidata.org/wiki/Property:P7416)

## Four more exact quotations

A second pass found 36 fields with a quote and a possible editorial suffix. Five items have zero `P1684` claims. One field has two separately quoted texts. The four below give one clear quotation and cite other text in the editorial suffix.

The NLI SRU service confirms `957:1:1` in each record. Each item has one exact `P3959` value and first creator UID `6935067`. The catalogue gives no folio for these quotations. The colophon role uses `P3831`.

| Item | MARC control number | Observed revision | Original record |
| --- | --- | --- | --- |
| Q141746853 | 990000464670205171 | 2574030581 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990000464670205171) |
| Q141747249 | 990001022910205171 | 2571966342 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001022910205171) |
| Q141748080 | 990001767770205171 | 2573510430 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001767770205171) |
| Q141748306 | 990001917110205171 | 2572024300 | [NLI catalogue](https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/viewerpage?vid=NNL_ALEPH990001917110205171) |
