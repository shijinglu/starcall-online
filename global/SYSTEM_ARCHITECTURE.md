# Stream Processing System Architecture

## Overview

A distributed, event-driven stream processing system for converting batch SQL jobs into realtime processors. Built on modern stream processing principles with type-safe RPC communication.

## Design Principles

1. **Minimalist microservices**: Each service has a single, well-defined responsibility
2. **Hot-reloadable processors**: Deploy new processors without system downtime
3. **Event-driven architecture**: All communication via events, no direct service coupling
4. **Idempotent processing**: All processors support replay and deduplication
5. **Point-in-time consistency**: SCD Type 2 tables enable historical queries
6. **Declarative DAG**: Processor topology defined in configuration, not code

---

## System Components

### Core Services (Always Running)

#### 1. ID Service
**Purpose**: Convert arbitrary identifiers (strings, UUIDs) to uint64 IDs for efficient storage and indexing

**Key Operations**:
- Convert string/UUID to uint64 (with auto-creation)
- Batch conversion for efficiency
- Reverse lookup for debugging

**Technology Stack**:
- RocksDB for persistent storage
- LRU cache for hot IDs
- Supports horizontal sharding by namespace

---

#### 2. State Store Service
**Purpose**: High-performance KV store for dimension tables with SCD Type 1/2 support

**Key Features**:
- SCD Type 2: Track historical changes with valid_from/valid_to timestamps
- SCD Type 1: Simple current-value updates
- Point-in-time queries (query data as it existed at any past timestamp)
- Range scans for aggregations
- Batch operations

**Technology Stack**:
- RocksDB with column families per table
- Composite keys for efficient temporal queries
- TTL support for rolling windows
- Read replicas for query load distribution

---

#### 3. DAG Scheduler
**Purpose**: Manage processor topology, validate dependencies, route events

**Key Responsibilities**:
- Register and manage processor specifications
- Validate DAG for cycles and missing dependencies
- Maintain service discovery registry
- Monitor processor health and auto-restart on failure
- Support hot reloading of processors

**Technology Stack**:
- etcd for distributed coordination
- Tarjan's algorithm for cycle detection

---

#### 4. Event Bus
**Purpose**: Message broker for event streaming

**Key Features**:
- Publish/subscribe messaging
- Exactly-once semantics via transactional commits
- Consumer groups for load balancing
- Message replay from arbitrary offset
- Partition-based ordering guarantees

**Technology Stack**:
- Kafka or Pulsar (configurable)
- Thin RPC wrapper for standardization

---

#### 5. Schema Registry
**Purpose**: Manage event schemas for validation and evolution

**Key Features**:
- Schema versioning
- Backward compatibility enforcement
- Schema evolution rules (add optional fields, deprecate fields)
- Event validation

**Technology Stack**:
- PostgreSQL for schema storage
- In-memory cache for hot schemas

---

#### 6. Control Plane
**Purpose**: Deploy, monitor, and manage processor lifecycle

**Key Responsibilities**:
- Deploy processor instances
- Scale processors (add/remove instances)
- Monitor processor health and metrics
- Hot reload (rolling restart with new code)
- Collect metrics and logs

**Technology Stack**:
- Process manager (Kubernetes or systemd)
- Object storage (S3/MinIO) for processor binaries
- Rolling deployment strategy

---

## User Flows

### Flow 1: Developer Creates New Processor

```
1. Developer identifies a batch SQL job to convert
   └─> Example: Aggregate user withdrawal stats

2. Developer uses CLI to scaffold processor
   └─> $ stream-cli new-processor --name update_withdrawal_stats
   └─> Generates processor template with config

3. Developer implements processor logic
   └─> Define event input schema
   └─> Implement transformation logic
   └─> Define state updates (SCD Type 1 or Type 2)
   └─> Define output events

4. Developer writes tests
   └─> Unit tests with mock services
   └─> Integration tests with test events

5. Developer deploys processor
   └─> $ stream-cli deploy --config config.yaml --instances 3
   └─> System validates DAG
   └─> Processor starts consuming events
   └─> Metrics available in dashboard
```

