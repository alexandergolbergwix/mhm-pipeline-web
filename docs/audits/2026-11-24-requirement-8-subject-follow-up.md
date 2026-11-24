# Requirement 8 subject follow-up

The production audit `0c285cca-8f4f-4fb4-9a8f-95770d7d5c6b` left 1,092 subject cases open. This review adds proof for 109 MARC occurrences. The proof register records the source fields, work titles, topic items, and authority URLs.

| Exact evidence | Cases | Source |
| --- | ---: | --- |
| Jewish prayer commentaries | 20 | The MARC notes identify each commentary. The NLI authority identifies Jewish prayer. [NLI prayer authority](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007531656605171.jsonld) |
| Yemenite prayer practice | 18 | Vana's commentary compares the Yemenite prayer formula with the printed formula. Each copy identifies the commentary as a separate work. [Autograph transcription](https://www.maharitz.co.il/?ArticleID=10165&CategoryID=321&Page=1), [NLI Yemenite rite authority](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007554973505171.jsonld) |
| Astronomy, calendars, astrology, and medicine | 15 | The contents notes identify the named tables, eclipse treatise, calendar manuals, or medical texts. The catalogue records date the relevant copies. [Astronomical table catalogue](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/bibs/990001902220205171.jsonld), [Eclipse treatise catalogue](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/bibs/990000649970205171.jsonld), [NLI calendar authority](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007476137105171.jsonld) |
| Biblical Hebrew grammar | 2 | The sources identify Torah grammar in Hammerschlag's diagram and biblical usage in Abraham Ibn Ezra's grammatical treatise. [Ilanot manuscript record](https://ample.dev.ilanot.org/manuscript/00519?lang=en), [NLI edition of *Safah Berurah*](https://www.nli.org.il/he/books/NNL_ALEPH990010923800205171/NLI), [Critical edition](https://benyehuda.org/read/62383) |
| Kabbalah and Sephirot | 10 | The full MARC notes identify the Kabbalistic works and passages that discuss the Sephirot. [BnF Kabbalah authority](https://catalogue.bnf.fr/ark:/12148/cb119554900), [Wikidata Sephirot](https://www.wikidata.org/wiki/Q660267) |
| Plague, Jewish ethics, synagogues, and book burning | 7 | The sources identify the exact plague accounts, ethical works, synagogue records, or lament on the book burning. [Candia synagogue record](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/bibs/990000856010205171.jsonld), [University of Leeds Ancona manuscript](https://digital.library.leeds.ac.uk/14957/) |
| Other named works and passages | 37 | Case sources identify religious disputations, Jewish law, Aggada, religious practices, and other exact topics. [NLI religious disputations authority](https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/authorities/987007557960405171.jsonld) |

The repair checks the original heading, the source note, and the current work link. It checks the first creator and revision of the work before each write. It sends `maxlag=100` and adds only subject claims or references.

The review excludes a calendar text where the MARC author conflicts with the work item. It holds genre descriptions that lack a subject assertion. It also holds the Ancona earthquake case until an exact event item has sufficient evidence.
