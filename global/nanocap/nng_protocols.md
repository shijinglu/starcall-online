
Here’s a concise summary of all NNG protocols in a single comparison table:


| Protocol                  | Pattern / Topology        | What it Does                                      | Microservice Analogy        | Delivery Semantics          | Strengths                                      | Weaknesses / Failure Handling |
|--------------------------|--------------------------|---------------------------------------------------|-----------------------------|-----------------------------|-----------------------------------------------|-------------------------------|
| Req / Rep                | 1 → 1 (request/reply)    | Client sends request, server replies              | RPC / gRPC unary            | At-least-once (client retry)| Simple RPC model, built-in retry               | Duplicate requests possible → use idempotency & request IDs |
| Pub / Sub                | 1 → N (broadcast)        | Publisher broadcasts to all subscribers           | Event bus / Kafka-lite      | Best-effort (fire-and-forget)| Decoupled fanout, scalable                     | No durability, missed messages → use replay/store if needed |
| Push / Pull              | 1 → N (load-balanced)    | Distributes work to workers                       | Work queue / async jobs     | Effectively at-most-once     | Simple load balancing                          | Lost work on failure → use job IDs, external tracking |
| Surveyor / Respondent    | 1 → N → 1 (fanout + collect) | Broadcast query, gather replies within deadline | Service discovery / quorum  | Best-effort within timeout   | Multi-response query pattern                   | Partial responses → design for timeouts & retries |
| Bus                      | N ↔ N (mesh broadcast)   | Each node sends to all peers                      | Gossip / peer broadcast     | Best-effort broadcast        | Fully decentralized                            | No reliability → use for soft-state/gossip only |
| Pair (v0 / v1)           | 1 ↔ 1 (bidirectional)    | Raw two-way communication                         | TCP socket / custom proto   | Best-effort (can drop)       | Maximum flexibility                            | Must implement reliability (acks, retries) yourself |


### Quick mental mapping

* **RPC-style** → Req/Rep
* **Kafka-like (but NOT durable)** → Pub/Sub
* **Background jobs** → Push/Pull
* **Cluster query / discovery** → Surveyor/Respondent
* **Gossip / mesh** → Bus
* **Custom protocol / control channel** → Pair

### Key takeaway

* **No NNG protocol provides exactly-once delivery**
* You must design for:

  * **idempotency (Req/Rep)**
  * **replay/durability (Pub/Sub)**
  * **job tracking (Push/Pull)**
  * **timeouts/partial results (Surveyor)**

### Protocols
All protocols work identically over any transport: TCP (tcp://), IPC/Unix domain sockets (ipc://), in-process (inproc://), WebSocket (ws://), TLS (tls+tcp://), and ZeroTier (zt://). You can change transports without changing application logic.
