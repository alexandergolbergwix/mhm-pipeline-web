# Persistent Wikidata `maxlag`: 17 November 2026

## Result

The observed refusal comes from the shared Wikidata Query Service (WDQS) lag metric.
The evidence does not identify an invalid repair, an account restriction, or a large request as the cause.
A minimal anonymous request fails before the API processes any item payload.
Smaller batches can reduce our load, but they cannot remove this shared lag condition.
This conclusion combines the local probe with the official [maxlag manual](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter).

The refusal does not establish that all users lose read access.

The supported automatic path retains `maxlag=5`, waits after each refusal, and resumes after the server accepts requests.
Persistent refusals require a Wikimedia report with the exact error and UTC times.
The official manual directs persistent-lag reports to `#wikimedia-tech` on IRC.
No public report or public edit formed part of this research.

## Local evidence supplied for this research

The parent task supplied these observations from `state/gila-breakpoint-2026-11-17/`.
This report does not claim a new independent live probe.

| Observation | Result |
| --- | --- |
| Anonymous 62-byte POST | `action=query`, no item or claim payload |
| Target | `https://www.wikidata.org/w/api.php` |
| Requested limit | `maxlag=5` |
| HTTP status | `200` |
| API error | `maxlag` |
| Error type | `wikibase-queryservice` |
| Error host | `wdqs1014` |
| Reported scaled lag | `9.883333333333333` |
| Response header | `Retry-After: 5` |
| Q141175584 | Six refusals; attempt 7 succeeded at revision `2570829322` |
| Q141175783 | At least 11 confirmed refusals at the evidence snapshot |
| Automatic retries | One item at a time; exponential delay; cap 300 seconds |

The parent repeated the anonymous probe at **2026-11-17T11:02:31Z**.
It again received HTTP 200 with `maxlag`, host `wdqs1014`, and type `wikibase-queryservice`.
The response gave `lag=15.0666666667`, **`queryserviceLag=904`**, and `Retry-After: 5`.
The raw lag was therefore **15 minutes 4 seconds**, rather than an estimate from the scaled number.
The probe made zero public writes.
Its evidence file is `state/gila-breakpoint-2026-11-17/research-gate-probe.json`.

The API error can use HTTP 200, so the client must inspect `error.code`.
`Retry-After` specifies a minimum delay; it does not promise recovery after that interval.
The manual recommends `maxlag=5`, a delay of at least five seconds, and no busy retry loop.
A low completion rate during high load is an expected result.
These rules appear in the [official maxlag manual](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter).

## What the lag number means

