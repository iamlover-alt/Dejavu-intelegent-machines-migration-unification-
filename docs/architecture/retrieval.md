# Retrieval layer

Episode memory, schema and connector documents, and decision traces share one retrieval path: embed, kNN, optional filters, rerank, then `final = rerank × store prior`. Every hit carries the breakdown (`knn`, `lexical`, `field_match`, `prior`, `rerank`, `final`, `warning`, `weights`, `terms`).

The default embedder and reranker run on this machine. They do not download weights and they do not call a paid API. Tests use them with the fake model provider. Trading stays paper-only: the trading connector is `paper_only: true` and `real_money: false`, and this layer has no broker client and no trading endpoint.

`POST /v1/retrieve` is the HTTP surface. The graph node `retrieve_context` is off unless `MIDPLANE_RETRIEVAL` is `1`, `true`, `yes`, or `on`.

## Path

```text
query
  -> Embedder
  -> VectorIndex kNN (metadata filters applied first)
  -> Reranker (lexical overlap, field match, metadata prior)
  -> final = rerank * store prior
```

`knn` is cosine similarity of L2-normalized vectors. It chooses the candidate pool (default `max(top_k * 5, 80)`). It is not multiplied into `final`. The breakdown still reports it so a later cross-encoder can show that a neighbor was close in vector space and then reordered.

`rerank = 0.70 * lexical + 0.30 * field_match`

- `lexical` is the fraction of query tokens found in the document.
- `field_match` is the fraction found in named fields (`path`, `name`, `title`, `table`, and the other fields a store sets).

`final = rerank * prior`. `prior` is the store weight below.

## Weights

### Episodes

Quality, then recency, then source. A high-quality episode outranks a closer lexical match. Recency breaks a quality tie. Source breaks a recency tie.

```text
quality_term = 0.25 + 0.75 * quality_score
recency_term = 0.70 + 0.30 * exp(-ln(2) * age_days / 14)
source_term  = 0.70 + 0.30 * source_prior
prior        = quality_term * recency_term * source_term
```

| Source | Prior |
| --- | --- |
| `dev_guild` | 1.0 |
| `app` | 0.65 |
| `trading` | 0.25 |

Missing timestamps use recency `0.5`. An episode with `outcome=error` is still returned and gets `warning=failure`.

Filters: `source`, `min_score`, `since`, `until`, and `run_ids`. The gateway forces `run_ids` to the API key's client. A key cannot widen that set.

Chunks are inputs, the output summary, each finding, and the model choice. `index_episode` replaces that run's chunks. The gateway reindexes from `EpisodeStore` on a retrieve that touches episodes.

### Schema and connectors

```text
prior = 0.60 + 0.40 * status_prior
```

| Status | Prior | Warning |
| --- | --- | --- |
| `wired` | 1.0 | none |
| `planned` | 0.55 | `planned` |

`BuiltinSchemaLoader` builds the corpus from:

- operations and component schemas in `openapi/midplane-v0.yaml`
- `create table` statements in `migrations/*.sql`
- `midplane/retrieval/connectors.yaml`

The seed marks EpisodeStore, Gateway REST+SSE, the in-process LLM provider (fake by default), the paper-only trading control client, and the iOS Swift client as `wired`. GitHub, Gmail, Calendar, Drive, and X are `planned`. Every connector has `real_money: false`. The trading row also has `paper_only: true`.

### Loader plug for the schema agent

`SchemaDocumentLoader.load_documents()` is the seam. `SchemaConnectorStore(loader=...)` accepts another loader. `midplane/schema_registry/` and the schema agent are a separate change. This package does not import them. A later registry can pass its loader without this store hardcoding table or path lists beyond the built-in loader.

### Decision traces

`migrations/004_retrieval.sql` creates `decision_traces`. Columns: agent, run id, episode id, decision point, options, choice, rationale, outcome (`success`, `fail`, or `unknown`), outcome score, created and updated timestamps.

```text
prior = 0.20 + 0.80 * outcome_prior
```

| Outcome | Prior | Warning |
| --- | --- | --- |
| `success` | 1.0 | none |
| `unknown` | 0.55 | none |
| `fail` | 0.15 | `failure` |

Failures stay in the result list with the warning, and they rank below a success with the same lexical overlap. `outcome_stats(decision_point)` returns counts, the mean outcome score, `worked` (more successes than failures), and `failed` (at least one failure). A mixed choice can appear in both.

