# Live Wikidata created-item defects

This report covers every item job `8dbc4090` **created** on [www.wikidata.org](https://www.wikidata.org).
It does not list the 56 items that failed, skipped, or blocked (those items have no live Q-id from this job).

- Run: `48ba6c13-115c-4763-bff1-c08b9031b518`
- Job: `8dbc4090-35f2-47b0-93bd-d9eeb13e65a7` (`upload_target=live`)
- Started: `2026-08-25 13:15:16+00:00`
- Finished: `2026-08-25 13:58:47+00:00`
- Created items read back: **177**
- Items with at least one defect: **45**
- Items with no listed defect: **132**

## Method

The audit loaded Studio natives from the canonical approved cache.
It fetched every created Q-id with `wbgetentities`.
It compared labels, descriptions, and mainsnak claims.
Quantity amounts compare as numbers. Time strings use `repair_wikidata_time`.
Unresolved `__LOCAL:` values mean Step 2 did not attach the link.
Work title hits use CirrusSearch `inlabel`. A hit is a candidate, not a merge.
Short titles such as מנהגים or תשב"ץ match many unrelated items.
The audit does not treat a thin work (P31 + P1476 + P2888) as a defect when those natives landed.

## Summary by defect class

- Unresolved local links: **31** manuscripts (P1574 / P3342 still `__LOCAL:`)
- Possible work-title duplicates: **12** works (5 look like the same literary work; 7 look like title collisions)
- Implausible dimension: **1** manuscript (P2049 = 5180 mm)
- Identifier probe unavailable: **1** person (P8189 DNS error during the audit)

### Likely same-work duplicates

These created works share a title with an older item that is a prayer or a known Hebrew book:

- [אב הרחמים](https://www.wikidata.org/wiki/Q141175480) → [Q2873224](https://www.wikidata.org/wiki/Q2873224) Av HaRachamim
- [יקום פורקן](https://www.wikidata.org/wiki/Q141175483) → [Q7060749](https://www.wikidata.org/wiki/Q7060749)
- [בחינת עולם](https://www.wikidata.org/wiki/Q141175541) → [Q140051042](https://www.wikidata.org/wiki/Q140051042)
- [מבוא התלמוד](https://www.wikidata.org/wiki/Q141175553) → [Q42192934](https://www.wikidata.org/wiki/Q42192934) Mevo ha-Talmud
- [ספר המעלות](https://www.wikidata.org/wiki/Q141175569) → [Q42195421](https://www.wikidata.org/wiki/Q42195421) Sefer ha-ma'alot

## Entity list

### 1. 69 A 1756

- Type: `manuscript` (`printed_facsimile`)
- Local id: `QDraft_MS_990019020880205171`
- Live item: [Q141175898](https://www.wikidata.org/wiki/Q141175898)
- Claim counts: live 12 / native 12
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:פנקס_המדינה_:_או_פנקס_ועד_הקהלות_הראשיות_במדינת_ליטא;_קובץ_תקנות_ופסקים_משנת_שפ_ג_עד_שנת_תקכ_א_נדפס_מכתב-יד_הנמצא_בהורודנא_עם_מלואים_ושנויי_נוסחאות_על_פי_העתקות_הפנקס_בבריסק_ובווילנא

### 2. Bodleian Library, F 16236

- Type: `manuscript`
- Local id: `QDraft_MS_990000880550205171`
- Live item: [Q141175777](https://www.wikidata.org/wiki/Q141175777)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_______by_6

### 3. Braginsky Collection, F 41164

- Type: `manuscript`
- Local id: `QDraft_MS_990001882630205171`
- Live item: [Q141175893](https://www.wikidata.org/wiki/Q141175893)
- Claim counts: live 13 / native 13
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_________by_2

### 4. British Library, F 12362

- Type: `manuscript`
- Local id: `QDraft_MS_990001205840205171`
- Live item: [Q141175794](https://www.wikidata.org/wiki/Q141175794)
- Claim counts: live 17 / native 18
- Defects:
  - Unresolved local links: **4** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:תרגום_אונקלוס_לתורה
    - P1574 → __LOCAL:work:תרגום_רס_ג_לתורה
    - P1574 → __LOCAL:work:פרוש_רש_י
    - P1574 → __LOCAL:work:מחברת_התיג'אן

### 5. British Library, F 5784

- Type: `manuscript`
- Local id: `QDraft_MS_990001056990205171`
- Live item: [Q141175787](https://www.wikidata.org/wiki/Q141175787)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work___________by_4

### 6. British Library, F 8013

- Type: `manuscript`
- Local id: `QDraft_MS_990001254240205171`
- Live item: [Q141175798](https://www.wikidata.org/wiki/Q141175798)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:קול_ברמה

### 7. Columbia University Libraries, F 40316

- Type: `manuscript`
- Local id: `QDraft_MS_990001948980205171`
- Live item: [Q141175896](https://www.wikidata.org/wiki/Q141175896)
- Claim counts: live 13 / native 13
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:דרשות

### 8. F 32325

- Type: `manuscript`
- Local id: `QDraft_MS_990001827870205171`
- Live item: [Q141175885](https://www.wikidata.org/wiki/Q141175885)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____________by

### 9. F 39766

- Type: `manuscript`
- Local id: `QDraft_MS_990001875220205171`
- Live item: [Q141175891](https://www.wikidata.org/wiki/Q141175891)
- Claim counts: live 12 / native 12
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_________by_4

### 10. F 9900

- Type: `manuscript`
- Local id: `QDraft_MS_990001286970205171`
- Live item: [Q141175799](https://www.wikidata.org/wiki/Q141175799)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_______by_6

### 11. Hebrew Union College – Jewish Institute of Religion, F 18187

- Type: `manuscript`
- Local id: `QDraft_MS_990001089360205171`
- Live item: [Q141175788](https://www.wikidata.org/wiki/Q141175788)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:סדר_משמרת_החדש

### 12. Hungarian Academy of Sciences Library and Information Centre, F 12658

- Type: `manuscript`
- Local id: `QDraft_MS_990001915930205171`
- Live item: [Q141175895](https://www.wikidata.org/wiki/Q141175895)
- Claim counts: live 16 / native 16
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:דרושים

### 13. Institute of Oriental Manuscripts of the Russian Academy of Sciences, F 46247

- Type: `manuscript`
- Local id: `QDraft_MS_990000825080205171`
- Live item: [Q141175769](https://www.wikidata.org/wiki/Q141175769)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_________by_3

### 14. Institute of Oriental Manuscripts of the Russian Academy of Sciences, F 69317

- Type: `manuscript`
- Local id: `QDraft_MS_990000864590205171`
- Live item: [Q141175776](https://www.wikidata.org/wiki/Q141175776)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____by_4

### 15. Israel Museum, F 32638

- Type: `manuscript`
- Local id: `QDraft_MS_990001801390205171`
- Live item: [Q141175884](https://www.wikidata.org/wiki/Q141175884)
- Claim counts: live 44 / native 52
- Defects:
  - Unresolved local links: **34** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:כתובים_(אמ_ת)_עם_ניקוד_טעמים_ומסורה_קטנה
    - P1574 → __LOCAL:work:תשב_ץ
    - P1574 → __LOCAL:work:סדור_מנהג_אשכנז_לכל_השנה
    - P1574 → __LOCAL:work:ספר_היראה
    - P1574 → __LOCAL:work:מנהגים
    - P1574 → __LOCAL:work:משנה_תורה_(ספר_זמנים_הלכות_חמץ_ומצה)
    - P1574 → __LOCAL:work:ספר_הקבלה
    - P1574 → __LOCAL:work:מבוא_התלמוד
    - (list truncated; remaining `__LOCAL:` values stay on the native)

### 16. Jewish Historical Institute, F 10117

- Type: `manuscript`
- Local id: `QDraft_MS_990000856010205171`
- Live item: [Q141175772](https://www.wikidata.org/wiki/Q141175772)
- Claim counts: live 25 / native 25
- Defects:
  - Unresolved local links: **5** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:סדר_קריאת_השבוע
    - P1574 → __LOCAL:work:סדר_קריאת_השבתות
    - P1574 → __LOCAL:QDraft_Work_23
    - P1574 → __LOCAL:QDraft_Work
    - P3342 → __LOCAL:mazal:987007313624205171

### 17. Jewish Theological Seminary Library, F 10792

- Type: `manuscript`
- Local id: `QDraft_MS_990001039720205171`
- Live item: [Q141175785](https://www.wikidata.org/wiki/Q141175785)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:כוונות_התפילה_לכל_השנה_עפ_י_קבלת_האר_י

### 18. Jewish Theological Seminary Library, F 24116

- Type: `manuscript`
- Local id: `QDraft_MS_990001028160205171`
- Live item: [Q141175783](https://www.wikidata.org/wiki/Q141175783)
- Claim counts: live 15 / native 15
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work___________by_2

### 19. Jewish Theological Seminary Library, F 42405

- Type: `manuscript`
- Local id: `QDraft_MS_990001135400205171`
- Live item: [Q141175790](https://www.wikidata.org/wiki/Q141175790)
- Claim counts: live 13 / native 13
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_________by

### 20. Jewish Theological Seminary Library, Mss-D 12909

- Type: `manuscript`
- Local id: `QDraft_MS_990038692590205171`
- Live item: [Q141175902](https://www.wikidata.org/wiki/Q141175902)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_______________by_2

### 21. Manfred and Anne Lehmann Foundation, F 72735

- Type: `manuscript`
- Local id: `QDraft_MS_990001878130205171`
- Live item: [Q141175892](https://www.wikidata.org/wiki/Q141175892)
- Claim counts: live 12 / native 12
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_______by_2

### 22. National Library of Israel, Ms. Heb. 197/11=4

- Type: `manuscript`
- Local id: `QDraft_MS_990025903200205171`
- Live item: [Q141175899](https://www.wikidata.org/wiki/Q141175899)
- Claim counts: live 15 / native 15
- Defects:
  - P2049 amount 5180 mm is larger than 1200 mm. Check MARC 300 before you treat this as a physical size.

### 23. National Library of Israel, Ms. Heb. 6720=4

- Type: `manuscript`
- Local id: `QDraft_MS_990000464110205171`
- Live item: [Q141175738](https://www.wikidata.org/wiki/Q141175738)
- Claim counts: live 16 / native 16
- Defects:
  - Unresolved local links: **4** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_71
    - P1574 → __LOCAL:work:ענף_שני_מפרי_עץ_החיים_שער_המצות
    - P1574 → __LOCAL:work:ענף_הג'_פע_ח_והוא_תיקוני_עוונות
    - P1574 → __LOCAL:work:שער_הנבואה_ורוח_הקדש_והייחודים

### 24. National Library of Israel, Ms. Heb. 6847=8

- Type: `manuscript`
- Local id: `QDraft_MS_990000403370205171`
- Live item: [Q141175735](https://www.wikidata.org/wiki/Q141175735)
- Claim counts: live 17 / native 17
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:שער_שברי_לוחות_:_פירוש_המסורת_אשר_חבר_הרב_ר'_אלי'_המדקדק_ז_ל_ה_ה_מבאר_בו_כל_ראשי_תיבות_והמילות_זרות_אשר_במסורה_קטנה_וקרא_שמו_שער_שברי_לוחות_יען_כי_בו_יתבארו_כל_מלות_זרות_ותיבות_קצרות_וחסרות_ושבורות_הנמצאות_בגליונות

### 25. National Library of Israel, Ms. Heb. 7516=28

- Type: `manuscript`
- Local id: `QDraft_MS_990000439040205171`
- Live item: [Q141175736](https://www.wikidata.org/wiki/Q141175736)
- Claim counts: live 17 / native 17
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_______________________________by

### 26. Private Collection, F 71971

- Type: `manuscript`
- Local id: `QDraft_MS_990001580110205171`
- Live item: [Q141175810](https://www.wikidata.org/wiki/Q141175810)
- Claim counts: live 12 / native 12
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____by_2

### 27. Russian State Library, F 28004

- Type: `manuscript`
- Local id: `QDraft_MS_990000776020205171`
- Live item: [Q141175745](https://www.wikidata.org/wiki/Q141175745)
- Claim counts: live 13 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____by_9

### 28. Russian State Library, F 48316

- Type: `manuscript`
- Local id: `QDraft_MS_990000759620205171`
- Live item: [Q141175744](https://www.wikidata.org/wiki/Q141175744)
- Claim counts: live 13 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____by_7

### 29. San Francisco State University, F 34601

- Type: `manuscript`
- Local id: `QDraft_MS_990001901440205171`
- Live item: [Q141175894](https://www.wikidata.org/wiki/Q141175894)
- Claim counts: live 13 / native 13
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_______by

### 30. University Library Johann Christian Senckenberg, F 10590

- Type: `manuscript`
- Local id: `QDraft_MS_990001379460205171`
- Live item: [Q141175804](https://www.wikidata.org/wiki/Q141175804)
- Claim counts: live 13 / native 13
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____by_3

### 31. University Library Johann Christian Senckenberg, F 4208

- Type: `manuscript`
- Local id: `QDraft_MS_990001406710205171`
- Live item: [Q141175808](https://www.wikidata.org/wiki/Q141175808)
- Claim counts: live 14 / native 14
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:QDraft_Work_____by

### 32. University of Leeds Libraries, F 15443

- Type: `manuscript`
- Local id: `QDraft_MS_990001858880205171`
- Live item: [Q141175888](https://www.wikidata.org/wiki/Q141175888)
- Claim counts: live 15 / native 15
- Defects:
  - Unresolved local links: **1** (Step 2 did not attach them).
    - P1574 → __LOCAL:work:תקנות_חברת_בקור_חולים_בוירונה

### 33. אב הרחמים

- Type: `work`
- Local id: `QDraft_Work`
- Live item: [Q141175480](https://www.wikidata.org/wiki/Q141175480)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [אב הרחמים](https://www.wikidata.org/wiki/Q2873224) (`Q2873224`) — Jewish memorial prayer [likely same work]

### 34. בחינת עולם

- Type: `work`
- Local id: `work:בחינת_עולם`
- Live item: [Q141175541](https://www.wikidata.org/wiki/Q141175541)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [בחינת עולם](https://www.wikidata.org/wiki/Q140051042) (`Q140051042`) — חיבור של ידעיה הפניני [likely same work]

### 35. דרושים

- Type: `work`
- Local id: `work:דרושים`
- Live item: [Q141175543](https://www.wikidata.org/wiki/Q141175543)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [מודעה](https://www.wikidata.org/wiki/Q614278) (`Q614278`) — form of advertising which is particularly common in newspapers, online and other periodicals [title collision (probably not the same entity)]
    - [מודעת דרושים](https://www.wikidata.org/wiki/Q356251) (`Q356251`) — advertisement to recruit job candidates [title collision (probably not the same entity)]
    - [דרישת הזאב : דרושים להתעוררות אל התשובה תפילה וצדקה](https://www.wikidata.org/wiki/Q126878898) (`Q126878898`) [title collision (probably not the same entity)]
    - [פורטל דרושים](https://www.wikidata.org/wiki/Q16129812) (`Q16129812`) — אתר אינטרנט ישראלי [title collision (probably not the same entity)]

### 36. דרשות

- Type: `work`
- Local id: `work:דרשות`
- Live item: [Q141175544](https://www.wikidata.org/wiki/Q141175544)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [ר"ן](https://www.wikidata.org/wiki/Q723531) (`Q723531`) — 14th century Talmudist and authority on Jewish law [title collision (probably not the same entity)]
    - [דרשות אורגניה](https://www.wikidata.org/wiki/Q154800) (`Q154800`) — first literary work in catalan [title collision (probably not the same entity)]
    - [Q9732163](https://www.wikidata.org/wiki/Q9732163) (`Q9732163`) — Wikimedia category [title collision (probably not the same entity)]
    - [חוט המשולש (דרשות)](https://www.wikidata.org/wiki/Q25636591) (`Q25636591`) — ספר דרשות [title collision (probably not the same entity)]
    - [דרשות הר"ן](https://www.wikidata.org/wiki/Q106960007) (`Q106960007`) — חיבורו של רבי ניסים בן ראובן גירונדי (הר"ן) [title collision (probably not the same entity)]

### 37. יקום פורקן

- Type: `work`
- Local id: `QDraft_Work_23`
- Live item: [Q141175483](https://www.wikidata.org/wiki/Q141175483)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [יקום פורקן](https://www.wikidata.org/wiki/Q7060749) (`Q7060749`) — aramaic prayers for Sabbath service [likely same work]

### 38. כתר תורה

- Type: `work`
- Local id: `QDraft_Work_____by_4`
- Live item: [Q141175531](https://www.wikidata.org/wiki/Q141175531)
- Claim counts: live 4 / native 4
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [כתר תורה](https://www.wikidata.org/wiki/Q91947877) (`Q91947877`) — Torah crown (307702) [title collision (probably not the same entity)]
    - [כתר תורה הנושא כתובת הקדשה בחרוזים](https://www.wikidata.org/wiki/Q91945363) (`Q91945363`) — 1857, 253080 Israel-Museum [title collision (probably not the same entity)]
    - [כתר תורה עם רימונים](https://www.wikidata.org/wiki/Q91922197) (`Q91922197`) — 281499, 1910, Israel Museum [title collision (probably not the same entity)]
    - [כתר תורה](https://www.wikidata.org/wiki/Q91947900) (`Q91947900`) — Torah crown 255135, Israel-Museum [title collision (probably not the same entity)]
    - [כתר תורה עם סמלי המזלות](https://www.wikidata.org/wiki/Q91918329) (`Q91918329`) — 281568, 1807 Israel Museum [title collision (probably not the same entity)]
    - [כתר תורה](https://www.wikidata.org/wiki/Q91924919) (`Q91924919`) — 1851, 253101 Israel-Museum [title collision (probably not the same entity)]
    - [כתר תורה מעוטר בידיים בתנוחת ברכת הכוהנים](https://www.wikidata.org/wiki/Q91917620) (`Q91917620`) — 1793, 254678 Israel-Museum [title collision (probably not the same entity)]
    - [כתר תורה עם פעמונים כדוריים](https://www.wikidata.org/wiki/Q91932371) (`Q91932371`) — 1818, 253100 Israel-Museum [title collision (probably not the same entity)]

### 39. לוח התקופות

- Type: `work`
- Local id: `work:לוח_התקופות`
- Live item: [Q141175550](https://www.wikidata.org/wiki/Q141175550)
- Claim counts: live 2 / native 2
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [לוח התקופות בארץ ישראל](https://www.wikidata.org/wiki/Q7805421) (`Q7805421`) — Wikimedia list article [title collision (probably not the same entity)]

### 40. מבוא התלמוד

- Type: `work`
- Local id: `work:מבוא_התלמוד`
- Live item: [Q141175553](https://www.wikidata.org/wiki/Q141175553)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [מבוא התלמוד](https://www.wikidata.org/wiki/Q42192934) (`Q42192934`) — חיבור בנושא כללי התלמוד שיוחס לשמואל הנגיד [likely same work]

### 41. מנהגים

- Type: `work`
- Local id: `work:מנהגים`
- Live item: [Q141175556](https://www.wikidata.org/wiki/Q141175556)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [מנהגים](https://www.wikidata.org/wiki/Q251777) (`Q251777`) — set of agreed, stipulated, or generally accepted standards, norms, social norms, or criteria [title collision (probably not the same entity)]
    - [מנהג יהודי](https://www.wikidata.org/wiki/Q1628649) (`Q1628649`) — an accepted tradition or group of traditions in Judaism [title collision (probably not the same entity)]
    - [קטגוריה:מנהגים](https://www.wikidata.org/wiki/Q7584980) (`Q7584980`) — Wikimedia category [title collision (probably not the same entity)]
    - [ספרי מנהג](https://www.wikidata.org/wiki/Q94156988) (`Q94156988`) — ספרות העוסקת בענייני מנהגים [title collision (probably not the same entity)]
    - [Category:Jewish law and rituals](https://www.wikidata.org/wiki/Q6916093) (`Q6916093`) — Wikimedia category [title collision (probably not the same entity)]
    - [קטגוריה:מנהגים יהודיים: חגים](https://www.wikidata.org/wiki/Q8566401) (`Q8566401`) — Wikimedia category [title collision (probably not the same entity)]
    - [קטגוריה:מנהגים רומניים](https://www.wikidata.org/wiki/Q8681785) (`Q8681785`) — Wikimedia category [title collision (probably not the same entity)]
    - [קטגוריה:ספרי מנהגים](https://www.wikidata.org/wiki/Q94156968) (`Q94156968`) — Wikimedia category [title collision (probably not the same entity)]

### 42. ספר המעלות

- Type: `work`
- Local id: `work:ספר_המעלות`
- Live item: [Q141175569](https://www.wikidata.org/wiki/Q141175569)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [ספר המעלות](https://www.wikidata.org/wiki/Q42195421) (`Q42195421`) — חיבור מאת שם טוב בן יוסף אבן פלקירה [likely same work]
    - [ספר המעלות לדרגות ימות המשיח](https://www.wikidata.org/wiki/Q25493504) (`Q25493504`) — ספר מחשבה של חכם יהודי-תימני [title collision (probably not the same entity)]

### 43. קול ברמה

- Type: `work`
- Local id: `work:קול_ברמה`
- Live item: [Q141175581](https://www.wikidata.org/wiki/Q141175581)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [רדיו קול ברמה](https://www.wikidata.org/wiki/Q6426924) (`Q6426924`) — radio station in Israel [title collision (probably not the same entity)]
    - [פסק דין רדיו "קול ברמה" נגד "קולך"](https://www.wikidata.org/wiki/Q55647599) (`Q55647599`) [title collision (probably not the same entity)]
    - [Q25636372](https://www.wikidata.org/wiki/Q25636372) (`Q25636372`) [title collision (probably not the same entity)]
    - [קטגוריה:סגל רדיו קול ברמה](https://www.wikidata.org/wiki/Q10152726) (`Q10152726`) — Wikimedia category [title collision (probably not the same entity)]

### 44. תשב"ץ

- Type: `work`
- Local id: `work:תשב_ץ`
- Live item: [Q141175592](https://www.wikidata.org/wiki/Q141175592)
- Claim counts: live 3 / native 3
- Defects:
  - Possible live duplicate (title search). Candidates:
    - [תשבץ](https://www.wikidata.org/wiki/Q83207) (`Q83207`) — word puzzle game [title collision (probably not the same entity)]
    - [תשבץ היגיון](https://www.wikidata.org/wiki/Q934140) (`Q934140`) — crossword puzzle in which each clue is a word puzzle in and of itself [title collision (probably not the same entity)]
    - [תשבץ שלד](https://www.wikidata.org/wiki/Q5448778) (`Q5448778`) — crossword-like puzzle in which a list of words is given (but not their positions in the grid), and one must place them into the grid [title collision (probably not the same entity)]
    - [תשבץ פסיפס](https://www.wikidata.org/wiki/Q6670843) (`Q6670843`) [title collision (probably not the same entity)]
    - [תשבץ תלת-מישורי](https://www.wikidata.org/wiki/Q25489005) (`Q25489005`) [title collision (probably not the same entity)]
    - [תשבץ מוצפן](https://www.wikidata.org/wiki/Q6623744) (`Q6623744`) [title collision (probably not the same entity)]
    - [תשבץ מספרים](https://www.wikidata.org/wiki/Q490544) (`Q490544`) — number puzzle [title collision (probably not the same entity)]
    - [תשבץ לבן](https://www.wikidata.org/wiki/Q6585151) (`Q6585151`) — תשבץ ללא סימון הפרדות בין פתרונות ההגדרות [title collision (probably not the same entity)]

### 45. Simcha H. Benyosef

- Type: `person`
- Local id: `QDraft_Person_205`
- Live item: [Q141175661](https://www.wikidata.org/wiki/Q141175661)
- Claim counts: live 4 / native 4
- Defects:
  - P8189 duplicate probe was unavailable.

## Items with no listed defect

132 created items matched labels, descriptions, and non-local claims. The duplicate probe did not return another live Q-id for those items.

- `manuscript`: 28
- `person`: 52
- `work`: 52

Compact live Q-ids with no listed defect:

- `manuscript` [Q141175809](https://www.wikidata.org/wiki/Q141175809) — Alliance Israélite Universelle, F 3238
- `manuscript` [Q141175778](https://www.wikidata.org/wiki/Q141175778) — Bodleian Library, F 16247
- `manuscript` [Q141175796](https://www.wikidata.org/wiki/Q141175796) — British Library, F 6072
- `manuscript` [Q141175740](https://www.wikidata.org/wiki/Q141175740) — British Library, F 8298
- `manuscript` [Q141175806](https://www.wikidata.org/wiki/Q141175806) — Cambridge University Library, F 18702
- `manuscript` [Q141175807](https://www.wikidata.org/wiki/Q141175807) — Cambridge University Library, F 18760
- `manuscript` [Q141175801](https://www.wikidata.org/wiki/Q141175801) — Columbia University Libraries, F 52502
- `manuscript` [Q141175793](https://www.wikidata.org/wiki/Q141175793) — F 22325
- `manuscript` [Q141175883](https://www.wikidata.org/wiki/Q141175883) — F 31646
- `manuscript` [Q141175742](https://www.wikidata.org/wiki/Q141175742) — F 46266
- `manuscript` [Q141175800](https://www.wikidata.org/wiki/Q141175800) — F 9342
- `manuscript` [Q141175906](https://www.wikidata.org/wiki/Q141175906) — Hebrew manuscript, Archives of the Jewish People, 997009236549805171
- `manuscript` [Q141175900](https://www.wikidata.org/wiki/Q141175900) — Jewish Theological Seminary Library, F 2557
- `manuscript` [Q141175789](https://www.wikidata.org/wiki/Q141175789) — Jewish Theological Seminary Library, F 43169
- `manuscript` [Q141175791](https://www.wikidata.org/wiki/Q141175791) — Jewish Theological Seminary Library, F 43271
- `manuscript` [Q141175795](https://www.wikidata.org/wiki/Q141175795) — Jewish Theological Seminary Library, F 49705
- `manuscript` [Q141175743](https://www.wikidata.org/wiki/Q141175743) — Jewish Theological Seminary Library, F 49930
- `manuscript` [Q141175901](https://www.wikidata.org/wiki/Q141175901) — Jewish Theological Seminary Library, Mss-D 3302
- `manuscript` [Q141175886](https://www.wikidata.org/wiki/Q141175886) — Klagsbald, Victor, F 7265
- `manuscript` [Q141175805](https://www.wikidata.org/wiki/Q141175805) — Latin manuscript, Cambridge University Library, 990001400870205171
- `manuscript` [Q141175880](https://www.wikidata.org/wiki/Q141175880) — National University Library of Turin, F 34404
- `manuscript` [Q141175770](https://www.wikidata.org/wiki/Q141175770) — Russian State Library, F 47961
- `manuscript` [Q141175773](https://www.wikidata.org/wiki/Q141175773) — Russian State Library, F 6713
- `manuscript` [Q141175739](https://www.wikidata.org/wiki/Q141175739) — The Ben Zvi Institute, F 27150
- `manuscript` [Q141175792](https://www.wikidata.org/wiki/Q141175792) — The Ben Zvi Institute, F 37883
- `manuscript` [Q141175882](https://www.wikidata.org/wiki/Q141175882) — University College London, F 14776
- `manuscript` [Q141175802](https://www.wikidata.org/wiki/Q141175802) — University Library Johann Christian Senckenberg, F 30328
- `manuscript` [Q141175887](https://www.wikidata.org/wiki/Q141175887) — University of Leeds Libraries, F 15262
- `work` [Q141175537](https://www.wikidata.org/wiki/Q141175537) — אגרת אל חכמי מונפישליר
- `work` [Q141175538](https://www.wikidata.org/wiki/Q141175538) — אגרת המוסר הכללית מיוחס לאריסטו
- `work` [Q141175528](https://www.wikidata.org/wiki/Q141175528) — אוצרות חיים
- `work` [Q141175529](https://www.wikidata.org/wiki/Q141175529) — אור הרופאים
- `work` [Q141175542](https://www.wikidata.org/wiki/Q141175542) — בן המלך והנזיר מתורגם על ידי אברהם הלוי בן חסדאי
- `work` [Q141175530](https://www.wikidata.org/wiki/Q141175530) — חזוק אמונה
- `work` [Q141175545](https://www.wikidata.org/wiki/Q141175545) — חלוקים שבין בני ארץ ישראל ובין בני בבל
- `work` [Q141175546](https://www.wikidata.org/wiki/Q141175546) — כוונות התפילה לכל השנה עפ"י קבלת האר"י
- `work` [Q141175547](https://www.wikidata.org/wiki/Q141175547) — כתב התנצלות
- `work` [Q141175548](https://www.wikidata.org/wiki/Q141175548) — כתובים (אמ"ת) עם ניקוד טעמים ומסורה קטנה
- `work` [Q141175520](https://www.wikidata.org/wiki/Q141175520) — לוח מאמרי עין ישראל
- `work` [Q141175551](https://www.wikidata.org/wiki/Q141175551) — מאמר על המשקלות והמדות
- `work` [Q141175552](https://www.wikidata.org/wiki/Q141175552) — מאמרי חז"ל הפותחים בג' דברים ד' דברים וכו
- `work` [Q141175532](https://www.wikidata.org/wiki/Q141175532) — מבוא שערים
- `work` [Q141175535](https://www.wikidata.org/wiki/Q141175535) — מגן אהרן
- `work` [Q141175524](https://www.wikidata.org/wiki/Q141175524) — מדרש הגדול (דברים)
- `work` [Q141175525](https://www.wikidata.org/wiki/Q141175525) — מדרש הגדול (שמות)
- `work` [Q141175554](https://www.wikidata.org/wiki/Q141175554) — מוסרי הפילוסופים
- `work` [Q141175521](https://www.wikidata.org/wiki/Q141175521) — מלאכת שלמה (סדר זרעים)
- `work` [Q141175558](https://www.wikidata.org/wiki/Q141175558) — מנחת יהודה : פרוש על שמואל, מלכים וישעיהו
- `work` [Q141175560](https://www.wikidata.org/wiki/Q141175560) — מעשה חירם
- `work` [Q141175561](https://www.wikidata.org/wiki/Q141175561) — משל הקדמוני
- `work` [Q141175514](https://www.wikidata.org/wiki/Q141175514) — משנה תורה (ספר הפלאה, זרעים, עבודה, קרבנות)
- `work` [Q141175516](https://www.wikidata.org/wiki/Q141175516) — משנה תורה (ספר מדע, אהבה, זמנים)
- `work` [Q141175562](https://www.wikidata.org/wiki/Q141175562) — משפט חבוט הקבר
- `work` [Q141175564](https://www.wikidata.org/wiki/Q141175564) — סדור מנהג אשכנז לכל השנה
- `work` [Q141175565](https://www.wikidata.org/wiki/Q141175565) — סדר משמרת החדש
- `work` [Q141175566](https://www.wikidata.org/wiki/Q141175566) — סדר קריאת השבוע
- `work` [Q141175567](https://www.wikidata.org/wiki/Q141175567) — סדר קריאת השבתות
- `work` [Q141175568](https://www.wikidata.org/wiki/Q141175568) — סוד הסודות מיוחס לאריסטו
- `work` [Q141175522](https://www.wikidata.org/wiki/Q141175522) — ספר המבחר : פרוש התורה
- `work` [Q141175571](https://www.wikidata.org/wiki/Q141175571) — ענף הג' פע"ח והוא תיקוני עוונות
- `work` [Q141175572](https://www.wikidata.org/wiki/Q141175572) — ענף שני מפרי עץ החיים, שער המצות
- `work` [Q141175573](https://www.wikidata.org/wiki/Q141175573) — פיוטים לפורים ולפסח
- `work` [Q141175575](https://www.wikidata.org/wiki/Q141175575) — פנקס המדינה : או פנקס ועד הקהלות הראשיות במדינת ליטא; קובץ תקנות ופסקים משנת שפ"ג עד שנת תקכ"א, נדפס מכתב-יד הנמצא בהורודנא, עם מלואים ושנויי נוסחאות על פי העתקות הפנקס בבריסק ובווילנא
- `work` [Q141175512](https://www.wikidata.org/wiki/Q141175512) — פסק דין : פסק דין מרבני קושטא ר' שמעון ן' חביב ור' משה בנבנשת, להתרת עגונה
- `work` [Q141175576](https://www.wikidata.org/wiki/Q141175576) — פסקי ר' יצחק מקורביל
- `work` [Q141175517](https://www.wikidata.org/wiki/Q141175517) — פרוש התורה לבחיי בן אשר
- `work` [Q141175577](https://www.wikidata.org/wiki/Q141175577) — פרוש רש"י
- `work` [Q141175501](https://www.wikidata.org/wiki/Q141175501) — פרי עץ חיים ענף ראשון
- `work` [Q141175578](https://www.wikidata.org/wiki/Q141175578) — צוואת יהודה החסיד מרגנשבורג
- `work` [Q141175579](https://www.wikidata.org/wiki/Q141175579) — קביעות השנים
- `work` [Q141175582](https://www.wikidata.org/wiki/Q141175582) — קערת כסף
- `work` [Q141175523](https://www.wikidata.org/wiki/Q141175523) — שלחן ערוך (ארח חיים)
- `work` [Q141175584](https://www.wikidata.org/wiki/Q141175584) — שער הנבואה ורוח הקדש והייחודים
- `work` [Q141175586](https://www.wikidata.org/wiki/Q141175586) — שער שברי לוחות : פירוש המסורת אשר חבר הרב ר' אלי' המדקדק ז"ל, ה"ה מבאר בו כל ראשי תיבות והמילות זרות אשר במסורה קטנה, וקרא שמו שער שברי לוחות יען כי בו יתבארו כל מלות זרות ותיבות קצרות וחסרות ושבורות הנמצאות בגליונות
- `work` [Q141175519](https://www.wikidata.org/wiki/Q141175519) — תחלת דבר : חבור בחכמת ההגיון
- `work` [Q141175588](https://www.wikidata.org/wiki/Q141175588) — תקנות חברת בקור חולים בוירונה
- `work` [Q141175589](https://www.wikidata.org/wiki/Q141175589) — תקנות רבנו גרשם מאור הגולה
- `work` [Q141175590](https://www.wikidata.org/wiki/Q141175590) — תרגום אונקלוס לתורה
- `work` [Q141175527](https://www.wikidata.org/wiki/Q141175527) — תרגום ערבי לתורה
- `work` [Q141175591](https://www.wikidata.org/wiki/Q141175591) — תרגום רס"ג לתורה
- `person` [Q141175733](https://www.wikidata.org/wiki/Q141175733) — Camillo Jagel
- `person` [Q141175714](https://www.wikidata.org/wiki/Q141175714) — Joseph Sänger
- `person` [Q141175734](https://www.wikidata.org/wiki/Q141175734) — Malkiel Kaisey
- `person` [Q141175604](https://www.wikidata.org/wiki/Q141175604) — Netanel Segal
- `person` [Q141175660](https://www.wikidata.org/wiki/Q141175660) — Refaʼel Avraham Ibn Asher
- `person` [Q141175699](https://www.wikidata.org/wiki/Q141175699) — Shimshon Baḳ
- `person` [Q141175726](https://www.wikidata.org/wiki/Q141175726) — Yahuda Mas'ud
- `person` [Q141175656](https://www.wikidata.org/wiki/Q141175656) — אברהם בן דוד פרובנצלו
- `person` [Q141175713](https://www.wikidata.org/wiki/Q141175713) — אברהם ונטורה
- `person` [Q141175594](https://www.wikidata.org/wiki/Q141175594) — אליהו בן משה כרמי
- `person` [Q141175708](https://www.wikidata.org/wiki/Q141175708) — אליהו דלפוגיט
- `person` [Q141175694](https://www.wikidata.org/wiki/Q141175694) — אליעזר מצליח
- `person` [Q141175695](https://www.wikidata.org/wiki/Q141175695) — אליקום בן משה
- `person` [Q141175715](https://www.wikidata.org/wiki/Q141175715) — ברוך אבן חביב
- `person` [Q141175716](https://www.wikidata.org/wiki/Q141175716) — גיילה יחיא בן מעוצ'ה חבני
- `person` [Q141175704](https://www.wikidata.org/wiki/Q141175704) — חיא גבריאל
- `person` [Q141175664](https://www.wikidata.org/wiki/Q141175664) — חיים בן יחיא אלטירי
- `person` [Q141175718](https://www.wikidata.org/wiki/Q141175718) — חנניה בן אפרים
- `person` [Q141175597](https://www.wikidata.org/wiki/Q141175597) — ידידיה בן אברהם מונדיני
- `person` [Q141175697](https://www.wikidata.org/wiki/Q141175697) — יהודה בן סעיד אלשיך
- `person` [Q141175703](https://www.wikidata.org/wiki/Q141175703) — יהודה גביזון
- `person` [Q141175701](https://www.wikidata.org/wiki/Q141175701) — יהונתן גאלנטי
- `person` [Q141175598](https://www.wikidata.org/wiki/Q141175598) — יוסף בן מרדכי מלינובסקי
- `person` [Q141175727](https://www.wikidata.org/wiki/Q141175727) — יוסף בן סעדיה
- `person` [Q141175696](https://www.wikidata.org/wiki/Q141175696) — יוסף בן עמרם בן עודד אלעדוי
- `person` [Q141175705](https://www.wikidata.org/wiki/Q141175705) — יוסף גדיגו
- `person` [Q141175731](https://www.wikidata.org/wiki/Q141175731) — יוסף הכהן
- `person` [Q141175721](https://www.wikidata.org/wiki/Q141175721) — יוסף טיטצק
- `person` [Q141175732](https://www.wikidata.org/wiki/Q141175732) — יוסף יוזלן
- `person` [Q141175602](https://www.wikidata.org/wiki/Q141175602) — יוסף סגיש
- `person` [Q141175648](https://www.wikidata.org/wiki/Q141175648) — יוסף סולל
- `person` [Q141175654](https://www.wikidata.org/wiki/Q141175654) — יוסף פינטו
- `person` [Q141175700](https://www.wikidata.org/wiki/Q141175700) — יחיא בן אברהם בשירי
- `person` [Q141175653](https://www.wikidata.org/wiki/Q141175653) — יחיא בן סאלם עראקי
- `person` [Q141175595](https://www.wikidata.org/wiki/Q141175595) — יצחק בן לוב
- `person` [Q141175593](https://www.wikidata.org/wiki/Q141175593) — ישועה אזולאי
- `person` [Q141175706](https://www.wikidata.org/wiki/Q141175706) — מאיר גורבתו
- `person` [Q141175711](https://www.wikidata.org/wiki/Q141175711) — מוסי עיאל בן דאוד דמרמרי
- `person` [Q141175658](https://www.wikidata.org/wiki/Q141175658) — מלכיאל קזני
- `person` [Q141175605](https://www.wikidata.org/wiki/Q141175605) — מעודד בן שלום אלגהסי
- `person` [Q141175725](https://www.wikidata.org/wiki/Q141175725) — משה בן ידידיה טריף
- `person` [Q141175723](https://www.wikidata.org/wiki/Q141175723) — נח טרוקי
- `person` [Q141175600](https://www.wikidata.org/wiki/Q141175600) — נסי נהרואני
- `person` [Q141175649](https://www.wikidata.org/wiki/Q141175649) — סלימאן בן יוסף
- `person` [Q141175599](https://www.wikidata.org/wiki/Q141175599) — סעדיה בן דוד מרחב
- `person` [Q141175651](https://www.wikidata.org/wiki/Q141175651) — סעיד בן סלם סעיד
- `person` [Q141175652](https://www.wikidata.org/wiki/Q141175652) — עודד בן יצחק
- `person` [Q141175659](https://www.wikidata.org/wiki/Q141175659) — עקיבא בן אברהם קרמטיל
- `person` [Q141175698](https://www.wikidata.org/wiki/Q141175698) — שלמה בלבו
- `person` [Q141175601](https://www.wikidata.org/wiki/Q141175601) — שלמה נרבוני
- `person` [Q141175596](https://www.wikidata.org/wiki/Q141175596) — שמואל לניאדו
- `person` [Q141175662](https://www.wikidata.org/wiki/Q141175662) — שמעון אבן חביב