The Wikidata.org extension divides the raw WDQS lag by `WikidataOrgQueryServiceMaxLagFactor`.
It returns the scaled value as `lag` and the raw value as `queryserviceLag`.
It sets `type` to `wikibase-queryservice` when this value exceeds the other lag metric.
See [Hooks.php, revision bf73b28d](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikidata.org/+/bf73b28d87cbb91df0d8c7f4066abd3822b0168e/src/Hooks.php#84).

The parent verified the current production factor as `60` in the public configuration.
Thus, `9.883333333333333 × 60 = 593` raw seconds, about 9 minutes 53 seconds.
That conversion is an inference from the current factor and the supplied response.
Prefer the response's `queryserviceLag` field when available.
See the [production configuration](https://noc.wikimedia.org/conf/InitialiseSettings.php.txt).
The [Wikidata limits page](https://www.wikidata.org/wiki/Wikidata:WikiProject_Limits_of_Wikidata#maxlag) also explains this scale, with a historical configuration date.

The source selects the most lagged eligible host from `wdqs-main` and `wdqs-scholarly`.
Its eligibility test uses the host's query rate over five minutes.
The API reads the stored result; our repair does not select this host.
Therefore, `wdqs1014` identifies the source of the lag metric, rather than an API origin that our client should select.
See the [provider source](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikidata.org/+/bf73b28d87cbb91df0d8c7f4066abd3822b0168e/src/QueryServiceLag/WikimediaPrometheusQueryServiceLagProvider.php#87).
This source revision explains the mechanism; it does not establish the exact deployed software revision.

## Documented incidents and remedies

| Date | Confirmed cause | Wikimedia remedy | Relevance |
| --- | --- | --- | --- |
| 6 August 2024 | An internal probe made an incomplete WDQS host appear eligible for the lag metric. | An operator stopped Blazegraph on that host and corrected the probe configuration. | A shared metric fault can stop compliant bots. |
| 7–11 May 2026 | Excessive external queries overloaded Blazegraph. The updater received HTTP 429 responses, and WDQS lag increased. | Operators removed the affected datacenter from service and restricted the excessive query traffic. | WDQS load can block Action API edits through `maxlag`. |
| 15 and 28 July 2026 | Users reported high maxlag; the public discussion does not establish a root cause. | The displayed discussion records no confirmed remedy. | Historical corroboration only. |

The August incident is [Phabricator T371871](https://phabricator.wikimedia.org/T371871).
The operator identified an incorrect internal user-agent classification and linked its configuration fix.
This resolved incident does not establish the cause on `wdqs1014` today.

The [May incident report](https://wikitech.wikimedia.org/wiki/Incidents/2026-05-13_wdqs) explicitly connects updater refusals to Wikidata edit restrictions.
Operators found an excessive scraper through deeper logs after sampled traffic data missed it.
An operator applied a `requestctl` rule, and the query timeout rate returned to its baseline.
This incident proves that client payload simplification does not solve every persistent `maxlag` event.

The [July discussion](https://www.wikidata.org/wiki/Wikidata:Report_a_technical_problem/WDQS_and_Search#High_maxlag_2026-07-15) predates this investigation.
Its presence does not prove an unresolved November incident.

## Wikimedia operator procedure

The official runbook treats isolated host lag and datacenter lag separately.
For one or two hosts, operators remove each host from service, restart Blazegraph, and check recovery before restoration.
For an affected datacenter, operators remove that datacenter from service and examine the RDF updater.
If necessary, operators restore the updater from a checkpoint or refer the case to a specialist.
These actions require Wikimedia operational access.
See [ElevatedMaxLagWDQS](https://wikitech.wikimedia.org/wiki/Wikidata_Query_Service/Runbooks/ElevatedMaxLagWDQS).

Our client cannot perform these repairs through Heroku configuration, a local database, or an item update.
The runbook's ten-minute host threshold also differs from our `maxlag=5` threshold, which corresponds to five raw minutes at factor 60.
Thus, an API refusal need not imply that the operator threshold has already been exceeded.
This threshold comparison is an inference from the runbook and the current factor.

## Current incident limits

The official [Wikimedia Status page](https://www.wikimediastatus.net/) showed all systems operational and no incident on 17 November 2026.
This general status does not override the specific API error in our evidence.
It also does not establish that every WDQS host has acceptable lag.

The [Wikitech incident index](https://wikitech.wikimedia.org/wiki/Incident_status) showed no report in the last 14 days.
The index warns that staff now use it less often and points to Corto records in Phabricator.
This research found no confirmed public November incident that explains `wdqs1014`.
The actual cause therefore remains unknown without current metrics or a Wikimedia response.

## Client actions that remain valid

1. Retain `maxlag=5` for every automatic edit.
2. Honor `Retry-After` as a minimum delay.
3. Retain exponential delay and add random delay if multiple workers can retry together.
4. Use one active repair at a time until service health improves.
5. Store the next retry time so a process restart does not cause immediate repeated requests.
6. Record the UTC time, host, type, scaled lag, raw lag, and response headers for each refusal.
7. Preserve the item and revision guards before each later retry.
8. Verify each accepted edit before the queue counts it as complete.
9. Continue local duplicate checks and read-only preparation during the delay.
10. Request Wikimedia review if repeated low-rate probes still show the shared condition.

Items 3–10 are recommendations from this investigation, rather than exact quotations from the manual.
The existing 300-second cap already exceeds the supplied five-second minimum.
A longer delay can reduce unnecessary requests, but no client delay guarantees a recovery time.
Local filters and an alternative query service can reduce discovery costs; neither changes the Action API lag metric.

The parent also reviewed the current client code.
`WriteRejectedError` retains the error code and retry delay, but drops `host`, `type`, `lag`, and `queryserviceLag`.
The queue stops after its 3,600-second limit for one item.
These facts explain why a shared outage can appear as one item that never completes.
The next code change should preserve the typed diagnostics and expose an explicit shared-service hold.
It should preserve a durable next retry time beyond the current per-item limit.

An optional low-rate health probe can check service recovery before the next repair attempt.
The probe must retain `maxlag=5`; a successful probe does not guarantee that the next edit succeeds.
These client changes improve observability and recovery; they do not repair the remote WDQS service.
No code change formed part of this research task.

The manual permits an exception for a genuinely interactive task where a user waits for a result.
This project limits that exception to an exact item with separate user confirmation.
An automatic sequence remains automatic, even when it sends only one item at a time.
Do not extend an interactive exception to the automatic queue.
See the [official distinction](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter).

## Support draft — prepared only

Suggested subject: **Persistent Wikidata `maxlag`, host `wdqs1014`, type `wikibase-queryservice`, 17 November 2026**

> Our low-rate Wikidata client receives repeated `maxlag` errors with `maxlag=5`.
> A minimal anonymous POST to `action=query` produces the same error without an item payload.
> At 2026-11-17T11:02:31Z, the response names `wdqs1014` and type `wikibase-queryservice`.
> It reports scaled lag `15.0666666667` and raw `queryserviceLag=904` seconds.
> The response uses HTTP 200 and `Retry-After: 5`.
> We retain `maxlag=5`, process one item at a time, and use exponential delays up to 300 seconds.
> One repair succeeded after six refusals; the next item received at least eleven refusals.
> Please examine the host's update lag, the shared maxlag metric, and the updater health.
> Please also check whether this host should contribute to the shared lag metric.
> We can provide exact UTC times, redacted response headers, and the anonymous reproduction.

Before a person sends the draft, attach the exact UTC evidence interval and any available `queryserviceLag` values.
Exclude cookies, authorization headers, CSRF tokens, and session data.
The manual recommends [`#wikimedia-tech`](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter) for persistent lag.
The [WDQS problem page](https://www.wikidata.org/wiki/Wikidata:Report_a_technical_problem/WDQS_and_Search) provides an additional public discussion route.

## Scope and source method

This report uses official documentation, official source, official operational records, and the supplied local evidence.
Gerrit source came from the official Gitiles endpoint after the web tool could not display its source pages.
No GitHub link, GitHub action, public edit, support post, source-data change, or database change formed part of this task.
The creator restriction remains unchanged: only entities created by `Alexander_Goldberg_IL` can receive repairs.
Foreign-created entities remain read-only link targets.


## Read access check: 17 November 2026, 14:14 Israel time

The user correctly challenged the scope of the earlier explanation.
The same anonymous client tested these paths in sequence without a public write.

| Request | Result |
| --- | --- |
| Action API siteinfo with `maxlag=5` | `maxlag`, host `wdqs1014`, raw delay 1,246 seconds |
| Interactive siteinfo without `maxlag` | Successful JSON response, HTTP 200 |
| Interactive Q42 label read without `maxlag` | Successful entity response, HTTP 200 |
| SPARQL `ASK { }` | HTTP 200, boolean `true`, 0.32 seconds |

A separate Q42 label query returned valid SPARQL results with zero rows.
That response does not establish dataset completeness or freshness.
The constant ASK proves query execution, rather than current entity data.
The successful interactive reads disprove a universal read outage.
The `maxlag` refusal applies to the request that supplies that guard.
The [official manual](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter) permits omission for interactive tasks and recommends the guard for noninteractive tasks.
These diagnostic reads do not authorize omission from automatic repairs.
The evidence is `state/gila-maxlag-client-2026-11-17/read-path-comparison.json`.