The router writes a `model_routing` trace when a `DecisionTraceStore` is attached. `episode_writer` sets that row's outcome from the episode (`error` becomes `fail`) and appends an `episode_completion` trace. With the graph flag off and no store passed in, nothing is written. Turning the flag on attaches a store on the episode database (a private memory database when the episode store is `:memory:`).

## Indexes

`EpisodeStore` picks SQLite unless the URL starts with `postgres://` or `postgresql://`. Retrieval uses the same rule.

| URL | Trace log | Vectors |
| --- | --- | --- |
| `sqlite:///...` | `decision_traces` in that file | numpy, in process |
| `postgres://` or `postgresql://` | `decision_traces` | `retrieval_vectors`, `vector(256)`, HNSW |

The HNSW index is `retrieval_vectors_embedding_hnsw` (`vector_cosine_ops`, `m=16`, `ef_construction=64`). It needs pgvector 0.5 or newer. The migration comments the IVFFlat statement for an older server. Do not create both. Compose uses `pgvector/pgvector:pg16` so `create extension vector` works. The parity test skips when that extension is not reachable. Set `MIDPLANE_TEST_POSTGRES_URL` to run it.

`EMBEDDING_DIM` is 256. The hashed embedder and the pgvector column share that number. A different size needs a new migration.

## Swapping the models

Keep the default for tests and for any process that must not spend money.

| Piece | Default | Optional adapter |
| --- | --- | --- |
| Embedder | `HashedNgramEmbedder`: SHA-256 hashed word and character n-grams, sublinear TF, smoothed IDF after `fit`, L2-normalized | `SentenceTransformerEmbedder` (`pip install 'midplane[embeddings]'`) or `OpenAICompatibleEmbedder` |
| Reranker | `LexicalReranker` | `CrossEncoderReranker` from the same extra. Its score is mixed with the lexical score, then the store prior still multiplies |

Construct the adapter and pass it to the store. Do not import it from the gateway or the default graph. `OpenAICompatibleEmbedder` calls `{base_url}/embeddings` only from `embed`, not from `__init__`. Its vectors use the provider's dimension. They do not fit `vector(256)` until a migration widens the column. Sentence-transformers downloads weights the first time the model object is built, then scores locally.

The HTTP chain has `provider=None`. `rewrite_with_model` on a `ChainStep` is for in-process chains. Tests pass `FakeModelProvider`. A chain built with a real `ModelProvider` and `rewrite_with_model=True` would spend tokens. The default graph steps do not set that flag, so `MIDPLANE_RETRIEVAL=1` does not add a model call. `code_review` is still the only model call on that path.

## Chain

`RetrievalChain` runs steps up to `max_hops` (3 on the gateway, 2 on the default graph path). Each step names a store. The query is, in order:

1. `rewrite_with_model` (one completion; refused when no provider is set)
2. `query_from(original, previous_hits)`
3. the step query, or the original query, plus the previous hit's `name` when `use_previous` is set

Hop traces record the query, doc ids, and duplicates already seen. The fused list is unique. With two or more hops the rank is reciprocal rank fusion:

```text
score(doc) = sum over hops of  weight(store) / (60 + rank)
```

| Store | Fusion weight |
| --- | --- |
| episodes | 1.0 |
| schema | 1.0 |
| traces | 0.85 |

`retrieve_context` sits between `router` and `code_review` only when the flag is on. It writes a short hit list onto state. `code_review` prepends that text to the prompt when the list is non-empty. With the flag off the prompt and the edges are unchanged.

## Cost

- Hashed embeddings, the lexical reranker, numpy search, SQLite, and the fake provider cost nothing and do not leave the process.
- `POST /v1/retrieve` does not call a model.
- The embeddings extra downloads a local model once. Inference after that is local.
- `OpenAICompatibleEmbedder` is a paid HTTP call. Nothing in the default path constructs it.
- Enabling the graph flag does not add a model call. A custom chain that sets `rewrite_with_model` and a non-fake provider does.
- pgvector is the Postgres extension, not an API.

## Related documents

- [README](../../README.md)
- [Mid-plane architecture](mid-plane.md)
- [Episode flywheel v0](episode-flywheel.md)
- [Gateway API v0](../api/gateway-v0.md)
- [90-day tracker](../plan/90-day-tracker.md)
