# 

Several Dimensions:

## DIM-X: NANOCAP: a low latency, zero copy oriented micro service architecture

We are going to build a plugin based microservice architecture for rust + c to support hot realoading:

1. Vessel + capsule + PRC
   - A Capsule or a cap is like a plugin, it can be hot loaded and run by a vessel.
   - A vessel is like a microservice, usually we have one vessel each machine/container/pod
   - Vessel is thin and mostly serve as the catalog service for caps to find out corresponding other caps
   - A cap is usually a single responsibility function module. For example, a CRUD operations in tradditional microservice, can involve:  redis logic, MySQL logic, Kafka Logic and logic of calling other services. In nanocap design, those will be handled by redis-cap, mysql-cap, kafka-cap and service-xxx-api-cap.
   - A cap communicates with other caps via protocol (c header, rust traits, java/go interface). Implmentation to those protocol depends on the environment:
     - If two caps are attached to the same vessel, then they can communicate via native function call or via nng_inproc transport (inproc://)
     - If two caps are in the same host, but not attached to the same vessel, then they can communicate via ipc transport (ipc://)
     - If two caps live in different hosts, they can communicate via TCP (tcp://)

2. Serialization and Communication
   1. Cap'n protocol
   2. Cap to Cap communicates via NNG

3. Zero Copy
   Most of the time we prefer [Deep integration of NNG and Capn protocol](../nanocap/zero_copy.md#the-deep-integration-true-end-to-end-zero-copy) but [simple combination](../nanocap/zero_copy.md#the-naïve-combination-still-pretty-good) is also acceptable


## DIM-Y: ULTRA GRID: Fast and Low Latency Distributed Tasks System

This is a distributed tasks system similar to python ray platform. But different from ray who provides Task, Actor and Objects. GRID borrows the idea of taskiq, it only provides `Actor` or  but provide reach backend to init or store states.

Important aspects of this system:
1. Rick state backend, for fast state recovery or storing result
2. Distributed brokers with RAFT concensus



## DIM-Z: Orchistrator, converting SQLs into distrubted executable tasks

This system basically manages a global DAG:
- convert SQL into execution plans
- build caps accordingly
- deploy caps into GRIPD


