# TODO

## capture full query text associated with VC digest

| queries        |
| -------        |
| VC digest (PK) |
| aurora digest  |
| query_example  |
| digest_text    |

| tags  |
| ----  |
| id    |
| key   |
| value |

| query_tags_by_hour    |
| ----------            |
| id                    |
| query_id              |
| host_id               |
| tag_id (or key/value) |
| hour                  |
| count                 |


| vc_query_stats_by_hour |
| ----------             |
| id                     |
| query_id               |
| host_id                |
| hour                   |
| <stats>                |

| vc_host_stats_by_hour |
| ----------            |
| id                    |
| host_id               |
| hour                  |
| <stats>               |

## collection
1. periodically query p_s threads joined with events_statements_?
  - want query_text and digest_text
  - query will not have completed so not have latency for this
    particular instance.  will assume latency follows same distribution
    regardless of tags
2. insert/identify new vs old queries
3. insert/identify new vs old tags
4. update counts for query_tags
5. on timer 
    a. save counts.
    b. query&save VC data


## Functional Core:
* compute hash from digest
  - test against VC
* extract tags from query text
* given query stats for query/tags compute weight
* test if not-yet-seen
  - ConCache/ETS?
* sample DB for queries
* flow
  - sample queries w/digests
  - compute digest hash
  - extract tags
  - Record sample (save query if needed; update counts)
