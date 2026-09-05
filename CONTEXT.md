# MHM Pipeline Web Domain Model

This file defines the shared terms for the code and architecture documents.

## Wikidata publication

- A **Source Snapshot** is one immutable set of approved run data.
- A **Publication** is the durable root for one source, profile, and Wikidata target.
- A **Release** is one immutable projection from a Source Snapshot.
- A **Publication Entity** is one canonical item in a Release.
- An **Identity Assertion** links an entity to a strong external identifier.
- An **Identity Group** contains entities that can describe the same real entity.
- A **Finding** is a typed result from an entity, group, or corpus rule.
- An **Approval Set** binds curator decisions to exact entity digests.
- A **Plan** binds a Release, an Approval Set, live observations, and proposed actions.
- A **Dry-run Receipt** proves that a Plan passed all required gates without writes.
- An **Execution** applies one Plan to one Wikidata target.
- A **Write Intent** records one proposed remote change before the request.
- A **Write Receipt** records the confirmed remote result after the request.
- A **Deferred Edge** is a local entity reference that waits for its target QID.

## Relationships

```text
Run -> Source Snapshot -> Publication -> Release
                                      -> Approval Set
                                      -> Plan -> Execution
                                                -> Write Intent
                                                -> Write Receipt
```

A Publication can have many Releases.

Each Release can have many Approval Sets and Plans.

Each Plan has one immutable action list.

Each Execution has durable entity and edge checkpoints.

## Safety boundaries

The projector cannot access Wikidata or another network service.

The Wikidata gateway is the only code that can access a public Wikidata target.

An AI verdict supplies advice. A deterministic rule supplies a publication gate.

An approval becomes stale when an entity digest changes.

A dry-run receipt becomes stale when its Plan expires or a bound digest changes.

An unknown lookup result never means that an entity is absent.

The executor never repeats an uncertain create request without remote recovery evidence.

The executor never removes a claim unless its journal proves bot ownership and an unchanged remote fingerprint.

## Scope

The first profile supports MHM manuscripts, persons, and works.

The publication module supports more entity types through a versioned internal profile.

The module does not expose source adapters or target adapters to HTTP callers.