---

### Flow 2: Event Processing (Runtime)

```
1. Source System publishes event
   └─> Event: onchain_withdrawal_events
   └─> Payload: { user_id, volume_usdt, status, timestamp }

2. Event Bus routes to subscribers
   └─> Kafka partitions by user_id for ordering
   └─> Multiple processor instances share load via consumer group

3. Processor Runtime receives event
   └─> Checks deduplication cache (Redis)
   └─> If already processed, skip and continue

4. Processor executes business logic
   a. Convert identifiers to uint64
      └─> Call ID Service: "user_abc" → 42

   b. Fetch current state
      └─> Call State Store: get_scd2_current("withdrawal_stats", key=42)

   c. Apply transformations
      └─> Increment counters, calculate aggregates

   d. Update state (SCD Type 2)
      └─> Close old row (set valid_to = event_timestamp)
      └─> Insert new row (set valid_from = event_timestamp)

   e. Emit output events
      └─> Publish: dim_withdrawal_stats_updated

5. Commit and mark processed
   └─> Mark event ID in deduplication cache
   └─> Commit Kafka offset
   └─> Update metrics (events_processed++)

6. Downstream processors triggered
   └─> Event propagates through DAG
   └─> Next level processors consume output events
```

---

### Flow 3: Query Historical Data

```
1. Application needs point-in-time query
   └─> Example: "What was user 42's stats on 2024-01-15?"

2. Query Service calls State Store
   └─> get_scd2_at_time("withdrawal_stats", key=42, timestamp=2024-01-15)

3. State Store performs range scan
   └─> Find row where:
       - key = 42
       - valid_from <= 2024-01-15
       - valid_to > 2024-01-15

4. Return historical snapshot
   └─> Response: { total_cnt: 8, total_vol: 800, ... }
   └─> Query latency: <10ms
```

---

### Flow 4: Hot Reload Processor

```
1. Developer updates processor code
   └─> Bug fix or feature enhancement

2. Developer deploys new version
   └─> $ stream-cli deploy --processor my_processor --version 2

3. Control Plane performs rolling update
   a. Deploy new instances alongside old ones
   b. New instances join same consumer group
   c. Kafka rebalances partitions
   d. Old instances drain in-flight events
   e. Old instances shut down gracefully
   f. All partitions now served by new version

4. Zero downtime achieved
   └─> Events continue processing throughout
   └─> No message loss or duplication
```

---

### Flow 5: DAG Configuration & Validation

```
1. Define processor dependencies in config
   └─> config/dag.yaml specifies:
       - Which events each processor subscribes to
       - Which events each processor emits
       - Service dependencies (ID Service, State Store)

2. Validate DAG before deployment
   └─> $ stream-cli dag validate
   └─> Checks for:
       - Circular dependencies
       - Missing topics
       - Orphaned processors

3. Visualize topology
   └─> $ stream-cli dag visualize --output dag.png
   └─> Shows event flow across all processors
   └─> Groups by dependency level (leaf → intermediate → aggregation)

4. Test with sample events
   └─> $ stream-cli dag test --events sample.json
   └─> Simulates event propagation
   └─> Verifies end-to-end flow
```

---

## Processor Development Framework

### Processor Interface (Conceptual)

Every processor implements:

1. **process(event)**: Transform single event
   - Parse event payload
   - Call ID Service to convert identifiers
   - Fetch current state from State Store
   - Apply business logic
   - Update state (SCD Type 1 or Type 2)
   - Emit output events

2. **process_batch(events)**: Batch processing optimization
   - Process multiple events in one transaction
   - Reduces RPC overhead

3. **init(config)**: Initialization
   - Load configuration
   - Connect to services
   - Validate dependencies

4. **shutdown()**: Graceful cleanup
   - Drain in-flight events
   - Close connections
   - Flush metrics

### Processor Context

Runtime provides each processor with:
- **ID Service client**: Identifier conversion
- **State Store client**: State management
- **Event Producer client**: Emit output events
- **Deduplication cache**: Idempotency checking
- **Logger**: Structured logging
- **Metrics collector**: Performance tracking

