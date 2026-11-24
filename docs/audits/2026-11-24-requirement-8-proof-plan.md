# Evidence plan for 80% on requirement 8

The production audit completed on 24 November 2026 at 11:29 Israel time. It verified **746 of 4,202 cases (17.75%)** for sources, bibliography, and inscriptions. At the same denominator, 80% requires **3,362 verified cases**, or **2,616 more**. The audit also lists 13,938 source passages for requirement 8 in a separate inventory. [Production audit](https://mhm-pipeline.org/admin/wikidata-gila-repairs) · Audit ID `95167685-3a8c-45e7-a033-9ef3e6809b11`

| Open group | Cases | Proof that the group needs |
| --- | ---: | --- |
| Subject terms | 1,238 | Exact authority and the work, copy, or passage that the term describes |
| Bibliography | 722 | Exact edition and the locator from the original MARC field |
| Catalogue status and other local notes | 461 | Exact note, date if available, source, and applicability |
| Inscriptions that need transcription proof | 431 | Quoted text, role, and folio from the catalogue or holder image |
| Genre or form | 326 | Exact authority and the relevant work or manuscript form |
| Electronic access | 107 | Holder, shelfmark, resource relation, and accessible copy |
| Citation differences between MARC snapshots | 88 | Same catalogue record, exact field occurrence, and source version |
| Other | 83 | 37 inscription overlaps, 22 historical proposals, 16 ambiguous claims, 4 scope cases, and 4 generated source repairs |
| **Total open** | **3,456** | |

The first revisions confirm that other accounts created every destination for the eight cases that require repair. Four source repair cases point to three items. The four historical repair cases each point to a separate item. Two audit rows show project manuscripts, but the proposed edit targets their linked works. Use the destination QID for the creator check. [Source case builder](../../backend/app/pipeline/wikidata_gila_sources.py) · [Creator check](../../backend/app/pipeline/wikidata_gila_corpus_repair.py)

## Proof to reuse across cases

1. **Bibliography.** The 722 MARC occurrences use 489 distinct strings after Unicode and whitespace normalization. The 10 most frequent strings cover 182 occurrences. The top 25 cover 244. Prove each edition once and check each occurrence's scope and locator. The strings for Neubauer 1886, the 1994 supplement, Margoliouth, and Weisz 1906 cover 126 occurrences. Treat those as evidence leads. [MARC 510](https://www.loc.gov/marc/bibliographic/bd510.html) · [MARC 581](https://www.loc.gov/marc/bibliographic/bd581.html) · [Bodleian catalogues](https://libguides.bodleian.ox.ac.uk/c.php?g=422971&p=2888528)
2. **First bibliography pilot.** The first-revision check confirmed our creator ID on all 14 items with the exact Weisz 1906 citation. The Frankfurt scan identifies the publication. Check the exact Wikidata edition or work item and the locator on each citation before any edit. The Hungarian Academy scan confirms the manuscript shelfmark for Q141175895. [Frankfurt edition](https://sammlungen.ub.uni-frankfurt.de/freimann/content/titleinfo/6528347) · [Holder scan](https://real-ms.mtak.hu/14760/) · [Wikidata manuscript](https://www.wikidata.org/wiki/Q141175895)
3. **Subjects and genres.** All 1,238 open subject occurrences and 326 open genre occurrences use `$2 nli`. They supply no `$0` or `$1` identifier. The authority vocabulary can supply concept evidence. The source scope still needs an exact join to the work, copy, or passage. Preserve the subdivision in the 845 subject fields that have one. The main term “Judaism” occurs in 576 open subject cases. A heading shared by many records needs a scope decision before a bulk edit. [MARC 650](https://loc.gov/marc/bibliographic/bd650.html) · [MARC 655](https://www.loc.gov/marc/bibliographic/bd655.html) · [MARC source codes](https://loc.gov/standards/sourcelist/subject.html) · [NLI authority catalogue](https://www.nli.org.il/en/research-and-teach/catalogs/authorities)
4. **Inscriptions and scans.** Of 431 transcription cases, 118 have a recognized inscription label followed by a quote. Of those, 32 also name a folio. Start with those 32. Check the quote boundaries, title-page or colophon role, and exact copy. Then check its folio on the holder image. Keep the catalogue's uncertainty. Of 107 unchecked access links, 16 have a MARC indicator for a component part. Use the manifest and canvas for copy and folio proof. [MARC 856](https://www.loc.gov/marc/bibliographic/bd856.html) · [IIIF Canvas](https://iiif.io/api/presentation/3.0/#53-canvas)
5. **Catalogue notes.** Of 461 notes in this group, 453 say the catalogue record is temporary. Preserve the text as source metadata. Keep the note's status separate from the manuscript's inscription text and from the certainty of individual claims. Confirm with Gila how to report these source-metadata cases. [Source case builder](../../backend/app/pipeline/wikidata_gila_sources.py)

## Completion checks

Run a read-only pilot on the 14 bibliography occurrences, the 32 folio leads, and a sample of scoped subjects and genres. Record how many cases pass the authority, scope, and destination creator checks. Use those rates to estimate how much of each group can contribute to the 2,616-case gap.

At the current denominator, if the 576 “Judaism” cases and all 461 catalogue-note cases stay open, the ceiling is 3,165 of 4,202 (75.32%). Even that ceiling assumes that every other case passes. A realistic plan needs verified subject scope and a clear policy for catalogue evidence. Keep a documented review outcome separate from a verified live statement. Show the effect of any approved denominator change separately from repairs.

Before each edit, confirm the first creator of the destination item and the current revision. Use the same source version and case definitions for the final measurement. Report the verified cases, proof still needed, and targets that other accounts created. The ownership pilot used public reads only. [Creator-only writes](../architecture/blocks/wikidata-studio/creator-only-writes.md)