---

## DAG Configuration Example

```yaml
# Simplified processor configuration

processors:
  # Level 0: Base dimension tables
  - id: update_withdrawal_stats
    subscribes_to: [onchain_withdrawal_events]
    emits: [dim_withdrawal_stats_updated]

  - id: update_deposit_stats
    subscribes_to: [onchain_deposit_events]
    emits: [dim_deposit_stats_updated]

  - id: update_detection_stats
    subscribes_to: [rule_engine_detection_events]
    emits: [dim_detection_stats_updated]

  # Level 1: Intermediate aggregations
  - id: update_transaction_coverage
    subscribes_to:
      - dim_withdrawal_stats_updated
      - dim_detection_stats_updated
    emits: [dim_transaction_coverage_updated]

  # Level 2: Final dashboard metrics
  - id: update_dashboard_metrics
    subscribes_to:
      - dim_withdrawal_stats_updated
      - dim_detection_stats_updated
      - dim_transaction_coverage_updated
    emits: [dim_dashboard_metrics_updated]
```

---

## Infrastructure Requirements

### Middleware Components

| Component | Purpose | Examples |
|-----------|---------|----------|
| **Message Queue** | Event streaming backbone | Kafka, Pulsar |
| **Key-Value Store** | Processor state persistence | RocksDB (embedded) |
| **Coordination Service** | Distributed coordination | etcd, ZooKeeper |
| **Relational Database** | Schema registry storage | PostgreSQL |
| **Cache** | Deduplication & hot data | Redis, Memcached |
| **Object Storage** | Processor binaries | S3, MinIO |
| **Metrics/Monitoring** | Observability | Prometheus, Grafana |
| **Tracing** | Distributed tracing | OpenTelemetry, Jaeger |

---

## Monitoring & Observability

### Key Metrics

**Per Processor**:
- Events processed per second
- Processing latency (p50, p95, p99)
- Error rate
- Consumer lag (time behind source)

**Core Services**:
- ID Service: Request rate, cache hit rate
- State Store: Query latency, cache hit rate, storage size
- DAG Scheduler: Processors registered, running, failed
- Event Bus: Throughput, partition lag, rebalance events

### Logging

All logs in structured JSON format:
- Timestamp, service, processor ID
- Event ID (for tracing)
- Processing duration
- Business context (user_id, amount, etc.)

### Tracing

Distributed traces across services:
- Track single event from ingestion to final output
- Identify bottlenecks in processing pipeline
- Measure RPC latency between services

---

## Scaling Strategy

### Horizontal Scaling

**Processors**:
- Add more instances to consumer group
- Kafka auto-rebalances partitions
- Linear scale-out with partitions

**ID Service**:
- Shard by namespace hash
- Each shard handles subset of namespaces

**State Store**:
- Shard by key hash
- Add read replicas for query load

**Event Bus**:
- Add Kafka brokers
- Increase topic partitions

### Vertical Scaling

- Increase processor memory for larger caches
- More CPU cores for parallel processing
- Faster disks for State Store (NVMe SSDs)

---

## Error Handling

### Transient Errors
- Automatic retry with exponential backoff
- Max 3 retries per event
- Metrics track retry count

### Permanent Errors
- Send to dead letter queue (DLQ)
- Include error context in DLQ message
- Alert operations team
- Manual investigation and replay

### Idempotency
- Every event has unique ID
- Deduplication cache (Redis) tracks processed events
- TTL of 24 hours (configurable)
- Safe to replay events without side effects

---

## Summary

This architecture provides:

1. **High-level simplicity**: Clear separation of concerns across 6 core services
2. **Developer productivity**: Simple processor interface, CLI tooling, automatic DAG management
3. **Operational excellence**: Hot reloading, zero-downtime deployments, comprehensive monitoring
4. **Scalability**: Horizontal scale-out for all components
5. **Reliability**: Idempotent processing, automatic retries, dead letter queues
6. **Performance**: <10ms query latency, millions of events per second

The system transforms complex batch SQL jobs into realtime stream processors while maintaining data consistency and enabling historical point-in-time queries.
